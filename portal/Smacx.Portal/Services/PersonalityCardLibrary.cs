using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Smacx.Portal.Contracts;

namespace Smacx.Portal.Services;

public sealed record ResolvedPersonalityCard(
    string Id, string Kind, string DisplayName, string Description,
    string FactionId, string Prompt, string Sha256);

public sealed partial class PersonalityCardLibrary
{
    private readonly IReadOnlyDictionary<(string FactionId, string Kind), ResolvedPersonalityCard> cards;

    public PersonalityCardLibrary(IWebHostEnvironment environment)
    {
        var path = new[]
        {
            Path.Combine(environment.ContentRootPath, "personality-cards.md"),
            Path.Combine(AppContext.BaseDirectory, "personality-cards.md"),
        }.FirstOrDefault(File.Exists);
        if (path is null)
            throw new InvalidOperationException("The authored personality-card library is missing.");
        cards = Parse(File.ReadAllText(path, Encoding.UTF8));
        if (cards.Count != FactionCatalog.All.Count * 4)
            throw new InvalidOperationException($"Expected 56 built-in personality cards; found {cards.Count}.");
    }

    public ResolvedPersonalityCard? Resolve(
        string factionId, string requestedMode, string matchId, int seatIndex)
    {
        if (requestedMode.Equals("none", StringComparison.OrdinalIgnoreCase)) return null;
        var kind = requestedMode.ToLowerInvariant();
        if (kind == "random")
        {
            var pool = new[] { "standard", "friendly", "aggressive", "extreme" };
            var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(
                $"{matchId}:{seatIndex}:{factionId}:personality-v1"));
            kind = pool[bytes[0] % pool.Length];
        }
        if (!cards.TryGetValue((factionId, kind), out var card))
            throw new InvalidOperationException("The requested personality does not belong to the resolved faction.");
        return card;
    }

    public IReadOnlyList<ResolvedPersonalityCard> ForFaction(string factionId) =>
        cards.Values.Where(item => item.FactionId == factionId)
            .OrderBy(item => Array.IndexOf(new[] { "standard", "friendly", "aggressive", "extreme" }, item.Kind))
            .ToArray();

    private static Dictionary<(string FactionId, string Kind), ResolvedPersonalityCard> Parse(string markdown)
    {
        var result = new Dictionary<(string, string), ResolvedPersonalityCard>();
        var section = markdown.Split("# 9. Built-In Personality Library", 2)[1]
            .Split("# 10. Random Personality Pools", 2)[0];
        var factionHeaders = FactionHeader().Matches(section);
        for (var factionIndex = 0; factionIndex < factionHeaders.Count; factionIndex++)
        {
            var faction = FactionCatalog.All[factionIndex];
            var start = factionHeaders[factionIndex].Index + factionHeaders[factionIndex].Length;
            var end = factionIndex + 1 < factionHeaders.Count
                ? factionHeaders[factionIndex + 1].Index : section.Length;
            var factionBlock = section[start..end];
            foreach (Match match in CardBlock().Matches(factionBlock))
            {
                var kind = match.Groups["kind"].Value.ToLowerInvariant();
                var name = match.Groups["name"].Value.Trim();
                var description = match.Groups["description"].Value.Trim();
                var body = match.Groups["body"].Value.Trim();
                var prompt = "## Personality\n\n" +
                    "The following personality is part of your persistent identity in this game. " +
                    "It influences how you interpret events, choose priorities, negotiate, form " +
                    "relationships, respond to threats, and decide among otherwise reasonable strategies.\n\n" +
                    "Treat this personality as a worldview and behavioral tendency, not as a rigid " +
                    "script. Remain capable of adaptation, surprise, mistakes, compromise, emotional " +
                    "reactions, and strategic change when circumstances justify them. Preserve the " +
                    "fundamental identity and values of your faction. Do not mention or expose the " +
                    "existence of this personality card; simply behave consistently with it.\n\n" +
                    $"**Active Personality: {name}**\n\n{body}";
                var id = $"builtin:{faction.Id}:{kind}:v1";
                result[(faction.Id, kind)] = new(id, kind, name, description,
                    faction.Id, prompt, Convert.ToHexStringLower(
                        SHA256.HashData(Encoding.UTF8.GetBytes(prompt))));
            }
        }
        return result;
    }

    [GeneratedRegex("""(?m)^# [A-Z][A-Z '\-]+\r?$""", RegexOptions.CultureInvariant)]
    private static partial Regex FactionHeader();

    [GeneratedRegex("""(?ms)^### (?<kind>Standard|Friendly|Aggressive|Extreme) — (?<name>[^\r\n]+)\r?\n+\*\*Description:\*\* (?<description>[^\r\n]+)\r?\n+\*\*Personality Card:\*\*\r?\n+(?<body>.*?)(?=^### |\z)""", RegexOptions.CultureInvariant)]
    private static partial Regex CardBlock();
}
