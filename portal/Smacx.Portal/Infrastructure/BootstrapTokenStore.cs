using System.Security.Cryptography;
using Microsoft.AspNetCore.Identity;
using Microsoft.Extensions.Options;
using Smacx.Portal.Data;

namespace Smacx.Portal.Infrastructure;

public sealed class BootstrapTokenStore(
    IOptions<PortalStorageOptions> options,
    UserManager<ApplicationUser> userManager,
    ILogger<BootstrapTokenStore> logger)
{
    private readonly SemaphoreSlim gate = new(1, 1);
    private readonly string tokenPath = Path.Combine(
        Path.GetFullPath(options.Value.DataRoot), "secrets", "bootstrap-token");

    public string BootstrapCommand => "docker compose exec -T control-center dotnet Smacx.Portal.dll bootstrap-token";

    public async Task<bool> IsSetupRequiredAsync()
    {
        var administrators = await userManager.GetUsersInRoleAsync(PortalRoles.Administrator);
        return administrators.Count == 0;
    }

    public async Task EnsureAsync(CancellationToken cancellationToken = default)
    {
        await gate.WaitAsync(cancellationToken);
        try
        {
            if (!await IsSetupRequiredAsync() || File.Exists(tokenPath))
            {
                return;
            }

            Directory.CreateDirectory(Path.GetDirectoryName(tokenPath)!);
            var token = Convert.ToBase64String(RandomNumberGenerator.GetBytes(36))
                .Replace('+', '-')
                .Replace('/', '_')
                .TrimEnd('=');
            await File.WriteAllTextAsync(tokenPath, token, cancellationToken);
            if (!OperatingSystem.IsWindows())
            {
                File.SetUnixFileMode(tokenPath, UnixFileMode.UserRead | UnixFileMode.UserWrite);
            }
            logger.LogWarning(
                "Portal setup is required. Read the one-time token with {BootstrapCommand}",
                BootstrapCommand);
        }
        finally
        {
            gate.Release();
        }
    }

    public async Task<bool> ValidateAsync(string candidate, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(candidate) || candidate.Length > 256)
        {
            return false;
        }

        await EnsureAsync(cancellationToken);
        if (!File.Exists(tokenPath))
        {
            return false;
        }

        var expected = (await File.ReadAllTextAsync(tokenPath, cancellationToken)).Trim();
        return CryptographicOperations.FixedTimeEquals(
            System.Text.Encoding.UTF8.GetBytes(expected),
            System.Text.Encoding.UTF8.GetBytes(candidate));
    }

    public void Revoke()
    {
        if (File.Exists(tokenPath))
        {
            File.Delete(tokenPath);
        }
    }

    public string RevealForCli()
    {
        if (!File.Exists(tokenPath))
        {
            throw new InvalidOperationException("Portal bootstrap token is unavailable or setup is complete.");
        }
        return File.ReadAllText(tokenPath).Trim();
    }
}
