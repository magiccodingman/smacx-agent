using System.Text.Json;
using Microsoft.AspNetCore.SignalR;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;
using Smacx.Portal.Hubs;

namespace Smacx.Portal.Services;

/// <summary>
/// Executes approved disruptive operations as resumable, visible workflows.
/// The native control plane remains the authority for quiescence, saves, and
/// worker lifetimes; approval alone can never bypass those gates.
/// </summary>
public sealed class PortalMaintenanceCoordinator(
    IServiceScopeFactory scopes,
    IHubContext<LobbyHub> hub,
    ILogger<PortalMaintenanceCoordinator> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(2));
        do
        {
            try { await ProcessOneAsync(stoppingToken); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception exception) { logger.LogError(exception, "Managed maintenance cycle failed"); }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    private async Task ProcessOneAsync(CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var database = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
        var control = scope.ServiceProvider.GetRequiredService<ControlPlaneClient>();
        if (await ProcessCapabilityRecoveryAsync(database, control, cancellationToken))
            return;
        // Rotate among approved work by its last attempt time.  A match that is
        // waiting for a human/native safe boundary must never starve approved
        // maintenance for every other match on the server.
        var proposal = await database.PortalGovernanceProposals
            .Where(item => item.Status == "approved")
            .OrderBy(item => database.PortalMaintenanceOperations
                .Where(operation => operation.ProposalId == item.ProposalId)
                .Select(operation => (DateTimeOffset?)operation.UpdatedAt)
                .FirstOrDefault())
            .ThenBy(item => item.ResolvedAt)
            .FirstOrDefaultAsync(cancellationToken);
        if (proposal is null) return;
        if (proposal.Kind == "waive_resolution_cooldown")
        {
            proposal.Status = "executed";
            database.PortalMatchEvents.Add(new PortalMatchEvent
            {
                MatchId = proposal.MatchId, EventType = "resolution_cooldown_waived",
                Summary = "Players approved another native resolution request.",
            });
            await database.SaveChangesAsync(cancellationToken);
            await NotifyAsync(proposal.MatchId, cancellationToken);
            return;
        }
        var operation = await database.PortalMaintenanceOperations.SingleOrDefaultAsync(
            item => item.ProposalId == proposal.ProposalId, cancellationToken);
        if (operation is null)
        {
            operation = new PortalMaintenanceOperation
            {
                ProposalId = proposal.ProposalId, MatchId = proposal.MatchId,
                Kind = proposal.Kind, PayloadJson = proposal.PayloadJson,
                Summary = "Waiting for a stable native checkpoint.", TotalSteps = 6,
            };
            database.PortalMaintenanceOperations.Add(operation);
            await database.SaveChangesAsync(cancellationToken);
        }
        // Reconcile durable terminal work if a previous process stopped after
        // committing the operation but before updating the proposal row.
        if (operation.Status is "completed" or "failed")
        {
            proposal.Status = operation.Status == "completed" ? "executed" : "failed";
            await database.SaveChangesAsync(cancellationToken);
            return;
        }
        try
        {
            operation.Status = "running";
            await StepAsync(database, operation, "quiescing_native_peers",
                "Waiting for all managed peers and native packets to become stable.", 0,
                cancellationToken);
            using var checkpointDocument = await control.PostRawAsync(
                $"api/v1/matches/{proposal.MatchId}/checkpoint",
                new { slot = "control_recovery" }, cancellationToken);
            var checkpoint = checkpointDocument.RootElement.GetProperty("checkpoint");
            operation.StableTurn = checkpoint.TryGetProperty("turn", out var turn) &&
                turn.ValueKind == JsonValueKind.Number ? turn.GetInt32() : null;
            operation.StableYear = checkpoint.TryGetProperty("year", out var year) &&
                year.ValueKind == JsonValueKind.Number ? year.GetInt32() : null;
            database.PortalStableCheckpoints.Add(new PortalStableCheckpoint
            {
                MatchId = proposal.MatchId, OperationId = operation.OperationId,
                Slot = checkpoint.GetProperty("slot").GetString() ?? "control_recovery",
                Turn = operation.StableTurn, Year = operation.StableYear,
                SeatMapJson = "[]", Stability = "three_sample_verified",
            });
            await StepAsync(database, operation, "checkpoint_verified",
                $"Stable checkpoint captured at turn {operation.StableTurn?.ToString() ?? "?"}.", 2,
                cancellationToken);
            // Only stop autonomous callers after the native save is verified.
            // They may need to resolve an ordinary popup or finish a legal
            // action before the match can reach the safe boundary.
            await StopHarnessRunsAsync(control, proposal.MatchId, cancellationToken);
            using (await control.PostRawAsync(
                $"api/v1/matches/{proposal.MatchId}/park", new { }, cancellationToken)) { }
            await StepAsync(database, operation, "workers_parked",
                "Every managed game worker is parked at the verified checkpoint.", 3,
                cancellationToken);

            if (proposal.Kind == "native_resolution_change")
            {
                using var payload = JsonDocument.Parse(proposal.PayloadJson);
                var profileId = payload.RootElement.GetProperty("profileId").GetString()!;
                using (await control.PostRawAsync(
                    $"api/v1/matches/{proposal.MatchId}/resolution",
                    new { profile_id = profileId }, cancellationToken)) { }
                await StepAsync(database, operation, "resolution_applied",
                    $"Native framebuffer profile {profileId} is ready for the new worker lifetime.", 4,
                    cancellationToken);
                using (await control.PostRawAsync(
                    $"api/v1/matches/{proposal.MatchId}/recover", new { }, cancellationToken)) { }
                await CompleteAsync(database, proposal, operation, "running",
                    "Resolution changed and every managed seat recovered from the stable checkpoint.",
                    cancellationToken);
            }
            else if (proposal.Kind == "continue_without_player")
            {
                var seatIndex = SeatIndex(proposal.PayloadJson);
                using (await control.PostRawAsync(
                    $"api/v1/matches/{proposal.MatchId}/seat-controller",
                    new { seat_index = seatIndex, delegated = true }, cancellationToken)) { }
                await StepAsync(database, operation, "temporary_controller_applied",
                    $"Seat {seatIndex + 1} will continue under the stock game AI.", 4,
                    cancellationToken);
                using (await control.PostRawAsync(
                    $"api/v1/matches/{proposal.MatchId}/recover", new { }, cancellationToken)) { }
                var seat = await database.PortalLobbySeats.SingleAsync(item =>
                    item.MatchId == proposal.MatchId && item.SeatIndex == seatIndex,
                    cancellationToken);
                seat.DelegationStatus = "active";
                seat.TemporaryControllerKind = "native_ai";
                seat.DelegatedAt = DateTimeOffset.UtcNow;
                seat.ConnectionState = "delegated";
                await CompleteAsync(database, proposal, operation, "running",
                    $"{seat.PlayerHandle ?? $"Seat {seatIndex + 1}"} is temporarily controlled by the stock game AI.",
                    cancellationToken);
            }
            else if (proposal.Kind == "reclaim_player_seat")
            {
                var seatIndex = SeatIndex(proposal.PayloadJson);
                using (await control.PostRawAsync(
                    $"api/v1/matches/{proposal.MatchId}/seat-controller",
                    new { seat_index = seatIndex, delegated = false }, cancellationToken)) { }
                await StepAsync(database, operation, "player_controller_restored",
                    $"Seat {seatIndex + 1} is reserved for its returning player.", 4,
                    cancellationToken);
                using (await control.PostRawAsync(
                    $"api/v1/matches/{proposal.MatchId}/recover", new { }, cancellationToken)) { }
                var seat = await database.PortalLobbySeats.SingleAsync(item =>
                    item.MatchId == proposal.MatchId && item.SeatIndex == seatIndex,
                    cancellationToken);
                seat.DelegationStatus = "none";
                seat.TemporaryControllerKind = "none";
                seat.DelegatedAt = null;
                seat.ConnectionState = "awaiting_browser";
                await CompleteAsync(database, proposal, operation, "running",
                    $"{seat.PlayerHandle ?? $"Seat {seatIndex + 1}"} reclaimed the saved faction.",
                    cancellationToken);
            }
            else if (proposal.Kind == "transfer_host")
            {
                var seatIndex = SeatIndex(proposal.PayloadJson);
                using (await control.PostRawAsync(
                    $"api/v1/matches/{proposal.MatchId}/host-seat",
                    new { seat_index = seatIndex }, cancellationToken)) { }
                var portalSeats = await database.PortalLobbySeats.Where(item =>
                    item.MatchId == proposal.MatchId).ToArrayAsync(cancellationToken);
                foreach (var portalSeat in portalSeats)
                    portalSeat.IsManagedHost = portalSeat.SeatIndex == seatIndex;
                await StepAsync(database, operation, "host_selected",
                    $"Seat {seatIndex + 1} will host the recovered native session.", 4,
                    cancellationToken);
                using (await control.PostRawAsync(
                    $"api/v1/matches/{proposal.MatchId}/recover", new { }, cancellationToken)) { }
                await CompleteAsync(database, proposal, operation, "running",
                    $"Native host authority moved to seat {seatIndex + 1}.",
                    cancellationToken);
            }
            else if (proposal.Kind == "park_match")
            {
                await CompleteAsync(database, proposal, operation, "parked",
                    "The match is safely parked and ready to resume later.", cancellationToken);
            }
            else if (proposal.Kind == "end_match")
            {
                using (await control.PostRawAsync(
                    $"api/v1/matches/{proposal.MatchId}/complete", new { }, cancellationToken)) { }
                await CompleteAsync(database, proposal, operation, "completed",
                    "The match ended after its final stable checkpoint.", cancellationToken);
            }
        }
        catch (ControlPlaneException exception) when (IsSafeBoundaryWait(exception))
        {
            operation.Status = "queued";
            operation.Phase = "waiting_for_safe_boundary";
            operation.Summary = "Waiting for the current native interaction and all peers to settle before saving.";
            operation.UpdatedAt = DateTimeOffset.UtcNow;
            await database.SaveChangesAsync(CancellationToken.None);
            await NotifyAsync(proposal.MatchId, CancellationToken.None);
        }
        catch (Exception exception)
        {
            operation.Status = "failed"; operation.Phase = "failed";
            operation.Summary = $"Maintenance stopped safely: {exception.Message}"[..Math.Min(
                2000, $"Maintenance stopped safely: {exception.Message}".Length)];
            operation.CanCancel = false; operation.CompletedAt = DateTimeOffset.UtcNow;
            operation.UpdatedAt = DateTimeOffset.UtcNow;
            proposal.Status = "failed";
            var match = await database.PortalMatches.SingleAsync(
                item => item.MatchId == proposal.MatchId, CancellationToken.None);
            match.Status = "error"; match.LastError = operation.Summary;
            match.UpdatedAt = DateTimeOffset.UtcNow;
            await database.SaveChangesAsync(CancellationToken.None);
            await NotifyAsync(proposal.MatchId, CancellationToken.None);
        }
    }

    private async Task<bool> ProcessCapabilityRecoveryAsync(
        ApplicationDbContext database, ControlPlaneClient control,
        CancellationToken stoppingToken)
    {
        var operation = await database.PortalMaintenanceOperations
            .Where(item => item.Kind == "capability_recovery" &&
                (item.Status == "queued" || item.Status == "running"))
            .OrderBy(item => item.UpdatedAt)
            .FirstOrDefaultAsync(stoppingToken);
        if (operation is null) return false;

        var match = await database.PortalMatches.SingleOrDefaultAsync(
            item => item.MatchId == operation.MatchId, stoppingToken);
        if (match is null)
        {
            operation.Status = "failed";
            operation.Phase = "failed";
            operation.Summary = "Recovery stopped because the campaign no longer exists.";
            operation.CompletedAt = DateTimeOffset.UtcNow;
            operation.UpdatedAt = DateTimeOffset.UtcNow;
            await database.SaveChangesAsync(stoppingToken);
            return true;
        }

        try
        {
            using var payload = JsonDocument.Parse(operation.PayloadJson);
            var incidentId = payload.RootElement.GetProperty("incidentId").GetString();
            if (string.IsNullOrWhiteSpace(incidentId))
                throw new InvalidOperationException("The queued recovery has no incident identifier.");

            operation.Status = "running";
            match.Status = "recovering";
            match.LastError = null;
            match.UpdatedAt = DateTimeOffset.UtcNow;
            await StepAsync(database, operation, "stopping_autonomous_player",
                "Stopping the paused autonomous player before rebuilding its native runtime.",
                1, stoppingToken);
            await StopHarnessRunsAsync(control, operation.MatchId, stoppingToken);

            await StepAsync(database, operation, "rebuilding_current_runtime",
                "Rebuilding the game worker, semantic bridge, and MCP from the current images, then restoring the verified checkpoint.",
                2, stoppingToken);
            using (await control.PostRawAsync(
                $"api/v1/matches/{operation.MatchId}/retry-after-update",
                new { incident_id = incidentId }, stoppingToken)) { }

            await StepAsync(database, operation, "native_checkpoint_restored",
                "The current native runtime is healthy and the verified checkpoint is restored.",
                4, stoppingToken);
            operation.Status = "completed";
            operation.Phase = "complete";
            operation.Summary = "Recovery completed. Autonomous play is reconnecting now.";
            operation.CompletedSteps = operation.TotalSteps;
            operation.CanCancel = false;
            operation.CompletedAt = DateTimeOffset.UtcNow;
            operation.UpdatedAt = DateTimeOffset.UtcNow;
            match.Status = "running";
            match.LastError = null;
            match.UpdatedAt = DateTimeOffset.UtcNow;
            database.PortalMatchEvents.Add(new PortalMatchEvent
            {
                MatchId = operation.MatchId,
                EventType = "incident_retry",
                Summary = "The capability-stopped campaign resumed from its verified checkpoint using the current managed runtime.",
                DetailsJson = JsonSerializer.Serialize(new
                {
                    operation.OperationId,
                    incidentId,
                }),
            });
            await database.SaveChangesAsync(stoppingToken);
            await NotifyAsync(operation.MatchId, stoppingToken);
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
            // Leave the row running. On process restart the same idempotent
            // control operation is resumed or reconciled against native truth.
            throw;
        }
        catch (Exception exception)
        {
            // The Python request may have finished after an HTTP disconnect.
            // Reconcile native truth before ever presenting a failed recovery.
            try
            {
                var native = await control.GetMatchAsync(operation.MatchId, CancellationToken.None);
                var incident = await control.GetActiveCapabilityIncidentAsync(
                    operation.MatchId, CancellationToken.None);
                if (native.Match.Status == "running" && incident is null)
                {
                    operation.Status = "completed";
                    operation.Phase = "complete";
                    operation.Summary = "Recovery completed and was reconciled after the portal connection changed.";
                    operation.CompletedSteps = operation.TotalSteps;
                    operation.CompletedAt = DateTimeOffset.UtcNow;
                    operation.UpdatedAt = DateTimeOffset.UtcNow;
                    match.Status = "running";
                    match.LastError = null;
                    match.UpdatedAt = DateTimeOffset.UtcNow;
                    await database.SaveChangesAsync(CancellationToken.None);
                    await NotifyAsync(operation.MatchId, CancellationToken.None);
                    return true;
                }
            }
            catch (Exception reconciliationException)
            {
                logger.LogDebug(reconciliationException,
                    "Native recovery reconciliation deferred for {MatchId}", operation.MatchId);
            }

            if (IsTransientLifecycleConflict(exception))
            {
                operation.Status = "queued";
                operation.Phase = "waiting_for_runtime_cleanup";
                operation.Summary = "A previous container cleanup is still finishing. Recovery will retry automatically.";
                operation.UpdatedAt = DateTimeOffset.UtcNow;
                match.Status = "recovering";
                match.UpdatedAt = DateTimeOffset.UtcNow;
            }
            else
            {
                var detail = $"Recovery stopped safely: {exception.Message}";
                operation.Status = "failed";
                operation.Phase = "operator_review";
                operation.Summary = detail[..Math.Min(detail.Length, 2000)];
                operation.CompletedAt = DateTimeOffset.UtcNow;
                operation.UpdatedAt = DateTimeOffset.UtcNow;
                operation.CanCancel = false;
                match.Status = "error";
                match.LastError = operation.Summary;
                match.UpdatedAt = DateTimeOffset.UtcNow;
                database.PortalMatchEvents.Add(new PortalMatchEvent
                {
                    MatchId = operation.MatchId,
                    EventType = "incident_retry_failed",
                    Summary = operation.Summary,
                });
            }
            await database.SaveChangesAsync(CancellationToken.None);
            await NotifyAsync(operation.MatchId, CancellationToken.None);
        }
        return true;
    }

    internal static bool IsTransientLifecycleConflict(Exception exception)
    {
        var code = exception is ControlPlaneException controlException
            ? controlException.Code : string.Empty;
        var text = $"{code} {exception.Message}";
        return text.Contains("docker_http_409", StringComparison.OrdinalIgnoreCase) ||
            text.Contains("already in progress", StringComparison.OrdinalIgnoreCase) ||
            text.Contains("removal of container", StringComparison.OrdinalIgnoreCase);
    }

    private static int SeatIndex(string payloadJson)
    {
        using var payload = JsonDocument.Parse(payloadJson);
        return payload.RootElement.GetProperty("seatIndex").GetInt32();
    }

    private static bool IsSafeBoundaryWait(ControlPlaneException exception) =>
        SafeBoundaryCode(exception.Code) || SafeBoundaryCode(exception.Message);

    private static bool SafeBoundaryCode(string value) =>
        value.Contains("checkpoint_waiting_for_quiescence", StringComparison.OrdinalIgnoreCase) ||
        value.Contains("checkpoint_waiting_for_human_interaction", StringComparison.OrdinalIgnoreCase) ||
        value.Contains("checkpoint_peers_not_synchronized", StringComparison.OrdinalIgnoreCase) ||
        value.Contains("checkpoint_state_changed_during_quiescence", StringComparison.OrdinalIgnoreCase) ||
        value.Contains("native_checkpoint_not_currently_legal", StringComparison.OrdinalIgnoreCase) ||
        value.Contains("game_worker_bridge_unavailable", StringComparison.OrdinalIgnoreCase);

    private async Task CompleteAsync(
        ApplicationDbContext database, PortalGovernanceProposal proposal,
        PortalMaintenanceOperation operation, string matchStatus, string summary,
        CancellationToken cancellationToken)
    {
        operation.Status = "completed"; operation.Phase = "complete";
        operation.Summary = summary; operation.CompletedSteps = operation.TotalSteps;
        operation.CanCancel = false; operation.CompletedAt = DateTimeOffset.UtcNow;
        operation.UpdatedAt = DateTimeOffset.UtcNow; proposal.Status = "executed";
        var match = await database.PortalMatches.SingleAsync(
            item => item.MatchId == proposal.MatchId, cancellationToken);
        match.Status = matchStatus; match.LastError = null; match.UpdatedAt = DateTimeOffset.UtcNow;
        if (matchStatus is "parked" or "completed")
        {
            var managedSeats = await database.PortalLobbySeats
                .Where(item => item.MatchId == proposal.MatchId && item.ControlInstanceId != null)
                .ToArrayAsync(cancellationToken);
            foreach (var seat in managedSeats)
            {
                seat.ConnectionState = matchStatus == "completed" ? "retired" : "worker_stopped";
                seat.UpdatedAt = DateTimeOffset.UtcNow;
            }
        }
        if (matchStatus == "completed") match.IsListed = false;
        database.PortalMatchEvents.Add(new PortalMatchEvent
        {
            MatchId = proposal.MatchId, EventType = "maintenance_completed", Summary = summary,
            DetailsJson = JsonSerializer.Serialize(new { operation.OperationId, operation.Kind }),
        });
        await database.SaveChangesAsync(cancellationToken);
        await NotifyAsync(proposal.MatchId, cancellationToken);
    }

    private async Task StepAsync(
        ApplicationDbContext database, PortalMaintenanceOperation operation,
        string phase, string summary, int completed, CancellationToken cancellationToken)
    {
        operation.Phase = phase; operation.Summary = summary;
        operation.CompletedSteps = completed; operation.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(cancellationToken);
        await NotifyAsync(operation.MatchId, cancellationToken);
    }

    private static async Task StopHarnessRunsAsync(
        ControlPlaneClient control, string matchId, CancellationToken cancellationToken)
    {
        using var document = await control.GetRawAsync("api/v1/harness-runs", cancellationToken);
        var ids = document.RootElement.GetProperty("harness_runs").EnumerateArray()
            .Where(item => item.GetProperty("match_id").GetString() == matchId &&
                item.GetProperty("status").GetString() is "queued" or "starting" or
                    "running" or "restarting")
            .Select(item => item.GetProperty("run_id").GetString()).Where(item => item is not null)
            .ToArray();
        foreach (var id in ids)
            using (await control.PostRawAsync(
                $"api/v1/harness-runs/{Uri.EscapeDataString(id!)}/stop", new { },
                cancellationToken)) { }
    }

    private Task NotifyAsync(string matchId, CancellationToken cancellationToken) =>
        hub.Clients.Group(LobbyHub.GroupName(matchId)).SendAsync(
            "LobbyChanged", matchId, cancellationToken);
}
