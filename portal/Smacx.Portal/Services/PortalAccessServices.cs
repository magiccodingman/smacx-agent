using System.Net;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;

namespace Smacx.Portal.Services;

public enum PortalRequestZone
{
    Trusted,
    Remote,
}

public sealed class RequestNetworkClassifier
{
    private readonly IReadOnlyList<CidrRange> trustedNetworks;

    public RequestNetworkClassifier()
    {
        var configured = Environment.GetEnvironmentVariable("SMACX_TRUSTED_NETWORKS");
        var values = string.IsNullOrWhiteSpace(configured)
            ? new[] { "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7", "fe80::/10" }
            : configured.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
        trustedNetworks = values.Select(CidrRange.Parse).ToArray();
    }

    public PortalRequestZone Classify(HttpContext context) =>
        IsTrusted(context.Connection.RemoteIpAddress) ? PortalRequestZone.Trusted : PortalRequestZone.Remote;

    public bool IsTrusted(IPAddress? address)
    {
        // In-memory test servers and Unix-domain frontends can legitimately
        // omit a peer IP. Real TCP requests always carry one.
        if (address is null) return true;
        if (address.IsIPv4MappedToIPv6) address = address.MapToIPv4();
        return trustedNetworks.Any(network => network.Contains(address));
    }

    public IReadOnlyList<string> TrustedNetworks => trustedNetworks.Select(item => item.Text).ToArray();

    private sealed record CidrRange(string Text, byte[] Network, int PrefixLength)
    {
        public static CidrRange Parse(string text)
        {
            var parts = text.Split('/', 2);
            if (!IPAddress.TryParse(parts[0], out var address))
                throw new InvalidOperationException($"Invalid trusted network '{text}'.");
            var bytes = address.GetAddressBytes();
            var bits = bytes.Length * 8;
            var prefix = parts.Length == 1 ? bits : int.TryParse(parts[1], out var parsed) ? parsed : -1;
            if (prefix < 0 || prefix > bits)
                throw new InvalidOperationException($"Invalid trusted network prefix '{text}'.");
            ApplyMask(bytes, prefix);
            return new(text, bytes, prefix);
        }

        public bool Contains(IPAddress address)
        {
            var bytes = address.GetAddressBytes();
            if (bytes.Length != Network.Length) return false;
            ApplyMask(bytes, PrefixLength);
            return bytes.AsSpan().SequenceEqual(Network);
        }

        private static void ApplyMask(byte[] bytes, int prefix)
        {
            var whole = prefix / 8;
            var remainder = prefix % 8;
            if (remainder > 0 && whole < bytes.Length)
            {
                bytes[whole] &= (byte)(0xff << (8 - remainder));
                whole++;
            }
            for (var index = whole; index < bytes.Length; index++) bytes[index] = 0;
        }
    }
}

public sealed class PortalAccessPolicy(
    ApplicationDbContext database,
    RequestNetworkClassifier networks)
{
    public PortalRequestZone Zone(HttpContext context) => networks.Classify(context);

    public bool PrimaryAdministratorRemoteLoginAllowed =>
        Environment.GetEnvironmentVariable("SMACX_ALLOW_PRIMARY_ADMIN_REMOTE_LOGIN") == "1";

    public async Task<bool> RegistrationRequiresInvitationAsync(
        HttpContext context,
        CancellationToken cancellationToken = default) =>
        Zone(context) == PortalRequestZone.Remote ||
        await SettingAsync("access.trusted.require_invitation", false, cancellationToken);

    public async Task<bool> InstallationVerificationRequiredAsync(
        HttpContext context,
        CancellationToken cancellationToken = default) =>
        Zone(context) == PortalRequestZone.Remote ||
        await SettingAsync("access.trusted.require_installation_verification", false, cancellationToken);

    public async Task<bool> SettingAsync(
        string key,
        bool fallback,
        CancellationToken cancellationToken = default)
    {
        var value = await database.PortalSettings.AsNoTracking()
            .Where(item => item.Key == key)
            .Select(item => item.Value)
            .SingleOrDefaultAsync(cancellationToken);
        return value is null ? fallback : value.Equals("true", StringComparison.OrdinalIgnoreCase);
    }
}

public sealed class PortalSecurityTicketService(IDataProtectionProvider protection)
{
    public const string RegistrationGrantCookie = "smacx.portal.registration-grant";
    public const string PendingVerificationCookie = "smacx.portal.pending-verification";
    private readonly IDataProtector registrationProtector = protection.CreateProtector("smacx.portal.registration-grant.v1");
    private readonly IDataProtector verificationProtector = protection.CreateProtector("smacx.portal.pending-verification.v1");
    private readonly IDataProtector challengeProtector = protection.CreateProtector("smacx.portal.installation-challenge.v1");

    public void SetRegistrationGrant(HttpContext context, string invitationId, DateTimeOffset expiresAt)
    {
        var payload = JsonSerializer.Serialize(new RegistrationGrant(invitationId, expiresAt));
        SetCookie(context, RegistrationGrantCookie, registrationProtector.Protect(payload), expiresAt);
    }

    public RegistrationGrant? ReadRegistrationGrant(HttpContext context)
    {
        if (!context.Request.Cookies.TryGetValue(RegistrationGrantCookie, out var encoded)) return null;
        try
        {
            var grant = JsonSerializer.Deserialize<RegistrationGrant>(registrationProtector.Unprotect(encoded));
            return grant?.ExpiresAt > DateTimeOffset.UtcNow ? grant : null;
        }
        catch (CryptographicException) { return null; }
        catch (JsonException) { return null; }
    }

    public void ClearRegistrationGrant(HttpContext context) => DeleteCookie(context, RegistrationGrantCookie);

    public void SetPendingVerification(
        HttpContext context,
        string userId,
        bool rememberMe,
        DateTimeOffset expiresAt)
    {
        var payload = JsonSerializer.Serialize(new PendingVerification(userId, rememberMe, expiresAt));
        SetCookie(context, PendingVerificationCookie, verificationProtector.Protect(payload), expiresAt);
    }

    public PendingVerification? ReadPendingVerification(HttpContext context)
    {
        if (!context.Request.Cookies.TryGetValue(PendingVerificationCookie, out var encoded)) return null;
        try
        {
            var pending = JsonSerializer.Deserialize<PendingVerification>(verificationProtector.Unprotect(encoded));
            return pending?.ExpiresAt > DateTimeOffset.UtcNow ? pending : null;
        }
        catch (CryptographicException) { return null; }
        catch (JsonException) { return null; }
    }

    public void ClearPendingVerification(HttpContext context) => DeleteCookie(context, PendingVerificationCookie);

    public string CreateChallenge(string userId, string manifestId, DateTimeOffset expiresAt) =>
        challengeProtector.Protect(JsonSerializer.Serialize(
            new InstallationChallenge(userId, manifestId, Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(16)), expiresAt)));

    public InstallationChallenge? ReadChallenge(string value)
    {
        try
        {
            var challenge = JsonSerializer.Deserialize<InstallationChallenge>(challengeProtector.Unprotect(value));
            return challenge?.ExpiresAt > DateTimeOffset.UtcNow ? challenge : null;
        }
        catch (CryptographicException) { return null; }
        catch (JsonException) { return null; }
    }

    private static void SetCookie(HttpContext context, string name, string value, DateTimeOffset expiresAt) =>
        context.Response.Cookies.Append(name, value, new CookieOptions
        {
            HttpOnly = true,
            Secure = context.Request.IsHttps,
            SameSite = SameSiteMode.Strict,
            IsEssential = true,
            Path = "/",
            Expires = expiresAt,
        });

    private static void DeleteCookie(HttpContext context, string name) =>
        context.Response.Cookies.Delete(name, new CookieOptions
        {
            HttpOnly = true,
            Secure = context.Request.IsHttps,
            SameSite = SameSiteMode.Strict,
            Path = "/",
        });

    public sealed record RegistrationGrant(string InvitationId, DateTimeOffset ExpiresAt);
    public sealed record PendingVerification(string UserId, bool RememberMe, DateTimeOffset ExpiresAt);
    public sealed record InstallationChallenge(string UserId, string ManifestId, string Nonce, DateTimeOffset ExpiresAt);
}
