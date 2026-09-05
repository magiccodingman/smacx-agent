using System.Collections.Concurrent;

namespace Smacx.Portal.Services;

// Presentation of an in-flight HTTP launch, before the existing durable
// provisioning lifecycle takes over. Never used as proof of native readiness.
public sealed class LobbyStartupTracker
{
    private readonly ConcurrentDictionary<string, DateTimeOffset> requests = new();
    public bool TryBegin(string matchId) => requests.TryAdd(matchId, DateTimeOffset.UtcNow);
    public DateTimeOffset? Get(string matchId) => requests.TryGetValue(matchId, out var at) ? at : null;
    public void End(string matchId) => requests.TryRemove(matchId, out _);
}
