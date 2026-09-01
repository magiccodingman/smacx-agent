using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace Smacx.KnowledgeService;

public sealed record EmbeddingAuditRecord(
    string Purpose,
    string Mode,
    string ModelId,
    string SpaceId,
    int Dimensions,
    string TokenCountKind,
    bool Success,
    long Calls,
    long Inputs,
    long InputTokens,
    long Vectors,
    long Chunks,
    double DurationMilliseconds,
    double QueueMilliseconds = 0,
    string? ErrorCode = null);

public sealed record EmbeddingQualityRecord(
    string Mode,
    string ModelId,
    string SpaceId,
    int Dimensions,
    bool Passed,
    double RelatedSimilarity,
    double UnrelatedSimilarity,
    double SemanticMargin,
    double RepeatSimilarity,
    double VectorNorm,
    double DurationMilliseconds,
    long InputTokens,
    string TokenCountKind,
    string? SearchTopTitle,
    IReadOnlyDictionary<string, bool> Checks,
    string? ErrorCode = null);

/// <summary>
/// Stores compact, content-free embedding telemetry. Hourly aggregation keeps
/// this useful for long-running installations without retaining prompts or
/// vectors and without growing one row per inference forever.
/// </summary>
public sealed class EmbeddingAuditStore
{
    private static readonly HashSet<string> Purposes = new(StringComparer.Ordinal)
    {
        "wiki_initial_build", "wiki_refresh", "wiki_search",
        "graphiti_projection", "graphiti_recall", "quality_canary",
    };

    private readonly string connectionString;
    private readonly SemaphoreSlim initialization = new(1, 1);
    private bool initialized;

    public EmbeddingAuditStore(KnowledgeRuntimeOptions options)
    {
        var path = Path.Combine(options.DataRoot, "embedding-audit.sqlite3");
        connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = path,
            Mode = SqliteOpenMode.ReadWriteCreate,
            Cache = SqliteCacheMode.Shared,
        }.ToString();
    }

    public async Task RecordAsync(EmbeddingAuditRecord item, CancellationToken cancellationToken)
    {
        Validate(item);
        await EnsureInitializedAsync(cancellationToken);
        var bucket = DateTimeOffset.UtcNow.ToUnixTimeSeconds() / 3600 * 3600;
        await using var connection = new SqliteConnection(connectionString);
        await connection.OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO embedding_audit_hourly(
                bucket_unix,purpose,mode,model_id,space_id,dimensions,token_count_kind,success,
                calls,inputs,input_tokens,vectors,chunks,duration_ms,queue_ms,min_duration_ms,
                max_duration_ms,errors,last_error_code)
            VALUES($bucket,$purpose,$mode,$model,$space,$dimensions,$token_kind,$success,
                $calls,$inputs,$tokens,$vectors,$chunks,$duration,$queue,$duration,$duration,
                $errors,$error)
            ON CONFLICT(bucket_unix,purpose,mode,model_id,space_id,dimensions,token_count_kind,success)
            DO UPDATE SET calls=calls+excluded.calls,inputs=inputs+excluded.inputs,
                input_tokens=input_tokens+excluded.input_tokens,vectors=vectors+excluded.vectors,
                chunks=chunks+excluded.chunks,duration_ms=duration_ms+excluded.duration_ms,
                queue_ms=queue_ms+excluded.queue_ms,
                min_duration_ms=min(min_duration_ms,excluded.min_duration_ms),
                max_duration_ms=max(max_duration_ms,excluded.max_duration_ms),
                errors=errors+excluded.errors,last_error_code=excluded.last_error_code;
            """;
        command.Parameters.AddWithValue("$bucket", bucket);
        command.Parameters.AddWithValue("$purpose", item.Purpose);
        command.Parameters.AddWithValue("$mode", Bounded(item.Mode, 24));
        command.Parameters.AddWithValue("$model", Bounded(item.ModelId, 240));
        command.Parameters.AddWithValue("$space", Bounded(item.SpaceId, 240));
        command.Parameters.AddWithValue("$dimensions", Math.Max(0, item.Dimensions));
        command.Parameters.AddWithValue("$token_kind", Bounded(item.TokenCountKind, 40));
        command.Parameters.AddWithValue("$success", item.Success ? 1 : 0);
        command.Parameters.AddWithValue("$calls", Math.Max(1, item.Calls));
        command.Parameters.AddWithValue("$inputs", Math.Max(0, item.Inputs));
        command.Parameters.AddWithValue("$tokens", Math.Max(0, item.InputTokens));
        command.Parameters.AddWithValue("$vectors", Math.Max(0, item.Vectors));
        command.Parameters.AddWithValue("$chunks", Math.Max(0, item.Chunks));
        command.Parameters.AddWithValue("$duration", Math.Max(0, item.DurationMilliseconds));
        command.Parameters.AddWithValue("$queue", Math.Max(0, item.QueueMilliseconds));
        command.Parameters.AddWithValue("$errors", item.Success ? 0 : Math.Max(1, item.Calls));
        command.Parameters.AddWithValue("$error", Bounded(item.ErrorCode ?? "", 160));
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    public async Task RecordQualityAsync(EmbeddingQualityRecord item, CancellationToken cancellationToken)
    {
        await EnsureInitializedAsync(cancellationToken);
        await using var connection = new SqliteConnection(connectionString);
        await connection.OpenAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO embedding_quality_audits(
                audit_id,created_unix,mode,model_id,space_id,dimensions,passed,
                related_similarity,unrelated_similarity,semantic_margin,repeat_similarity,
                vector_norm,duration_ms,input_tokens,token_count_kind,search_top_title,
                checks_json,error_code)
            VALUES($id,$created,$mode,$model,$space,$dimensions,$passed,$related,$unrelated,
                $margin,$repeat,$norm,$duration,$tokens,$token_kind,$title,$checks,$error);
            """;
        command.Parameters.AddWithValue("$id", $"audit-{Guid.NewGuid():N}");
        command.Parameters.AddWithValue("$created", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000d);
        command.Parameters.AddWithValue("$mode", Bounded(item.Mode, 24));
        command.Parameters.AddWithValue("$model", Bounded(item.ModelId, 240));
        command.Parameters.AddWithValue("$space", Bounded(item.SpaceId, 240));
        command.Parameters.AddWithValue("$dimensions", Math.Max(0, item.Dimensions));
        command.Parameters.AddWithValue("$passed", item.Passed ? 1 : 0);
        command.Parameters.AddWithValue("$related", item.RelatedSimilarity);
        command.Parameters.AddWithValue("$unrelated", item.UnrelatedSimilarity);
        command.Parameters.AddWithValue("$margin", item.SemanticMargin);
        command.Parameters.AddWithValue("$repeat", item.RepeatSimilarity);
        command.Parameters.AddWithValue("$norm", item.VectorNorm);
        command.Parameters.AddWithValue("$duration", Math.Max(0, item.DurationMilliseconds));
        command.Parameters.AddWithValue("$tokens", Math.Max(0, item.InputTokens));
        command.Parameters.AddWithValue("$token_kind", Bounded(item.TokenCountKind, 40));
        command.Parameters.AddWithValue("$title", Bounded(item.SearchTopTitle ?? "", 240));
        command.Parameters.AddWithValue("$checks", JsonSerializer.Serialize(item.Checks));
        command.Parameters.AddWithValue("$error", Bounded(item.ErrorCode ?? "", 160));
        await command.ExecuteNonQueryAsync(cancellationToken);
        await using var trim = connection.CreateCommand();
        trim.CommandText = """
            DELETE FROM embedding_quality_audits WHERE audit_id IN (
                SELECT audit_id FROM embedding_quality_audits
                ORDER BY created_unix DESC LIMIT -1 OFFSET 200);
            """;
        await trim.ExecuteNonQueryAsync(cancellationToken);
    }

    public async Task<object> SummaryAsync(
        EmbeddingRuntimeConfiguration configuration,
        IServiceProvider services,
        CancellationToken cancellationToken)
    {
        await EnsureInitializedAsync(cancellationToken);
        var model = configuration.ModelId ?? "external";
        var space = configuration.SpaceId ?? "";
        var dimensions = configuration.Dimensions;
        var instances = 0;
        if (configuration.Mode == "local")
        {
            var embedding = services.GetService<OnnxTextEmbeddings.ITextEmbeddingService>();
            model = embedding?.ModelInfo?.ModelId ?? "smacx-local-embeddings";
            space = embedding?.ModelInfo?.EmbeddingSpaceFingerprint ?? "local-onnx";
            dimensions = embedding?.ModelInfo?.Dimensions ?? 0;
            instances = embedding?.ModelInfo?.ModelInstanceCount ?? 0;
        }
        await using var connection = new SqliteConnection(connectionString);
        await connection.OpenAsync(cancellationToken);
        var purposes = new List<object>();
        await using (var command = connection.CreateCommand())
        {
            command.CommandText = """
                SELECT purpose,mode,model_id,space_id,dimensions,token_count_kind,
                    sum(calls) calls,sum(inputs) inputs,sum(input_tokens) input_tokens,
                    sum(vectors) vectors,sum(chunks) chunks,sum(duration_ms) duration_ms,
                    sum(errors) errors,min(min_duration_ms) min_duration_ms,
                    max(max_duration_ms) max_duration_ms
                FROM embedding_audit_hourly GROUP BY purpose,mode,model_id,space_id,
                    dimensions,token_count_kind ORDER BY purpose,model_id;
                """;
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken)) purposes.Add(MapMetric(reader));
        }
        var buckets = new List<object>();
        await using (var command = connection.CreateCommand())
        {
            command.CommandText = """
                SELECT bucket_unix,purpose,sum(calls) calls,sum(input_tokens) input_tokens,
                    sum(duration_ms) duration_ms,sum(errors) errors
                FROM embedding_audit_hourly WHERE bucket_unix >= $after
                GROUP BY bucket_unix,purpose ORDER BY bucket_unix,purpose;
                """;
            command.Parameters.AddWithValue("$after", DateTimeOffset.UtcNow.AddDays(-30).ToUnixTimeSeconds());
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken)) buckets.Add(new
            {
                bucket_unix = reader.GetInt64(0), purpose = reader.GetString(1),
                calls = reader.GetInt64(2), input_tokens = reader.GetInt64(3),
                duration_ms = reader.GetDouble(4), errors = reader.GetInt64(5),
            });
        }
        var quality = new List<object>();
        await using (var command = connection.CreateCommand())
        {
            command.CommandText = "SELECT * FROM embedding_quality_audits ORDER BY created_unix DESC LIMIT 20";
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken)) quality.Add(MapQuality(reader));
        }
        return new
        {
            enabled = configuration.Mode != "disabled",
            configuration = new
            {
                mode = configuration.Mode, model_id = model,
                space_id = space,
                dimensions, shared_model_instances = instances,
            },
            purposes, recent_buckets = buckets, quality_audits = quality,
            privacy = new { input_text_retained = false, vectors_retained = false, credentials_retained = false },
        };
    }

    private async Task EnsureInitializedAsync(CancellationToken cancellationToken)
    {
        if (initialized) return;
        await initialization.WaitAsync(cancellationToken);
        try
        {
            if (initialized) return;
            await using var connection = new SqliteConnection(connectionString);
            await connection.OpenAsync(cancellationToken);
            await using var command = connection.CreateCommand();
            command.CommandText = """
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS embedding_audit_hourly(
                    bucket_unix INTEGER NOT NULL,purpose TEXT NOT NULL,mode TEXT NOT NULL,
                    model_id TEXT NOT NULL,space_id TEXT NOT NULL,dimensions INTEGER NOT NULL,
                    token_count_kind TEXT NOT NULL,success INTEGER NOT NULL,
                    calls INTEGER NOT NULL,inputs INTEGER NOT NULL,input_tokens INTEGER NOT NULL,
                    vectors INTEGER NOT NULL,chunks INTEGER NOT NULL,duration_ms REAL NOT NULL,
                    queue_ms REAL NOT NULL,min_duration_ms REAL NOT NULL,max_duration_ms REAL NOT NULL,
                    errors INTEGER NOT NULL,last_error_code TEXT NOT NULL,
                    PRIMARY KEY(bucket_unix,purpose,mode,model_id,space_id,dimensions,token_count_kind,success));
                CREATE TABLE IF NOT EXISTS embedding_quality_audits(
                    audit_id TEXT PRIMARY KEY,created_unix REAL NOT NULL,mode TEXT NOT NULL,
                    model_id TEXT NOT NULL,space_id TEXT NOT NULL,dimensions INTEGER NOT NULL,
                    passed INTEGER NOT NULL,related_similarity REAL NOT NULL,
                    unrelated_similarity REAL NOT NULL,semantic_margin REAL NOT NULL,
                    repeat_similarity REAL NOT NULL,vector_norm REAL NOT NULL,duration_ms REAL NOT NULL,
                    input_tokens INTEGER NOT NULL,token_count_kind TEXT NOT NULL,
                    search_top_title TEXT NOT NULL,checks_json TEXT NOT NULL,error_code TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS embedding_audit_hourly_purpose
                    ON embedding_audit_hourly(purpose,bucket_unix);
                CREATE INDEX IF NOT EXISTS embedding_quality_created
                    ON embedding_quality_audits(created_unix DESC);
                """;
            await command.ExecuteNonQueryAsync(cancellationToken);
            initialized = true;
        }
        finally { initialization.Release(); }
    }

    private static object MapMetric(SqliteDataReader reader)
    {
        var calls = reader.GetInt64(6);
        var tokens = reader.GetInt64(8);
        var duration = reader.GetDouble(11);
        return new
        {
            purpose = reader.GetString(0), mode = reader.GetString(1), model_id = reader.GetString(2),
            space_id = reader.GetString(3), dimensions = reader.GetInt32(4),
            token_count_kind = reader.GetString(5), calls, inputs = reader.GetInt64(7),
            input_tokens = tokens, vectors = reader.GetInt64(9), chunks = reader.GetInt64(10),
            duration_ms = duration, errors = reader.GetInt64(12),
            min_duration_ms = reader.GetDouble(13), max_duration_ms = reader.GetDouble(14),
            average_duration_ms = calls == 0 ? 0 : duration / calls,
            effective_tokens_per_second = duration <= 0 ? 0 : tokens / (duration / 1000d),
        };
    }

    private static object MapQuality(SqliteDataReader reader) => new
    {
        audit_id = reader.GetString(reader.GetOrdinal("audit_id")),
        created_unix = reader.GetDouble(reader.GetOrdinal("created_unix")),
        mode = reader.GetString(reader.GetOrdinal("mode")),
        model_id = reader.GetString(reader.GetOrdinal("model_id")),
        space_id = reader.GetString(reader.GetOrdinal("space_id")),
        dimensions = reader.GetInt32(reader.GetOrdinal("dimensions")),
        passed = reader.GetInt32(reader.GetOrdinal("passed")) == 1,
        related_similarity = reader.GetDouble(reader.GetOrdinal("related_similarity")),
        unrelated_similarity = reader.GetDouble(reader.GetOrdinal("unrelated_similarity")),
        semantic_margin = reader.GetDouble(reader.GetOrdinal("semantic_margin")),
        repeat_similarity = reader.GetDouble(reader.GetOrdinal("repeat_similarity")),
        vector_norm = reader.GetDouble(reader.GetOrdinal("vector_norm")),
        duration_ms = reader.GetDouble(reader.GetOrdinal("duration_ms")),
        input_tokens = reader.GetInt64(reader.GetOrdinal("input_tokens")),
        token_count_kind = reader.GetString(reader.GetOrdinal("token_count_kind")),
        search_top_title = reader.GetString(reader.GetOrdinal("search_top_title")),
        checks = JsonSerializer.Deserialize<Dictionary<string, bool>>(
            reader.GetString(reader.GetOrdinal("checks_json"))) ?? [],
        error_code = reader.GetString(reader.GetOrdinal("error_code")),
    };

    private static void Validate(EmbeddingAuditRecord item)
    {
        if (!Purposes.Contains(item.Purpose)) throw new ArgumentException("invalid_embedding_audit_purpose");
        if (item.Mode is not ("local" or "external" or "disabled"))
            throw new ArgumentException("invalid_embedding_audit_mode");
    }

    private static string Bounded(string value, int maximum) =>
        value.Length <= maximum ? value : value[..maximum];
}
