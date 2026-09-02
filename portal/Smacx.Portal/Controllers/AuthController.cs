using System.ComponentModel.DataAnnotations;
using System.Text.RegularExpressions;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Infrastructure;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Route("api/auth")]
public sealed partial class AuthController(
    UserManager<ApplicationUser> userManager,
    SignInManager<ApplicationUser> signInManager,
    ApplicationDbContext database,
    BootstrapTokenStore bootstrapTokens,
    PortalAccessPolicy accessPolicy,
    PortalSecurityTicketService securityTickets,
    IAntiforgery antiforgery,
    IOptions<IdentityOptions> identityOptions) : ControllerBase
{
    private static readonly SemaphoreSlim BootstrapGate = new(1, 1);
    private static readonly SemaphoreSlim RegistrationGate = new(1, 1);

    [HttpGet("csrf")]
    [AllowAnonymous]
    public ActionResult<ApiResponse<CsrfTokenResponse>> Csrf()
    {
        var tokens = antiforgery.GetAndStoreTokens(HttpContext);
        return ApiResponse<CsrfTokenResponse>.Success(new(tokens.RequestToken!));
    }

    [HttpGet("setup")]
    [AllowAnonymous]
    public async Task<ActionResult<ApiResponse<PortalSetupState>>> Setup()
    {
        await bootstrapTokens.EnsureAsync(HttpContext.RequestAborted);
        var registrationEnabled = await IsRegistrationEnabledAsync();
        var requiresInvitation = await accessPolicy.RegistrationRequiresInvitationAsync(
            HttpContext, HttpContext.RequestAborted);
        return ApiResponse<PortalSetupState>.Success(new(
            await bootstrapTokens.IsSetupRequiredAsync(),
            registrationEnabled,
            "admin",
            bootstrapTokens.BootstrapCommand,
            identityOptions.Value.Password.RequiredLength,
            accessPolicy.Zone(HttpContext) == PortalRequestZone.Trusted ? "trusted" : "remote",
            requiresInvitation));
    }

    [HttpGet("session")]
    [AllowAnonymous]
    public async Task<ActionResult<ApiResponse<PortalSession>>> Session()
    {
        if (User.Identity?.IsAuthenticated != true)
        {
            return ApiResponse<PortalSession>.Success(new(false, null));
        }

        var user = await userManager.GetUserAsync(User);
        if (user is null)
        {
            return ApiResponse<PortalSession>.Success(new(false, null));
        }

        return ApiResponse<PortalSession>.Success(new(true, await ToPortalUserAsync(user)));
    }

    [HttpPost("bootstrap")]
    [AllowAnonymous]
    [EnableRateLimiting("authentication")]
    public async Task<ActionResult<ApiResponse<PortalSession>>> Bootstrap(BootstrapRequest request)
    {
        if (accessPolicy.Zone(HttpContext) == PortalRequestZone.Remote)
            return StatusCode(StatusCodes.Status403Forbidden,
                ApiResponse<PortalSession>.Failure(
                    "trusted_bootstrap_required",
                    "Initial administrator setup is available only from a trusted network."));
        if (request.Password != request.ConfirmPassword)
        {
            return BadRequest(ApiResponse<PortalSession>.Failure(
                "password_confirmation_mismatch", "The password confirmation does not match."));
        }

        await BootstrapGate.WaitAsync(HttpContext.RequestAborted);
        try
        {
            if (!await bootstrapTokens.IsSetupRequiredAsync())
            {
                return Conflict(ApiResponse<PortalSession>.Failure(
                    "setup_already_complete", "The administrator account has already been configured."));
            }
            if (!await bootstrapTokens.ValidateAsync(request.Token, HttpContext.RequestAborted))
            {
                return Unauthorized(ApiResponse<PortalSession>.Failure(
                    "invalid_bootstrap_token", "The one-time setup token is invalid."));
            }

            var admin = NewUser("admin", "Administrator");
            admin.IsPrimaryAdministrator = true;
            admin.InstallationVerifiedAt = DateTimeOffset.UtcNow;
            admin.InstallationVerificationSource = "server_game_source";
            admin.InstallationFingerprintId = "server-game-source";
            var result = await userManager.CreateAsync(admin, request.Password);
            if (!result.Succeeded)
            {
                return BadRequest(ApiResponse<PortalSession>.Failure(
                    "administrator_creation_failed", FormatIdentityErrors(result)));
            }

            var roleResult = await userManager.AddToRolesAsync(
                admin, [PortalRoles.Administrator, PortalRoles.Member]);
            if (!roleResult.Succeeded)
            {
                await userManager.DeleteAsync(admin);
                return BadRequest(ApiResponse<PortalSession>.Failure(
                    "administrator_role_failed", FormatIdentityErrors(roleResult)));
            }

            bootstrapTokens.Revoke();
            await signInManager.SignInAsync(admin, isPersistent: false);
            return ApiResponse<PortalSession>.Success(new(true, await ToPortalUserAsync(admin)));
        }
        finally
        {
            BootstrapGate.Release();
        }
    }

    [HttpPost("register")]
    [AllowAnonymous]
    [EnableRateLimiting("authentication")]
    public async Task<ActionResult<ApiResponse<RegistrationResult>>> Register(RegistrationRequest request)
    {
        if (accessPolicy.Zone(HttpContext) == PortalRequestZone.Remote && !Request.IsHttps)
            return StatusCode(StatusCodes.Status426UpgradeRequired,
                ApiResponse<RegistrationResult>.Failure("remote_https_required",
                    "Remote registration requires HTTPS. Configure a public hostname in the Internet hosting guide."));
        if (!await IsRegistrationEnabledAsync())
        {
            return StatusCode(StatusCodes.Status403Forbidden, ApiResponse<RegistrationResult>.Failure(
                "registration_disabled", "New account registration is disabled."));
        }
        await RegistrationGate.WaitAsync(HttpContext.RequestAborted);
        try
        {
            RegistrationInvitation? registrationInvitation = null;
            if (await accessPolicy.RegistrationRequiresInvitationAsync(
                    HttpContext, HttpContext.RequestAborted))
            {
                var grant = securityTickets.ReadRegistrationGrant(HttpContext);
                if (grant is null)
                    return StatusCode(StatusCodes.Status403Forbidden,
                        ApiResponse<RegistrationResult>.Failure(
                            "registration_invitation_required",
                            "A valid registration invitation is required."));
                registrationInvitation = await database.RegistrationInvitations.SingleOrDefaultAsync(
                    item => item.Id == grant.InvitationId, HttpContext.RequestAborted);
                if (registrationInvitation is null ||
                    registrationInvitation.ExpiresAt <= DateTimeOffset.UtcNow ||
                    registrationInvitation.UsedAt is not null ||
                    registrationInvitation.RevokedAt is not null)
                    return StatusCode(StatusCodes.Status403Forbidden,
                        ApiResponse<RegistrationResult>.Failure(
                            "registration_invitation_invalid",
                            "The registration invitation is invalid, expired, or already used."));
            }
            if (request.Password != request.ConfirmPassword)
            {
                return BadRequest(ApiResponse<RegistrationResult>.Failure(
                    "password_confirmation_mismatch", "The password confirmation does not match."));
            }
            if (!ValidUsername().IsMatch(request.Username))
            {
                return BadRequest(ApiResponse<RegistrationResult>.Failure(
                    "invalid_username",
                    "Use 3–31 letters, numbers, periods, underscores, or hyphens; begin with a letter or number."));
            }
            var user = await userManager.FindByNameAsync(request.Username);
            if (user is null)
            {
                var requestedDisplay = NormalizeDisplayName(request.DisplayName);
                user = await database.Users.SingleOrDefaultAsync(item =>
                    item.IsProvisional && item.NormalizedDisplayName == requestedDisplay,
                    HttpContext.RequestAborted);
            }
            if (user is { IsProvisional: true } &&
                user.NormalizedDisplayName != NormalizeDisplayName(request.DisplayName))
                return Conflict(ApiResponse<RegistrationResult>.Failure(
                    "invitation_display_name_mismatch",
                    "Claim this invited seat with its reserved public display name."));
            var displayValidation = await ValidateDisplayNameAsync(
                request.DisplayName, user is { IsProvisional: true } ? user.Id : null);
            if (displayValidation is not null)
            {
                return BadRequest(ApiResponse<RegistrationResult>.Failure(
                    displayValidation.Value.Code, displayValidation.Value.Message));
            }

            IdentityResult result;
            if (user is { IsProvisional: true })
            {
                var renamed = await userManager.SetUserNameAsync(user, request.Username);
                if (!renamed.Succeeded)
                    return Conflict(ApiResponse<RegistrationResult>.Failure(
                        "username_unavailable", FormatIdentityErrors(renamed)));
                user.DisplayName = request.DisplayName.Trim();
                user.NormalizedDisplayName = NormalizeDisplayName(request.DisplayName);
                user.GameHandle = request.DisplayName.Trim();
                user.NormalizedGameHandle = userManager.NormalizeName(request.DisplayName)!;
                user.IsProvisional = false;
                user.UpdatedAt = DateTimeOffset.UtcNow;
                result = await userManager.AddPasswordAsync(user, request.Password);
                if (result.Succeeded) result = await userManager.UpdateAsync(user);
            }
            else if (user is null)
            {
                user = NewUser(request.Username, request.DisplayName.Trim());
                result = await userManager.CreateAsync(user, request.Password);
            }
            else
            {
                return Conflict(ApiResponse<RegistrationResult>.Failure(
                    "username_unavailable", "That sign-in username already has an account."));
            }
            if (!result.Succeeded)
            {
                return BadRequest(ApiResponse<RegistrationResult>.Failure(
                    "registration_failed", FormatIdentityErrors(result)));
            }
            var roleResult = await userManager.IsInRoleAsync(user, PortalRoles.Member)
                ? IdentityResult.Success
                : await userManager.AddToRoleAsync(user, PortalRoles.Member);
            if (!roleResult.Succeeded)
            {
                return BadRequest(ApiResponse<RegistrationResult>.Failure(
                    "registration_role_failed", FormatIdentityErrors(roleResult)));
            }

            // A lobby invitation can arrive from the native game before this LAN
            // account exists. Claim every still-unclaimed seat with the same public
            // display name so reconnects and history attach to one durable identity.
            var normalizedHandle = user.NormalizedDisplayName;
            var invitedSeats = await database.PortalLobbySeats
                .Where(seat => seat.UserId == null && seat.ControllerKind == "human" &&
                    seat.PlayerHandle != null && seat.PlayerHandle.ToUpper() == normalizedHandle)
                .ToArrayAsync(HttpContext.RequestAborted);
            foreach (var seat in invitedSeats)
            {
                seat.UserId = user.Id;
                seat.Status = "ready";
                seat.UpdatedAt = DateTimeOffset.UtcNow;
                if (!await database.PortalMatchMembers.AnyAsync(
                        member => member.MatchId == seat.MatchId && member.UserId == user.Id,
                        HttpContext.RequestAborted))
                {
                    database.PortalMatchMembers.Add(new PortalMatchMember
                    {
                        MatchId = seat.MatchId,
                        UserId = user.Id,
                        SeatIndex = seat.SeatIndex,
                        Role = "player",
                        JoinMode = seat.JoinMode,
                    });
                }
            }
            if (invitedSeats.Length > 0)
            {
                await database.SaveChangesAsync(HttpContext.RequestAborted);
            }
            if (registrationInvitation is not null)
            {
                registrationInvitation.UsedAt = DateTimeOffset.UtcNow;
                registrationInvitation.UsedByUserId = user.Id;
                await database.SaveChangesAsync(HttpContext.RequestAborted);
                securityTickets.ClearRegistrationGrant(HttpContext);
            }
            return ApiResponse<RegistrationResult>.Success(new(
                user.UserName ?? request.Username,
                true,
                "Your account is ready. Sign in to continue."));
        }
        finally
        {
            RegistrationGate.Release();
        }
    }

    [HttpPost("login")]
    [AllowAnonymous]
    [EnableRateLimiting("authentication")]
    public async Task<ActionResult<ApiResponse<PortalSession>>> Login(LoginRequest request)
    {
        if (accessPolicy.Zone(HttpContext) == PortalRequestZone.Remote && !Request.IsHttps)
            return StatusCode(StatusCodes.Status426UpgradeRequired,
                ApiResponse<PortalSession>.Failure("remote_https_required",
                    "Remote sign-in requires HTTPS. Use this server's secure public address."));
        var user = await userManager.FindByNameAsync(request.Username);
        if (user is null || !user.IsActive)
            return Unauthorized(ApiResponse<PortalSession>.Failure(
                "invalid_credentials", "The username or password is incorrect."));
        if (await userManager.IsLockedOutAsync(user))
            return Unauthorized(ApiResponse<PortalSession>.Failure(
                "account_locked", "This account is temporarily locked."));
        if (!await userManager.CheckPasswordAsync(user, request.Password))
        {
            await userManager.AccessFailedAsync(user);
            return Unauthorized(ApiResponse<PortalSession>.Failure(
                await userManager.IsLockedOutAsync(user) ? "account_locked" : "invalid_credentials",
                await userManager.IsLockedOutAsync(user)
                    ? "This account is temporarily locked."
                    : "The username or password is incorrect."));
        }
        await userManager.ResetAccessFailedCountAsync(user);
        if (user?.MustResetPassword == true)
        {
            return StatusCode(StatusCodes.Status403Forbidden, ApiResponse<PortalSession>.Failure(
                "password_reset_required", "An administrator requested a password reset for this account."));
        }
        var remote = accessPolicy.Zone(HttpContext) == PortalRequestZone.Remote;
        if (remote && user!.IsPrimaryAdministrator &&
            !accessPolicy.PrimaryAdministratorRemoteLoginAllowed)
            return StatusCode(StatusCodes.Status403Forbidden, ApiResponse<PortalSession>.Failure(
                "primary_administrator_remote_login_disabled",
                "The primary administrator is restricted to trusted networks by server configuration."));
        var verificationRequired = await accessPolicy.InstallationVerificationRequiredAsync(
            HttpContext, HttpContext.RequestAborted);
        if (verificationRequired && !user!.IsPrimaryAdministrator &&
            user.InstallationVerifiedAt is null)
        {
            securityTickets.SetPendingVerification(
                HttpContext, user.Id, request.RememberMe, DateTimeOffset.UtcNow.AddMinutes(30));
            return ApiResponse<PortalSession>.Success(new(
                false, null, true, "/verify-installation"));
        }
        await signInManager.SignInAsync(user!, request.RememberMe);
        return ApiResponse<PortalSession>.Success(new(true, await ToPortalUserAsync(user!)));
    }

    [HttpPost("reset/complete")]
    [AllowAnonymous]
    [EnableRateLimiting("authentication")]
    public async Task<ActionResult<ApiResponse<PortalSession>>> CompleteReset(
        CompletePasswordResetRequest request)
    {
        var remote = accessPolicy.Zone(HttpContext) == PortalRequestZone.Remote;
        if (remote && !Request.IsHttps)
            return StatusCode(StatusCodes.Status426UpgradeRequired,
                ApiResponse<PortalSession>.Failure("remote_https_required",
                    "Remote password reset requires HTTPS. Use this server's secure public address."));
        if (request.Password != request.ConfirmPassword)
        {
            return BadRequest(ApiResponse<PortalSession>.Failure(
                "password_confirmation_mismatch", "The password confirmation does not match."));
        }
        var user = await userManager.FindByNameAsync(request.Username);
        if (user is null || !user.IsActive)
        {
            return Unauthorized(ApiResponse<PortalSession>.Failure(
                "invalid_reset_ticket", "The reset ticket is invalid or expired."));
        }
        var tokenHash = Convert.ToHexStringLower(
            System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(request.Token)));
        var grant = await database.PasswordResetGrants.SingleOrDefaultAsync(
            item => item.UserId == user.Id && item.TokenHash == tokenHash && item.UsedAt == null,
            HttpContext.RequestAborted);
        if (grant is null || grant.ExpiresAt <= DateTimeOffset.UtcNow)
        {
            return Unauthorized(ApiResponse<PortalSession>.Failure(
                "invalid_reset_ticket", "The reset ticket is invalid or expired."));
        }
        if (remote && user.IsPrimaryAdministrator &&
            !accessPolicy.PrimaryAdministratorRemoteLoginAllowed)
            return StatusCode(StatusCodes.Status403Forbidden,
                ApiResponse<PortalSession>.Failure(
                    "primary_administrator_remote_login_disabled",
                    "The primary administrator is restricted to trusted networks by server configuration."));
        var identityToken = await userManager.GeneratePasswordResetTokenAsync(user);
        var result = await userManager.ResetPasswordAsync(user, identityToken, request.Password);
        if (!result.Succeeded)
        {
            return BadRequest(ApiResponse<PortalSession>.Failure(
                "password_reset_failed", FormatIdentityErrors(result)));
        }
        grant.UsedAt = DateTimeOffset.UtcNow;
        user.MustResetPassword = false;
        user.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        var verificationRequired = await accessPolicy.InstallationVerificationRequiredAsync(
            HttpContext, HttpContext.RequestAborted);
        if (verificationRequired && !user.IsPrimaryAdministrator &&
            user.InstallationVerifiedAt is null)
        {
            securityTickets.SetPendingVerification(
                HttpContext, user.Id, false, DateTimeOffset.UtcNow.AddMinutes(30));
            return ApiResponse<PortalSession>.Success(new(
                false, null, true, "/verify-installation"));
        }
        await signInManager.SignInAsync(user, isPersistent: false);
        return ApiResponse<PortalSession>.Success(new(true, await ToPortalUserAsync(user)));
    }

    [HttpPost("logout")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<bool>>> Logout()
    {
        await signInManager.SignOutAsync();
        return ApiResponse<bool>.Success(true);
    }

    [HttpPost("display-name")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<PortalSession>>> UpdateDisplayName(
        UpdateDisplayNameRequest request)
    {
        var user = await userManager.GetUserAsync(User);
        if (user is null)
            return Unauthorized(ApiResponse<PortalSession>.Failure(
                "session_user_not_found", "The signed-in account no longer exists."));
        var validation = await ValidateDisplayNameAsync(request.DisplayName, user.Id);
        if (validation is not null)
            return BadRequest(ApiResponse<PortalSession>.Failure(
                validation.Value.Code, validation.Value.Message));
        user.DisplayName = request.DisplayName.Trim();
        user.NormalizedDisplayName = NormalizeDisplayName(request.DisplayName);
        user.GameHandle = request.DisplayName.Trim();
        user.NormalizedGameHandle = userManager.NormalizeName(request.DisplayName)!;
        user.UpdatedAt = DateTimeOffset.UtcNow;
        var result = await userManager.UpdateAsync(user);
        if (!result.Succeeded)
            return BadRequest(ApiResponse<PortalSession>.Failure(
                "display_name_update_failed", FormatIdentityErrors(result)));
        await signInManager.RefreshSignInAsync(user);
        return ApiResponse<PortalSession>.Success(new(true, await ToPortalUserAsync(user)));
    }

    [HttpPost("password/change")]
    [Authorize]
    public async Task<ActionResult<ApiResponse<PortalSession>>> ChangePassword(
        ChangePasswordRequest request)
    {
        if (request.NewPassword != request.ConfirmPassword)
        {
            return BadRequest(ApiResponse<PortalSession>.Failure(
                "password_confirmation_mismatch", "The password confirmation does not match."));
        }
        var user = await userManager.GetUserAsync(User);
        if (user is null)
        {
            return Unauthorized(ApiResponse<PortalSession>.Failure(
                "session_user_not_found", "The signed-in account no longer exists."));
        }
        user.UpdatedAt = DateTimeOffset.UtcNow;
        var result = await userManager.ChangePasswordAsync(
            user, request.CurrentPassword, request.NewPassword);
        if (!result.Succeeded)
        {
            return BadRequest(ApiResponse<PortalSession>.Failure(
                "password_change_failed", FormatIdentityErrors(result)));
        }
        await signInManager.RefreshSignInAsync(user);
        return ApiResponse<PortalSession>.Success(new(true, await ToPortalUserAsync(user)));
    }

    private ApplicationUser NewUser(string username, string displayName)
    {
        var gameHandle = displayName.Trim();
        return new ApplicationUser
        {
            UserName = username.Trim(),
            DisplayName = displayName,
            NormalizedDisplayName = NormalizeDisplayName(displayName),
            GameHandle = gameHandle,
            NormalizedGameHandle = userManager.NormalizeName(gameHandle)!,
            EmailConfirmed = true,
            IsActive = true,
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
        };
    }

    private async Task<PortalUser> ToPortalUserAsync(ApplicationUser user)
    {
        var roles = await userManager.GetRolesAsync(user);
        return new PortalUser(
            user.Id,
            user.UserName ?? string.Empty,
            user.DisplayName,
            user.GameHandle,
            roles.ToArray(),
            roles.Contains(PortalRoles.Administrator, StringComparer.Ordinal),
            user.MustResetPassword,
            user.IsActive,
            user.IsPrimaryAdministrator,
            user.InstallationVerifiedAt is not null,
            user.InstallationVerifiedAt);
    }

    private async Task<bool> IsRegistrationEnabledAsync()
    {
        var setting = await database.PortalSettings
            .AsNoTracking()
            .SingleOrDefaultAsync(
                item => item.Key == "registration.enabled",
                HttpContext.RequestAborted);
        return setting?.Value.Equals("true", StringComparison.OrdinalIgnoreCase) != false;
    }

    private static string FormatIdentityErrors(IdentityResult result) =>
        string.Join(" ", result.Errors.Select(error => error.Description));

    private async Task<(string Code, string Message)?> ValidateDisplayNameAsync(
        string? value, string? currentUserId = null)
    {
        var displayName = value?.Trim() ?? string.Empty;
        if (displayName.Length is < 1 or > 31 || displayName.Any(character => character < 32 || character > 126))
            return ("invalid_display_name", "Public display names must contain 1–31 printable ASCII characters for DirectPlay compatibility.");
        if (FactionCatalog.IsReservedLeaderName(displayName))
            return ("reserved_faction_leader_name", "That name is reserved for an AI faction leader.");
        var normalized = NormalizeDisplayName(displayName);
        if (await database.Users.AsNoTracking().AnyAsync(
                item => item.NormalizedDisplayName == normalized && item.Id != currentUserId,
                HttpContext.RequestAborted))
            return ("display_name_unavailable", "Another LAN player already uses that public display name.");
        if (await database.PortalLobbySeats.AsNoTracking()
                .Join(database.PortalMatches.AsNoTracking(), seat => seat.MatchId,
                    match => match.MatchId, (seat, match) => new { Seat = seat, Match = match })
                .AnyAsync(item => item.Seat.ControllerKind == "human" &&
                    item.Seat.UserId != currentUserId && item.Seat.PlayerHandle != null &&
                    item.Seat.PlayerHandle.ToUpper() == normalized &&
                    item.Match.Status != "completed" && item.Match.Status != "deleted",
                    HttpContext.RequestAborted))
            return ("display_name_reserved_by_match",
                "That public display name is reserved by an unfinished match.");
        return null;
    }

    private static string NormalizeDisplayName(string value) => value.Trim().ToUpperInvariant();

    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9_.-]{2,30}$", RegexOptions.CultureInvariant)]
    private static partial Regex ValidUsername();
}
