using System.Text.Json;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Hubs;

namespace Smacx.Portal.Services;

/// <summary>
/// Resumes durable portal lifecycle work after process restarts.  The native
/// control plane remains authoritative; this service only drives requested
/// transitions and mirrors their public status into the portal database.
/// </summary>
public sealed class PortalMatchSupervisor(
    IServiceScopeFactory scopes,
    IHubContext<LobbyHub> lobbyHub,
    StreamPresenceTracker presence,
    ILogger<PortalMatchSupervisor> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await RequeueInterruptedAsync(stoppingToken);
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(3));
        do
        {
            try
            {
                await ProcessOneAsync(stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception exception)
            {
                logger.LogError(exception, "Portal match supervision cycle failed");
            }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    private async Task RequeueInterruptedAsync(CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var database = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
        var interrupted = await database.PortalMatches
            .Where(item => item.Status == "starting")
            .ToArrayAsync(cancellationToken);
        foreach (var match in interrupted)
        {
            match.Status = "provisioning";
            match.UpdatedAt = DateTimeOffset.UtcNow;
        }
        if (interrupted.Length > 0)
        {
            await database.SaveChangesAsync(cancellationToken);
        }
    }

    private async Task ProcessOneAsync(CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var database = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
        var control = scope.ServiceProvider.GetRequiredService<ControlPlaneClient>();
        var match = await database.PortalMatches
            .OrderBy(item => item.UpdatedAt)
            .FirstOrDefaultAsync(item => item.Status == "provisioning", cancellationToken);
        if (match is not null)
        {
            await StartMatchAsync(database, control, match, cancellationToken);
            return;
        }

        var observed = await database.PortalMatches
            .Where(item => item.Status == "running" || item.Status == "lobby" ||
                item.Status == "starting" || item.Status == "error")
            .OrderBy(item => item.UpdatedAt).Take(12).ToArrayAsync(cancellationToken);
        foreach (var item in observed)
        {
            await SynchronizeAsync(database, control, item, cancellationToken);
        }
        await AutoParkIdleBrowserMatchesAsync(database, control, cancellationToken);
    }

    private async Task AutoParkIdleBrowserMatchesAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        CancellationToken cancellationToken)
    {
        var cutoff = DateTimeOffset.UtcNow - TimeSpan.FromMinutes(10);
        var matches = await database.PortalMatches
            .Where(item => item.Status == "running")
            .ToArrayAsync(cancellationToken);
        foreach (var match in matches)
        {
            var humans = await database.PortalLobbySeats.AsNoTracking()
                .Where(item => item.MatchId == match.MatchId && item.ControllerKind == "human")
                .ToArrayAsync(cancellationToken);
            // Direct clients have no trustworthy browser-presence signal, and
            // AI-only simulations must continue unattended.
            if (humans.Length == 0 || humans.Any(item => item.JoinMode != "browser" ||
                    item.ControlInstanceId == null)) continue;
            var snapshots = humans.Select(item => presence.Get(item.ControlInstanceId!)).ToArray();
            if (snapshots.Any(item => !item.EverConnected || item.ActiveConnections > 0 ||
                    item.LastSeen is null || item.LastSeen > cutoff)) continue;
            try
            {
                await control.PostRawAsync($"api/v1/matches/{match.MatchId}/checkpoint",
                    new { slot = "control_recovery" }, cancellationToken);
                await control.PostRawAsync($"api/v1/matches/{match.MatchId}/park",
                    new { }, cancellationToken);
                match.Status = "parked";
                match.UpdatedAt = DateTimeOffset.UtcNow;
                database.PortalMatchEvents.Add(new PortalMatchEvent
                {
                    MatchId = match.MatchId, EventType = "idle_park",
                    Summary = "All browser players disconnected; a verified checkpoint was created and workers were parked.",
                });
                await database.SaveChangesAsync(cancellationToken);
                await NotifyAsync(match.MatchId, cancellationToken);
            }
            catch (ControlPlaneException exception)
            {
                logger.LogWarning(exception, "Idle park deferred for {MatchId}", match.MatchId);
            }
        }
    }

    private async Task StartMatchAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        PortalMatchProfile match, CancellationToken cancellationToken)
    {

        // Claim before the long native call. A crash leaves `starting`, which
        // startup reconciliation can safely inspect instead of issuing a
        // duplicate lobby mutation.
        match.Status = "starting";
        match.LastError = null;
        match.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(cancellationToken);
        await NotifyAsync(match.MatchId, cancellationToken);
        try
        {
            var observed = await control.GetMatchAsync(match.MatchId, cancellationToken);
            if (observed.Match.Status == "running")
            {
                match.Status = "running";
                match.CurrentTurn = observed.Match.LastTurn;
                match.CurrentYear = observed.Match.LastYear;
                match.UpdatedAt = DateTimeOffset.UtcNow;
                await database.SaveChangesAsync(cancellationToken);
                await NotifyAsync(match.MatchId, cancellationToken);
                return;
            }
            using var settings = JsonDocument.Parse(match.NativeSettingsJson);
            var operation = await control.StartLanMatchAsync(
                match.MatchId,
                match.DisplayName[..Math.Min(match.DisplayName.Length, 31)],
                match.LanProfile,
                match.ScenarioId is null && match.ResumeSlot is null
                    ? JsonSerializer.Deserialize<object>(settings.RootElement.GetRawText()) : null,
                match.ScenarioId, match.ResumeSlot,
                cancellationToken);
            match.Status = operation.AwaitingExternalHumans ? "lobby" : operation.Match.Status;
            match.CurrentTurn = operation.Match.LastTurn;
            match.CurrentYear = operation.Match.LastYear;
            match.UpdatedAt = DateTimeOffset.UtcNow;
            if (operation.ExternalJoin is { } externalJoin)
            {
                await StoreNativeJoinAsync(
                    database, match.MatchId, operation.NetworkSessionId, externalJoin,
                    cancellationToken);
            }
            await database.SaveChangesAsync(cancellationToken);
            if (match.Status == "running")
            {
                await EnsureAgentRunsAsync(database, control, match, cancellationToken);
            }
        }
        catch (Exception exception)
        {
            match.Status = "error";
            match.LastError = exception.Message[..Math.Min(exception.Message.Length, 4000)];
            match.UpdatedAt = DateTimeOffset.UtcNow;
            await database.SaveChangesAsync(CancellationToken.None);
            logger.LogError(exception, "Match {MatchId} failed to start", match.MatchId);
        }
        await NotifyAsync(match.MatchId, CancellationToken.None);
    }

    private async Task SynchronizeAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        PortalMatchProfile match, CancellationToken cancellationToken)
    {
        try
        {
            // Native reads share the bridge's serialized request slot with
            // the agent. A ten-second cadence keeps the portal current without
            // competing with ordinary semantic decisions every supervisor tick.
            JsonDocument? nativeStatus = null;
            if (DateTimeOffset.UtcNow - match.UpdatedAt >= TimeSpan.FromSeconds(10))
            {
                nativeStatus = await control.GetRawAsync(
                    $"api/v1/matches/{match.MatchId}/status", cancellationToken);
            }
            var observed = await control.GetMatchAsync(match.MatchId, cancellationToken);
            var previousStatus = match.Status;
            var previousTurn = match.CurrentTurn;
            match.Status = observed.Match.Status;
            match.CurrentTurn = observed.Match.LastTurn;
            match.CurrentYear = observed.Match.LastYear;
            match.UpdatedAt = DateTimeOffset.FromUnixTimeMilliseconds(
                (long)(observed.Match.UpdatedUnix * 1000));
            var seats = await database.PortalLobbySeats
                .Where(item => item.MatchId == match.MatchId)
                .ToArrayAsync(cancellationToken);
            foreach (var controlSeat in observed.Seats)
            {
                var seat = seats.SingleOrDefault(item => item.SeatIndex == controlSeat.SeatIndex);
                if (seat is null) continue;
                seat.FactionId = controlSeat.FactionId;
                seat.FactionName = controlSeat.FactionName;
                seat.ControlInstanceId ??= controlSeat.InstanceId;
                if (!string.IsNullOrWhiteSpace(controlSeat.PlayerHandle))
                    seat.PlayerHandle = controlSeat.PlayerHandle;
                seat.Status = controlSeat.Status;
                seat.UpdatedAt = DateTimeOffset.UtcNow;
            }
            if (nativeStatus is not null)
            {
                using (nativeStatus)
                {
                    if (nativeStatus.RootElement.TryGetProperty("seats", out var nativeSeats) &&
                        nativeSeats.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var nativeSeat in nativeSeats.EnumerateArray())
                        {
                            if (!nativeSeat.TryGetProperty("seat_index", out var indexValue) ||
                                !indexValue.TryGetInt32(out var index) ||
                                !nativeSeat.TryGetProperty("outcome", out var outcome) ||
                                outcome.ValueKind != JsonValueKind.Object) continue;
                            var seat = seats.SingleOrDefault(item => item.SeatIndex == index);
                            if (seat is null) continue;
                            seat.OutcomeFinalized = outcome.TryGetProperty(
                                "final_score_completed", out var finalized) && finalized.ValueKind == JsonValueKind.True;
                            seat.OutcomeResult = outcome.TryGetProperty(
                                "perspective_result", out var result) && result.ValueKind == JsonValueKind.String
                                ? result.GetString() : null;
                            seat.VictoryType = outcome.TryGetProperty(
                                "victory_type", out var victory) && victory.ValueKind == JsonValueKind.String
                                ? victory.GetString() : null;
                        }
                    }
                }
            }
            if (previousStatus != match.Status)
            {
                database.PortalMatchEvents.Add(new PortalMatchEvent
                {
                    MatchId = match.MatchId, EventType = "lifecycle",
                    Summary = $"Match state changed from {previousStatus} to {match.Status}.",
                });
            }
            if (match.CurrentTurn is not null && match.CurrentTurn != previousTurn)
            {
                var now = DateTimeOffset.UtcNow;
                var previousEvent = await database.PortalMatchEvents.AsNoTracking()
                    .Where(item => item.MatchId == match.MatchId && item.EventType == "turn")
                    .OrderByDescending(item => item.CreatedAt)
                    .FirstOrDefaultAsync(cancellationToken);
                database.PortalMatchEvents.Add(new PortalMatchEvent
                {
                    MatchId = match.MatchId, EventType = "turn",
                    Summary = $"Turn {match.CurrentTurn} · year {match.CurrentYear?.ToString() ?? "unknown"}.",
                    CreatedAt = now,
                });
                foreach (var seat in seats.Where(item =>
                             item.ControllerKind == "agent" && item.AgentId != null))
                {
                    var metric = new PortalTurnMetric
                    {
                        MatchId = match.MatchId,
                        AgentId = seat.AgentId!,
                        ProfileVersionId = seat.AiProfileVersionId,
                        Turn = match.CurrentTurn.Value,
                        DurationSeconds = previousEvent is null ? null :
                            Math.Max(0, (now - previousEvent.CreatedAt).TotalSeconds),
                        StartedAt = previousEvent?.CreatedAt ?? now,
                        CompletedAt = now,
                    };
                    database.PortalTurnMetrics.Add(metric);
                    await PopulateTelemetryAsync(
                        database, control, match.MatchId, seat.AgentId!, metric,
                        cancellationToken);
                }
            }
            await database.SaveChangesAsync(cancellationToken);
            if (match.Status == "running")
            {
                await EnsureAgentRunsAsync(database, control, match, cancellationToken);
                await ImportNativeChatAsync(database, control, match, cancellationToken);
            }
            if (previousStatus != match.Status || previousTurn != match.CurrentTurn)
                await NotifyAsync(match.MatchId, cancellationToken);
        }
        catch (ControlPlaneException exception) when (exception.Code is "unknown_match" or "control_unavailable")
        {
            logger.LogDebug(exception, "Control match {MatchId} is temporarily unavailable", match.MatchId);
        }
    }

    private async Task PopulateTelemetryAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        string matchId, string agentId, PortalTurnMetric metric,
        CancellationToken cancellationToken)
    {
        try
        {
            using var runsDocument = await control.GetRawAsync(
                "api/v1/harness-runs", cancellationToken);
            var run = runsDocument.RootElement.GetProperty("harness_runs").EnumerateArray()
                .Where(item => item.GetProperty("match_id").GetString() == matchId &&
                    item.GetProperty("agent_id").GetString() == agentId)
                .OrderByDescending(item => item.GetProperty("created_unix").GetDouble())
                .FirstOrDefault();
            if (run.ValueKind != JsonValueKind.Object) return;
            var runId = run.GetProperty("run_id").GetString();
            if (string.IsNullOrWhiteSpace(runId)) return;
            using var response = await control.PostRawAsync(
                $"api/v1/harness-runs/{Uri.EscapeDataString(runId)}/telemetry",
                new { }, cancellationToken);
            var telemetry = response.RootElement.GetProperty("result").GetProperty("telemetry");
            var prior = await database.PortalTurnMetrics.AsNoTracking()
                .Where(item => item.MatchId == matchId && item.AgentId == agentId)
                .ToArrayAsync(cancellationToken);
            static long Value(JsonElement item, string name) =>
                item.TryGetProperty(name, out var value) && value.TryGetInt64(out var parsed)
                    ? parsed : 0;
            metric.PromptTokens = Math.Max(0,
                Value(telemetry, "input_tokens") - prior.Sum(item => item.PromptTokens));
            metric.CompletionTokens = Math.Max(0,
                Value(telemetry, "output_tokens") - prior.Sum(item => item.CompletionTokens));
            metric.CacheReadTokens = Math.Max(0,
                Value(telemetry, "cache_read_tokens") - prior.Sum(item => item.CacheReadTokens));
            metric.CacheWriteTokens = Math.Max(0,
                Value(telemetry, "cache_write_tokens") - prior.Sum(item => item.CacheWriteTokens));
            metric.ReasoningTokens = Math.Max(0,
                Value(telemetry, "reasoning_tokens") - prior.Sum(item => item.ReasoningTokens));
            metric.ApiCalls = Math.Max(0,
                Value(telemetry, "api_calls") - prior.Sum(item => item.ApiCalls));
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            logger.LogDebug(exception,
                "Hermes telemetry is temporarily unavailable for {MatchId}/{AgentId}",
                matchId, agentId);
        }
    }

    private async Task EnsureAgentRunsAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        PortalMatchProfile match, CancellationToken cancellationToken)
    {
        using var runsDocument = await control.GetRawAsync("api/v1/harness-runs", cancellationToken);
        var runs = runsDocument.RootElement.GetProperty("harness_runs").EnumerateArray().ToArray();
        var seats = await database.PortalLobbySeats.AsNoTracking()
            .Where(item => item.MatchId == match.MatchId && item.ControllerKind == "agent")
            .ToArrayAsync(cancellationToken);
        foreach (var seat in seats)
        {
            if (seat.AgentId is null || runs.Any(run =>
                    run.GetProperty("match_id").GetString() == match.MatchId &&
                    run.GetProperty("agent_id").GetString() == seat.AgentId &&
                    run.GetProperty("status").GetString() is "queued" or "starting" or "running" or "restarting"))
                continue;
            var profile = await database.PortalAiProfileVersions.AsNoTracking()
                .SingleOrDefaultAsync(item => item.AgentId == seat.AgentId, cancellationToken);
            if (profile is null)
            {
                logger.LogWarning("Agent seat {AgentId} has no versioned portal profile; automatic Hermes launch skipped", seat.AgentId);
                continue;
            }
            try
            {
                using var started = await control.PostRawAsync("api/v1/harness-runs", new
                {
                    match_id = match.MatchId,
                    agent_id = seat.AgentId,
                    provider_id = profile.ProviderId,
                    model_id = profile.ModelId,
                    context_length = profile.ContextLength,
                    reasoning_effort = profile.ReasoningEffort,
                    run_budget_seconds = 86_400,
                    max_turns = 5_000,
                    restart_limit = 1_000,
                    initial_prompt = "Join the managed match now. Play autonomously as a genuine participant using only semantic SMACX tools. Pay attention to chat, diplomacy, commitments, and the durable per-match memory system. Continue until the operator stops the match or a semantic capability gap is reported.",
                }, cancellationToken);
                database.PortalMatchEvents.Add(new PortalMatchEvent
                {
                    MatchId = match.MatchId, EventType = "agent_started",
                    Summary = $"Started {profile.DisplayName} v{profile.Version} ({profile.ModelId}, {profile.ReasoningEffort}).",
                });
                await database.SaveChangesAsync(cancellationToken);
            }
            catch (ControlPlaneException exception) when (exception.Code == "harness_run_already_active_for_seat")
            {
                // Another supervisor cycle won the idempotent start race.
            }
        }
    }

    private async Task ImportNativeChatAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        PortalMatchProfile match, CancellationToken cancellationToken)
    {
        var imported = false;
        var seats = await database.PortalLobbySeats
            .Where(item => item.MatchId == match.MatchId && item.ControllerKind == "human" &&
                item.JoinMode == "browser" && item.ControlInstanceId != null)
            .ToArrayAsync(cancellationToken);
        foreach (var seat in seats)
        {
            try
            {
                using var document = await control.PostRawAsync(
                    $"api/v1/workers/{seat.ControlInstanceId}/chat",
                    new { action = "list", after_sequence = seat.LastChatSequence }, cancellationToken);
                var root = document.RootElement;
                var participants = root.GetProperty("participants").EnumerateArray().ToDictionary(
                    item => item.GetProperty("faction_id").GetInt32(),
                    item =>
                    {
                        var player = item.GetProperty("player_name").GetString();
                        var factionName = item.GetProperty("faction_name").GetString();
                        if (!string.IsNullOrWhiteSpace(player) && !string.IsNullOrWhiteSpace(factionName) &&
                            !string.Equals(player, factionName, StringComparison.OrdinalIgnoreCase))
                            return $"{player} · {factionName}";
                        return player ?? factionName ?? "Unknown faction";
                    });
                foreach (var message in root.GetProperty("messages").EnumerateArray())
                {
                    var sequence = message.GetProperty("sequence").GetInt64();
                    seat.LastChatSequence = Math.Max(seat.LastChatSequence, sequence);
                    if (message.GetProperty("direction").GetString() != "inbound") continue;
                    var faction = message.GetProperty("sender_faction_id").GetInt32();
                    var uid = $"{seat.ControlInstanceId}:{sequence}:from:{faction}";
                    if (await database.PortalLobbyMessages.AnyAsync(
                            item => item.MatchId == match.MatchId && item.NativeMessageUid == uid,
                            cancellationToken)) continue;
                    database.PortalLobbyMessages.Add(new PortalLobbyMessage
                    {
                        MatchId = match.MatchId, SenderHandle = participants.GetValueOrDefault(
                            faction, $"Faction {faction}"),
                        Content = message.GetProperty("text").GetString() ?? string.Empty,
                        DeliveredToGame = true, NativeMessageUid = uid,
                    });
                    imported = true;
                }
                if (root.TryGetProperty("latest_sequence", out var latest))
                    seat.LastChatSequence = Math.Max(seat.LastChatSequence, latest.GetInt64());
                await database.SaveChangesAsync(cancellationToken);
            }
            catch (Exception exception)
            {
                logger.LogDebug(exception, "Native chat poll failed for {InstanceId}", seat.ControlInstanceId);
            }
        }
        if (imported) await NotifyAsync(match.MatchId, cancellationToken);
    }

    private Task NotifyAsync(string matchId, CancellationToken cancellationToken) =>
        lobbyHub.Clients.Group(LobbyHub.GroupName(matchId))
            .SendAsync("LobbyChanged", matchId, cancellationToken);

    private static async Task StoreNativeJoinAsync(
        ApplicationDbContext database, string matchId, string? networkSessionId,
        JsonElement externalJoin, CancellationToken cancellationToken)
    {
        var players = externalJoin.TryGetProperty("human_players", out var humans) &&
                humans.ValueKind == JsonValueKind.Array
            ? humans.EnumerateArray().Select(item => new NativeJoinPlayer(
                item.GetProperty("seat_index").GetInt32(),
                item.GetProperty("player_name").GetString() ?? "Player",
                item.TryGetProperty("expected_faction_id", out var faction) &&
                    faction.ValueKind == JsonValueKind.Number ? faction.GetInt32() : null)).ToArray()
            : [];
        var details = new NativeJoinDetails(
            externalJoin.TryGetProperty("host_address", out var host) ? host.GetString() ?? "" : "",
            externalJoin.TryGetProperty("session_name", out var session) ? session.GetString() ?? "" : "",
            networkSessionId,
            externalJoin.TryGetProperty("network", out var network) ? network.GetString() ?? "LAN" : "LAN",
            players,
            externalJoin.TryGetProperty("instructions", out var instructions)
                ? instructions.GetString() ?? "" :
                "Join Multiplayer > TCP/IP with the assigned handle, then mark Ready.");
        var key = $"native-join:{matchId}";
        var setting = await database.PortalSettings.SingleOrDefaultAsync(
            item => item.Key == key, cancellationToken);
        if (setting is null)
        {
            setting = new PortalSetting { Key = key };
            database.PortalSettings.Add(setting);
        }
        setting.Value = JsonSerializer.Serialize(details);
        setting.UpdatedAt = DateTimeOffset.UtcNow;
    }
}
