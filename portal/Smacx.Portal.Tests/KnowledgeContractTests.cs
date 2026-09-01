using System.Text.Json;
using Smacx.Portal.Controllers;
using Smacx.Portal.Services;

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

    [Fact]
    public void HierarchicalCollectionShapeMapsForTheReusableReader()
    {
        using var json = JsonDocument.Parse("""
            {
              "id":"leaf-id","parent_id":"parent-id","title":"Facilities",
              "description":"Base facilities","tags":["smacx","rules"],
              "path":["Datalinks","Bases and economy","Facilities"],
              "direct_document_count":18,"document_count":18,
              "documents":[{"document_id":"doc-1","title":"Network Node","description":"A research facility."}]
            }
            """);
        var collection = KnowledgeController.MapCollection(json.RootElement);
        Assert.Equal("parent-id", collection.ParentId);
        Assert.Equal(["Datalinks", "Bases and economy", "Facilities"], collection.Path);
        Assert.Equal(18, collection.DirectDocumentCount);
        var document = Assert.Single(collection.Documents!);
        Assert.Equal("doc-1", document.DocumentId);
        Assert.Equal("Network Node", document.Title);
    }

    [Fact]
    public void MarkdownRendererBuildsOutlineAndRejectsRawHtml()
    {
        var rendered = new DatalinksMarkdownRenderer().Render("""
            # Planetary Networks

            ## Effects

            **Network Nodes** improve research.

            <script>alert('never')</script>

            ### Notes

            | Resource | Value |
            | --- | ---: |
            | Energy | 2 |
            """);

        Assert.Contains("<strong>Network Nodes</strong>", rendered.Html);
        Assert.Contains("<table>", rendered.Html);
        Assert.DoesNotContain("<script", rendered.Html, StringComparison.OrdinalIgnoreCase);
        Assert.Contains(rendered.Headings, item => item.Text == "Effects" && item.Level == 2);
        Assert.Contains(rendered.Headings, item => item.Text == "Notes" && item.Level == 3);
    }
}
