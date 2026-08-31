using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using HtmlAgilityPack;
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
            await InitializeAsync(cancellationToken);
            var acquisition = await AcquireAsync(manifestBytes, cancellationToken);
            var pages = acquisition.Pages;
            var grouped = pages.GroupBy(page => page.Topic, StringComparer.OrdinalIgnoreCase).ToArray();
            var inserted = 0; var updated = 0; var unchanged = 0; var deleted = 0;
            foreach (var group in grouped)
            {
                var collection = await EnsureCollectionAsync(group.Key, cancellationToken);
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

            var activeTopics = grouped.Select(group => TopicKey(group.Key)).ToHashSet(StringComparer.Ordinal);
            foreach (var old in await storage.GetCollectionsAsync(knowledgeBaseId, cancellationToken))
            {
                if (acquisition.Warnings.Count != 0 || old.ExternalId is null || activeTopics.Contains(old.ExternalId)) continue;
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
            await WriteStateAsync(statePath, new StoredState(options.ParserRevision, manifestHash, refreshed, status), cancellationToken);
            return status;
        }
        catch (Exception exception)
        {
            logger.LogError(exception, "Knowledge corpus refresh failed; the last published snapshot remains active");
            status = status with { State = "degraded", LastError = $"{exception.GetType().Name}: {exception.Message}" };
            return status;
        }
        finally
        {
            refreshLock.Release();
        }
    }

    public async Task<object> SearchAsync(KnowledgeSearchApiRequest request, CancellationToken cancellationToken)
    {
        await InitializeAsync(cancellationToken);
        var collections = await storage.GetCollectionsAsync(knowledgeBaseId, cancellationToken);
        var collectionTitles = collections.ToDictionary(item => item.Id, item => item.Title);
        var selected = string.IsNullOrWhiteSpace(request.Topic)
            ? Array.Empty<Guid>()
            : collections.Where(item => string.Equals(item.Title, request.Topic, StringComparison.OrdinalIgnoreCase)
                || string.Equals(item.ExternalId, TopicKey(request.Topic), StringComparison.Ordinal))
                .Select(item => item.Id).ToArray();
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
                request.Query, search, Math.Clamp(request.MaxContentTokens, 256, 64_000), cancellationToken);
            return new
            {
                query = request.Query,
                results = content.Hits.Select(item => ToHit(item, collectionTitles)).ToArray(),
                evidence = content.Evidence.Select(item => new
                {
                    document_id = item.DocumentId, field = item.FieldKey,
                    content = item.Text, token_count = item.TokenCount,
                }).ToArray(),
                approximate_tokens = content.ApproximateTokenCount,
            };
        }
        var hits = await store.SearchAsync(request.Query, search, cancellationToken);
        return new { query = request.Query, results = hits.Select(item => ToHit(item, collectionTitles)).ToArray() };

        IKnowledgeContentSearch storeSearch() => contentSearch
            ?? throw new InvalidOperationException("Knowledge content search is unavailable.");
    }

    private IKnowledgeContentSearch? contentSearch;

    public async Task<object?> GetAsync(Guid documentId, CancellationToken cancellationToken)
    {
        await InitializeAsync(cancellationToken);
        var document = await store.GetDocumentAsync(documentId, cancellationToken);
        if (document is null || document.KnowledgeBaseId != knowledgeBaseId) return null;
        var topic = (await storage.GetCollectionsAsync(knowledgeBaseId, cancellationToken))
            .FirstOrDefault(item => item.Id == document.CollectionId)?.Title ?? "Mechanics";
        return new
        {
            document_id = document.Id,
            external_id = document.ExternalId,
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
        return (await storage.GetCollectionsAsync(knowledgeBaseId, cancellationToken))
            .OrderBy(item => item.Title)
            .Select(item => (object)new
            {
                id = item.Id, item.Title, item.Description, item.Tags,
                document_count = documents.Count(document => document.CollectionId == item.Id),
            }).ToArray();
    }

    public void AttachContentSearch(IKnowledgeContentSearch search) => contentSearch = search;

    private async Task InitializeAsync(CancellationToken cancellationToken)
    {
        if (knowledgeBaseId != Guid.Empty) return;
        await store.InitializeAsync(cancellationToken);
        var knowledgeBase = await store.GetOrCreateKnowledgeBaseAsync(
            "Sid Meier's Alpha Centauri: Alien Crossfire rules", "smacx-rules", cancellationToken);
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

    private async Task<KnowledgeCollectionRecord> EnsureCollectionAsync(string topic, CancellationToken cancellationToken)
    {
        var item = await store.GetOrCreateCollectionAsync(
            knowledgeBaseId, topic, defaultSchemaId: schemaId, externalId: TopicKey(topic), cancellationToken: cancellationToken);
        return await catalog.UpsertCollectionAsync(item with
        {
            Description = $"Rules, mechanics, reference data, and datalinks for {topic}. Strategy guides are intentionally excluded.",
            Tags = ["smacx", "rules", TopicKey(topic)],
        }, cancellationToken);
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
        foreach (var file in files)
        {
            var path = Path.Combine(root, file);
            if (!File.Exists(path)) continue;
            // The shipped Windows game data is effectively Windows-1252, not
            // ISO-8859-1. Decode its punctuation before turning it into clean
            // model-facing Markdown.
            Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
            var text = await File.ReadAllTextAsync(path, Encoding.GetEncoding(1252), cancellationToken);
            foreach (var section in ParseGameSections(file, text)) result.Add(section);
        }
        return result;
    }

    private IEnumerable<CorpusPage> ParseGameSections(string file, string text)
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
                    yield return GamePage(file, key, title, current.ToString());
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
            yield return GamePage(file, key, title, current.ToString());
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
        var title = string.IsNullOrWhiteSpace(displayTitle) ? Humanize(key) : displayTitle.Trim();
        var body = CleanMarkdown($"# {title}\n\n{CleanGameMarkup(raw)}");
        var topic = TopicFor(file + " " + key + " " + title);
        return new CorpusPage(
            "game:" + TopicKey(file) + ":" + Hash(key)[..16], topic, title,
            FirstSentence(body), body, ["local-game", TopicKey(topic), TopicKey(file)],
            "installed-game:" + file,
            Hash(options.ParserRevision + "\n" + file + "\n" + key + "\n" + body));
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

    private static object ToHit(KnowledgeSearchHit hit, IReadOnlyDictionary<Guid, string> collectionTitles) => new
    {
        document_id = hit.DocumentId, collection_id = hit.CollectionId,
        topic = collectionTitles.GetValueOrDefault(hit.CollectionId, "Mechanics"),
        hit.Title, hit.Description, hit.Tags, score = hit.Score,
        matches = hit.Matches.Select(match => new
        {
            field = match.FieldKey, score = match.AdjustedSimilarity,
            token_count = match.TokenCount,
        }).ToArray(),
    };

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
    private static string TopicKey(string value) => Regex.Replace(value.ToLowerInvariant(), @"[^a-z0-9]+", "-").Trim('-');
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

    private sealed record CorpusPage(string ExternalId, string Topic, string Title, string Description,
        string Body, IReadOnlyList<string> Tags, string Source, string Hash);
    private sealed record CorpusAcquisition(List<CorpusPage> Pages, IReadOnlyList<string> Warnings);
    private sealed record StoredState(string ParserRevision, string ManifestHash, DateTimeOffset RefreshedAt, CorpusStatus Status);
}

public sealed class KnowledgeCorpusWorker(
    KnowledgeCorpus corpus,
    IKnowledgeContentSearch contentSearch,
    KnowledgeRuntimeOptions options,
    ILogger<KnowledgeCorpusWorker> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        corpus.AttachContentSearch(contentSearch);
        while (!stoppingToken.IsCancellationRequested)
        {
            try { await corpus.RefreshAsync(force: false, stoppingToken); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
            catch (Exception exception) { logger.LogError(exception, "Unexpected knowledge refresh worker failure"); }
            try { await Task.Delay(options.RefreshInterval, stoppingToken); }
            catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { break; }
        }
    }
}
