using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Hubs;

[Authorize]
public sealed class LobbyHub(
    ApplicationDbContext database,
    WaitingLobbyPresenceTracker waitingPresence,
    AccountConnectionRegistry accountConnections) : Hub
{
    private const string JoinedLobbyKey = "joined-waiting-lobbies";
    private const string AccountRevocationKey = "account-revocation-registration";
    public static string GroupName(string matchId) => $"match:{matchId}";
    public const string DirectoryGroup = "lobby-directory";

    public Task JoinDirectory() => Groups.AddToGroupAsync(Context.ConnectionId, DirectoryGroup);

    public override Task OnConnectedAsync()
    {
        var userId = Context.User?.FindFirstValue(ClaimTypes.NameIdentifier);
        if (userId is not null)
            Context.Items[AccountRevocationKey] = accountConnections.Token(userId)
                .Register(Context.Abort);
        return base.OnConnectedAsync();
    }

    public async Task JoinLobby(string matchId)
    {
        if (string.IsNullOrWhiteSpace(matchId) || matchId.Length > 96)
        {
            throw new HubException("invalid_match_id");
        }
        var lobby = await database.PortalMatches.SingleOrDefaultAsync(
            match => match.MatchId == matchId, Context.ConnectionAborted);
        if (lobby is null || !lobby.IsListed ||
            Context.User?.Identity?.IsAuthenticated != true)
        {
            throw new HubException("lobby_access_denied");
        }
        await Groups.AddToGroupAsync(Context.ConnectionId, GroupName(matchId));
        var userId = Context.User?.FindFirstValue(ClaimTypes.NameIdentifier);
        waitingPresence.Join(matchId, Context.ConnectionId, userId);
        JoinedLobbies.Add(matchId);
        if (lobby.Status == "waiting")
        {
            lobby.WaitingVacantSince = null;
            if (userId is not null)
            {
                var seat = await database.PortalLobbySeats
                    .OrderBy(item => item.SeatIndex)
                    .FirstOrDefaultAsync(item => item.MatchId == matchId &&
                        item.UserId == userId && item.ControllerKind == "human" &&
                        item.JoinMode == "browser" && item.Status == "ready",
                        Context.ConnectionAborted);
                if (seat is not null)
                {
                    seat.ConnectionState = "connected";
                    seat.LastBrowserSeenAt = DateTimeOffset.UtcNow;
                    seat.UpdatedAt = DateTimeOffset.UtcNow;
                }
            }
            lobby.UpdatedAt = DateTimeOffset.UtcNow;
            await database.SaveChangesAsync(Context.ConnectionAborted);
            await Clients.Group(GroupName(matchId)).SendAsync(
                "LobbyChanged", matchId, Context.ConnectionAborted);
            await Clients.Group(DirectoryGroup).SendAsync(
                "LobbyDirectoryChanged", matchId, Context.ConnectionAborted);
        }
    }

    public async Task LeaveLobby(string matchId)
    {
        waitingPresence.Leave(matchId, Context.ConnectionId);
        JoinedLobbies.Remove(matchId);
        await Groups.RemoveFromGroupAsync(Context.ConnectionId, GroupName(matchId));
        await MarkDisconnectedAsync(matchId, Context.ConnectionAborted);
    }

    public override async Task OnDisconnectedAsync(Exception? exception)
    {
        foreach (var matchId in JoinedLobbies.ToArray())
        {
            waitingPresence.Leave(matchId, Context.ConnectionId);
            await MarkDisconnectedAsync(matchId, CancellationToken.None);
        }
        if (Context.Items.Remove(AccountRevocationKey, out var registration) &&
            registration is CancellationTokenRegistration tokenRegistration)
            tokenRegistration.Dispose();
        await base.OnDisconnectedAsync(exception);
    }

    private async Task MarkDisconnectedAsync(string matchId, CancellationToken cancellationToken)
    {
        var lobby = await database.PortalMatches.SingleOrDefaultAsync(
            item => item.MatchId == matchId && item.Status == "waiting", cancellationToken);
        if (lobby is null) return;
        var now = DateTimeOffset.UtcNow;
        var userId = Context.User?.FindFirstValue(ClaimTypes.NameIdentifier);
        if (userId is not null && !waitingPresence.IsUserActive(matchId, userId))
        {
            var seat = await database.PortalLobbySeats
                .OrderBy(item => item.SeatIndex)
                .FirstOrDefaultAsync(item => item.MatchId == matchId &&
                    item.UserId == userId && item.ControllerKind == "human" &&
                    item.JoinMode == "browser" && item.Status == "ready",
                    cancellationToken);
            if (seat is not null)
            {
                seat.ConnectionState = "reconnecting";
                seat.LastBrowserSeenAt = now;
                seat.UpdatedAt = now;
            }
        }
        if (!waitingPresence.IsActive(matchId)) lobby.WaitingVacantSince ??= now;
        lobby.UpdatedAt = now;
        await database.SaveChangesAsync(cancellationToken);
        await Clients.Group(GroupName(matchId)).SendAsync(
            "LobbyChanged", matchId, cancellationToken);
        await Clients.Group(DirectoryGroup).SendAsync(
            "LobbyDirectoryChanged", matchId, cancellationToken);
    }

    private HashSet<string> JoinedLobbies
    {
        get
        {
            if (Context.Items.TryGetValue(JoinedLobbyKey, out var value) &&
                value is HashSet<string> joined) return joined;
            var created = new HashSet<string>(StringComparer.Ordinal);
            Context.Items[JoinedLobbyKey] = created;
            return created;
        }
    }
}
