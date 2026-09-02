using System.Security.Cryptography;
using System.Text;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Infrastructure;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Route("api/admin/users")]
[Authorize(Roles = PortalRoles.Administrator)]
public sealed class AdminUsersController(
    ApplicationDbContext database,
    UserManager<ApplicationUser> userManager,
    AccountConnectionRegistry connections) : ControllerBase
{
    [HttpGet]
    public async Task<ActionResult<ApiResponse<IReadOnlyList<AdminUserSummary>>>> List()
    {
        var users = await database.Users.AsNoTracking().OrderBy(user => user.NormalizedUserName)
            .ToArrayAsync(HttpContext.RequestAborted);
        var results = new List<AdminUserSummary>(users.Length);
        foreach (var user in users)
        {
            results.Add(new(
                user.Id, user.UserName ?? string.Empty, user.DisplayName, user.GameHandle,
                await userManager.IsInRoleAsync(user, PortalRoles.Administrator),
                user.IsProvisional, user.MustResetPassword, user.CreatedAt,
                user.IsActive, user.IsPrimaryAdministrator,
                user.InstallationVerifiedAt is not null, user.InstallationVerifiedAt));
        }
        return ApiResponse<IReadOnlyList<AdminUserSummary>>.Success(results);
    }

    [HttpPost("{userId}/administrator")]
    public async Task<ActionResult<ApiResponse<AdminUserSummary>>> SetAdministrator(
        string userId, SetAdministratorRequest request)
    {
        var user = await userManager.FindByIdAsync(userId);
        if (user is null)
        {
            return NotFound(ApiResponse<AdminUserSummary>.Failure("user_not_found", "The user was not found."));
        }
        var isAdministrator = await userManager.IsInRoleAsync(user, PortalRoles.Administrator);
        if (user.IsPrimaryAdministrator && !request.IsAdministrator)
            return Conflict(ApiResponse<AdminUserSummary>.Failure(
                "primary_administrator_protected",
                "The primary administrator cannot be demoted."));
        if (isAdministrator && !request.IsAdministrator &&
            (await userManager.GetUsersInRoleAsync(PortalRoles.Administrator)).Count <= 1)
        {
            return Conflict(ApiResponse<AdminUserSummary>.Failure(
                "last_administrator", "Promote another administrator before removing this role."));
        }
        if (request.IsAdministrator != isAdministrator)
        {
            var result = request.IsAdministrator
                ? await userManager.AddToRoleAsync(user, PortalRoles.Administrator)
                : await userManager.RemoveFromRoleAsync(user, PortalRoles.Administrator);
            if (!result.Succeeded)
            {
                return BadRequest(ApiResponse<AdminUserSummary>.Failure(
                    "role_update_failed", string.Join(" ", result.Errors.Select(error => error.Description))));
            }
        }
        return ApiResponse<AdminUserSummary>.Success(new(
            user.Id, user.UserName ?? string.Empty, user.DisplayName, user.GameHandle,
            request.IsAdministrator, user.IsProvisional, user.MustResetPassword, user.CreatedAt,
            user.IsActive, user.IsPrimaryAdministrator,
            user.InstallationVerifiedAt is not null, user.InstallationVerifiedAt));
    }

    [HttpPost("{userId}/active")]
    public async Task<ActionResult<ApiResponse<AdminUserSummary>>> SetActive(
        string userId, SetAccountActiveRequest request)
    {
        var user = await userManager.FindByIdAsync(userId);
        if (user is null)
            return NotFound(ApiResponse<AdminUserSummary>.Failure(
                "user_not_found", "The user was not found."));
        if (user.IsPrimaryAdministrator && !request.IsActive)
            return Conflict(ApiResponse<AdminUserSummary>.Failure(
                "primary_administrator_protected",
                "The primary administrator cannot be deactivated."));
        user.IsActive = request.IsActive;
        user.UpdatedAt = DateTimeOffset.UtcNow;
        var result = await userManager.UpdateAsync(user);
        if (!result.Succeeded)
            return BadRequest(ApiResponse<AdminUserSummary>.Failure(
                "account_state_update_failed",
                string.Join(" ", result.Errors.Select(error => error.Description))));
        await userManager.UpdateSecurityStampAsync(user);
        if (!request.IsActive) connections.Revoke(user.Id);
        return ApiResponse<AdminUserSummary>.Success(await SummaryAsync(user));
    }

    [HttpPost("{userId}/installation-verification")]
    public async Task<ActionResult<ApiResponse<AdminUserSummary>>> ApproveInstallation(string userId)
    {
        var user = await userManager.FindByIdAsync(userId);
        if (user is null)
            return NotFound(ApiResponse<AdminUserSummary>.Failure(
                "user_not_found", "The user was not found."));
        user.InstallationVerifiedAt = DateTimeOffset.UtcNow;
        user.InstallationVerificationSource = "administrator_approval";
        user.InstallationFingerprintId = "administrator-approved";
        user.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        return ApiResponse<AdminUserSummary>.Success(await SummaryAsync(user));
    }

    [HttpPost("{userId}/password-reset")]
    public async Task<ActionResult<ApiResponse<PasswordResetTicket>>> ResetPassword(string userId)
    {
        var user = await userManager.FindByIdAsync(userId);
        if (user is null)
        {
            return NotFound(ApiResponse<PasswordResetTicket>.Failure("user_not_found", "The user was not found."));
        }
        var now = DateTimeOffset.UtcNow;
        var active = await database.PasswordResetGrants
            .Where(grant => grant.UserId == userId && grant.UsedAt == null)
            .ToArrayAsync(HttpContext.RequestAborted);
        foreach (var grant in active) grant.UsedAt = now;
        var token = Convert.ToBase64String(RandomNumberGenerator.GetBytes(24))
            .Replace('+', '-').Replace('/', '_').TrimEnd('=');
        var expires = now.AddMinutes(30);
        database.PasswordResetGrants.Add(new PasswordResetGrant
        {
            UserId = user.Id,
            IssuedByUserId = userManager.GetUserId(User)!,
            TokenHash = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(token))),
            CreatedAt = now,
            ExpiresAt = expires,
        });
        user.MustResetPassword = true;
        user.UpdatedAt = now;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        await userManager.UpdateSecurityStampAsync(user);
        return ApiResponse<PasswordResetTicket>.Success(new(user.UserName!, token, expires));
    }

    private async Task<AdminUserSummary> SummaryAsync(ApplicationUser user) => new(
        user.Id, user.UserName ?? string.Empty, user.DisplayName, user.GameHandle,
        await userManager.IsInRoleAsync(user, PortalRoles.Administrator),
        user.IsProvisional, user.MustResetPassword, user.CreatedAt,
        user.IsActive, user.IsPrimaryAdministrator,
        user.InstallationVerifiedAt is not null, user.InstallationVerifiedAt);
}
