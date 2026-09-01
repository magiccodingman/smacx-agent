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
public sealed class CatalogController(
    ControlPlaneClient control,
    ApplicationDbContext database,
    PersonalityCardLibrary personalityCards) : ControllerBase
{
    [HttpGet("lobby")]
    public async Task<ActionResult<ApiResponse<LobbyCatalog>>> Lobby()
    {
        try
        {
            var sources = await control.ListGameSourcesAsync(HttpContext.RequestAborted);
            var runtimes = await control.ListRuntimesAsync(HttpContext.RequestAborted);
            var agents = await control.ListAgentsAsync(HttpContext.RequestAborted);
            using var providersDocument = await control.GetRawAsync("api/v1/providers", HttpContext.RequestAborted);
            var readyModels = providersDocument.RootElement.GetProperty("providers").EnumerateArray()
                .Where(provider => provider.GetProperty("status").GetString() == "healthy")
                .SelectMany(provider => provider.GetProperty("models").EnumerateArray().Select(model => (
                    ProviderId: provider.GetProperty("provider_id").GetString()!,
                    ModelId: model.GetProperty("model_id").GetString()!)))
                .ToHashSet();
            var activeProfiles = await database.PortalAiProfiles.AsNoTracking()
                .Where(item => item.Active)
                .OrderBy(item => item.DisplayName)
                .ToArrayAsync(HttpContext.RequestAborted);
            var agentStatus = agents.ToDictionary(item => item.AgentId, item => item.Status);
            return ApiResponse<LobbyCatalog>.Success(new(
                sources.Select(item => new CatalogItem(item.Id, item.DisplayName, item.Status)).ToArray(),
                runtimes.Select(item => new CatalogItem(item.Id, item.DisplayName, item.Status)).ToArray(),
                activeProfiles.Where(item => readyModels.Contains((item.ProviderId, item.ModelId))).Select(item => new CatalogItem(
                    item.AgentId, $"{item.DisplayName} · {item.ModelId} · {item.ReasoningEffort}",
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

    [HttpGet("factions-personalities")]
    public ActionResult<ApiResponse<FactionPersonalityCatalog>> FactionsAndPersonalities()
    {
        var builtIns = FactionCatalog.All.SelectMany(faction =>
            personalityCards.ForFaction(faction.Id).Select(card =>
                new PersonalityCatalogItem(card.Kind, card.Kind, card.DisplayName,
                    card.Description, faction.Id))).ToArray();
        return ApiResponse<FactionPersonalityCatalog>.Success(new(
            FactionCatalog.All, BuiltInPersonalityCatalog.Modes, builtIns));
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
