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
            var knowledge = await Read("api/v1/reference/status");
            var workers = await Read("api/v1/workers");
            var operations = await Read("api/v1/operations/status");
            var storage = await Read("api/v1/storage-policy");
            var schedules = await Read("api/v1/schedules", "schedules");
            var backups = await Read("api/v1/backups", "backups");
            return ApiResponse<AdminSnapshot>.Success(new(
                providers, agents, profiles, runs, graphiti, knowledge, workers,
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
        var items = await database.PortalAiProfiles.AsNoTracking()
            .OrderByDescending(item => item.Active).ThenBy(item => item.DisplayName)
            .Select(item => ToContract(item)).ToArrayAsync(HttpContext.RequestAborted);
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
            generation = NormalizeGeneration(request.Generation, request.ModelId, request.ReasoningEffort);
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
        return ApiResponse<AiProfile>.Success(ToContract(entity));
    }

    [HttpPost("ai-profiles/{profileId}/deactivate")]
    public async Task<ActionResult<ApiResponse<AiProfile>>> DeactivateProfile(string profileId)
    {
        var item = await database.PortalAiProfiles.FindAsync([profileId], HttpContext.RequestAborted);
        if (item is null) return NotFound();
        item.Active = false;
        item.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
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
        if (!string.IsNullOrWhiteSpace(request.ProfileId))
        {
            var item = await database.PortalAiProfiles.AsNoTracking().FirstOrDefaultAsync(
                candidate => candidate.ProfileId == request.ProfileId,
                HttpContext.RequestAborted);
            if (item is null || !item.Active)
                return BadRequest(ApiResponse<JsonElement?>.Failure(
                    "invalid_graphiti_profile", "Choose an active AI profile for Graphiti extraction."));
            profile = new
            {
                profile_id = item.ProfileId,
                display_name = item.DisplayName,
                provider_id = item.ProviderId,
                model_id = item.ModelId,
                reasoning_effort = item.ReasoningEffort,
                generation_settings = JsonSerializer.Deserialize<JsonElement>(item.GenerationSettingsJson),
            };
        }
        return await Proxy("api/v1/graphiti", new { enabled = request.Enabled, profile });
    }

    [HttpPost("embeddings")]
    public Task<ActionResult<ApiResponse<JsonElement?>>> Embeddings(EmbeddingConfigurationRequest request) =>
        Proxy("api/v1/embeddings", new
        {
            mode = request.Mode, provider_id = request.ProviderId,
            model_id = request.ModelId, dimensions = request.Dimensions,
            space_id = request.SpaceId,
        }, "configuration");

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

    private static AiProfile ToContract(PortalAiProfile item) => new(
        item.ProfileId, item.DisplayName,
        item.AgentId, item.ProviderId, item.ModelId, item.ReasoningEffort,
        item.ContextLength, item.Notes, item.Active, item.PersonalityCardId, item.CreatedAt, item.UpdatedAt,
        ParseGeneration(item.GenerationSettingsJson));

    private static ModelGenerationSettings NormalizeGeneration(
        ModelGenerationSettings? requested, string modelId, string reasoningEffort)
    {
        var generation = requested ?? new ModelGenerationSettings();
        generation = generation.Preset switch
        {
            "provider-default" => new ModelGenerationSettings(),
            "qwen38-instant" => Qwen("qwen38-instant", false, generation),
            "qwen38-low" => Qwen("qwen38-low", true, generation),
            "qwen38-medium" => Qwen("qwen38-medium", true, generation),
            "qwen38-xhigh" => Qwen("qwen38-xhigh", true, generation),
            // Pre-release compatibility aliases. New UI never writes them.
            "qwen38-thinking" => Qwen("qwen38-low", true, generation),
            "qwen38-instruct" => Qwen("qwen38-instant", false, generation),
            "custom" => generation,
            _ => throw new ArgumentException(
                "Choose Provider defaults, a Qwen3.8 template, or Custom."),
        };
        if (generation.Preset.StartsWith("qwen38-", StringComparison.Ordinal) &&
            !modelId.Contains("qwen3.8", StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("The Qwen3.8 presets can only be used with a Qwen3.8 model.");
        var expectedReasoning = generation.Preset switch
        {
            "qwen38-instant" => "none", "qwen38-low" => "low",
            "qwen38-medium" => "medium", "qwen38-xhigh" => "xhigh", _ => null,
        };
        if (expectedReasoning is not null && reasoningEffort != expectedReasoning)
            throw new ArgumentException($"{generation.Preset} requires reasoning effort {expectedReasoning}.");
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
        ValidateExtraParameters(generation.ExtraParameters);
        return generation;
    }

    private static ModelGenerationSettings Qwen(
        string preset, bool thinking, ModelGenerationSettings requested)
    {
        var extras = new Dictionary<string, JsonElement>(StringComparer.Ordinal)
        {
            ["top_k"] = JsonSerializer.SerializeToElement(20),
            ["min_p"] = JsonSerializer.SerializeToElement(0.0),
            ["repetition_penalty"] = JsonSerializer.SerializeToElement(1.0),
            ["chat_template_kwargs"] = JsonSerializer.SerializeToElement(new Dictionary<string, bool>
            {
                ["enable_thinking"] = thinking,
                ["preserve_thinking"] = false,
            }),
        };
        return new ModelGenerationSettings(
            preset, thinking ? 1.0 : 0.7, thinking ? 0.95 : 0.80,
            null, null, thinking ? 0.0 : 1.5, null, null,
            requested.MaxOutputTokens, requested.Seed, thinking, false, extras);
    }

    private static void ValidateExtraParameters(
        IReadOnlyDictionary<string, JsonElement>? parameters)
    {
        if (parameters is null) return;
        if (parameters.Count > 64) throw new ArgumentException("At most 64 custom request parameters are allowed.");
        var reserved = new HashSet<string>(StringComparer.Ordinal)
        {
            "model", "messages", "stream", "tools", "tool_choice", "reasoning_effort",
            "temperature", "top_p", "presence_penalty", "frequency_penalty", "max_tokens", "seed",
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
