using System.Diagnostics;

namespace Smacx.KnowledgeService;

public sealed class EmbeddingQualityAuditor(
    GraphEmbeddingFacade embeddings,
    EmbeddingAuditStore audit,
    EmbeddingRuntimeConfiguration configuration,
    IServiceProvider services,
    ILogger<EmbeddingQualityAuditor> logger)
{
    private readonly SemaphoreSlim gate = new(1, 1);
    private string? completedFingerprint;

    public async Task RunOnceAsync(string? searchTopTitle, CancellationToken cancellationToken)
    {
        if (completedFingerprint == configuration.Fingerprint || !await gate.WaitAsync(0, cancellationToken))
            return;
        try
        {
            if (completedFingerprint == configuration.Fingerprint) return;
            var modelId = configuration.ModelId ?? "external";
            var spaceId = configuration.SpaceId ?? "";
            var dimensions = configuration.Dimensions;
            if (configuration.Mode == "local")
            {
                var local = services.GetRequiredService<OnnxTextEmbeddings.ITextEmbeddingService>();
                var modelInfo = local.ModelInfo;
                modelId = modelInfo?.ModelId ?? "smacx-local-embeddings";
                spaceId = modelInfo?.EmbeddingSpaceFingerprint ?? "local-onnx";
                dimensions = modelInfo?.Dimensions ?? 0;
            }
            var started = Stopwatch.GetTimestamp();
            try
            {
                var generated = await embeddings.EmbedAsync([
                    "How do supply crawlers convoy minerals and resources to a base?",
                    "Supply crawler resource convoy mineral production at a home base.",
                    "Planetary Council elections, treaties, pacts, and diplomatic voting.",
                    "How do supply crawlers convoy minerals and resources to a base?",
                ], cancellationToken, "quality_canary");
                var first = generated.Vectors[0];
                var related = Cosine(first, generated.Vectors[1]);
                var unrelated = Cosine(first, generated.Vectors[2]);
                var repeat = Cosine(first, generated.Vectors[3]);
                var norm = Math.Sqrt(first.Sum(value => value * value));
                var checks = new Dictionary<string, bool>(StringComparer.Ordinal)
                {
                    ["correct_dimensions"] = generated.Vectors.All(vector => vector.Length == dimensions),
                    ["finite_values"] = generated.Vectors.All(vector => vector.All(float.IsFinite)),
                    ["normalized_vector"] = norm is >= 0.98 and <= 1.02,
                    ["repeatable_embedding"] = repeat >= 0.9999,
                    ["semantic_separation"] = related - unrelated >= 0.08,
                    ["wiki_retrieval"] = string.Equals(searchTopTitle, "Supply Crawler", StringComparison.OrdinalIgnoreCase),
                };
                var passed = checks.Values.All(value => value);
                await SafeRecordAsync(new(
                    configuration.Mode, modelId, spaceId, dimensions, passed,
                    related, unrelated, related - unrelated, repeat, norm,
                    Stopwatch.GetElapsedTime(started).TotalMilliseconds,
                    generated.Tokens, generated.TokenCountKind, searchTopTitle, checks),
                    CancellationToken.None);
                completedFingerprint = configuration.Fingerprint;
                if (!passed) logger.LogWarning(
                    "Embedding quality audit failed one or more checks for {ModelId}", modelId);
            }
            catch (Exception exception)
            {
                await SafeRecordAsync(new(
                    configuration.Mode, modelId, spaceId, dimensions, false,
                    0, 0, 0, 0, 0, Stopwatch.GetElapsedTime(started).TotalMilliseconds,
                    0, "unavailable", searchTopTitle,
                    new Dictionary<string, bool> { ["inference_completed"] = false },
                    exception.GetType().Name), CancellationToken.None);
                logger.LogError(exception, "Embedding quality audit failed for {ModelId}", modelId);
            }
        }
        finally { gate.Release(); }
    }

    private static double Cosine(IReadOnlyList<float> left, IReadOnlyList<float> right)
    {
        if (left.Count != right.Count || left.Count == 0) return 0;
        double dot = 0, leftNorm = 0, rightNorm = 0;
        for (var index = 0; index < left.Count; index++)
        {
            dot += left[index] * right[index];
            leftNorm += left[index] * left[index];
            rightNorm += right[index] * right[index];
        }
        return leftNorm <= 0 || rightNorm <= 0 ? 0 : dot / Math.Sqrt(leftNorm * rightNorm);
    }

    private async Task SafeRecordAsync(
        EmbeddingQualityRecord record, CancellationToken cancellationToken)
    {
        try { await audit.RecordQualityAsync(record, cancellationToken); }
        catch (Exception exception)
        {
            logger.LogWarning(exception, "Embedding quality audit persistence failed");
        }
    }
}
