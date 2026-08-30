using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;

namespace Smacx.Portal.Infrastructure;

public sealed class PortalDatabaseInitializer(
    IServiceScopeFactory scopeFactory,
    ILogger<PortalDatabaseInitializer> logger)
{
    public async Task InitializeAsync(CancellationToken cancellationToken = default)
    {
        await using var scope = scopeFactory.CreateAsyncScope();
        var database = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
        await database.Database.EnsureCreatedAsync(cancellationToken);

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

        logger.LogInformation("Portal canonical schema is ready");
    }
}
