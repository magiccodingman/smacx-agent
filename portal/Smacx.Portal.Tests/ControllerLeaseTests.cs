using Smacx.Portal.Services;

namespace Smacx.Portal.Tests;

public sealed class ControllerLeaseTests
{
    [Fact]
    public void SecondGameViewIsReadOnlyUntilItExplicitlyTakesControl()
    {
        var clock = new MutableTimeProvider(DateTimeOffset.Parse("2026-08-30T12:00:00Z"));
        var service = new ControllerLeaseService(clock);

        var first = service.Acquire("match-one", 0, "instance-one", "user-one", "tab-one");
        var second = service.Acquire("match-one", 0, "instance-one", "user-one", "tab-two");

        Assert.Equal("controller", first.Role);
        Assert.Equal("viewer", second.Role);
        Assert.True(service.IsController("instance-one", "user-one", first.LeaseId));
        Assert.False(service.IsController("instance-one", "user-one", second.LeaseId));
        Assert.True(service.TryGetControllerCancellation(
            "instance-one", "user-one", first.LeaseId, out var firstConnection));
        Assert.False(firstConnection.IsCancellationRequested);

        var takeover = service.TakeControl(
            "match-one", 0, "user-one", second.LeaseId);
        var previous = service.Heartbeat(
            "match-one", 0, "user-one", first.LeaseId);

        Assert.Equal("controller", takeover.Role);
        Assert.Equal("viewer", previous.Role);
        Assert.False(service.IsController("instance-one", "user-one", first.LeaseId));
        Assert.True(service.IsController("instance-one", "user-one", second.LeaseId));
        Assert.True(firstConnection.IsCancellationRequested);
    }

    [Fact]
    public void ExpiredControllerCannotAuthorizeInputAndAReconnectCanClaimTheSeat()
    {
        var clock = new MutableTimeProvider(DateTimeOffset.Parse("2026-08-30T12:00:00Z"));
        var service = new ControllerLeaseService(clock);
        var expired = service.Acquire(
            "match-one", 0, "instance-one", "user-one", "tab-one");
        Assert.True(service.TryGetControllerCancellation(
            "instance-one", "user-one", expired.LeaseId, out var activeConnection));
        Assert.False(activeConnection.IsCancellationRequested);
        clock.Advance(ControllerLeaseService.LeaseLifetime + TimeSpan.FromSeconds(1));
        Assert.False(service.IsController("instance-one", "user-one", expired.LeaseId));
        Assert.True(activeConnection.IsCancellationRequested);
        var replacement = service.Acquire(
            "match-one", 0, "instance-one", "user-one", "tab-two");
        Assert.Equal("controller", replacement.Role);
        Assert.NotEqual(expired.LeaseId, replacement.LeaseId);
    }

    [Fact]
    public void LeaseCannotAuthorizeAnotherUserOrWorker()
    {
        var service = new ControllerLeaseService(new MutableTimeProvider(DateTimeOffset.UtcNow));
        var lease = service.Acquire(
            "match-one", 0, "instance-one", "user-one", "tab-one");

        Assert.False(service.IsController("instance-two", "user-one", lease.LeaseId));
        Assert.False(service.IsController("instance-one", "user-two", lease.LeaseId));
    }

    private sealed class MutableTimeProvider(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
        public void Advance(TimeSpan duration) => now += duration;
    }
}
