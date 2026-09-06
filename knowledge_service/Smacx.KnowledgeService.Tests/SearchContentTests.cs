using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging.Abstractions;
using SemanticKnowledge;
using Smacx.KnowledgeService;

namespace Smacx.KnowledgeService.Tests;

public sealed class SearchContentTests
{
    [Fact]
    public async Task ContentUsesSameRankedScopedDocumentsAndBoundedBodyEvidence()
    {
        var root = Path.Combine(Path.GetTempPath(), "smacx-content-search-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var kb = Guid.NewGuid(); var research = Guid.NewGuid(); var other = Guid.NewGuid();
            var artifact = Guid.NewGuid(); var configuration = Guid.NewGuid(); var foreign = Guid.NewGuid();
            IReadOnlyList<KnowledgeCollectionRecord> collections = [
                new() { Id = research, KnowledgeBaseId = kb, Title = "Research" },
                new() { Id = other, KnowledgeBaseId = kb, Title = "Other" }];
            IReadOnlyList<KnowledgeDocumentRecord> documents = [
                new() { Id = artifact, KnowledgeBaseId = kb, CollectionId = research, SchemaId = Guid.Empty, Title = "Artifacts" },
                new() { Id = configuration, KnowledgeBaseId = kb, CollectionId = research, SchemaId = Guid.Empty, Title = "Configuration" },
                new() { Id = foreign, KnowledgeBaseId = kb, CollectionId = other, SchemaId = Guid.Empty, Title = "Artifacts" }];
            KnowledgeSearchHit Hit(Guid id, Guid collection, string title, string body) => new()
            {
                DocumentId = id, CollectionId = collection, SchemaId = Guid.Empty, Title = title, Score = 1,
                Matches = [new() { FieldKey = KnowledgeSystemFields.Body, RawSimilarity = 1, AdjustedSimilarity = 1, CharacterRange = default, Text = body, TokenCount = body.Length / 4 }]
            };
            // Deliberately poor semantic order; exact title and requested scope must win.
            IReadOnlyList<KnowledgeSearchHit> hits = [
                Hit(configuration, research, "Configuration", new string('x', 8_000)),
                Hit(foreign, other, "Artifacts", "OUTSIDE REQUESTED TOPIC"),
                Hit(artifact, research, "Artifacts", "An artifact can be linked at a Network Node for a technology, or used for eligible production.")];
            var storage = Stub<IKnowledgeStorageProvider>((method, _) => method.Name switch
            {
                "GetCollectionsAsync" => Task.FromResult(collections),
                "GetDocumentsAsync" => Task.FromResult(documents),
                _ => throw new InvalidOperationException(method.Name)
            });
            var store = Stub<ISemanticKnowledgeStore>((method, _) => method.Name == "SearchAsync"
                ? Task.FromResult(hits) : throw new InvalidOperationException(method.Name));
            using var services = new ServiceCollection().BuildServiceProvider();
            var options = new KnowledgeRuntimeOptions(root, Path.Combine(root, "manifest.json"), null, root,
                true, true, "tests", TimeSpan.FromHours(1));
            var corpus = new KnowledgeCorpus(store, null!, null!, storage, options, null!, services,
                new("external", "test-space", ModelId: "test-model", Dimensions: 3, SpaceId: "test-space"),
                new EmbeddingAuditStore(options), NullLogger<KnowledgeCorpus>.Instance);
            typeof(KnowledgeCorpus).GetField("knowledgeBaseId", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(corpus, kb);
            corpus.AttachContentSearch(Stub<IKnowledgeContentSearch>((_, _) =>
                throw new InvalidOperationException("Content must not bypass hybrid document ranking.")));
            var metadata = JsonSerializer.SerializeToElement(await corpus.SearchAsync(
                new("Artifacts", Topic: "Research", Top: 2, IncludeContent: false), default));
            var content = JsonSerializer.SerializeToElement(await corpus.SearchAsync(
                new("Artifacts", Topic: "Research", Top: 2, IncludeContent: true, MaxContentTokens: 256), default));
            Guid[] Ids(JsonElement result) => result.GetProperty("results").EnumerateArray()
                .Select(row => row.GetProperty("document_id").GetGuid()).ToArray();
            Assert.Equal(new[] { artifact, configuration }, Ids(metadata));
            Assert.Equal(Ids(metadata), Ids(content));
            Assert.Contains("Network Node", content.GetProperty("evidence")[0].GetProperty("content").GetString());
            Assert.DoesNotContain("OUTSIDE REQUESTED TOPIC", content.ToString());
            Assert.InRange(content.GetProperty("approximate_tokens").GetInt32(), 1, 256);
            Assert.Contains(content.GetProperty("evidence").EnumerateArray(), row => row.GetProperty("truncated").GetBoolean());
        }
        finally { Directory.Delete(root, recursive: true); }
    }

    private static T Stub<T>(Func<MethodInfo, object?[]?, object?> handler) where T : class
    {
        var proxy = DispatchProxy.Create<T, SearchStub>();
        ((SearchStub)(object)proxy).Handler = handler;
        return proxy;
    }
    public class SearchStub : DispatchProxy
    {
        public Func<MethodInfo, object?[]?, object?> Handler { get; set; } = null!;
        protected override object? Invoke(MethodInfo? method, object?[]? args) => Handler(method!, args);
    }
}
