using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Hubs;

public sealed class LobbyHub(
    ApplicationDbContext database,
    WaitingLobbyPresenceTracker waitingPresence) : Hub
{
    private const string JoinedLobbyKey = "joined-waiting-lobbies";
    public static string GroupName(string matchId) => $"match:{matchId}";

    public async Task JoinLobby(string matchId)
    {
        if (string.IsNullOrWhiteSpace(matchId) || matchId.Length > 96)
        {
            throw new HubException("invalid_match_id");
        }
        var lobby = await database.PortalMatches.SingleOrDefaultAsync(
            match => match.MatchId == matchId, Context.ConnectionAborted);
        if (lobby is null || !lobby.IsListed ||
            Context.User?.Identity?.IsAuthenticated != true && !lobby.AllowAnonymousSpectators)
        {
            throw new HubException("lobby_access_denied");
        }
        await Groups.AddToGroupAsync(Context.ConnectionId, GroupName(matchId));
        waitingPresence.Join(matchId, Context.ConnectionId);
        JoinedLobbies.Add(matchId);
        if (lobby.Status == "waiting")
        {
            lobby.UpdatedAt = DateTimeOffset.UtcNow;
            await database.SaveChangesAsync(Context.ConnectionAborted);
        }
    }

    public async Task LeaveLobby(string matchId)
    {
        waitingPresence.Leave(matchId, Context.ConnectionId);
        JoinedLobbies.Remove(matchId);
        await Groups.RemoveFromGroupAsync(Context.ConnectionId, GroupName(matchId));
        await TouchWaitingLobbyAsync(matchId, Context.ConnectionAborted);
    }

    public override async Task OnDisconnectedAsync(Exception? exception)
    {
        foreach (var matchId in JoinedLobbies.ToArray())
        {
            waitingPresence.Leave(matchId, Context.ConnectionId);
            await TouchWaitingLobbyAsync(matchId, CancellationToken.None);
        }
        await base.OnDisconnectedAsync(exception);
    }

    private async Task TouchWaitingLobbyAsync(string matchId, CancellationToken cancellationToken)
    {
        var lobby = await database.PortalMatches.SingleOrDefaultAsync(
            item => item.MatchId == matchId && item.Status == "waiting", cancellationToken);
        if (lobby is null) return;
        lobby.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(cancellationToken);
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
