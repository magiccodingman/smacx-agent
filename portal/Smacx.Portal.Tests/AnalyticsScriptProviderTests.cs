using System.Net;
using Microsoft.Extensions.Caching.Memory;
using Microsoft.Extensions.Logging.Abstractions;
using Smacx.Portal.Services;

namespace Smacx.Portal.Tests;

public sealed class AnalyticsScriptProviderTests
{
    [Fact]
    public async Task SuccessfulScriptIsValidatedAndCached()
    {
        var handler = new StubHandler(new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(new string('x', 4_900)),
        });
        using var client = new HttpClient(handler);
        using var cache = new MemoryCache(new MemoryCacheOptions());
        var provider = new AnalyticsScriptProvider(
            new StubFactory(client), cache, NullLogger<AnalyticsScriptProvider>.Instance);

        var first = await provider.GetAsync(CancellationToken.None);
        var second = await provider.GetAsync(CancellationToken.None);

        Assert.Equal(first, second);
        Assert.Equal(4_900, first?.Length);
        Assert.Equal(1, handler.RequestCount);
        Assert.Equal(AnalyticsScriptProvider.SourceUrl, handler.LastRequestUri?.ToString());
    }

    [Fact]
    public async Task InvalidOrUnavailableScriptFailsOpen()
    {
        var handler = new StubHandler(new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent("too short"),
        });
        using var client = new HttpClient(handler);
        using var cache = new MemoryCache(new MemoryCacheOptions());
        var provider = new AnalyticsScriptProvider(
            new StubFactory(client), cache, NullLogger<AnalyticsScriptProvider>.Instance);

        Assert.Null(await provider.GetAsync(CancellationToken.None));
    }

    private sealed class StubFactory(HttpClient client) : IHttpClientFactory
    {
        public HttpClient CreateClient(string name)
        {
            Assert.Equal("plausible-script", name);
            return client;
        }
    }

    private sealed class StubHandler(HttpResponseMessage response) : HttpMessageHandler
    {
        public int RequestCount { get; private set; }
        public Uri? LastRequestUri { get; private set; }

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            RequestCount++;
            LastRequestUri = request.RequestUri;
            return Task.FromResult(response);
        }
    }
}
