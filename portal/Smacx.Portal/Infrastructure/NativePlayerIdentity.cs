using System.Security.Cryptography;
using System.Text;

namespace Smacx.Portal.Infrastructure;

public static class NativePlayerIdentity
{
    /// <summary>
    /// Uses the public display name as the DirectPlay name whenever possible.
    /// A deterministic suffix is only a defensive fallback for an already
    /// occupied transport name; normal portal validation prevents that case.
    /// </summary>
    public static string AllocateAlias(
        string matchId, int seatIndex, string canonicalHandle,
        ISet<string> occupiedNames)
    {
        var handle = canonicalHandle.Trim();
        if (handle.Length <= 31 && handle.All(character => character is >= ' ' and <= '~') &&
            occupiedNames.Add(handle))
            return handle;
        for (var attempt = 0; attempt < 256; attempt++)
        {
            var digest = SHA256.HashData(Encoding.UTF8.GetBytes(
                $"{matchId}\0{seatIndex}\0{attempt}\0{handle.ToUpperInvariant()}"));
            var suffix = $"~{seatIndex + 1}{Convert.ToHexString(digest.AsSpan(0, 2))}";
            var prefixLength = Math.Min(handle.Length, 31 - suffix.Length);
            var candidate = handle[..prefixLength] + suffix;
            if (occupiedNames.Add(candidate)) return candidate;
        }
        throw new InvalidOperationException("Could not allocate a unique native player alias.");
    }
}
