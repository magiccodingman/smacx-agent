using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Options;
using Smacx.Portal.Contracts;

namespace Smacx.Portal.Services;

public sealed class ControlPlaneClient(
    HttpClient client,
    IOptions<ControlPlaneOptions> options,
    ILogger<ControlPlaneClient> logger)
{
    private readonly string serviceTokenFile = Path.GetFullPath(options.Value.ServiceTokenFile);

    public async Task<(bool Connected, string State)> HealthAsync(
        CancellationToken cancellationToken = default)
    {
        try
        {
            using var response = await client.GetAsync("healthz", cancellationToken);
            if (!response.IsSuccessStatusCode)
            {
                return (false, $"HTTP {(int)response.StatusCode}");
            }
            using var document = await JsonDocument.ParseAsync(
                await response.Content.ReadAsStreamAsync(cancellationToken),
                cancellationToken: cancellationToken);
            return document.RootElement.TryGetProperty("ok", out var ok) && ok.GetBoolean()
                ? (true, "ready")
                : (false, "invalid health response");
        }
        catch (HttpRequestException exception)
        {
            logger.LogDebug(exception, "Control service health check failed");
            return (false, "unavailable");
        }
    }

    public async Task<IReadOnlyList<ControlMatch>> ListMatchesAsync(
        CancellationToken cancellationToken = default)
    {
        using var document = await SendAsync(HttpMethod.Get, "api/v1/matches", null, cancellationToken);
        return document.RootElement.GetProperty("matches").EnumerateArray()
            .Select(ParseMatch).ToArray();
    }

    public async Task<ControlMatchDetails> GetMatchAsync(
        string matchId,
        CancellationToken cancellationToken = default)
    {
        using var document = await SendAsync(
            HttpMethod.Get, $"api/v1/matches/{Uri.EscapeDataString(matchId)}", null, cancellationToken);
        var match = ParseMatch(document.RootElement.GetProperty("match"));
        var seats = document.RootElement.GetProperty("seats").EnumerateArray()
            .Select(ParseSeat).ToArray();
        return new(match, seats);
    }

    public async Task<ControlMatchDetails> CreateLanMatchAsync(
        object request,
        CancellationToken cancellationToken = default)
    {
        using var document = await SendAsync(HttpMethod.Post, "api/v1/matches/lan", request, cancellationToken);
        var match = ParseMatch(document.RootElement.GetProperty("match"));
        var seats = document.RootElement.GetProperty("seats").EnumerateArray()
            .Select(ParseSeat).ToArray();
        return new(match, seats);
    }

    public async Task<ControlMatchOperation> StartLanMatchAsync(
        string matchId, string sessionName, string profile, object? gameSettings,
        string? scenarioId = null, string? resumeSlot = null,
        CancellationToken cancellationToken = default)
    {
        using var document = await SendAsync(
            HttpMethod.Post,
            $"api/v1/matches/{Uri.EscapeDataString(matchId)}/start",
            new { session_name = sessionName, profile, game_settings = gameSettings,
                scenario_id = scenarioId, resume_slot = resumeSlot },
            cancellationToken);
        var root = document.RootElement;
        var match = root.TryGetProperty("match", out var matchElement)
            ? ParseMatch(matchElement)
            : (await GetMatchAsync(matchId, cancellationToken)).Match;
        return new ControlMatchOperation(
            match,
            root.TryGetProperty("awaiting_external_humans", out var waiting) && waiting.GetBoolean(),
            root.TryGetProperty("network_session_id", out var session) ? session.GetString() : null,
            root.TryGetProperty("external_join", out var externalJoin) &&
                externalJoin.ValueKind == JsonValueKind.Object ? externalJoin.Clone() : null);
    }

    public async Task<IReadOnlyList<ControlAgent>> ListAgentsAsync(
        CancellationToken cancellationToken = default)
    {
        using var document = await SendAsync(HttpMethod.Get, "api/v1/agents", null, cancellationToken);
        return document.RootElement.GetProperty("agents").EnumerateArray().Select(item => new ControlAgent(
            item.GetProperty("agent_id").GetString()!,
            item.GetProperty("display_name").GetString()!,
            item.GetProperty("status").GetString()!)).ToArray();
    }

    public async Task<ControlIncident?> GetActiveCapabilityIncidentAsync(
        string matchId, CancellationToken cancellationToken = default)
    {
        using var document = await SendAsync(
            HttpMethod.Get,
            $"api/v1/incidents?match_id={Uri.EscapeDataString(matchId)}&active_only=true",
            null, cancellationToken);
        foreach (var item in document.RootElement.GetProperty("incidents").EnumerateArray())
        {
            var kind = item.GetProperty("incident_kind").GetString() ?? "";
            if (kind.StartsWith("capability_gap:", StringComparison.Ordinal))
            {
                return ParseIncident(item);
            }
        }
        return null;
    }

    public async Task<ControlIncident> GetIncidentAsync(
        string incidentId, CancellationToken cancellationToken = default)
    {
        using var document = await SendAsync(
            HttpMethod.Get, $"api/v1/incidents/{Uri.EscapeDataString(incidentId)}",
            null, cancellationToken);
        return ParseIncident(document.RootElement.GetProperty("incident"));
    }

    public async Task<ControlStreamAccess> GetStreamAccessAsync(
        string instanceId, bool interactive, CancellationToken cancellationToken = default)
    {
        using var document = await SendAsync(
            HttpMethod.Post,
            $"api/v1/workers/{Uri.EscapeDataString(instanceId)}/spectator",
            new { interactive }, cancellationToken);
        var root = document.RootElement;
        return new ControlStreamAccess(
            root.GetProperty("instance_id").GetString()!,
            root.GetProperty("access_mode").GetString()!,
            root.GetProperty("internal_base_url").GetString()!,
            root.GetProperty("path").GetString()!,
            root.GetProperty("password").GetString()!);
    }

    public async Task<IReadOnlyList<ControlAsset>> ListGameSourcesAsync(
        CancellationToken cancellationToken = default) =>
        await ListAssetsAsync("api/v1/game-sources", "game_sources", "game_source_id", cancellationToken);

    public async Task<IReadOnlyList<ControlAsset>> ListRuntimesAsync(
        CancellationToken cancellationToken = default) =>
        await ListAssetsAsync("api/v1/runtimes", "runtimes", "runtime_id", cancellationToken);

    public async Task<IReadOnlyList<ScenarioCatalogItem>> ListScenariosAsync(
        string gameSourceId, CancellationToken cancellationToken = default)
    {
        using var document = await SendAsync(HttpMethod.Get,
            $"api/v1/game-sources/{Uri.EscapeDataString(gameSourceId)}/scenarios", null, cancellationToken);
        return document.RootElement.GetProperty("scenarios").EnumerateArray().Select(item =>
            new ScenarioCatalogItem(
                item.GetProperty("scenario_id").GetString()!,
                item.GetProperty("display_name").GetString()!,
                item.GetProperty("relative_path").GetString()!)).ToArray();
    }

    private async Task<IReadOnlyList<ControlAsset>> ListAssetsAsync(
        string path, string collectionName, string idName, CancellationToken cancellationToken)
    {
        using var document = await SendAsync(HttpMethod.Get, path, null, cancellationToken);
        return document.RootElement.GetProperty(collectionName).EnumerateArray().Select(item => new ControlAsset(
            item.GetProperty(idName).GetString()!,
            item.GetProperty("display_name").GetString()!,
            item.GetProperty("status").GetString()!)).ToArray();
    }

    private async Task<JsonDocument> SendAsync(
        HttpMethod method,
        string path,
        object? body,
        CancellationToken cancellationToken)
    {
        using var request = new HttpRequestMessage(method, path);
        if (!File.Exists(serviceTokenFile))
        {
            throw new ControlPlaneException("service_token_unavailable", "The portal control credential is unavailable.");
        }
        var token = (await File.ReadAllTextAsync(serviceTokenFile, cancellationToken)).Trim();
        request.Headers.TryAddWithoutValidation("X-SMACX-Service-Token", token);
        if (body is not null)
        {
            // The deliberately small Python control server enforces a bounded
            // Content-Length before reading JSON. JsonContent may choose
            // chunked transfer, so serialize to a fixed StringContent buffer.
            request.Content = new StringContent(
                JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        }
        HttpResponseMessage response;
        try
        {
            response = await client.SendAsync(request, cancellationToken);
        }
        catch (HttpRequestException exception)
        {
            logger.LogWarning(exception, "Control service request failed for {Path}", path);
            throw new ControlPlaneException(
                "control_unavailable",
                "The control service is unavailable. Check its status and try again.",
                StatusCodes.Status503ServiceUnavailable);
        }
        using (response)
        {
            JsonDocument document;
            try
            {
                document = await JsonDocument.ParseAsync(
                    await response.Content.ReadAsStreamAsync(cancellationToken),
                    cancellationToken: cancellationToken);
            }
            catch (JsonException exception)
            {
                logger.LogWarning(exception,
                    "Control service returned a non-JSON response with HTTP status {StatusCode}",
                    (int)response.StatusCode);
                throw new ControlPlaneException(
                    "invalid_control_response",
                    $"The control service returned an invalid response (HTTP {(int)response.StatusCode}).",
                    StatusCodes.Status502BadGateway);
            }
            if (!response.IsSuccessStatusCode)
            {
                var error = document.RootElement.TryGetProperty("error", out var payload) ? payload : default;
                var code = error.ValueKind == JsonValueKind.Object && error.TryGetProperty("code", out var codeValue)
                    ? codeValue.GetString() ?? "control_error"
                    : "control_error";
                var message = error.ValueKind == JsonValueKind.Object && error.TryGetProperty("message", out var messageValue)
                    ? messageValue.GetString() ?? code
                    : code;
                document.Dispose();
                throw new ControlPlaneException(code, message, (int)response.StatusCode);
            }
            return document;
        }
    }

    public Task<JsonDocument> GetRawAsync(string path, CancellationToken cancellationToken = default) =>
        SendAsync(HttpMethod.Get, path, null, cancellationToken);

    public Task<JsonDocument> PostRawAsync(
        string path, object body, CancellationToken cancellationToken = default) =>
        SendAsync(HttpMethod.Post, path, body, cancellationToken);

    private static ControlMatch ParseMatch(JsonElement item) => new(
        item.GetProperty("match_id").GetString()!,
        item.GetProperty("display_name").GetString()!,
        item.GetProperty("mode").GetString()!,
        item.GetProperty("status").GetString()!,
        item.TryGetProperty("ruleset_id", out var ruleset) && ruleset.ValueKind == JsonValueKind.String
            ? ruleset.GetString() : null,
        item.TryGetProperty("last_turn", out var turn) && turn.ValueKind == JsonValueKind.Number
            ? turn.GetInt32() : null,
        item.TryGetProperty("last_year", out var year) && year.ValueKind == JsonValueKind.Number
            ? year.GetInt32() : null,
        item.GetProperty("created_unix").GetDouble(),
        item.GetProperty("updated_unix").GetDouble());

    private static ControlIncident ParseIncident(JsonElement item)
    {
        var details = item.GetProperty("details").Clone();
        return new ControlIncident(
            item.GetProperty("incident_id").GetString()!,
            item.GetProperty("match_id").GetString()!,
            item.GetProperty("instance_id").GetString()!,
            item.GetProperty("incident_kind").GetString()!,
            item.GetProperty("status").GetString()!,
            details,
            item.GetProperty("first_seen_unix").GetDouble(),
            item.GetProperty("last_seen_unix").GetDouble());
    }

    private static ControlSeat ParseSeat(JsonElement item)
    {
        string? MetadataString(string name) =>
            item.TryGetProperty("metadata", out var metadata) && metadata.ValueKind == JsonValueKind.Object &&
            metadata.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
                ? value.GetString() : null;
        return new(
            item.GetProperty("seat_index").GetInt32(),
            item.GetProperty("controller_kind").GetString()!,
            item.TryGetProperty("agent_id", out var agent) && agent.ValueKind == JsonValueKind.String ? agent.GetString() : null,
            MetadataString("external_player_name") ?? MetadataString("player_name"),
            item.TryGetProperty("faction_id", out var factionId) && factionId.ValueKind == JsonValueKind.Number
                ? factionId.GetInt32() : null,
            item.TryGetProperty("faction_name", out var faction) && faction.ValueKind == JsonValueKind.String ? faction.GetString() : null,
            item.GetProperty("status").GetString()!,
            item.TryGetProperty("instance_id", out var instance) && instance.ValueKind == JsonValueKind.String
                ? instance.GetString() : null);
    }
}

public sealed record ControlMatch(
    string MatchId, string DisplayName, string Mode, string Status, string? RulesetId,
    int? LastTurn, int? LastYear, double CreatedUnix, double UpdatedUnix);

public sealed record ControlSeat(
    int SeatIndex, string ControllerKind, string? AgentId, string? PlayerHandle,
    int? FactionId, string? FactionName, string Status, string? InstanceId)
{
    public bool Managed => InstanceId is not null;
}

public sealed record ControlMatchDetails(ControlMatch Match, IReadOnlyList<ControlSeat> Seats);
public sealed record ControlAgent(string AgentId, string DisplayName, string Status);
public sealed record ControlAsset(string Id, string DisplayName, string Status);
public sealed record ControlStreamAccess(
    string InstanceId, string AccessMode, string InternalBaseUrl, string Path, string Password);
public sealed record ControlMatchOperation(
    ControlMatch Match, bool AwaitingExternalHumans, string? NetworkSessionId,
    JsonElement? ExternalJoin);
public sealed record ControlIncident(
    string IncidentId, string MatchId, string InstanceId, string IncidentKind,
    string Status, JsonElement Details, double FirstSeenUnix, double LastSeenUnix);

public sealed class ControlPlaneException(
    string code,
    string message,
    int? statusCode = null) : Exception(message)
{
    public string Code { get; } = code;
    public int? StatusCode { get; } = statusCode;
}
