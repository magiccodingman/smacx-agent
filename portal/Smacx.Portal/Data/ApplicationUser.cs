using Microsoft.AspNetCore.Identity;

namespace Smacx.Portal.Data;

public class ApplicationUser : IdentityUser
{
    public string DisplayName { get; set; } = string.Empty;

    public string GameHandle { get; set; } = string.Empty;

    public string NormalizedGameHandle { get; set; } = string.Empty;

    public bool IsProvisional { get; set; }

    public bool MustResetPassword { get; set; }

    public DateTimeOffset CreatedAt { get; set; } = DateTimeOffset.UtcNow;

    public DateTimeOffset UpdatedAt { get; set; } = DateTimeOffset.UtcNow;
}
