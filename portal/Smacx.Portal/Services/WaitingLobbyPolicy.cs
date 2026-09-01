using System.Collections.Concurrent;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;

namespace Smacx.Portal.Services;

public sealed class WaitingLobbyPolicy
{
    public const int MemberLimit = 5;
    public TimeSpan IdleLifetime { get; }

    public WaitingLobbyPolicy()
    {
        const int defaultHours = 24;
        var configured = Environment.GetEnvironmentVariable("SMACX_WAITING_LOBBY_TTL_HOURS");
        if (string.IsNullOrWhiteSpace(configured))
        {
            IdleLifetime = TimeSpan.FromHours(defaultHours);
            return;
        }
        if (!int.TryParse(configured, out var hours) || hours is < 1 or > 720)
            throw new InvalidOperationException(
                "SMACX_WAITING_LOBBY_TTL_HOURS must be an integer between 1 and 720.");
        IdleLifetime = TimeSpan.FromHours(hours);
    }
}

public sealed class WaitingLobbyPresenceTracker
{
    private readonly ConcurrentDictionary<string, ConcurrentDictionary<string, byte>> matches =
        new(StringComparer.Ordinal);

    public void Join(string matchId, string connectionId)
    {
        matches.GetOrAdd(matchId, _ => new(StringComparer.Ordinal))[connectionId] = 0;
    }

    public void Leave(string matchId, string connectionId)
    {
        if (!matches.TryGetValue(matchId, out var connections)) return;
        connections.TryRemove(connectionId, out _);
        if (connections.IsEmpty) matches.TryRemove(matchId, out _);
    }

    public bool IsActive(string matchId) =>
        matches.TryGetValue(matchId, out var connections) && !connections.IsEmpty;
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
        var cutoff = now - policy.IdleLifetime;
        var candidates = await database.PortalMatches
            .Where(item => item.Status == "waiting" && item.UpdatedAt <= cutoff)
            .OrderBy(item => item.UpdatedAt)
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
