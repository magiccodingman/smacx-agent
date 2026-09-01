using System.Text.Json;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.DependencyInjection;
using Smacx.KnowledgeService;

namespace Smacx.KnowledgeService.Tests;

public sealed class EmbeddingAuditTests : IDisposable
{
    private readonly string root = Path.Combine(
        Path.GetTempPath(), "smacx-embedding-audit-" + Guid.NewGuid().ToString("N"));

    [Fact]
    public async Task AggregatesPurposeMetricsAndExposesPrivacyBoundary()
    {
        Directory.CreateDirectory(root);
        var store = new EmbeddingAuditStore(Options());
        await store.RecordAsync(new(
            "graphiti_projection", "external", "embed-model", "space-1", 768,
            "provider_reported", true, 2, 4, 120, 4, 4, 30), default);
        await store.RecordAsync(new(
            "graphiti_projection", "external", "embed-model", "space-1", 768,
            "provider_reported", true, 1, 2, 60, 2, 2, 15), default);
        await store.RecordAsync(new(
            "graphiti_recall", "external", "embed-model", "space-1", 768,
            "character_estimate", false, 1, 1, 10, 0, 0, 8,
            ErrorCode: "HttpRequestException"), default);
        await store.RecordQualityAsync(new(
            "external", "embed-model", "space-1", 768, true,
            .9, .3, .6, 1, 1, 25, 80, "provider_reported", "Supply Crawler",
            new Dictionary<string, bool> { ["semantic_separation"] = true }), default);

        using var services = new ServiceCollection().BuildServiceProvider();
        var summary = await store.SummaryAsync(new(
            "external", "fingerprint", ModelId: "embed-model", Dimensions: 768,
            SpaceId: "space-1"), services, default);
        var json = JsonSerializer.SerializeToElement(summary);
        var purposes = json.GetProperty("purposes").EnumerateArray().ToArray();
        var projection = Assert.Single(purposes, item =>
            item.GetProperty("purpose").GetString() == "graphiti_projection");
        Assert.Equal(3, projection.GetProperty("calls").GetInt64());
        Assert.Equal(6, projection.GetProperty("inputs").GetInt64());
        Assert.Equal(180, projection.GetProperty("input_tokens").GetInt64());
        Assert.Equal(45, projection.GetProperty("duration_ms").GetDouble());
        Assert.Equal(4_000, projection.GetProperty("effective_tokens_per_second").GetDouble());

        var recall = Assert.Single(purposes, item =>
            item.GetProperty("purpose").GetString() == "graphiti_recall");
        Assert.Equal(1, recall.GetProperty("errors").GetInt64());
        Assert.True(json.GetProperty("quality_audits")[0].GetProperty("passed").GetBoolean());
        var privacy = json.GetProperty("privacy");
        Assert.False(privacy.GetProperty("input_text_retained").GetBoolean());
        Assert.False(privacy.GetProperty("vectors_retained").GetBoolean());
        Assert.False(privacy.GetProperty("credentials_retained").GetBoolean());
    }

    [Fact]
    public async Task SchemaCannotStoreEmbeddingContentOrVectors()
    {
        Directory.CreateDirectory(root);
        var store = new EmbeddingAuditStore(Options());
        await store.RecordAsync(new(
            "wiki_search", "local", "local-model", "local-space", 2048,
            "local_tokenizer", true, 1, 1, 12, 1, 1, 10), default);

        await using var connection = new SqliteConnection(
            $"Data Source={Path.Combine(root, "embedding-audit.sqlite3")};Mode=ReadOnly");
        await connection.OpenAsync();
        foreach (var table in new[] { "embedding_audit_hourly", "embedding_quality_audits" })
        {
            await using var command = connection.CreateCommand();
            command.CommandText = $"PRAGMA table_info({table})";
            await using var reader = await command.ExecuteReaderAsync();
            var columns = new List<string>();
            while (await reader.ReadAsync()) columns.Add(reader.GetString(1));
            Assert.DoesNotContain(columns, name => new[]
            {
                "text", "input_text", "content", "prompt", "embedding", "vector", "vector_blob",
            }.Contains(name, StringComparer.OrdinalIgnoreCase));
        }
    }

    [Fact]
    public async Task RejectsUnknownPurposes()
    {
        Directory.CreateDirectory(root);
        var store = new EmbeddingAuditStore(Options());
        await Assert.ThrowsAsync<ArgumentException>(() => store.RecordAsync(new(
            "secret_unbounded_bucket", "local", "model", "space", 3,
            "local_tokenizer", true, 1, 1, 1, 1, 1, 1), default));
    }

    private KnowledgeRuntimeOptions Options() => new(
        root, Path.Combine(root, "manifest.json"), null, root,
        true, true, "tests", TimeSpan.FromHours(1));

    public void Dispose()
    {
        if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
    }
}
