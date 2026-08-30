using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Route("api/admin")]
[Authorize(Roles = "Administrator")]
public sealed class AdministrationController(
    ApplicationDbContext database,
    ControlPlaneClient control) : ControllerBase
{
    [HttpGet("snapshot")]
    public async Task<ActionResult<ApiResponse<AdminSnapshot>>> Snapshot()
    {
        try
        {
            async Task<JsonElement> Read(string path, string? property = null)
            {
                using var document = await control.GetRawAsync(path, HttpContext.RequestAborted);
                var value = property is null ? document.RootElement : document.RootElement.GetProperty(property);
                return value.Clone();
            }
            var providers = await Read("api/v1/providers", "providers");
            var agents = await Read("api/v1/agents", "agents");
            var profiles = await Read("api/v1/harness-profiles", "harness_profiles");
            var runs = await Read("api/v1/harness-runs", "harness_runs");
            var graphiti = await Read("api/v1/graphiti");
            var workers = await Read("api/v1/workers");
            var operations = await Read("api/v1/operations/status");
            var schedules = await Read("api/v1/schedules", "schedules");
            var backups = await Read("api/v1/backups", "backups");
            return ApiResponse<AdminSnapshot>.Success(new(
                providers, agents, profiles, runs, graphiti, workers,
                operations, schedules, backups));
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<AdminSnapshot>.Failure(exception.Code, exception.Message));
        }
    }

    [HttpPost("providers")]
    public Task<ActionResult<ApiResponse<JsonElement>>> ConfigureProvider(
        ProviderConfigurationRequest request) => Proxy("api/v1/providers", new
        {
            display_name = request.DisplayName,
            base_url = request.BaseUrl,
            api_key = request.ApiKey,
            provider_id = request.ProviderId,
            default_model_id = request.DefaultModelId,
            context_length_override = request.ContextLengthOverride,
        }, "provider");

    [HttpPost("providers/{providerId}/discover")]
    public Task<ActionResult<ApiResponse<JsonElement>>> DiscoverProvider(string providerId) =>
        Proxy($"api/v1/providers/{providerId}/discover", new { }, "provider");

    [HttpPost("providers/{providerId}/select")]
    public Task<ActionResult<ApiResponse<JsonElement>>> SelectProvider(
        string providerId, ProviderModelSelectionRequest request) =>
        Proxy($"api/v1/providers/{providerId}/select", new
        {
            model_id = request.ModelId,
            context_length_override = request.ContextLengthOverride,
        }, "provider");

    [HttpGet("ai-profiles")]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<AiProfileVersion>>>> Profiles()
    {
        var items = await database.PortalAiProfileVersions.AsNoTracking()
            .OrderBy(item => item.DisplayName).ThenByDescending(item => item.Version)
            .Select(item => ToContract(item)).ToArrayAsync(HttpContext.RequestAborted);
        return ApiResponse<IReadOnlyList<AiProfileVersion>>.Success(items);
    }

    [HttpPost("ai-profiles")]
    public async Task<ActionResult<ApiResponse<AiProfileVersion>>> CreateProfile(
        AiProfileVersionRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.DisplayName) || request.DisplayName.Trim().Length > 160 ||
            string.IsNullOrWhiteSpace(request.ProviderId) || string.IsNullOrWhiteSpace(request.ModelId) ||
            request.ReasoningEffort is not ("none" or "minimal" or "low" or "medium" or "high" or "xhigh" or "max" or "ultra") ||
            request.ContextLength is not null and (< 1024 or > 16_777_216))
            return BadRequest(ApiResponse<AiProfileVersion>.Failure(
                "invalid_ai_profile", "Check the profile name, provider, model, reasoning, and context limit."));
        var stableId = request.StableProfileId ?? $"profile-{Guid.NewGuid():N}";
        var latest = await database.PortalAiProfileVersions
            .Where(item => item.StableProfileId == stableId)
            .OrderByDescending(item => item.Version).FirstOrDefaultAsync(HttpContext.RequestAborted);
        if (request.StableProfileId is not null && latest is null)
            return NotFound(ApiResponse<AiProfileVersion>.Failure(
                "profile_not_found", "The profile family was not found."));
        var version = (latest?.Version ?? 0) + 1;
        var agentId = $"agent-{Guid.NewGuid():N}";
        try
        {
            using var created = await control.PostRawAsync("api/v1/agents", new
            {
                agent_id = agentId,
                display_name = $"{request.DisplayName.Trim()} v{version}",
                personality_ref = "none",
            }, HttpContext.RequestAborted);
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<AiProfileVersion>.Failure(exception.Code, exception.Message));
        }
        var entity = new PortalAiProfileVersion
        {
            StableProfileId = stableId,
            Version = version,
            DisplayName = request.DisplayName.Trim(),
            AgentId = agentId,
            ProviderId = request.ProviderId,
            ModelId = request.ModelId.Trim(),
            ReasoningEffort = request.ReasoningEffort,
            ContextLength = request.ContextLength,
            Notes = string.IsNullOrWhiteSpace(request.Notes) ? null : request.Notes.Trim(),
            PersonalityCardId = "none",
        };
        database.PortalAiProfileVersions.Add(entity);
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        return CreatedAtAction(nameof(Profiles), ApiResponse<AiProfileVersion>.Success(ToContract(entity)));
    }

    [HttpPost("ai-profiles/{profileVersionId}/deactivate")]
    public async Task<ActionResult<ApiResponse<AiProfileVersion>>> DeactivateProfile(string profileVersionId)
    {
        var item = await database.PortalAiProfileVersions.FindAsync([profileVersionId], HttpContext.RequestAborted);
        if (item is null) return NotFound();
        item.Active = false;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        return ApiResponse<AiProfileVersion>.Success(ToContract(item));
    }

    [HttpPost("game-sources")]
    public Task<ActionResult<ApiResponse<JsonElement>>> ValidateSource(GameSourceRequest request) =>
        Proxy("api/v1/game-sources/validate", new
        {
            display_name = request.DisplayName, host_path = request.HostPath,
        }, "game_source");

    [HttpGet("runtime")]
    public async Task<ActionResult<ApiResponse<AdminRuntimeSnapshot>>> Runtime()
    {
        try
        {
            using var sources = await control.GetRawAsync("api/v1/game-sources", HttpContext.RequestAborted);
            using var runtimes = await control.GetRawAsync("api/v1/runtimes", HttpContext.RequestAborted);
            using var workers = await control.GetRawAsync("api/v1/workers", HttpContext.RequestAborted);
            return ApiResponse<AdminRuntimeSnapshot>.Success(new(
                sources.RootElement.GetProperty("game_sources").Clone(),
                runtimes.RootElement.GetProperty("runtimes").Clone(),
                workers.RootElement.Clone()));
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<AdminRuntimeSnapshot>.Failure(exception.Code, exception.Message));
        }
    }

    [HttpPost("runtimes")]
    public Task<ActionResult<ApiResponse<JsonElement>>> ImportRuntime(RuntimeImportRequest request) =>
        Proxy("api/v1/runtimes/import-proton", new
        {
            display_name = request.DisplayName, source_host_path = request.SourceHostPath,
        }, "runtime");

    [HttpPost("graphiti")]
    public Task<ActionResult<ApiResponse<JsonElement>>> Graphiti([FromBody] JsonElement request) =>
        Proxy("api/v1/graphiti", new
        {
            enabled = request.TryGetProperty("enabled", out var value) && value.GetBoolean(),
        });

    [HttpPost("backups")]
    public Task<ActionResult<ApiResponse<JsonElement>>> Backup(BackupRequest request) =>
        Proxy("api/v1/backups", new
        {
            include_secrets = request.IncludeSecrets, include_workers = request.IncludeWorkers,
        }, "backup");

    [HttpPost("backups/{backupId}/verify")]
    public Task<ActionResult<ApiResponse<JsonElement>>> VerifyBackup(string backupId) =>
        Proxy($"api/v1/backups/{backupId}/verify", new { });

    [HttpPost("schedules")]
    public Task<ActionResult<ApiResponse<JsonElement>>> Schedule(ScheduleRequest request) =>
        Proxy("api/v1/schedules", new
        {
            display_name = request.DisplayName, operation_kind = request.OperationKind,
            target_kind = request.TargetKind, target_id = request.TargetId,
            interval_seconds = request.IntervalSeconds,
        }, "schedule");

    [HttpPost("schedules/{scheduleId}/{action}")]
    public Task<ActionResult<ApiResponse<JsonElement>>> ScheduleAction(string scheduleId, string action)
    {
        if (action is not ("activate" or "pause" or "disable"))
            return Task.FromResult<ActionResult<ApiResponse<JsonElement>>>(BadRequest(
                ApiResponse<JsonElement>.Failure("invalid_schedule_action", "Invalid schedule action.")));
        return Proxy($"api/v1/schedules/{scheduleId}/{action}", new { }, "schedule");
    }

    private async Task<ActionResult<ApiResponse<JsonElement>>> Proxy(
        string path, object body, string? property = null)
    {
        try
        {
            using var document = await control.PostRawAsync(path, body, HttpContext.RequestAborted);
            var value = property is null ? document.RootElement : document.RootElement.GetProperty(property);
            return ApiResponse<JsonElement>.Success(value.Clone());
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<JsonElement>.Failure(exception.Code, exception.Message));
        }
    }

    private static AiProfileVersion ToContract(PortalAiProfileVersion item) => new(
        item.ProfileVersionId, item.StableProfileId, item.Version, item.DisplayName,
        item.AgentId, item.ProviderId, item.ModelId, item.ReasoningEffort,
        item.ContextLength, item.Notes, item.Active, item.PersonalityCardId, item.CreatedAt);
}
