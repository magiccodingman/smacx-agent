using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;

namespace Smacx.Portal.Services;

public static class RecoveredMatchReconciliation
{
    public static async Task<bool> ApplyAsync(ApplicationDbContext database,
        PortalMatchProfile match, ControlMatch observed, CancellationToken cancellationToken)
    {
        // Recovery briefly parks the control match. A portal that observed that
        // intermediate state must follow a subsequently verified recovery,
        // without undoing a later explicit park or a completed campaign.
        var portalUpdatedUnix = (match.UpdatedAt - DateTimeOffset.UnixEpoch).TotalSeconds;
        if (match.Status != "parked" || observed.Status != "running" ||
            !observed.HasVerifiedRecoveryCheckpoint ||
            !double.IsFinite(observed.RuntimeGeneration) ||
            observed.RuntimeGeneration <= portalUpdatedUnix)
            return false;

        // Compare-and-update the persisted status, not just the entity loaded
        // before the control request. A concurrent owner park must win.
        await using var transaction = await database.Database.BeginTransactionAsync(cancellationToken);
        var changed = await database.PortalMatches.Where(item =>
                item.MatchId == match.MatchId && item.Status == "parked" &&
                item.UpdatedAt == match.UpdatedAt &&
                !database.PortalMaintenanceOperations.Any(operation =>
                    operation.MatchId == item.MatchId &&
                    (operation.Status == "queued" || operation.Status == "running")))
            .ExecuteUpdateAsync(update => update
                .SetProperty(item => item.Status, "running")
                .SetProperty(item => item.LastError, (string?)null)
                .SetProperty(item => item.CurrentTurn, observed.LastTurn)
                .SetProperty(item => item.CurrentYear, observed.LastYear)
                .SetProperty(item => item.UpdatedAt, DateTimeOffset.UtcNow), cancellationToken);
        if (changed == 0) return false;
        database.PortalMatchEvents.Add(new PortalMatchEvent
        {
            MatchId = match.MatchId,
            EventType = "recovery_reconciled",
            Summary = "A newer verified control-plane recovery restored the live campaign and observation feed.",
        });
        await database.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        await database.Entry(match).ReloadAsync(cancellationToken);
        return true;
    }
}
