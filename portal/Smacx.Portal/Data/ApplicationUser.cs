using Microsoft.AspNetCore.Identity;

namespace Smacx.Portal.Data;

public class ApplicationUser : IdentityUser
{
    public string DisplayName { get; set; } = string.Empty;

    public string NormalizedDisplayName { get; set; } = string.Empty;

    public string GameHandle { get; set; } = string.Empty;

    public string NormalizedGameHandle { get; set; } = string.Empty;

    public bool IsProvisional { get; set; }

    public bool MustResetPassword { get; set; }

    public bool IsActive { get; set; } = true;

    public bool IsPrimaryAdministrator { get; set; }

    public DateTimeOffset? InstallationVerifiedAt { get; set; }

    public string? InstallationVerificationSource { get; set; }

    public string? InstallationFingerprintId { get; set; }

    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;

    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
}
