using System.Net;
using System.Security.Cryptography;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using HtmlAgilityPack;
using OnnxTextEmbeddings;
using SemanticKnowledge;

namespace Smacx.KnowledgeService;

public sealed record CorpusStatus(
    string State, int Sources, int Documents, int Inserted, int Updated,
    int Unchanged, int Deleted, string? Revision, DateTimeOffset? RefreshedAt,
    string? LastError = null, IReadOnlyList<string>? SourceWarnings = null);

public sealed class KnowledgeCorpus(
    ISemanticKnowledgeStore store,
    IKnowledgeSynchronizationService synchronization,
    IKnowledgeCatalog catalog,
    IKnowledgeStorageProvider storage,
    KnowledgeRuntimeOptions options,
    IHttpClientFactory httpFactory,
    IServiceProvider services,
    EmbeddingRuntimeConfiguration embeddingConfiguration,
    EmbeddingAuditStore embeddingAudit,
    ILogger<KnowledgeCorpus> logger)
{
    private readonly SemaphoreSlim refreshLock = new(1, 1);
    private volatile CorpusStatus status = new("starting", 0, 0, 0, 0, 0, 0, null, null);
    private Guid knowledgeBaseId;
    private Guid schemaId;
    public CorpusStatus Status => status;

    public async Task<CorpusStatus> RefreshAsync(bool force, CancellationToken cancellationToken)
    {
        await refreshLock.WaitAsync(cancellationToken);
        var auditStarted = Stopwatch.GetTimestamp();
        string? auditError = null;
        var auditPurpose = "wiki_refresh";
        var auditRelevant = false;
        var auditInputs = 0;
        var auditTokens = 0L;
        var auditTokenKind = embeddingConfiguration.Mode == "local"
            ? "local_tokenizer_source" : "character_estimate";
        try
        {
            var statePath = Path.Combine(options.DataRoot, "corpus-state.json");
            var previous = await ReadStateAsync(statePath, cancellationToken);
            var manifestBytes = await File.ReadAllBytesAsync(options.SourceManifest, cancellationToken);
            var manifestHash = Convert.ToHexStringLower(SHA256.HashData(manifestBytes));
            var due = previous?.RefreshedAt is null || DateTimeOffset.UtcNow - previous.RefreshedAt >= options.RefreshInterval;
            if (!force && !due && previous?.ParserRevision == options.ParserRevision && previous.ManifestHash == manifestHash)
            {
                await InitializeAsync(cancellationToken);
                status = previous.Status with { State = "ready" };
                return status;
            }

            status = status with { State = "refreshing", LastError = null };
            auditRelevant = true;
            await InitializeAsync(cancellationToken);
            var acquisition = await AcquireAsync(manifestBytes, cancellationToken);
            var pages = CorpusOrganization.Organize(acquisition.Pages, options.ParserRevision);
            auditPurpose = previous is null || previous.Status.Documents == 0
                ? "wiki_initial_build" : "wiki_refresh";
            var grouped = pages.GroupBy(page => string.Join('/', page.CollectionPath!), StringComparer.OrdinalIgnoreCase).ToArray();
            var inserted = 0; var updated = 0; var unchanged = 0; var deleted = 0;
            foreach (var group in grouped)
            {
                var collection = await EnsureCollectionAsync(group.First().CollectionPath!, cancellationToken);
                var revision = Hash(options.ParserRevision + "\n" + string.Join("\n", group.Select(page => page.Hash).Order()));
                var result = await synchronization.SyncCollectionSnapshotAsync(
                    knowledgeBaseId, collection.Id, schemaId, revision,
                    Documents(group, cancellationToken),
                    // Do not turn a transient upstream outage into document deletion.
                    // A clean acquisition or an explicit manifest/parser change performs
                    // the authoritative delete-missing pass.
                    deleteMissing: acquisition.Warnings.Count == 0,
                    cancellationToken);
                inserted += result.Inserted; updated += result.Updated;
                unchanged += result.Unchanged; deleted += result.Deleted;
            }

            var activeTopics = grouped.Select(group => CollectionKey(group.First().CollectionPath!)).ToHashSet(StringComparer.Ordinal);
            foreach (var old in await storage.GetCollectionsAsync(knowledgeBaseId, cancellationToken))
            {
                if (acquisition.Warnings.Count != 0 || old.ExternalId is null || activeTopics.Contains(old.ExternalId) ||
                    (old.ExternalId.StartsWith("datalinks:", StringComparison.Ordinal) &&
                     pages.Any(page => page.CollectionPath!.Take(page.CollectionPath!.Count - 1)
                        .Select((_, index) => CollectionKey(page.CollectionPath.Take(index + 1)))
                        .Contains(old.ExternalId, StringComparer.Ordinal)))) continue;
                var result = await synchronization.SyncCollectionSnapshotAsync(
                    knowledgeBaseId, old.Id, schemaId,
                    Hash(options.ParserRevision + ":removed:" + old.ExternalId),
                    EmptyDocuments(cancellationToken), deleteMissing: true, cancellationToken);
                deleted += result.Deleted;
            }

            var refreshed = DateTimeOffset.UtcNow;
            var corpusRevision = Hash(options.ParserRevision + "\n" + string.Join("\n", pages.Select(page => page.Hash).Order()));
            status = new("ready", pages.Select(page => page.Source).Distinct().Count(), pages.Count,
                inserted, updated, unchanged, deleted, corpusRevision, refreshed,
                SourceWarnings: acquisition.Warnings);
            auditInputs = inserted + updated;
            if (auditInputs > 0)
            {
                var changed = previous?.DocumentHashes is { Count: > 0 }
                    ? pages.Where(page => !previous.DocumentHashes.TryGetValue(page.ExternalId, out var prior) || prior != page.Hash).ToArray()
                    : pages.ToArray();
                // Snapshot synchronization is authoritative about how many
                // documents were actually embedded. A legacy state file may
                // not yet contain hashes, so do not report unchanged content.
                auditTokens = await CountSourceTokensAsync(
                    changed.Take(auditInputs), cancellationToken);
            }
            await WriteStateAsync(statePath, new StoredState(
                options.ParserRevision, manifestHash, refreshed, status,
                pages.ToDictionary(page => page.ExternalId, page => page.Hash, StringComparer.Ordinal)),
                cancellationToken);
            return status;
        }
        catch (Exception exception)
        {
            auditError = exception.GetType().Name;
            logger.LogError(exception, "Knowledge corpus refresh failed; the last published snapshot remains active");
            status = status with { State = "degraded", LastError = $"{exception.GetType().Name}: {exception.Message}" };
            return status;
        }
        finally
        {
            if (auditRelevant || auditError is not null)
                await SafeAuditAsync(new(
                    auditPurpose, embeddingConfiguration.Mode, AuditModelId(), AuditSpaceId(),
                    AuditDimensions(), auditTokenKind, auditError is null, 1, auditInputs,
                    auditTokens, 0, 0,
                    Stopwatch.GetElapsedTime(auditStarted).TotalMilliseconds,
                    ErrorCode: auditError));
            refreshLock.Release();
        }
    }

    public async Task<object> SearchAsync(KnowledgeSearchApiRequest request, CancellationToken cancellationToken)
    {
        var auditStarted = Stopwatch.GetTimestamp();
        LimitedQuery? limited = null;
        string? auditError = null;
        try
        {
        await InitializeAsync(cancellationToken);
        limited = await LimitQueryAsync(request.Query, Math.Clamp(request.MaxQueryTokens, 32, 4_096), cancellationToken);
        var collections = await storage.GetCollectionsAsync(knowledgeBaseId, cancellationToken);
        var documents = await storage.GetDocumentsAsync(knowledgeBaseId, cancellationToken);
        var selected = SelectCollections(request.Topic, collections);
        var search = new KnowledgeSearchRequest
        {
            KnowledgeBaseId = knowledgeBaseId,
            Mode = selected.Length == 0 ? KnowledgeSearchMode.Smart : KnowledgeSearchMode.Scoped,
            CollectionIds = selected,
            IncludeDescendants = true,
            Top = Math.Clamp(request.Top, 1, 30),
            Include = KnowledgeResultInclude.MatchedChunks,
        };
        if (request.IncludeContent)
        {
            var content = await storeSearch().SearchContentAsync(
                limited.Query, search, Math.Clamp(request.MaxContentTokens, 256, 64_000), cancellationToken);
            return new
            {
                query = limited.Query, original_query = request.Query,
                query_truncated = limited.Truncated, query_tokens = limited.Tokens,
                results = content.Hits.Select(item => ToHit(item, collections)).ToArray(),
                evidence = content.Evidence.Select(item => new
                {
                    document_id = item.DocumentId, field = item.FieldKey,
                    content = item.Text, token_count = item.TokenCount,
                }).ToArray(),
                approximate_tokens = content.ApproximateTokenCount,
            };
        }
        var advanced = KnowledgeSearchQuery.Create(knowledgeBaseId, limited.Query)
            .Add(KnowledgeRetrievalStage.Semantic("semantic-context",
                    KnowledgeSearchField.Title(4), KnowledgeSearchField.Description(3),
                    KnowledgeSearchField.Tags(2), KnowledgeSearchField.Body())
                .Candidates(Math.Max(100, request.Top * 12)))
            .Add(KnowledgeRetrievalStage.Lexical("lexical-identity",
                    KnowledgeSearchField.Title(10), KnowledgeSearchField.Tags(5),
                    KnowledgeSearchField.Description(3), KnowledgeSearchField.Body())
                .Candidates(Math.Max(100, request.Top * 12)).WithWeight(1.35f))
            .UseReciprocalRankFusion(60)
            .IncludeResults(KnowledgeResultInclude.MatchedChunks)
            .Take(Math.Clamp(request.Top, 1, 30));
        if (selected.Length == 0) advanced.Smart(); else advanced.Scoped(selected, includeDescendants: true);
        var primaryHits = await store.SearchAsync(advanced, cancellationToken);
        var rankedLists = new List<(IReadOnlyList<KnowledgeSearchHit> Hits, double Weight)>
        {
            (primaryHits, 1.0),
        };
        var lexicalTokens = LexicalTokens(limited.Query).Take(6).ToArray();
        foreach (var token in lexicalTokens)
        {
            var lexical = KnowledgeSearchQuery.Create(knowledgeBaseId, token)
                .Add(KnowledgeRetrievalStage.Lexical("lexical-term",
                        KnowledgeSearchField.Title(10), KnowledgeSearchField.Tags(5),
                        KnowledgeSearchField.Description(3), KnowledgeSearchField.Body())
                    .Candidates(Math.Max(80, request.Top * 10)))
                .IncludeResults(KnowledgeResultInclude.MatchedChunks)
                .Take(Math.Max(24, request.Top * 4));
            if (selected.Length == 0) lexical.Smart(); else lexical.Scoped(selected, includeDescendants: true);
            rankedLists.Add((await store.SearchAsync(lexical, cancellationToken), 1.5));
        }
        var fused = new Dictionary<Guid, (KnowledgeSearchHit Hit, double Score)>();
        foreach (var (ranked, weight) in rankedLists)
        {
            for (var rank = 0; rank < ranked.Count; rank++)
            {
                var hit = ranked[rank];
                var score = weight / (60d + rank + 1);
                fused[hit.DocumentId] = fused.TryGetValue(hit.DocumentId, out var current)
                    ? (current.Hit, current.Score + score) : (hit, score);
            }
        }
        var normalized = NormalizeTitle(limited.Query);
        var rankedResults = documents.Select(document =>
            {
                var normalizedTitle = NormalizeTitle(document.Title);
                var words = normalizedTitle.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                return new
                {
                    Document = document,
                    Exact = normalizedTitle == normalized,
                    TitleTerms = lexicalTokens.Count(token => words.Contains(token, StringComparer.Ordinal)),
                    Fused = fused.GetValueOrDefault(document.Id),
                };
            })
            .Where(item => item.Exact || item.TitleTerms > 0 || item.Fused.Hit is not null)
            .OrderByDescending(item => item.Exact)
            .ThenByDescending(item => item.TitleTerms)
            .ThenByDescending(item => item.Fused.Score)
            .Take(Math.Clamp(request.Top, 1, 30))
            .Select(item => item.Fused.Hit is not null
                ? ToHit(item.Fused.Hit, collections)
                : ToDocumentHit(item.Document, collections))
            .ToArray();
        return new
        {
            query = limited.Query, original_query = request.Query,
            query_truncated = limited.Truncated, query_tokens = limited.Tokens,
            results = rankedResults,
        };

        IKnowledgeContentSearch storeSearch() => contentSearch
            ?? throw new InvalidOperationException("Knowledge content search is unavailable.");
        }
        catch (Exception exception)
        {
            auditError = exception.GetType().Name;
            throw;
        }
        finally
        {
            if (limited is not null)
                await SafeAuditAsync(new(
                    "wiki_search", embeddingConfiguration.Mode, AuditModelId(), AuditSpaceId(),
                    AuditDimensions(), embeddingConfiguration.Mode == "local"
                        ? "local_tokenizer" : "character_estimate",
                    auditError is null, 1, 1, limited.Tokens, 1, 1,
                    Stopwatch.GetElapsedTime(auditStarted).TotalMilliseconds,
                    ErrorCode: auditError));
        }
    }

    public async Task<string?> QualityCanaryTopTitleAsync(CancellationToken cancellationToken)
    {
        var result = await SearchAsync(new(
            "How do supply crawlers convoy minerals to a base?", Top: 5,
            MaxContentTokens: 512, IncludeContent: false, MaxQueryTokens: 512),
            cancellationToken);
        var json = JsonSerializer.SerializeToElement(result);
        if (!json.TryGetProperty("results", out var results) || results.GetArrayLength() == 0)
            return null;
        var first = results[0];
        return (first.TryGetProperty("title", out var title) ||
                first.TryGetProperty("Title", out title))
            ? title.GetString() : null;
    }

    private IKnowledgeContentSearch? contentSearch;

    public async Task<object?> GetAsync(Guid documentId, CancellationToken cancellationToken)
    {
        await InitializeAsync(cancellationToken);
        var document = await store.GetDocumentAsync(documentId, cancellationToken);
        if (document is null || document.KnowledgeBaseId != knowledgeBaseId) return null;
        var collections = await storage.GetCollectionsAsync(knowledgeBaseId, cancellationToken);
        var collection = collections.FirstOrDefault(item => item.Id == document.CollectionId);
        var topic = collection?.Title ?? "Mechanics";
        return new
        {
            document_id = document.Id,
            external_id = document.ExternalId,
            collection_id = document.CollectionId,
            collection_path = CollectionPath(collection, collections),
            topic,
            document.Title,
            document.Description,
            document.Tags,
            body = document.Values.TryGetValue(KnowledgeSystemFields.Body, out var body) ? body.Text : null,
            source = document.Values.TryGetValue("source", out var source) ? source.Text : null,
            source_hash = document.SourceHash,
        };
    }

    public async Task<IReadOnlyList<object>> TopicsAsync(CancellationToken cancellationToken)
    {
        await InitializeAsync(cancellationToken);
        var documents = await storage.GetDocumentsAsync(knowledgeBaseId, cancellationToken);
        var collections = await storage.GetCollectionsAsync(knowledgeBaseId, cancellationToken);
        var root = collections.FirstOrDefault(item => item.ExternalId == "datalinks:datalinks");
        return collections.Where(item => item.ParentCollectionId == root?.Id)
            .OrderBy(item => item.Title, StringComparer.OrdinalIgnoreCase)
            .Select(item => (object)new
            {
                id = item.Id, item.Title, item.Description, item.Tags,
                document_count = documents.Count(document => IsDescendant(document.CollectionId, item.Id, collections)),
            }).ToArray();
    }

    public async Task<IReadOnlyList<object>> TreeAsync(bool includeDocuments, CancellationToken cancellationToken)
    {
        await InitializeAsync(cancellationToken);
        var collections = await storage.GetCollectionsAsync(knowledgeBaseId, cancellationToken);
        var documents = await storage.GetDocumentsAsync(knowledgeBaseId, cancellationToken);
        var active = new HashSet<Guid>();
        foreach (var document in documents)
        {
            var current = collections.FirstOrDefault(item => item.Id == document.CollectionId);
            while (current is not null && active.Add(current.Id))
                current = current.ParentCollectionId is { } parent
                    ? collections.FirstOrDefault(item => item.Id == parent) : null;
        }
        return collections.Where(item => active.Contains(item.Id) && item.ExternalId?.StartsWith("datalinks:", StringComparison.Ordinal) == true)
            .OrderBy(item => string.Join('\u001f', CollectionPath(item, collections)), StringComparer.OrdinalIgnoreCase)
            .Select(item => (object)new
            {
                id = item.Id, parent_id = item.ParentCollectionId,
                item.Title, item.Description, item.Tags,
                path = CollectionPath(item, collections),
                direct_document_count = documents.Count(document => document.CollectionId == item.Id),
                document_count = documents.Count(document => IsDescendant(document.CollectionId, item.Id, collections)),
                documents = includeDocuments
                    ? documents.Where(document => document.CollectionId == item.Id)
                        .OrderBy(document => document.Title, StringComparer.OrdinalIgnoreCase)
                        .Select(document => new
                        {
                            document_id = document.Id,
                            document.Title,
                            document.Description,
                        }).ToArray()
                    : null,
            }).ToArray();
    }

    public async Task<IReadOnlyList<object>?> CollectionDocumentsAsync(Guid collectionId, CancellationToken cancellationToken)
    {
        await InitializeAsync(cancellationToken);
        var collections = await storage.GetCollectionsAsync(knowledgeBaseId, cancellationToken);
        var collection = collections.FirstOrDefault(item => item.Id == collectionId);
        if (collection is null || collection.ExternalId?.StartsWith("datalinks:", StringComparison.Ordinal) != true)
            return null;
        var documents = await storage.GetDocumentsAsync(knowledgeBaseId, cancellationToken);
        return documents.Where(document => document.CollectionId == collectionId)
            .OrderBy(document => document.Title, StringComparer.OrdinalIgnoreCase)
            .Select(document => (object)new
            {
                document_id = document.Id, collection_id = collection.Id,
                collection_path = CollectionPath(collection, collections), topic = collection.Title,
                document.Title, document.Description, document.Tags,
                source = document.Values.TryGetValue("source", out var source) ? source.Text : null,
            }).ToArray();
    }

    public void AttachContentSearch(IKnowledgeContentSearch search) => contentSearch = search;

    private async Task<LimitedQuery> LimitQueryAsync(string query, int maxTokens, CancellationToken cancellationToken)
    {
        query = query.Trim();
        var embedding = services.GetService<ITextEmbeddingService>();
        if (embedding is null)
        {
            var maximumCharacters = maxTokens * 4;
            return query.Length <= maximumCharacters
                ? new(query, Math.Max(1, query.Length / 4), false)
                : new(query[..maximumCharacters].TrimEnd(), maxTokens, true);
        }
        var count = await embedding.CountQueryTokensAsync(query, cancellationToken);
        if (count.InputTokenCount <= maxTokens) return new(query, count.InputTokenCount, false);
        var low = 1; var high = query.Length;
        while (low < high)
        {
            var midpoint = low + (high - low + 1) / 2;
            var candidate = await embedding.CountQueryTokensAsync(query[..midpoint], cancellationToken);
            if (candidate.InputTokenCount <= maxTokens) low = midpoint; else high = midpoint - 1;
        }
        var limited = query[..low].TrimEnd();
        var limitedCount = await embedding.CountQueryTokensAsync(limited, cancellationToken);
        return new(limited, limitedCount.InputTokenCount, true);
    }

    private async Task<long> CountSourceTokensAsync(
        IEnumerable<CorpusPage> pages, CancellationToken cancellationToken)
    {
        var embedding = services.GetService<ITextEmbeddingService>();
        long total = 0;
        foreach (var page in pages)
        {
            var text = string.Join('\n', new[]
            {
                page.Title, page.Description, string.Join(' ', page.Tags), page.Body,
            });
            if (embedding is null) total += Math.Max(1, (text.Length + 3) / 4);
            else total += (await embedding.CountQueryTokensAsync(text, cancellationToken)).InputTokenCount;
        }
        return total;
    }

    private async Task SafeAuditAsync(EmbeddingAuditRecord record)
    {
        try { await embeddingAudit.RecordAsync(record, CancellationToken.None); }
        catch (Exception exception)
        {
            logger.LogWarning(exception, "Embedding audit write failed for {Purpose}", record.Purpose);
        }
    }

    private string AuditModelId() => embeddingConfiguration.Mode == "local"
        ? services.GetService<ITextEmbeddingService>()?.ModelInfo?.ModelId ?? "smacx-local-embeddings"
        : embeddingConfiguration.ModelId ?? "external";
    private string AuditSpaceId() => embeddingConfiguration.Mode == "local"
        ? services.GetService<ITextEmbeddingService>()?.ModelInfo?.EmbeddingSpaceFingerprint ?? "local-onnx"
        : embeddingConfiguration.SpaceId ?? "external";
    private int AuditDimensions() => embeddingConfiguration.Mode == "local"
        ? services.GetService<ITextEmbeddingService>()?.ModelInfo?.Dimensions ?? 0
        : embeddingConfiguration.Dimensions;

    private static bool IsDescendant(Guid candidateId, Guid ancestorId, IReadOnlyList<KnowledgeCollectionRecord> collections)
    {
        var current = collections.FirstOrDefault(item => item.Id == candidateId);
        while (current is not null)
        {
            if (current.Id == ancestorId) return true;
            current = current.ParentCollectionId is { } parent
                ? collections.FirstOrDefault(item => item.Id == parent) : null;
        }
        return false;
    }

    private static IReadOnlyList<string> CollectionPath(KnowledgeCollectionRecord? collection, IReadOnlyList<KnowledgeCollectionRecord> collections)
    {
        var path = new List<string>();
        while (collection is not null)
        {
            path.Add(collection.Title);
            collection = collection.ParentCollectionId is { } parent
                ? collections.FirstOrDefault(item => item.Id == parent) : null;
        }
        path.Reverse();
        return path;
    }

    private async Task InitializeAsync(CancellationToken cancellationToken)
    {
        if (knowledgeBaseId != Guid.Empty) return;
        await store.InitializeAsync(cancellationToken);
        var knowledgeBase = await store.GetOrCreateKnowledgeBaseAsync(
            "Sid Meier's Alpha Centauri: Alien Crossfire rules", "smacx-rules", cancellationToken);
        knowledgeBase = await catalog.UpsertKnowledgeBaseAsync(knowledgeBase with
        {
            Description = "A locally built factual mechanics library for Sid Meier's Alpha Centauri and Alien Crossfire, organized into semantically meaningful gameplay domains.",
            Tags = ["smacx", "alien-crossfire", "game-rules", "datalinks"],
        }, cancellationToken);
        knowledgeBaseId = knowledgeBase.Id;
        var schema = new KnowledgeSchemaBuilder("smacx-rule-page", "SMACX rule page",
                new Guid("939de398-0ad4-47ca-9cb9-10d596a681d1"))
            .SetSemanticWeight(KnowledgeSystemFields.Title, 24)
            .SetSemanticWeight(KnowledgeSystemFields.Description, 16)
            .SetSemanticWeight(KnowledgeSystemFields.Tags, 15)
            .Text(KnowledgeSystemFields.Body, 45, SemanticMode.Chunked, required: true)
            .Text("source", required: true, filterable: true)
            .Build();
        schemaId = (await store.EnsureSchemaAsync(schema, cancellationToken)).Id;
    }

    private async Task<KnowledgeCollectionRecord> EnsureCollectionAsync(IReadOnlyList<string> path, CancellationToken cancellationToken)
    {
        KnowledgeCollectionRecord? parent = null;
        for (var index = 0; index < path.Count; index++)
        {
            var partial = path.Take(index + 1).ToArray();
            var item = await store.GetOrCreateCollectionAsync(
                knowledgeBaseId, path[index], parentCollectionId: parent?.Id,
                defaultSchemaId: schemaId, externalId: CollectionKey(partial), cancellationToken: cancellationToken);
            var metadata = CorpusTaxonomy.MetadataFor(partial);
            parent = await catalog.UpsertCollectionAsync(item with
            {
                ParentCollectionId = index == 0 ? null : parent?.Id,
                Description = metadata.Description,
                Tags = metadata.Tags,
            }, cancellationToken);
        }
        return parent!;
    }

    private async Task<CorpusAcquisition> AcquireAsync(byte[] manifestBytes, CancellationToken cancellationToken)
    {
        using var manifest = JsonDocument.Parse(manifestBytes);
        var sources = manifest.RootElement.GetProperty("sources").EnumerateArray().ToArray();
        var pages = new List<CorpusPage>();
        var warnings = new List<string>();
        var client = httpFactory.CreateClient("wiki");
        foreach (var source in sources)
        {
            var canonical = source.GetProperty("canonical_url").GetString()!;
            var archive = source.TryGetProperty("archive_url", out var archived) ? archived.GetString() : null;
            try
            {
                var html = await DownloadAsync(client, canonical, archive, cancellationToken);
                var page = HtmlPage(canonical, html);
                if (page is not null) pages.Add(page);
                else warnings.Add($"unusable:{Hash(canonical)[..12]}");
            }
            catch (HttpRequestException)
            {
                // A single dead source must not discard the last good snapshot.
                warnings.Add($"unavailable:{Hash(canonical)[..12]}");
            }
        }
        if (!string.IsNullOrWhiteSpace(options.GameRoot) && Directory.Exists(options.GameRoot))
            pages.AddRange(await GamePagesAsync(options.GameRoot, cancellationToken));
        if (pages.Count == 0) throw new InvalidOperationException("No reference sources could be acquired.");
        return new CorpusAcquisition(
            pages.GroupBy(page => page.ExternalId, StringComparer.Ordinal).Select(group => group.Last()).ToList(),
            warnings);
    }

    private static async Task<string> DownloadAsync(HttpClient client, string canonical, string? archive, CancellationToken cancellationToken)
    {
        foreach (var address in new[] { canonical, archive }.Where(value => !string.IsNullOrWhiteSpace(value)))
        {
            try
            {
                using var response = await client.GetAsync(address, cancellationToken);
                if (!response.IsSuccessStatusCode) continue;
                var content = await response.Content.ReadAsStringAsync(cancellationToken);
                if (content.Length is > 500 and < 8_000_000) return content;
            }
            catch (HttpRequestException) { }
        }
        throw new HttpRequestException($"Both canonical and archived sources were unavailable: {canonical}");
    }

    private CorpusPage? HtmlPage(string source, string html)
    {
        var document = new HtmlDocument();
        document.LoadHtml(html);
        var root = document.DocumentNode.SelectSingleNode("//*[@id='mw-content-text']//*[contains(@class,'mw-parser-output')]")
            ?? document.DocumentNode.SelectSingleNode("//*[@id='mw-content-text']")
            // Older, otherwise valid Internet Archive snapshots predate the
            // mw-content-text wrapper used by current MediaWiki releases.
            ?? document.DocumentNode.SelectSingleNode("//*[@id='bodyContent']");
        if (root is null) return null;
        RemoveGuideSections(root);
        foreach (var node in root.SelectNodes(".//script|.//style|.//noscript|.//form|.//nav|.//*[@id='contentSub' or @id='siteSub' or @id='catlinks']|.//*[contains(@class,'navbox') or contains(@class,'nav_box') or contains(@class,'NavFrame') or contains(@class,'toc') or contains(@class,'mw-editsection') or contains(@class,'noprint') or contains(@class,'printfooter') or contains(@class,'metadata') or contains(@class,'ambox') or contains(@class,'catlinks')]|.//sup") ?? Enumerable.Empty<HtmlNode>())
            node.Remove();
        var title = document.DocumentNode.SelectSingleNode("//*[@id='firstHeading']")?.InnerText
            ?? document.DocumentNode.SelectSingleNode("//*[contains(concat(' ', normalize-space(@class), ' '), ' firstHeading ')]")?.InnerText;
        title = WebUtility.HtmlDecode(title ?? source.Split('/').Last()).Trim();
        var markdown = CleanMarkdown(ToMarkdown(root));
        if (markdown.Length < 120) return null;
        var shortTitle = title.Replace("Sid Meier's Alpha Centauri/", "", StringComparison.OrdinalIgnoreCase);
        var topic = TopicFor(shortTitle);
        return new CorpusPage(
            ExternalId: "wiki:" + Hash(source)[..20],
            Topic: topic,
            Title: shortTitle,
            Description: FirstSentence(markdown),
            Body: markdown,
            Tags: ["wiki", TopicKey(topic), TopicKey(shortTitle)],
            // Canonical/archive addresses remain in the local source manifest and corpus
            // state, not in text returned to the model on every retrieval.
            Source: "wiki:" + Hash(source)[..20],
            Hash: Hash(options.ParserRevision + "\n" + source + "\n" + markdown));
    }

    private async Task<IReadOnlyList<CorpusPage>> GamePagesAsync(string root, CancellationToken cancellationToken)
    {
        var files = new List<string>();
        files.Add(File.Exists(Path.Combine(root, "conceptsx.txt")) ? "conceptsx.txt" : "concepts.txt");
        files.Add(File.Exists(Path.Combine(root, "helpx.txt")) ? "helpx.txt" : "help.txt");
        files.AddRange(["alphax.txt", "TECHSHORTS.txt", "TECHLONGS.TXT"]);
        var result = new List<CorpusPage>();
        var indexedNames = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var alphaPath = Path.Combine(root, "alphax.txt");
        if (File.Exists(alphaPath))
        {
            Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
            var alpha = await File.ReadAllTextAsync(alphaPath, Encoding.GetEncoding(1252), cancellationToken);
            indexedNames = ExtractAlphaNames(alpha);
        }
        foreach (var file in files)
        {
            var path = Path.Combine(root, file);
            if (!File.Exists(path)) continue;
            // The shipped Windows game data is effectively Windows-1252, not
            // ISO-8859-1. Decode its punctuation before turning it into clean
            // model-facing Markdown.
            Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
            var text = await File.ReadAllTextAsync(path, Encoding.GetEncoding(1252), cancellationToken);
            foreach (var section in ParseGameSections(file, text, indexedNames)) result.Add(section);
        }
        return result;
    }

    private IEnumerable<CorpusPage> ParseGameSections(
        string file, string text, IReadOnlyDictionary<string, string> indexedNames)
    {
        var lines = text.Replace("\r\n", "\n").Split('\n');
        var indexedTitles = ExtractIndexedTitles(lines);
        var current = new StringBuilder();
        var key = "preamble";
        string? title = null;
        string? pendingTitle = null;
        for (var index = 0; index < lines.Length; index++)
        {
            var line = lines[index].TrimEnd();
            if (line.StartsWith("#;", StringComparison.Ordinal) || line.StartsWith("##", StringComparison.Ordinal))
            {
                pendingTitle = line.TrimStart('#', ';').Trim();
                continue;
            }
            if (line.StartsWith('#') && line.Length > 1)
            {
                var marker = line.TrimStart('#').Trim();
                var next = lines.Skip(index + 1)
                    .Select(candidate => candidate.Trim())
                    .FirstOrDefault(candidate => candidate.Length > 0 && !candidate.StartsWith(';'));
                // A human-readable heading immediately followed by another
                // record marker names that record (several shipped data files
                // use this instead of the #;Name convention).
                if (next?.StartsWith('#') == true)
                {
                    pendingTitle = marker;
                    continue;
                }
                if (current.Length > 20 && key is not "TITLES" and not "ADVTITLES")
                    yield return GamePage(file, key, title ?? indexedNames.GetValueOrDefault(key), current.ToString());
                key = marker;
                title = pendingTitle ?? indexedTitles.GetValueOrDefault(marker);
                pendingTitle = null;
                current.Clear();
                continue;
            }
            if (line.StartsWith(";") || line.StartsWith("//")) continue;
            current.AppendLine(line);
        }
        if (current.Length > 20 && key is not "TITLES" and not "ADVTITLES")
            yield return GamePage(file, key, title ?? indexedNames.GetValueOrDefault(key), current.ToString());
    }

    private static Dictionary<string, string> ExtractAlphaNames(string text)
    {
        var lines = text.Replace("\r\n", "\n").Split('\n');
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        var sections = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["UNITS"] = "UNITDESC", ["CHASSIS"] = "CHASSISDESC", ["WEAPONS"] = "WEAPONDESC",
            ["DEFENSES"] = "ARMORDESC", ["ABILITIES"] = "ABILDESC",
        };
        foreach (var pair in sections)
        {
            var start = Array.FindIndex(lines, line => line.Trim().Equals("#" + pair.Key, StringComparison.OrdinalIgnoreCase));
            if (start < 0) continue;
            var index = 0;
            for (var cursor = start + 1; cursor < lines.Length; cursor++)
            {
                var line = lines[cursor].Trim();
                if (line.StartsWith('#')) break;
                if (line.Length == 0 || line.StartsWith(';') || int.TryParse(line, out _)) continue;
                var comma = line.IndexOf(',');
                if (comma <= 0) continue;
                var name = line[..comma].Trim().TrimStart('*');
                if (name.Length == 0) continue;
                result[pair.Value + index++] = name;
            }
        }
        result["ARMORDESC"] = "Armor";
        result["REACTORDESC"] = "Reactors";
        return result;
    }

    private static Dictionary<string, string> ExtractIndexedTitles(string[] lines)
    {
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (var index = 0; index < lines.Length; index++)
        {
            var marker = lines[index].Trim();
            var prefix = marker switch { "#TITLES" => "CONCEPT", "#ADVTITLES" => "ADVCONCEPT", _ => null };
            if (prefix is null) continue;
            var item = 0;
            for (index++; index < lines.Length; index++)
            {
                var candidate = lines[index].Trim();
                if (candidate.StartsWith('#')) { index--; break; }
                if (candidate.Length == 0 || candidate.StartsWith(';')) continue;
                result[$"{prefix}{item++}"] = candidate;
            }
        }
        return result;
    }

    private CorpusPage GamePage(string file, string key, string? displayTitle, string raw)
    {
        var title = FriendlyTitle(key, displayTitle, raw);
        var body = CleanMarkdown($"# {title}\n\n{CleanGameMarkup(raw)}");
        var topic = TopicFor(file + " " + key + " " + title);
        return new CorpusPage(
            "game:" + TopicKey(file) + ":" + Hash(key)[..16], topic, title,
            FirstSentence(body), body, ["local-game", TopicKey(topic), TopicKey(file)],
            "installed-game:" + file,
            Hash(options.ParserRevision + "\n" + file + "\n" + key + "\n" + body),
            SourceFile: file, SourceKey: key);
    }

    private static string ToMarkdown(HtmlNode root)
    {
        var builder = new StringBuilder();
        void Visit(HtmlNode node, int depth = 0)
        {
            if (node.NodeType == HtmlNodeType.Text)
            {
                var text = WebUtility.HtmlDecode(node.InnerText);
                if (!string.IsNullOrWhiteSpace(text)) builder.Append(Regex.Replace(text, @"\s+", " "));
                return;
            }
            var name = node.Name.ToLowerInvariant();
            if (name is "h1" or "h2" or "h3" or "h4" or "h5" or "h6")
            {
                builder.Append("\n\n").Append(new string('#', int.Parse(name[1..]))).Append(' ')
                    .Append(WebUtility.HtmlDecode(node.InnerText.Trim())).Append("\n\n"); return;
            }
            if (name == "br") { builder.Append('\n'); return; }
            if (name == "li") builder.Append("\n- ");
            if (name is "p" or "div" or "section" or "article" or "table" or "tr" or "dl") builder.Append("\n\n");
            if (name is "td" or "th") builder.Append(" | ");
            if (name is "strong" or "b") builder.Append("**");
            if (name is "em" or "i") builder.Append('*');
            foreach (var child in node.ChildNodes) Visit(child, depth + 1);
            if (name is "strong" or "b") builder.Append("**");
            if (name is "em" or "i") builder.Append('*');
            if (name is "p" or "div" or "section" or "article" or "table" or "tr" or "ul" or "ol" or "dl") builder.Append("\n\n");
        }
        Visit(root);
        return builder.ToString();
    }

    private static void RemoveGuideSections(HtmlNode root)
    {
        var excluded = new Regex(@"\b(strategy|walkthrough|tips?|cheats?|exploits?|recommended|opening moves?)\b",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        var headings = root.SelectNodes(".//h1|.//h2|.//h3|.//h4|.//h5|.//h6")?.ToArray()
            ?? Array.Empty<HtmlNode>();
        foreach (var heading in headings)
        {
            if (heading.ParentNode is null || !excluded.IsMatch(WebUtility.HtmlDecode(heading.InnerText))) continue;
            var level = int.Parse(heading.Name[1..]);
            var current = heading.NextSibling;
            heading.Remove();
            while (current is not null)
            {
                var next = current.NextSibling;
                if (current.Name.Length == 2 && current.Name[0] == 'h' && char.IsDigit(current.Name[1]) &&
                    int.Parse(current.Name[1..]) <= level) break;
                current.Remove(); current = next;
            }
        }
    }

    private static string CleanMarkdown(string value)
    {
        value = Regex.Replace(value, @"\[[0-9]+\]", "");
        value = Regex.Replace(value, @"https?://\S+", "");
        value = Regex.Replace(value, @"[ \t]+", " ");
        value = Regex.Replace(value, @" *\n *", "\n");
        value = Regex.Replace(value, @"\n{3,}", "\n\n");
        return value.Trim();
    }

    private static string CleanGameMarkup(string value)
    {
        // Datalinks text uses compact presentation tokens understood by the
        // 1999 client. Preserve their human meaning without spending agent
        // context on renderer syntax.
        value = Regex.Replace(value, @"\$LINK<([^=>]+)(?:=[^>]*)?>", "$1",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        value = Regex.Replace(value, @"\$[A-Z][A-Z0-9_]*<([^>]*)>", "$1",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        value = Regex.Replace(value, @"\{([^{}\r\n]+)\}", "$1");
        value = Regex.Replace(value, @"(?m)^\^+", "");
        return value;
    }

    private static string TopicFor(string value)
    {
        var lowered = value.ToLowerInvariant();
        if (lowered.Contains("tech")) return "Research and technology";
        if (lowered.Contains("faction")) return "Factions and leaders";
        if (lowered.Contains("unit") || lowered.Contains("weapon") || lowered.Contains("shield") || lowered.Contains("reactor") || lowered.Contains("ability")) return "Units and combat";
        if (lowered.Contains("terra") || lowered.Contains("world") || lowered.Contains("map")) return "Planet and terraforming";
        if (lowered.Contains("diplom") || lowered.Contains("social") || lowered.Contains("helpsoc") ||
            lowered.Contains("council") || lowered.Contains("governor") || lowered.Contains("treaty") ||
            lowered.Contains("pact") || lowered.Contains("truce") || lowered.Contains("vendetta") ||
            lowered.Contains("probe") || lowered.Contains("atrocit") || lowered.Contains("integrity") ||
            lowered.Contains("commerce")) return "Diplomacy and society";
        if (lowered.Contains("facilit") || lowered.Contains("project") || lowered.Contains("base") || lowered.Contains("citizen")) return "Bases, facilities, and projects";
        return "Core rules and terminology";
    }

    private static object ToHit(KnowledgeSearchHit hit, IReadOnlyList<KnowledgeCollectionRecord> collections) => new
    {
        document_id = hit.DocumentId, collection_id = hit.CollectionId,
        collection_path = CollectionPath(collections.FirstOrDefault(item => item.Id == hit.CollectionId), collections),
        topic = collections.FirstOrDefault(item => item.Id == hit.CollectionId)?.Title ?? "Mechanics",
        hit.Title, hit.Description, hit.Tags, score = hit.Score,
        matches = hit.Matches.Select(match => new
        {
            field = match.FieldKey, score = match.AdjustedSimilarity,
            token_count = match.TokenCount,
        }).ToArray(),
    };
    private static object ToDocumentHit(KnowledgeDocumentRecord document, IReadOnlyList<KnowledgeCollectionRecord> collections) => new
    {
        document_id = document.Id, collection_id = document.CollectionId,
        collection_path = CollectionPath(collections.FirstOrDefault(item => item.Id == document.CollectionId), collections),
        topic = collections.FirstOrDefault(item => item.Id == document.CollectionId)?.Title ?? "Mechanics",
        document.Title, document.Description, document.Tags, score = 1d,
        matches = Array.Empty<object>(),
    };

    private static Guid[] SelectCollections(
        string? requested,
        IReadOnlyList<KnowledgeCollectionRecord> collections)
    {
        if (string.IsNullOrWhiteSpace(requested)) return [];
        requested = requested.Trim();
        if (Guid.TryParse(requested, out var id) && collections.Any(item => item.Id == id)) return [id];
        var normalizedPath = requested.Replace(" > ", " / ", StringComparison.Ordinal).Trim('/');
        return collections.Where(item =>
                string.Equals(item.Title, requested, StringComparison.OrdinalIgnoreCase) ||
                string.Equals(item.ExternalId, requested, StringComparison.OrdinalIgnoreCase) ||
                string.Equals(item.ExternalId, TopicKey(requested), StringComparison.OrdinalIgnoreCase) ||
                string.Equals(string.Join(" / ", CollectionPath(item, collections)), normalizedPath,
                    StringComparison.OrdinalIgnoreCase))
            .Select(item => item.Id).ToArray();
    }

    private async IAsyncEnumerable<ExternalKnowledgeDocumentInput> Documents(
        IEnumerable<CorpusPage> pages,
        [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken)
    {
        foreach (var page in pages)
        {
            cancellationToken.ThrowIfCancellationRequested();
            yield return new ExternalKnowledgeDocumentInput
            {
                ExternalId = page.ExternalId,
                Title = page.Title,
                Description = page.Description,
                Tags = page.Tags,
                Values = new Dictionary<string, KnowledgeValue>
                {
                    [KnowledgeSystemFields.Body] = KnowledgeValue.From(page.Body),
                    ["source"] = KnowledgeValue.From(page.Source),
                },
            };
            await Task.Yield();
        }
    }

    private static async IAsyncEnumerable<ExternalKnowledgeDocumentInput> EmptyDocuments(
        [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken)
    { cancellationToken.ThrowIfCancellationRequested(); await Task.CompletedTask; yield break; }

    private static string FirstSentence(string value)
    {
        var plain = Regex.Replace(value, @"[#*|]", "");
        var end = plain.IndexOfAny(['.', '!', '?', '\n']);
        return (end >= 40 ? plain[..(end + 1)] : plain[..Math.Min(plain.Length, 220)]).Trim();
    }
    private static string Humanize(string value) => Regex.Replace(value.Replace('_', ' ').Trim(), "(?<=[a-z])(?=[A-Z])", " ");
    private static string FriendlyTitle(string key, string? displayTitle, string raw)
    {
        var candidate = displayTitle?.Trim();
        if (key.Equals("CONCEPT44", StringComparison.OrdinalIgnoreCase))
            candidate = "Economy energy allocation";
        else if (Regex.Match(key, @"^HELPEFFECT(\d+)$", RegexOptions.IgnoreCase) is { Success: true } effect &&
                 int.TryParse(effect.Groups[1].Value, out var effectIndex))
            candidate = SocialEffectTitle(effectIndex);
        else if (key.StartsWith("HELPTERRA", StringComparison.OrdinalIgnoreCase))
        {
            candidate = Regex.Match(raw, @"\{([^}\r\n]+)\}").Groups[1].Value;
            if (candidate.Length == 0) candidate = TerraformTitle(displayTitle);
            if (candidate.Length == 0)
                candidate = Regex.Match(raw, @"\$LINK<([^=>]+)", RegexOptions.IgnoreCase).Groups[1].Value;
        }
        candidate = Regex.Replace(candidate ?? string.Empty, @"\s+\d+\s*$", "").Trim();
        if (candidate.Length == 0)
        {
            candidate = key.ToUpperInvariant() switch
            {
                "RULES" => "Core game rules", "DIFF" => "Difficulty levels", "TIMECONTROLS" => "Time controls",
                "TERRAIN" => "Terrain definitions", "RESOURCEINFO" => "Resource production", "RESOURCES" => "Resource types",
                "WORLDBUILDER" => "World generation rules", "WORLDSIZE" => "World sizes", "NATURAL" => "Native life",
                "TECHNOLOGY" => "Research configuration", "CHASSIS" => "Chassis configuration", "REACTORS" => "Reactor configuration",
                "WEAPONS" => "Weapon configuration", "DEFENSES" => "Armor configuration", "ABILITIES" => "Special ability configuration",
                "MORALE" => "Morale levels", "DEFENSEMODES" => "Defensive combat modes", "OFFENSEMODES" => "Offensive combat modes",
                "UNITS" => "Predefined unit configuration", "FACILITIES" => "Facility and project configuration",
                "ORDERS" => "Unit orders", "COMPASS" => "Map directions", "PLANS" => "Unit strategic roles", "TRIAD" => "Unit domains",
                "ENERGY" => "Energy rules", "CITIZENS" => "Citizen types", "SOCIO" => "Social engineering categories",
                "SOCECONOMY" => "Economy social effect configuration", "SOCEFFIC" => "Efficiency social effect configuration",
                "SOCSUPPORT" => "Support social effect configuration", "SOCTALENT" => "Talent social effect configuration",
                "SOCMORALE" => "Morale social effect configuration", "SOCPOLICE" => "Police social effect configuration",
                "SOCGROWTH" => "Growth social effect configuration", "SOCPLANET" => "Planet social effect configuration",
                "SOCPROBE" => "Probe social effect configuration", "SOCINDUSTRY" => "Industry social effect configuration",
                "SOCRESEARCH" => "Research social effect configuration", "REPUTE" => "Reputation levels",
                "PROPOSALS" => "Planetary Council proposals", "FACTIONS" => "Original faction definitions",
                "NEWFACTIONS" => "Alien Crossfire faction definitions", "CUSTOMFACTIONS" => "Custom faction rules",
                "MANDATE" => "Faction mandates", "MOOD" => "Faction diplomatic moods", "MIGHT" => "Faction strength comparisons",
                "BONUSNAMES" => "Faction bonus terminology",
                _ => Humanize(key),
            };
        }
        if (candidate.All(character => !char.IsLetter(character) || char.IsUpper(character)))
            candidate = string.Join(' ', candidate.ToLowerInvariant().Split(' ', StringSplitOptions.RemoveEmptyEntries)
                .Select(word => char.ToUpperInvariant(word[0]) + word[1..]));
        else if (candidate.Length > 0 && char.IsLower(candidate[0]))
            candidate = char.ToUpperInvariant(candidate[0]) + candidate[1..];
        return candidate;
    }
    private static string SocialEffectTitle(int index) => index switch
    {
        0 => "Economy social effect", 1 => "Efficiency social effect", 2 => "Support social effect",
        3 => "Talent social effect", 4 => "Morale social effect", 5 => "Police social effect",
        6 => "Growth social effect", 7 => "Planet social effect", 8 => "Probe social effect",
        9 => "Industry social effect", 10 => "Research social effect", _ => $"Social effect {index}",
    };
    private static string TerraformTitle(string? value) => value?.Trim().ToUpperInvariant() switch
    {
        "FARM" => "Build farm", "SOIL" => "Enrich soil", "MINE" => "Build mine",
        "SOLAR" => "Build solar collector", "FOREST" => "Plant forest", "ROAD" => "Build road",
        "MAGTUBE" => "Build mag tube", "BUNKER" => "Build bunker", "AIRBASE" => "Build airbase",
        "SENSOR" => "Build sensor array", "REMOVEFUNGUS" => "Remove fungus",
        "MAKEFUNGUS" => "Cultivate fungus", "CONDENSER" => "Build condenser",
        "BOREHOLE" => "Drill thermal borehole", "AQUIFER" => "Drill aquifer",
        "RAISE" => "Terraform raise", "LOWER" => "Terraform lower", "LEVEL" => "Terraform level",
        "MIRROR" => "Launch orbital solar mirror", null or "" => "",
        _ => Humanize(value!),
    };
    private static string TopicKey(string value) => Regex.Replace(value.ToLowerInvariant(), @"[^a-z0-9]+", "-").Trim('-');
    private static string NormalizeTitle(string value) => Regex.Replace(value.ToLowerInvariant(), @"[^a-z0-9]+", " ").Trim();
    private static IEnumerable<string> LexicalTokens(string query)
    {
        var ignored = new HashSet<string>(StringComparer.Ordinal)
        {
            "and", "are", "can", "did", "does", "for", "from", "how", "into", "the", "this", "what", "when", "where", "which", "who", "why", "with", "work",
        };
        return Regex.Matches(query.ToLowerInvariant(), @"[a-z0-9]+")
            .Select(match => match.Value).Where(token => token.Length >= 3 && !ignored.Contains(token))
            .Select(token => token.EndsWith("ies", StringComparison.Ordinal) && token.Length > 4
                ? token[..^3] + "y"
                : token.EndsWith('s') && token.Length > 4 &&
                  !token.EndsWith("us", StringComparison.Ordinal) && !token.EndsWith("ss", StringComparison.Ordinal)
                    ? token[..^1] : token)
            .Distinct(StringComparer.Ordinal);
    }
    private static string CollectionKey(IEnumerable<string> path) => "datalinks:" + string.Join('/', path.Select(TopicKey));
    private static string Hash(string value) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(value)));
    private static Task<StoredState?> ReadStateAsync(string path, CancellationToken cancellationToken) =>
        !File.Exists(path) ? Task.FromResult<StoredState?>(null) : ReadAsync(path, cancellationToken);
    private static async Task<StoredState?> ReadAsync(string path, CancellationToken cancellationToken)
    {
        try { return JsonSerializer.Deserialize<StoredState>(await File.ReadAllTextAsync(path, cancellationToken)); }
        catch { return null; }
    }
    private static async Task WriteStateAsync(string path, StoredState value, CancellationToken cancellationToken)
    {
        var temporary = path + ".tmp";
        await File.WriteAllTextAsync(temporary, JsonSerializer.Serialize(value), cancellationToken);
        File.Move(temporary, path, true);
    }

    private sealed record CorpusAcquisition(List<CorpusPage> Pages, IReadOnlyList<string> Warnings);
    private sealed record StoredState(
        string ParserRevision, string ManifestHash, DateTimeOffset RefreshedAt,
        CorpusStatus Status, Dictionary<string, string>? DocumentHashes = null);
    private sealed record LimitedQuery(string Query, int Tokens, bool Truncated);
}

public sealed class KnowledgeCorpusWorker(
    KnowledgeCorpus corpus,
    IKnowledgeContentSearch contentSearch,
    EmbeddingQualityAuditor quality,
    KnowledgeRuntimeOptions options,
    ILogger<KnowledgeCorpusWorker> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        corpus.AttachContentSearch(contentSearch);
        while (!stoppingToken.IsCancellationRequested)
        {
            try
            {
                var refreshed = await corpus.RefreshAsync(force: false, stoppingToken);
                if (refreshed.State == "ready")
                    await quality.RunOnceAsync(
                        await corpus.QualityCanaryTopTitleAsync(stoppingToken), stoppingToken);
            }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception exception) { logger.LogError(exception, "Unexpected knowledge refresh worker failure"); }
            try { await Task.Delay(options.RefreshInterval, stoppingToken); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
        }
    }
}
