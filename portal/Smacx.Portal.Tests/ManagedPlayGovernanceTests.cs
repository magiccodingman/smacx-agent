using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Tests;

public sealed class ManagedPlayGovernanceTests
{
    [Fact]
    public async Task OnlyConnectedPeerHumansVoteAndOnePeerIsACompleteQuorum()
    {
        await using var fixture = await GovernanceFixture.CreateAsync();
        var requester = fixture.AddUser("requester", "Deirdre");
        var connected = fixture.AddUser("connected", "Lal");
        var disconnected = fixture.AddUser("disconnected", "Yang");
        var match = fixture.AddRunningMatch(requester);
        fixture.AddHumanSeat(match, requester, 0, "requester-stream", "running");
        fixture.AddHumanSeat(match, connected, 1, "connected-stream", "running");
        fixture.AddHumanSeat(match, disconnected, 2, "disconnected-stream", "disconnected");
        await fixture.Database.SaveChangesAsync();

        using var connectedLease = fixture.Presence.Enter("connected-stream");
        var service = new MatchGovernanceService(fixture.Database, fixture.Presence);
        var proposal = await service.CreateAsync(
            match, requester,
            new CreateGovernanceProposalRequest(
                "native_resolution_change", "{\"profileId\":\"1920x1080\"}", 120),
            CancellationToken.None);

        Assert.Equal("open", proposal.Status);
        Assert.Equal(1, proposal.EligibleVoters);
        Assert.False(proposal.CurrentUserEligible);

        var approved = await service.VoteAsync(
            match.MatchId, proposal.ProposalId, connected.Id, "yes", CancellationToken.None);
        Assert.Equal("approved", approved.Status);
        Assert.Equal(1, approved.YesVotes);

        var rejected = await Assert.ThrowsAsync<GovernanceException>(() => service.VoteAsync(
            match.MatchId, proposal.ProposalId, disconnected.Id, "yes", CancellationToken.None));
        Assert.Equal("proposal_closed", rejected.Code);
    }

    [Fact]
    public async Task SoloManagedMatchApprovesImmediately()
    {
        await using var fixture = await GovernanceFixture.CreateAsync();
        var requester = fixture.AddUser("solo", "Aki");
        var match = fixture.AddRunningMatch(requester);
        fixture.AddHumanSeat(match, requester, 0, "solo-stream", "running");
        await fixture.Database.SaveChangesAsync();

        var service = new MatchGovernanceService(fixture.Database, fixture.Presence);
        var proposal = await service.CreateAsync(
            match, requester,
            new CreateGovernanceProposalRequest("park_match", "{}", 120),
            CancellationToken.None);

        Assert.Equal("approved", proposal.Status);
        Assert.Equal(0, proposal.EligibleVoters);
    }

    [Fact]
    public async Task NativeResolutionCooldownRequiresAnExecutedWaiver()
    {
        await using var fixture = await GovernanceFixture.CreateAsync();
        var requester = fixture.AddUser("requester", "Miriam");
        var match = fixture.AddRunningMatch(requester);
        fixture.AddHumanSeat(match, requester, 0, "requester-stream", "running");
        fixture.Database.PortalMaintenanceOperations.Add(new PortalMaintenanceOperation
        {
            MatchId = match.MatchId,
            Kind = "native_resolution_change",
            Status = "completed",
            CompletedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
        });
        await fixture.Database.SaveChangesAsync();

        var service = new MatchGovernanceService(fixture.Database, fixture.Presence);
        var request = new CreateGovernanceProposalRequest(
            "native_resolution_change", "{\"profileId\":\"1024x768\"}", 120);
        var blocked = await Assert.ThrowsAsync<GovernanceException>(() =>
            service.CreateAsync(match, requester, request, CancellationToken.None));
        Assert.Equal("resolution_cooldown_active", blocked.Code);

        var waiver = await service.CreateAsync(
            match, requester,
            new CreateGovernanceProposalRequest("waive_resolution_cooldown", "{}", 120),
            CancellationToken.None);
        Assert.Equal("approved", waiver.Status);

        var persistedWaiver = await fixture.Database.PortalGovernanceProposals
            .SingleAsync(item => item.ProposalId == waiver.ProposalId);
        persistedWaiver.Status = "executed";
        persistedWaiver.ResolvedAt = DateTimeOffset.UtcNow.AddSeconds(1);
        await fixture.Database.SaveChangesAsync();

        var allowed = await service.CreateAsync(
            match, requester, request, CancellationToken.None);
        Assert.Equal("approved", allowed.Status);
    }

    [Fact]
    public void ValidatedResolutionCatalogIncludesMobileThroughSuperUltrawide()
    {
        Assert.Equal((800, 600),
            (ResolutionProfiles.All.First().Width, ResolutionProfiles.All.First().Height));
        Assert.NotNull(ResolutionProfiles.Find("1024x768"));
        Assert.NotNull(ResolutionProfiles.Find("1920x1080"));
        var maximum = ResolutionProfiles.Find("5120x1440");
        Assert.NotNull(maximum);
        Assert.True(maximum!.Ultrawide);
        Assert.Null(ResolutionProfiles.Find("5120x2160"));
    }

    private sealed class GovernanceFixture : IAsyncDisposable
    {
        private readonly SqliteConnection connection;
        public ApplicationDbContext Database { get; }
        public StreamPresenceTracker Presence { get; } = new();

        private GovernanceFixture(SqliteConnection connection, ApplicationDbContext database)
        {
            this.connection = connection;
            Database = database;
        }

        public static async Task<GovernanceFixture> CreateAsync()
        {
            var connection = new SqliteConnection("Data Source=:memory:");
            await connection.OpenAsync();
            var options = new DbContextOptionsBuilder<ApplicationDbContext>()
                .UseSqlite(connection).Options;
            var database = new ApplicationDbContext(options);
            await database.Database.EnsureCreatedAsync();
            return new GovernanceFixture(connection, database);
        }

        public ApplicationUser AddUser(string id, string handle)
        {
            var user = new ApplicationUser
            {
                Id = id,
                UserName = handle,
                NormalizedUserName = handle.ToUpperInvariant(),
                DisplayName = handle,
                GameHandle = handle,
                NormalizedGameHandle = handle.ToUpperInvariant(),
            };
            Database.Users.Add(user);
            return user;
        }

        public PortalMatchProfile AddRunningMatch(ApplicationUser owner)
        {
            var match = new PortalMatchProfile
            {
                MatchId = $"match-{Guid.NewGuid():N}",
                OwnerUserId = owner.Id,
                DisplayName = "Governance test",
                Status = "running",
            };
            Database.PortalMatches.Add(match);
            return match;
        }

        public void AddHumanSeat(
            PortalMatchProfile match, ApplicationUser user, int seatIndex,
            string instanceId, string connectionState)
        {
            Database.PortalLobbySeats.Add(new PortalLobbySeat
            {
                MatchId = match.MatchId,
                SeatIndex = seatIndex,
                ControllerKind = "human",
                UserId = user.Id,
                PlayerHandle = user.GameHandle,
                JoinMode = "browser",
                ControlInstanceId = instanceId,
                Status = "running",
                ConnectionState = connectionState,
                LastBrowserSeenAt = DateTimeOffset.UtcNow,
            });
        }

        public async ValueTask DisposeAsync()
        {
            await Database.DisposeAsync();
            await connection.DisposeAsync();
        }
    }
}
