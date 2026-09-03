using System.Net;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;
using Smacx.Portal.Client.Services;
using Smacx.Portal.Contracts;
using Smacx.Portal.Services;

namespace Smacx.Portal.Tests;

public sealed class ResponseHandlingTests
{
    [Fact]
    public async Task BrowserClientTurnsHtmlErrorIntoTypedFailure()
    {
        using var http = new HttpClient(new SequenceHandler(
            JsonResponse("{\"ok\":true,\"data\":{\"token\":\"csrf-test\"}}"),
            HtmlResponse(HttpStatusCode.InternalServerError)))
        {
            BaseAddress = new Uri("http://portal.test/"),
        };
        var client = new PortalApiClient(http);

        var result = await client.PostAsync<object>("api/admin/providers", new { });

        Assert.Equal(500, result.StatusCode);
        Assert.False(result.Payload?.Ok ?? true);
        Assert.Equal("invalid_portal_response", result.Payload?.Error?.Code);
    }

    [Fact]
    public async Task ServerClientTurnsHtmlControlErrorIntoGatewayFailure()
    {
        var root = Path.Combine(Path.GetTempPath(), $"smacx-control-client-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var tokenFile = Path.Combine(root, "portal-service-token");
        await File.WriteAllTextAsync(tokenFile, "test-service-token");
        try
        {
            using var http = new HttpClient(new SequenceHandler(
                HtmlResponse(HttpStatusCode.InternalServerError)))
            {
                BaseAddress = new Uri("http://control.test/"),
            };
            var client = new ControlPlaneClient(
                http,
                Options.Create(new ControlPlaneOptions { ServiceTokenFile = tokenFile }),
                NullLogger<ControlPlaneClient>.Instance);

            var exception = await Assert.ThrowsAsync<ControlPlaneException>(
                () => client.GetRawAsync("api/v1/providers"));

            Assert.Equal("invalid_control_response", exception.Code);
            Assert.Equal(502, exception.StatusCode);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public void NullableJsonElementFailureEnvelopeSerializesAsJson()
    {
        var response = ApiResponse<JsonElement?>.Failure(
            "provider_display_name_already_exists",
            "A model endpoint already uses that display name.");

        var json = JsonSerializer.Serialize(response);

        Assert.Contains("provider_display_name_already_exists", json);
        Assert.Contains("\"Data\":null", json);
    }

    [Fact]
    public async Task ServerClientTurnsNetworkFailureIntoServiceUnavailable()
    {
        var root = Path.Combine(Path.GetTempPath(), $"smacx-control-client-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var tokenFile = Path.Combine(root, "portal-service-token");
        await File.WriteAllTextAsync(tokenFile, "test-service-token");
        try
        {
            using var http = new HttpClient(new ThrowingHandler())
            {
                BaseAddress = new Uri("http://control.test/"),
            };
            var client = new ControlPlaneClient(
                http,
                Options.Create(new ControlPlaneOptions { ServiceTokenFile = tokenFile }),
                NullLogger<ControlPlaneClient>.Instance);

            var exception = await Assert.ThrowsAsync<ControlPlaneException>(
                () => client.GetRawAsync("api/v1/providers"));

            Assert.Equal("control_unavailable", exception.Code);
            Assert.Equal(503, exception.StatusCode);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task ServerClientReadsDurableCapabilityIncidentAndBundleMetadata()
    {
        var root = Path.Combine(Path.GetTempPath(), $"smacx-control-client-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var tokenFile = Path.Combine(root, "portal-service-token");
        await File.WriteAllTextAsync(tokenFile, "test-service-token");
        try
        {
            var json = """
                {"ok":true,"incidents":[{"incident_id":"incident-test1234","match_id":"match-test1234","instance_id":"instance-test1234","incident_kind":"capability_gap:gap-cccccccccccccccccccccccccccccccc","status":"operator_required","details":{"gap_id":"gap-cccccccccccccccccccccccccccccccc","summary":"AI stopped safely","diagnostic_bundle":{"file_name":"smacx-gap-test.zip","size_bytes":4096}},"first_seen_unix":1800000000,"last_seen_unix":1800000001}]}
                """;
            using var http = new HttpClient(new SequenceHandler(JsonResponse(json)))
            {
                BaseAddress = new Uri("http://control.test/"),
            };
            var client = new ControlPlaneClient(
                http,
                Options.Create(new ControlPlaneOptions { ServiceTokenFile = tokenFile }),
                NullLogger<ControlPlaneClient>.Instance);

            var incident = await client.GetActiveCapabilityIncidentAsync("match-test1234");

            Assert.NotNull(incident);
            Assert.Equal("operator_required", incident.Status);
            Assert.Equal("smacx-gap-test.zip",
                incident.Details.GetProperty("diagnostic_bundle").GetProperty("file_name").GetString());
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task ServerClientSurfacesGenericOperatorIncidentToPortal()
    {
        var root = Path.Combine(Path.GetTempPath(), $"smacx-control-client-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var tokenFile = Path.Combine(root, "portal-service-token");
        await File.WriteAllTextAsync(tokenFile, "test-service-token");
        try
        {
            var json = """
                {"ok":true,"incidents":[{"incident_id":"incident-worker1234","match_id":"match-test1234","instance_id":"instance-test1234","incident_kind":"worker_lost","status":"operator_required","details":{"checkpoint_available":false},"first_seen_unix":1800000000,"last_seen_unix":1800000001}]}
                """;
            using var http = new HttpClient(new SequenceHandler(JsonResponse(json)))
            {
                BaseAddress = new Uri("http://control.test/"),
            };
            var client = new ControlPlaneClient(
                http,
                Options.Create(new ControlPlaneOptions { ServiceTokenFile = tokenFile }),
                NullLogger<ControlPlaneClient>.Instance);

            var incident = await client.GetActiveOperatorIncidentAsync("match-test1234");

            Assert.NotNull(incident);
            Assert.Equal("worker_lost", incident.IncidentKind);
            Assert.Equal("operator_required", incident.Status);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task ServerClientPrefersCapabilityGapOverGenericOperatorIncident()
    {
        var root = Path.Combine(Path.GetTempPath(), $"smacx-control-client-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var tokenFile = Path.Combine(root, "portal-service-token");
        await File.WriteAllTextAsync(tokenFile, "test-service-token");
        try
        {
            var json = """
                {"ok":true,"incidents":[{"incident_id":"incident-worker1234","match_id":"match-test1234","instance_id":"instance-test1234","incident_kind":"worker_lost","status":"operator_required","details":{},"first_seen_unix":1800000000,"last_seen_unix":1800000001},{"incident_id":"incident-gap1234","match_id":"match-test1234","instance_id":"instance-test1234","incident_kind":"capability_gap:gap-cccccccccccccccccccccccccccccccc","status":"operator_required","details":{},"first_seen_unix":1800000002,"last_seen_unix":1800000003}]}
                """;
            using var http = new HttpClient(new SequenceHandler(JsonResponse(json)))
            {
                BaseAddress = new Uri("http://control.test/"),
            };
            var client = new ControlPlaneClient(
                http,
                Options.Create(new ControlPlaneOptions { ServiceTokenFile = tokenFile }),
                NullLogger<ControlPlaneClient>.Instance);

            var incident = await client.GetActiveOperatorIncidentAsync("match-test1234");

            Assert.NotNull(incident);
            Assert.StartsWith("capability_gap:", incident.IncidentKind);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Fact]
    public async Task ServerClientReportsOnlyVerifiedRecoveryCheckpoints()
    {
        var root = Path.Combine(Path.GetTempPath(), $"smacx-control-client-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        var tokenFile = Path.Combine(root, "portal-service-token");
        await File.WriteAllTextAsync(tokenFile, "test-service-token");
        try
        {
            var json = """
                {"ok":true,"match":{"match_id":"match-test1234","display_name":"Recovery test","mode":"singleplayer","status":"running","last_turn":12,"last_year":2112,"created_unix":1800000000,"updated_unix":1800000001,"metadata":{"recovery_checkpoint":{"slot":"control_recovery","verified":true},"last_recovered_unix":1800000012.5}},"seats":[]}
                """;
            using var http = new HttpClient(new SequenceHandler(JsonResponse(json)))
            {
                BaseAddress = new Uri("http://control.test/"),
            };
            var client = new ControlPlaneClient(
                http,
                Options.Create(new ControlPlaneOptions { ServiceTokenFile = tokenFile }),
                NullLogger<ControlPlaneClient>.Instance);

            var match = await client.GetMatchAsync("match-test1234");

            Assert.True(match.Match.HasVerifiedRecoveryCheckpoint);
            Assert.Equal(1800000012.5, match.Match.RuntimeGeneration);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [Theory]
    [InlineData("docker_http_409", "conflict")]
    [InlineData("control_error", "removal of container worker is already in progress")]
    public void ContainerRemovalConflictsAreRetried(string code, string message)
    {
        var exception = new ControlPlaneException(code, message, 409);

        Assert.True(PortalMaintenanceCoordinator.IsTransientLifecycleConflict(exception));
    }

    [Fact]
    public void OrdinaryRecoveryFailureRequiresOperatorReview()
    {
        var exception = new ControlPlaneException(
            "verified_recovery_checkpoint_required", "No checkpoint is available.", 409);

        Assert.False(PortalMaintenanceCoordinator.IsTransientLifecycleConflict(exception));
    }

    private static HttpResponseMessage JsonResponse(string json) => new(HttpStatusCode.OK)
    {
        Content = new StringContent(json, Encoding.UTF8, "application/json"),
    };

    private static HttpResponseMessage HtmlResponse(HttpStatusCode status) => new(status)
    {
        Content = new StringContent("<html><body>upstream failure</body></html>", Encoding.UTF8, "text/html"),
    };

    private sealed class SequenceHandler(params HttpResponseMessage[] responses) : HttpMessageHandler
    {
        private readonly Queue<HttpResponseMessage> responses = new(responses);

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken)
        {
            Assert.NotEmpty(responses);
            return Task.FromResult(responses.Dequeue());
        }
    }

    private sealed class ThrowingHandler : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request,
            CancellationToken cancellationToken) =>
            throw new HttpRequestException("contained test failure");
    }
}
