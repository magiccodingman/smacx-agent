# Game Semantics Coverage Matrix

This matrix defines the implemented perceptual/mechanical boundary. “Native”
means a reviewed UI-thread bridge adapter; “derived” means a deterministic
calculation over perspective-known fields. `decision/choice` denotes the guarded
opaque action surface. Journal entries and checkpoint snapshots are scoped by
match, agent, perspective, and timeline. No row grants hidden state.

| Domain | Native source | Visibility / entitlement | Push attention and deltas | Pull query | Action surface | Provenance and rollback | Regression evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Map and mutable terrain | paged tiles, semantic snapshot | owned/visible plus native remembered items; Unity Survey terrain/altitude only when native-entitled; unseen resources/ownership remain unknown | changed known location; coastline/road changes re-anchor | world area/route/reachability/render | opaque Former, road/tube, airdrop and target choices | current/stale native fields; snapshot rebuilt on rollback | topology, fair-play, terrain-destruction, strategic fixtures |
| Physical land/ocean masses and coastlines | native-shaped known tile terrain | perspective-known current/stale terrain only | terrain split/merge invalidates lineage; ownership/diplomacy does not | world overview/area/relation | none | versioned terrain-connectivity identity is separate from mobility topology; complete server-held region identities remain queryable outside LOD; paginated world-geography discovery; relations distinguish unresolved fog connections from closed mapped components | geographic identity, coastal mobility-endpoint, terrain split/merge and Huge-fragmented fixtures |
| Territorial composition and ownership interfaces | current/stale known tile owner plus visible/remembered bases | field-level current/stale ownership; unknown is never counted as owned | ownership and relationship composition re-anchor independently from physical identity | world overview/area/relation | none | mechanical differently-owned square adjacency is never promoted into a political border claim | native-shaped territory, diplomacy/ownership invariance and ownership-interface fixtures |
| Resources and natural landmarks | known tile resources/improvements plus currently visible native named-landmark center receipt | current visible landmark identity; remembered projection becomes explicitly stale in fog | changed known resources/landmarks re-anchor | world overview/area/compare | native terrain actions remain guarded | stable landmark refs derive from visible named centers; mass summaries bound counts and representatives | native-shaped resource/landmark freshness and geographic aggregation fixture |
| Exploration frontiers | missing neighbors of perspective-known physical components | unknown geography is not enumerated or inferred | component/frontier changes and salient events promote detail | world overview; lazy area query for scout ETA | none | contiguous unknown boundaries expose possibility, provenance, known-component connection qualification, nearby known resources/landmarks and coastline; routes are computed only on demand | peninsula unknown-connection, stale/current and lazy-scout fixtures |
| Cross-region operational theaters | active known contacts/bases/routes plus focus, operations, plans, watches and recent material events | perspective-known mechanics only | active/recent/plan-linked dependencies promote while unrelated geography demotes | world overview/area/logistics | none | local interacting participants and explicit route subjects may join across mobility/geographic regions; giant-region coincidence and adjacency alone do not join crises | multi-front amphibious/allied, quiet-plan promotion and Huge chaotic fixtures |
| Expansion/base-site mechanics | target-specific bounded native legality/yield/radius receipt plus known topology | receipt requires a currently visible nominated location; hidden rejection reasons excluded | queried site dependency changes invalidate receipt/query | world compare | founding remains an opaque current-unit choice | legal land/sea founding, current yields, radius visibility, known overlap, resources, ownership, connectivity, transport, hostile proximity and frontier exposure are facts; no best site | native receipt source contract and materially different candidate fixture |
| Repair and staging logistics | owned damage/roles, known facilities/features, current repair-rule snapshot | own units and mechanically usable owned/Pact infrastructure; foreign unknown remains unknown | damage, access, facility, project and route changes | world logistics/route | ordinary unit movement only | damaged-unit arrival options, repair mechanisms/modifiers, refuel and staging-base timings are subject-relative and never ranked | repair-rule, stale Pact/no-riot, current owned access, feasible ferry, no-transport and staging fixtures; predicted healing totals remain native-authoritative |
| Base worker/economic geography | bases, citizens, base radius/yields | owned full; visible foreign base identity only | base appeared/lost, riots, production and threat changes | world base/area/compare | production, queue, governor, citizens, hurry, recycle, rename | native fields plus derived completion/response; journal action | base observation/management/citizen/purchase/status tests |
| Own units and support | paged units plus rich unit adapter | owned full only | ready/order/location/damage/lifecycle changes | world forces/logistics/relation | opaque unit actions and persistent orders | monotonic semantic refs survive native compaction and verified checkpoint/process recovery; nested native support cost is projected while Clean/no-support units are excluded | unit lifecycle/orders/ready refs, private identity restart, support-shape and persistent-orders fixtures |
| Foreign contacts | visible native units plus bounded semantic movement/damage/destruction feed | current visibility or qualified temporal occurrence; feed-owned episode checkpoints survive publications, while gaps and unproved snapshot cuts do not preserve identity | contact appeared/moved/damaged/destroyed/lost is critical; confirmed destruction supersedes lost state | world forces/intel/changes | combat/probe choices only when natively legal | opaque visible-episode identity; lost envelope includes residual disappearance motion plus every refreshed unseen turn and remains a superset; unrelated continuous contacts retain identity | fair-play honeytokens, 17 cross-publication/gap adversaries, six transient publication cases, native continuity, destruction, phase-superset, rendezvous, fog pursuit and combat tests |
| Combat and bombardment | visible stack/odds/modal adapters | only visible participants and native dialog terms | damage/destruction/contact deltas and blocking focus | forces/relation/response | guarded combat, artillery, bombing, missile, nerve gas, self-destruct | current native outcome; no unseen collateral claims | artillery, bombing, confirmation, nerve-gas, missile tests |
| Roads, Mag Tubes, fungus, ZOC | known tile items and owned/observed abilities | only known squares and legitimately known subject relations | topology dependency invalidation | route/reachability/connectors | opaque road/tube/former/patrol orders | one stateful arrival map drives route, ETA, reachability, response and lost envelopes; ZOC/occupancy are subject-relative and uncertainty is explicit | topology parity, relationship differential, persistent-orders and peninsula fixtures |
| Transports, carriers, air logistics | rich owned unit state plus demand-driven native airdrop receipt | owned cargo/roles; visible destinations and subject-relative mechanically usable foreign infrastructure only | cargo/order/refuel changes | logistics/route/reachability | board/disembark/recover/launch/airdrop | foreign subjects never borrow sovereign/Pact infrastructure or sovereign-relative drop blockers; hypothetical/foreign drops remain conditional; routine collection exposes only Drop readiness/range, while an owned query obtains and revision-caches one bounded native receipt for its specific unit; current exact embark binds an active provider-safe base object whose owner/coastal fields are current and whose relationship is owned or Pact, while remembered, missing, destroyed, enemy, Treaty, Truce, and neutral ports are rejected; boarding skips only the passenger; transport residual, passenger refresh, charged disembark and post-disembark residual are composed as independent actor states; preparatory arrival search exhausts the finite known graph while bounded candidate frontiers expose completeness/count/optimality; every known non-Pact occupant blocks Drop insertion, an empty at-war base follows the separate native base rule, and native Aerospace Complex/Air-Superiority suppression is honored | native-aligned transport state matrix and aggregate ETA, winding-region exhaustive-search adversary, owned/Pact-port fixtures, owned demand-receipt and foreign conditional airdrop matrices, production-native diplomacy/anti-Drop fixture, orbital-Drop collector stress, demanded-receipt latency stress, amphibious stress and carrier recovery fixtures |
| Crawlers and convoys | owned unit/order and base economy | owned only | convoy/order/base-yield changes | logistics/base | returned convoy/production actions | native order with derived support summaries | base/worker and strategic logistics fixtures |
| Diplomacy and chat | faction relations, native dialogs, semantic chat | contacted parties; message recipient/channel only | critical chat, relation/commitment changes | world intel/global; chat/memory recall | exact offers, responses, native and group chat | speech is reported; mechanics current; deduped message UID | diplomacy, LAN diplomacy, chat and communication contracts |
| Pact/infiltration intelligence | contacted faction adapter and entitled foreign economy/research/orbital report fields | exact Pact, infiltration, Governor, or Empath Guild channel; absent entitlement remains explicit unknown and never becomes a generic foreign field | material relation/intelligence changes re-anchor | world intel/global | native diplomatic choices | field-level entitlement provenance attached; timeline-scoped | compiled native adapter, end-to-end global pipeline, entitlement adversarial and diplomacy tests |
| Technology and research | technologies, research snapshot and dialogs | owned tech/research; contacted trade terms only | acquisition/focus/offer changes | world global; reference corpus | research focus, technology commerce/demands/gifts | native/current or reported offer; journaled outcomes | research, demand/counter, commerce tests |
| Secret Projects and victory races | owned production, completed-project public registry, public victory state, and BEGIN/CHANGE/HALT/SURVIVE/DONE project report popups | own production plus completed public projects and legitimately observed reports; unobserved rival production remains explicit unknown | observed project-race transitions and project/global changes re-anchor | world base/global/changes | production, Artifact acceleration, victory actions | journal semantic history is authority for reported builder/provenance and rebuilds after all derived projections are destroyed; CHANGE/HALT/new reports supersede it | compiled native report adapter, journal-only rebuild, observation transition, global pipeline and project/Artifact/endgame fixtures |
| Planetary Council | Council windows and last public result | contacted/public proposal, ballot and result | calls, bargains, votes, results are critical | world global/intel | exact proposal/vote/bargain choices | current modal or public report; journal event | council, incoming vote, bargain tests |
| Economy and energy market | owned economy, treasury and native offers | owned resources; quoted counterpart terms | treasury/loan/market state change | world base/global | gifts, loans, purchases, corner market | current owned fact or reported offer; journal action | loan, gift, purchase, economic-victory tests |
| Ecology/native life/sea level/global events | known terrain, own eco damage, public scenario/global state | owned/known/public channel only | terrain/global events re-anchor | world area/base/global | native legal Former/combat/scenario responses | current/stale/public provenance preserved | ecology and sea-level strategic fixture |
| Orbital systems | owned orbital totals and entitled foreign orbital report totals | owned state plus exact infiltration report entitlement; no generic foreign orbital disclosure | orbital/global changes re-anchor | world global | only native-returned legal production/actions | owned/infiltration field provenance; rollback-derived | compiled native adapter, end-to-end global pipeline and entitlement fixtures |
| Scenarios, overrides and objectives | briefing, scenario snapshot, game settings | scenario-public/seat-specific contract | configuration/objective changes are critical | world global and match briefing | scenario-defined choices through decision surface | ruleset/config hash plus scenario provenance | custom scenario, briefing and Alien Crossfire fixtures |
| Alien Crossfire/Progenitor mechanics | expansion rules, factions, artifacts, objectives and modals | same perspective and scenario rules | contact/objective/artifact changes | world global/intel/reference | guarded native expansion actions | source and uncertainty identical to stock domains | Artifact, endgame and expansion-objective fixtures |
| Endgame paths | victory state and presentation windows | public/owned result only | outcome is critical | world global | accession, defiance, presentation advance, finish/continue | public/native outcome; recovery remains coherent | economic and full-endgame pipeline tests |
| Cognition, attention, plans, operations and watches | journal plus perspective projection | exact sovereign scope only | durable at-least-once queue capped at installed publication; typed watch triggers | runtime context, cognition and memory tools | cognition writes only; no deterministic strategy automation | verified canonical orphan recovery; all derivatives discarded on rollback | internal journal crash matrix, concurrent publication readers, runtime, attention, rollback, 500-action tests |

Cross-cutting single-player/no-timer behavior is covered by the semantic action
fixtures and the reproducible no-timer benchmark procedure. LAN-native mutation
paths retain their dedicated two-client and mixed-LAN suites. Hidden-state
differentials compare byte-identical visible positions with different hidden
honeytokens across anchor, world query, region, route, render, attention, and
pinned specialist world inputs. See [Contributor testing](testing.md) for exact commands.

Unity Survey and current Planetary Governor status are read directly from native
rule/governor state. Scenario rules, owned orbitals, completed public Secret
Projects, public victory state, and legitimately displayed project-race reports
are native-backed. Entitled foreign fields retain the precise Pact,
infiltration, Governor, or Empath Guild channel that made the field visible.
Unobserved rival state is deliberately represented as unknown; completeness
means complete handling of legitimate player-visible sources, never disclosure
of hidden engine state.

## Sovereign correctness checkpoint

The following entries distinguish implementation from the evidence obtained.
They do not certify the later managed-parameter, intent, or counterfactual layers.
See [checkpoint acceptance](benchmarks/2026-09-04-sovereign-correctness.md).

| Capability | Observed / represented | Calculated / provider-queryable | Action / effect verification | Recovery and bounds |
| --- | --- | --- | --- | --- |
| Complete geography addressability | native-shaped tiles through perspective projection | all physical masses and mobility regions have persisted identities; `area(origin_ref="world-geography")` enumerates bounded pages or containing regions for nominated bases; owned/foreign overflow counts remain explicit | read-only; omitted mass and mobility-region queries exercised through WorldService | split/merge fixtures; 6K/16K anchor gates retained |
| Peripheral geography and global systems | current owned bases, foreign bases and land contacts; current/stale map fields | quiet colony and six-unit foreign landing retained; essential global domains survive 60 bases; frontier promotion and map-only freshness | derived awareness, no control/dominance or strategic ranking | fragmented Huge and mature-empire fixtures |
| Survey topography | compiled native producer exposes terrain/altitude via Survey entitlement; second boundary filters channel | fresh perspective consumes Survey terrain with separate provenance; unseen features/ownership remain unknown | observation adapter and native-shaped entitlement fixtures; no claim of hidden-resource knowledge | ordinary projection/journal recovery contracts; live source validation tracked separately |
| Base/geography query caches | current scoped perspective, units, infrastructure and movement rules | responder changes invalidate; distant irrelevant noncombat changes preserve base cache; warm hits skip rich geography | read-only service tests and cold/warm timing | calculator version advanced; timeline/epoch scoping preserved |
| Repair/staging evidence | field-level base, access, riot, facility, feature and repair-rule evidence retained | stale and unknown access stay qualified; mature transport scheduler used for island candidates | route mechanics verified by transport contracts; exact healing totals are not claimed | bounded candidate search exposes unresolved coverage; no known path differs from bounded miss |
| Watches | material publication supplies frozen previous/current field evidence | selected-field changes and typed operators; lifecycle notices for expired/invalid watches | production-field arithmetic cannot trigger an unchanged-production watch; no gameplay automation | publication retry and lifecycle notification deduplication fixtures |
| Semantic target resolution | current perspective/timeline/epoch/revision private mappings | base-site and Drop query selectors use equality mapping, never ref decoding; remembered map addresses remain valid | guarded opaque action tests and live outside-page Drop execution/cache hit | stale entity, cross-scope and replay gates preserved |
| Native base-site receipt responsiveness | currently visible nominated squares; owned/current-visible overlap bases | 32 candidates × 21-square radius; shared overlap index; isolated native yield context | dual-gated 512-base stress invokes production receipt and verifies restoration; live pass: 229 ms wall, 215 ms probe, 170,436 bytes; native restoration checks passed | 500 ms operation/probe limits retained; fixture restores base rows, count, visibility and yield scratch state |

## Managed action checkpoint

Checkpoint 2 passed with the following evidence boundaries. Native adapter tests
prove binding and guards; the isolated single-player and two-seat tests prove the
managed action/effect chain. See [acceptance and sanitized measurements](benchmarks/2026-09-04-managed-action-paths.md).

| Family | Observed / represented / calculated / provider-queryable | Sovereign expression / execution / verified effect | Recovery and limits |
| --- | --- | --- | --- |
| Actor discovery | Ordinary paginated world queries independently discover owned fixture actors and base | Issued semantic refs reach current guarded choices | Native selectors remain private; stale/cross-scope refs rejected |
| Workshop and upgrades | Current native catalogs and final legality/cost/count receipts | Staged design creation/discovery, single-unit and bulk upgrades execute; world units and costs verified | Draft identity/revision/epoch/expiry/replay tests pass; no claim that retirement was executed |
| Production, citizens and governors | Owned queue, governor and citizen fields visible through world base queries | Set/append/remove queue, governor and worker/specialist changes execute and appear in world state | Existing guarded journal actions retained; completion/progression attention belongs to checkpoint 3 |
| Allocation and Social Engineering | Bounded selections and native complete-combination validation | Selected allocation and social models execute and match native/current world state | Changed displayed terms cannot silently rebase consent |
| Native gifts, technology purchases and loans | Discrete offered gift values and native quotes retain technology, principal and payment schedule | 37-credit unsupported gift rejected before mutation; 125-credit gift, technology purchase and loan effects verified | Price/schedule/session changes reject stale acceptance; arbitrary loan renegotiation is not invented |
| Human diplomacy | Provider selects issued amount/technology/relationship clauses; recipient reads actual terms | Energy, technology and Pact agreements accepted through managed tools and replicated | Managed checkpoint reload restores journal/world/identity; current treasury, technologies and Pact relationships preserved |
| Council and research | Closed candidate/yea/nay ballots; directed research command with Blind Research guard | Council proposal/ballot resolves to the observed public result; directed research has adapter proof only | Fresh guarded choices required; no unsupported research exactness claim |
| Founding, terraforming and transport | Public actors, native legal actions and current colocated rendezvous calculation | Founding yields an observed base; terraforming order appears; boarding increases selected transport cargo by one | Terraform completion is not claimed; nontrivial movement timing retains separate movement/Pact-port evidence |
| Drop target page | Bounded globally nearest page checked against full-sort exact native oracle | Demanded receipt matches choices; exact outside-page semantic choice executes and is revision-cached | Routine collection omits enumeration; target refs remain private-mapped and revision-bound |
| Lifecycle and roster | New-game catalog IDs distinguished from loaded save slots; explicit roster preserved | Native saved-slot selection, paired reload and faction seat restoration pass | Whole LAN transition and supervisor observation serialized; current world agreement effects survive restore |

The final native 512-base/32-site probe passed at 222.476 ms wall and 215.505 ms
maximum concurrent probe gap. Fifteen managed tools and the 492-conservative-token
world schema remain intact. Concurrent campaign startup stress and provider
inference remain later integrated gates; this checkpoint does not claim completion
of intent-linked attention or counterfactual assistance.

## Intent and attention checkpoint (accepted)

Checkpoint 3 is **accepted** through the following bounded chains. See the
[acceptance ledger](benchmarks/2026-09-04-intent-attention.md).

| Capability | Evidence established | Recovery and claim boundary |
| --- | --- | --- |
| Semantic spatial scopes | Managed constructor/crossing fixtures pass; native base-radius query succeeds; 65,536-square service yields a 99-token descriptor; dependency invalidation and expiry enforced | Private scope definitions survive restart; rollback discards old-timeline watches; native-table agreement is not yield prediction |
| Production occurrences | Controlled native repeated units, facility and project completion → observation → managed milestone → runtime attention; project interruption remains distinct; guarded project-information close executes; compact base discovery passes | Completed unit identities preserved by managed recovery; native birth receipts retain immediately destroyed identities; production fixtures invoke real native routines with controlled inputs |
| Aggregate milestones | Managed create/inspect, all/threshold evaluation, stale qualification, retained destroyed requirements and durable transition delivery; live production becomes ready; SQL-only plans rejected | Journal plan revision expires prior milestones; native recovery discards old-timeline watches; no second plan store or automatic gameplay actions |
| Plan health | Sovereign writes conflicting production reservations and stationary unit assignment through managed memory; provider query reports them; timed unit/credit conflicts and qualified dependencies covered by fixtures | Managed recovery preserves journaled conflicts and assignment; incomplete intent withholds unassigned counts; runtime stays within existing budgets |

Counterfactual mechanical assistance and the final integrated acceptance remain
checkpoints 4 and 5; this acceptance does not imply they are complete.

## Counterfactual checkpoint

Checkpoint 4 is accepted within the documented calculation limits. The
[counterfactual acceptance ledger](benchmarks/2026-09-05-counterfactual.md) and
[sanitized evidence](benchmarks/2026-09-05-counterfactual.json) distinguish the
native sequence, corrected recovery continuation and deterministic fixtures.
The final uninterrupted integrated run belongs to checkpoint 5.

| Capability | Observed / represented → calculated / queryable | Sovereign expression → execution → effect verification | Recovery / bounds / limits |
| --- | --- | --- | --- |
| Site economy | Current guarded founding receipts; conditional center/worker yields, material variants and feasible joint outputs through bounded world query | Nominated semantic locations; actual managed founding center and Recycling Tanks completion delta match | Variant state restoration and hidden-mirror/foreign-worker independence; four sites with 511 base input rows: 248 ms wall; other variants remain conditional |
| Social Engineering | Current final choice → native ratings/cost, conditional support and stock rescaling → world result | Guarded application; actual ratings, charge, support and mineral-stock rescaling match | Revision/session-bound preview; recovery rejects old reference; full psych/commerce/research excluded; LAN regression at checkpoint 5 |
| Terraforming | Current Former choice → local tile hypothesis/work estimate → world result | Managed cooperative Farm completion matches standalone and owned-base yields | Opaque guards and recovery scope retained; continued workers conditional; climate/secondary harvest excluded |
| Deployment | Explicit target/capability → current actors or nominated build/hurry/upgrade → qualified preparation/travel | Guarded production and move; four native fixed-surplus upkeeps and one movement phase match | Current boarding link and carrier reservation inputs; boarded/stale/air/transport fixtures; complete aircraft arrival behavior not claimed |
| Action collateral | Current choice → production/upgrade/support consequences and journal-authoritative links → world result | Positive retool, hurry, upgrade charge and rehome/disband support effects match; explicit plan links verified | First restored query retains assignments/conflicts; old preview rejected; local police capacity does not claim actual drone/riot changes |

The surface remains 15 tools. Whole-result ceilings apply; the world schema is
619 conservative tokens. The dense site fixture returns 2,034/2,213 estimated
tokens at standard/deep and an explicit compact size error. These are adapter
measurements, not native timing claims.

## Integrated acceptance

Checkpoint 5 is accepted within the stated evidence limits. The
[final ledger and 30-row audit](benchmarks/2026-09-05-integrated-acceptance.md) and
[sanitized results](benchmarks/2026-09-05-integrated-acceptance.json) supersede
integrated gates left open by earlier checkpoint reports. The final R12
single-player and two-seat LAN processes both passed uninterrupted.

| Capability | Observed / represented / calculated / provider-queryable | Sovereign expression / execution / verified effect | Recovery and limits |
| --- | --- | --- | --- |
| Cold managed readiness | First active-world reconciliation precedes runtime/MCP listeners; controlled blocking contract | Concurrent native startup reaches the complete managed gameplay sequence | R12 single-player/LAN recovery passes; lobby observer retained; provider HTTP and native/UI limits unchanged |
| Journal-backed cache publication | Canonical observations are appended before batched disposable SQLite rows; same projected objects and event identities | Full native action/effect sequence and attention delivery pass on the batched image | Before/during/after cache-commit failures replay 279 objects without duplicate events; full frozen-publication and native recovery pass |
| Geographic communication | Current known names and relation bearing/distance are queryable | Native text-only chat adapter; incoming location-like labels remain untrusted speech | Stock Pact contact/refresh map exchange lacks controlled native comparison; no automatic map authority or continuous allied vision is claimed |
| Context and semantic GC | 15 tools; 6K/16K anchors pass; 0.285% quiet growth; million-token fixture → 13,185 | Managed world queries and native request/attention sequence pass; live synthetic prefix reuse passes | Full collector passes: 25,600 squares in 17.058 s / 271.816 ms probe; provider gameplay inference not claimed |
| LAN Social Engineering | Current native staged choice and predicted switch charge | Both replicas receive pending models and exactly-once charge through existing tools | Selected policy and agreements survive R12 checkpoint recovery; all derived social ratings are not independently compared |
| Counterfactual and intent integration | Native effects match tested site/terraform/social/action predictions; explicit plans and milestones are queryable | Six native production upkeeps and deterministic movement match; production/interrupt attention delivered in seven acknowledged batches | First recovered plan query preserves conflict/assignment; completed identities survive; old watches and preview choices rejected; constant-surplus timing is not full campaign simulation |
| Native responsiveness | Demanded receipts retain current visibility and guarded legality | R12 32-site receipt: 231.477 ms wall / 222.187 ms probe / 169,970 bytes; four-site economy: 210.431 ms / 218.370 ms / 52,338 bytes | Read-state restoration passes; unchanged 500 ms and payload gates; 129-unit routine Drop paging and exact outside-page execution also pass |

## Focused peer-review corrections (R1–R8)

The [acceptance ledger](benchmarks/2026-09-05-peer-review-corrections.md) and
[numeric evidence](benchmarks/results/2026-09-05-peer-review-corrections.json)
qualify this checkpoint. These rows amend the corresponding domains above.
A supported read-only capability is proved through observed → represented →
calculated → provider-queryable → sovereign-expressible → effect verified →
recovery-safe. Watch/operation execution here means activating declared
bookkeeping and receiving its verified event; it does not claim a native game
action. Native action coverage remains in the earlier checkpoint rows.

| Ref kind | World area | Direct spatial watch / scope | Operation / explicit plan or milestone dependency | Immutable specialist input | Lifetime and evidence |
| --- | --- | --- | --- | --- | --- |
| Physical landmass | Supported | Entry/exit and geography; identical membership | Supported | Explicitly rejected | Terrain identity independent of mobility; direct/wrapped crossings; omitted mass paginated discovery |
| Physical ocean mass | Supported | Entry/exit and geography; identical membership | Supported | Explicitly rejected | Perspective-known ocean membership; direct/wrapped crossings |
| Mobility region | Supported | Entry/exit and geography | Supported | Explicitly rejected | Mobility dependency validity; no physical-identity substitution |
| Frontier | Supported | Frontier contact, spatial perimeter and geography | Supported | Explicitly rejected | Complete known frontier reconstruction; omitted frontier paginated discovery |
| Theater | Supported | Activity footprint and geography | Supported | Explicitly rejected | Local participant/explicit route cells; containing continent never becomes footprint; amphibious case retained |
| Route | Supported | Perimeter and route corridor | Supported | Explicitly rejected | Complete dependency set/hash, rules/calculator, perspective/timeline/epoch; native receipts additionally require unchanged action revision |
| Spatial scope | Supported | Perimeter and union | Supported | Explicitly rejected | Same private frozen membership, dependency invalidation, expiration and timeline rules |
| Base | Supported | Direct perimeter rejected; explicit base-radius scope supported | Supported | Supported object snapshot | Current object fields; stale/unknown qualification retained |
| Own unit | Supported | Direct perimeter/geography rejected | Supported | Supported object snapshot | Current owned semantic identity; explicitly nominate a location for geography |
| Current foreign contact | Supported | Direct perimeter/geography rejected | Supported, stale sighting becomes unknown | Supported object snapshot | Visible episode identity; no reuse across visibility gap |

The specialist column describes commissioning inputs: live-derived handles are
not part of the immutable object snapshot and now fail explicitly. The specialist
can derive geography inside its frozen perspective from nominated world objects.
Positive snapshot evidence is the specialist contract suite, not merely class
existence. No live capability is smuggled into that snapshot.

R3 crossings retain observed episode identity, continuous path segments, native
sequence and relationship at occurrence. A later loss, destruction or diplomacy
change does not erase an observed crossing; appearance without motion and stale
reports do not create crossings. Evidence combines the compiled production
adapter with native-shaped publication and deterministic episode tests, not a
new running-game timing or combat-mechanics comparison.

R6 threshold milestones count ready and feasible requirements, preserving each
optional blocker without falsely blocking the aggregate. R7 potential terrain
connectivity uses matching known terrain plus unknown cells and stops at known
opposite terrain, including flat/wrapped parity boundaries. R8 active journal
plan revisions retain prior explicit dependency health and produce one positive
availability notification; unknown evidence cannot satisfy it. Restart, rollback,
plan revision/completion and pre-publication-ack crash retries are covered.

Multiple-scope and full collector acceptance passed unchanged gates. The ledger
also retains a prior 55.9-second quiet collector failure whose cause was not
established. Native Pact contact/reopening map refresh remains unverified by a
clean controlled comparison; no continuous allied live vision is implied.

### Final hostile review: publication consistency (Gate A)

F1/F3: publication N watches, milestones and scope validity now consume immutable
candidate N. Pre-head work precedes installation; mandatory dependency attention
completes before acknowledgement and before any N+1 native drain, including
post-head recovery. The native-shaped production collector verifies new-unit
completion without false blocking, garrison arrival/departure, field and scope
changes, and 20 crash/downtime-reversal cases. This is publication/effect/recovery
evidence, not a running-game mechanics comparison. See the
[final-review ledger](benchmarks/2026-09-05-final-hostile-review.md).

### Final hostile review: between-snapshot episodes (Gate B)

F2: ordered native staging now supplies scoped temporal contact episodes even
when both reconciled snapshots omit the contact. A surviving current contact is
associated only with the final uninterrupted episode. Six real staging/collector
cases cover loss/destruction, page-boundary restart and publication retry;
four-episode, reset, discontinuity and perspective-isolation checks pass. Observed
crossing attention is verified; temporal refs never imply present whereabouts or
cross-gap opponent identity. See the final-review ledger for qualified evidence.

### Final hostile review: derived lifecycle (Gate C)

F4: current derived geography alone supplies spatial authority. Persisted history
and issued anchors cannot revive superseded mass, mobility, frontier or theater
footprints. Six land/ocean version/split/merge cases verify the interval before
history refresh across direct watches, scopes, operations, plan/milestone
requirements and area inspection. Existing one-successor watch migration remains;
ambiguous splits invalidate and frozen intent is not silently retargeted.

F5: successful current inspections receive private validation receipts; warm
reuse promotes the target in the normal anchor after restart while preserving
historical creation revision. True dependency changes prevent current promotion.

F6: 64 recent query rows plus explicit pins replace unbounded history as resolver
input. Independent watch/scope/operation/plan/milestone pins, release GC, restart,
true invalidation and checkpoint isolation pass. The 100/1,000/5,000-row storage
fixture retains 65 rows; steady watch/inspection/runtime cost follows retained
complexity. See the final-review ledger for exact measurements and evidence class.


Final acceptance reran the integrated consumer contracts and preserved all nine
collector measurements from three successive distributions. The final repeated
large-map runs passed at 8.489/11.255/23.245 seconds and 242/316/327 ms probes.
Earlier failures and the original unexplained 55.9-second outlier remain in the
ledger. Canonical journal writes and verification remain intact; only detached
section copying and a disposable canonical filename index reduce repeated work.

### H4 canonical persistence checkpoint (2026-09-05)

Observed real event-file/manifest crash → verified canonical chain → recovered manifest → collector frozen retry → distinct S+2 append → replay/hash verification. Invalid or ambiguous suffixes fail closed. See [H review acceptance](benchmarks/2026-09-05-h-review.md). This closes the journal crash seam; the other H findings remain pending.

### H1 provider visibility checkpoint (2026-09-05)

Persisted N evidence → installed-head visibility cap → normal history/snapshot/runtime queries → contiguous leased delivery → acknowledgment/restart verification. Four concurrent publication/recovery cases pass. Internal pre-head durability remains permitted; provider reads cannot advance beyond their captured world. See [H review acceptance](benchmarks/2026-09-05-h-review.md).

### H2/H3 temporal continuity checkpoint (2026-09-05)

Observed native segment → private perspective/epoch episode assignment → single semantic occurrence → normal temporal history/watch access → frozen publish/ack checkpoint → next-publication extension/closure and restart verification. Seventeen adversaries plus existing transient/native contracts pass. Gaps qualify new identity without discarding observed movement; unstable feed cuts cannot equate an earlier episode with a later snapshot contact. See [H review acceptance](benchmarks/2026-09-05-h-review.md).

### H5 inspection lifetime checkpoint (2026-09-05)

Successful semantic query → cache authority → recent inspection → normal anchor promotion → action-only churn and restart → dependency invalidation. Eight cold/warm route/area/geography/native-receipt cases pass. Native action-bound authority still expires on revision change. See [H review acceptance](benchmarks/2026-09-05-h-review.md).

### H review capability matrix

| Finding | Observed → represented → calculated | Provider/sovereign surface and verified effect | Recovery and evidence |
| --- | --- | --- | --- |
| H1 | Durable N evidence remains behind installed-head cursor | History, snapshot, runtime and contiguous attention expose only committed world evidence; no native action added | Four concurrent phase/restart cases and acknowledgement verification |
| H2/H3 | Feed segments map to private perspective/epoch episode checkpoints, with ordered gap/reset boundaries | One semantic occurrence per observed move; normal history/watch access; no unproved snapshot identity join | Seventeen collector/projector adversaries plus prior transient/native contracts |
| H4 | Installed event suffix is schema/scope/sequence/hash validated before manifest advancement | Journal replay and collector retry recover authority before further append; S+2 links correctly | Internal event/manifest/marker matrix, invalid suffixes, concurrent replay and 22 publication crashes |
| H5 | Explicit inspection is retained only while complete query dependencies/rules/calculator/native receipt remain valid | Normal anchor promotion survives unrelated action and material churn; native action-bound authority expires | Eight cold/warm action/material/restart cases; actual dependency change withdraws promotion |

This matrix covers the requested provider/effect/recovery chain. It does not claim new running-game execution for an internal publication, identity, journal or inspection capability. All applicable native actions continue through the existing guarded managed surface.

### H1–H5 integrated acceptance

All 41 distinct scripts pass, including the 22-case publication crash matrix, four concurrent visibility cases, 17 episode/gap adversaries and eight inspection authority cases. The final five exact 25,600-square/nine-scope/four-watch runs pass the unchanged active-publication/probe gates; both five-run H-review distributions and all historical failed/passing timing data are preserved in the [H-review ledger](benchmarks/2026-09-05-h-review.md). Automatic inspection validation also preserves explicit inspection time and rejects changed rulesets. Native adapter compilation passes; the unchanged native cross-build reports no work. This is deterministic/native-shaped/compiled-adapter acceptance, not a new running-game, LAN, .NET or provider-tokenizer run. PR remains unmerged for independent final review.

### Packaged-image deployment checkpoint (2026-09-05)

The control image now includes `smacx_temporal_episodes`; the Graphiti image includes the world-store publication-barrier dependencies. The built control image passes all 17 cross-publication episode cases with only test scripts mounted (production source comes from the image). The built Graphiti image imports its publication dependencies and reads an empty committed cursor using a temporary initialized store. The full Graphiti fixture cannot run unchanged in that image because its timeline-fork setup requires Git, which the projector runtime does not install; no full packaged Graphiti fixture pass is claimed.

Deployment against the existing local database fails closed with `unsupported_prerelease_schema_recreate_database`. Existing volumes are preserved; this checkpoint does not claim a successful current-version live deployment or game acceptance. A fresh compatible database is required by the locked prerelease schema contract.

### I1 owned transient lifecycle checkpoint (2026-09-05)

| Native evidence | Representation and calculation | Provider/sovereign access and effect | Recovery evidence |
| --- | --- | --- | --- |
| Owned production, then movement/damage/destruction absent from both snapshots | Lifecycle-scoped owned proof in the existing private episode checkpoint; shared semantic occurrence assignment | Normal temporal history retains production and owned destruction on `own-unit-777`; no fabricated contact destruction | 20 targeted cases, canonical journal assertions, packaged-image pass, existing gap/crash suites green |

See [I1 acceptance and deployment evidence](benchmarks/2026-09-05-i1-review.md). No new executable action or live-game mechanics claim. Future schema changes require forward migrations; this deployment alone uses the explicitly authorized fresh prerelease database.

### Gameplay doctrine checkpoint 1

| Capability | Evidence chain | Status |
| --- | --- | --- |
| Static literacy and 16 match blocks | Authored doctrine → paragraph inventory/source review → typed deterministic compiler → 18 rendered prompts and 3 deliberate errors | Content fixtures pass; managed/provider integration and behavior evaluation remain pending |

See [doctrine acceptance](doctrine/acceptance.md). Source evidence is distinguished from running-game comparisons; stock defaults are not inferred for unknown rulesets.

### Gameplay doctrine checkpoint 2

| Capability | Evidence chain | Status |
| --- | --- | --- |
| Confirmed public match literacy | Native rule-loader/seat receipt → typed public adapter → 16 blocks → SQLite profile and hashes → real Hermes provider capture | Implemented; actual isolated native receipt plus integration/adversarial and provider-wire evidence |
| Restart and fair-play boundaries | Session-scoped receipt → frozen opening/roster → exact persisted bytes after SQLite reopen; controller/runtime sentinels excluded | Integration checks pass; no hidden faction roster or live world state in doctrine |
| Longer prefix capacity | Conservative system/tool reserve → minimum remaining history guard → unchanged 15 tools and semantic-GC policy | Capacity checks pass; exact costs and behavioral ablation remain checkpoint 3 |

See [checkpoint acceptance and limitations](doctrine/acceptance.md) and [assembly architecture](doctrine/implementation.md). These receipts do not certify new native actions or full-game strategic performance.

### Gameplay doctrine checkpoint 3

| Capability | Evidence chain | Status |
| --- | --- | --- |
| Dynamic gameplay literacy delivery | Rule-load attestation → public receipt → compiler → persisted bytes → actual Hermes provider capture → live frozen response evaluation | Implemented and returned for review; exact final contract evidence recorded |
| Behavioral usefulness and limitations | 23 cases × A/B/C, initial/final raw outputs, per-case source/evidence review, separate explicit SUPPORT supplement | Measured, mixed results; specialist and Progenitor comprehension improvements with remaining timing, cap and epistemic errors; no full-game competence claim |
| Cost and persistence | Exact provider tokenizer → 8,034–8,380 system tokens → 15-tool budget and context reserve → 4,944/8,217 live cache hits; SQLite restart exact bytes | Verified at the stated interfaces/provider; no promise of complete cache reuse |
| Recovery and failure behavior | Required-input/compatibility rejection → explicit recompile → corrupted/oversized prompt fail-closed checks, including swallowed-startup-error simulation | Contract checks pass; uses existing persistence, no database rebuild or new schema |

See [final acceptance](doctrine/acceptance.md), [behavior review](doctrine/evidence/behavior-review.json), and [reproduction commands](doctrine/validation.md). Doctrine has no new executable action; native legality and effect verification remain the existing managed action path's authority.

### Worker incident reliability checkpoint (2026-09-05)

| Layer | Coverage chain and status | Acceptance |
| --- | --- | --- |
| Native + AI memory checkpoint archive | Observed empty 0640 archive → production uid/gid/umask represented → actual manager/helper writes archive → final private permissions → memory/timeline restore contracts. Permission defect repaired; full live campaign recovery is not claimed. | Distinct-uid Docker helper test and AI memory checkpoint regression. |
| Worker loss containment | Unhealthy bridge → lifecycle error → owned workers/collectors frozen → harnesses stopped and sovereign leases cancelled → durable operator latch. Original incomplete checkpoint remains non-resumable. | Idempotent quarantine and supervision regression; reported live worker/collector confirmed paused. |
| Provider continuation | Exit code zero + unavailable native progress → no new provider invocation → shared bounded outage incident; fresh valid observation permits continuation. | Clean-exit/live-outage continuation contracts. |
| Incident authority and presentation | Identical repeated incident state → one journal event; bridge loss distinguished from native process exit and confirmed freeze. | Incident regression and 56 portal tests. |
| Native initiating stall | Last Explore action observed; bridge request guard latched. Exact native trigger remains **open**. | Fresh and saved-state isolated runs passed turn 5, including repeated saves plus background collector; no claim of native root-cause repair. |

See [incident acceptance and explicit limitations](benchmarks/2026-09-05-worker-incident.md).

### Lobby startup presentation (2026-09-05)

| Capability | Observed → represented → available to user | Verification / limits |
|---|---|---|
| Startup visibility | In-flight launch request + existing provisioning/starting/native-lobby status → lobby DTO → responsive image and blue indeterminate progress | HTTP DTO integration assertion; launch-state contracts; real Razor component exercised in Chrome with isolated responses; non-root image read and deployed HTTP checksum verified. The in-flight marker is process-local presentation, not native health or recovery authority. |
| Startup handoff | Running campaign + existing seat permissions → one navigation for a visit that observed startup | Player/spectator/no-access/state-transition contracts; browser spectator handoff. Bounded wait for pending seat reconciliation. No new access privileges. |
| Later entry | Current seat permissions → prominent Return to game / Watch live | Browser inspection at phone/tablet/desktop sizes; running-lobby visits stay in lobby. Existing controller acquisition and stream authorization still enforce entry. |
| Failure and dismissal | Error/incident/unavailable status or Stay in lobby → overlay removed, automatic entry disarmed | Contracts and browser failure/offline/dismissal fixtures; polling can restore manual entry but cannot silently rearm the same launch. |

Acceptance details: [Lobby startup UX acceptance](benchmarks/2026-09-05-lobby-startup.md).

### AI - 2 action-progress repairs (in progress)

| Capability | Evidence chain and status | Verification / limits |
|---|---|---|
| Supervisor progress classification | Observed failure receipt churn → production fingerprint excludes receipts → unchanged-state timer can retain its baseline | Deterministic fingerprint and continuation contracts pass; native movement fields now expose real effect state; isolated real MCP/Docker containment passes. Full provider stall acceptance remains open. |
| Managed rejection recovery | Preserved transcript → four-failure managed submission budget across targets/IDs → explicit consumption/status/next-step receipts → latched dispatch block and incident reporting | Production wrapper/native-shaped tests pass, including success reset and session isolation. Real MCP failures now prove Docker quarantine in an isolated installation. Native rejection cause remains unknown; provider campaign acceptance remains open. |
| Incident containment | Capability report / sustained active stall → durable operator incident → whole-match quarantine → native/collector pause, harness stop and authority cancellation → explicit verified recovery | Production lifecycle contracts with simulated Docker boundary pass; redacted diagnostics and idempotency retained. Real MCP failure-budget incident pauses native and collector containers in isolated Docker acceptance. Portal incident presentation and sustained provider acceptance remain pending. |
| Movement and order feedback | Retained turn-4 save → rebuilt native replay → current focused HP/movement/scale/order → dispatch-stage receipt and qualified order assignment → Skip removes ready unit | All four movement rejections and interrupted return order reproduced; valid native Skip verified. Specific move rejection cause remains unknown. Production images deployed; new campaign launched, spectator handoff and sovereign briefing observed. Provider reached turn 6 with no incidents/error restarts and a verified turn-4 native/journal/AI-memory checkpoint; full playthrough remains in progress. |

See [action-progress repair evidence](benchmarks/2026-09-06-action-progress.md).

### Diagnostics-first gameplay acceptance (in progress)

| Capability | Evidence chain | Current acceptance |
|---|---|---|
| Causal diagnostic storage | structured event → actor-isolated stream → bounded capture/gap record | Concurrent storage/redaction/bounds contracts pass. Live Hermes/MCP/native interception and export remain unimplemented; not complete. |
| Provider/cognition/intent audit | intended write → journal → selected runtime state → final provider request → action/effect/recovery | New mission explicitly requires this full chain. Existing component contracts do not establish complete campaign observability. |

Checkpoint plan and limitations: [Gameplay diagnostics mission](gameplay-diagnostics-mission.md).

Hermes dispatch capture is now opt-in and tested against a contained dispatcher:
pre-MCP failure rows are retained exactly once. No live provider or native effect
coverage is claimed from this fixture. Checkpoint 1 remains in progress.

Provider request capture: opt-in synchronous chat-completions HTTPX boundary tested
inside the Hermes image with MockTransport. Actual serialized message/tool payload
matches capture; transport errors remain errors. Live provider and omission audits
remain open; no full request-delivery chain is certified yet.

Diagnostics checkpoint update: managed/native invocation adapters, runtime selection
and emitted-response capture, and administrator campaign export are implemented but
remain **partial** until deployed end-to-end evidence is captured. Export contracts
verify match isolation, bounded output and explicit gaps; portal tests pass 72/72.
Specialist attempts/dependencies/manifests are scoped via mission joins. No claim of
historical provider delivery, full trace completeness or gameplay acceptance follows
from these fixtures. See `gameplay-diagnostics-mission.md` for outstanding gates.

| Diagnostics review layer | Current evidence | Remaining acceptance |
| --- | --- | --- |
| Research/hurry semantics | Additive native named preferences and item/mineral receipt; cross-build passes | Controlled native comparison and provider delivery |
| Focus/force representation | Current owned role/order/production summary; runtime tier and huge fixture pass | Live use and home-base/assigned-mission detail |
| Intent reconciliation | Canonical goal/plan metadata; bounded unbudgeted review; managed explicit/final-unit tests | Native auto-turn and full provider action/write loop |
| Discovery vs change | Bounded newly-known extent; current vs refresh qualifiers; observer contracts pass | Saved/live campaign trace comparison |
| Cognition persistence | Guarded write + journal reinitialization; internal crash tests | Same-record next-request/handoff/GC/recovery provider capture |
| Journal diagnostic export | Locked committed-prefix snapshots implemented | Export against running/parked/archive campaign and browser |

Delivery/export update: controlled real writer→journal→runtime→Hermes sanitizer→HTTP
serialization chain now passes immediate, handoff/resume, GC and journal-restart cases.
Native checkpoint recovery remains pending. Compressed bounded tracing and immutable
prefix exports pass contracts; parked Hermes extraction is verified (405 messages).
SSE completion/rationale capture and envelope-hash correlation pass controlled transport
checks. Specialist lifecycle/trace host tests pass. Live provider/full-game evidence,
browser download checks and complete campaign attribution remain open.

Reconciliation follow-up acceptance: repeated pre-dispatch reviews do not consume
choices or count as repeated native attempts (eight-review adapter regression).
The full parked baseline attribution audit is in `gameplay-diagnostics-findings.md`;
remaining native/provider acceptance is still explicitly open.

Operational tracing follow-up: exact constrained helper execution passes on the parked
Hermes volume; compact named request/result lines are enabled in process logs. Live
container dispatch and browser download remain acceptance gates.

Native auto-turn acceptance is being extended with a double-gated private fixture and
real managed goal/write/skip path. The test requires observed turn advancement after
explicit deferral; merely accepting Skip is insufficient. Result remains pending.

Diagnostic export resilience: corrupt deflate members remain in the downloadable
evidence with an explicit gap; scope/readability regression passes. Live Qwen
prompt-cost/prefix test passes (1,324 operational tokens; 6,592/8,548 reused on
the second controlled request). These checks do not certify game completion or
native recovery. Native integration and deployed download acceptance remain open.

Packaged-service gate found and repaired: the control image omitted newly imported
diagnostics/intent modules. Build-time imports now validate the installed MCP
module graph. Installed-image export contract passes; native startup rerun pending.
Source-mounted test passes alone do not certify image completeness.

Live MCP causal capture verified at the adapter boundary (11 calls/results and
21 native receipts). Production `record` compression/human-output wiring corrected
after live inspection; its environment-driven regression passes. Full-game and
deployed corrected-image evidence remain pending.

Actual Hermes-process diagnostic request/response correlation passes against a
controlled receiving provider in four profiles. Native managed effects have partial
recorded evidence; council wait exposed a read-only enumeration race. Bounded refresh
and no-preparation-replay regressions pass; native rerun is required. See the partial
native benchmark; automatic-turn/recovery acceptance is not yet complete.

Corrected image capture observed live: compressed actor streams and named stderr
summaries verified; execution-status/decision-consumption receipt regression passes.
Native cold-start reliability has one unresolved intermittent failure, with a
subsequent identical launch successful. Full native rerun is ongoing.

Provider audit completeness follow-up: metrics now identify requests without any
captured response or transport-failure terminal event. Header receipt alone does
not close the request. The bounded ID list explicitly permits in-flight, interrupted
or missing capture interpretations; it does not infer provider failure. Summary
and campaign-export regressions pass. This does not certify full stream capture.

Native checkpoint acceptance: current-turn goal is written, appears next runtime,
blocks final-ready-unit Skip without effect/consumption, and explicit deferral
permits actual automatic native turn 1→2. Managed actions and named Explore flags
pass against native state; milestone attention delivered in seven batches. Evidence:
`benchmarks/gameplay-diagnostics-native-checkpoints.json`. Post-intent recovery,
full test completion, deployment/browser export and full-game acceptance remain open.

Native driver follow-up: automatic turn advancement succeeded, but its next fixture
ran before the next turn's passive project popup settled. The boundary driver now
requires the controlled first-turn state, observes the required handoff, ends that
episode, acquires the next episode, then finishes passive presentation before
returning an actionable turn. A repeat on already-advanced arbitrary console state
did not auto-end and is not acceptance evidence. The complete fresh native driver
must pass; its prior partial result is retained rather than relabeled successful.

## Diagnostics-first review coverage matrix

This is the current acceptance status; detailed chronological evidence remains in
[the mission log](gameplay-diagnostics-mission.md) and
[causal findings](gameplay-diagnostics-findings.md). A controlled fixture does not
prove autonomous use. Each applicable chain is evaluated through observation,
representation, calculation, provider delivery, sovereign expression, execution,
effect verification and recovery.

| Review | Capability | Established evidence | Remaining gate |
| --- | --- | --- | --- |
| 1 | Named causal calls and receipts | Actual sovereign/MCP/native records link arguments, opaque choices, results, latency and publication receipts; pre-dispatch failures are captured. Compact human menu rendering now prevents repeated move rows from hiding later action families (887-character43-choice regression); packaged and live41/42 rendering verified. See [findings](gameplay-diagnostics-findings.md). | Actual52 movement receipt reaches next provider request with all location/epistemic fields; independent native53 confirms arrival. Continue nondisplacement and other correlation checks; historical uncaptured calls remain explicitly unknown.  At85, enumeration completeness with unbound plans was ambiguous. Explicit assessment scope, bounded unbound-plan counts, participant guidance and CLI summaries pass focused/runtime tests; actual repaired provider delivery is pending. [Evidence](benchmarks/gameplay-plan-binding-visibility-turn85.json). |
| 2 | Specialists and reference lookup | Real child-process supervisor and16-call Hermes specialist regressions prove lifecycle, scoped queries, trace-derived citations and fresh-process isolation. Live direct reference queries and repaired named/content ranking are verified. | Spontaneous sovereign specialist commissioning remains unobserved; acknowledgement alone is not use. |
| 3 | Typed cognition and recovery | Production guarded writer→journal→runtime→Hermes transport passes next-request, handoff/resume, GC, restart and ambiguous post-journal failure. Actual native repeated recovery and solo/LAN identity ordering pass. [Guard mapping](benchmarks/gameplay-memory-guard-discovery.json) is present on live wire. | Actual sovereign plan42 is journaled, delivered immediately and in next episode43, then retained by verified43 recovery in the first resumed provider request. Continue auditing plan maintenance and other record types.  At85, enumeration completeness with unbound plans was ambiguous. Explicit assessment scope, bounded unbound-plan counts, participant guidance and CLI summaries pass focused/runtime tests; actual repaired provider delivery is pending. [Evidence](benchmarks/gameplay-plan-binding-visibility-turn85.json). |
| 4 | Actual provider context and omissions | Actual receiving-provider/capture equality, prompt replacement on real deployed resume (exact current profile equality), omission inventory,15/9 schemas and bounded GC pass. [Complementary answers](benchmarks/gameplay-complementary-result-delivery.json) now survive live delivery. [History pagination](benchmarks/gameplay-history-pagination.json) returns700 deltas/400 events exactly once. | Keep auditing live requests; autonomous large-history traversal is not claimed from fixtures. |
| 5 | Current-turn reconciliation | Controlled native automatic1→2 transition requires explicit same-record deferral; rejected Skip leaves readiness intact. Turn57 actual provider delivery, critical-review block without native execution, explicit acknowledgement and same-choice combat execution pass; native58 independently confirms own-unit state and turn progress ([evidence](benchmarks/gameplay-critical-attention-turn57.json)); ordinary ecology counter updates no longer create critical alarms. | Sustained autonomous reconciliation across consequential unfinished typed intents remains a live gate. Turn68 expired responded attention became invisible while still gating action; redelivery/invalid-ack repair passes regression and real-Hermes validation; deployed verified68 recovery succeeds, with live acknowledgement acceptance pending ([evidence](benchmarks/gameplay-attention-expiry-turn68.json)). |
| 6 | Prose versus durable intention | Handoffs are distinct from typed authority. Native session/revision guards reach the provider; managed memory/history/search filters private selectors without altering canonical journal records. | Historical prose-only intentions cannot be counted as persisted writes; plan42 now has canonical write and next-request/episode proof; continue auditing its maintenance.  At85, enumeration completeness with unbound plans was ambiguous. Explicit assessment scope, bounded unbound-plan counts, participant guidance and CLI summaries pass focused/runtime tests; actual repaired provider delivery is pending. [Evidence](benchmarks/gameplay-plan-binding-visibility-turn85.json). |
| 7 | Alien Artifact investigation | Baseline contains no retained authoritative lookup. Live content ranking was defective and repaired; scoped content retrieval now returns the reference body. | Observe sovereign lookup and mechanics use in a relevant native situation; the old behavior remains insufficiently evidenced. |
| 8 | Production and citizen actions | Native retool/queue/citizen comparisons pass. Actual turn32 wire retained production and citizen answers; sovereign-selected hurry changed22→30 minerals and88→69 credits. Native turn33 produced the Colony Pod. [Evidence](benchmarks/gameplay-complementary-result-delivery.json). | The requery followed an operational repair notice. Subsequent production requery and Former selection are observed; native+AI recovery34 retains the completed Pod. Citizen tile/yield omission and unexplained two-step reassignment are now repaired with guarded adapter tests; deployed; expanded native two-step reassignment, worked-state/yield comparison and full control/MCP suite pass. Turn40 actual provider context and both sovereign-selected reassignment steps now pass: independent native1253 worked state/yields and+1 nutrient surplus match. Verified native+AI checkpoint41 restore preserves the allocation, nutrient surplus and two completed Formers. Turn47–51 exposes zero-surplus defense production remaining at1 mineral: conditional production-flow response and doctrine repairs pass packaged/epistemic contracts and deployed exact prompt delivery. Native52 has changed to+2 surplus/3 accumulated, correctly reported as accumulating; At73 the sovereign recognizes zero passive progress, executes affordable34-credit hurry7→20 minerals, and the new Garrison is independently verified at74. The exact next request retains completion_verified=false; premature completed wording is subsequently qualified as appearing next turn. Turn54 sovereign hurry reaches30/30 with pending-native-processing context in the actual next request; native55 independently verifies the new Garrison and provider force summary includes it. Turn65 native repeat occurrence was dropped from queue-exhaustion attention; same-base/turn occurrence retention and bounded runtime regression pass. [Evidence](benchmarks/gameplay-production-repeat-attention.json); deployed; actual74 request b6f0731546fa41f89cf525f6780b4028 retains both queue exhaustion and the same-base/turn repeat occurrence. Restored68 summary omitted food balance; native69 starvation is observed. Nutrient-surplus summary retention passes stale/strategic fixtures; initial deployment exposed persisted-anchor reuse, now addressed by format/cache-version regression; actual format2 request delivers qualified owned food balance matching native69 state. Future deficit behavior remains under observation ([evidence](benchmarks/gameplay-base-food-visibility.json)).  Turn75 exposes forced support disband with empty NOSUPPORT information; qualified popup context and conditional production support projection pass managed retention and16 controlled running-native comparisons. The actual native NOSUPPORT dialog exposes the explanation, acknowledgement closes it, and unit count falls6→5. The full native/MCP suite passes, including repeated native+AI recovery after timeline GC; deployed after verified75 recovery, with actual sovereign support-query delivery still pending. Native74 completion remains proven, but the subsequent net army loss predates verified75 recovery. [Evidence](benchmarks/gameplay-support-shortage-turn75.json). |
| 9 | Focus and management access | Ready-unit focus is nonmandatory; frames identify other management families and turn-closing risk. Native action/intent contracts and actual provider notices pass; live production queries and hurry execute through the15-tool surface. | Continue checking management before final-unit actions; do not infer broad competence from one repaired workflow. Turn68 sovereign initiates citizen management before the final ready unit, with both receipts delivered and native nutrient deficit−1→0 verified; [evidence](benchmarks/gameplay-citizen-management-turn68.json). |
| 10 | Force composition | [Actual turn23 wire](benchmarks/gameplay-force-summary-live.json) contains3 Scouts, orders, Colony Pod production and support/role qualifiers. Turn33 native state and sovereign frame include the completed Pod. | Continue checking composition after force losses, new roles and support changes. |
| 11 | Research semantics | Actual Explore/Build category flags and hidden-target qualifiers are verified. Live Social Psych acquisition and count2→3 reach the provider; hidden target remains null. [Evidence](benchmarks/gameplay-research-delivery-live.json). | Do not infer hidden technology selection or exact timing without native evidence. |
| 12 | Discovery versus physical change | Native observation and aggregation contracts preserve knowledge-refresh/change-basis qualifiers and avoid converting newly seen territory into physical expansion. | Compare future sovereign claims with the delivered observation basis; old unsupported claims remain unproven. |
| 13 | Spatial provenance | Route/area/mechanical query arguments, results and epistemic evidence are retained. Historical eyeballed estimates had no retained route query. Native turn35 reported nondisplacement, but managed receipts omitted location evidence; [receipt translation](benchmarks/gameplay-movement-receipt-delivery.json) now preserves it with historical qualifiers and adapter/fair-play checks. | Actual52 receipt delivery and independent53 arrival pass; turn57 combat receipt is explicitly treated as outcome-ambiguous by the sovereign, which requests a targeted area query; continue nondisplacement interpretation and attribute future suspect distances/threat estimates using actual query evidence; missing historical evidence cannot be recreated. Captured71 movement to sentinel location--1 now projects as an explicit gap and cannot extend contact identity across restart. Historical projection is qualified without journal mutation; live repaired delivery remains under observation ([evidence](benchmarks/gameplay-contact-loss-turn71.json)). |
| 14 | Failures and operational recovery | Pre-MCP rejection, argument validation, watcher allocation, stable failure taxonomy and failure-origin capture pass. Popup use-after-lifetime repair passes matched native replay, autonomous Social Psych and active-dialog/LAN regression. [Final-cut catch-up](benchmarks/gameplay-contact-feed-alignment.json) addresses the turn32 recurrence. | Turn41 exposed read-only WAL checkpoint capture failure; private staging preserves source/journals and scoped filtering in helper regressions, and deployed verified41 checkpoint/restore now passes. Turn47 falsely classified an observed production change as no progress because the compact snapshot omitted owned base state. Final native digest build, actual managed production/citizen effect comparisons, stable reads, full control/MCP recovery and C++ foreign/slot-order regressions pass; gated deployed recovery succeeds at47 with exactly one sovereign and the native digest sampled; autonomous same-turn reset is under observation. Receipt churn remains excluded. Inclusive output-token accounting passes packaged threshold-boundary regression and is deployed at50 without native rollback. Separate invalid_tile_id origin remains unreproduced with telemetry deployed. Native outages recur at55 and59; both guarded saved-state replays succeed. The59 pre-recovery capture verifies both native exception files absent. Live thread-stack preservation is armed; cause remains undetermined; bounded native exception capture is now deployed before worker replacement. Recovery also exposed a stale portal parked state hiding the healthy stream; newer-verified-recovery reconciliation passes80 portal tests including concurrent owner parking. Deployed existing-browser polling restores the Lal seat and Selkies frame without reload; one sovereign resumes. Actual provider-complete response was misclassified as truncated when Hermes display buffering skipped terminal metadata. Recorded-chunk causal replay and repaired real-Hermes provider/resume/direct-tool regressions pass ([evidence](benchmarks/gameplay-stream-terminal-metadata.json)); harness ebc9db6e5519 is deployed and one sovereign resumed without replacing native state. Continue native effect, final-cut and recovery checks. Turn68 changes query hit the anchor ceiling; frontier-detail demotion passes captured-state exact history replay and regression checks. [Evidence](benchmarks/gameplay-world-anchor-pressure.json); deployed with one sovereign and retained native state; assisted deep retry succeeds and the exact first-page result/continuation reaches the next provider request. Full autonomous pagination is not claimed. Supervisor detects the resulting68 attention stall after10 calls/363 seconds and quarantines the native worker; no weakening of no-progress containment. Turn69 exposes an indefinitely pending end-turn path. Controlled native refusal/retry and callback/managed-receipt tests pass; new diagnostic markers do not count as progress. Final serial native validation and verified69 deployment recovery pass ([evidence](benchmarks/gameplay-end-turn-refusal-turn69.json)). Sovereign-reported SOCIETY gap73 is repaired through guarded deferral; native effect and exact provider receipt pass after verified72 recovery ([evidence](benchmarks/gameplay-society-prompt-turn73.json)). Repeated modal waits self-corrected; clearer wait responses pass tests and native comparison ([evidence](benchmarks/gameplay-wait-interaction-turn72.json)).  Verified75 recovery retains newly completed own-unit-27 and unchanged social models; earlier own-unit-19 loss is attributable to pre-checkpoint NOSUPPORT, not recovery. Support-context repair passes controlled native dialog and actual removal; deployment and verified75 native recovery pass; resumed sovereign support-query delivery remains pending. |
| 15 | Actor output and reasoning | Actual emitted response/reasoning and controlled SSE/transport records retain actor separation and request correlation. | Only emitted output is observable; private/unemitted reasoning is not available. |
| 16 | Campaign diagnostics download | Authorization and archive contracts pass; live ZIP integrity, recent runtime telemetry and bounded export timing are verified. Lobby desktop/mobile layout was checked. | Browser download reports ERR_BLOCKED_BY_CLIENT; browser delivery remains unverified despite backend ZIP success. |
| 17 | Prompt and tool education | Minimal operating contract, schema and doctrine checks pass:1,338 exact Qwen operating-contract tokens and4,641 exact tool-schema tokens. Live direct schemas and memory guard mapping are present. Missing base selectors now receive public base_ref retry guidance and declared conditional requirements; actual deployed tool-schema delivery verified; turn-frame metadata is delivered. Wait responses now distinguish blocking interactions from engine processing; native read-only comparison passes and deployed sovereign use remains under observation. | Continue behavioral validation; supplied instructions are not proof that the model used them. |
| 18 | Evidence-based attribution | [Findings](gameplay-diagnostics-findings.md) distinguish confirmed delivery/semantic/native bugs from affordance weaknesses and missing evidence. Actual erased affordable-hurry answer establishes a harness defect. Later zero-combat prose is paired with a correct delivered count of four combat-capable Scouts; literal inaccuracy is attributable, while the broader strategy remains unjudged. | Earn behavioral attribution from the complete delivered/executed chain; do not promote controlled fixtures into autonomous proof.  Turn75 old-Garrison loss is a native support-shortage outcome before recovery; new-Garrison completion and restore remain proven. Do not attribute it to combat or checkpoint loss. |
| 19 | Integrated acceptance game | Reviewable checkpoint commits and evidence are on draft PR55. Verified checkpoints and native+AI restores preserve the campaign. Autonomous gameplay reached74; the fourth Garrison is independently verified. Verified72 recovery and sovereign SOCIETY deferral pass with exact next-provider receipt and native dialog closure; full-game acceptance remains open. Verified native+AI restores preserve two bases, completed Formers, citizen allocation and the sovereign plan; checkpoint46 restores on the selector-feedback deployment; the watchdog observation repair is deployed and gated recovery selects the newer automatically verified47 checkpoint. The turn55 Garrison move encountered a native outage; automatic checkpoint recovery succeeded and the capture repair is deployed and one sovereign has resumed from coherent55 recovery. Matched saved-state/action replay passed to56 without reproducing the outage; that operator replay was discarded through coherent recovery. See [evidence](benchmarks/gameplay-turn55-native-outage.json). | Full game is unfinished; support deployment ran autonomously75→85; the sovereign is parked for plan-binding visibility validation. Continue monitoring, repair, PR updates and deployment; never merge automatically.  Autonomous75 exposes NOSUPPORT explanatory omission; the newly completed Garrison survives verified75 restore. Controlled support estimates and real forced-disband dialog/removal pass, with the full native/MCP recovery suite and deployment passing; actual sovereign support-query delivery remains under observation. |

Turn47 watchdog correction is tracked in [owned-state evidence](benchmarks/gameplay-owned-progress-watchdog.json): observed production was absent from the supervisor snapshot. Python/C++ contracts and final-image native effect/recovery comparisons pass; deployed campaign recovery passes; autonomous same-turn effect reset is under observation. Receipt churn and native safety contracts remain locked.

Supervisor usage accounting: [evidence](benchmarks/gameplay-supervisor-token-accounting.json) corrects addition of Hermes reasoning detail to an already inclusive output total. Old incident evidence remains immutable with an explicit corrected delta; threshold boundary regression passes and deployment resumes at50 without native rollback.

### Turn69 responsive end-turn stall — repair in validation

The preserved worker remains healthy while end-turn action1 stays pending at69. The sampled native UI stack does not show the callback; its timer is cleared and the native turn-complete flag is unset and human input remains active. This does not by itself prove normal callback return. The base window is visible; its causal role is not established. The bridge currently retains the pending latch indefinitely on this returned refusal. A bounded post-return check now reports `native_turn_transition_not_accepted` only on the same actionable human turn while the native human-input gate remains active; nested modal polling and accepted transitions remain pending. Callback contracts pass; isolated running-game validation and deployment are pending. This does not close the separate55/59 outages or full-game acceptance. [Evidence](benchmarks/gameplay-end-turn-refusal-turn69.json).

Controlled native acceptance now passes on the final input-gate implementation: an explicit end-turn refused by the test-only native gate returns `native_turn_transition_not_accepted`; the callback-return marker is true and the pending latch is false. Releasing the fixture gate and submitting a fresh guarded request advances1→2 with `native_turn_advanced`. The earlier turn-complete-bit hypothesis was disproved by this native test; the corrected check uses the native human-input gate. New instrumentation is excluded from the supervisor progress fingerprint. Full serial native acceptance and verified69 deployment recovery pass; the original69 cause is not claimed reproduced.

Final serial native suite passes with the reviewed image:15 guarded tools, native action/counterfactual comparisons, current-turn intent/handoff, stable identity, and repeated checkpoint recovery. Base-site512-base/32-candidate stress is253.401ms wall/227.261ms probe gap; four-site counterfactual stress is245.194ms/224.625ms, below the unchanged500ms ceiling. Prior failed attempts remain in the evidence; no provider-driven refusal or original69 reproduction is claimed.

Deployment acceptance: normal recovery restores verified checkpoint `checkpoint-997b03ee1a3748b0bc010c2e46a6fc9b` at69 with matching memory and native identities into `session-f7791626f0e84163ad9c782f2048b71f`. The observed native source matches reviewed6ac10a95; initial interaction is turn with no pending receipt. Exactly one sovereign, `run-f9947d007ffc42829258997ea4561f7a`, receives a fresh End Turn decision. Failure capture is rearmed and the10-minute mission heartbeat remains active. Autonomous post-repair advancement is under observation; full-game acceptance remains open.

Live post-repair acceptance reaches71. Sovereign end-turn call `chatcmpl-tool-b5773e0a6eda90c6` exposes the pending native confirmation; `chatcmpl-tool-8cca9506dbce2ab2` confirms it, and native70 independently shows completed action1 with no pending latch. At70, `chatcmpl-tool-9a722f29b47e0161` completes action2 and advances71. Each exact tool receipt is present in its following actual provider request. This verifies live continuation after coherent recovery, not reproduction of the original refusal cause.

Turn73 exposes a sovereign-reported `SOCIETY` interaction gap after Industrial Economics: actual native display offers continue/current models or open the social editor, while managed interaction choices are empty. The worker and collector are quarantined and diagnostics preserved. The repair exposes only a guarded explicit deferral to the existing staged social-engineering family; it neither adopts Free Market nor labels the decision information-only. Production-handler guard and managed choice-binding regressions pass. Running native effect, provider delivery and recovery acceptance remain pending. Rows1/14/17/19 remain open at this checkpoint. [Evidence](benchmarks/gameplay-society-prompt-turn73.json).

Turn71–72 combat attribution: the journal proves Scout destruction and Colony Pod damage10→7→5 followed by contact loss; it does not prove the pod destroyed. The sovereign nevertheless records destruction in its plan. Delivered combat completion and projection removals lacked an explicit distinction, and temporal history fabricated `location--1` from a native sentinel. Repairs qualify receipts/removal attention, preserve incomplete movement as a gap, close unsupported cross-publication identity, and qualify old malformed records only at provider projection. Journal originals remain unchanged. Sentinel, restart, valid-zero-tile, historical-copy, episode/feed and runtime-bound regressions pass; deployed delivery and sovereign correction remain pending. Rows1/4/13/18/19 remain open for live acceptance. [Evidence](benchmarks/gameplay-contact-loss-turn71.json).

Turn73 repair predeployment acceptance: the complete native control/MCP suite passes with15 tools, mechanical comparisons, identity restoration and repeated recovery after timeline GC. Base-site stress257.813ms wall/241.040ms probe gap; counterfactual-site stress253.084ms/238.859ms. No live provider was supplied to this isolated suite. Both actual malformed turn71 history records qualify as incomplete under the repaired provider projection without changing retained originals. Native SOCIETY replay and actual new provider delivery remain pending deployment.

Verified72 recovery on reviewed SOCIETY/epistemic images succeeds into `session-47965e5abd0f4d15a6433afc6f12a8db`; one sovereign resumes. It repeats3/5/10-second waits on BEGINPROJECT because wait receipts show only unchanged/modal, then self-corrects, acknowledges it and reaches73 before operator stop. This is a response-affordance delay, not a native deadlock. Repaired waits retain semantic phase/interaction and require a fresh decision at a blocking dialog, including in human traces. Handler/decision contracts pass; read-only live technology-dialog comparison returns in201.723ms for a requested30-second wait. Native SOCIETY replay and final provider delivery remain pending. [Evidence](benchmarks/gameplay-wait-interaction-turn72.json).

Live SOCIETY replay passes: sovereign choice `choice-677672b9195140d0a101cac727095e53` in request `dddefa9725274caaa8fd78cda8f886df` executes the guarded deferral and journals it. Its exact receipt is present in next actual provider request `fb6c92a012f140139af04e82d664d006`. Independent native73 observation shows the dialog closed and phase=turn, all four social models unchanged, and upheaval cost0. This closes the observed action/delivery gap after verified72 recovery; a post-deferral restore has not yet been exercised. One sovereign continues on the deployed wait-response image; full-game acceptance remains open.
