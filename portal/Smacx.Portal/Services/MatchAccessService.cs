using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;
using Smacx.Portal.Infrastructure;

namespace Smacx.Portal.Services;

public sealed class MatchAccessService(ApplicationDbContext database)
{
    public async Task<bool> IsParticipantAsync(
        string matchId,
        string? userId,
        CancellationToken cancellationToken = default)
    {
        if (userId is null) return false;
        if (await database.PortalMatchParticipants.AsNoTracking().AnyAsync(
                item => item.MatchId == matchId && item.UserId == userId,
                cancellationToken)) return true;
        // Compatibility for campaigns created before durable participation was
        // introduced. LeftAt deliberately does not erase participation.
        return await database.PortalMatchMembers.AsNoTracking().AnyAsync(
            item => item.MatchId == matchId && item.UserId == userId &&
                item.SeatIndex != null && item.Role == "player",
            cancellationToken);
    }

    public async Task<bool> CanSpectateAsync(
        PortalMatchProfile match,
        string? userId,
        bool administrator,
        CancellationToken cancellationToken = default)
    {
        if (userId is null || await IsParticipantAsync(match.MatchId, userId, cancellationToken))
            return false;
        if (administrator || match.AllowSpectators) return true;

        // An unattended simulation must never become an opaque process that its
        // owner cannot observe. This is an effective access rule rather than a
        // mutation of the lobby's preference: if a human later joins a restored
        // campaign, the ordinary opt-in spectator policy applies again.
        return match.Status == "running" && !await database.PortalLobbySeats.AsNoTracking()
            .AnyAsync(item => item.MatchId == match.MatchId &&
                item.ControllerKind == "human", cancellationToken);
    }

    public async Task RecordAssignedPlayersAsync(
        string matchId,
        CancellationToken cancellationToken = default)
    {
        var seats = await database.PortalLobbySeats
            .Where(item => item.MatchId == matchId && item.ControllerKind == "human" &&
                item.UserId != null)
            .Select(item => new { item.UserId, item.SeatIndex })
            .ToArrayAsync(cancellationToken);
        var existing = await database.PortalMatchParticipants
            .Where(item => item.MatchId == matchId)
            .Select(item => item.UserId)
            .ToArrayAsync(cancellationToken);
        foreach (var seat in seats.Where(item => !existing.Contains(item.UserId!)))
            database.PortalMatchParticipants.Add(new PortalMatchParticipant
            {
                MatchId = matchId,
                UserId = seat.UserId!,
                FirstSeatIndex = seat.SeatIndex,
            });
        if (database.ChangeTracker.HasChanges())
            await database.SaveChangesAsync(cancellationToken);
    }
}
