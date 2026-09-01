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
    private int dormantReconcileOffset;
    private bool dormantReconciled;
    private readonly Dictionary<string, string> announcedCapabilityIncidents =
        new(StringComparer.Ordinal);

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
        await ReconcileDormantMatchesAsync(database, control, cancellationToken);
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
            try
            {
                var incident = await control.GetActiveCapabilityIncidentAsync(
                    item.MatchId, cancellationToken);
                if (incident is null)
                {
                    announcedCapabilityIncidents.Remove(item.MatchId);
                }
                else if (!announcedCapabilityIncidents.TryGetValue(item.MatchId, out var announced) ||
                         announced != incident.IncidentId)
                {
                    announcedCapabilityIncidents[item.MatchId] = incident.IncidentId;
                    await NotifyAsync(item.MatchId, cancellationToken);
                }
            }
            catch (ControlPlaneException exception)
            {
                logger.LogDebug(exception,
                    "Capability incident poll failed for {MatchId}", item.MatchId);
            }
        }
        await AutoParkIdleBrowserMatchesAsync(database, control, cancellationToken);
    }

    private async Task ReconcileDormantMatchesAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        CancellationToken cancellationToken)
    {
        if (dormantReconciled) return;
        var dormant = await database.PortalMatches.AsNoTracking()
            .Where(item => item.Status == "parked" || item.Status == "completed")
            .OrderBy(item => item.MatchId).Skip(dormantReconcileOffset).Take(20)
            .ToArrayAsync(cancellationToken);
        if (dormant.Length == 0)
        {
            dormantReconciled = true;
            return;
        }
        var processed = 0;
        foreach (var match in dormant)
        {
            try
            {
                var observed = await control.GetMatchAsync(match.MatchId, cancellationToken);
                var status = observed.Match.Status;
                if (status != match.Status)
                {
                    if (status != "parked" && status != "completed")
                    {
                        using var parked = await control.PostRawAsync(
                            $"api/v1/matches/{match.MatchId}/park", new { }, cancellationToken);
                        status = "parked";
                    }
                    if (match.Status == "completed" && status != "completed")
                    {
                        using var completed = await control.PostRawAsync(
                            $"api/v1/matches/{match.MatchId}/complete", new { }, cancellationToken);
                    }
                    logger.LogInformation(
                        "Reconciled dormant portal campaign {MatchId} to {Status}",
                        match.MatchId, match.Status);
                }
                var managedSeats = await database.PortalLobbySeats
                    .Where(item => item.MatchId == match.MatchId && item.ControlInstanceId != null)
                    .ToArrayAsync(cancellationToken);
                foreach (var seat in managedSeats)
                {
                    seat.ConnectionState = match.Status == "completed" ? "retired" : "worker_stopped";
                    seat.UpdatedAt = DateTimeOffset.UtcNow;
                }
                if (managedSeats.Length > 0)
                    await database.SaveChangesAsync(cancellationToken);
                processed++;
            }
            catch (ControlPlaneException exception)
            {
                logger.LogWarning(exception,
                    "Dormant campaign reconciliation deferred for {MatchId}", match.MatchId);
                dormantReconcileOffset += processed;
                return;
            }
        }
        dormantReconcileOffset += dormant.Length;
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
            if (snapshots.Any(item => item.ActiveConnections > 0)) continue;
            var lastHumanActivity = snapshots.Where(item => item.LastSeen is not null)
                .Select(item => item.LastSeen!.Value).DefaultIfEmpty(match.CreatedAt).Max();
            if (lastHumanActivity > cutoff) continue;
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
            var nativeSessionLost = false;
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
                if (!string.IsNullOrWhiteSpace(controlSeat.PlayerHandle) &&
                    (seat.ControllerKind != "human" || string.IsNullOrWhiteSpace(seat.PlayerHandle)))
                    seat.PlayerHandle = controlSeat.PlayerHandle;
                seat.Status = controlSeat.Status;
                seat.UpdatedAt = DateTimeOffset.UtcNow;
                if (seat.ControllerKind == "human" && seat.JoinMode == "browser" &&
                    seat.ControlInstanceId is not null)
                {
                    var stream = presence.Get(seat.ControlInstanceId);
                    if (stream.LastSeen is not null)
                        seat.LastBrowserSeenAt = stream.LastSeen;
                    seat.ConnectionState = stream.ActiveConnections > 0 ? "connected" :
                        !stream.EverConnected ? "awaiting_first_connection" :
                        stream.LastSeen > DateTimeOffset.UtcNow - TimeSpan.FromSeconds(30)
                            ? "temporarily_disconnected" : "idle_grace_period";
                }
                else if (seat.ControllerKind == "human" && seat.JoinMode != "browser")
                {
                    seat.ConnectionState = match.Status == "running" ? "native_client" : "waiting";
                }
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
                                !indexValue.TryGetInt32(out var index)) continue;
                            var seat = seats.SingleOrDefault(item => item.SeatIndex == index);
                            if (seat is null) continue;
                            if (nativeSeat.TryGetProperty("worker", out var worker) &&
                                worker.ValueKind == JsonValueKind.Object)
                            {
                                var running = worker.TryGetProperty("running", out var runningValue) &&
                                    runningValue.ValueKind == JsonValueKind.True;
                                var healthy = worker.TryGetProperty("health", out var healthValue) &&
                                    healthValue.ValueKind == JsonValueKind.String &&
                                    healthValue.GetString() == "healthy";
                                if (running)
                                {
                                    seat.LastWorkerSeenAt = DateTimeOffset.UtcNow;
                                    if (seat.ControllerKind == "agent")
                                        seat.ConnectionState = healthy ? "connected" : "starting";
                                }
                                else if (match.Status == "running" && seat.DelegationStatus != "active")
                                {
                                    seat.ConnectionState = "worker_stopped";
                                    seat.LastExitKind ??= "unexpected_worker_stop";
                                }
                            }
                            if (nativeSeat.TryGetProperty("native", out var native) &&
                                native.ValueKind == JsonValueKind.Object &&
                                native.TryGetProperty("lifecycle", out var lifecycle) &&
                                lifecycle.ValueKind == JsonValueKind.String &&
                                lifecycle.GetString() == "menu" && match.Status == "running")
                            {
                                seat.ConnectionState = "left_native_game";
                                seat.LastExitKind = "returned_to_menu";
                                nativeSessionLost = true;
                            }
                            if (!nativeSeat.TryGetProperty("outcome", out var outcome) ||
                                outcome.ValueKind != JsonValueKind.Object) continue;
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
                             item.ControllerKind == "agent" && item.AgentId != null &&
                             item.ControlInstanceId != null))
                {
                    var metric = new PortalTurnMetric
                    {
                        MatchId = match.MatchId,
                        // A reusable profile may occupy several seats. The
                        // worker instance is the durable player identity for
                        // this run; ProfileId remains the aggregation
                        // key used for model analytics.
                        AgentId = seat.ControlInstanceId!,
                        ProfileId = seat.AiProfileId,
                        Turn = match.CurrentTurn.Value,
                        DurationSeconds = previousEvent is null ? null :
                            Math.Max(0, (now - previousEvent.CreatedAt).TotalSeconds),
                        StartedAt = previousEvent?.CreatedAt ?? now,
                        CompletedAt = now,
                    };
                    database.PortalTurnMetrics.Add(metric);
                    await PopulateTelemetryAsync(
                        database, control, match.MatchId, seat.ControlInstanceId!, metric,
                        cancellationToken);
                }
            }
            await database.SaveChangesAsync(cancellationToken);
            if (previousStatus != "completed" && match.Status == "completed")
            {
                // A native final-score screen changes the control lifecycle
                // before the portal sees it. Complete is idempotent and stops
                // autonomous callers before it releases bulky worker state.
                using (await control.PostRawAsync(
                    $"api/v1/matches/{match.MatchId}/complete", new { }, cancellationToken)) { }
                foreach (var seat in seats.Where(item => item.ControlInstanceId is not null))
                {
                    seat.ConnectionState = "retired";
                    seat.UpdatedAt = DateTimeOffset.UtcNow;
                }
                await database.SaveChangesAsync(cancellationToken);
            }
            if (match.Status == "running")
            {
                if (nativeSessionLost)
                {
                    await RecoverLostNativeSessionAsync(
                        database, control, match, cancellationToken);
                }
                else
                {
                    await EnsureAgentRunsAsync(database, control, match, cancellationToken);
                    await ImportNativeChatAsync(database, control, match, cancellationToken);
                    if (match.CurrentTurn is not null)
                        await TryTurnCheckpointAsync(
                            database, control, match, cancellationToken);
                }
            }
            if (previousStatus != match.Status || previousTurn != match.CurrentTurn)
                await NotifyAsync(match.MatchId, cancellationToken);
        }
        catch (ControlPlaneException exception) when (exception.Code is "unknown_match" or "control_unavailable")
        {
            logger.LogDebug(exception, "Control match {MatchId} is temporarily unavailable", match.MatchId);
        }
    }

    private async Task TryTurnCheckpointAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        PortalMatchProfile match, CancellationToken cancellationToken)
    {
        var alreadySaved = await database.PortalStableCheckpoints.AsNoTracking().AnyAsync(
            item => item.MatchId == match.MatchId && item.Turn == match.CurrentTurn,
            cancellationToken);
        if (alreadySaved) return;
        try
        {
            using var document = await control.PostRawAsync(
                $"api/v1/matches/{match.MatchId}/checkpoint",
                new { slot = "control_recovery" }, cancellationToken);
            var checkpoint = document.RootElement.GetProperty("checkpoint");
            database.PortalStableCheckpoints.Add(new PortalStableCheckpoint
            {
                MatchId = match.MatchId,
                Slot = checkpoint.GetProperty("slot").GetString() ?? "control_recovery",
                Turn = checkpoint.TryGetProperty("turn", out var turn) &&
                    turn.ValueKind == JsonValueKind.Number ? turn.GetInt32() : match.CurrentTurn,
                Year = checkpoint.TryGetProperty("year", out var year) &&
                    year.ValueKind == JsonValueKind.Number ? year.GetInt32() : match.CurrentYear,
                Stability = "three_sample_verified_turn_boundary",
            });
            await database.SaveChangesAsync(cancellationToken);
        }
        catch (ControlPlaneException exception)
        {
            // Simultaneous-turn packets, native dialogs, or a model taking its
            // next action can make this moment unsafe. The following monitor
            // pass retries without pausing or rolling anyone back.
            logger.LogDebug(exception,
                "Turn checkpoint deferred for {MatchId} at turn {Turn}",
                match.MatchId, match.CurrentTurn);
        }
    }

    private async Task RecoverLostNativeSessionAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        PortalMatchProfile match, CancellationToken cancellationToken)
    {
        var existing = await database.PortalMaintenanceOperations.AnyAsync(item =>
            item.MatchId == match.MatchId && item.Kind == "automatic_recovery" &&
            (item.Status == "queued" || item.Status == "running"), cancellationToken);
        if (existing) return;
        var checkpoint = await database.PortalStableCheckpoints.AsNoTracking()
            .Where(item => item.MatchId == match.MatchId)
            .OrderByDescending(item => item.CreatedAt)
            .FirstOrDefaultAsync(cancellationToken);
        if (checkpoint is null)
        {
            match.Status = "error";
            match.LastError = "A native player left before the first verified recovery checkpoint. The match is preserved for operator review.";
            await database.SaveChangesAsync(cancellationToken);
            return;
        }
        var operation = new PortalMaintenanceOperation
        {
            MatchId = match.MatchId, Kind = "automatic_recovery", Status = "running",
            Phase = "recovering_native_session", CompletedSteps = 1, TotalSteps = 2,
            StableTurn = checkpoint.Turn, StableYear = checkpoint.Year,
            Summary = "A native session ended; reconnecting every managed seat to the latest verified checkpoint.",
            CanCancel = false,
        };
        database.PortalMaintenanceOperations.Add(operation);
        await database.SaveChangesAsync(cancellationToken);
        await NotifyAsync(match.MatchId, cancellationToken);
        try
        {
            using (await control.PostRawAsync(
                $"api/v1/matches/{match.MatchId}/recover", new { }, cancellationToken)) { }
            operation.Status = "completed"; operation.Phase = "complete";
            operation.CompletedSteps = 2; operation.CompletedAt = DateTimeOffset.UtcNow;
            operation.Summary = "Every managed seat reconnected to the latest verified checkpoint.";
            operation.UpdatedAt = DateTimeOffset.UtcNow;
            match.Status = "running"; match.LastError = null;
            database.PortalMatchEvents.Add(new PortalMatchEvent
            {
                MatchId = match.MatchId, EventType = "automatic_recovery",
                Summary = $"Recovered the native session from turn {checkpoint.Turn?.ToString() ?? "?"}.",
            });
        }
        catch (Exception exception)
        {
            operation.Status = "failed"; operation.Phase = "operator_review";
            operation.Summary = $"Automatic recovery stopped safely: {exception.Message}";
            operation.CompletedAt = DateTimeOffset.UtcNow;
            operation.UpdatedAt = DateTimeOffset.UtcNow;
            match.Status = "error"; match.LastError = operation.Summary;
        }
        match.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(CancellationToken.None);
        await NotifyAsync(match.MatchId, CancellationToken.None);
    }

    private async Task PopulateTelemetryAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        string matchId, string instanceId, PortalTurnMetric metric,
        CancellationToken cancellationToken)
    {
        try
        {
            using var runsDocument = await control.GetRawAsync(
                "api/v1/harness-runs", cancellationToken);
            var run = runsDocument.RootElement.GetProperty("harness_runs").EnumerateArray()
                .Where(item => item.GetProperty("match_id").GetString() == matchId &&
                    item.GetProperty("instance_id").GetString() == instanceId)
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
                .Where(item => item.MatchId == matchId && item.AgentId == instanceId)
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
                "Hermes telemetry is temporarily unavailable for {MatchId}/{InstanceId}",
                matchId, instanceId);
        }
    }

    private async Task EnsureAgentRunsAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        PortalMatchProfile match, CancellationToken cancellationToken)
    {
        using var runsDocument = await control.GetRawAsync("api/v1/harness-runs", cancellationToken);
        var runs = runsDocument.RootElement.GetProperty("harness_runs").EnumerateArray().ToArray();
        var controlMatch = await control.GetMatchAsync(match.MatchId, cancellationToken);
        var seats = await database.PortalLobbySeats.AsNoTracking()
            .Where(item => item.MatchId == match.MatchId && item.ControllerKind == "agent")
            .ToArrayAsync(cancellationToken);
        foreach (var seat in seats)
        {
            var runtimeSeat = controlMatch.Seats.SingleOrDefault(item =>
                item.InstanceId == seat.ControlInstanceId);
            var runtimeAgentId = runtimeSeat?.AgentId;
            if (seat.AgentId is null || seat.ControlInstanceId is null ||
                string.IsNullOrWhiteSpace(runtimeAgentId) || runs.Any(run =>
                    run.GetProperty("match_id").GetString() == match.MatchId &&
                    run.GetProperty("instance_id").GetString() == seat.ControlInstanceId &&
                    run.GetProperty("status").GetString() is "queued" or "starting" or "running" or "restarting"))
                continue;
            var profile = await database.PortalAiProfiles.AsNoTracking()
                .SingleOrDefaultAsync(item => item.AgentId == seat.AgentId, cancellationToken);
            if (profile is null)
            {
                logger.LogWarning("Agent seat {AgentId} has no portal profile; automatic Hermes launch skipped", seat.AgentId);
                continue;
            }
            try
            {
                using var started = await control.PostRawAsync("api/v1/harness-runs", new
                {
                    match_id = match.MatchId,
                    agent_id = runtimeAgentId,
                    provider_id = profile.ProviderId,
                    model_id = profile.ModelId,
                    // Null deliberately means provider-advertised automatic.
                    // The control plane resolves and records the exact value;
                    // never silently cap a profile selected by the operator.
                    context_length = profile.ContextLength,
                    reasoning_effort = profile.ReasoningEffort,
                    generation_settings = GenerationPayload(profile.GenerationSettingsJson),
                    run_budget_seconds = 86_400,
                    max_turns = 5_000,
                    restart_limit = 1_000,
                    initial_prompt = "Begin or resume this managed match now. Follow the system contract's opening briefing protocol, then continue autonomous play until the operator stops the run or a semantic capability gap is reported.",
                }, cancellationToken);
                database.PortalMatchEvents.Add(new PortalMatchEvent
                {
                    MatchId = match.MatchId, EventType = "agent_started",
                    Summary = $"Started {profile.DisplayName} ({profile.ModelId}, {profile.ReasoningEffort}, {GenerationPreset(profile.GenerationSettingsJson)}).",
                });
                await database.SaveChangesAsync(cancellationToken);
            }
            catch (ControlPlaneException exception) when (exception.Code == "harness_run_already_active_for_seat")
            {
                // Another supervisor cycle won the idempotent start race.
            }
        }
    }

    private static string GenerationPreset(string json)
    {
        try
        {
            using var document = JsonDocument.Parse(json);
            return document.RootElement.TryGetProperty("Preset", out var pascal)
                ? pascal.GetString() ?? "provider-default"
                : document.RootElement.TryGetProperty("preset", out var camel)
                    ? camel.GetString() ?? "provider-default"
                    : "provider-default";
        }
        catch (JsonException)
        {
            return "provider-default";
        }
    }

    private static object GenerationPayload(string json)
    {
        try
        {
            return JsonSerializer.Deserialize<object>(json) ?? new { preset = "provider-default" };
        }
        catch (JsonException)
        {
            return new { preset = "provider-default" };
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
        var canonicalParticipants = (await database.PortalLobbySeats.AsNoTracking()
                .Where(item => item.MatchId == match.MatchId && item.FactionId != null &&
                    item.PlayerHandle != null)
                .Select(item => new { FactionId = item.FactionId!.Value, item.PlayerHandle })
                .ToArrayAsync(cancellationToken))
            .GroupBy(item => item.FactionId)
            .ToDictionary(group => group.Key, group => group.First().PlayerHandle!);
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
                    var content = message.GetProperty("text").GetString() ?? string.Empty;
                    var channel = "private";
                    string? conversationId = null;
                    string? conversationName = null;
                    if (content.StartsWith("[Group: ", StringComparison.Ordinal))
                    {
                        var end = content.IndexOf("] ", StringComparison.Ordinal);
                        if (end > 8)
                        {
                            conversationName = content[8..end];
                            var group = await database.PortalChatGroups.AsNoTracking()
                                .SingleOrDefaultAsync(item => item.MatchId == match.MatchId &&
                                    item.DisplayName == conversationName && item.Status == "active",
                                    cancellationToken);
                            if (group is not null)
                            {
                                channel = "group";
                                conversationId = group.GroupId;
                                content = content[(end + 2)..];
                            }
                        }
                    }
                    database.PortalLobbyMessages.Add(new PortalLobbyMessage
                    {
                        MatchId = match.MatchId, SenderHandle = canonicalParticipants
                            .GetValueOrDefault(faction) ?? participants.GetValueOrDefault(
                                faction, $"Faction {faction}"),
                        Content = content,
                        DeliveredToGame = true, NativeMessageUid = uid,
                        Channel = channel, ConversationId = conversationId,
                        ConversationName = conversationName,
                        SenderFactionId = faction,
                        RecipientFactionId = seat.FactionId ?? 0,
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
