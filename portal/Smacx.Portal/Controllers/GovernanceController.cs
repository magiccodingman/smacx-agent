using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Hubs;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Authorize]
[Route("api/lobbies/{matchId}/governance")]
public sealed class GovernanceController(
    ApplicationDbContext database,
    UserManager<ApplicationUser> users,
    MatchGovernanceService governance,
    IHubContext<LobbyHub> hub) : ControllerBase
{
    [HttpGet]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<GovernanceProposal>>>> List(
        string matchId)
    {
        if (!await IsMemberAsync(matchId)) return Forbid();
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        return ApiResponse<IReadOnlyList<GovernanceProposal>>.Success(
            await governance.ListAsync(matchId, userId, HttpContext.RequestAborted));
    }

    [HttpPost]
    public async Task<ActionResult<ApiResponse<GovernanceProposal>>> Create(
        string matchId, CreateGovernanceProposalRequest request)
    {
        var user = await users.GetUserAsync(User);
        if (user is null) return Unauthorized();
        var match = await database.PortalMatches.SingleOrDefaultAsync(
            item => item.MatchId == matchId, HttpContext.RequestAborted);
        if (match is null) return NotFound();
        if (!await IsMemberAsync(matchId)) return Forbid();
        try
        {
            var created = await governance.CreateAsync(
                match, user, request, HttpContext.RequestAborted);
            await hub.Clients.Group(LobbyHub.GroupName(matchId)).SendAsync(
                "GovernanceChanged", created, HttpContext.RequestAborted);
            return ApiResponse<GovernanceProposal>.Success(created);
        }
        catch (GovernanceException exception)
        {
            return BadRequest(ApiResponse<GovernanceProposal>.Failure(
                exception.Code, exception.Message));
        }
    }

    [HttpPost("{proposalId}/vote")]
    public async Task<ActionResult<ApiResponse<GovernanceProposal>>> Vote(
        string matchId, string proposalId, GovernanceVoteRequest request)
    {
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        if (userId is null) return Unauthorized();
        if (!await IsMemberAsync(matchId)) return Forbid();
        try
        {
            var result = await governance.VoteAsync(
                matchId, proposalId, userId, request.Vote,
                HttpContext.RequestAborted);
            await hub.Clients.Group(LobbyHub.GroupName(matchId)).SendAsync(
                "GovernanceChanged", result, HttpContext.RequestAborted);
            return ApiResponse<GovernanceProposal>.Success(result);
        }
        catch (GovernanceException exception)
        {
            return BadRequest(ApiResponse<GovernanceProposal>.Failure(
                exception.Code, exception.Message));
        }
    }

    [HttpGet("maintenance")]
    public async Task<ActionResult<ApiResponse<MaintenanceProgress>>> Maintenance(
        string matchId)
    {
        if (!await IsMemberAsync(matchId) && !User.IsInRole("Administrator")) return Forbid();
        var row = await database.PortalMaintenanceOperations.AsNoTracking()
            .Where(item => item.MatchId == matchId)
            .OrderByDescending(item => item.UpdatedAt)
            .FirstOrDefaultAsync(HttpContext.RequestAborted);
        var progress = row is null
            ? new MaintenanceProgress(null, matchId, "none", "idle", "playing",
                "No maintenance operation is active.", 0, 0, null, null,
                DateTimeOffset.UtcNow)
            : new MaintenanceProgress(
                row.OperationId, row.MatchId, row.Kind, row.Status, row.Phase,
                row.Summary, row.CompletedSteps, row.TotalSteps, row.StableTurn,
                row.StableYear, row.UpdatedAt, row.CanCancel);
        return ApiResponse<MaintenanceProgress>.Success(progress);
    }

    private async Task<bool> IsMemberAsync(string matchId)
    {
        if (User.IsInRole("Administrator")) return true;
        var userId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        return userId is not null && await database.PortalMatchMembers.AsNoTracking().AnyAsync(
            item => item.MatchId == matchId && item.UserId == userId && item.LeftAt == null,
            HttpContext.RequestAborted);
    }
}
