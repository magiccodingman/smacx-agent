# PR #48 focused semantic correctness pass

Status: **focused corrections accepted for final peer review; not merged**.
All existing gates passed in the final runs; one earlier collector timing failure remains recorded below.
Reviewed baseline: `0b07e3400a5f74c9570c7975ac437e11009910a2`.
This pass retains the five accepted architectural checkpoints. It does not merge the PR.

## Findings and evidence boundaries

| Finding | Correction | Evidence |
| --- | --- | --- |
| R1 physical entry/exit watches | Physical land/ocean and mobility membership share the spatial resolver; unsupported/empty direct footprints reject explicitly | Direct versus geography-wrapped entry/exit fixture; physical identity regressions |
| R2 theater footprint | Actual activity locations and explicit linked route cells are distinct from containing regions; world area and scopes use that footprint | Separated western/eastern crises; existing amphibious theater fixtures |
| R3 event-time crossings | Bounded native movement POD captures relationship when observed; private staging and semantic path segments retain occurrence identity, continuous visibility and event-time relationship | Production helper cross-build and compiled controlled adapter; native-shaped full collector crossing then visibility-loss publication; deterministic destruction/diplomacy/discontinuity cases |
| R4 cached derived refs | Current consumers validate the same complete query dependency set/hash, perspective, timeline, epoch, ruleset and calculator version; creation revision is not validity | Managed route query → unrelated revision → warm reuse → scope/watch/area; true dependency invalidation; restart and timeline tests; native-backed routes additionally expire with action revision |
| R5 scope area inspection | Existing `smac_world(mode="area", origin_ref=...)` uses the exact private membership used by watches; output is bounded and qualified | Managed scope query chain and cross-consumer membership checks |
| R6 threshold milestones | Ready/potential/known-pending feasibility determines aggregate state; blocked optional requirements remain visible | 256 all/threshold state combinations; ready/pending transition deduplication |
| R7 unknown physical connection | Potential graph crosses matching known terrain and unknown cells; known opposite terrain blocks | 14 flat/wrapped land/ocean cases including disjoint enclosed fog pockets; geography acceptance |
| R8 explicit dependency reactivation | Journaled prior health for active plan revisions emits one bounded positive transition; stale evidence cannot establish availability | Repeated-state, restart, revision/completion and rollback tests; native-shaped post-world-head publication with injected pre-ack crash; explicit expectation fields only |

## Semantic consumer boundary

The shared spatial resolver is a relocation of existing scope/reference logic,
not a new planning or universal reference framework. Complete geographic
reconstruction supplements current issued anchors so omitted discoveries remain
resolvable. Scope geometry is calculated once and shared by spatial consumers.
Private member lists remain outside ordinary provider descriptors.

Current physical masses, mobility regions, frontiers, theaters, routes and scopes
support their declared world/spatial/operation/dependency consumers. Base and
unit/contact objects are not silently converted into direct entry/exit perimeters:
use an explicit supported base/location scope. Unsupported inputs fail explicitly.

Immutable specialist snapshots currently retain world objects, not live watch
rows or query-cache capabilities. Commissioning a world specialist with such a
live-derived handle now rejects explicitly; nominate immutable object refs and
let the specialist derive geometry inside its frozen perspective. Existing
specialist snapshot and fair-play contracts remain the positive object evidence.
No new specialist architecture or cross-perspective resolver is introduced.

## Active-scope measurements

The measured workload contains four proximity scopes, two geography scopes, one
route corridor, two unions, four crossing watches and observed movement. These
are simultaneous measurements, not multiplication of a single-scope result.

| Measurement | Before reuse, 6,400 tiles | After reuse, 6,400 tiles | After reuse, 25,600 tiles |
| --- | ---: | ---: | ---: |
| Registry | 763 ms | 69 ms | 245 ms |
| Inspection of all nine scopes | 7,273 ms | 698 ms | 2,969 ms |
| Watch evaluation | 897 ms | 141 ms | 356 ms |
| Maximum public descriptor | 113 tokens | 113 tokens | 113 tokens |

Two bounded disposable cache entries retain shared geography/registry work.
Validity keys include perspective/timeline/epoch/world and action revision, current scope rows,
issued query dependencies, region membership and anchor inputs. Process restart
reconstructs these caches. No cache row is made current by merely rewriting its
creation revision.

A separate 6,400-square **native-shaped full collector** run with all nine scopes
and four watches published an observed crossing followed by visibility loss.
Collection took 3,337 ms with a 147 ms maximum independent probe gap; it emitted
crossing attention despite the final loss. This is production-pipeline evidence,
not a running-game UI timing claim.

The final 25,600-square active-scope collector passed: initial publication
19,143 ms, creation of all nine scopes 34,173 ms, active publication 11,367 ms,
and maximum independent probe gap 456 ms. Two crossing alerts survived loss in
the same publication. Initial shared registry construction took 2,424 ms;
individual scope creation took 575–1,996 ms in the standalone workload. Scope
creation and all-scope inspection are user-request work, not native UI calls.
The 500 ms law applies to the independent probe; it is not a per-query ceiling.
Shared geography construction dominates the first lookup. Two disposable cache
entries reduce repeated evaluation while retaining full dependency validation.

The complete collector benchmark first failed its unchanged 30-second initial
25,600-square quiet gate at **55,914 ms** (279 ms probe). An isolated profile
subsequently measured **18,792 ms** (271 ms probe). Journal append consumed
11.40 seconds, replay 3.20 seconds and projection replacement 3.51 seconds in
that profile; no expensive geographic resolver ran during its initial publish.
The cause of the first failure is not established. Do not interpret the passing
repeat as proof that all host timing variability has been eliminated.

The final six-case collector run passed the existing gates: the gated large
quiet case took **19,527 ms** (341 ms probe). The dense orbital case took
32,450 ms initially and 10,861 ms unchanged; the pre-existing suite gates its
bounded unit payload and probe, not its initial wall time. Its 73,339-byte
routine unit payload and 322 ms probe passed. No thresholds, fsync behavior,
publication ordering, or journal authority were weakened.

## Context and schema

Current conservative measurements retain the accepted limits:

- Huge quiet growth: **0.285%** for 100× the tile count.
- Huge fragmented 64K anchor: **5,449 tokens**.
- Huge chaotic anchors: **5,669 / 15,809 tokens**, below **6,000 / 16,000**.
- Runtime stress anchor: **15,932 tokens** at 256K, including intent/attention workload.
- Managed tools: **15**; `smac_world` schema **1,855 bytes / 619 conservative tokens**.
- Prompt SHA remains unchanged. Exact provider tokenization was not rerun; the
  prior 1,117-token Qwen measurement remains historical evidence.
- Geography warm queries retain improvement: relation **261 → 75 ms**, compare
  **260 → 82 ms**, logistics **99 → 80 ms**, area **61 → 47 ms** in this pass.
  The managed route test independently asserts a warm hit without rewriting
  the row's creation revision.

## Native scope and limitations

Native changes add a bounded copied relationship field to the existing observation
ring. The LLVM-MinGW cross-build passes with 22 existing warnings. A host-compiled
production helper fixture verifies event-time copies, diplomacy changes and ring
bounds with controlled native-shaped inputs. It is not a new live mechanics
comparison. Existing native game/LAN/action/recovery measurements remain historical
checkpoint-5 evidence. Unrelated destructive suites are not rerun merely for ceremony.

The existing limitation remains: stock native Pact contact/reopening map-refresh
behavior lacks a clean controlled comparison. Managed Pact effects and separate
map-transfer code paths do not imply continuous allied live vision.

Full psych/commerce/research/climate simulation remains outside the established
counterfactual coverage. No new tool, strategist, universal ranking system,
gameplay doctrine or hidden-state channel is introduced.

## Exact executions and evidence classification

The following 30 scripts passed during this focused pass. Changed paths received
focused reruns after their final edits. MCP-dependent scripts ran in the supplied
`smacx-agent-control:dev` MCP environment. The compiled adapter script ran on the
host. No specialist supervisor implementation changed, so its prior host process
acceptance remains historical rather than being claimed as a new run.

- `scripts/active_scope_benchmark.py`
- `scripts/active_scope_collector_benchmark.py`
- `scripts/attention_communication_contract_test.py`
- `scripts/counterfactual_choice_contract_test.py`
- `scripts/counterfactual_contract_test.py`
- `scripts/event_time_episode_test.py`
- `scripts/fair_play_world_test.py`
- `scripts/geographic_semantics_contract_test.py`
- `scripts/milestone_contract_test.py`
- `scripts/milestone_threshold_transition_test.py`
- `scripts/native_event_time_contract_test.py`
- `scripts/native_observation_contract_test.py`
- `scripts/observation_batch_recovery_test.py`
- `scripts/observation_collector_benchmark.py`
- `scripts/plan_dependency_publication_test.py`
- `scripts/plan_dependency_transition_test.py`
- `scripts/plan_health_contract_test.py`
- `scripts/provider_schema_budget_test.py`
- `scripts/rollback_world_contract_test.py`
- `scripts/runtime_context_contract_test.py`
- `scripts/semantic_consumer_contract_test.py`
- `scripts/semantic_ref_matrix_test.py`
- `scripts/semantic_ref_recovery_test.py`
- `scripts/sovereign_geography_acceptance_test.py`
- `scripts/sovereign_hardening_contract_test.py`
- `scripts/spatial_scope_contract_test.py`
- `scripts/specialist_contract_test.py`
- `scripts/strategic_world_fixtures_test.py`
- `scripts/world_context_benchmark.py`
- `scripts/world_model_contract_test.py`

The native LLVM-MinGW bridge cross-build also passed. R1/R2/R4/R5/R6/R7/R8 have
deterministic contract evidence; R3 additionally has compiled production native
adapter evidence and native-shaped observation publication evidence. R8 has
native-shaped publication/crash-retry evidence. Actual `smac_world` and cognition
functions execute in the managed-chain fixtures with explicitly stubbed native
selector/refresh boundaries; this is not a live provider or running-game test.
The composite peninsula/coastal-water fixture creates, inspects and watches
through managed functions, then checks the same membership after a crossing.

All 10 representative semantic kinds are covered. The matrix also exercises
both an omitted frontier and an omitted physical mass from paginated discovery.
Object-specialist positive evidence comes from `specialist_contract_test.py`;
live-derived specialist inputs explicitly reject instead of silently producing
an empty immutable-world view. Route/corridor restart, checkpoint fork and
cross-perspective isolation pass. No live mechanics prediction accuracy claim
was added, so unrelated destructive game/LAN suites were not rerun.

[Sanitized numeric results and the exact test list](results/2026-09-05-peer-review-corrections.json)
preserve the failed timing measurement alongside final acceptance.
