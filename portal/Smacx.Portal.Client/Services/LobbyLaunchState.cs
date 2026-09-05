using Smacx.Portal.Contracts;

namespace Smacx.Portal.Client.Services;

// A page visit owns its handoff. Merely opening an already running campaign
// must never eject an owner from the campaign controls.
public sealed class LobbyLaunchState
{
    public bool Active { get; private set; }
    public bool Dismissed { get; private set; }
    public DateTimeOffset? BeganAt { get; private set; }
    public DateTimeOffset? LastConfirmedAt { get; private set; }
    private DateTimeOffset? waitingForSeatAt;
    public string? ConnectionProblem { get; private set; }
    public static bool IsStarting(string status) => status is "provisioning" or "starting" or "lobby";
    public void Begin(DateTimeOffset now)
    {
        Dismissed = false;
        Active = true;
        BeganAt = now;
        waitingForSeatAt = null;
        ConnectionProblem = null;
    }
    public void Stay() { Active = false; Dismissed = true; }
    public void Failed(string message) { Stay(); ConnectionProblem = message; }
    public static string? Destination(LobbyDetails lobby)
    {
        if (lobby.Status != "running" || lobby.NeedsAttention is not null || !string.IsNullOrEmpty(lobby.LastError)) return null;
        var player = lobby.Seats.FirstOrDefault(s => s.CanControl && s.InstanceId is not null && s.DelegationStatus != "active");
        if (player is not null) return $"/play/{lobby.MatchId}/{player.SeatIndex}";
        return lobby.Seats.Any(s => s.CanSpectate && s.InstanceId is not null)
            ? $"/spectate/{lobby.MatchId}" : null;
    }
    public string? Observe(LobbyDetails lobby, DateTimeOffset now)
    {
        LastConfirmedAt = now;
        ConnectionProblem = null;
        if (lobby.NeedsAttention is not null || !string.IsNullOrEmpty(lobby.LastError)) { Stay(); return null; }
        if (IsStarting(lobby.Status) || lobby.Status == "waiting" && lobby.StartupRequestedAt is not null)
        {
            if (!Active && !Dismissed) Begin(now);
            return null;
        }
        if (!Active)
        {
            // A later retry from a returned-to-staging lobby is a new launch.
            if (lobby.Status == "waiting" && lobby.StartupRequestedAt is null) Dismissed = false;
            return null;
        }
        var destination = Destination(lobby);
        // The supervisor publishes running before its following seat sync.
        // Allow that reconciliation without losing the player's handoff.
        if (lobby.Status == "running" && destination is null &&
            lobby.Seats.Any(s => s.Managed && s.Status == "provisioned"))
        {
            waitingForSeatAt ??= now;
            if (now - waitingForSeatAt < TimeSpan.FromSeconds(60)) return null;
            Failed("The game started, but seat availability is still being confirmed.");
            return null;
        }
        Stay();
        return destination;
    }
}
