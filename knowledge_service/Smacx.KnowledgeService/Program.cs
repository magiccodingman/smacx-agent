using OnnxTextEmbeddings;
using SemanticKnowledge;
using SemanticKnowledge.Http;
using SemanticKnowledge.Sqlite;
using Smacx.KnowledgeService;

var builder = WebApplication.CreateBuilder(args);
var dataRoot = Path.GetFullPath(Environment.GetEnvironmentVariable("SMACX_KNOWLEDGE_DATA") ?? "/var/lib/smacx-knowledge");
Directory.CreateDirectory(dataRoot);
var gameRoot = Environment.GetEnvironmentVariable("SMACX_GAME_SOURCE");
var controlRoot = Path.GetFullPath(Environment.GetEnvironmentVariable("SMACX_CONTROL_DATA") ?? "/var/lib/smacx-control");
var sourceManifest = Environment.GetEnvironmentVariable("SMACX_KNOWLEDGE_SOURCES")
    ?? Path.Combine(AppContext.BaseDirectory, "sources.json");
var environmentEnabled = !string.Equals(Environment.GetEnvironmentVariable("SMACX_EMBEDDINGS_ENABLED"), "0", StringComparison.Ordinal);
var embeddingConfiguration = EmbeddingRuntimeConfiguration.Load(controlRoot, environmentEnabled);
var enabled = embeddingConfiguration.Mode != "disabled";

builder.Services.AddSingleton(new KnowledgeRuntimeOptions(
    dataRoot, sourceManifest, gameRoot, controlRoot, enabled, environmentEnabled,
    ParserRevision: "smacx-semantic-datalinks-v14",
    RefreshInterval: TimeSpan.FromHours(24)));
builder.Services.AddSingleton(embeddingConfiguration);
builder.Services.AddHttpClient("wiki", client =>
{
    client.Timeout = TimeSpan.FromSeconds(30);
    client.DefaultRequestHeaders.UserAgent.ParseAdd("smacx-agent-knowledge/1.0 (local private corpus builder)");
});

if (embeddingConfiguration.Mode == "local")
{
    // One and only one ONNX model registration. SemanticKnowledge's
    // UseOnnxEmbeddings() sees this singleton and reuses it.
    builder.Services.AddOnnxTextEmbeddings(options =>
    {
        options.Cache.Directory = Path.Combine(dataRoot, "models");
        options.DocumentChunkMaxTokens = 768;
        options.QueryMaxTokens = 1024;
        options.Inference.ModelInstanceCount = 1;
        options.Inference.ThreadsPerModel = Math.Clamp(Environment.ProcessorCount, 2, 8);
        options.Inference.ConcurrentRequestsPerModel = 2;
        options.Inference.QueueCapacity = 128;
        options.Chunking.ChunkOverlapTokens = 48;
        options.Chunking.RepeatHeadingContext = true;
        options.Chunking.IncludeChunkText = true;
    });
    builder.Services.AddSemanticKnowledge(options =>
    {
        // This projection is rebuildable from the mounted game/source
        // manifest. Version 2 deliberately retires the former alphabetic
        // collection nodes so they cannot continue influencing Smart Search.
        options.DatabaseVersion = 2;
        options.PersistenceMode = KnowledgePersistenceMode.Rebuildable;
        options.Embeddings.Storage = VectorStoragePreference.Compact;
        options.Embeddings.PersistedRecordFormat = EmbeddingVectorFormat.Int8;
    }).UseSqlite(Path.Combine(dataRoot, "knowledge.sqlite3")).UseOnnxEmbeddings();
}
else if (embeddingConfiguration.Mode == "external")
{
    builder.Services.AddSemanticKnowledge(options =>
    {
        options.DatabaseVersion = 2;
        options.PersistenceMode = KnowledgePersistenceMode.Rebuildable;
        options.Embeddings.Storage = VectorStoragePreference.Compact;
        options.Embeddings.PersistedRecordFormat = EmbeddingVectorFormat.Int8;
    }).UseSqlite(Path.Combine(dataRoot, "knowledge.sqlite3")).UseHttpEmbeddings(options =>
    {
        options.Endpoint = new Uri(embeddingConfiguration.BaseUrl!.TrimEnd('/') + "/embeddings");
        options.ModelId = embeddingConfiguration.ModelId!;
        options.SpaceId = embeddingConfiguration.SpaceId!;
        options.Dimensions = embeddingConfiguration.Dimensions;
        options.BearerToken = embeddingConfiguration.ApiKey;
        options.ApproximateChunkCharacters = 3_000;
    });
}

if (enabled)
{
    builder.Services.AddSingleton<KnowledgeCorpus>();
    builder.Services.AddHostedService<KnowledgeCorpusWorker>();
    builder.Services.AddSingleton<GraphEmbeddingFacade>();
}
builder.Services.AddHostedService<EmbeddingConfigurationMonitor>();
builder.Services.AddHttpClient("external-embeddings", client => client.Timeout = TimeSpan.FromSeconds(90));

var app = builder.Build();

app.MapGet("/healthz", (KnowledgeRuntimeOptions options, IServiceProvider services) =>
{
    if (!options.Enabled)
        return Results.Ok(new { ok = true, enabled = false, state = "disabled" });
    var corpus = services.GetRequiredService<KnowledgeCorpus>();
    var status = corpus.Status;
    var ready = status.State is "ready" or "refreshing";
    if (embeddingConfiguration.Mode == "local")
        ready = ready && services.GetRequiredService<ITextEmbeddingService>().Status.State == EmbeddingServiceState.Ready;
    return Results.Json(new
    {
        ok = ready,
        enabled = true,
        state = ready ? "ready" : status.State,
        embeddings = embeddingConfiguration.Mode,
        corpus = status,
    }, statusCode: ready ? 200 : 503);
});

app.MapGet("/api/status", (KnowledgeRuntimeOptions options, IServiceProvider services) =>
{
    if (!options.Enabled) return Results.Ok(new { enabled = false, state = "disabled" });
    var embedding = embeddingConfiguration.Mode == "local"
        ? services.GetRequiredService<ITextEmbeddingService>() : null;
    return Results.Ok(new
    {
        enabled = true,
        mode = embeddingConfiguration.Mode,
        state = services.GetRequiredService<KnowledgeCorpus>().Status,
        embedding = new
        {
            state = embedding?.Status.State.ToString().ToLowerInvariant() ?? "external",
            message = embedding?.Status.Message,
            model = embedding?.ModelInfo,
            model_id = embeddingConfiguration.ModelId ?? embedding?.ModelInfo?.ModelId,
            dimensions = embeddingConfiguration.Dimensions > 0 ? embeddingConfiguration.Dimensions : embedding?.ModelInfo?.Dimensions,
            shared_model_instances = embedding?.ModelInfo?.ModelInstanceCount ?? 0,
        },
    });
});

if (enabled)
{
app.MapPost("/api/refresh", async (KnowledgeCorpus corpus, CancellationToken cancellationToken) =>
    Results.Ok(await corpus.RefreshAsync(force: true, cancellationToken)));

app.MapGet("/api/topics", async (KnowledgeCorpus corpus, CancellationToken cancellationToken) =>
    Results.Ok(new { topics = await corpus.TopicsAsync(cancellationToken) }));

app.MapGet("/api/tree", async (bool? includeDocuments, KnowledgeCorpus corpus, CancellationToken cancellationToken) =>
    Results.Ok(new { collections = await corpus.TreeAsync(includeDocuments == true, cancellationToken) }));

app.MapGet("/api/collections/{collectionId:guid}/documents", async (
    Guid collectionId, KnowledgeCorpus corpus, CancellationToken cancellationToken) =>
{
    var documents = await corpus.CollectionDocumentsAsync(collectionId, cancellationToken);
    return documents is null ? Results.NotFound(new { error = "collection_not_found" })
        : Results.Ok(new { documents });
});

app.MapPost("/api/search", async (KnowledgeSearchApiRequest request, KnowledgeCorpus corpus, CancellationToken cancellationToken) =>
{
    if (string.IsNullOrWhiteSpace(request.Query)) return Results.BadRequest(new { error = "query_required" });
    return Results.Ok(await corpus.SearchAsync(request, cancellationToken));
});

app.MapGet("/api/documents/{documentId:guid}", async (Guid documentId, KnowledgeCorpus corpus, CancellationToken cancellationToken) =>
{
    var document = await corpus.GetAsync(documentId, cancellationToken);
    return document is null ? Results.NotFound(new { error = "document_not_found" }) : Results.Ok(document);
});

// Internal OpenAI-compatible facade for Graphiti. Long inputs are embedded as
// chunks and combined once; SemanticKnowledge keeps the richer chunk array.
app.MapGet("/v1/models", () => Results.Ok(new
{
    @object = "list",
    data = new[] { new { id = embeddingConfiguration.ModelId ?? "smacx-local-embeddings", @object = "model" } },
}));

app.MapPost("/v1/embeddings", async (OpenAiEmbeddingRequest request, GraphEmbeddingFacade embedding, CancellationToken cancellationToken) =>
{
    var inputs = request.Input.ValueKind switch
    {
        System.Text.Json.JsonValueKind.String => new[] { request.Input.GetString()! },
        System.Text.Json.JsonValueKind.Array => request.Input.EnumerateArray().Select(item => item.GetString() ?? "").ToArray(),
        _ => Array.Empty<string>(),
    };
    if (inputs.Length is 0 or > 64 || inputs.Any(string.IsNullOrWhiteSpace))
        return Results.BadRequest(new { error = new { message = "input must be a string or an array of 1-64 strings" } });
    var generated = await embedding.EmbedAsync(inputs, cancellationToken);
    var output = generated.Vectors.Select((vector, index) =>
        (object)new { @object = "embedding", index, embedding = vector }).ToArray();
    return Results.Ok(new
    {
        @object = "list",
        data = output,
        model = request.Model ?? embeddingConfiguration.ModelId ?? "smacx-local-embeddings",
        usage = new { prompt_tokens = generated.Tokens, total_tokens = generated.Tokens },
    });
});
}

app.Run();

namespace Smacx.KnowledgeService
{
    public sealed record KnowledgeRuntimeOptions(
        string DataRoot, string SourceManifest, string? GameRoot, string ControlRoot,
        bool Enabled, bool EnvironmentEmbeddingsEnabled,
        string ParserRevision, TimeSpan RefreshInterval);
    public sealed record KnowledgeSearchApiRequest(
        string Query, string? Topic = null, int Top = 8, int MaxContentTokens = 8_000,
        bool IncludeContent = true, int MaxQueryTokens = 1_024);
    public sealed record OpenAiEmbeddingRequest(string? Model, System.Text.Json.JsonElement Input);
}
