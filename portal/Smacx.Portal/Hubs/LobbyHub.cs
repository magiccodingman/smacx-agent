using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;

namespace Smacx.Portal.Hubs;

public sealed class LobbyHub(ApplicationDbContext database) : Hub
{
    public static string GroupName(string matchId) => $"match:{matchId}";

    public async Task JoinLobby(string matchId)
    {
        if (string.IsNullOrWhiteSpace(matchId) || matchId.Length > 96)
        {
            throw new HubException("invalid_match_id");
        }
        var lobby = await database.PortalMatches.AsNoTracking().SingleOrDefaultAsync(
            match => match.MatchId == matchId, Context.ConnectionAborted);
        if (lobby is null || !lobby.IsListed ||
            Context.User?.Identity?.IsAuthenticated != true && !lobby.AllowAnonymousSpectators)
        {
            throw new HubException("lobby_access_denied");
        }
        await Groups.AddToGroupAsync(Context.ConnectionId, GroupName(matchId));
    }

    public Task LeaveLobby(string matchId) =>
        Groups.RemoveFromGroupAsync(Context.ConnectionId, GroupName(matchId));
}
