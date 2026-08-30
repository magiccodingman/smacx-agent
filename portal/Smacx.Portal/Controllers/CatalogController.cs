using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Route("api/catalog")]
[Authorize]
public sealed class CatalogController(ControlPlaneClient control, ApplicationDbContext database) : ControllerBase
{
    [HttpGet("lobby")]
    public async Task<ActionResult<ApiResponse<LobbyCatalog>>> Lobby()
    {
        try
        {
            var sources = await control.ListGameSourcesAsync(HttpContext.RequestAborted);
            var runtimes = await control.ListRuntimesAsync(HttpContext.RequestAborted);
            var agents = await control.ListAgentsAsync(HttpContext.RequestAborted);
            var activeProfiles = await database.PortalAiProfileVersions.AsNoTracking()
                .Where(item => item.Active).ToArrayAsync(HttpContext.RequestAborted);
            var agentStatus = agents.ToDictionary(item => item.AgentId, item => item.Status);
            return ApiResponse<LobbyCatalog>.Success(new(
                sources.Select(item => new CatalogItem(item.Id, item.DisplayName, item.Status)).ToArray(),
                runtimes.Select(item => new CatalogItem(item.Id, item.DisplayName, item.Status)).ToArray(),
                activeProfiles.Select(item => new CatalogItem(
                    item.AgentId, $"{item.DisplayName} v{item.Version} · {item.ModelId} · {item.ReasoningEffort}",
                    agentStatus.GetValueOrDefault(item.AgentId, "configured"))).ToArray(),
                true));
        }
        catch (ControlPlaneException exception)
        {
            return ApiResponse<LobbyCatalog>.Success(new([], [], [], false, exception.Code));
        }
        catch (HttpRequestException)
        {
            return ApiResponse<LobbyCatalog>.Success(new([], [], [], false, "control_unavailable"));
        }
    }

    [HttpGet("scenarios/{gameSourceId}")]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<ScenarioCatalogItem>>>> Scenarios(string gameSourceId)
    {
        try
        {
            return ApiResponse<IReadOnlyList<ScenarioCatalogItem>>.Success(
                await control.ListScenariosAsync(gameSourceId, HttpContext.RequestAborted));
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<IReadOnlyList<ScenarioCatalogItem>>.Failure(exception.Code, exception.Message));
        }
    }
}
