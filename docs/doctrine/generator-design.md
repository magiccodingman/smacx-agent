# Dynamic Gameplay Context Generator

This guide defines **match-assembly text** inserted into the Sovereign Gameplay Doctrine. It is implementation guidance and is **not** shown wholesale to the sovereign.

The generator exists to resolve public, seat-specific or match-specific rule conditions into plain language **before** the sovereign sees the doctrine. It is not a substitute for the runtime world model.

# 1. Static vs Dynamic Boundary

Use this rule for every sentence:

> **If the sentence would change or disappear solely because a different match was created, it belongs here, not in the static doctrine.**

Keep invariant mechanics in the doctrine even when they contain words like "if", "when", or "once". For example, "if two bases overlap, one tile cannot be worked by both" is an invariant base rule and remains static.

## Match-assembly dynamic context belongs here

Examples:

- the sovereign's loaded faction mechanics;
- the participating faction roster and each participant's public faction mechanics;
- world size and world-generation settings;
- difficulty;
- fixed advanced-rule flags;
- research mode;
- enabled victory routes;
- Cooperative Victory;
- Do or Die;
- Progenitor-specific rules when Progenitors participate.

## Volatile runtime state does **not** belong here

Do not regenerate the system prompt because any of these change:

- current diplomatic relationships;
- current commlink possession;
- current Charter state;
- current Council officeholder;
- current researched technologies;
- current units, bases, production, economy, or map knowledge;
- current threats, projects, votes, promises, plans, or wars;
- current available actions;
- current turn number.

Those belong in the fair-play world model, attention, memory, or request-local runtime context.

# 2. Explicitly Excluded Match Settings

The following settings are intentionally **not rendered into the gameplay doctrine**:

- `intense_rivalry`
- `random_leader_personalities`
- `random_leader_agendas`

Do not teach the sovereign built-in opponent behavioral scripts, controller-specific tendencies, or other implementation meta-knowledge. Public faction mechanics are enough; observed behavior belongs to evidence and memory.

Also do **not** render `time_control` into the gameplay doctrine while the sovereign has effectively unrestricted thinking time. If timed-turn gameplay is implemented later, design that as a separate reviewed capability rather than reviving an old placeholder automatically.

Opponent bullets do **not** include controller type. Do not label participants as human, agent, or built-in computer merely for strategic interpretation.

# 3. Assembly Rules

1. Insert only the branch that applies to the current seat and match.
2. Resolve settings into plain-language mechanical meaning; never show raw IDs or an options table.
3. Place each dynamic fact beside the static concept it modifies.
4. Do not leave conditional setup prose in the finished doctrine such as "if Diplomatic Victory is enabled". Resolve it to the enabled sentence or omit it.
5. Omit inactive rules when their absence has no strategic meaning. Render both states only when either state materially changes interpretation.
6. Do not repeat a fact in a top-level settings dump and again in the relevant section.
7. Describe world-generation values as generation targets or biases, never as hidden realized geography.
8. Include only information legitimately available to a human in the same seat.
9. For custom factions or modded rules, derive context from the actual loaded public rule data. Loaded data outranks stock fallback text.
10. Do not inject bot-only bonuses, behavioral logic, hidden agendas, or implementation-only difficulty effects.
11. Future/custom rules receive a named mapping beside the concept they modify. Do not add a generic "other rules" blob.
12. A rendered dynamic block contains declarative resolved text, not branching instructions.

# 4. Placeholder Manifest

The doctrine contains exactly these dynamic blocks:

- `{{SELF_FACTION_CONTEXT}}`
- `{{OPPONENT_FACTION_CONTEXT}}`
- `{{DIFFICULTY_BASE_CONTEXT}}`
- `{{WORLD_CONTEXT}}`
- `{{EXPLORATION_RULE_CONTEXT}}`
- `{{OPENING_RULE_CONTEXT}}`
- `{{PERSISTENCE_RULE_CONTEXT}}`
- `{{DIFFICULTY_ECOLOGY_CONTEXT}}`
- `{{RESEARCH_RULE_CONTEXT}}`
- `{{DIFFICULTY_SOCIAL_CONTEXT}}`
- `{{SPECIAL_DIPLOMACY_CONTEXT}}`
- `{{DIPLOMATIC_VICTORY_CONTEXT}}`
- `{{ENABLED_VICTORY_CONTEXT}}`
- `{{COOPERATIVE_VICTORY_CONTEXT}}`
- `{{PROGENITOR_VICTORY_CONTEXT}}`
- `{{ELIMINATION_RULE_CONTEXT}}`

Every placeholder must either render valid text or render an empty string according to the rules below. No other match-state prose should be injected elsewhere.

# 5. Faction and Participant Context

## `{{SELF_FACTION_CONTEXT}}`

Generate one compact paragraph containing:

- faction name and leader identity when public;
- exact public bonuses, penalties, prohibitions, starting technologies, units, facilities, or special abilities that materially affect play;
- one sentence translating those rules into strategic affordances and constraints;
- no fixed build order;
- no assumption that the sovereign follows the stock leader's agenda.

Preferred shape:

`You govern **{{FACTION_NAME}}**. {{PUBLIC_MECHANICS}} These mechanics make {{STRENGTHS}} unusually effective while making {{CONSTRAINTS}} important; they do not require a fixed strategy.`

Use the actual loaded faction definition whenever available. A stock fallback must never override loaded public rules.

### Stock fallback content inherited from v0.5

Use these only when the loaded public definition is unavailable and the stock rules are otherwise verified.

- **Gaia's Stepdaughters:** +2 EFFICIENCY, +1 PLANET, -1 conventional MORALE, -1 POLICE; Centauri Ecology; additional nutrient value from xenofungus; cannot adopt Free Market. Efficient administration, ecology, fungus, and native life are unusually strong; conventional troop quality and policing need more attention.
- **Human Hive:** +1 GROWTH, +1 INDUSTRY, -2 ECONOMY; Doctrine: Loyalty; free Perimeter Defenses; protection from negative EFFICIENCY effects; cannot adopt Democratic. Growth, production, defense, and wide administration are strong; Energy is structurally weaker.
- **University of Planet:** +2 RESEARCH, -2 PROBE; Information Networks plus an additional technology; Network Node at every base; extra Drone pressure; cannot adopt Fundamentalist. Research accelerates quickly, while unrest and covert vulnerability need attention.
- **Morgan Industries:** +1 ECONOMY, enhanced commerce, extra starting Energy Credits, -1 SUPPORT, and an early population ceiling until habitation infrastructure; cannot adopt Planned. Wealth gives flexibility while support and early population limits constrain conventional expansion.
- **Spartan Federation:** +2 MORALE, +1 POLICE, -1 INDUSTRY; Doctrine: Mobility and a Rover; prototype advantage; cannot adopt Wealth. Military quality and design flexibility are strong while construction is comparatively expensive.
- **Lord's Believers:** +2 SUPPORT, +1 PROBE, -2 RESEARCH, -1 PLANET, +25% offensive combat strength; Social Psych; cannot adopt Knowledge. Sustained military pressure and covert acquisition are strong; native research is slow.
- **Peacekeeping Forces:** -1 EFFICIENCY; Biogenetics; additional Talents; higher population ceiling; double Governor and Supreme Leader votes; cannot adopt Police State. Population, citizen stability, and Council politics are unusually powerful while administration is less efficient.
- **Cybernetic Consciousness:** +2 EFFICIENCY, +2 RESEARCH, -1 GROWTH; Applied Physics and Information Networks; technology gain through conquest; special Cybernetic-future interaction; cannot adopt Fundamentalist. Research and administration are strong while growth is slower.
- **Data Angels:** +2 PROBE, -1 POLICE; Information Networks, Planetary Networks, Probe Team; infiltration-related technology advantage; use the loaded prohibition/model rules when available rather than relying on memory. Intelligence and covert leverage are strong while conventional unrest control is weaker.
- **Free Drones:** +2 INDUSTRY, -2 RESEARCH; Industrial Base; reduced Drone pressure and riot-related advantages; cannot adopt Green. Material production and social resilience are strong while technology access needs solving.
- **Cult of Planet:** +2 PLANET, -1 INDUSTRY, -1 ECONOMY; Centauri Ecology, Social Psych, Mind Worm; native-life policing and later Brood Pit advantages; cannot adopt Wealth. Native life and ecology are exceptional while conventional construction and Energy are weaker.
- **Nautilus Pirates:** -1 GROWTH, -1 EFFICIENCY; sea start; Doctrine: Mobility and Doctrine: Flexibility; naval capability and enhanced ocean resources/infrastructure. Maritime expansion and mobility are strong while growth and administration are weaker.
- **Manifold Caretakers:** Progenitor faction; +1 PLANET and +25% defensive combat strength; several starting technologies; free Recycling Tanks; directed research. Development, defense, and Planet interaction are strong.
- **Manifold Usurpers:** Progenitor faction; +1 GROWTH, +1 MORALE, -1 PLANET, +25% offensive combat strength; several starting technologies; free Recycling Tanks; directed research; cannot adopt Democratic. Growth, early development, and military tempo are strong.

## `{{OPPONENT_FACTION_CONTEXT}}`

Emit one bullet for every other participating faction.

Each bullet contains:

- faction name;
- a compact summary of its public mechanical strengths and weaknesses;
- no control type;
- no personality prediction;
- no stock agenda inference;
- no hidden information.

Example shape:

`- **Morgan Industries:** exceptional Energy, commerce, and financial flexibility; weak support and a tight early population ceiling.`

The participant list itself is legitimate setup knowledge only to the extent the game exposes it to the seat.

# 6. World Context

## `{{WORLD_CONTEXT}}`

Emit five compact bullets in this order:

1. world size plus participant-density prior;
2. ocean coverage;
3. erosive forces;
4. cloud cover;
5. native life.

These are generation priors, not hidden realized map facts.

### World Size

Stock dimensions:

- Tiny: 24×48
- Small: 32×64
- Standard: 40×80
- Large: 44×90
- Huge: 64×128
- Custom: actual width × height

Baseline descriptions:

- Tiny: compressed world; contact, contested settlement, and response are generally earlier.
- Small: compact world with relatively short contact and reinforcement distances.
- Standard: baseline scale with moderate expansion and travel distances.
- Large: spacious world with more isolated development and longer logistical distances.
- Huge: very large world where separation can last longer and distant wars demand major logistics.

Append a participant-density clause derived from map area and participating faction count.

A deterministic implementation may compare raw tiles per faction with Standard seven-player density:

`density_ratio = (width × height / participant_count) / (40 × 80 / 7)`

Suggested labels:

- `< 0.60`: extremely crowded — little uncontested room and very early contact pressure
- `0.60–0.84`: crowded — constrained expansion and likely early borders
- `0.85–1.24`: moderate — no strong density conclusion
- `1.25–1.74`: spacious — more room and later contact are plausible
- `>= 1.75`: very sparse — prolonged isolation and long distances are plausible

Do not expose the formula or ratio. Ocean coverage may sharpen the land-pressure interpretation.

For Custom size, compare total area with stock sizes and mention unusual shape when relevant. Do not claim exact contact timing.

### Ocean Coverage

- Low: target roughly 30–50% ocean. Connected land is more likely; land expansion and land warfare are comparatively important, though naval access can still matter.
- Medium: target roughly 50–70% ocean. Multiple landmasses and meaningful sea lanes are likely; exploration, transports, and naval control can determine contact and war.
- High: target roughly 70–90% ocean. Land is comparatively scarce and fragmented; sea bases, naval mobility, transports, and later air projection are likely to matter strongly.

### Erosive Forces

- Strong: favors flatter/rolling terrain, easier movement and development, fewer extreme rugged regions.
- Average: no strong global flat/rugged bias.
- Weak: favors rugged/mountainous terrain, more rocky/mineral/defensible ground, harder movement and development.

### Cloud Cover

- Sparse: favors a drier world with less rainy nutrient-rich terrain.
- Average: no strong global wet/dry bias.
- Dense: favors a wetter world with more rainy nutrient-rich terrain and greater natural food potential.

### Native Life

- Rare: native organisms and fungus-related danger/opportunity are less frequent.
- Average: no extreme global native-life bias.
- Abundant: native organisms and fungus are common; append one brief implication that psi preparation, PLANET effects, safe exploration, and ecological interaction matter earlier and more often.

Do not add a second native-life placeholder elsewhere.

# 7. Difficulty Context

Difficulty context includes only effects that alter the sovereign's own rules or shared mechanics. Do not include opponent-only bonuses.

## `{{DIFFICULTY_BASE_CONTEXT}}`

Describe the baseline natural contentment threshold before other modifiers:

- Citizen: first six citizens naturally content before other effects; bureaucracy pressure comparatively forgiving.
- Specialist: first five naturally content; bureaucracy pressure relatively forgiving.
- Talent: first four naturally content; bureaucracy pressure more important.
- Librarian: first three naturally content; bureaucracy pressure stricter.
- Thinker: first two naturally content; wide expansion creates severe bureaucracy pressure unless EFFICIENCY and unrest control keep pace.
- Transcend: first citizen naturally content; bureaucracy pressure is harshest.

Faction effects, Talents, conquered populations, facilities, Projects, and Social Engineering can change the final result. This sentence is a baseline, not a complete Drone calculation.

## `{{DIFFICULTY_ECOLOGY_CONTEXT}}`

Render only at Thinker or Transcend:

`This difficulty uses harsher eco-damage rules, so intensive mineral production and disruptive terraforming provoke Planet more readily than on lower difficulties.`

Otherwise empty.

## `{{DIFFICULTY_SOCIAL_CONTEXT}}`

Citizen:

`At Citizen difficulty, changing Social Engineering does not cost Energy Credits.`

Specialist through Transcend:

`Changing Social Engineering costs Energy Credits at this difficulty, and higher difficulty increases that cost; repeated switching is therefore an economic decision as well as a policy decision.`

If the legal choice already exposes the exact resolved cost, do not duplicate a formula here.

Difficulty's research burden is emitted inside `{{RESEARCH_RULE_CONTEXT}}`.

# 8. Opening and Persistence Rules

## `{{OPENING_RULE_CONTEXT}}`

Assemble only applicable start-state clauses.

### Look First

Render only when enabled **and** a normal initial Colony Pod placement decision still exists:

`Look First is enabled: the initial Colony Pod is not forced to found immediately, so the starting area can be evaluated before choosing the first base location.`

If the actual start makes this irrelevant, omit it.

### Time Warp

Render only when enabled:

`Time Warp is enabled: the match begins from an accelerated developed position with multiple bases, technologies, and improvements. Evaluate the actual starting state rather than following assumptions from an ordinary Planetfall opening.`

## `{{PERSISTENCE_RULE_CONTEXT}}`

### Iron Man

Render only when enabled and the rule is relevant to the sovereign's allowed play:

`Iron Man is enabled: ordinary save-and-restore rollback is restricted and final score is doubled. This does not otherwise change ordinary turn mechanics.`

Otherwise empty.

# 9. Exploration Rule Context

## `{{EXPLORATION_RULE_CONTEXT}}`

Assemble from Unity Survey, Unity Scattering, and Random Events.

### Unity Survey

Render either branch because the difference determines whether undiscovered geography begins known or unknown.

Enabled:

`Unity Survey is available: broad planetary geography is known from the start, but ordinary fog of war still hides detailed current information and units.`

Disabled:

`Unity Survey is unavailable: unexplored planetary geography begins unknown and must be discovered or legitimately learned.`

### Unity Scattering

Render either branch because it changes the spatial prior for Unity Pods.

Enabled:

`Unity Pods are scattered broadly across Planet, so pods may be found far beyond faction landing regions.`

Disabled:

`Unity Pods are concentrated around faction landing regions rather than broadly scattered across Planet.`

### Random Events

If disabled:

`Random Events are suppressed in this match.`

If enabled, emit one resolved sentence with the earliest eligible turn derived from difficulty:

`Random Events are enabled; qualifying events may begin after turn {{EARLIEST_EVENT_TURN}}.`

Mapping inherited from v0.5:

- Citizen: 75
- Specialist: 65
- Talent: 55
- Librarian: 45
- Thinker: 35
- Transcend: 25

Do not repeat this timing in the difficulty section.

# 10. Research Rule Context

## `{{RESEARCH_RULE_CONTEXT}}`

Combine:

- Blind/Directed Research;
- Progenitor override if applicable;
- Tech Stagnation;
- Spoils of War;
- difficulty research burden.

### Blind or Directed Research

Ordinary faction with Blind Research enabled:

`Blind Research is enabled: choose broad Explore, Discover, Build, and Conquer priorities rather than freely selecting an exact technology.`

Blind Research disabled:

`Directed Research is enabled: choose a specific currently available technology, subject to prerequisites and the choices exposed by the game.`

Progenitor override:

`Although Blind Research is enabled globally, your Progenitor faction uses Directed Research and can choose a specific available technology.`

### Tech Stagnation

Enabled only:

`Tech Stagnation is enabled: discoveries take substantially longer, so existing unit generations, infrastructure, and military eras remain relevant for more turns.`

Disabled: omit.

### Spoils of War

Enabled only:

`Spoils of War is enabled: capturing an enemy base can grant a technology known by that faction and unknown to you, adding a technological reward to conquest.`

Disabled: omit.

Faction-specific technology-on-capture abilities remain independent and belong in faction context.

### Difficulty research burden

Append one compact clause:

- Citizen: research costs comparatively forgiving.
- Specialist: research costs comparatively forgiving.
- Talent: moderate research cost.
- Librarian: greater research investment.
- Thinker: expensive research.
- Transcend: highest research burden.

# 11. Special Diplomacy Context

## `{{SPECIAL_DIPLOMACY_CONTEXT}}`

Normally empty.

When Progenitor participation makes special public diplomacy rules relevant, insert only the exact loaded/verified restrictions the sovereign is entitled to know.

Stock summary inherited from v0.5:

`Human-Progenitor communication requires the relevant cross-species technology, and ordinary Pacts between human and Progenitor factions are restricted. The two Progenitor factions cannot make peace with one another.`

Do not add Progenitor-specific mechanics to unrelated static sections merely because they exist in the game.

# 12. Planetary Council and Diplomatic Victory

## `{{DIPLOMATIC_VICTORY_CONTEXT}}`

If Diplomatic Victory is disabled: empty.

If enabled for a sovereign eligible for the ordinary human route:

`Diplomatic Victory is enabled. After the required technology permits a Supreme Leader election, eligible leading factions can stand for office. Winning requires 75% of all Council votes, not merely a simple majority. Factions may defy the result; holdouts must then be defeated or subjugated before victory is complete. Progenitor presence can impose additional requirements.`

If the route is globally enabled but the sovereign's faction cannot win by the ordinary human route, state that directly and describe the Supreme Leader path as an opponent victory threat rather than its own path.

The static Council list intentionally does **not** mention Supreme Leader conditionally. This block owns that match-specific rule.

# 13. Victory Context

## `{{ENABLED_VICTORY_CONTEXT}}`

Start with:

`Enabled standard victory paths: {{ENABLED_NAMES}}.`

Then define only enabled standard paths.

### Conquest

`**Conquest:** win by eliminating, conquering, or subjugating the remaining independent opposition as the current rules require. Peace, alliances, technology, economy, and preparation can all serve an eventual conquest; the path does not require constant war.`

### Economic

`**Economic:** after discovering Planetary Economics and accumulating the required Energy Credits, a faction can attempt to corner the Global Energy Market. The game then announces a countdown during which opponents can stop the victory by capturing or destroying the initiating faction's Headquarters. Wealth must therefore be paired with security and the ability to survive the countdown.`

### Diplomatic

Do not repeat the full definition here when `{{DIPLOMATIC_VICTORY_CONTEXT}}` already explains it in the Council section. Listing the enabled name is enough.

### Transcendence

`**Transcendence:** an eligible human faction can follow the late scientific and Planetary sequence through The Voice of Planet and then construct The Ascent to Transcendence. Completing the Ascent wins immediately.`

If the sovereign's loaded faction is barred from a globally enabled standard route, state that in self-faction context.

Do not emit paragraphs for disabled standard paths.

## `{{COOPERATIVE_VICTORY_CONTEXT}}`

Always render because both states materially change the meaning of alliances at the endgame.

Enabled:

`Cooperative Victory is enabled: qualifying Pact partners can share applicable standard victories, so an alliance does not necessarily have to dissolve before the endgame. Special victory paths can have exceptions.`

Disabled:

`Cooperative Victory is disabled: Treaty, Pact, friendship, or a private promise does not make factions joint mechanical winners. Cooperation may continue, but the enabled victory rules ultimately distinguish the winner.`

## `{{PROGENITOR_VICTORY_CONTEXT}}`

Omit when no Progenitor participates.

Otherwise:

`**Progenitor Victory:** a Progenitor faction wins by completing six Subspace Generators in bases of size 10 or greater, thereby assembling the Resonance Communicator. This special victory cannot be shared cooperatively. For a non-Progenitor sovereign it is an opponent victory threat; for a Progenitor sovereign it is an available route.`

## `{{ELIMINATION_RULE_CONTEXT}}`

Render either Do or Die branch because the difference changes what elimination means even when Conquest Victory is disabled.

Enabled:

`Do or Die is enabled: when a faction loses all bases and is eliminated, it does not receive the ordinary early-game chance to escape and restart elsewhere.`

Disabled:

`Do or Die is disabled: an eliminated faction may, when the game's eligibility conditions are met, escape and restart elsewhere instead of being permanently removed immediately.`

# 14. Future and Custom Rules

For any future active match setting:

1. decide whether it is an invariant mechanic, a fixed match condition, or volatile runtime state;
2. keep invariant mechanics in the doctrine;
3. map fixed public match conditions to a named dynamic block beside the concept they modify;
4. keep volatile runtime state out of the system prompt;
5. define the active state and mechanical consequence;
6. state a likely false inference when useful;
7. render one resolved branch only.

Preferred rendered form:

`{{RULE_NAME}} is active: {{TRIGGER_OR_SCOPE}} causes {{MECHANICAL_EFFECT}}. It does not imply {{LIKELY_FALSE_INFERENCE}}.`

Do not add a generic runtime settings dump. Do not add a new placeholder until the rule exists and its player-facing meaning is known.
