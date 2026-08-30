using System.Collections.Concurrent;

namespace Smacx.Portal.Services;

/// <summary>Process-local stream presence. Durable match state never depends on it.</summary>
public sealed class StreamPresenceTracker
{
    private readonly ConcurrentDictionary<string, Presence> entries = new(StringComparer.Ordinal);

    public IDisposable Enter(string instanceId)
    {
        var presence = entries.GetOrAdd(instanceId, _ => new Presence());
        lock (presence)
        {
            presence.ActiveConnections++;
            presence.EverConnected = true;
            presence.LastSeen = DateTimeOffset.UtcNow;
        }
        return new Lease(this, instanceId);
    }

    public PresenceSnapshot Get(string instanceId)
    {
        if (!entries.TryGetValue(instanceId, out var presence)) return new(false, 0, null);
        lock (presence) return new(presence.EverConnected, presence.ActiveConnections, presence.LastSeen);
    }

    private void Exit(string instanceId)
    {
        if (!entries.TryGetValue(instanceId, out var presence)) return;
        lock (presence)
        {
            presence.ActiveConnections = Math.Max(0, presence.ActiveConnections - 1);
            presence.LastSeen = DateTimeOffset.UtcNow;
        }
    }

    private sealed class Presence
    {
        public bool EverConnected { get; set; }
        public int ActiveConnections { get; set; }
        public DateTimeOffset LastSeen { get; set; }
    }

    private sealed class Lease(StreamPresenceTracker owner, string instanceId) : IDisposable
    {
        private int disposed;
        public void Dispose()
        {
            if (Interlocked.Exchange(ref disposed, 1) == 0) owner.Exit(instanceId);
        }
    }
}

public sealed record PresenceSnapshot(bool EverConnected, int ActiveConnections, DateTimeOffset? LastSeen);
