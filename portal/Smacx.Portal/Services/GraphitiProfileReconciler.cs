using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;

namespace Smacx.Portal.Services;

/// <summary>
/// Keeps Graphiti's failure-isolated control snapshot aligned with the editable
/// portal profile. Immediate save synchronization is the fast path; this loop
/// heals a transient control-service outage without operator intervention.
/// </summary>
public sealed class GraphitiProfileReconciler(
    IServiceScopeFactory scopes,
    ILogger<GraphitiProfileReconciler> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(30));
        do
        {
            try { await ReconcileAsync(stoppingToken); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception exception)
            {
                logger.LogWarning(exception, "Graphiti profile reconciliation failed");
            }
        } while (await timer.WaitForNextTickAsync(stoppingToken));
    }

    internal async Task ReconcileOnceAsync(CancellationToken cancellationToken = default)
    {
        await using var scope = scopes.CreateAsyncScope();
        var control = scope.ServiceProvider.GetRequiredService<ControlPlaneClient>();
        var database = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
        using var status = await control.GetRawAsync("api/v1/graphiti", cancellationToken);
        if (!status.RootElement.TryGetProperty("profile", out var selected) ||
            selected.ValueKind != JsonValueKind.Object ||
            !selected.TryGetProperty("profile_id", out var selectedId) ||
            string.IsNullOrWhiteSpace(selectedId.GetString())) return;

        var profileId = selectedId.GetString()!;
        var item = await database.PortalAiProfiles.AsNoTracking()
            .SingleOrDefaultAsync(profile => profile.ProfileId == profileId, cancellationToken);
        if (item is null || !item.Active)
        {
            // Reconciliation is deliberately healing-only. A replacement or
            // diagnostic portal process may temporarily observe an empty,
            // stale, or unavailable portal database while the durable control
            // snapshot remains valid. Treating that absence as an operator
            // deletion used to erase the selected profile and disable
            // Graphiti during otherwise ordinary container recreation.
            // Explicit profile deactivation and the administrator toggle own
            // the destructive clear operation.
            logger.LogDebug(
                "Selected Graphiti profile {ProfileId} is not active in this portal store; preserving the durable control snapshot",
                profileId);
            return;
        }
        using var synced = await control.PostRawAsync(
            "api/v1/graphiti/sync-profile", new
            {
                profile = new
                {
                    profile_id = item.ProfileId,
                    display_name = item.DisplayName,
                    provider_id = item.ProviderId,
                    model_id = item.ModelId,
                    reasoning_effort = item.ReasoningEffort,
                    generation_settings = JsonSerializer.Deserialize<JsonElement>(item.GenerationSettingsJson),
                },
            }, cancellationToken);
    }

    private Task ReconcileAsync(CancellationToken cancellationToken) =>
        ReconcileOnceAsync(cancellationToken);
}
