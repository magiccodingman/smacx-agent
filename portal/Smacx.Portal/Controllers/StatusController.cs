using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Route("api/status")]
public sealed class StatusController(ControlPlaneClient control, ApplicationDbContext database) : ControllerBase
{
    [HttpGet]
    [AllowAnonymous]
    public async Task<ActionResult<ApiResponse<PortalStatus>>> Get()
    {
        var health = await control.HealthAsync(HttpContext.RequestAborted);
        var active = await database.PortalMatches.AsNoTracking().CountAsync(
            item => item.Status == "running", HttpContext.RequestAborted);
        var recoverable = await database.PortalMatches.AsNoTracking().CountAsync(
            item => item.Status == "parked" || item.Status == "error", HttpContext.RequestAborted);
        var aiSeats = await database.PortalLobbySeats.AsNoTracking().CountAsync(
            item => item.ControllerKind == "agent" && item.Status != "stopped",
            HttpContext.RequestAborted);
        var onlinePlayers = await database.PortalMatchMembers.AsNoTracking().CountAsync(
            item => item.LeftAt == null && database.PortalMatches.Any(match =>
                match.MatchId == item.MatchId && match.Status == "running"),
            HttpContext.RequestAborted);
        return ApiResponse<PortalStatus>.Success(new PortalStatus(
            "SMACX Control Center",
            typeof(StatusController).Assembly.GetName().Version?.ToString(3) ?? "0.1.0",
            health.Connected,
            health.State,
            active,
            onlinePlayers,
            aiSeats,
            recoverable));
    }

    [HttpGet("activity")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<PortalActivityItem>>>> Activity()
    {
        var items = await (
                from activity in database.PortalMatchEvents.AsNoTracking()
                join match in database.PortalMatches.AsNoTracking()
                    on activity.MatchId equals match.MatchId
                where match.IsListed
                orderby activity.CreatedAt descending
                select new PortalActivityItem(
                    match.MatchId, match.DisplayName, match.Status,
                    activity.Summary, activity.CreatedAt))
            .Take(8)
            .ToArrayAsync(HttpContext.RequestAborted);
        return ApiResponse<IReadOnlyList<PortalActivityItem>>.Success(items);
    }
}
