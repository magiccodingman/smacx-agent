using System.Collections.Concurrent;

namespace Smacx.Portal.Services;

public sealed class AccountConnectionRegistry
{
    private readonly ConcurrentDictionary<string, CancellationTokenSource> generations =
        new(StringComparer.Ordinal);

    public CancellationToken Token(string userId) =>
        generations.GetOrAdd(userId, _ => new CancellationTokenSource()).Token;

    public void Revoke(string userId)
    {
        if (!generations.TryRemove(userId, out var generation)) return;
        generation.Cancel();
        generation.Dispose();
    }
}
