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
        pages = MergeTechnologyDescriptions(pages, parserRevision);
        pages = MergeTerraformDescriptions(pages, parserRevision);

        var classified = pages.Select(page => page with
        {
            CollectionPath = Classify(page),
            Topic = Classify(page).Last(),
        }).ToList();

        // The native files occasionally repeat identical descriptions under
        // multiple renderer records. Keep the human-facing identity once.
        classified = classified
            .GroupBy(page => $"{string.Join('/', page.CollectionPath!)}\n{Normalize(page.Title)}\n{Normalize(page.Body)}",
                StringComparer.Ordinal)
            .Select(group => group.First())
            .ToList();

        return SplitCrowdedLeaves(classified).Select(page => page with
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

    private static IReadOnlyList<string> Classify(CorpusPage page)
    {
        var file = page.SourceFile.ToLowerInvariant();
        var key = page.SourceKey.ToUpperInvariant();
        var text = $"{key} {page.Title}".ToLowerInvariant();

        if (file == "technology")
            return ["Datalinks", "Research and technology", "Technology entries"];
        if (file.StartsWith("concept", StringComparison.Ordinal) && key == "CONCEPT44")
            return ["Datalinks", "Bases and economy", "Production, citizens, and resources"];

        if (key.StartsWith("HELPFAC", StringComparison.Ordinal))
            return ["Datalinks", "Bases and economy", "Facilities"];
        if (key.StartsWith("HELPPROJ", StringComparison.Ordinal))
            return ["Datalinks", "Bases and economy", "Secret projects"];
        if (key.StartsWith("UNITDESC", StringComparison.Ordinal))
            return ["Datalinks", "Units and combat", "Predefined units"];
        if (key.StartsWith("CHASSISDESC", StringComparison.Ordinal) || key == "CHASSIS")
            return ["Datalinks", "Units and combat", "Chassis"];
        if (key.StartsWith("WEAPONDESC", StringComparison.Ordinal) || key == "WEAPONS")
            return ["Datalinks", "Units and combat", "Weapons"];
        if (key.StartsWith("ARMORDESC", StringComparison.Ordinal) || key is "DEFENSES" or "REACTORS" or "REACTORDESC")
            return ["Datalinks", "Units and combat", "Armor and reactors"];
        if (key.StartsWith("ABILDESC", StringComparison.Ordinal) || key == "ABILITIES")
            return ["Datalinks", "Units and combat", "Special abilities"];
        if (key.StartsWith("HELPTERRA", StringComparison.Ordinal))
            return ["Datalinks", "Planet", "Terraforming"];
        if (key.StartsWith("HELPSOC", StringComparison.Ordinal) || key.StartsWith("HELPEFFECT", StringComparison.Ordinal) ||
            key.StartsWith("SOC", StringComparison.Ordinal))
            return ["Datalinks", "Diplomacy and society", "Social engineering"];

        if (file.StartsWith("alphax", StringComparison.Ordinal))
        {
            if (key is "TERRAIN" or "RESOURCEINFO" or "RESOURCES" or "NATURAL")
                return ["Datalinks", "Planet", "Terrain, resources, and native life"];
            if (key is "WORLDBUILDER" or "WORLDSIZE")
                return ["Datalinks", "Planet", "World generation"];
            if (key is "TECHNOLOGY")
                return ["Datalinks", "Research and technology", "Research rules"];
            if (key is "UNITS" or "ORDERS" or "COMPASS" or "PLANS" or "TRIAD" or "MORALE" or "DEFENSEMODES" or "OFFENSEMODES")
                return ["Datalinks", "Units and combat", "Orders and combat rules"];
            if (key is "FACILITIES" or "CITIZENS" or "ENERGY")
                return ["Datalinks", "Bases and economy", "Production, citizens, and resources"];
            if (key is "FACTIONS" or "NEWFACTIONS" or "CUSTOMFACTIONS" or "MANDATE" or "MOOD" or "MIGHT")
                return ["Datalinks", "Factions and leaders", "Faction rules and identities"];
            if (key is "REPUTE" or "PROPOSALS")
                return ["Datalinks", "Diplomacy and society", "Diplomacy, reputation, and council"];
            if (key is "RULES" or "DIFF" or "TIMECONTROLS")
                return ["Datalinks", "Rules and interface", "Game settings and difficulty"];
        }

        if (ContainsAny(text, "diplom", "treaty", "pact", "truce", "vendetta", "council", "commerce", "atrocit", "probe", "reputation"))
            return ["Datalinks", "Diplomacy and society", "Diplomacy, reputation, and council"];
        if (ContainsAny(text, "social", "politic", "econom", "values", "future society"))
            return ["Datalinks", "Diplomacy and society", "Social engineering"];
        if (ContainsAny(text, "research", "technology", "discover", "tech"))
            return ["Datalinks", "Research and technology", "Research rules"];
        if (ContainsAny(text, "base", "facility", "project", "citizen", "drone", "talent", "mineral", "nutrient", "energy"))
            return ["Datalinks", "Bases and economy", "Production, citizens, and resources"];
        if (ContainsAny(text, "unit", "combat", "attack", "defense", "movement", "zone of control", "morale"))
            return ["Datalinks", "Units and combat", "Orders and combat rules"];
        if (ContainsAny(text, "planet", "terrain", "fungus", "ecolog", "terraform", "ocean", "map"))
            return ["Datalinks", "Planet", "Planetary rules"];
        if (ContainsAny(text, "faction", "leader"))
            return ["Datalinks", "Factions and leaders", "Faction rules and identities"];
        return ["Datalinks", "Rules and interface", "Core concepts"];
    }

    private static IReadOnlyList<CorpusPage> SplitCrowdedLeaves(List<CorpusPage> pages)
    {
        var bucketed = new List<CorpusPage>();
        foreach (var group in pages.GroupBy(page => string.Join('\u001f', page.CollectionPath!), StringComparer.Ordinal))
        {
            var ordered = group.OrderBy(page => page.Title, StringComparer.OrdinalIgnoreCase).ToArray();
            if (ordered.Length <= 24) { bucketed.AddRange(ordered); continue; }
            foreach (var page in ordered)
                bucketed.Add(page with { CollectionPath = [.. page.CollectionPath!, AlphabetBucket(page.Title)] });
        }
        var output = new List<CorpusPage>();
        foreach (var group in bucketed.GroupBy(page => string.Join('\u001f', page.CollectionPath!), StringComparer.Ordinal))
        {
            var ordered = group.OrderBy(page => page.Title, StringComparer.OrdinalIgnoreCase).ToArray();
            if (ordered.Length <= 24) { output.AddRange(ordered); continue; }
            for (var index = 0; index < ordered.Length; index++)
                output.Add(ordered[index] with { CollectionPath = [.. ordered[index].CollectionPath!, $"Part {index / 24 + 1}"] });
        }
        return output;
    }

    private static string AlphabetBucket(string title)
    {
        title = Regex.Replace(title.Trim(), @"^(?:the|a|an)\s+", "", RegexOptions.IgnoreCase);
        var initial = title.FirstOrDefault(char.IsLetterOrDigit);
        if (initial is >= '0' and <= '9') return "Numbers and symbols";
        return char.ToUpperInvariant(initial) switch
        {
            >= 'A' and <= 'C' => "A–C", >= 'D' and <= 'F' => "D–F",
            >= 'G' and <= 'I' => "G–I", >= 'J' and <= 'L' => "J–L",
            >= 'M' and <= 'O' => "M–O", >= 'P' and <= 'R' => "P–R",
            >= 'S' and <= 'U' => "S–U", >= 'V' and <= 'Z' => "V–Z",
            _ => "Other",
        };
    }

    private static bool ContainsAny(string value, params string[] terms) => terms.Any(value.Contains);
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
