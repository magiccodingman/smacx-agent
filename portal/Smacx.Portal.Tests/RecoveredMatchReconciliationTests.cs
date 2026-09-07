using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Tests;

public sealed class RecoveredMatchReconciliationTests
{
    [Fact]
    public async Task ConcurrentOwnerParkWinsOverPreviouslyLoadedRecoveryCandidate()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();
        await using var database = new ApplicationDbContext(
            new DbContextOptionsBuilder<ApplicationDbContext>().UseSqlite(connection).Options);
        await database.Database.EnsureCreatedAsync();
        database.Users.Add(new ApplicationUser { Id = "owner", UserName = "owner" });
        var match = new PortalMatchProfile { MatchId = "match-race", OwnerUserId = "owner",
            Status = "parked", UpdatedAt = DateTimeOffset.FromUnixTimeSeconds(100) };
        database.PortalMatches.Add(match);
        await database.SaveChangesAsync();
        // The previously loaded object is deliberately stale, like a control
        // status response arriving after an owner starts a park operation.
        await database.PortalMatches.ExecuteUpdateAsync(update => update
            .SetProperty(item => item.Status, "parking")
            .SetProperty(item => item.UpdatedAt, DateTimeOffset.FromUnixTimeSeconds(102)));
        var observed = new ControlMatch(match.MatchId, "Recovery", "singleplayer",
            "running", null, 55, 2155, 1, 102, true, 101);
        Assert.False(await RecoveredMatchReconciliation.ApplyAsync(
            database, match, observed, CancellationToken.None));
        database.ChangeTracker.Clear();
        Assert.Equal("parking", (await database.PortalMatches.SingleAsync()).Status);
        Assert.Empty(await database.PortalMatchEvents.ToArrayAsync());
    }

    [Theory]
    [InlineData("parked", "running", true, 101, false, true)]
    [InlineData("parked", "running", true, 99, false, false)]
    [InlineData("parked", "running", false, 101, false, false)]
    [InlineData("parked", "starting", true, 101, false, false)]
    [InlineData("completed", "running", true, 101, false, false)]
    [InlineData("parking", "running", true, 101, false, false)]
    [InlineData("parked", "running", true, 101, true, false)]
    public async Task OnlyNewerFinishedRecoveryRestoresPortalVisibility(
        string status, string nativeStatus, bool verified, double generation,
        bool maintenance, bool expected)
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();
        await using var database = new ApplicationDbContext(
            new DbContextOptionsBuilder<ApplicationDbContext>().UseSqlite(connection).Options);
        await database.Database.EnsureCreatedAsync();
        database.Users.Add(new ApplicationUser { Id = "owner", UserName = "owner" });
        var match = new PortalMatchProfile { MatchId = "match-recovery", Status = status,
            OwnerUserId = "owner",
            UpdatedAt = DateTimeOffset.FromUnixTimeSeconds(100) };
        database.PortalMatches.Add(match);
        if (maintenance) database.PortalMaintenanceOperations.Add(new PortalMaintenanceOperation
            { MatchId = match.MatchId, Kind = "direct_park", Status = "queued" });
        await database.SaveChangesAsync();
        var observed = new ControlMatch(match.MatchId, "Recovery", "singleplayer",
            nativeStatus, null, 55, 2155, 1, 102, verified, generation);
        Assert.Equal(expected, await RecoveredMatchReconciliation.ApplyAsync(
            database, match, observed, CancellationToken.None));
        database.ChangeTracker.Clear();
        var persisted = await database.PortalMatches.SingleAsync();
        Assert.Equal(expected ? "running" : status, persisted.Status);
        Assert.Equal(expected ? 1 : 0, await database.PortalMatchEvents.CountAsync());
        if (expected)
        {
            Assert.Equal(55, persisted.CurrentTurn);
            Assert.False(await RecoveredMatchReconciliation.ApplyAsync(
                database, persisted, observed, CancellationToken.None));
            Assert.Equal(1, await database.PortalMatchEvents.CountAsync());
        }
    }
}
