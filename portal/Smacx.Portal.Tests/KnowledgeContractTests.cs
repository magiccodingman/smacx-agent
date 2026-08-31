using System.Text.Json;
using Smacx.Portal.Controllers;

namespace Smacx.Portal.Tests;

public sealed class KnowledgeContractTests
{
    [Fact]
    public void SemanticKnowledgeTopicAndDocumentShapesMapWithoutLegacyWikiFields()
    {
        using var topicJson = JsonDocument.Parse("""
            {"id":"topic-id","title":"Diplomacy and society","description":"Rules","tags":["rules"],"document_count":40}
            """);
        var topic = KnowledgeController.MapTopic(topicJson.RootElement);
        Assert.Equal("Diplomacy and society", topic.Topic);
        Assert.Equal(40, topic.DocumentCount);

        using var documentJson = JsonDocument.Parse("""
            {
              "document_id":"doc-id",
              "topic":"Diplomacy and society",
              "title":"Council Proposals",
              "description":"The Planetary Council can elect a governor.",
              "tags":["local-game","diplomacy-and-society"],
              "source":"installed-game:conceptsx.txt",
              "body":"# Council Proposals\n\nFactual rules."
            }
            """);
        var document = KnowledgeController.Map(documentJson.RootElement, includeBody: true);

        Assert.Equal("doc-id", document.DocumentId);
        Assert.Equal("Diplomacy and society", document.Topic);
        Assert.Equal("Council Proposals", document.Title);
        Assert.Equal("The Planetary Council can elect a governor.", document.Summary);
        Assert.Equal(["local-game", "diplomacy-and-society"], document.Tags);
        Assert.Equal("installed-game:conceptsx.txt", document.Provenance);
        Assert.Equal("# Council Proposals\n\nFactual rules.", document.Body);
    }
}
