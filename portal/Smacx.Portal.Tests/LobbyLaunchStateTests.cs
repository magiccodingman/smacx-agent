using Smacx.Portal.Client.Services;
using Smacx.Portal.Contracts;

namespace Smacx.Portal.Tests;

public class LobbyLaunchStateTests
{
    private static readonly DateTimeOffset Now = DateTimeOffset.UtcNow;
    private static LobbyDetails Lobby(string status, params LobbySeatSummary[] seats) => new(
        "match-test", "Test campaign", "solo", status, null, 1, 2101, true, true, true,
        "unranked", false, "standard", true, seats, new Dictionary<string, object?>(), null,
        null, Now, Now);
    private static LobbySeatSummary Player => new(0,"human",null,"Player",1,"Faction","ready",true,"worker", "browser",CanControl:true);
    private static LobbySeatSummary Spectator => new(1,"agent",null,"AI",2,"Faction","ready",true,"worker",CanSpectate:true);

    [Fact] public void LaterVisitNeverRedirects()
    {
        var state=new LobbyLaunchState();
        Assert.Null(state.Observe(Lobby("running",Player),Now));
        Assert.False(state.Active);
        Assert.Equal("/play/match-test/0",LobbyLaunchState.Destination(Lobby("running",Player)));
    }
    [Theory] [InlineData("provisioning")] [InlineData("starting")] [InlineData("lobby")]
    public void EnteringAnyStartupPhaseArmsOneHandoff(string phase)
    {
        var state=new LobbyLaunchState();
        Assert.Null(state.Observe(Lobby(phase),Now));
        Assert.True(state.Active);
        Assert.Equal("/play/match-test/0",state.Observe(Lobby("running",Player,Spectator),Now));
        Assert.Null(state.Observe(Lobby("running",Player),Now));
    }
    [Fact] public void SpectatorRoutesToDeckOnlyWhenAllowed()
    {
        var state=new LobbyLaunchState();state.Begin(Now);
        Assert.Equal("/spectate/match-test",state.Observe(Lobby("running",Spectator),Now));
        state.Begin(Now);
        Assert.Null(state.Observe(Lobby("running"),Now));
        Assert.False(state.Active);
    }
    [Fact] public void StaySurvivesPollingAndCompletion()
    {
        var state=new LobbyLaunchState();state.Begin(Now);state.Stay();
        state.Observe(Lobby("starting"),Now);
        Assert.False(state.Active);
        Assert.Null(state.Observe(Lobby("running",Player),Now));
    }
    [Fact] public void ConnectionFailureNeverRearmsAutomaticEntry()
    {
        var state=new LobbyLaunchState();state.Begin(Now);state.Failed("offline");
        Assert.False(state.Active);
        state.Observe(Lobby("starting"),Now);
        Assert.False(state.Active);
        Assert.Null(state.Observe(Lobby("running",Player),Now));
    }
    [Theory] [InlineData("error")] [InlineData("parked")] [InlineData("completed")] [InlineData("waiting")] [InlineData("recovering")]
    public void TerminalOrNonStartupStateClosesOverlay(string status)
    {
        var state=new LobbyLaunchState();state.Begin(Now);
        Assert.Null(state.Observe(Lobby(status,Player),Now));
        Assert.False(state.Active);
    }
    [Fact] public void ErrorsAndUnavailableOrDelegatedSeatsCannotRoute()
    {
        var state=new LobbyLaunchState();state.Begin(Now);
        Assert.Null(state.Observe(Lobby("running",Player) with {LastError="Launch failed"},Now));
        Assert.False(state.Active);
        Assert.Null(LobbyLaunchState.Destination(Lobby("running",Player with {InstanceId=null})));
        Assert.Null(LobbyLaunchState.Destination(Lobby("running",Player with {DelegationStatus="active"})));
    }

    [Fact] public void InitialPreparationIsVisibleBeforeProvisioning()
    {
        var state=new LobbyLaunchState();
        state.Observe(Lobby("waiting") with { StartupRequestedAt=Now },Now);
        Assert.True(state.Active);
        Assert.Equal("/play/match-test/0",state.Observe(Lobby("running",Player),Now));
    }
    [Fact] public void RunningBeforeSeatReconciliationRetainsHandoffButIsBounded()
    {
        var state=new LobbyLaunchState();state.Begin(Now);
        var pending=Lobby("running",Player with {CanControl=false,Status="provisioned"});
        Assert.Null(state.Observe(pending,Now));Assert.True(state.Active);
        Assert.Equal("/play/match-test/0",state.Observe(Lobby("running",Player),Now.AddSeconds(5)));
        state.Begin(Now);state.Observe(pending,Now);
        state.Observe(pending,Now.AddSeconds(61));Assert.False(state.Active);
        Assert.NotNull(state.ConnectionProblem);
    }
}

public class LobbyStartupTrackerTests
{
    [Fact] public void InFlightLaunchIsVisibleAndAlwaysReleasable()
    {
        var tracker=new Smacx.Portal.Services.LobbyStartupTracker();
        Assert.True(tracker.TryBegin("match"));
        Assert.NotNull(tracker.Get("match"));
        Assert.False(tracker.TryBegin("match"));
        tracker.End("match");Assert.Null(tracker.Get("match"));
        Assert.True(tracker.TryBegin("match"));
    }
}
