namespace Smacx.Portal.Contracts;

public sealed record FactionCatalogItem(
    string Id, int NativeChoiceId, string FactionName, string LeaderName);

public sealed record PersonalityCatalogItem(
    string Id, string Kind, string DisplayName, string Description,
    string? FactionId = null);

public sealed record AgentSeatRequest(
    string AgentId,
    string FactionId = "random",
    string PersonalityId = "standard");

public sealed record UpdateDisplayNameRequest(string DisplayName);

public static class FactionCatalog
{
    public const string Random = "random";

    public static IReadOnlyList<FactionCatalogItem> All { get; } =
    [
        new("gaians", 0, "Gaia's Stepdaughters", "Lady Deirdre Skye"),
        new("hive", 1, "Human Hive", "Chairman Sheng-ji Yang"),
        new("university", 2, "University of Planet", "Academician Prokhor Zakharov"),
        new("morgan", 3, "Morgan Industries", "CEO Nwabudike Morgan"),
        new("spartans", 4, "Spartan Federation", "Colonel Corazon Santiago"),
        new("believers", 5, "The Lord's Believers", "Sister Miriam Godwinson"),
        new("peacekeepers", 6, "Peacekeeping Forces", "Commissioner Pravin Lal"),
        new("cybernetic", 7, "Cybernetic Consciousness", "Prime Function Aki Zeta-5"),
        new("pirates", 8, "Nautilus Pirates", "Captain Ulrik Svensgaard"),
        new("drones", 9, "Free Drones", "Foreman Domai"),
        new("angels", 10, "Data Angels", "Datajack Sinder Roze"),
        new("cult", 11, "Cult of Planet", "Prophet Cha Dawn"),
        new("caretakers", 12, "Manifold Caretakers", "Guardian Lular H'minee"),
        new("usurpers", 13, "Manifold Usurpers", "Conqueror Marr"),
    ];

    public static FactionCatalogItem? Find(string? id) =>
        All.FirstOrDefault(item => item.Id.Equals(id, StringComparison.OrdinalIgnoreCase));

    public static bool IsReservedLeaderName(string? value) =>
        !string.IsNullOrWhiteSpace(value) && All.Any(item =>
            item.LeaderName.Equals(value.Trim(), StringComparison.OrdinalIgnoreCase));
}

public static class BuiltInPersonalityCatalog
{
    public static IReadOnlyList<PersonalityCatalogItem> Modes { get; } =
    [
        new("none", "none", "None", "No personality layer is applied. The model develops its own temperament and strategy."),
        new("standard", "standard", "Standard", "The canonical faction worldview. This is the default for every AI seat."),
        new("random", "random", "Random", "Chooses one built-in faction personality once, then locks it for the entire match."),
        new("friendly", "friendly", "Friendly", "A cooperative expression of the faction's values without abandoning its ideological center."),
        new("aggressive", "aggressive", "Aggressive", "A harder, more confrontational expression of the faction's values."),
        new("extreme", "extreme", "Extreme", "The faction's worldview pushed to its most uncompromising and dramatic edge."),
    ];

    public static PersonalityCatalogItem? FindMode(string? id) =>
        Modes.FirstOrDefault(item => item.Id.Equals(id, StringComparison.OrdinalIgnoreCase));
}
