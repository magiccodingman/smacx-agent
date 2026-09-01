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
        Assert.Equal(["Datalinks", "Research and technology", "Technology tree", "Cross-disciplinary advances"], technology.CollectionPath);
    }

    [Fact]
    public void RoutesRulesIntoMeaningfulSemanticCollectionsWithoutPaginationBuckets()
    {
        var organized = CorpusOrganization.Organize([
            Page("drone-riots", "Drone Riots", "Uncontrolled drones cause riots.", "conceptsx.txt", "DronesAdvanced"),
            Page("perimeter", "Perimeter Defense", "Improves base defense.", "helpx.txt", "HELPFAC16"),
            Page("planet-buster", "Planet Buster", "A strategic missile payload.", "helpx.txt", "WEAPONDESC20"),
            Page("treaty", "Treaty of Friendship", "A diplomatic agreement.", "conceptsx.txt", "TreatyOfFriendship"),
        ], "test-parser");

        Assert.Contains(organized, page => page.Title == "Drone Riots" &&
            page.CollectionPath!.SequenceEqual(["Datalinks", "Bases and economy", "Citizens, drones, talents, and growth"]));
        Assert.Contains(organized, page => page.Title == "Perimeter Defense" &&
            page.CollectionPath!.SequenceEqual(["Datalinks", "Bases and economy", "Facilities", "Military, aerospace, and security facilities"]));
        Assert.Contains(organized, page => page.Title == "Planet Buster" &&
            page.CollectionPath!.SequenceEqual(["Datalinks", "Units and combat", "Unit design", "Weapons and modules", "Missiles and strategic payloads"]));
        Assert.Contains(organized, page => page.Title == "Treaty of Friendship" &&
            page.CollectionPath!.SequenceEqual(["Datalinks", "Diplomacy and society", "Treaties, pacts, territory, and conflict"]));

        Assert.DoesNotContain(organized.SelectMany(page => page.CollectionPath!), name =>
            name.Contains('–') || name.StartsWith("Part ", StringComparison.OrdinalIgnoreCase));
        Assert.All(organized, page => Assert.All(Enumerable.Range(1, page.CollectionPath!.Count), length =>
            Assert.True(CorpusTaxonomy.IsRegistered(page.CollectionPath.Take(length).ToArray()))));
    }

    [Fact]
    public void UsesInstalledTechnologyPrioritiesAsSemanticResearchRoutes()
    {
        var configuration = Page("config", "Technology configuration", "Biogenetics, BioGen, 0, 4, 1, 0, None, 0000000",
            "alphax.txt", "TECHNOLOGY");
        var organized = CorpusOrganization.Organize([
            configuration,
            Page("short", "Biogenetics", "Biology research.", "TECHSHORTS.txt", "TECH0"),
            Page("long", "Biogenetics", "A scientific advance.", "TECHLONGS.TXT", "TECH0"),
        ], "test-parser");

        var technology = Assert.Single(organized, page => page.Title == "Biogenetics");
        Assert.Equal(["Datalinks", "Research and technology", "Technology tree", "Discover-oriented advances"],
            technology.CollectionPath);
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
        Assert.Equal(["Datalinks", "Planet and terraforming", "Terraforming", "Water, elevation, and ecological engineering"], article.CollectionPath);
    }

    private static CorpusPage Page(string id, string title, string body, string file, string key) => new(
        id, "old-topic", title, body, $"# {title}\n\n{body}", ["local-game"],
        "installed-game:" + file, id + "-hash", SourceFile: file, SourceKey: key);
}
