# Sovereign checkpoint 2: managed action paths

Status: checkpoint 2 passed. Both final single-player and two-seat integrated
acceptance passed. Checkpoints 3 through 5 remain separate required work.
Baseline: `95b7d4ebcebcda7beeb5e7a649bb8c5dc8d159d2`.
Machine-readable results: [sanitized evidence](results/2026-09-04-managed-action-paths.json).

## Implemented boundary

The surface remains 15 tools. `smac_choices` issues bounded, read-only preparation
capabilities for Workshop components, bulk-upgrade source/replacement, the complete
Social Engineering combination, allocation percentages and supported energy
amounts. Each selection consumes its previous capability. Native match/session/
revision and world timeline/epoch bind every draft. Expiry and process recovery
revoke drafts; final choices receive fresh native legality and cost validation.
Only the existing opaque executor mutates gameplay. Native IDs, command names and
argument dictionaries are not added as provider inputs.

Council proposals bind candidate or yea/nay ballots into closed choices. Directed
research rows gain their missing native command; Blind Research guards remain.
Human technology catalog values are translated to the executor's reviewed
parameter. An optional Workshop name uses the executor's existing bounded text
field. Unbound numeric choices remain withheld.

Native information rows preserve technology names, clauses, loan principal and
payment schedules. Stale-choice revalidation compares displayed consent terms as
well as arguments and campaign identity. Different agreements have different
repetition fingerprints; repeated identical choices still trip the circuit.
Owned-base observations now expose bounded queues, governor settings and citizen
assignments, and owned economy exposes treasury credits for effect queries.
Foreign bases receive no additional entitlement.

The container explicitly includes the preparation module. The worker image copies
rebuilt native artifacts after runtime dependencies, avoiding expensive unrelated
runtime rebuilds when only bridge code changes.

## Running-game evidence

The isolated control/MCP single-player test passed against rebuilt native and MCP
images, using actual managed tool calls for the following workflows:

| Family | Guarded action and observed effect |
| --- | --- |
| Workshop | Create and rediscover the selected custom design; this does not claim retirement was executed |
| Upgrades | Single-unit upgrade and staged bulk upgrade; displayed counts/cost agree with execution and world unit observations |
| Energy allocation | Selected 40/20/40 split agrees with native and provider-queryable economy |
| Social Engineering | Complete selected model combination agrees with native and world state |
| Production queue | Set, append and remove through managed choices; world queue verifies each effect |
| Citizens/governor | Governor change and worker/specialist assignment effects appear in world base fields |
| Transport | Public colocated rendezvous calculation reaches guarded boarding; observed cargo increases by exactly one |
| Terraforming | Managed order is accepted and visible; completed terrain improvement is not asserted |
| Founding | Managed founding produces a base at the observed colony location |
| Native gift | Non-offered 37 credits rejected before mutation; selected offered 125 credits transfers exactly |
| Technology purchase | Quoted technology and price preserved; treasury change and acquired technology verified |
| Loan | Quoted principal/payment/term preserved; treasury and native debt/payment records verified |
| Council | Managed proposal plus ballot resolves to the observed public Council result |

The same run passed checkpoint/volume backup, intentional native crash recovery,
private vehicle-identity restoration and sidecar removal on park. It also passed
Pact-port movement/boarding and demanded Drop legality, including an exact target
outside the public page. Routine collection still omits destination enumeration.

The final two-seat run verified managed energy, technology and Pact offers,
recipient-visible clauses, acceptance and replicated effects. Managed recovery
restored journal forks, both native identity capsules and faction seats. Owned
treasury, technologies and Pact counterparts matched before checkpoint and after
recovery, with current provider world fields. The final single-player run also
independently discovered all fixture actors through paginated ordinary world
queries before selecting their actions.

## Repairs exposed by live acceptance

* Native energy gifting uses a discrete menu, not arbitrary numeric entry. The
  initial request for 37 transferred 25 and correctly failed effect verification.
  The bridge now advertises the native menu values and selects the requested
  offered value without temporarily rewriting treasury balances.
* A Pact fixture stalled inside `veh_init -> spot_all -> wants_to_speak`, opening
  `COMM` during its synchronous request. The controlled fixture now establishes
  an already-discussed Pact and clears pending talk flags. Native UI-thread guards
  and ordinary unit initialization remain intact. Fresh live acceptance passed.
* Accepting three different human agreements falsely triggered repetition
  protection because information/clauses were omitted from its fingerprint.
  Terms now distinguish those states; both same-state rejection and changed-state
  distinction have regression coverage.
* The Drop page sorted an initial map-scan slice. A bounded heap now selects the
  globally nearest targets. The destructive native fixture checks count and
  ordering against an independent full-sort exact-legality oracle after its
  controlled visibility setup. Fresh integrated acceptance passed.
* LAN restart could collide with supervision recovery of an intermediate missing
  peer. Whole LAN startup and supervisor observation/decision now share the
  existing lifecycle lock. A concurrent regression proves the supervisor cannot
  observe the partial transition; full live managed recovery subsequently passed.

The live test also explicitly declines the optional Unit Workshop offer following
technology acquisition through its existing managed choice. No generic popup
fallback or runtime automatic decision was added.

## Bounds and validation

Fifteen focused contract checks passed, covering preparation, stale terms,
opaque execution/repetition, semantic binding, fairness, native observation,
worker/lifecycle serialization, HTTP and incident recovery, operations, and schema.
Native-shaped contracts establish adapters and guards, not predictions of game
mechanics. The native image build completed all 38 build steps with 16 compiler
warning diagnostics and no errors. The incremental host LLVM cross-build also
passed, with 22 warnings. This checkpoint does not add a counterfactual exactness claim.

| Measurement | Result |
| --- | ---: |
| Managed tools | 15 |
| `smac_world` schema | 1,475 bytes / 492 conservative tokens |
| All tool schemas | 18,742 bytes |
| Unchanged v6 prompt | 5,280 bytes; exact tokenizer not rerun |
| 512-base / 32-site receipt | 222.476 ms wall / 215.505 ms maximum probe gap |
| Site receipt payload | 171,045 bytes |
| 129 ready Drop units on 8,192 tiles, routine page | 222.600 ms maximum / 128,007 bytes maximum |
| Demanded Drop page | 217.233 ms wall / 221.862 ms probe / 8,669 bytes |
| Exact outside-page Drop receipt | 243.663 ms wall / 249.584 ms probe / 287 bytes |

Two concurrently started isolated campaigns failed native startup health checks
before gameplay. A subsequent standalone run passed; the concurrent startup cause
is unresolved and must not be described as repaired. Deadlines were not increased.
Provider inference and specialist/provider-cache acceptance were not run here.
Raw logs, debugger output, native binaries and game assets remain excluded from
committed evidence. Later checkpoints retain production lifecycle events,
intent-linked attention, counterfactual assistance and integrated stress/recovery
acceptance as separate work.

A subsequent two-seat recovery exposed a separate native roster loop. Debugger
inspection found `NetWin::prepare_game` repeatedly selecting catalog IDs 0..6
while its loaded selector pool used saved slots 1..7 (record zero unavailable).
The existing filter could never return the remaining slot 7. Catalog filtering
now applies only to new games; saves/scenarios retain native saved-slot selection.
Explicit managed rosters also take precedence over optional random-roster
exclusions so their intersection cannot remove a required faction. Ordinary
unconstrained random-roster exclusions remain intact. A compiled-adapter fixture
with controlled RNG proves all seven distinct choices remain reachable in each
mode. It is adapter proof; the final live LAN run separately passed native reload and
compared owned treasury, technologies and Pact counterparts before checkpoint and
after recovery, including current provider world fields.
