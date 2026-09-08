using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Tests;

public sealed class TurnMetricRecoveryTests
{
    [Fact]
    public async Task ReplayedTurnPreservesEvidenceAndAllowsLaterTurnToCommit()
    {
        await using var connection = new SqliteConnection("Data Source=:memory:");
        await connection.OpenAsync();
        await using var database = new ApplicationDbContext(
            new DbContextOptionsBuilder<ApplicationDbContext>().UseSqlite(connection).Options);
        await database.Database.EnsureCreatedAsync();
        database.Users.Add(new ApplicationUser { Id = "owner", UserName = "owner" });
        database.PortalMatches.Add(new PortalMatchProfile {
            MatchId = "match-replay", OwnerUserId = "owner", Status = "running",
        });
        await database.SaveChangesAsync();
        PortalTurnMetric Metric(int turn) => new() {
            MatchId = "match-replay", AgentId = "instance-replay", Turn = turn,
            PromptTokens = 123, ApiCalls = 4,
        };
        Assert.True(await PortalMatchSupervisor.AddTurnMetricIfMissingAsync(database, Metric(6)));
        Assert.False(await PortalMatchSupervisor.AddTurnMetricIfMissingAsync(database, Metric(6)));
        await database.SaveChangesAsync();
        database.ChangeTracker.Clear();
        var replay = Metric(6);
        replay.PromptTokens = 999;
        Assert.False(await PortalMatchSupervisor.AddTurnMetricIfMissingAsync(database, replay));
        Assert.True(await PortalMatchSupervisor.AddTurnMetricIfMissingAsync(database, Metric(7)));
        await database.SaveChangesAsync();
        Assert.Equal(2, await database.PortalTurnMetrics.CountAsync());
        Assert.Equal(123, (await database.PortalTurnMetrics.SingleAsync(x => x.Turn == 6)).PromptTokens);
    }
}
