using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
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
    public async Task ProtectedPagesUseOwnedLoginAndLegacyIdentityRoutesAreAbsent()
    {
        using var challenge = await client!.GetAsync("/lobbies/new");
        Assert.Equal(HttpStatusCode.Redirect, challenge.StatusCode);
        Assert.Equal("/login?ReturnUrl=%2Flobbies%2Fnew", challenge.Headers.Location?.PathAndQuery);

        using var legacy = await client.GetAsync("/Account/Login");
        Assert.Equal(HttpStatusCode.NotFound, legacy.StatusCode);
    }

    [Fact]
    public async Task WaitingLobbyExpirationPreservesActiveRoomsAndDeletesInactiveRooms()
    {
        var csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var bootstrapToken = (await File.ReadAllTextAsync(
            Path.Combine(dataRoot, "secrets", "bootstrap-token"))).Trim();
        await PostAsync<PortalSession>("api/auth/bootstrap",
            new BootstrapRequest(bootstrapToken, "StrongP1", "StrongP1"), csrf.Token);
        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var request = new CreateLobbyRequest(
            "Expiring lobby", "source-test", "runtime-test", "alien-crossfire",
            "standard", "small", "talent", true, false, false, false, true);
        var created = await PostAsync<LobbyDetails>("api/lobbies", request, csrf.Token);
        var matchId = created.Payload.Data!.MatchId;

        await using var scope = factory!.Services.CreateAsyncScope();
        var database = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
        var tracker = scope.ServiceProvider.GetRequiredService<Smacx.Portal.Services.WaitingLobbyPresenceTracker>();
        var policy = scope.ServiceProvider.GetRequiredService<Smacx.Portal.Services.WaitingLobbyPolicy>();
        var match = await database.PortalMatches.SingleAsync(item => item.MatchId == matchId);
        match.UpdatedAt = DateTimeOffset.UtcNow - TimeSpan.FromHours(25);
        await database.SaveChangesAsync();

        tracker.Join(matchId, "test-connection");
        var protectedIds = await Smacx.Portal.Services.WaitingLobbyExpiration.ExpireAsync(
            database, tracker, policy, DateTimeOffset.UtcNow);
        Assert.Empty(protectedIds);
        Assert.True(await database.PortalMatches.AnyAsync(item => item.MatchId == matchId));

        tracker.Leave(matchId, "test-connection");
        var expiredIds = await Smacx.Portal.Services.WaitingLobbyExpiration.ExpireAsync(
            database, tracker, policy, DateTimeOffset.UtcNow);
        Assert.Contains(matchId, expiredIds);
        Assert.False(await database.PortalMatches.AnyAsync(item => item.MatchId == matchId));
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
            "standard", "small", "talent", true, false, false, false, true);
        var created = await PostAsync<LobbyDetails>("api/lobbies", create, csrf.Token);
        Assert.Equal(HttpStatusCode.Created, created.Response.StatusCode);
        Assert.True(created.Payload.Ok);
        Assert.Equal("waiting", created.Payload.Data?.Status);
        Assert.Equal(7, created.Payload.Data?.Seats.Count);
        Assert.Equal("human", created.Payload.Data!.Seats[0].ControllerKind);
        Assert.Equal("Administrator", created.Payload.Data.Seats[0].PlayerHandle);
        Assert.True(created.Payload.Data.Seats[0].CanLeave);
        Assert.Equal("reconnecting", created.Payload.Data.Seats[0].ConnectionState);
        Assert.NotNull(created.Payload.Data.Seats[0].StagingPresenceExpiresAt);
        Assert.All(created.Payload.Data.Seats.Skip(1),
            seat => Assert.Equal("open", seat.ControllerKind));
        Assert.True(created.Payload.Data?.CanManage);
        var matchId = created.Payload.Data!.MatchId;
        var administratorCatalog = await GetDataAsync<LobbyCatalog>("api/catalog/lobby");
        Assert.True(administratorCatalog.WaitingLobbies.Unlimited);
        Assert.Null(administratorCatalog.WaitingLobbies.Limit);

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var movedOwner = await PutAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/seats/1",
            new UpdateLobbySeatRequest("human"), csrf.Token);
        Assert.Equal("open", movedOwner.Payload.Data?.Seats[0].ControllerKind);
        Assert.Equal("human", movedOwner.Payload.Data?.Seats[1].ControllerKind);
        var joinedOpenSeat = await PostAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/join", new JoinLobbyRequest(0, "browser"), csrf.Token);
        Assert.Equal(HttpStatusCode.OK, joinedOpenSeat.Response.StatusCode);
        Assert.True(joinedOpenSeat.Payload.Data?.Seats[0].CanLeave);
        Assert.Equal("open", joinedOpenSeat.Payload.Data?.Seats[1].ControllerKind);
        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var leftOpenSeat = await PostAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/leave", new LeaveLobbySeatRequest(0), csrf.Token);
        Assert.Equal(HttpStatusCode.OK, leftOpenSeat.Response.StatusCode);
        Assert.Equal("open", leftOpenSeat.Payload.Data?.Seats[0].ControllerKind);

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var ownerSeat = await PutAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/seats/0",
            new UpdateLobbySeatRequest("human"), csrf.Token);
        Assert.True(ownerSeat.Payload.Data?.Seats[0].CanLeave);
        Assert.False(ownerSeat.Payload.Data?.Seats[0].CanJoin);
        var invitedSeat = await PutAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/seats/1",
            new UpdateLobbySeatRequest("human", PlayerHandle: "GuestOne"), csrf.Token);
        Assert.Equal("GuestOne", invitedSeat.Payload.Data?.Seats[1].PlayerHandle);

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
            database.PortalAiProfiles.Add(new PortalAiProfile
            {
                ProfileId = "profile-test", DisplayName = "Test Qwen",
                NormalizedDisplayName = "TEST QWEN", AgentId = "agent-test",
                ProviderId = "provider-test", ModelId = "model-test", ReasoningEffort = "low",
                GenerationSettingsJson = System.Text.Json.JsonSerializer.Serialize(
                    new ModelGenerationSettings("custom", Temperature: 0.8, TopP: 0.9)),
            });
            var agentSeat = database.PortalLobbySeats.Single(item =>
                item.MatchId == matchId && item.SeatIndex == 2);
            agentSeat.ControllerKind = "agent";
            agentSeat.AgentId = "agent-test";
            agentSeat.AiProfileId = "profile-test";
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
                ProfileId = "profile-test", Turn = 1,
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

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var deactivated = await PostAsync<AiProfile>(
            "api/admin/ai-profiles/profile-test/deactivate", new { }, csrf.Token);
        Assert.False(deactivated.Payload.Data?.Active);
        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var inactiveDuplicate = await PostAsync<AiProfile>(
            "api/admin/ai-profiles",
            new AiProfileRequest("test qwen", "unused-provider", "unused-model", "none"), csrf.Token);
        Assert.Equal(HttpStatusCode.Conflict, inactiveDuplicate.Response.StatusCode);
        Assert.Equal("inactive_ai_profile_name_in_use", inactiveDuplicate.Payload.Error?.Code);
        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var reactivated = await PostAsync<AiProfile>(
            "api/admin/ai-profiles/profile-test/reactivate", new { }, csrf.Token);
        Assert.True(reactivated.Payload.Data?.Active);
        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var activeDuplicate = await PostAsync<AiProfile>(
            "api/admin/ai-profiles",
            new AiProfileRequest("TEST QWEN", "unused-provider", "unused-model", "none"), csrf.Token);
        Assert.Equal(HttpStatusCode.Conflict, activeDuplicate.Response.StatusCode);
        Assert.Equal("ai_profile_name_in_use", activeDuplicate.Payload.Error?.Code);

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var editedSeat = await PutAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/seats/3",
            new UpdateLobbySeatRequest("agent", "agent-test", null, "browser", "random", "standard"),
            csrf.Token);
        Assert.Equal(HttpStatusCode.OK, editedSeat.Response.StatusCode);
        Assert.Equal("agent-test", editedSeat.Payload.Data?.Seats[3].AgentId);
        var configuredHuman = await PutAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/seats/0",
            new UpdateLobbySeatRequest("human", null, null, "browser", "gaians"),
            csrf.Token);
        Assert.Equal("gaians", configuredHuman.Payload.Data?.Seats[0].RequestedFactionId);
        var configuredComputer = await PutAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/seats/4",
            new UpdateLobbySeatRequest("native", null, null, "browser", "hive"),
            csrf.Token);
        Assert.Equal("hive", configuredComputer.Payload.Data?.Seats[4].RequestedFactionId);
        var duplicateFaction = await PutAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/seats/3",
            new UpdateLobbySeatRequest("agent", "agent-test", null, "browser", "gaians"),
            csrf.Token);
        Assert.Equal(HttpStatusCode.Conflict, duplicateFaction.Response.StatusCode);
        Assert.Equal("faction_already_reserved", duplicateFaction.Payload.Error?.Code);
        var reopened = await PutAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/seats/4",
            new UpdateLobbySeatRequest("open"), csrf.Token);
        Assert.Equal("open", reopened.Payload.Data?.Seats[4].ControllerKind);
        var repeatedProfile = await PutAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/seats/2",
            new UpdateLobbySeatRequest("agent", "agent-test"), csrf.Token);
        Assert.Equal(HttpStatusCode.OK, repeatedProfile.Response.StatusCode);
        Assert.Equal(2, repeatedProfile.Payload.Data?.Seats.Count(item => item.AgentId == "agent-test"));
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
        Assert.Equal("custom", profileAnalytics.GenerationPreset);

        var historyPage = await GetDataAsync<MatchHistoryPage>(
            "api/reports/history/page?status=active&query=Planetfall&limit=1");
        Assert.Equal(1, historyPage.FilteredTotal);
        Assert.Single(historyPage.Items);
        Assert.Equal(matchId, historyPage.Items[0].MatchId);

        var publicLobbies = await GetDataAsync<IReadOnlyList<PublicLobbySummary>>("api/lobbies");
        var publicTestLobby = Assert.Single(publicLobbies, item => item.DisplayName == "Test Planetfall");
        Assert.Equal(4, publicTestLobby.SeatCount);

        var ranked = create with { DisplayName = "Ranked", RankingMode = "ranked" };
        var rejected = await PostAsync<LobbyDetails>("api/lobbies", ranked, csrf.Token);
        Assert.Equal(HttpStatusCode.BadRequest, rejected.Response.StatusCode);
        Assert.Equal("ranked_not_available", rejected.Payload.Error?.Code);

        var duplicateRejected = await PutAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/seats/5",
            new UpdateLobbySeatRequest("human", PlayerHandle: "guestone"), csrf.Token);
        Assert.Equal(HttpStatusCode.Conflict, duplicateRejected.Response.StatusCode);
        Assert.Equal("display_name_in_use", duplicateRejected.Payload.Error?.Code);

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

        await using var columnsCommand = connection.CreateCommand();
        columnsCommand.CommandText = "PRAGMA table_info('PortalAiProfiles')";
        var columns = new List<string>();
        await using var columnsReader = await columnsCommand.ExecuteReaderAsync();
        while (await columnsReader.ReadAsync()) columns.Add(columnsReader.GetString(1));
        Assert.Contains("GenerationSettingsJson", columns);
        Assert.Contains("ProfileId", columns);
        Assert.Contains("NormalizedDisplayName", columns);
        Assert.Contains("UpdatedAt", columns);
        Assert.DoesNotContain("Version", columns);
        Assert.DoesNotContain("StableProfileId", columns);

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var claimed = await PostAsync<PortalSession>("api/auth/register",
            new RegistrationRequest("guestone", "GuestOne", "GuestA1b", "GuestA1b"),
            csrf.Token);
        Assert.Equal("GuestOne", claimed.Payload.Data?.User?.GameHandle);
        Assert.Equal("GuestOne", claimed.Payload.Data?.User?.DisplayName);
        var claimedLobby = await GetDataAsync<LobbyDetails>($"api/lobbies/{matchId}");
        Assert.Contains(claimedLobby.Seats,
            seat => seat.PlayerHandle == "GuestOne" && seat.CanJoin && !seat.CanLeave);

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var acceptedSeat = await PostAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/join", new JoinLobbyRequest(1, "browser"), csrf.Token);
        Assert.True(acceptedSeat.Payload.Data?.Seats[1].CanLeave);
        Assert.Equal("ready", acceptedSeat.Payload.Data?.Seats[1].Status);
        var left = await PostAsync<LobbyDetails>(
            $"api/lobbies/{matchId}/leave", new LeaveLobbySeatRequest(1), csrf.Token);
        Assert.Equal(HttpStatusCode.OK, left.Response.StatusCode);
        Assert.Equal("open", left.Payload.Data?.Seats[1].ControllerKind);

        var memberLobbies = new List<string>();
        for (var index = 1; index <= 5; index++)
        {
            csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
            var memberCreated = await PostAsync<LobbyDetails>(
                "api/lobbies", create with { DisplayName = $"Guest lobby {index}" }, csrf.Token);
            Assert.Equal(HttpStatusCode.Created, memberCreated.Response.StatusCode);
            memberLobbies.Add(memberCreated.Payload.Data!.MatchId);
        }
        var quotaCatalog = await GetDataAsync<LobbyCatalog>("api/catalog/lobby");
        Assert.Equal(5, quotaCatalog.WaitingLobbies.OwnedCount);
        Assert.Equal(5, quotaCatalog.WaitingLobbies.Limit);
        Assert.False(quotaCatalog.WaitingLobbies.CanCreate);
        Assert.Equal(5, quotaCatalog.WaitingLobbies.Lobbies.Count);

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var overLimit = await PostAsync<LobbyDetails>(
            "api/lobbies", create with { DisplayName = "Guest lobby 6" }, csrf.Token);
        Assert.Equal(HttpStatusCode.Conflict, overLimit.Response.StatusCode);
        Assert.Equal("waiting_lobby_limit_reached", overLimit.Payload.Error?.Code);

        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var closed = await PostAsync<bool>(
            $"api/lobbies/{memberLobbies[0]}/close", new { }, csrf.Token);
        Assert.True(closed.Payload.Data);
        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var replacement = await PostAsync<LobbyDetails>(
            "api/lobbies", create with { DisplayName = "Replacement lobby" }, csrf.Token);
        Assert.Equal(HttpStatusCode.Created, replacement.Response.StatusCode);
    }

    [Fact]
    public async Task WaitingBrowserSeatUsesMultiTabGraceAndReopensWithoutRemovingOwner()
    {
        var csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var bootstrapToken = (await File.ReadAllTextAsync(
            Path.Combine(dataRoot, "secrets", "bootstrap-token"))).Trim();
        await PostAsync<PortalSession>("api/auth/bootstrap",
            new BootstrapRequest(bootstrapToken, "StrongP1", "StrongP1"), csrf.Token);
        csrf = await GetDataAsync<CsrfTokenResponse>("api/auth/csrf");
        var created = await PostAsync<LobbyDetails>("api/lobbies", new CreateLobbyRequest(
            "Presence lobby", "source-test", "runtime-test", "alien-crossfire",
            "standard", "small", "talent", true, false, false, false, true), csrf.Token);
        var matchId = created.Payload.Data!.MatchId;

        await using var scope = factory!.Services.CreateAsyncScope();
        var database = scope.ServiceProvider.GetRequiredService<ApplicationDbContext>();
        var tracker = scope.ServiceProvider.GetRequiredService<Smacx.Portal.Services.WaitingLobbyPresenceTracker>();
        var policy = scope.ServiceProvider.GetRequiredService<Smacx.Portal.Services.WaitingLobbyPolicy>();
        var ownerSeat = await database.PortalLobbySeats.SingleAsync(item =>
            item.MatchId == matchId && item.SeatIndex == 0);
        var ownerId = ownerSeat.UserId!;
        var now = DateTimeOffset.UtcNow;

        tracker.Join(matchId, "tab-one", ownerId);
        tracker.Join(matchId, "tab-two", ownerId);
        await Smacx.Portal.Services.WaitingLobbySeatLifecycle.SynchronizeAsync(
            database, tracker, policy, now);
        Assert.Equal("connected", ownerSeat.ConnectionState);

        tracker.Leave(matchId, "tab-one");
        await Smacx.Portal.Services.WaitingLobbySeatLifecycle.SynchronizeAsync(
            database, tracker, policy, now + policy.SeatReconnectGrace + TimeSpan.FromSeconds(1));
        Assert.Equal("human", ownerSeat.ControllerKind);
        Assert.Equal("connected", ownerSeat.ConnectionState);

        tracker.Leave(matchId, "tab-two");
        var disconnectedAt = now + policy.SeatReconnectGrace + TimeSpan.FromSeconds(2);
        await Smacx.Portal.Services.WaitingLobbySeatLifecycle.SynchronizeAsync(
            database, tracker, policy, disconnectedAt);
        Assert.Equal("reconnecting", ownerSeat.ConnectionState);

        tracker.Join(matchId, "returning-tab", ownerId);
        await Smacx.Portal.Services.WaitingLobbySeatLifecycle.SynchronizeAsync(
            database, tracker, policy, disconnectedAt + policy.SeatReconnectGrace);
        Assert.Equal("human", ownerSeat.ControllerKind);
        Assert.Equal("connected", ownerSeat.ConnectionState);

        tracker.Leave(matchId, "returning-tab");
        var finalDisconnect = disconnectedAt + policy.SeatReconnectGrace + TimeSpan.FromSeconds(1);
        await Smacx.Portal.Services.WaitingLobbySeatLifecycle.SynchronizeAsync(
            database, tracker, policy, finalDisconnect);
        await Smacx.Portal.Services.WaitingLobbySeatLifecycle.SynchronizeAsync(
            database, tracker, policy,
            finalDisconnect + policy.SeatReconnectGrace + TimeSpan.FromSeconds(1));

        Assert.Equal("open", ownerSeat.ControllerKind);
        Assert.Null(ownerSeat.UserId);
        var membership = await database.PortalMatchMembers.SingleAsync(item =>
            item.MatchId == matchId && item.UserId == ownerId);
        Assert.Equal("owner", membership.Role);
        Assert.Null(membership.SeatIndex);
        Assert.Null(membership.LeftAt);
        Assert.True(await database.PortalMatches.AnyAsync(item => item.MatchId == matchId));
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

    private async Task<(HttpResponseMessage Response, ApiResponse<T> Payload)> PutAsync<T>(
        string path, object body, string csrfToken)
    {
        using var request = new HttpRequestMessage(HttpMethod.Put, path)
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
