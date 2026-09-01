using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace Smacx.KnowledgeService;

internal sealed record CorpusPage(
    string ExternalId,
    string Topic,
    string Title,
    string Description,
    string Body,
    IReadOnlyList<string> Tags,
    string Source,
    string Hash,
    IReadOnlyList<string>? CollectionPath = null,
    string SourceFile = "",
    string SourceKey = "");

internal static class CorpusOrganization
{
    private static readonly HashSet<string> HelpTemplates = new(StringComparer.OrdinalIgnoreCase)
    {
        "HELPCATS", "HELPTECHS", "HELPTECH", "TECHHECK", "CITIZENHECK",
        "HELPWEAPONS", "HELPWEAPON", "HELPWEAPON2", "HELPDEFENSES", "HELPDEFENSE",
        "HELPDEFENSE2", "HELPREACTOR", "HELPCHASSIS2", "HELPCHASSIS", "HELPCROPS",
        "HELPFACS", "HELPFAC", "HELPFACFREE", "HELPPROJS", "HELPPROJ", "HELPUNIT",
        "HELPUNITS", "HELPABILS", "HELPABIL", "HELPTERRAFORM",
    };

    public static IReadOnlyList<CorpusPage> Organize(IEnumerable<CorpusPage> input, string parserRevision)
    {
        var pages = input.Where(IsUseful).ToList();
        var technologyRoles = CorpusTaxonomy.TechnologyRoles(pages);
        pages = MergeTechnologyDescriptions(pages, parserRevision);
        pages = MergeTerraformDescriptions(pages, parserRevision);

        var classified = pages.Select(page =>
        {
            var path = CorpusTaxonomy.Classify(page, technologyRoles);
            return page with { CollectionPath = path, Topic = path.Last() };
        }).ToList();

        // The native files occasionally repeat identical descriptions under
        // multiple renderer records. Keep the human-facing identity once.
        classified = classified
            .GroupBy(page => $"{string.Join('/', page.CollectionPath!)}\n{Normalize(page.Title)}\n{Normalize(page.Body)}",
                StringComparer.Ordinal)
            .Select(group => group.First())
            .ToList();

        return classified.Select(page => page with
        {
            // SemanticKnowledge document identities are knowledge-base-wide.
            // Binding a generated identity to its canonical leaf lets a future
            // taxonomy revision publish moved pages before retiring the old
            // snapshot, without an in-place database migration.
            ExternalId = page.ExternalId + ":leaf:" + Hash(string.Join('/', page.CollectionPath!))[..12],
        }).ToArray();
    }

    private static bool IsUseful(CorpusPage page)
    {
        var normalizedTitle = Regex.Replace(page.Title.ToLowerInvariant(), @"[^a-z0-9]+", "");
        // Navigation/index pages and broad duplicate web summaries are not
        // factual articles. Installed Datalinks already supplies their useful
        // mechanics as individually titled documents.
        if (normalizedTitle is "tableofcontents" or "sidmeiersalphacentauri" or
            "importantnotetotranslators") return false;
        if (page.SourceFile.Length == 0 && normalizedTitle is
            "facilities" or "terraforming" or "socialengineering") return false;
        if (!page.SourceFile.Equals("helpx.txt", StringComparison.OrdinalIgnoreCase) &&
            !page.SourceFile.Equals("help.txt", StringComparison.OrdinalIgnoreCase)) return true;
        var key = page.SourceKey.Trim();
        return key.Length > 0 &&
            !key.Contains("TRANSLATOR", StringComparison.OrdinalIgnoreCase) &&
            !key.StartsWith("xs ", StringComparison.OrdinalIgnoreCase) &&
            !key.StartsWith("caption ", StringComparison.OrdinalIgnoreCase) &&
            !HelpTemplates.Contains(key);
    }

    private static List<CorpusPage> MergeTechnologyDescriptions(List<CorpusPage> pages, string parserRevision)
    {
        var technology = pages.Where(page =>
            page.SourceFile.Equals("TECHSHORTS.txt", StringComparison.OrdinalIgnoreCase) ||
            page.SourceFile.Equals("TECHLONGS.TXT", StringComparison.OrdinalIgnoreCase)).ToArray();
        if (technology.Length == 0) return pages;

        var merged = new List<CorpusPage>();
        foreach (var group in technology.GroupBy(page => page.SourceKey, StringComparer.OrdinalIgnoreCase))
        {
            var shortPage = group.FirstOrDefault(page => page.SourceFile.Contains("SHORT", StringComparison.OrdinalIgnoreCase));
            var longPage = group.FirstOrDefault(page => page.SourceFile.Contains("LONG", StringComparison.OrdinalIgnoreCase));
            var chosen = longPage ?? shortPage ?? group.First();
            var sections = new List<string> { $"# {chosen.Title}" };
            if (shortPage is not null)
                sections.Add("## Summary\n\n" + StripLeadingTitle(shortPage.Body));
            if (longPage is not null)
                sections.Add("## Datalinks\n\n" + StripLeadingTitle(longPage.Body));
            var body = string.Join("\n\n", sections).Trim();
            merged.Add(chosen with
            {
                ExternalId = "game:technology:" + Slug(group.Key),
                Body = body,
                Description = FirstSentence(shortPage?.Body ?? longPage?.Body ?? body),
                Source = "installed-game:TECHSHORTS.txt+TECHLONGS.TXT",
                SourceFile = "technology",
                Hash = Hash(parserRevision + "\n" + group.Key + "\n" + body),
                Tags = ["local-game", "research", "technology"],
            });
        }
        pages.RemoveAll(page => technology.Contains(page));
        pages.AddRange(merged);
        return pages;
    }

    private static List<CorpusPage> MergeTerraformDescriptions(List<CorpusPage> pages, string parserRevision)
    {
        var terraform = pages.Select(page => (Page: page, Match: Regex.Match(
                page.SourceKey, @"^HELPTERRA(LAND|SEA)(\d+)$", RegexOptions.IgnoreCase)))
            .Where(item => item.Match.Success)
            .ToArray();
        if (terraform.Length == 0) return pages;

        var merged = new List<CorpusPage>();
        foreach (var group in terraform.GroupBy(item => item.Match.Groups[2].Value, StringComparer.Ordinal))
        {
            var land = group.FirstOrDefault(item => item.Match.Groups[1].Value.Equals("LAND", StringComparison.OrdinalIgnoreCase)).Page;
            var sea = group.FirstOrDefault(item => item.Match.Groups[1].Value.Equals("SEA", StringComparison.OrdinalIgnoreCase)).Page;
            var chosen = land ?? sea ?? group.First().Page;
            var landText = land is null ? null : StripLeadingTitle(land.Body);
            var seaText = sea is null ? null : StripLeadingTitle(sea.Body);
            var sections = new List<string> { $"# {chosen.Title}" };
            if (landText is not null && seaText is not null && Normalize(landText) == Normalize(seaText))
                sections.Add("## Datalinks\n\n" + landText);
            else
            {
                if (landText is not null) sections.Add("## Land\n\n" + landText);
                if (seaText is not null) sections.Add("## Ocean\n\n" + seaText);
            }
            var body = string.Join("\n\n", sections).Trim();
            merged.Add(chosen with
            {
                ExternalId = "game:terraform:" + group.Key,
                Body = body,
                Description = FirstSentence(landText ?? seaText ?? body),
                Source = "installed-game:helpx.txt",
                SourceFile = "helpx.txt",
                SourceKey = "HELPTERRA" + group.Key,
                Hash = Hash(parserRevision + "\nterraform\n" + group.Key + "\n" + body),
                Tags = ["local-game", "planet", "terraforming"],
            });
        }
        pages.RemoveAll(page => terraform.Any(item => ReferenceEquals(item.Page, page)));
        pages.AddRange(merged);
        return pages;
    }

    private static string StripLeadingTitle(string body) => Regex.Replace(body, @"^#[^\n]*\n+", "").Trim();
    private static string Normalize(string value) => Regex.Replace(value.ToLowerInvariant(), @"\s+", " ").Trim();
    private static string Slug(string value) => Regex.Replace(value.ToLowerInvariant(), @"[^a-z0-9]+", "-").Trim('-');
    private static string Hash(string value) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(value)));
    private static string FirstSentence(string value)
    {
        var plain = Regex.Replace(value, @"[#*|]", "");
        var end = plain.IndexOfAny(['.', '!', '?', '\n']);
        return (end >= 40 ? plain[..(end + 1)] : plain[..Math.Min(plain.Length, 220)]).Trim();
    }
}
