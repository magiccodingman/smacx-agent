using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Smacx.Portal.Contracts;
using Smacx.Portal.Data;

namespace Smacx.Portal.Tests;

public sealed class PortalFlowTests : IAsyncLifetime
{
    private readonly string dataRoot = Path.Combine(
        Path.GetTempPath(), $"smacx-portal-test-{Guid.NewGuid():N}");
    private PortalFactory? factory;
    private HttpClient? client;

    public Task InitializeAsync()
    {
        Directory.CreateDirectory(dataRoot);
        Environment.SetEnvironmentVariable("SMACX_PORTAL_DATA", dataRoot);
        factory = new PortalFactory(dataRoot);
        client = factory.CreateClient(new WebApplicationFactoryClientOptions
        {
            AllowAutoRedirect = false,
            HandleCookies = true,
        });
        return Task.CompletedTask;
    }

    public async Task DisposeAsync()
    {
        client?.Dispose();
        if (factory is not null)
        {
            await factory.DisposeAsync();
        }
        if (Directory.Exists(dataRoot))
        {
            Directory.Delete(dataRoot, recursive: true);
        }
        Environment.SetEnvironmentVariable("SMACX_PORTAL_DATA", null);
    }

    [Fact]
    public async Task BootstrapAuthAndStagedLobbyFlowUsesCanonicalSchema()
    {
        var setup = await GetDataAsync<PortalSetupState>("api/auth/setup");
        Assert.True(setup.SetupRequired);
        Assert.Equal("admin", setup.DefaultAdministrator);
        Assert.Equal(8, setup.PasswordMinimumLength);

        var csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var bootstrapToken = (await File.ReadAllTextAsync(
            Path.Combine(dataRoot, "secrets", "bootstrap-token"))).Trim();
        var bootstrap = await PostAsync<PortalSession>(
            "api/auth/bootstrap",
            new BootstrapRequest(bootstrapToken, "StrongP1", "StrongP1"),
            csrf.Token);
        Assert.Equal(HttpStatusCode.OK, bootstrap.Response.StatusCode);
        Assert.True(bootstrap.Payload.Data?.User?.IsAdministrator);

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var changedPassword = await PostAsync<PortalSession>(
            "api/auth/password/change",
            new ChangePasswordRequest("StrongP1", "Changed2", "Changed2"), csrf.Token);
        Assert.Equal(HttpStatusCode.OK, changedPassword.Response.StatusCode);
        Assert.True(changedPassword.Payload.Ok);

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var create = new CreateLobbyRequest(
            "Test Planetfall", "source-test", "runtime-test", "alien-crossfire",
            "standard", "small", "talent", true, false, false, false, true,
            "none", [], ["GuestOne"], "human", true, 0, "librarian", false, "unranked");
        var created = await PostAsync<LobbyDetails>("api/lobbies", create, csrf.Token);
        Assert.Equal(HttpStatusCode.Created, created.Response.StatusCode);
        Assert.True(created.Payload.Ok);
        Assert.Equal("waiting", created.Payload.Data?.Status);
        Assert.Equal(7, created.Payload.Data?.Seats.Count);
        Assert.Equal("human", created.Payload.Data?.Seats[0].ControllerKind);
        Assert.True(created.Payload.Data?.CanManage);
        var matchId = created.Payload.Data!.MatchId;

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var reservedLeader = await PostAsync<PortalSession>(
            "api/auth/display-name", new UpdateDisplayNameRequest("LADY DEIRDRE SKYE"), csrf.Token);
        Assert.Equal(HttpStatusCode.BadRequest, reservedLeader.Response.StatusCode);
        Assert.Equal("reserved_faction_leader_name", reservedLeader.Payload.Error?.Code);
        var invitedName = await PostAsync<PortalSession>(
            "api/auth/display-name", new UpdateDisplayNameRequest("guestone"), csrf.Token);
        Assert.Equal(HttpStatusCode.BadRequest, invitedName.Response.StatusCode);
        Assert.Equal("display_name_unavailable", invitedName.Payload.Error?.Code);

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var privateMessage = await PostAsync<LobbyMessage>(
            $"api/lobbies/{matchId}/messages",
            new SendLobbyMessageRequest("A private diplomatic test", 3, "private"), csrf.Token);
        Assert.Equal(3, privateMessage.Payload.Data?.RecipientFactionId);
        Assert.Equal("private", privateMessage.Payload.Data?.Channel);

        await using (var scope = factory!.Services.CreateAsyncScope())
        {
            var database = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
            database.PortalAiProfileVersions.Add(new PortalAiProfileVersion
            {
                ProfileVersionId = "profile-version-test", StableProfileId = "profile-test",
                Version = 1, DisplayName = "Test Qwen", AgentId = "agent-test",
                ProviderId = "provider-test", ModelId = "model-test", ReasoningEffort = "low",
            });
            var agentSeat = database.PortalLobbySeats.Single(item =>
                item.MatchId == matchId && item.SeatIndex == 2);
            agentSeat.ControllerKind = "agent";
            agentSeat.AgentId = "agent-test";
            agentSeat.AiProfileVersionId = "profile-version-test";
            agentSeat.OutcomeFinalized = true;
            agentSeat.OutcomeResult = "win";
            agentSeat.VictoryType = "economic_solo";
            database.PortalSettings.Add(new PortalSetting
            {
                Key = $"native-join:{matchId}",
                Value = System.Text.Json.JsonSerializer.Serialize(new NativeJoinDetails(
                    "192.0.2.25", "Test Planetfall", "ABCD-123456789012", "lan",
                    [new NativeJoinPlayer(1, "GaianGuest", 2)], "Join over TCP/IP.")),
            });
            database.PortalMatchEvents.Add(new PortalMatchEvent
            {
                MatchId = matchId, EventType = "lifecycle", Summary = "Test lobby became ready.",
            });
            database.PortalTurnMetrics.Add(new PortalTurnMetric
            {
                MatchId = matchId, AgentId = "agent-test",
                ProfileVersionId = "profile-version-test", Turn = 1,
                DurationSeconds = 2.5, PromptTokens = 100, CompletionTokens = 20,
                CacheReadTokens = 70, CacheWriteTokens = 5, ReasoningTokens = 8,
                ApiCalls = 3, CompletedAt = DateTimeOffset.UtcNow,
            });
            await database.SaveChangesAsync();
        }
        var protectedProvider = await PostAsync<System.Text.Json.JsonElement>(
            "api/admin/providers/provider-test/delete", new { }, csrf.Token);
        Assert.Equal(HttpStatusCode.Conflict, protectedProvider.Response.StatusCode);
        Assert.Equal("provider_in_use_by_ai_profile", protectedProvider.Payload.Error?.Code);
        var withJoin = await GetDataAsync<LobbyDetails>($"api/lobbies/{matchId}");
        Assert.Equal("192.0.2.25", withJoin.NativeJoin?.HostAddress);
        Assert.Equal("GaianGuest", withJoin.NativeJoin?.Players.Single().PlayerName);
        var activity = await GetDataAsync<IReadOnlyList<PortalActivityItem>>("api/status/activity");
        Assert.Contains(activity, item => item.MatchId == matchId && item.Summary.Contains("ready"));
        var analytics = await GetDataAsync<AnalyticsSummary>("api/reports/analytics");
        Assert.Equal(1, analytics.RecordedTurns);
        Assert.Equal(2.5, analytics.MedianTurnSeconds);
        Assert.Equal(100, analytics.PromptTokens);
        Assert.Equal(70, analytics.CacheReadTokens);
        Assert.Equal(8, analytics.ReasoningTokens);
        Assert.Equal(3, analytics.ApiCalls);
        var profileAnalytics = Assert.Single(analytics.Profiles);
        Assert.Equal(1, profileAnalytics.ClassifiedOutcomes);
        Assert.Equal(1, profileAnalytics.Wins);
        Assert.Equal(1, profileAnalytics.WinRate);

        var publicLobbies = await GetDataAsync<IReadOnlyList<PublicLobbySummary>>("api/lobbies");
        Assert.Single(publicLobbies);
        Assert.Equal("Test Planetfall", publicLobbies[0].DisplayName);
        Assert.Equal(3, publicLobbies[0].SeatCount);

        var ranked = create with { DisplayName = "Ranked", RankingMode = "ranked" };
        var rejected = await PostAsync<LobbyDetails>("api/lobbies", ranked, csrf.Token);
        Assert.Equal(HttpStatusCode.BadRequest, rejected.Response.StatusCode);
        Assert.Equal("ranked_not_available", rejected.Payload.Error?.Code);

        var duplicateHandles = create with
        {
            DisplayName = "Duplicate handles",
            InvitedHumanHandles = ["CaseTwin", "casetwin"],
        };
        var duplicateRejected = await PostAsync<LobbyDetails>(
            "api/lobbies", duplicateHandles, csrf.Token);
        Assert.Equal(HttpStatusCode.BadRequest, duplicateRejected.Response.StatusCode);
        Assert.Equal("duplicate_player_handle", duplicateRejected.Payload.Error?.Code);

        await using var connection = new SqliteConnection(
            $"Data Source={Path.Combine(dataRoot, "portal.sqlite3")}");
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name";
        var tables = new List<string>();
        await using var reader = await command.ExecuteReaderAsync();
        while (await reader.ReadAsync()) tables.Add(reader.GetString(0));
        Assert.Contains("PortalMatches", tables);
        Assert.Contains("PortalLobbySeats", tables);
        Assert.DoesNotContain("__EFMigrationsHistory", tables);

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var claimed = await PostAsync<PortalSession>("api/auth/register",
            new RegistrationRequest("guestone", "GuestOne", "GuestA1b", "GuestA1b"),
            csrf.Token);
        Assert.Equal("GuestOne", claimed.Payload.Data?.User?.GameHandle);
        Assert.Equal("GuestOne", claimed.Payload.Data?.User?.DisplayName);
        var claimedLobby = await GetDataAsync<LobbyDetails>($"api/lobbies/{matchId}");
        Assert.Contains(claimedLobby.Seats,
            seat => seat.PlayerHandle == "GuestOne" && seat.CanControl);
    }

    private async Task<T> GetDataAsync<T>(string path)
    {
        var response = await client!.GetFromJsonAsync<ApiResponse<T>>(path);
        Assert.NotNull(response);
        Assert.True(response.Ok, response.Error?.Message);
        return response.Data!;
    }

    private async Task<(HttpResponseMessage Response, ApiResponse<T> Payload)> PostAsync<T>(
        string path, object body, string csrfToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Post, path)
        {
            Content = JsonContent.Create(body),
        };
        request.Headers.Add("X-CSRF-TOKEN", csrfToken);
        var response = await client!.SendAsync(request);
        var payload = await response.Content.ReadFromJsonAsync<ApiResponse<T>>();
        Assert.NotNull(payload);
        return (response, payload);
    }

    private sealed class PortalFactory(string dataRoot) : WebApplicationFactory<Program>
    {
        protected override void ConfigureWebHost(IWebHostBuilder builder)
        {
            builder.UseEnvironment("Testing");
            builder.ConfigureAppConfiguration((_, configuration) => configuration.AddInMemoryCollection(
                new Dictionary<string, string?>
                {
                    ["PortalStorage:DataRoot"] = dataRoot,
                    ["ControlPlane:BaseUrl"] = "http://127.0.0.1:1/",
                    ["ControlPlane:ServiceTokenFile"] = Path.Combine(dataRoot, "portal-service-token"),
                }));
        }
    }
}
