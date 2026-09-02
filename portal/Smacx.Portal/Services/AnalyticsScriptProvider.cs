using Microsoft.Extensions.Caching.Memory;

namespace Smacx.Portal.Services;

public sealed class AnalyticsScriptProvider(
    IHttpClientFactory clients,
    IMemoryCache cache,
    ILogger<AnalyticsScriptProvider> logger)
{
    internal const string SourceUrl =
        "https://track.truthorigin.com/js/script.file-downloads.hash.outbound-links.pageview-props.tagged-events.js";
    private const string CacheKey = "plausible-browser-script";
    private readonly SemaphoreSlim refreshLock = new(1, 1);

    public async Task<string?> GetAsync(CancellationToken cancellationToken)
    {
        if (cache.TryGetValue<string>(CacheKey, out var cached)) return cached;

        await refreshLock.WaitAsync(cancellationToken);
        try
        {
            if (cache.TryGetValue<string>(CacheKey, out cached)) return cached;

            using var response = await clients.CreateClient("plausible-script")
                .GetAsync(SourceUrl, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            response.EnsureSuccessStatusCode();
            var script = await response.Content.ReadAsStringAsync(cancellationToken);
            if (script.Length is < 1_000 or > 256_000)
                throw new InvalidOperationException(
                    $"The Plausible browser script had an unexpected size of {script.Length} bytes.");

            cache.Set(CacheKey, script, TimeSpan.FromHours(24));
            return script;
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            logger.LogWarning(exception, "The Plausible browser script could not be refreshed.");
            return null;
        }
        finally
        {
            refreshLock.Release();
        }
    }
}
