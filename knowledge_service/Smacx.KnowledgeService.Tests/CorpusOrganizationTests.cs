using Smacx.KnowledgeService;

namespace Smacx.KnowledgeService.Tests;

public sealed class CorpusOrganizationTests
{
    [Fact]
    public void RemovesRendererTemplatesAndMergesTechnologyDescriptions()
    {
        var organized = CorpusOrganization.Organize([
            Page("template", "Selection", "placeholder", "helpx.txt", "xs 440"),
            Page("short", "Biogenetics", "Short factual description.", "TECHSHORTS.txt", "TECH0"),
            Page("long", "Biogenetics", "Long factual description.", "TECHLONGS.TXT", "TECH0"),
        ], "test-parser");

        var technology = Assert.Single(organized);
        Assert.Equal("Biogenetics", technology.Title);
        Assert.Contains("## Summary", technology.Body);
        Assert.Contains("## Datalinks", technology.Body);
        Assert.Equal(["Datalinks", "Research and technology", "Technology entries"], technology.CollectionPath);
    }

    [Fact]
    public void SplitsCrowdedLeavesIntoStableAlphabeticCollections()
    {
        var pages = Enumerable.Range(0, 25)
            .Select(index => Page($"facility-{index}", $"{(char)('A' + index % 20)} Facility {index}",
                $"Facility rule {index}.", "helpx.txt", $"HELPFAC{index}"));

        var organized = CorpusOrganization.Organize(pages, "test-parser");

        Assert.All(organized, page => Assert.Equal(4, page.CollectionPath!.Count));
        Assert.All(organized.GroupBy(page => string.Join('/', page.CollectionPath!)), group => Assert.True(group.Count() <= 24));
        Assert.Contains(organized, page => page.CollectionPath!.Last() == "A–C");
    }

    [Fact]
    public void MergesLandAndOceanTerraformingRecordsIntoOneArticle()
    {
        var organized = CorpusOrganization.Organize([
            Page("land", "Terraform Raise", "Raises land terrain.", "helpx.txt", "HELPTERRALAND16"),
            Page("sea", "Terraform Raise", "Raises ocean terrain.", "helpx.txt", "HELPTERRASEA16"),
        ], "test-parser");

        var article = Assert.Single(organized);
        Assert.Equal("Terraform Raise", article.Title);
        Assert.Contains("## Land", article.Body);
        Assert.Contains("## Ocean", article.Body);
        Assert.Contains("Raises land terrain.", article.Body);
        Assert.Contains("Raises ocean terrain.", article.Body);
        Assert.Equal(["Datalinks", "Planet", "Terraforming"], article.CollectionPath);
    }

    private static CorpusPage Page(string id, string title, string body, string file, string key) => new(
        id, "old-topic", title, body, $"# {title}\n\n{body}", ["local-game"],
        "installed-game:" + file, id + "-hash", SourceFile: file, SourceKey: key);
}
