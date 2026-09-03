using System.Text.Json;
using System.Security.Cryptography;
using System.Text;
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
    ControlPlaneClient control,
    IConfiguration configuration,
    ILogger<AdministrationController> logger) : ControllerBase
{
    private const int HermesMinimumContextLength = 65_536;
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
            var specialists = await Read("api/v1/specialists");
            var knowledge = await Read("api/v1/reference/status");
            var workers = await Read("api/v1/workers");
            var operations = await Read("api/v1/operations/status");
            var storage = await Read("api/v1/storage-policy");
            var schedules = await Read("api/v1/schedules", "schedules");
            var backups = await Read("api/v1/backups", "backups");
            return ApiResponse<AdminSnapshot>.Success(new(
                providers, agents, profiles, runs, graphiti, specialists, knowledge, workers,
                operations, storage, schedules, backups));
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<AdminSnapshot>.Failure(exception.Code, exception.Message));
        }
    }

    [HttpPost("providers")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> ConfigureProvider(
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
    public Task<ActionResult<ApiResponse<JsonElement?>>> DiscoverProvider(string providerId) =>
        Proxy($"api/v1/providers/{providerId}/discover", new { }, "provider");

    [HttpPost("providers/{providerId}/select")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> SelectProvider(
        string providerId, ProviderModelSelectionRequest request) =>
        Proxy($"api/v1/providers/{providerId}/select", new
        {
            model_id = request.ModelId,
            context_length_override = request.ContextLengthOverride,
        }, "provider");

    [HttpPost("providers/{providerId}/delete")]
    public async Task<ActionResult<ApiResponse<JsonElement?>>> DeleteProvider(string providerId)
    {
        var referenced = await database.PortalAiProfiles.AsNoTracking()
            .AnyAsync(item => item.ProviderId == providerId, HttpContext.RequestAborted);
        if (referenced)
        {
            return Conflict(ApiResponse<JsonElement?>.Failure(
                "provider_in_use_by_ai_profile",
                "This model endpoint is referenced by an AI player profile and must remain for historical records. Only unused endpoints can be removed."));
        }
        return await Proxy($"api/v1/providers/{Uri.EscapeDataString(providerId)}/delete", new { });
    }

    [HttpGet("ai-profiles")]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<AiProfile>>>> Profiles()
    {
        var entities = await database.PortalAiProfiles.AsNoTracking()
            .OrderByDescending(item => item.Active).ThenBy(item => item.DisplayName)
            .ToArrayAsync(HttpContext.RequestAborted);
        var settings = await database.PortalSettings.AsNoTracking()
            .Where(item => item.Key.StartsWith("ai-profile.acceptance."))
            .ToDictionaryAsync(item => item.Key, item => item.Value, HttpContext.RequestAborted);
        var items = entities.Select(item => ToContract(item,
            ParseAcceptance(settings.GetValueOrDefault(AcceptanceKey(item.ProfileId)), item))).ToArray();
        return ApiResponse<IReadOnlyList<AiProfile>>.Success(items);
    }

    [HttpPost("ai-profiles")]
    public async Task<ActionResult<ApiResponse<AiProfile>>> SaveProfile(
        AiProfileRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.DisplayName) || request.DisplayName.Trim().Length > 160 ||
            string.IsNullOrWhiteSpace(request.ProviderId) || string.IsNullOrWhiteSpace(request.ModelId) ||
            request.ReasoningEffort is not ("none" or "minimal" or "low" or "medium" or "high" or "xhigh" or "max" or "ultra") ||
            request.ContextLength is not null and < HermesMinimumContextLength)
            return BadRequest(ApiResponse<AiProfile>.Failure(
                "invalid_ai_profile", $"Check the profile name, provider, model, and reasoning. A manual context budget must be at least {HermesMinimumContextLength:N0} tokens; leave it blank to use the model's advertised context."));
        var displayName = request.DisplayName.Trim();
        var normalizedDisplayName = displayName.ToUpperInvariant();
        var duplicate = await database.PortalAiProfiles.AsNoTracking().FirstOrDefaultAsync(
            item => item.NormalizedDisplayName == normalizedDisplayName && item.ProfileId != request.ProfileId,
            HttpContext.RequestAborted);
        if (duplicate is not null)
            return Conflict(ApiResponse<AiProfile>.Failure(
                duplicate.Active ? "ai_profile_name_in_use" : "inactive_ai_profile_name_in_use",
                duplicate.Active
                    ? $"An AI profile named '{displayName}' already exists. Edit that profile or choose a different name."
                    : $"An inactive AI profile named '{displayName}' already owns that history. Show inactive profiles and reactivate it instead of creating a duplicate."));
        PortalAiProfile? entity = null;
        if (!string.IsNullOrWhiteSpace(request.ProfileId))
        {
            entity = await database.PortalAiProfiles.FindAsync([request.ProfileId], HttpContext.RequestAborted);
            if (entity is null)
                return NotFound(ApiResponse<AiProfile>.Failure(
                    "profile_not_found", "The AI profile was not found."));
        }
        int? advertisedContext;
        try
        {
            using var providers = await control.GetRawAsync("api/v1/providers", HttpContext.RequestAborted);
            var provider = providers.RootElement.GetProperty("providers").EnumerateArray()
                .FirstOrDefault(item => item.GetProperty("provider_id").GetString() == request.ProviderId);
            if (provider.ValueKind != JsonValueKind.Object)
                return BadRequest(ApiResponse<AiProfile>.Failure(
                    "unknown_provider", "Choose a configured model endpoint."));
            var model = provider.GetProperty("models").EnumerateArray()
                .FirstOrDefault(item => item.GetProperty("model_id").GetString() == request.ModelId);
            if (model.ValueKind != JsonValueKind.Object)
                return BadRequest(ApiResponse<AiProfile>.Failure(
                    "unknown_provider_model", "Choose a model currently advertised by this endpoint."));
            advertisedContext = provider.TryGetProperty("context_length_override", out var providerOverride) &&
                providerOverride.ValueKind == JsonValueKind.Number
                    ? providerOverride.GetInt32()
                    : model.TryGetProperty("context_length", out var advertised) &&
                        advertised.ValueKind == JsonValueKind.Number
                            ? advertised.GetInt32()
                            : null;
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<AiProfile>.Failure(exception.Code, exception.Message));
        }
        if (request.ContextLength is { } manual && advertisedContext is { } maximum && manual > maximum)
            return BadRequest(ApiResponse<AiProfile>.Failure(
                "context_length_exceeds_model_limit",
                $"This endpoint advertises a {maximum:N0}-token context for the selected model. Leave the field blank to use it automatically, or enter a value between {HermesMinimumContextLength:N0} and {maximum:N0}."));
        if (request.ContextLength is null && advertisedContext is { } automatic && automatic < HermesMinimumContextLength)
            return BadRequest(ApiResponse<AiProfile>.Failure(
                "model_context_below_hermes_minimum",
                $"This endpoint advertises only {automatic:N0} tokens. Managed Hermes agents require at least {HermesMinimumContextLength:N0}."));
        ModelGenerationSettings generation;
        try
        {
            generation = NormalizeGeneration(request.Generation);
        }
        catch (ArgumentException exception)
        {
            return BadRequest(ApiResponse<AiProfile>.Failure(
                "invalid_generation_settings", exception.Message));
        }
        var creating = entity is null;
        entity ??= new PortalAiProfile { AgentId = $"agent-{Guid.NewGuid():N}" };
        try
        {
            using var created = await control.PostRawAsync("api/v1/agents", new
            {
                agent_id = entity.AgentId,
                display_name = displayName,
                personality_ref = "none",
            }, HttpContext.RequestAborted);
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<AiProfile>.Failure(exception.Code, exception.Message));
        }
        entity.DisplayName = displayName;
        entity.NormalizedDisplayName = normalizedDisplayName;
        entity.ProviderId = request.ProviderId;
        entity.ModelId = request.ModelId.Trim();
        entity.ReasoningEffort = request.ReasoningEffort;
        entity.ContextLength = request.ContextLength;
        entity.GenerationSettingsJson = JsonSerializer.Serialize(generation);
        entity.Notes = string.IsNullOrWhiteSpace(request.Notes) ? null : request.Notes.Trim();
        entity.PersonalityCardId = "none";
        entity.UpdatedAt = DateTimeOffset.UtcNow;
        if (creating) database.PortalAiProfiles.Add(entity);
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        try
        {
            using var synced = await control.PostRawAsync(
                "api/v1/graphiti/sync-profile", new { profile = GraphitiProfile(entity) },
                HttpContext.RequestAborted);
            using var specialistSynced = await control.PostRawAsync(
                "api/v1/specialists/sync-profile", new { profile = GraphitiProfile(entity) },
                HttpContext.RequestAborted);
        }
        catch (ControlPlaneException exception)
        {
            logger.LogWarning(exception,
                "AI profile {ProfileId} was saved, but its selected Graphiti snapshot could not be refreshed immediately",
                entity.ProfileId);
        }
        return ApiResponse<AiProfile>.Success(ToContract(entity));
    }

    [HttpPost("ai-profiles/{profileId}/test-provider-acceptance")]
    public async Task<ActionResult<ApiResponse<GenerationAcceptanceStatus>>> TestProviderAcceptance(
        string profileId)
    {
        var item = await database.PortalAiProfiles.AsNoTracking()
            .SingleOrDefaultAsync(profile => profile.ProfileId == profileId, HttpContext.RequestAborted);
        if (item is null)
            return NotFound(ApiResponse<GenerationAcceptanceStatus>.Failure(
                "profile_not_found", "The AI profile was not found."));
        JsonElement result;
        try
        {
            using var response = await control.PostRawAsync(
                $"api/v1/providers/{Uri.EscapeDataString(item.ProviderId)}/probe-generation",
                new
                {
                    model_id = item.ModelId,
                    reasoning_effort = item.ReasoningEffort,
                    generation = ParseGeneration(item.GenerationSettingsJson),
                }, HttpContext.RequestAborted);
            result = response.RootElement.Clone();
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<GenerationAcceptanceStatus>.Failure(exception.Code, exception.Message));
        }
        var testedAt = DateTimeOffset.UtcNow;
        var status = new GenerationAcceptanceStatus(
            result.GetProperty("state").GetString() ?? "rejected",
            result.GetProperty("accepted").GetBoolean(), false,
            result.GetProperty("message").GetString() ?? "The acceptance probe completed.",
            testedAt,
            result.TryGetProperty("profile_fields", out var fields) && fields.ValueKind == JsonValueKind.Array
                ? fields.EnumerateArray().Select(value => value.GetString() ?? "").Where(value => value.Length > 0).ToArray()
                : [],
            result.TryGetProperty("http_status", out var http) && http.ValueKind == JsonValueKind.Number
                ? http.GetInt32() : null);
        var stored = JsonSerializer.Serialize(new
        {
            fingerprint = ProfileFingerprint(item),
            status,
        });
        var key = AcceptanceKey(profileId);
        var setting = await database.PortalSettings.FindAsync([key], HttpContext.RequestAborted);
        if (setting is null)
        {
            setting = new PortalSetting { Key = key };
            database.PortalSettings.Add(setting);
        }
        setting.Value = stored;
        setting.UpdatedAt = testedAt;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        return ApiResponse<GenerationAcceptanceStatus>.Success(status);
    }

    [HttpPost("ai-profiles/{profileId}/deactivate")]
    public async Task<ActionResult<ApiResponse<AiProfile>>> DeactivateProfile(string profileId)
    {
        var item = await database.PortalAiProfiles.FindAsync([profileId], HttpContext.RequestAborted);
        if (item is null) return NotFound();
        item.Active = false;
        item.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        try
        {
            using var cleared = await control.PostRawAsync(
                "api/v1/graphiti/clear-profile",
                new { profile_id = item.ProfileId }, HttpContext.RequestAborted);
            using var specialistCleared = await control.PostRawAsync(
                "api/v1/specialists/clear-profile",
                new { profile_id = item.ProfileId }, HttpContext.RequestAborted);
        }
        catch (ControlPlaneException exception)
        {
            logger.LogWarning(exception,
                "AI profile {ProfileId} was deactivated, but its Graphiti selection could not be reconciled immediately",
                item.ProfileId);
        }
        return ApiResponse<AiProfile>.Success(ToContract(item));
    }

    [HttpPost("ai-profiles/{profileId}/reactivate")]
    public async Task<ActionResult<ApiResponse<AiProfile>>> ReactivateProfile(string profileId)
    {
        var item = await database.PortalAiProfiles.FindAsync([profileId], HttpContext.RequestAborted);
        if (item is null) return NotFound();
        item.Active = true;
        item.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        return ApiResponse<AiProfile>.Success(ToContract(item));
    }

    [HttpPost("game-sources")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> ValidateSource(GameSourceRequest request) =>
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
    public Task<ActionResult<ApiResponse<JsonElement?>>> ImportRuntime(RuntimeImportRequest request) =>
        Proxy("api/v1/runtimes/import-proton", new
        {
            display_name = request.DisplayName, source_host_path = request.SourceHostPath,
        }, "runtime");

    [HttpPost("graphiti")]
    public async Task<ActionResult<ApiResponse<JsonElement?>>> Graphiti(GraphitiConfigurationRequest request)
    {
        object? profile = null;
        if (request.Enabled && string.IsNullOrWhiteSpace(request.ProfileId))
            return BadRequest(ApiResponse<JsonElement?>.Failure(
                "graphiti_profile_required", "Choose an active extraction profile to enable Graphiti."));
        if (request.Enabled)
        {
            var item = await database.PortalAiProfiles.AsNoTracking().FirstOrDefaultAsync(
                candidate => candidate.ProfileId == request.ProfileId,
                HttpContext.RequestAborted);
            if (item is null || !item.Active)
                return BadRequest(ApiResponse<JsonElement?>.Failure(
                    "invalid_graphiti_profile", "Choose an active AI profile for Graphiti extraction."));
            profile = GraphitiProfile(item);
        }
        return await Proxy("api/v1/graphiti", new { enabled = request.Enabled, profile });
    }

    [HttpPost("graphiti/probe")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> ProbeGraphiti() =>
        Proxy("api/v1/graphiti/probe", new { });

    [HttpPost("specialists")]
    public async Task<ActionResult<ApiResponse<JsonElement?>>> Specialists(
        SpecialistConfigurationRequest request)
    {
        object? profile = null;
        if (!string.IsNullOrWhiteSpace(request.ProfileId))
        {
            var item = await database.PortalAiProfiles.AsNoTracking().FirstOrDefaultAsync(
                candidate => candidate.ProfileId == request.ProfileId,
                HttpContext.RequestAborted);
            if (item is null || !item.Active)
                return BadRequest(ApiResponse<JsonElement?>.Failure(
                    "invalid_specialist_profile", "Choose an active AI profile or use the sovereign fallback."));
            profile = GraphitiProfile(item);
        }
        if (request.InstallationConcurrency is < 1 or > 16 ||
            request.SeatConcurrency is < 1 or > 8)
            return BadRequest(ApiResponse<JsonElement?>.Failure(
                "invalid_specialist_concurrency", "Choose an installation limit from 1 through 16."));
        static object Workload(SpecialistWorkloadPolicyRequest? value,
            SpecialistWorkloadPolicyRequest fallback) => new
        {
            tool_budget = (value ?? fallback).ToolBudget,
            provider_call_budget = (value ?? fallback).ProviderCallBudget,
            provider_token_budget = (value ?? fallback).ProviderTokenBudget,
            context_token_ceiling = (value ?? fallback).ContextTokenCeiling,
            output_token_budget = (value ?? fallback).OutputTokenBudget,
            wall_seconds = (value ?? fallback).WallSeconds,
        };
        return await Proxy("api/v1/specialists", new
        {
            profile, max_concurrency = request.InstallationConcurrency,
            policy = new
            {
                installation_concurrency = request.InstallationConcurrency,
                seat_concurrency = request.SeatConcurrency,
                automatic_retries = request.AutomaticRetries,
                schema_repairs = request.SchemaRepairs,
                trace_capture = request.TraceCapture,
                trace_success_generations = request.TraceSuccessGenerations,
                trace_failed_generations = request.TraceFailedGenerations,
                trace_byte_ceiling = request.TraceByteCeiling,
                trace_high_retention = request.TraceHighRetention,
                synthesis = Workload(request.Synthesis,
                    new(4, 4, 96_000, 65_536, 1_500, 90)),
                investigation = Workload(request.Investigation,
                    new(24, 16, 512_000, 262_144, 4_000, 300)),
            },
        });
    }

    [HttpGet("specialists/missions")]
    public async Task<ActionResult<ApiResponse<JsonElement>>> SpecialistMissions(
        string status = "", int limit = 100)
    {
        using var document = await control.GetRawAsync(
            $"api/v1/specialists/missions?status={Uri.EscapeDataString(status)}&limit={Math.Clamp(limit, 1, 500)}",
            HttpContext.RequestAborted);
        return ApiResponse<JsonElement>.Success(
            document.RootElement.GetProperty("missions").Clone());
    }

    [HttpGet("specialists/missions/{missionId}")]
    public async Task<ActionResult<ApiResponse<JsonElement>>> SpecialistMission(string missionId)
    {
        using var document = await control.GetRawAsync(
            $"api/v1/specialists/missions/{Uri.EscapeDataString(missionId)}",
            HttpContext.RequestAborted);
        return ApiResponse<JsonElement>.Success(
            document.RootElement.GetProperty("mission").Clone());
    }

    [HttpPost("specialists/traces/gc")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> SpecialistTraceGc() =>
        Proxy("api/v1/specialists/traces/gc", new { });

    [HttpGet("specialists/traces/{attemptId}/download")]
    public async Task<IActionResult> DownloadSpecialistTrace(string attemptId)
    {
        using var document = await control.GetRawAsync(
            $"api/v1/specialists/traces/{Uri.EscapeDataString(attemptId)}",
            HttpContext.RequestAborted);
        var trace = document.RootElement.GetProperty("trace");
        var relative = trace.GetProperty("relative_path").GetString() ?? "";
        if (Path.IsPathRooted(relative) || relative.Split(
                Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar).Contains(".."))
            return BadRequest();
        var mountedControlRoot = Path.GetFullPath(configuration[
            "SMACX_CONTROL_DATA_MOUNT"] ?? "/var/lib/smacx-control");
        var traceRoot = Path.Combine(mountedControlRoot, "specialist-traces");
        var path = Path.GetFullPath(Path.Combine(traceRoot, relative));
        if (!path.StartsWith(traceRoot + Path.DirectorySeparatorChar,
                StringComparison.Ordinal) || !System.IO.File.Exists(path))
            return NotFound();
        var expectedBytes = trace.GetProperty("bytes").GetInt64();
        if (new FileInfo(path).Length != expectedBytes)
            return Conflict();
        await using (var stream = System.IO.File.OpenRead(path))
        {
            var digest = Convert.ToHexString(await System.Security.Cryptography.SHA256.HashDataAsync(
                stream, HttpContext.RequestAborted)).ToLowerInvariant();
            if (!string.Equals(digest, trace.GetProperty("content_sha256").GetString(),
                    StringComparison.OrdinalIgnoreCase))
                return Conflict();
        }
        return PhysicalFile(path, "application/zstd", $"{attemptId}.jsonl.zst");
    }

    [HttpPost("embeddings")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> Embeddings(EmbeddingConfigurationRequest request) =>
        Proxy("api/v1/embeddings", new
        {
            mode = request.Mode, provider_id = request.ProviderId,
            model_id = request.ModelId, dimensions = request.Dimensions,
            space_id = request.SpaceId,
        }, "configuration");

    private static object GraphitiProfile(PortalAiProfile item) => new
    {
        profile_id = item.ProfileId,
        display_name = item.DisplayName,
        provider_id = item.ProviderId,
        model_id = item.ModelId,
        reasoning_effort = item.ReasoningEffort,
        generation_settings = JsonSerializer.Deserialize<JsonElement>(item.GenerationSettingsJson),
    };

    [HttpPost("backups")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> Backup(BackupRequest request) =>
        Proxy("api/v1/backups", new
        {
            include_secrets = request.IncludeSecrets, include_workers = request.IncludeWorkers,
        }, "backup");

    [HttpPost("storage-policy")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> StoragePolicy(StoragePolicyRequest request) =>
        Proxy("api/v1/storage-policy", new
        {
            recent_checkpoints = request.RecentCheckpoints,
            milestone_interval = request.MilestoneInterval,
            retain_full_turn_history = request.RetainFullTurnHistory,
        });

    [HttpPost("backups/{backupId}/verify")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> VerifyBackup(string backupId) =>
        Proxy($"api/v1/backups/{backupId}/verify", new { });

    [HttpPost("schedules")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> Schedule(ScheduleRequest request) =>
        Proxy("api/v1/schedules", new
        {
            display_name = request.DisplayName, operation_kind = request.OperationKind,
            target_kind = request.TargetKind, target_id = request.TargetId,
            interval_seconds = request.IntervalSeconds,
        }, "schedule");

    [HttpPost("schedules/{scheduleId}/{action}")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> ScheduleAction(string scheduleId, string action)
    {
        if (action is not ("activate" or "pause" or "disable"))
            return Task.FromResult<ActionResult<ApiResponse<JsonElement?>>>(BadRequest(
                ApiResponse<JsonElement?>.Failure("invalid_schedule_action", "Invalid schedule action.")));
        return Proxy($"api/v1/schedules/{scheduleId}/{action}", new { }, "schedule");
    }

    private async Task<ActionResult<ApiResponse<JsonElement?>>> Proxy(
        string path, object body, string? property = null)
    {
        try
        {
            using var document = await control.PostRawAsync(path, body, HttpContext.RequestAborted);
            var value = property is null ? document.RootElement : document.RootElement.GetProperty(property);
            return ApiResponse<JsonElement?>.Success(value.Clone());
        }
        catch (ControlPlaneException exception)
        {
            return StatusCode(exception.StatusCode ?? 502,
                ApiResponse<JsonElement?>.Failure(exception.Code, exception.Message));
        }
    }

    private static AiProfile ToContract(
        PortalAiProfile item, GenerationAcceptanceStatus? acceptance = null) => new(
        item.ProfileId, item.DisplayName,
        item.AgentId, item.ProviderId, item.ModelId, item.ReasoningEffort,
        item.ContextLength, item.Notes, item.Active, item.PersonalityCardId, item.CreatedAt, item.UpdatedAt,
        ParseGeneration(item.GenerationSettingsJson), acceptance);

    private static string AcceptanceKey(string profileId) => $"ai-profile.acceptance.{profileId}";

    private static string ProfileFingerprint(PortalAiProfile item)
    {
        var canonical = JsonSerializer.Serialize(new
        {
            item.ProviderId,
            item.ModelId,
            item.ReasoningEffort,
            Generation = ParseGeneration(item.GenerationSettingsJson),
        });
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(canonical))).ToLowerInvariant();
    }

    private static GenerationAcceptanceStatus? ParseAcceptance(string? json, PortalAiProfile item)
    {
        if (string.IsNullOrWhiteSpace(json)) return null;
        try
        {
            using var document = JsonDocument.Parse(json);
            var root = document.RootElement;
            var status = root.GetProperty("status").Deserialize<GenerationAcceptanceStatus>();
            if (status is null) return null;
            return root.GetProperty("fingerprint").GetString() == ProfileFingerprint(item)
                ? status
                : status with
                {
                    State = "stale",
                    Accepted = null,
                    Message = "This profile changed after its last provider acceptance test. Test it again.",
                };
        }
        catch (JsonException)
        {
            return null;
        }
    }

    internal static ModelGenerationSettings NormalizeGeneration(ModelGenerationSettings? requested)
    {
        var generation = requested ?? new ModelGenerationSettings();
        var preset = generation.Preset switch
        {
            "provider-default" or "custom" or "qwen38-instant" or "qwen38-low" or
            "qwen38-medium" or "qwen38-high" or "qwen38-xhigh" => generation.Preset,
            "qwen38-thinking" => "qwen38-low",
            "qwen38-instruct" => "qwen38-instant",
            _ => throw new ArgumentException("Choose a supported starting template."),
        };
        Range(generation.Temperature, 0, 2, "Temperature");
        Range(generation.TopP, 0, 1, "Top P");
        Range(generation.MinP, 0, 1, "Min P");
        Range(generation.PresencePenalty, -2, 2, "Presence penalty");
        Range(generation.FrequencyPenalty, -2, 2, "Frequency penalty");
        Range(generation.RepetitionPenalty, 0.01, 2, "Repetition penalty");
        if (generation.TopK is < 0 or > 100_000)
            throw new ArgumentException("Top K must be between 0 and 100,000.");
        if (generation.MaxOutputTokens is < 1 or > 262_144)
            throw new ArgumentException("Maximum output tokens must be between 1 and 262,144.");
        if (generation.ReasoningContinuity is not ("automatic" or "off" or "current_episode"))
            throw new ArgumentException("Reasoning continuity must be automatic, current episode, or off.");
        ValidateExtraParameters(generation.ExtraParameters);
        return generation with { Preset = preset };
    }

    private static void ValidateExtraParameters(
        IReadOnlyDictionary<string, JsonElement>? parameters)
    {
        if (parameters is null) return;
        if (parameters.Count > 64) throw new ArgumentException("At most 64 custom request parameters are allowed.");
        var reserved = new HashSet<string>(StringComparer.Ordinal)
        {
            "model", "messages", "stream", "tools", "tool_choice", "reasoning_effort",
            "temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty",
            "repetition_penalty", "max_tokens", "seed",
        };
        foreach (var (key, value) in parameters)
        {
            if (!System.Text.RegularExpressions.Regex.IsMatch(key, "^[A-Za-z][A-Za-z0-9_.-]{0,127}$") || reserved.Contains(key))
                throw new ArgumentException($"Custom request parameter '{key}' is invalid or reserved.");
            if (value.ValueKind is JsonValueKind.Undefined) throw new ArgumentException($"Custom request parameter '{key}' has no JSON value.");
        }
        if (JsonSerializer.Serialize(parameters).Length > 32_768)
            throw new ArgumentException("Custom request parameters are too large.");
    }

    private static void Range(double? value, double minimum, double maximum, string label)
    {
        if (value is not null && (!double.IsFinite(value.Value) || value < minimum || value > maximum))
            throw new ArgumentException($"{label} must be between {minimum} and {maximum}.");
    }

    private static ModelGenerationSettings ParseGeneration(string json)
    {
        try
        {
            return JsonSerializer.Deserialize<ModelGenerationSettings>(json) ?? new();
        }
        catch (JsonException)
        {
            return new();
        }
    }
}
