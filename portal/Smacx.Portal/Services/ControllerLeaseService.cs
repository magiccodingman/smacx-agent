using System.Collections.Concurrent;
using System.Security.Cryptography;

namespace Smacx.Portal.Services;

/// <summary>
/// Process-local, deliberately ephemeral ownership of an interactive browser
/// seat. Durable campaign state never depends on a browser tab surviving.
/// </summary>
public sealed class ControllerLeaseService(TimeProvider clock)
{
    public static readonly TimeSpan LeaseLifetime = TimeSpan.FromSeconds(30);

    private readonly ConcurrentDictionary<string, SeatState> seats =
        new(StringComparer.Ordinal);

    public ControllerLeaseSnapshot Acquire(
        string matchId, int seatIndex, string instanceId, string userId,
        string playInstanceId)
    {
        var key = SeatKey(matchId, seatIndex);
        var state = seats.GetOrAdd(key, _ => new SeatState());
        lock (state)
        {
            var now = clock.GetUtcNow();
            Prune(state, now);
            var lease = state.Leases.Values.FirstOrDefault(item =>
                item.UserId == userId && item.PlayInstanceId == playInstanceId);
            if (lease is null)
            {
                lease = new Lease
                {
                    LeaseId = $"lease-{Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(18))}",
                    UserId = userId,
                    PlayInstanceId = playInstanceId,
                    InstanceId = instanceId,
                };
                state.Leases[lease.LeaseId] = lease;
            }
            lease.ExpiresAt = now + LeaseLifetime;
            if (state.ActiveLeaseId is null)
            {
                state.ActiveLeaseId = lease.LeaseId;
                lease.BeginControl();
            }
            return Snapshot(state, lease, now);
        }
    }

    public ControllerLeaseSnapshot Heartbeat(
        string matchId, int seatIndex, string userId, string leaseId)
    {
        var state = RequiredState(matchId, seatIndex);
        lock (state)
        {
            var now = clock.GetUtcNow();
            Prune(state, now);
            var lease = RequiredLease(state, leaseId, userId);
            lease.ExpiresAt = now + LeaseLifetime;
            return Snapshot(state, lease, now);
        }
    }

    public ControllerLeaseSnapshot TakeControl(
        string matchId, int seatIndex, string userId, string leaseId)
    {
        var state = RequiredState(matchId, seatIndex);
        lock (state)
        {
            var now = clock.GetUtcNow();
            Prune(state, now);
            var lease = RequiredLease(state, leaseId, userId);
            lease.ExpiresAt = now + LeaseLifetime;
            if (state.ActiveLeaseId != lease.LeaseId)
            {
                RevokeActiveController(state);
                state.ActiveLeaseId = lease.LeaseId;
                lease.BeginControl();
                state.Generation++;
            }
            return Snapshot(state, lease, now);
        }
    }

    public void Release(string matchId, int seatIndex, string userId, string leaseId)
    {
        if (!seats.TryGetValue(SeatKey(matchId, seatIndex), out var state)) return;
        lock (state)
        {
            if (!state.Leases.TryGetValue(leaseId, out var lease) || lease.UserId != userId)
                return;
            state.Leases.Remove(leaseId);
            if (state.ActiveLeaseId == leaseId)
            {
                lease.RevokeControl();
                state.ActiveLeaseId = null;
                state.Generation++;
            }
        }
    }

    public bool TryGetControllerCancellation(
        string instanceId, string userId, string? leaseId,
        out CancellationToken cancellationToken)
    {
        cancellationToken = CancellationToken.None;
        if (string.IsNullOrWhiteSpace(leaseId)) return false;
        var now = clock.GetUtcNow();
        foreach (var state in seats.Values)
        {
            lock (state)
            {
                Prune(state, now);
                if (state.ActiveLeaseId != leaseId ||
                    !state.Leases.TryGetValue(leaseId, out var lease)) continue;
                if (lease.InstanceId != instanceId || lease.UserId != userId)
                    return false;
                cancellationToken = lease.ControlCancellation.Token;
                return true;
            }
        }
        return false;
    }

    public bool IsController(string instanceId, string userId, string? leaseId) =>
        TryGetControllerCancellation(instanceId, userId, leaseId, out _);

    private SeatState RequiredState(string matchId, int seatIndex) =>
        seats.TryGetValue(SeatKey(matchId, seatIndex), out var state)
            ? state : throw new ControllerLeaseException(
                "controller_lease_expired", "This game-view lease expired. Reconnect the seat.");

    private static Lease RequiredLease(SeatState state, string leaseId, string userId)
    {
        if (!state.Leases.TryGetValue(leaseId, out var lease) || lease.UserId != userId)
            throw new ControllerLeaseException(
                "controller_lease_expired", "This game-view lease expired. Reconnect the seat.");
        return lease;
    }

    private static void Prune(SeatState state, DateTimeOffset now)
    {
        foreach (var expired in state.Leases.Values.Where(item => item.ExpiresAt <= now).ToArray())
        {
            expired.RevokeControl();
            state.Leases.Remove(expired.LeaseId);
        }
        if (state.ActiveLeaseId is not null && !state.Leases.ContainsKey(state.ActiveLeaseId))
        {
            state.ActiveLeaseId = null;
            state.Generation++;
        }
    }

    private static void RevokeActiveController(SeatState state)
    {
        if (state.ActiveLeaseId is not null &&
            state.Leases.TryGetValue(state.ActiveLeaseId, out var active))
            active.RevokeControl();
    }

    private static ControllerLeaseSnapshot Snapshot(
        SeatState state, Lease lease, DateTimeOffset now) => new(
            lease.LeaseId,
            state.ActiveLeaseId == lease.LeaseId ? "controller" : "viewer",
            lease.ExpiresAt,
            state.Generation,
            state.ActiveLeaseId is not null,
            Math.Max(0, (int)Math.Ceiling((lease.ExpiresAt - now).TotalSeconds)));

    private static string SeatKey(string matchId, int seatIndex) => $"{matchId}:{seatIndex}";

    private sealed class SeatState
    {
        public Dictionary<string, Lease> Leases { get; } = new(StringComparer.Ordinal);
        public string? ActiveLeaseId { get; set; }
        public long Generation { get; set; }
    }

    private sealed class Lease
    {
        public required string LeaseId { get; init; }
        public required string UserId { get; init; }
        public required string PlayInstanceId { get; init; }
        public required string InstanceId { get; init; }
        public DateTimeOffset ExpiresAt { get; set; }
        public CancellationTokenSource ControlCancellation { get; private set; } = new();

        public void BeginControl()
        {
            if (!ControlCancellation.IsCancellationRequested) return;
            ControlCancellation.Dispose();
            ControlCancellation = new CancellationTokenSource();
        }

        public void RevokeControl()
        {
            if (!ControlCancellation.IsCancellationRequested)
                ControlCancellation.Cancel();
        }
    }
}

public sealed record ControllerLeaseSnapshot(
    string LeaseId, string Role, DateTimeOffset ExpiresAt, long Generation,
    bool ControllerPresent, int ExpiresInSeconds);

public sealed class ControllerLeaseException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}
