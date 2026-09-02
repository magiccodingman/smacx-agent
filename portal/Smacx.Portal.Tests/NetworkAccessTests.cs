using System.Net;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Tests;

public sealed class NetworkAccessTests
{
    [Theory]
    [InlineData("127.0.0.1", true)]
    [InlineData("10.40.2.8", true)]
    [InlineData("172.31.255.4", true)]
    [InlineData("192.168.50.12", true)]
    [InlineData("8.8.8.8", false)]
    [InlineData("203.0.113.17", false)]
    public void DefaultClassifierSeparatesPrivateAndRemoteAddresses(string value, bool trusted)
    {
        var classifier = new RequestNetworkClassifier();
        Assert.Equal(trusted, classifier.IsTrusted(IPAddress.Parse(value)));
    }

    [Fact]
    public void AccountRevocationCancelsExistingGenerationButNotFutureConnections()
    {
        var registry = new AccountConnectionRegistry();
        var first = registry.Token("player-one");
        registry.Revoke("player-one");
        Assert.True(first.IsCancellationRequested);
        Assert.False(registry.Token("player-one").IsCancellationRequested);
    }

    [Fact]
    public async Task CampaignParticipantCanNeverSpectateEvenAsAdministrator()
    {
        await using var database = await DatabaseAsync();
        var service = new MatchAccessService(database);
        var match = await database.PortalMatches.SingleAsync();

        Assert.False(await service.CanSpectateAsync(match, "participant", true));
        Assert.True(await service.CanSpectateAsync(match, "observer", true));
        Assert.True(await service.CanSpectateAsync(match, "observer", false));

        match.AllowSpectators = false;
        await database.SaveChangesAsync();
        Assert.False(await service.CanSpectateAsync(match, "observer", false));
        Assert.True(await service.CanSpectateAsync(match, "observer", true));
        Assert.False(await service.CanSpectateAsync(match, null, true));
    }

    [Fact]
    public async Task RunningAiOnlyCampaignIsVisibleToEveryAuthenticatedNonParticipant()
    {
        await using var database = await DatabaseAsync();
        var service = new MatchAccessService(database);
        var match = await database.PortalMatches.SingleAsync();
        match.AllowSpectators = false;
        match.Status = "running";
        database.PortalLobbySeats.Add(new PortalLobbySeat
        {
            MatchId = match.MatchId,
            SeatIndex = 0,
            ControllerKind = "agent",
            Status = "ready",
        });
        await database.SaveChangesAsync();

        Assert.True(await service.CanSpectateAsync(match, "owner", false));
        Assert.True(await service.CanSpectateAsync(match, "observer", false));
        Assert.False(await service.CanSpectateAsync(match, "participant", true));
        Assert.False(await service.CanSpectateAsync(match, null, true));
    }

    [Fact]
    public async Task AiOnlyVisibilityStartsOnlyAfterTheCampaignStarts()
    {
        await using var database = await DatabaseAsync();
        var service = new MatchAccessService(database);
        var match = await database.PortalMatches.SingleAsync();
        match.AllowSpectators = false;

        Assert.False(await service.CanSpectateAsync(match, "observer", false));
    }

    [Fact]
    public async Task HumanCampaignStillRequiresSpectatorOptIn()
    {
        await using var database = await DatabaseAsync();
        var service = new MatchAccessService(database);
        var match = await database.PortalMatches.SingleAsync();
        match.AllowSpectators = false;
        match.Status = "running";
        database.PortalLobbySeats.Add(new PortalLobbySeat
        {
            MatchId = match.MatchId,
            SeatIndex = 0,
            ControllerKind = "human",
            UserId = "owner",
            Status = "ready",
        });
        await database.SaveChangesAsync();

        Assert.False(await service.CanSpectateAsync(match, "observer", false));
        Assert.True(await service.CanSpectateAsync(match, "observer", true));
    }

    private static async Task<ApplicationDbContext> DatabaseAsync()
    {
        var options = new DbContextOptionsBuilder<ApplicationDbContext>()
            .UseSqlite($"Data Source={Path.Combine(Path.GetTempPath(), $"smacx-access-{Guid.NewGuid():N}.sqlite3")}")
            .Options;
        var database = new ApplicationDbContext(options);
        await database.Database.EnsureCreatedAsync();
        database.Users.AddRange(
            User("owner"), User("participant"), User("observer"));
        database.PortalMatches.Add(new PortalMatchProfile
        {
            MatchId = "match-access-test", OwnerUserId = "owner",
            DisplayName = "Access test", AllowSpectators = true,
        });
        database.PortalMatchParticipants.Add(new PortalMatchParticipant
        {
            MatchId = "match-access-test", UserId = "participant", FirstSeatIndex = 1,
        });
        await database.SaveChangesAsync();
        return database;
    }

    private static ApplicationUser User(string id) => new()
    {
        Id = id, UserName = id, NormalizedUserName = id.ToUpperInvariant(),
        DisplayName = id, NormalizedDisplayName = id.ToUpperInvariant(),
        GameHandle = id, NormalizedGameHandle = id.ToUpperInvariant(),
    };
}
