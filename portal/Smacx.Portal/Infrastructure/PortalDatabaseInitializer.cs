using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;

namespace Smacx.Portal.Infrastructure;

public sealed class PortalDatabaseInitializer(
    IServiceScopeFactory scopeFactory,
    ILogger<PortalDatabaseInitializer> logger)
{
    private const string CanonicalSchemaId = "smacx.portal.canonical.2026-08-stable-ai-profiles";

    public async Task InitializeAsync(CancellationToken cancellationToken = default)
    {
        await using var scope = scopeFactory.CreateAsyncScope();
        var database = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
        await database.Database.EnsureCreatedAsync(cancellationToken);

        var requiredTables = new HashSet<string>(StringComparer.Ordinal)
        {
            "PortalMatches", "PortalLobbySeats", "PortalLobbyMessages",
            "PortalChatGroups", "PortalChatGroupMembers", "PortalChatDeliveries",
            "PortalGovernanceProposals", "PortalGovernanceVotes",
            "PortalMaintenanceOperations", "PortalStableCheckpoints",
            "PortalAiProfiles", "RegistrationInvitations", "PortalMatchParticipants",
        };
        var connection = database.Database.GetDbConnection();
        await connection.OpenAsync(cancellationToken);
        await using (var command = connection.CreateCommand())
        {
            command.CommandText = "SELECT name FROM sqlite_master WHERE type='table'";
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken))
                requiredTables.Remove(reader.GetString(0));
        }
        if (requiredTables.Count > 0)
            throw new InvalidOperationException(
                "The unreleased portal database does not match the canonical schema. " +
                "Back up any development data, remove portal.sqlite3, and restart. " +
                $"Missing: {string.Join(", ", requiredTables.Order())}.");

        var requiredSeatColumns = new HashSet<string>(StringComparer.Ordinal)
        {
            "ConnectionState", "LastExitKind", "LastWorkerSeenAt", "IsManagedHost",
            "TemporaryControllerKind", "DelegationStatus", "LastBrowserSeenAt",
            "RequestedFactionId", "ResolvedFactionKey", "LeaderName",
            "RequestedPersonalityId", "PersonalityName", "PersonalityPrompt",
            "PersonalityPromptSha256", "AiProfileId",
        };
        await using (var command = connection.CreateCommand())
        {
            command.CommandText = "PRAGMA table_info('PortalLobbySeats')";
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken))
                requiredSeatColumns.Remove(reader.GetString(1));
        }
        if (requiredSeatColumns.Count > 0)
            throw new InvalidOperationException(
                "The unreleased portal database predates the canonical managed-play columns. " +
                "Back up any development data, remove portal.sqlite3, and restart. " +
                $"Missing: {string.Join(", ", requiredSeatColumns.Order())}.");

        var requiredUserColumns = new HashSet<string>(StringComparer.Ordinal)
        {
            "NormalizedDisplayName", "IsActive", "IsPrimaryAdministrator",
            "InstallationVerifiedAt", "InstallationVerificationSource",
            "InstallationFingerprintId",
        };
        await using (var command = connection.CreateCommand())
        {
            command.CommandText = "PRAGMA table_info('AspNetUsers')";
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken))
                requiredUserColumns.Remove(reader.GetString(1));
        }
        if (requiredUserColumns.Count > 0)
            throw new InvalidOperationException(
                "The unreleased portal database predates public display-name identity. " +
                "Back up any development data, remove portal.sqlite3, and restart.");

        var requiredProfileColumns = new HashSet<string>(StringComparer.Ordinal)
        {
            "GenerationSettingsJson", "NormalizedDisplayName", "UpdatedAt",
        };
        await using (var command = connection.CreateCommand())
        {
            command.CommandText = "PRAGMA table_info('PortalAiProfiles')";
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken))
                requiredProfileColumns.Remove(reader.GetString(1));
        }
        if (requiredProfileColumns.Count > 0)
            throw new InvalidOperationException(
                "The unreleased portal database predates stable editable AI profiles. " +
                "Back up any development data, remove portal.sqlite3, and restart.");

        var roleManager = scope.ServiceProvider.GetRequiredService<RoleManager<IdentityRole>>();
        foreach (var role in new[] { PortalRoles.Administrator, PortalRoles.Member })
        {
            if (!await roleManager.RoleExistsAsync(role))
            {
                var result = await roleManager.CreateAsync(new IdentityRole(role));
                if (!result.Succeeded)
                {
                    throw new InvalidOperationException(
                        $"Unable to initialize portal role {role}: " +
                        string.Join(", ", result.Errors.Select(error => error.Code)));
                }
            }
        }

        var userManager = scope.ServiceProvider.GetRequiredService<UserManager<ApplicationUser>>();
        var primary = await database.Users.SingleOrDefaultAsync(
            user => user.IsPrimaryAdministrator, cancellationToken);
        if (primary is null)
        {
            var bootstrapAdministrator = await userManager.FindByNameAsync("admin");
            if (bootstrapAdministrator is not null &&
                await userManager.IsInRoleAsync(bootstrapAdministrator, PortalRoles.Administrator))
            {
                bootstrapAdministrator.IsPrimaryAdministrator = true;
                bootstrapAdministrator.IsActive = true;
                bootstrapAdministrator.InstallationVerifiedAt ??= DateTimeOffset.UtcNow;
                bootstrapAdministrator.InstallationVerificationSource ??= "server_game_source";
                bootstrapAdministrator.InstallationFingerprintId ??= "server-game-source";
                bootstrapAdministrator.UpdatedAt = DateTimeOffset.UtcNow;
                await database.SaveChangesAsync(cancellationToken);
            }
        }

        var registration = await database.PortalSettings.FindAsync(["registration.enabled"], cancellationToken);
        if (registration is null)
        {
            database.PortalSettings.Add(new PortalSetting
            {
                Key = "registration.enabled",
                Value = "true",
            });
            await database.SaveChangesAsync(cancellationToken);
        }

        var schema = await database.PortalSettings.FindAsync(
            ["schema.identity"], cancellationToken);
        if (schema is null)
        {
            database.PortalSettings.Add(new PortalSetting
            {
                Key = "schema.identity", Value = CanonicalSchemaId,
            });
            await database.SaveChangesAsync(cancellationToken);
        }
        else if (!string.Equals(schema.Value, CanonicalSchemaId, StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                "The unreleased portal database has a different canonical schema identity. " +
                "Back up any development data, remove portal.sqlite3, and restart.");
        }

        logger.LogInformation("Portal canonical schema is ready");
    }
}
