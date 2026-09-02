using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;

namespace Smacx.Portal.Services;

public sealed class WaitingLobbyPolicy
{
    public const int MemberLimit = 5;
    public TimeSpan AbandonLifetime { get; }
    public TimeSpan SeatReconnectGrace { get; }

    public WaitingLobbyPolicy()
    {
        const int defaultMinutes = 30;
        var configured = Environment.GetEnvironmentVariable("SMACX_WAITING_LOBBY_ABANDON_MINUTES");
        if (string.IsNullOrWhiteSpace(configured))
        {
            AbandonLifetime = TimeSpan.FromMinutes(defaultMinutes);
        }
        else if (!int.TryParse(configured, out var minutes) || minutes is < 5 or > 1440)
        {
            throw new InvalidOperationException(
                "SMACX_WAITING_LOBBY_ABANDON_MINUTES must be an integer between 5 and 1440.");
        }
        else
        {
            AbandonLifetime = TimeSpan.FromMinutes(minutes);
        }

        const int defaultGraceSeconds = 30;
        configured = Environment.GetEnvironmentVariable("SMACX_STAGING_SEAT_GRACE_SECONDS");
        if (string.IsNullOrWhiteSpace(configured))
        {
            SeatReconnectGrace = TimeSpan.FromSeconds(defaultGraceSeconds);
        }
        else if (!int.TryParse(configured, out var seconds) || seconds is < 10 or > 300)
        {
            throw new InvalidOperationException(
                "SMACX_STAGING_SEAT_GRACE_SECONDS must be an integer between 10 and 300.");
        }
        else
        {
            SeatReconnectGrace = TimeSpan.FromSeconds(seconds);
        }
    }
}

public sealed class WaitingLobbyPresenceTracker
{
    private readonly object sync = new();
    private readonly Dictionary<string, Dictionary<string, string?>> matches =
        new(StringComparer.Ordinal);

    public void Join(string matchId, string connectionId, string? userId = null)
    {
        lock (sync)
        {
            if (!matches.TryGetValue(matchId, out var connections))
            {
                connections = new(StringComparer.Ordinal);
                matches[matchId] = connections;
            }
            connections[connectionId] = userId;
        }
    }

    public void Leave(string matchId, string connectionId)
    {
        lock (sync)
        {
            if (!matches.TryGetValue(matchId, out var connections)) return;
            connections.Remove(connectionId);
            if (connections.Count == 0) matches.Remove(matchId);
        }
    }

    public bool IsActive(string matchId)
    {
        lock (sync)
            return matches.TryGetValue(matchId, out var connections) && connections.Count > 0;
    }

    public bool IsUserActive(string matchId, string userId)
    {
        lock (sync)
            return matches.TryGetValue(matchId, out var connections) &&
                connections.Values.Any(value => value == userId);
    }
}

public static class WaitingLobbySeatLifecycle
{
    public static async Task<IReadOnlyList<string>> SynchronizeAsync(
        ApplicationDbContext database,
        WaitingLobbyPresenceTracker presence,
        WaitingLobbyPolicy policy,
        DateTimeOffset now,
        CancellationToken cancellationToken = default)
    {
        var seats = await database.PortalLobbySeats
            .Where(seat => seat.ControllerKind == "human" && seat.JoinMode == "browser" &&
                seat.Status == "ready" && seat.UserId != null &&
                database.PortalMatches.Any(match => match.MatchId == seat.MatchId &&
                    match.Status == "waiting"))
            .ToArrayAsync(cancellationToken);
        if (seats.Length == 0) return [];

        var changedMatches = new HashSet<string>(StringComparer.Ordinal);
        foreach (var seat in seats)
        {
            if (presence.IsUserActive(seat.MatchId, seat.UserId!))
            {
                if (seat.ConnectionState == "connected") continue;
                seat.ConnectionState = "connected";
                seat.LastBrowserSeenAt = now;
                seat.UpdatedAt = now;
                changedMatches.Add(seat.MatchId);
                continue;
            }

            if (seat.ConnectionState != "reconnecting" || seat.LastBrowserSeenAt is null)
            {
                seat.ConnectionState = "reconnecting";
                seat.LastBrowserSeenAt = now;
                seat.UpdatedAt = now;
                changedMatches.Add(seat.MatchId);
                continue;
            }

            if (seat.LastBrowserSeenAt.Value + policy.SeatReconnectGrace > now) continue;
            var oldUserId = seat.UserId;
            ResetToOpen(seat, now);
            var member = await database.PortalMatchMembers.SingleOrDefaultAsync(item =>
                item.MatchId == seat.MatchId && item.UserId == oldUserId, cancellationToken);
            if (member is not null)
            {
                member.SeatIndex = null;
                if (member.Role != "owner") member.LeftAt = now;
            }
            changedMatches.Add(seat.MatchId);
        }

        if (changedMatches.Count == 0) return [];
        var profiles = await database.PortalMatches
            .Where(match => changedMatches.Contains(match.MatchId))
            .ToArrayAsync(cancellationToken);
        foreach (var profile in profiles) profile.UpdatedAt = now;
        await database.SaveChangesAsync(cancellationToken);
        return changedMatches.ToArray();
    }

    public static void ResetToOpen(PortalLobbySeat seat, DateTimeOffset? now = null)
    {
        seat.ControllerKind = "open";
        seat.AgentId = null;
        seat.AiProfileId = null;
        seat.UserId = null;
        seat.PlayerHandle = null;
        seat.JoinMode = "browser";
        seat.RequestedFactionId = FactionCatalog.Random;
        seat.ResolvedFactionKey = null;
        seat.LeaderName = null;
        seat.RequestedPersonalityId = "standard";
        seat.PersonalityCardId = "none";
        seat.PersonalityName = null;
        seat.PersonalityPrompt = null;
        seat.PersonalityPromptSha256 = null;
        seat.FactionId = null;
        seat.FactionName = null;
        seat.ControlInstanceId = null;
        seat.Status = "open";
        seat.ConnectionState = "unknown";
        seat.LastBrowserSeenAt = null;
        seat.LastWorkerSeenAt = null;
        seat.IsManagedHost = false;
        seat.TemporaryControllerKind = "none";
        seat.DelegationStatus = "none";
        seat.UpdatedAt = now ?? DateTimeOffset.UtcNow;
    }
}

public static class WaitingLobbyExpiration
{
    public static async Task<IReadOnlyList<string>> ExpireAsync(
        ApplicationDbContext database,
        WaitingLobbyPresenceTracker presence,
        WaitingLobbyPolicy policy,
        DateTimeOffset now,
        CancellationToken cancellationToken = default)
    {
        var cutoff = now - policy.AbandonLifetime;
        var candidates = await database.PortalMatches
            .Where(item => item.Status == "waiting" && item.WaitingVacantSince != null &&
                item.WaitingVacantSince <= cutoff)
            .OrderBy(item => item.WaitingVacantSince)
            .Take(50)
            .ToArrayAsync(cancellationToken);
        var expired = candidates.Where(item => !presence.IsActive(item.MatchId)).ToArray();
        if (expired.Length == 0) return [];

        var matchIds = expired.Select(item => item.MatchId).ToArray();
        var provisionalCandidates = await database.PortalLobbySeats.AsNoTracking()
            .Where(item => matchIds.Contains(item.MatchId) && item.UserId != null)
            .Select(item => item.UserId!)
            .Distinct()
            .ToArrayAsync(cancellationToken);
        database.PortalMatches.RemoveRange(expired);
        await database.SaveChangesAsync(cancellationToken);

        if (provisionalCandidates.Length > 0)
        {
            var orphans = await database.Users
                .Where(item => provisionalCandidates.Contains(item.Id) && item.IsProvisional &&
                    !database.PortalLobbySeats.Any(seat => seat.UserId == item.Id))
                .ToArrayAsync(cancellationToken);
            if (orphans.Length > 0)
            {
                database.Users.RemoveRange(orphans);
                await database.SaveChangesAsync(cancellationToken);
            }
        }
        return matchIds;
    }
}
