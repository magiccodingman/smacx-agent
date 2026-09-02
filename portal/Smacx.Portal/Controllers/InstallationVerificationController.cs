using System.Security.Claims;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Controllers;

[ApiController]
[Route("api/installation-verification")]
public sealed class InstallationVerificationController(
    ApplicationDbContext database,
    SignInManager<ApplicationUser> signInManager,
    PortalSecurityTicketService tickets,
    InstallationFingerprintCatalog fingerprints) : ControllerBase
{
    [HttpGet("manifest")]
    [AllowAnonymous]
    public async Task<ActionResult<ApiResponse<InstallationFingerprintManifest>>> Manifest()
    {
        var user = await PendingOrSignedInUserAsync();
        if (user is null)
            return Unauthorized(ApiResponse<InstallationFingerprintManifest>.Failure(
                "verification_session_required", "Sign in again to verify your installation."));
        var challenge = tickets.CreateChallenge(
            user.Id, fingerprints.ManifestId, DateTimeOffset.UtcNow.AddMinutes(15));
        return ApiResponse<InstallationFingerprintManifest>.Success(new(
            fingerprints.ManifestId,
            challenge,
            fingerprints.Edition,
            fingerprints.RequiredRecognizedAnchors,
            fingerprints.Files,
            "/api/installation-verification/complete"));
    }

    [HttpPost("complete")]
    [AllowAnonymous]
    public async Task<ActionResult<ApiResponse<InstallationVerificationResult>>> Complete(
        InstallationVerificationRequest request)
    {
        var user = await PendingOrSignedInUserAsync();
        var challenge = tickets.ReadChallenge(request.ChallengeId);
        if (user is null || challenge is null || challenge.UserId != user.Id ||
            challenge.ManifestId != request.ManifestId || request.ManifestId != fingerprints.ManifestId)
            return Unauthorized(ApiResponse<InstallationVerificationResult>.Failure(
                "invalid_verification_challenge", "The verification session expired. Sign in and try again."));
        if (request.Evidence.Count is < 1 or > 32 ||
            request.Evidence.Any(item => item.Id.Length > 96 || item.Sha256.Length != 64))
            return BadRequest(ApiResponse<InstallationVerificationResult>.Failure(
                "invalid_verification_evidence", "The local verification result is invalid."));
        var result = fingerprints.Verify(request.Evidence);
        if (!result.Verified)
            return UnprocessableEntity(ApiResponse<InstallationVerificationResult>.Success(new(
                false,
                null,
                $"This installation did not match enough known ownership anchors ({result.RecognizedAnchors}/{fingerprints.RequiredRecognizedAnchors}). Mods are allowed; ask the server administrator for manual approval if this is a legitimate unsupported release.")));
        user.InstallationVerifiedAt = DateTimeOffset.UtcNow;
        user.InstallationVerificationSource = "browser_fingerprint";
        user.InstallationFingerprintId = result.FingerprintId;
        user.UpdatedAt = DateTimeOffset.UtcNow;
        await database.SaveChangesAsync(HttpContext.RequestAborted);
        var pending = tickets.ReadPendingVerification(HttpContext);
        if (pending is not null)
        {
            await signInManager.SignInAsync(user, pending.RememberMe);
            tickets.ClearPendingVerification(HttpContext);
        }
        return ApiResponse<InstallationVerificationResult>.Success(new(
            true,
            result.FingerprintId,
            "Ownership verified. This account may now use remote browser play from any device.",
            user.InstallationVerifiedAt));
    }

    private async Task<ApplicationUser?> PendingOrSignedInUserAsync()
    {
        var signedInId = User.FindFirstValue(ClaimTypes.NameIdentifier);
        var userId = signedInId ?? tickets.ReadPendingVerification(HttpContext)?.UserId;
        return userId is null ? null : await database.Users.SingleOrDefaultAsync(
            item => item.Id == userId && item.IsActive, HttpContext.RequestAborted);
    }
}
