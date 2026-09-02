using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Infrastructure;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Route("api/access")]
public sealed class AccessController(
    ApplicationDbContext database,
    UserManager<ApplicationUser> userManager,
    PortalAccessPolicy access,
    RequestNetworkClassifier networks,
    PortalSecurityTicketService tickets) : ControllerBase
{
    [HttpGet("context")]
    [AllowAnonymous]
    public async Task<ActionResult<ApiResponse<AccessContext>>> Context()
    {
        var zone = access.Zone(HttpContext);
        var user = User.Identity?.IsAuthenticated == true
            ? await userManager.GetUserAsync(User)
            : null;
        var registrationEnabled = await database.PortalSettings.AsNoTracking()
            .Where(item => item.Key == "registration.enabled")
            .Select(item => item.Value)
            .SingleOrDefaultAsync(HttpContext.RequestAborted) is not string setting ||
            !setting.Equals("false", StringComparison.OrdinalIgnoreCase);
        var verificationRequired = await access.InstallationVerificationRequiredAsync(
            HttpContext, HttpContext.RequestAborted);
        return ApiResponse<AccessContext>.Success(new(
            zone == PortalRequestZone.Trusted ? "trusted" : "remote",
            zone == PortalRequestZone.Trusted,
            registrationEnabled,
            await access.RegistrationRequiresInvitationAsync(
                HttpContext, HttpContext.RequestAborted),
            verificationRequired,
            user?.InstallationVerifiedAt is not null,
            access.PrimaryAdministratorRemoteLoginAllowed));
    }

    [HttpGet("settings")]
    [Authorize(Roles = PortalRoles.Administrator)]
    public async Task<ActionResult<ApiResponse<NetworkAccessSettings>>> Settings()
    {
        return ApiResponse<NetworkAccessSettings>.Success(new(
            networks.TrustedNetworks,
            await access.SettingAsync("access.trusted.require_invitation", false, HttpContext.RequestAborted),
            await access.SettingAsync("access.trusted.require_installation_verification", false, HttpContext.RequestAborted),
            access.PrimaryAdministratorRemoteLoginAllowed,
            Environment.GetEnvironmentVariable("SMACX_PUBLIC_HOSTNAME") ?? string.Empty,
            Environment.GetEnvironmentVariable("SMACX_DDNS_PROVIDER") ?? "off",
            !string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("SMACX_PUBLIC_HOSTNAME")) &&
            (Environment.GetEnvironmentVariable("SMACX_DDNS_PROVIDER") ?? "off") != "off"));
    }

    [HttpPost("settings")]
    [Authorize(Roles = PortalRoles.Administrator)]
    public async Task<ActionResult<ApiResponse<NetworkAccessSettings>>> UpdateSettings(
        UpdateNetworkAccessSettingsRequest request)
    {
        await SetSettingAsync("access.trusted.require_invitation", request.TrustedRegistrationRequiresInvite);
        await SetSettingAsync("access.trusted.require_installation_verification", request.TrustedInstallationVerificationRequired);
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        return await Settings();
    }

    [HttpPost("invitations/redeem")]
    [AllowAnonymous]
    [EnableRateLimiting("invitation")]
    public async Task<ActionResult<ApiResponse<InvitationGrantResult>>> Redeem(
        InvitationGrantRequest request)
    {
        if (access.Zone(HttpContext) == PortalRequestZone.Remote && !Request.IsHttps)
            return StatusCode(StatusCodes.Status426UpgradeRequired,
                ApiResponse<InvitationGrantResult>.Failure("remote_https_required",
                    "Remote invitations require HTTPS. Use this server's secure public address."));
        var secret = request.Invitation.Trim();
        if (secret.Length is < 24 or > 256)
            return Unauthorized(ApiResponse<InvitationGrantResult>.Failure(
                "invalid_registration_invitation", "The invitation is invalid or expired."));
        var tokenHash = Hash(secret);
        var invitation = await database.RegistrationInvitations.SingleOrDefaultAsync(
            item => item.TokenHash == tokenHash, HttpContext.RequestAborted);
        if (invitation is null || invitation.ExpiresAt <= DateTimeOffset.UtcNow ||
            invitation.UsedAt is not null || invitation.RevokedAt is not null)
            return Unauthorized(ApiResponse<InvitationGrantResult>.Failure(
                "invalid_registration_invitation", "The invitation is invalid or expired."));
        tickets.SetRegistrationGrant(HttpContext, invitation.Id, invitation.ExpiresAt);
        return ApiResponse<InvitationGrantResult>.Success(new(
            invitation.ExpiresAt, "/register"));
    }

    [HttpGet("invitations")]
    [Authorize(Roles = PortalRoles.Administrator)]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<AdminInvitationSummary>>>> Invitations()
    {
        var now = DateTimeOffset.UtcNow;
        var invitations = await database.RegistrationInvitations.AsNoTracking()
            .OrderByDescending(item => item.CreatedAt)
            .Take(200)
            .ToArrayAsync(HttpContext.RequestAborted);
        var usedIds = invitations.Where(item => item.UsedByUserId is not null)
            .Select(item => item.UsedByUserId!).Distinct().ToArray();
        var users = await database.Users.AsNoTracking().Where(item => usedIds.Contains(item.Id))
            .ToDictionaryAsync(item => item.Id, item => item.DisplayName, HttpContext.RequestAborted);
        return ApiResponse<IReadOnlyList<AdminInvitationSummary>>.Success(invitations.Select(item =>
            new AdminInvitationSummary(
                item.Id,
                item.Label,
                item.RevokedAt is not null ? "revoked" : item.UsedAt is not null ? "used" :
                    item.ExpiresAt <= now ? "expired" : "active",
                item.CreatedAt,
                item.ExpiresAt,
                item.UsedAt,
                item.UsedByUserId is not null ? users.GetValueOrDefault(item.UsedByUserId) : null))
            .ToArray());
    }

    [HttpPost("invitations")]
    [Authorize(Roles = PortalRoles.Administrator)]
    public async Task<ActionResult<ApiResponse<CreatedInvitation>>> Create(
        CreateInvitationRequest request)
    {
        var label = string.IsNullOrWhiteSpace(request.Label) ? null : request.Label.Trim();
        if (label?.Length > 120)
            return BadRequest(ApiResponse<CreatedInvitation>.Failure(
                "invalid_invitation_label", "Invitation labels may contain at most 120 characters."));
        var secret = Convert.ToBase64String(RandomNumberGenerator.GetBytes(32))
            .Replace('+', '-').Replace('/', '_').TrimEnd('=');
        var invitation = new RegistrationInvitation
        {
            TokenHash = Hash(secret),
            CreatedByUserId = userManager.GetUserId(User)!,
            Label = label,
            ExpiresAt = DateTimeOffset.UtcNow.AddHours(24),
        };
        database.RegistrationInvitations.Add(invitation);
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        return ApiResponse<CreatedInvitation>.Success(new(
            invitation.Id,
            secret,
            invitation.ExpiresAt,
            $"/join#invite={Uri.EscapeDataString(secret)}"));
    }

    [HttpPost("invitations/{invitationId}/revoke")]
    [Authorize(Roles = PortalRoles.Administrator)]
    public async Task<ActionResult<ApiResponse<bool>>> Revoke(string invitationId)
    {
        var invitation = await database.RegistrationInvitations.FindAsync(
            [invitationId], HttpContext.RequestAborted);
        if (invitation is null) return NotFound(ApiResponse<bool>.Failure(
            "invitation_not_found", "The invitation was not found."));
        if (invitation.UsedAt is null) invitation.RevokedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        return ApiResponse<bool>.Success(true);
    }

    private static string Hash(string value) => Convert.ToHexStringLower(
        SHA256.HashData(Encoding.UTF8.GetBytes(value)));

    private async Task SetSettingAsync(string key, bool value)
    {
        var setting = await database.PortalSettings.SingleOrDefaultAsync(
            item => item.Key == key, HttpContext.RequestAborted);
        if (setting is null)
        {
            database.PortalSettings.Add(new PortalSetting { Key = key, Value = value ? "true" : "false" });
        }
        else
        {
            setting.Value = value ? "true" : "false";
            setting.UpdatedAt = DateTimeOffset.UtcNow;
        }
    }
}
