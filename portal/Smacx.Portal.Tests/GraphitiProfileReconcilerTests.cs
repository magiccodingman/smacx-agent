using System.Net;
using System.Text;
using Microsoft.Data.Sqlite;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Smacx.Portal.Data;
using Smacx.Portal.Services;

namespace Smacx.Portal.Tests;

public sealed class GraphitiProfileReconcilerTests
{
    [Fact]
    public async Task MissingPortalProfileNeverClearsDurableGraphitiSelection()
    {
        var requests = new List<(HttpMethod Method, string Path)>();
        var handler = new RecordingHandler(requests);
        await using var fixture = await ReconcilerFixture.CreateAsync(handler);

        await fixture.Reconciler.ReconcileOnceAsync();

        Assert.Collection(requests, request =>
        {
            Assert.Equal(HttpMethod.Get, request.Method);
            Assert.Equal("/api/v1/graphiti", request.Path);
        });
    }

    [Fact]
    public async Task ActivePortalProfileStillHealsDurableGraphitiSnapshot()
    {
        var requests = new List<(HttpMethod Method, string Path)>();
        var handler = new RecordingHandler(requests);
        await using var fixture = await ReconcilerFixture.CreateAsync(handler);
        fixture.Database.PortalAiProfiles.Add(new PortalAiProfile
        {
            ProfileId = "profile-selected",
            DisplayName = "Selected profile",
            NormalizedDisplayName = "SELECTED PROFILE",
            ProviderId = "provider-test",
            ModelId = "model-test",
            ReasoningEffort = "low",
            GenerationSettingsJson = "{}",
            Active = true,
            CreatedAt = DateTimeOffset.UtcNow,
            UpdatedAt = DateTimeOffset.UtcNow,
        });
        await fixture.Database.SaveChangesAsync();

        await fixture.Reconciler.ReconcileOnceAsync();

        Assert.Equal(2, requests.Count);
        Assert.Equal((HttpMethod.Get, "/api/v1/graphiti"), requests[0]);
        Assert.Equal((HttpMethod.Post, "/api/v1/graphiti/sync-profile"), requests[1]);
    }

    private sealed class RecordingHandler(List<(HttpMethod Method, string Path)> requests)
        : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            requests.Add((request.Method, request.RequestUri!.AbsolutePath));
            var json = request.Method == HttpMethod.Get
                ? "{\"ok\":true,\"enabled\":true,\"profile\":{\"profile_id\":\"profile-selected\"}}"
                : "{\"ok\":true,\"synced\":true,\"changed\":false}";
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json"),
            });
        }
    }

    private sealed class ReconcilerFixture : IAsyncDisposable
    {
        private readonly string root;
        private readonly SqliteConnection connection;
        private readonly ServiceProvider services;

        public ApplicationDbContext Database { get; }
        public GraphitiProfileReconciler Reconciler { get; }

        private ReconcilerFixture(
            string root, SqliteConnection connection, ServiceProvider services,
            ApplicationDbContext database, GraphitiProfileReconciler reconciler)
        {
            this.root = root;
            this.connection = connection;
            this.services = services;
            Database = database;
            Reconciler = reconciler;
        }

        public static async Task<ReconcilerFixture> CreateAsync(HttpMessageHandler handler)
        {
            var root = Path.Combine(Path.GetTempPath(), $"smacx-graphiti-reconciler-{Guid.NewGuid():N}");
            Directory.CreateDirectory(root);
            var tokenFile = Path.Combine(root, "portal-service-token");
            await File.WriteAllTextAsync(tokenFile, "test-service-token");
            var connection = new SqliteConnection("Data Source=:memory:");
            await connection.OpenAsync();

            var collection = new ServiceCollection();
            collection.AddDbContext<ApplicationDbContext>(options => options.UseSqlite(connection));
            collection.AddSingleton(new ControlPlaneClient(
                new HttpClient(handler) { BaseAddress = new Uri("http://control.test/") },
                Options.Create(new ControlPlaneOptions { ServiceTokenFile = tokenFile }),
                NullLogger<ControlPlaneClient>.Instance));
            var services = collection.BuildServiceProvider();
            var database = services.GetRequiredService<ApplicationDbContext>();
            await database.Database.EnsureCreatedAsync();
            var reconciler = new GraphitiProfileReconciler(
                services.GetRequiredService<IServiceScopeFactory>(),
                NullLogger<GraphitiProfileReconciler>.Instance);
            return new ReconcilerFixture(root, connection, services, database, reconciler);
        }

        public async ValueTask DisposeAsync()
        {
            await Database.DisposeAsync();
            await services.DisposeAsync();
            await connection.DisposeAsync();
            Directory.Delete(root, recursive: true);
        }
    }
}
