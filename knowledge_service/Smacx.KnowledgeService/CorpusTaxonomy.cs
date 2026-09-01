using System.Text.RegularExpressions;

namespace Smacx.KnowledgeService;

internal sealed record CorpusCollectionMetadata(
    string Description,
    IReadOnlyList<string> Tags);

/// <summary>
/// Domain taxonomy for the runtime-built Datalinks corpus.
///
/// SemanticKnowledge Collections are semantic routing nodes, not presentation
/// buckets. Every route in this catalog therefore describes a real game
/// concept. Alphabetic and numbered pagination labels are deliberately absent.
/// </summary>
internal static class CorpusTaxonomy
{
    private const string Root = "Datalinks";

    private static readonly HashSet<string> PopulationFacilities = Names(
        "Children's Creche", "Hab Complex", "Habitation Dome", "Hologram Theatre",
        "Paradise Dome", "Paradise Garden", "Punishment Sphere", "Recreation Commons",
        "Recreation Dome", "Research Hospital", "Nanohospital");

    private static readonly HashSet<string> EconomyResearchFacilities = Names(
        "Biology Lab", "Energy Bank", "Fusion Lab", "Headquarters", "Network Node",
        "Quantum Lab", "Stockpile Energy");

    private static readonly HashSet<string> IndustryResourceFacilities = Names(
        "Aquafarm", "Genejack Factory", "Hybrid Forest", "Nanoreplicator",
        "Quantum Converter", "Recycling Tanks", "Robotic Assembly Plant",
        "Subsea Trunkline", "Thermocline Transducer", "Tree Farm");

    private static readonly HashSet<string> MilitaryFacilities = Names(
        "Aerospace Complex", "Bioenhancement Center", "Command Center", "Covert Ops Center",
        "Flechette Defense System", "Geosynchronous Survey Pod", "Naval Base", "Naval Yard",
        "Perimeter Defense", "Skunkworks", "Tachyon Field");

    private static readonly HashSet<string> PlanetFacilities = Names(
        "Brood Pit", "Centauri Preserve", "Temple of Planet");

    private static readonly HashSet<string> AdvancedFacilities = Names(
        "Nessus Mining Station", "Orbital Defense Pod", "Orbital Power Transmitter",
        "Pressure Dome", "Psi Gate", "Sky Hydroponics", "Sky Hydroponics Lab",
        "Subspace Generator");

    private static readonly HashSet<string> PopulationProjects = Names(
        "Clinical Immortality", "The Ascetic Virtues", "The Cloning Vats",
        "Human Genome Project", "The Human Genome Project", "The Longevity Vaccine",
        "The Planetary Transit System", "The Self-Aware Colony", "The Telepathic Matrix");

    private static readonly HashSet<string> EconomyProjects = Names(
        "The Bulk Matter Transmitter", "The Living Refinery", "The Maritime Control Center",
        "The Merchant Exchange", "The Nano Factory", "The Planetary Energy Grid",
        "The Singularity Inductor", "The Space Elevator");

    private static readonly HashSet<string> ResearchProjects = Names(
        "The Cyborg Factory", "The Network Backbone", "The Planetary Datalinks",
        "The Supercollider", "The Theory of Everything", "Universal Translator",
        "The Virtual World");

    private static readonly HashSet<string> MilitaryProjects = Names(
        "The Citizens' Defense Force", "The Cloudbase Academy", "The Command Nexus",
        "The Dream Twister", "The Hunter-Seeker Algorithm", "The Nethack Terminus",
        "The Neural Amplifier");

    private static readonly HashSet<string> PlanetProjects = Names(
        "The Ascent to Transcendence", "The Empath Guild", "The Manifold Harmonics",
        "The Pholus Mutagen", "The Voice of Planet", "The Weather Paradigm",
        "The Xenoempathy Dome");

    private static readonly HashSet<string> PoliticsModels = Names(
        "Frontier", "Police State", "Democratic", "Fundamentalist");
    private static readonly HashSet<string> EconomicsModels = Names(
        "Simple", "Free Market", "Planned", "Green");
    private static readonly HashSet<string> ValuesModels = Names(
        "Survival", "Power", "Knowledge", "Wealth");
    private static readonly HashSet<string> FutureModels = Names(
        "None", "Cybernetic", "Eudaimonic", "Eudiamonic", "Thought Control");

    private static readonly HashSet<string> ResourceTerraforming = Names(
        "Condenser", "Echelon Mirror", "Farm", "Forest", "Mine", "Soil Enricher",
        "Solar Collector", "Thermal Borehole");
    private static readonly HashSet<string> InfrastructureTerraforming = Names(
        "Airbase", "Bunker", "Mag Tubes", "Roads", "Sensor Array");
    private static readonly HashSet<string> PlanetEngineeringTerraforming = Names(
        "Cultivate Fungus", "Drilling to an Aquifer", "Fungus", "Terraform Level",
        "Terraform Lower", "Terraform Raise");

    private static readonly HashSet<string> MissileWeapons = Names(
        "Conventional Payload", "Fungal Payload", "Planet Buster", "Tectonic Payload");
    private static readonly HashSet<string> PsiWeapons = Names(
        "Psi Attack", "Resonance Bolt", "Resonance Laser");
    private static readonly HashSet<string> SupportModules = Names(
        "Colony Module", "Probe Team", "Supply Transport", "Terraforming Unit",
        "Troop Transport", "Weapon Configuration");

    private static readonly HashSet<string> OffensiveAbilities = Names(
        "Air Superiority", "Blink Displacer", "Dissociative Wave", "Empath Song",
        "Heavy Artillery", "Nerve Gas Pods", "Soporific Gas Pods");
    private static readonly HashSet<string> DefensiveAbilities = Names(
        "AAA Tracking", "Comm Jammer", "Hypnotic Trance", "Polymorphic Encryption");
    private static readonly HashSet<string> MobilityAbilities = Names(
        "Amphibious Pods", "Antigrav Struts", "Cloaking Device", "Deep Pressure Hull",
        "Deep Radar", "Drop Pods", "Fuel Nanocells");
    private static readonly HashSet<string> SupportAbilities = Names(
        "Algorithmic Enhancement", "Carrier Deck", "Clean Reactor", "High Morale",
        "Marine Detachment", "Non-Lethal Methods", "Repair Bay", "Special Ability Configuration");
    private static readonly HashSet<string> TerraformAbilities = Names(
        "Fungicide Tanks", "Super Former");

    private static readonly HashSet<string> NativeUnits = Names(
        "Fungal Tower", "Isle of the Deep", "Locusts of Chiron", "Mind Worms",
        "Sealurk", "Spore Launcher");
    private static readonly HashSet<string> ProgenitorUnits = Names(
        "Battle Ogre MK1", "Battle Ogre MK2", "Battle Ogre MK3");
    private static readonly HashSet<string> UnityUnits = Names(
        "Unity Foil", "Unity Mining Laser", "Unity Rover", "Unity Scout Chopper");

    private static readonly Dictionary<string, CorpusCollectionMetadata> Metadata = BuildMetadata();

    public static IReadOnlyList<string> Classify(
        CorpusPage page,
        IReadOnlyDictionary<string, string> technologyRoles)
    {
        var file = page.SourceFile.ToLowerInvariant();
        var key = page.SourceKey.ToUpperInvariant();
        var title = NameKey(page.Title);
        var text = $"{key} {page.Title} {page.Description}".ToLowerInvariant();

        if (file == "technology")
        {
            var role = technologyRoles.GetValueOrDefault(NameKey(page.Title), "Cross-disciplinary");
            return role switch
            {
                "Conquer" => Path("Research and technology", "Technology tree", "Conquer-oriented advances"),
                "Discover" => Path("Research and technology", "Technology tree", "Discover-oriented advances"),
                "Build" => Path("Research and technology", "Technology tree", "Build-oriented advances"),
                "Explore" => Path("Research and technology", "Technology tree", "Explore-oriented advances"),
                _ => Path("Research and technology", "Technology tree", "Cross-disciplinary advances"),
            };
        }

        if (IsFacility(key, page.Title))
        {
            if (PopulationFacilities.Contains(title)) return Path("Bases and economy", "Facilities", "Population, health, and psych facilities");
            if (EconomyResearchFacilities.Contains(title)) return Path("Bases and economy", "Facilities", "Economy, research, and energy facilities");
            if (IndustryResourceFacilities.Contains(title)) return Path("Bases and economy", "Facilities", "Industry and resource facilities");
            if (MilitaryFacilities.Contains(title)) return Path("Bases and economy", "Facilities", "Military, aerospace, and security facilities");
            if (PlanetFacilities.Contains(title)) return Path("Bases and economy", "Facilities", "Ecology and native-life facilities");
            return Path("Bases and economy", "Facilities", "Orbital and specialized infrastructure");
        }

        if (key.StartsWith("HELPPROJ", StringComparison.Ordinal) || IsKnownProject(title))
        {
            if (PopulationProjects.Contains(title)) return Path("Bases and economy", "Secret projects", "Population and society projects");
            if (EconomyProjects.Contains(title)) return Path("Bases and economy", "Secret projects", "Economy and infrastructure projects");
            if (ResearchProjects.Contains(title)) return Path("Bases and economy", "Secret projects", "Research and information projects");
            if (MilitaryProjects.Contains(title)) return Path("Bases and economy", "Secret projects", "Military, security, and intelligence projects");
            return Path("Bases and economy", "Secret projects", "Planet and transcendence projects");
        }

        if (PoliticsModels.Contains(title)) return Path("Diplomacy and society", "Social engineering", "Politics models");
        if (EconomicsModels.Contains(title)) return Path("Diplomacy and society", "Social engineering", "Economics models");
        if (ValuesModels.Contains(title)) return Path("Diplomacy and society", "Social engineering", "Values models");
        if (FutureModels.Contains(title)) return Path("Diplomacy and society", "Social engineering", "Future society models");
        if (key.StartsWith("HELPEFFECT", StringComparison.Ordinal) || text.Contains("social effect"))
            return SocialEffectPath(page.Title);
        if (key.StartsWith("HELPSOC", StringComparison.Ordinal) || key.StartsWith("SOC", StringComparison.Ordinal) ||
            title is "socialengineering" or "socialengineeringcategories" or "politics" or "economics" or "values" or "futuresociety")
            return Path("Diplomacy and society", "Social engineering", "Social engineering rules and categories");

        if (MissileWeapons.Contains(title)) return Path("Units and combat", "Unit design", "Weapons and modules", "Missiles and strategic payloads");
        if (PsiWeapons.Contains(title)) return Path("Units and combat", "Unit design", "Weapons and modules", "Psi and resonance weapons");
        if (SupportModules.Contains(title)) return Path("Units and combat", "Unit design", "Weapons and modules", "Civilian and support modules");
        if (key.StartsWith("WEAPONDESC", StringComparison.Ordinal) || key == "WEAPONS")
            return Path("Units and combat", "Unit design", "Weapons and modules", "Conventional weapons");

        if (key.StartsWith("ABILDESC", StringComparison.Ordinal) || key == "ABILITIES")
        {
            if (OffensiveAbilities.Contains(title)) return Path("Units and combat", "Unit design", "Special abilities", "Offensive combat abilities");
            if (DefensiveAbilities.Contains(title)) return Path("Units and combat", "Unit design", "Special abilities", "Defensive abilities");
            if (MobilityAbilities.Contains(title)) return Path("Units and combat", "Unit design", "Special abilities", "Mobility, scouting, and deployment");
            if (TerraformAbilities.Contains(title)) return Path("Units and combat", "Unit design", "Special abilities", "Terraforming and native-life abilities");
            return Path("Units and combat", "Unit design", "Special abilities", "Logistics, policing, and covert abilities");
        }

        if (key.StartsWith("CHASSISDESC", StringComparison.Ordinal) || key == "CHASSIS")
            return Path("Units and combat", "Unit design", "Chassis");
        if (key.StartsWith("ARMORDESC", StringComparison.Ordinal) || key is "DEFENSES" or "ARMOR")
            return Path("Units and combat", "Unit design", "Armor systems");
        if (key is "REACTORS" or "REACTORDESC" || title.Contains("reactor", StringComparison.Ordinal))
            return Path("Units and combat", "Unit design", "Reactor systems");

        if (key.StartsWith("UNITDESC", StringComparison.Ordinal))
        {
            if (NativeUnits.Contains(title)) return Path("Units and combat", "Unit types", "Native life forms");
            if (ProgenitorUnits.Contains(title)) return Path("Units and combat", "Unit types", "Progenitor war machines");
            if (UnityUnits.Contains(title)) return Path("Units and combat", "Unit types", "Unity expedition units");
            return Path("Units and combat", "Unit types", "Colonization, exploration, and support units");
        }

        if (key.StartsWith("HELPTERRA", StringComparison.Ordinal))
        {
            if (ResourceTerraforming.Contains(title)) return Path("Planet and terraforming", "Terraforming", "Resource improvements");
            if (InfrastructureTerraforming.Contains(title)) return Path("Planet and terraforming", "Terraforming", "Roads, sensors, and military infrastructure");
            return Path("Planet and terraforming", "Terraforming", "Water, elevation, and ecological engineering");
        }

        if (file.StartsWith("alphax", StringComparison.Ordinal))
        {
            if (key is "TERRAIN" or "RESOURCEINFO" or "RESOURCES" or "NATURAL")
                return Path("Planet and terraforming", "Terrain, climate, and resources");
            if (key is "WORLDBUILDER" or "WORLDSIZE")
                return Path("Planet and terraforming", "World generation and landmarks");
            if (key == "TECHNOLOGY")
                return Path("Research and technology", "Research rules and artifacts");
            if (key is "UNITS" or "ORDERS" or "COMPASS" or "PLANS" or "TRIAD" or "DEFENSEMODES" or "OFFENSEMODES")
                return Path("Units and combat", "Combat and operations", "Orders, automation, and strategic roles");
            if (key == "MORALE")
                return Path("Units and combat", "Combat and operations", "Morale, damage, and unit cost");
            if (key is "FACILITIES" or "CITIZENS")
                return Path("Bases and economy", "Base administration and reference data");
            if (key == "ENERGY")
                return Path("Bases and economy", "Energy, economy, psych, and labs");
            if (key is "FACTIONS" or "NEWFACTIONS" or "CUSTOMFACTIONS" or "MANDATE" or "MOOD" or "MIGHT")
                return key is "MANDATE" or "MOOD" or "MIGHT"
                    ? Path("Factions and leaders", "Attitudes, strength, and alien culture")
                    : Path("Factions and leaders", "Faction identities and customization");
            if (key is "REPUTE" or "PROPOSALS")
                return key == "PROPOSALS"
                    ? Path("Diplomacy and society", "Planetary Council and diplomatic victory")
                    : Path("Diplomacy and society", "Commerce, reputation, and atrocities");
            if (key is "RULES" or "DIFF" or "TIMECONTROLS")
                return Path("Rules and game setup", "Match rules, difficulty, and time controls");
        }

        return ClassifyConcept(page, text, title);
    }

    public static CorpusCollectionMetadata MetadataFor(IReadOnlyList<string> path)
    {
        var key = PathKey(path);
        return Metadata.TryGetValue(key, out var metadata)
            ? metadata
            : throw new InvalidOperationException($"No semantic Collection metadata is registered for '{string.Join(" / ", path)}'.");
    }

    public static bool IsRegistered(IReadOnlyList<string> path) => Metadata.ContainsKey(PathKey(path));

    public static IReadOnlyDictionary<string, string> TechnologyRoles(IEnumerable<CorpusPage> pages)
    {
        var configuration = pages.FirstOrDefault(page =>
            page.SourceFile.Equals("alphax.txt", StringComparison.OrdinalIgnoreCase) &&
            page.SourceKey.Equals("TECHNOLOGY", StringComparison.OrdinalIgnoreCase));
        if (configuration is null) return new Dictionary<string, string>(StringComparer.Ordinal);

        var roles = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var rawLine in StripLeadingTitle(configuration.Body).Split('\n'))
        {
            var line = rawLine.Trim();
            if (line.Length == 0 || line.StartsWith('#')) continue;
            var fields = line.Split(',').Select(item => item.Trim()).ToArray();
            if (fields.Length < 6 || !int.TryParse(fields[2], out var conquer) ||
                !int.TryParse(fields[3], out var discover) || !int.TryParse(fields[4], out var build) ||
                !int.TryParse(fields[5], out var explore)) continue;
            if (NameKey(fields[0]) is "deleted" or "usertechnology") continue;
            var values = new[] { conquer, discover, build, explore };
            var maximum = values.Max();
            var winners = values.Select((value, index) => (value, index)).Where(item => item.value == maximum).ToArray();
            roles[NameKey(fields[0])] = winners.Length == 1
                ? new[] { "Conquer", "Discover", "Build", "Explore" }[winners[0].index]
                : "Cross-disciplinary";
        }
        return roles;
    }

    private static IReadOnlyList<string> ClassifyConcept(CorpusPage page, string text, string title)
    {
        if (title is "tableofcontents" or "sidmeiersalphacentauri" or "importantnotetotranslators")
            return Path("Rules and game setup", "General mechanics and terminology");

        if (title.StartsWith("victory", StringComparison.Ordinal) || title is "scoring" or "retirement")
            return Path("Rules and game setup", "Victory, scoring, and retirement");

        if (title is "nutrients" or "minerals" or "resourceproduction" or "resourcetypes")
            return Path("Bases and economy", "Nutrients, minerals, and production");
        if (title is "energy" or "energygrid" or "energyreserves" or "energyrules" or
            "economyenergyallocation" or "labs" or "psych")
            return Path("Bases and economy", "Energy, economy, psych, and labs");
        if (title is "citizentypes" or "drones" or "dronesadvanced" or "droneriots" or "talents" or "goldenage")
            return Path("Bases and economy", "Citizens, drones, talents, and growth");
        if (title is "bureaucracy" or "inefficiencybasic" or "inefficiencyadvanced" or "facilities" or
            "facilityandprojectconfiguration" or "buildgovernor" or "conquergovernor" or
            "discovergovernor" or "exploregovernor")
            return Path("Bases and economy", "Base administration and reference data");

        if (title is "bloodtruce" or "treatyoffriendship" or "pactofbrotherhood" or "vendetta" or "diplomacy" or "territoryborders")
            return Path("Diplomacy and society", "Treaties, pacts, territory, and conflict");
        if (title is "atrocities" or "commercebasic" or "commerceadvanced" or "integrity" or "reputationlevels")
            return Path("Diplomacy and society", "Commerce, reputation, and atrocities");
        if (title is "planetarycouncil" or "planetarycouncilproposals" or "councilproposals" or "planetarygovernor")
            return Path("Diplomacy and society", "Planetary Council and diplomatic victory");

        if (title is "factions" or "originalfactiondefinitions" or "aliencrossfirefactiondefinitions" or "customfactions" or "factionbonusterminology")
            return Path("Factions and leaders", "Faction identities and customization");
        if (title is "factiondiplomaticmoods" or "factionstrengthcomparisons" or "might" or "mightformula" or
            "progenitors" or "manifolds" or "resonance")
            return Path("Factions and leaders", "Attitudes, strength, and alien culture");

        if (title is "altitude" or "rainfall" or "rockiness" or "terraindefinitions")
            return Path("Planet and terraforming", "Terrain, climate, and resources");
        if (title is "nativelife" or "fungusxenofungus" or "ecologybasic" or "ecologyadvanced")
            return Path("Planet and terraforming", "Xenofungus, native life, and ecology");
        if (title is "worldgenerationrules" or "worldsizes" or "landmarksvolcanosetc" or "monoliths")
            return Path("Planet and terraforming", "World generation and landmarks");
        if (title is "terraforming")
            return Path("Planet and terraforming", "Terraforming", "Water, elevation, and ecological engineering");

        if (title is "artifacts" or "researchconfiguration")
            return Path("Research and technology", "Research rules and artifacts");

        if (title is "bombardment" or "psicombat" or "zoneofcontrol" or "offensivecombatmodes" or "defensivecombatmodes")
            return Path("Units and combat", "Combat and operations", "Combat, bombardment, and zones of control");
        if (title is "damageandrepair" or "morale" or "moralelevels" or "unitcostbasic" or "unitcostadvanced")
            return Path("Units and combat", "Combat and operations", "Morale, damage, and unit cost");
        if (title is "disengage" or "patrol" or "waypoints" or "unitorders" or "unitstrategicroles" or "mapdirections" or "predefinedunitconfiguration")
            return Path("Units and combat", "Combat and operations", "Orders, automation, and strategic roles");
        if (title == "prototypes")
            return Path("Units and combat", "Unit design", "Unit design rules");

        if (text.Contains("diplom") || text.Contains("treaty") || text.Contains("pact") || text.Contains("truce") || text.Contains("vendetta"))
            return Path("Diplomacy and society", "Treaties, pacts, territory, and conflict");
        if (text.Contains("citizen") || text.Contains("drone") || text.Contains("talent") || text.Contains("base"))
            return Path("Bases and economy", "Base administration and reference data");
        if (text.Contains("planet") || text.Contains("terrain") || text.Contains("fungus") || text.Contains("ecolog"))
            return Path("Planet and terraforming", "Xenofungus, native life, and ecology");
        if (text.Contains("unit") || text.Contains("combat") || text.Contains("attack") || text.Contains("defense"))
            return Path("Units and combat", "Combat and operations", "Combat, bombardment, and zones of control");
        return Path("Rules and game setup", "General mechanics and terminology");
    }

    private static IReadOnlyList<string> SocialEffectPath(string title)
    {
        var normalized = NameKey(title);
        if (normalized.StartsWith("economy") || normalized.StartsWith("efficiency") || normalized.StartsWith("industry"))
            return Path("Diplomacy and society", "Social engineering", "Economic and industrial effects");
        if (normalized.StartsWith("growth") || normalized.StartsWith("police") || normalized.StartsWith("talent"))
            return Path("Diplomacy and society", "Social engineering", "Population and governance effects");
        if (normalized.StartsWith("morale") || normalized.StartsWith("probe") || normalized.StartsWith("support"))
            return Path("Diplomacy and society", "Social engineering", "Military and security effects");
        return Path("Diplomacy and society", "Social engineering", "Research and Planet effects");
    }

    private static bool IsFacility(string key, string title) =>
        key.StartsWith("HELPFAC", StringComparison.Ordinal) ||
        PopulationFacilities.Contains(NameKey(title)) || EconomyResearchFacilities.Contains(NameKey(title)) ||
        IndustryResourceFacilities.Contains(NameKey(title)) || MilitaryFacilities.Contains(NameKey(title)) ||
        PlanetFacilities.Contains(NameKey(title)) || AdvancedFacilities.Contains(NameKey(title));

    private static bool IsKnownProject(string title) => PopulationProjects.Contains(title) || EconomyProjects.Contains(title) ||
        ResearchProjects.Contains(title) || MilitaryProjects.Contains(title) || PlanetProjects.Contains(title);

    private static IReadOnlyList<string> Path(params string[] path) => [Root, .. path];
    private static string PathKey(IEnumerable<string> path) => string.Join('\u001f', path);
    private static HashSet<string> Names(params string[] values) =>
        values.Select(NameKey).ToHashSet(StringComparer.Ordinal);
    private static string NameKey(string value) => Regex.Replace(
        Regex.Replace(value.Trim(), @"^(?:the|a|an)\s+", "", RegexOptions.IgnoreCase),
        @"[^a-z0-9]+", "", RegexOptions.IgnoreCase).ToLowerInvariant();
    private static string StripLeadingTitle(string body) => Regex.Replace(body, @"^#[^\n]*\n+", "").Trim();

    private static Dictionary<string, CorpusCollectionMetadata> BuildMetadata()
    {
        var result = new Dictionary<string, CorpusCollectionMetadata>(StringComparer.Ordinal);
        void Add(string[] path, string description, params string[] tags) =>
            result.Add(PathKey(path), new(description, ["smacx", "rules", .. tags]));

        Add([Root], "The locally built factual rules library for Sid Meier's Alpha Centauri and Alien Crossfire, organized for semantic routing and human browsing.", "datalinks", "alien-crossfire", "game-mechanics");

        Add([Root, "Rules and game setup"], "Match-wide rules, difficulty, clocks, victory conditions, scoring, and general mechanics that shape an entire game.", "game-rules", "match-setup", "victory");
        Add([Root, "Rules and game setup", "Match rules, difficulty, and time controls"], "Core rules, difficulty effects, and multiplayer time-control settings chosen when a match is configured.", "difficulty", "time-controls", "match-rules");
        Add([Root, "Rules and game setup", "Victory, scoring, and retirement"], "Victory conditions, defeat, final scoring, retirement, and the ways a match can end.", "victory-conditions", "scoring", "retirement");
        Add([Root, "Rules and game setup", "General mechanics and terminology"], "General factual mechanics and terminology that do not belong to a narrower strategic system.", "core-mechanics", "terminology");

        Add([Root, "Bases and economy"], "Base growth, citizens, resources, production, facilities, secret projects, governors, and economic administration.", "bases", "economy", "production");
        Add([Root, "Bases and economy", "Nutrients, minerals, and production"], "How bases collect nutrients and minerals, support population, and turn mineral output into production.", "nutrients", "minerals", "production");
        Add([Root, "Bases and economy", "Energy, economy, psych, and labs"], "Energy production and allocation among credits, psych, and research, including reserves and faction energy systems.", "energy", "economy", "psych", "labs");
        Add([Root, "Bases and economy", "Citizens, drones, talents, and growth"], "Citizen roles, drones, talents, riots, Golden Ages, population growth, and base quality of life.", "citizens", "drones", "talents", "growth");
        Add([Root, "Bases and economy", "Base administration and reference data"], "Base governors, bureaucracy, inefficiency, facility/project configuration, and other base-management reference rules.", "base-management", "governors", "inefficiency");
        Add([Root, "Bases and economy", "Facilities"], "Base facilities grouped by the gameplay systems they change, including economy, population, industry, military readiness, ecology, and orbital infrastructure.", "facilities", "base-improvements");
        Add([Root, "Bases and economy", "Facilities", "Population, health, and psych facilities"], "Facilities that change population limits, drones, talents, psych output, health, disease resistance, or growth.", "population", "health", "psych", "drones");
        Add([Root, "Bases and economy", "Facilities", "Economy, research, and energy facilities"], "Facilities that generate or multiply economy, energy, laboratory output, research, or administrative efficiency.", "economy", "research", "energy", "labs");
        Add([Root, "Bases and economy", "Facilities", "Industry and resource facilities"], "Facilities that increase minerals, nutrients, forest or sea-square yields, production capacity, or industrial output.", "industry", "resources", "minerals", "nutrients");
        Add([Root, "Bases and economy", "Facilities", "Military, aerospace, and security facilities"], "Training, repair, defense, aerospace, probe security, sensors, and prototype-support facilities.", "military", "defense", "aerospace", "security");
        Add([Root, "Bases and economy", "Facilities", "Ecology and native-life facilities"], "Facilities that reduce ecological damage or improve the lifecycle, cost, and policing of native life forms.", "ecology", "native-life", "planet");
        Add([Root, "Bases and economy", "Facilities", "Orbital and specialized infrastructure"], "Orbital installations and specialized facilities for satellites, teleportation, submersion survival, strategic defense, and Progenitor victory.", "orbital", "satellites", "psi-gate", "specialized");
        Add([Root, "Bases and economy", "Secret projects"], "Unique faction-wide Secret Projects grouped by their principal strategic effects.", "secret-projects", "unique-projects");
        Add([Root, "Bases and economy", "Secret projects", "Population and society projects"], "Secret Projects that alter population growth, citizens, drones, talents, policing, or social stability.", "population", "society", "drones", "talents");
        Add([Root, "Bases and economy", "Secret projects", "Economy and infrastructure projects"], "Secret Projects that improve minerals, energy, support, transportation, construction, or faction-wide infrastructure.", "economy", "infrastructure", "industry");
        Add([Root, "Bases and economy", "Secret projects", "Research and information projects"], "Secret Projects that accelerate research, share discoveries, improve networks, or transform information systems.", "research", "information", "networks");
        Add([Root, "Bases and economy", "Secret projects", "Military, security, and intelligence projects"], "Secret Projects that strengthen combat forces, defenses, morale, aerospace, probe security, or psi warfare.", "military", "security", "intelligence", "psi");
        Add([Root, "Bases and economy", "Secret projects", "Planet and transcendence projects"], "Secret Projects connected to Planet, native life, terraforming, diplomacy, the Voice of Planet, and Transcendence.", "planet", "native-life", "transcendence");

        Add([Root, "Diplomacy and society"], "Treaties, conflict, commerce, reputation, the Planetary Council, diplomatic victory, and Social Engineering.", "diplomacy", "society", "council");
        Add([Root, "Diplomacy and society", "Treaties, pacts, territory, and conflict"], "Contact, negotiation, territorial borders, Blood Truces, Treaties, Pacts, Vendettas, and the diplomatic consequences of conflict.", "treaties", "pacts", "vendetta", "territory");
        Add([Root, "Diplomacy and society", "Commerce, reputation, and atrocities"], "Commerce income, integrity and reputation, atrocities, sanctions, and the diplomatic consequences of faction behavior.", "commerce", "reputation", "integrity", "atrocities");
        Add([Root, "Diplomacy and society", "Planetary Council and diplomatic victory"], "Planetary Council membership, proposals, Governor powers, voting, and diplomatic victory.", "planetary-council", "governor", "voting", "diplomatic-victory");
        Add([Root, "Diplomacy and society", "Social engineering"], "Social Engineering choices and the society ratings they modify, organized by model category and mechanical effect.", "social-engineering", "society-ratings");
        Add([Root, "Diplomacy and society", "Social engineering", "Social engineering rules and categories"], "How Politics, Economics, Values, and Future Society choices combine to produce faction-wide social effects.", "politics", "economics", "values", "future-society");
        Add([Root, "Diplomacy and society", "Social engineering", "Politics models"], "Frontier, Police State, Democratic, and Fundamentalist political systems and their rating changes.", "politics", "government");
        Add([Root, "Diplomacy and society", "Social engineering", "Economics models"], "Simple, Free Market, Planned, and Green economic systems and their rating changes.", "economics", "free-market", "planned", "green");
        Add([Root, "Diplomacy and society", "Social engineering", "Values models"], "Survival, Power, Knowledge, and Wealth value systems and their rating changes.", "values", "power", "knowledge", "wealth");
        Add([Root, "Diplomacy and society", "Social engineering", "Future society models"], "None, Cybernetic, Eudaimonic, and Thought Control future societies and their rating changes.", "future-society", "cybernetic", "eudaimonic", "thought-control");
        Add([Root, "Diplomacy and society", "Social engineering", "Economic and industrial effects"], "Rules and configuration for Economy, Efficiency, and Industry ratings.", "economy-rating", "efficiency", "industry-rating");
        Add([Root, "Diplomacy and society", "Social engineering", "Population and governance effects"], "Rules and configuration for Growth, Police, and Talent-related social effects.", "growth-rating", "police-rating", "talents");
        Add([Root, "Diplomacy and society", "Social engineering", "Military and security effects"], "Rules and configuration for Morale, Probe, and Support ratings.", "morale-rating", "probe-rating", "support-rating");
        Add([Root, "Diplomacy and society", "Social engineering", "Research and Planet effects"], "Rules and configuration for Research and Planet ratings and their faction-wide consequences.", "research-rating", "planet-rating");

        Add([Root, "Factions and leaders"], "Faction identities, bonuses, customization, diplomatic attitudes, comparative strength, and Progenitor culture.", "factions", "leaders", "progenitors");
        Add([Root, "Factions and leaders", "Faction identities and customization"], "Original and Alien Crossfire faction definitions, bonuses, terminology, and rules for custom factions.", "faction-definitions", "faction-bonuses", "custom-factions");
        Add([Root, "Factions and leaders", "Attitudes, strength, and alien culture"], "Diplomatic moods, comparative might, Progenitors, Manifolds, resonance, and other faction-behavior or alien-culture concepts.", "diplomatic-moods", "might", "progenitors", "resonance");

        Add([Root, "Planet and terraforming"], "Planet's terrain, climate, resources, xenofungus, native ecology, world generation, landmarks, and terraforming systems.", "planet", "terraforming", "terrain");
        Add([Root, "Planet and terraforming", "Terrain, climate, and resources"], "Altitude, rainfall, rockiness, terrain definitions, bonus resources, and the production potential of map squares.", "terrain", "climate", "resources", "altitude");
        Add([Root, "Planet and terraforming", "Xenofungus, native life, and ecology"], "Xenofungus, native life, ecological damage, Planet's response, and the ecological rules governing settlement and industry.", "xenofungus", "native-life", "ecology");
        Add([Root, "Planet and terraforming", "World generation and landmarks"], "World sizes, map generation, continents and oceans, landmarks, volcanoes, and monoliths.", "world-generation", "world-size", "landmarks", "monoliths");
        Add([Root, "Planet and terraforming", "Terraforming"], "Former actions grouped by whether they improve resources, build infrastructure, or reshape Planet's water, elevation, and ecology.", "terraforming", "formers");
        Add([Root, "Planet and terraforming", "Terraforming", "Resource improvements"], "Farms, mines, forests, condensers, soil enrichers, mirrors, collectors, and boreholes that change tile yields.", "farms", "mines", "forests", "tile-yields");
        Add([Root, "Planet and terraforming", "Terraforming", "Roads, sensors, and military infrastructure"], "Roads, Mag Tubes, sensors, bunkers, and airbases that support movement, detection, defense, and air operations.", "roads", "mag-tubes", "sensors", "airbases");
        Add([Root, "Planet and terraforming", "Terraforming", "Water, elevation, and ecological engineering"], "Aquifers, fungus cultivation, terrain raising/lowering/leveling, and other actions that reshape Planet's hydrology or ecology.", "aquifers", "elevation", "fungus", "ecological-engineering");

        Add([Root, "Research and technology"], "Research rules, alien artifacts, and the technology tree organized by the game's Conquer, Discover, Build, and Explore priorities.", "research", "technology-tree", "artifacts");
        Add([Root, "Research and technology", "Research rules and artifacts"], "Research configuration, discovery mechanics, laboratory progress, alien artifacts, prerequisites, and technology-tree reference data.", "research-rules", "artifacts", "prerequisites");
        Add([Root, "Research and technology", "Technology tree"], "Individual technologies grouped by their highest strategic priority in the installed game's technology configuration.", "technologies", "conquer", "discover", "build", "explore");
        Add([Root, "Research and technology", "Technology tree", "Conquer-oriented advances"], "Technologies whose strongest configured priority is military power and conquest.", "conquer-technologies", "military-research");
        Add([Root, "Research and technology", "Technology tree", "Discover-oriented advances"], "Technologies whose strongest configured priority is scientific discovery and advancement of knowledge.", "discover-technologies", "science", "research");
        Add([Root, "Research and technology", "Technology tree", "Build-oriented advances"], "Technologies whose strongest configured priority is infrastructure, industry, and economic development.", "build-technologies", "infrastructure", "industry");
        Add([Root, "Research and technology", "Technology tree", "Explore-oriented advances"], "Technologies whose strongest configured priority is exploration, colonization, mobility, ecology, or expansion.", "explore-technologies", "exploration", "colonization");
        Add([Root, "Research and technology", "Technology tree", "Cross-disciplinary advances"], "Technologies tied for the strongest priority across more than one strategic research role.", "cross-disciplinary", "technology");

        Add([Root, "Units and combat"], "Unit design, predefined units, weapons, armor, reactors, special abilities, combat rules, morale, movement, and orders.", "units", "combat", "unit-design");
        Add([Root, "Units and combat", "Unit design"], "Rules and components used in the Design Workshop: chassis, weapons, armor, reactors, modules, prototypes, and special abilities.", "design-workshop", "prototypes", "components");
        Add([Root, "Units and combat", "Unit design", "Unit design rules"], "Prototype costs and the rules for combining unlocked components into custom unit designs.", "prototypes", "unit-design-rules");
        Add([Root, "Units and combat", "Unit design", "Chassis"], "Land, sea, and air chassis, including speed, range, cargo, movement domain, and prerequisite information.", "chassis", "land-units", "sea-units", "air-units");
        Add([Root, "Units and combat", "Unit design", "Armor systems"], "Conventional, psi, pulse, and resonance defensive systems and their combat strengths.", "armor", "defense", "psi-defense", "resonance-armor");
        Add([Root, "Units and combat", "Unit design", "Reactor systems"], "Reactor types and their effects on unit cost, hit points, weapon payloads, and destructive power.", "reactors", "hit-points", "unit-cost");
        Add([Root, "Units and combat", "Unit design", "Weapons and modules"], "Offensive weapons, psi and resonance systems, missiles, strategic payloads, and civilian/support modules.", "weapons", "modules", "payloads");
        Add([Root, "Units and combat", "Unit design", "Weapons and modules", "Conventional weapons"], "Direct-fire conventional weapons and their attack strengths, prerequisites, and costs.", "conventional-weapons", "attack-strength");
        Add([Root, "Units and combat", "Unit design", "Weapons and modules", "Psi and resonance weapons"], "Psi Attack and resonance weapons whose combat rules interact with native life and resonance fields.", "psi-weapons", "resonance-weapons");
        Add([Root, "Units and combat", "Unit design", "Weapons and modules", "Missiles and strategic payloads"], "Conventional missiles, Planet Busters, fungal payloads, and tectonic payloads, including atrocity and terrain effects.", "missiles", "planet-busters", "strategic-payloads", "atrocities");
        Add([Root, "Units and combat", "Unit design", "Weapons and modules", "Civilian and support modules"], "Modules for colony founding, terraforming, probes, supply convoys, cargo transport, and unit-configuration reference data.", "colony-modules", "terraforming-modules", "probe-teams", "transport");
        Add([Root, "Units and combat", "Unit design", "Special abilities"], "Optional unit abilities grouped by offensive, defensive, mobility, logistical, covert, policing, and terraforming roles.", "special-abilities", "unit-abilities");
        Add([Root, "Units and combat", "Unit design", "Special abilities", "Offensive combat abilities"], "Abilities that improve attacks, artillery, psi offense, special-ability countermeasures, or prohibited weapons.", "offensive-abilities", "artillery", "psi-attack");
        Add([Root, "Units and combat", "Unit design", "Special abilities", "Defensive abilities"], "Abilities that defend against air, fast ground, psi, or probe threats.", "defensive-abilities", "aaa", "hypnotic-trance");
        Add([Root, "Units and combat", "Unit design", "Special abilities", "Mobility, scouting, and deployment"], "Abilities for amphibious assault, airdrops, concealment, submarines, reconnaissance, range, and movement.", "mobility", "scouting", "drop-pods", "cloaking");
        Add([Root, "Units and combat", "Unit design", "Special abilities", "Logistics, policing, and covert abilities"], "Abilities for transport, repair, support, morale, marine capture, policing, and probe operations.", "logistics", "policing", "covert-operations", "repair");
        Add([Root, "Units and combat", "Unit design", "Special abilities", "Terraforming and native-life abilities"], "Abilities that accelerate Former work or clearing xenofungus.", "terraforming-abilities", "fungicide", "super-former");
        Add([Root, "Units and combat", "Unit types"], "Predefined human, Unity, native-life, and Progenitor units and their intended functions.", "predefined-units", "unit-types");
        Add([Root, "Units and combat", "Unit types", "Colonization, exploration, and support units"], "Colony Pods, Formers, probes, scouts, supply crawlers, transports, and other conventional support units.", "colony-pods", "formers", "supply-crawlers", "transports");
        Add([Root, "Units and combat", "Unit types", "Unity expedition units"], "Surviving Unity vehicles and equipment originally carried aboard the U.N.S. Unity.", "unity-units", "exploration");
        Add([Root, "Units and combat", "Unit types", "Native life forms"], "Mind worms, Isles, Locusts, fungal towers, spore launchers, sealurks, and their psi or ecological behavior.", "native-life", "mind-worms", "psi-combat");
        Add([Root, "Units and combat", "Unit types", "Progenitor war machines"], "Battle Ogres and other predefined Progenitor combat machines.", "progenitor-units", "battle-ogres");
        Add([Root, "Units and combat", "Combat and operations"], "Combat resolution, morale, damage, repair, movement restrictions, unit costs, orders, automation, and strategic roles.", "combat-rules", "unit-orders", "morale");
        Add([Root, "Units and combat", "Combat and operations", "Combat, bombardment, and zones of control"], "Conventional and psi combat, bombardment, offensive/defensive modes, disengagement, and zones of control.", "combat", "bombardment", "zone-of-control", "psi-combat");
        Add([Root, "Units and combat", "Combat and operations", "Morale, damage, and unit cost"], "Morale levels, damage, repair, reactor durability, and the formulas that determine unit mineral cost.", "morale", "damage", "repair", "unit-cost");
        Add([Root, "Units and combat", "Combat and operations", "Orders, automation, and strategic roles"], "Unit orders, map directions, waypoints, patrol, automation, predefined roles, and behavior used by governors or native AI.", "unit-orders", "automation", "strategic-roles", "waypoints");

        return result;
    }
}
