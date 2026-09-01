using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Net.Http.Json;
using System.Diagnostics;
using Microsoft.Data.Sqlite;
using OnnxTextEmbeddings;

namespace Smacx.KnowledgeService;

public sealed record EmbeddingRuntimeConfiguration(
    string Mode, string Fingerprint, string? ProviderId = null,
    string? BaseUrl = null, string? ApiKey = null, string? ModelId = null,
    int Dimensions = 0, string? SpaceId = null)
{
    public static EmbeddingRuntimeConfiguration Load(string controlRoot, bool environmentEnabled)
    {
        if (!environmentEnabled) return Disabled("environment");
        var database = Path.Combine(controlRoot, "smacx.sqlite3");
        if (!File.Exists(database)) return Local();
        try
        {
            using var connection = new SqliteConnection($"Data Source={database};Mode=ReadOnly");
            connection.Open();
            using var command = connection.CreateCommand();
            command.CommandText = "SELECT value_json FROM control_settings WHERE setting_key='embeddings.configuration'";
            var raw = command.ExecuteScalar() as string;
            if (string.IsNullOrWhiteSpace(raw)) return Local();
            using var document = JsonDocument.Parse(raw);
            var root = document.RootElement;
            var mode = root.TryGetProperty("mode", out var modeValue) ? modeValue.GetString() : "local";
            if (mode == "disabled") return Disabled(raw);
            if (mode != "external") return Local(raw);
            var providerId = root.GetProperty("provider_id").GetString()!;
            var modelId = root.GetProperty("model_id").GetString()!;
            var dimensions = root.GetProperty("dimensions").GetInt32();
            var spaceId = root.GetProperty("space_id").GetString()!;
            using var provider = connection.CreateCommand();
            provider.CommandText = "SELECT base_url, api_key_secret_id FROM model_providers WHERE provider_id=$id";
            provider.Parameters.AddWithValue("$id", providerId);
            using var reader = provider.ExecuteReader();
            if (!reader.Read()) throw new InvalidOperationException("Configured embedding provider no longer exists.");
            var baseUrl = reader.GetString(0).TrimEnd('/');
            var secretId = reader.IsDBNull(1) ? null : reader.GetString(1);
            var apiKey = secretId is null ? "local" : ReadSecret(connection, controlRoot, secretId);
            return new("external", Hash(raw), providerId, baseUrl, apiKey, modelId, dimensions, spaceId);
        }
        catch (SqliteException)
        {
            // Control may still be creating its first database. The monitor will
            // request a clean restart if the resulting setting differs.
            return Local();
        }
    }

    private static string ReadSecret(SqliteConnection connection, string controlRoot, string secretId)
    {
        using var command = connection.CreateCommand();
        command.CommandText = "SELECT relative_path, fingerprint FROM secret_refs WHERE secret_id=$id AND status='active'";
        command.Parameters.AddWithValue("$id", secretId);
        using var reader = command.ExecuteReader();
        if (!reader.Read()) throw new InvalidOperationException("Embedding provider secret is unavailable.");
        var root = Path.GetFullPath(Path.Combine(controlRoot, "secrets"));
        var path = Path.GetFullPath(Path.Combine(root, reader.GetString(0)));
        if (Path.GetDirectoryName(path) != root || !File.Exists(path))
            throw new InvalidOperationException("Embedding provider secret path is invalid.");
        var value = File.ReadAllText(path).Trim();
        if (!string.Equals(Hash(value), reader.GetString(1), StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("Embedding provider secret failed its integrity check.");
        return value;
    }

    private static EmbeddingRuntimeConfiguration Local(string source = "local") =>
        new("local", Hash(source));
    private static EmbeddingRuntimeConfiguration Disabled(string source) =>
        new("disabled", Hash(source));
    private static string Hash(string value) => Convert.ToHexStringLower(
        SHA256.HashData(Encoding.UTF8.GetBytes(value)));
}

public sealed class EmbeddingConfigurationMonitor(
    EmbeddingRuntimeConfiguration active,
    KnowledgeRuntimeOptions options,
    IHostApplicationLifetime lifetime,
    ILogger<EmbeddingConfigurationMonitor> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        while (!stoppingToken.IsCancellationRequested)
        {
            try { await Task.Delay(TimeSpan.FromSeconds(30), stoppingToken); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { return; }
            var current = EmbeddingRuntimeConfiguration.Load(options.ControlRoot, options.EnvironmentEmbeddingsEnabled);
            if (current.Fingerprint == active.Fingerprint) continue;
            logger.LogInformation("Embedding configuration changed; requesting a clean container restart and corpus-space revalidation");
            lifetime.StopApplication();
            return;
        }
    }
}

public sealed class GraphEmbeddingFacade(
    EmbeddingRuntimeConfiguration configuration,
    IServiceProvider services,
    IHttpClientFactory clients,
    EmbeddingAuditStore audit,
    ILogger<GraphEmbeddingFacade> logger)
{
    public async Task<EmbeddingGenerationResult> EmbedAsync(
        IReadOnlyList<string> inputs, CancellationToken cancellationToken,
        string purpose = "graphiti_projection")
    {
        var started = Stopwatch.GetTimestamp();
        var modelId = configuration.ModelId ?? "external";
        var spaceId = configuration.SpaceId ?? "";
        var dimensions = configuration.Dimensions;
        var tokens = 0;
        var chunks = 0;
        var tokenKind = "character_estimate";
        try
        {
            if (configuration.Mode == "local")
            {
                var embedding = services.GetRequiredService<OnnxTextEmbeddings.ITextEmbeddingService>();
                var modelInfo = embedding.ModelInfo;
                modelId = modelInfo?.ModelId ?? "smacx-local-embeddings";
                spaceId = modelInfo?.EmbeddingSpaceFingerprint ?? "local-onnx";
                dimensions = modelInfo?.Dimensions ?? 0;
                tokenKind = "local_tokenizer";
                var output = new List<float[]>(inputs.Count);
                foreach (var input in inputs)
                {
                    var embedded = await embedding.EmbedDocumentAsync(input, new OnnxTextEmbeddings.EmbeddingRequestOptions
                    {
                        MaxTokens = 768,
                        VectorFormat = OnnxTextEmbeddings.EmbeddingVectorFormat.Float32,
                    }, cancellationToken);
                    var combined = embedded.CombineToSingle(new OnnxTextEmbeddings.SingleEmbeddingOptions
                    {
                        OutputFormat = OnnxTextEmbeddings.EmbeddingVectorFormat.Float32,
                    });
                    output.Add(combined.Vector.ToFloat32());
                    tokens += combined.SourceTokenCount;
                    chunks += embedded.Count;
                }
                var result = new EmbeddingGenerationResult(output, tokens, tokenKind, chunks);
                await RecordAsync(true, null, result);
                return result;
            }

            var client = clients.CreateClient("external-embeddings");
            using var request = new HttpRequestMessage(HttpMethod.Post, configuration.BaseUrl + "/embeddings")
            {
                Content = JsonContent.Create(new { model = configuration.ModelId, input = inputs }),
            };
            if (configuration.ApiKey is not null)
                request.Headers.Authorization = new("Bearer", configuration.ApiKey);
            using var response = await client.SendAsync(request, cancellationToken);
            response.EnsureSuccessStatusCode();
            using var document = JsonDocument.Parse(await response.Content.ReadAsStreamAsync(cancellationToken));
            var vectors = document.RootElement.GetProperty("data").EnumerateArray()
                .OrderBy(item => item.GetProperty("index").GetInt32())
                .Select(item => item.GetProperty("embedding").EnumerateArray().Select(value => value.GetSingle()).ToArray())
                .ToArray();
            if (vectors.Length != inputs.Count || vectors.Any(vector => vector.Length != configuration.Dimensions))
                throw new InvalidOperationException("External embedding response has the wrong shape.");
            if (document.RootElement.TryGetProperty("usage", out var usage) &&
                usage.TryGetProperty("prompt_tokens", out var promptTokens))
            {
                tokens = promptTokens.GetInt32();
                tokenKind = "provider_reported";
            }
            else tokens = inputs.Sum(input => Math.Max(1, (input.Length + 3) / 4));
            chunks = inputs.Count;
            var external = new EmbeddingGenerationResult(vectors, tokens, tokenKind, chunks);
            await RecordAsync(true, null, external);
            return external;
        }
        catch (Exception exception)
        {
            await RecordAsync(false, exception.GetType().Name,
                new([], tokens, tokenKind, chunks));
            throw;
        }

        async Task RecordAsync(
            bool success, string? error, EmbeddingGenerationResult generated)
        {
            try
            {
                await audit.RecordAsync(new(
                    purpose, configuration.Mode, modelId, spaceId, dimensions,
                    generated.TokenCountKind, success, 1, inputs.Count,
                    generated.Tokens, generated.Vectors.Count, generated.Chunks,
                    Stopwatch.GetElapsedTime(started).TotalMilliseconds,
                    ErrorCode: error), CancellationToken.None);
            }
            catch (Exception exception)
            {
                // Observability must never become a dependency of gameplay or
                // memory recall. A later operation can recreate the audit DB.
                logger.LogWarning(exception, "Embedding audit write failed for {Purpose}", purpose);
            }
        }
    }
}

public sealed record EmbeddingGenerationResult(
    IReadOnlyList<float[]> Vectors, int Tokens, string TokenCountKind, int Chunks);
