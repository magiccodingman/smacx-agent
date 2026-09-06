# Contributor testing

SMACX Agent separates tests that run on an ordinary development machine from
tests that require an operator-owned game installation, Docker, a model
provider, or a native LAN fixture. Tests create isolated data roots and must not
write acquired game/reference content into the repository.

## .NET portal and knowledge services

Restore once, then run the solution:

```bash
dotnet restore Smacx.Agent.slnx
dotnet test Smacx.Agent.slnx --no-restore
```

The solution covers portal accounts and authorization, lobby lifecycle,
presence and controller leases, provider profiles, Graphiti reconciliation,
network policy, knowledge organization, embedding audits, and response
handling.

## Python contracts

Compile the Python surface and run the dependency-free control contracts:

```bash
python3 -m compileall -q src worker scripts
PYTHONPATH=src python3 scripts/control_plane_test.py
PYTHONPATH=src python3 scripts/control_http_test.py
PYTHONPATH=src python3 scripts/worker_contract_test.py
PYTHONPATH=src python3 scripts/capability_manifest_test.py
PYTHONPATH=src python3 scripts/mcp_command_schema_test.py
PYTHONPATH=src python3 scripts/semantic_choice_binding_test.py
PYTHONPATH=src python3 scripts/stale_revision_recovery_test.py
PYTHONPATH=src python3 scripts/match_briefing_contract_test.py
PYTHONPATH=src python3 scripts/strict_prompt_contract_test.py
PYTHONPATH=src python3 scripts/operations_contract_test.py
PYTHONPATH=src python3 scripts/capability_incident_contract_test.py
PYTHONPATH=src python3 scripts/incident_recovery_test.py
PYTHONPATH=src python3 scripts/worker_lifecycle_serialization_test.py
PYTHONPATH=src python3 scripts/graphiti_worker_contract_test.py
PYTHONPATH=src python3 scripts/reference_corpus_test.py
PYTHONPATH=src python3 scripts/campaign_journal_test.py
PYTHONPATH=src python3 scripts/ai_memory_checkpoint_test.py
PYTHONPATH=src python3 scripts/opaque_choice_execution_test.py
PYTHONPATH=src python3 scripts/semantic_progress_contract_test.py
PYTHONPATH=src python3 scripts/world_model_contract_test.py
PYTHONPATH=src python3 scripts/native_observation_contract_test.py
PYTHONPATH=src python3 scripts/fair_play_world_test.py
PYTHONPATH=src python3 scripts/strategic_world_fixtures_test.py
PYTHONPATH=src python3 scripts/geographic_semantics_contract_test.py
PYTHONPATH=src python3 scripts/global_world_pipeline_test.py
PYTHONPATH=src python3 scripts/movement_mechanics_contract_test.py
PYTHONPATH=src python3 scripts/runtime_context_contract_test.py
PYTHONPATH=src python3 scripts/notebook_scale_test.py
PYTHONPATH=src python3 scripts/attention_communication_contract_test.py
PYTHONPATH=src python3 scripts/spatial_scope_contract_test.py
PYTHONPATH=src python3 scripts/spatial_scope_scale_test.py
PYTHONPATH=src python3 scripts/milestone_contract_test.py
PYTHONPATH=src python3 scripts/plan_health_contract_test.py
PYTHONPATH=src python3 scripts/specialist_contract_test.py
PYTHONPATH=src python3 scripts/specialist_supervisor_contract_test.py
PYTHONPATH=src python3 scripts/specialist_provider_capture_test.py
PYTHONPATH=src python3 scripts/specialist_provider_meter_test.py
PYTHONPATH=src python3 scripts/managed_memory_scale_test.py
PYTHONPATH=src python3 scripts/rollback_world_contract_test.py
PYTHONPATH=src python3 scripts/provider_schema_budget_test.py
PYTHONPATH=src python3 scripts/reference_bounding_contract_test.py
PYTHONPATH=src python3 scripts/specialist_snapshot_gc_scale_test.py
PYTHONPATH=src python3 scripts/world_context_benchmark.py
PYTHONPATH=src python3 scripts/observation_collector_benchmark.py
PYTHONPATH=src python3 scripts/amphibious_query_benchmark.py
```

The provider-wire context policy must run inside the built Hermes image because
it deliberately tests the pinned harness's private message-construction hooks.
The fixture mirrors Hermes's generic MCP `tool_call` dispatcher envelope and
asserts that completed episodes are pruned, current-episode reasoning and tool
protocol remain coherent, superseded state frames collapse, and a long active
episode stays below its provider-wire growth ceiling:

```bash
docker run --rm --entrypoint /opt/hermes/.venv/bin/python \
  -e PYTHONPATH=/workspace/harness \
  -v "$PWD:/workspace:ro" -w /workspace \
  smacx-agent-harness:dev scripts/harness_context_policy_test.py
```

Managed parameter-path contracts require the MCP dependency environment. The
single-player control/MCP live test also runs `managed_action_path_live_test.py`
against the current managed endpoint; the two-seat control LAN test runs
`managed_human_action_live_test.py`. Both use isolated installations and the
packaged native runtime. Build both images from the same checkout and select them
with `SMACX_TEST_CONTROL_IMAGE`, `SMACX_TEST_MCP_IMAGE`, and
`SMACX_TEST_WORKER_IMAGE`. Set `SMACX_TEST_GAME_SOURCE` to the operator-owned game
directory. The managed helpers use native fixtures only for controlled setup and
native observations for additional effect checks; actions go through issued
managed choices. Raw logs, saves and game assets must stay outside committed
evidence.

```bash
docker run --rm --entrypoint /opt/smacx/mcp-venv/bin/python \
  -v "$PWD:/workspace:ro" -w /workspace -e PYTHONPATH=/workspace/src \
  smacx-agent-control:dev scripts/managed_action_path_contract_test.py
```

The native roster adapter regression runs on the host with a C++ compiler:

```bash
python3 scripts/native_lan_roster_contract_test.py
```

It compiles the production adapter with controlled RNG and selector-state inputs.
Actual loaded-game and journal/identity recovery are separately exercised by the
control LAN live test; the compiled adapter is not a substitute for that proof.

Additional focused `scripts/*_test.py` files cover individual semantic action
families. Run the relevant focused contract whenever changing its bridge,
controller, MCP schema, worker, or portal behavior.

## Browser and installable-app contracts

```bash
node scripts/pwa_install_test.mjs
node scripts/incident_actions_test.mjs
python3 scripts/human_ui_safety_test.py
```

For UI changes, also inspect the real portal at desktop and mobile widths.
Exercise the affected lobby, administration, play, spectator, chat, recovery,
or install flow rather than relying only on component compilation.

## Container integration

Build and start the same images used by operators:

```bash
SMACX_GAME_SOURCE=/absolute/path/to/your/game \
  ./scripts/control-center-up.sh
docker compose ps
curl --fail http://127.0.0.1:8080/healthz
```

The launcher serializes memory-intensive image builds. Do not add parallel
build steps that make the ordinary development launcher less reliable on a
small home-lab host.

Graphiti has an optional integration profile:

```bash
./scripts/graphiti-up.sh
PYTHONPATH=src python3 scripts/graphiti_projection_test.py
PYTHONPATH=src python3 scripts/graphiti_worker_contract_test.py
PYTHONPATH=src python3 scripts/graphiti_provider_capture_test.py
```

## Provider and Hermes boundaries

The contained provider-capture tests use local recording endpoints and must not
print prompts, reasoning, secrets, or response content:

```bash
docker build -f harness/Dockerfile -t smacx-agent-harness:dev .
PYTHONPATH=src python3 scripts/hermes_adapter_test.py
PYTHONPATH=src python3 scripts/hermes_provider_capture_test.py
PYTHONPATH=src python3 scripts/provider_generation_probe_test.py
PYTHONPATH=src python3 scripts/graphiti_provider_capture_test.py
```

Tests against a real OpenAI-compatible provider are opt-in. Supply credentials
through ignored environment variables or secret files, never command output,
fixtures, snapshots, or commits.

The v6 prompt and `smac_world` gates use the provider's exact tokenizer when
available, and the prefix-cache test reads content-free vLLM counters:

```bash
SMACX_QWEN_TOKENIZE_URL=http://provider.example/v1/tokenize \
  SMACX_QWEN_TOKENIZE_MODEL=model-id \
  PYTHONPATH=src python3 scripts/provider_schema_budget_test.py

PYTHONPATH=src python3 scripts/provider_prefix_cache_live_test.py \
  --base-url http://provider.example/v1 --model model-id

PYTHONPATH=src python3 scripts/specialist_provider_live_test.py \
  --base-url http://provider.example/v1 --model model-id
```

The specialist capture and live gates start real disposable Hermes processes.
Each child receives exactly one faculty instrument, no personality or sovereign
transcript, no game volume, and no unrestricted terminal/files/web/chat/memory/
delegation/mutation authority. The tests cover iterative retrieval, stable
system/tool prefixes, actual-call dependency capture, strict results, fresh
attempt isolation, hard leashes, cancellation, retry, schema repair, scheduling,
and trace retention. `specialist_provider_meter_test.py` separately proves the
attempt-local provider proxy enforces actual calls, cumulative usage,
per-request context, and output reservations even when a failed response omits
usage. It also proves that `/chat/completions` and `/v1/chat/completions` inputs
resolve to exactly one `/v1` prefix when the configured provider base already
contains it. Live scripts print only content-free usage/cache/latency aggregates.
The provider-facing child evidence envelope contains a bounded semantic result
and one opaque query receipt; internal object/material hashes and dependency
graphs remain in the retained diagnostic trace rather than being replayed into
the child's next prompt.

## Native game integration

Native semantic tests require the user's installed game, the built bridge, and
the prepared Wine/Proton environment. Relevant entry points include:

```bash
./scripts/build_bridge.sh
PYTHONPATH=src python3 scripts/native_automation_turn_test.py
PYTHONPATH=src python3 scripts/save_load_test.py
PYTHONPATH=src python3 scripts/full_endgame_pipeline_test.py
```

`native_observation_contract_test.py` checks the native observation adapters,
including more than 256 events, cross-page visible movement continuity, visible
destruction versus fog loss, capture/recapture, tile visibility transitions,
chat, global events, public Secret Project report transitions, durable
two-phase publication across both injected crash windows, and a staged drain
larger than the native ring. The adversarial recovery fixture mutates the native
feed and action revision after partial journal publication and proves N finishes
from its original immutable package before N+1 consumes the new event. It also
rejects action revisions coupled to native
row indices and verifies that loss/reappearance retires only the affected
foreign visible-episode identity. The managed real-game gate deliberately
compacts an owned VEH row before checkpoint, kills the native worker, and
proves the surviving stable refs and private identity capsule restore exactly.
`global_world_pipeline_test.py` carries legitimate global, intelligence,
base-geography, support, convoy, native-life, orbital, project, ecology,
victory, anchor, runtime, and frozen-specialist facts through the complete
provider-safe projection path.

`movement_mechanics_contract_test.py` proves route, reachability, response, and
lost-contact envelopes share one stateful arrival engine across turn
boundaries, fungus, rough terrain, roads, rivers, tubes, ZOC, occupancy and
remaining movement, including residual disappearance phases and refreshed
movement after unseen turn boundaries. It routes own, Pact, truce, hostile and
epistemically unknown subjects under subject-relative access, including
foreign aircraft refuelling/carrier/gate isolation. Transport fixtures cover
adjacent-coast rejection, same-square coastal-port rendezvous, passenger and
transport arrivals, 0/partial/full residual movement, same-turn transport
movement after boarding, the passenger's mandatory post-board refresh,
charged disembark movement, same-turn land continuation, and final aggregate
ETA. Exact embark fixtures require an active provider-safe base object with
current owned-or-Pact coastal and relationship evidence and reject stale,
destroyed, missing, enemy, Treaty, Truce, and neutral ports. A winding-region adversary proves preparatory
arrival search exhausts the finite known graph instead of stopping at
`width + height`. Foreign-airdrop fixtures cover same-owner, sovereign, Pact,
war, treaty, and unknown occupants/bases without borrowing sovereign ZOC or
diplomacy; owned Drop receipts are demand-driven per specific unit/action
revision and expose truncation, while routine collection reports only readiness
and range. The amphibious benchmark separately enforces bounded candidate
frontiers, explicit search completeness/optimality, transport ownership and
diplomatic access, capacity/boarding legality, and conditional opposed landing
on a 4,096-square custom world. `native_airdrop_legality_test.py` is the focused
entry point for an isolated native worker deliberately launched with both
`SMACX_AGENT_TEST_MODE=1` and `SMACX_ACCEPTANCE_AIRDROP_LEGALITY=1`; either flag
alone is rejected by the destructive endpoint and production workers receive
neither. The managed live worker gate covers the same narrow fixture in its
isolated game process and runs the real
`allow_airdrop` diplomacy, Aerospace Complex, and stationed Air Superiority
matrices plus many-ready-orbital-Drop routine-page latency/payload checks before
parking its disposable no-timer worker.

The geographic/LOD acceptance suite is deterministic and uses native-shaped
tile, base, unit, faction, landmark, repair-rule, and guarded site-receipt rows:

```bash
PYTHONPATH=src python3 scripts/geographic_semantics_contract_test.py
```

It proves physical land/ocean identity invariance, terrain split/merge,
coastal mobility separation, territorial/resource/landmark aggregation,
unknown-connectivity frontiers, lazy scout access, cross-region theaters,
active-plan/recent-event promotion, non-ranking expansion mechanics,
repair/staging logistics, and Huge fragmented-map bounding.
The latest content-free measurements are recorded in
[Geographic semantics and hierarchical LOD acceptance](benchmarks/2026-09-04-geographic-semantics.md).

LAN integration additionally requires isolated native workers and appropriate
DirectPlay networking. Use the dedicated scripts for the path being changed:

```bash
PYTHONPATH=src python3 scripts/lan_profile_contract_test.py
PYTHONPATH=src python3 scripts/external_lan_contract_test.py
PYTHONPATH=src python3 scripts/human_hosted_lan_contract_test.py
PYTHONPATH=src python3 scripts/virtual_lan_contract_test.py
```

Native tests must use an isolated display and test-owned match/data roots. They
must never send input to the developer's normal desktop or reuse a live player
campaign.

The whole managed path has one opt-in, self-cleaning integration fixture. The
worker contains the pinned Proton/DirectX runtime, so the only acquired runtime
input is the absolute game-directory path. Use distinct test image tags; the
fixture binds its control API to an ephemeral loopback port and does not touch a
running portal stack:

```bash
SMACX_TEST_GAME_SOURCE=/absolute/path/to/your/game \
SMACX_TEST_CONTROL_IMAGE=smacx-agent-control:test \
SMACX_TEST_WORKER_IMAGE=smacx-agent-worker:test \
SMACX_TEST_MCP_IMAGE=smacx-agent-control:test \
SMACX_TEST_HERMES_IMAGE=smacx-agent-harness:test \
  PYTHONPATH=src python3 scripts/control_worker_mcp_live_test.py
```

If host Python lacks `mcp`, run the driver from the control image. Unlike the
ordinary Python contracts, this driver also needs the Docker CLI/socket and host
network access to its ephemeral loopback endpoints:

```bash
docker run --rm --no-healthcheck --user 0 --network host \
  --entrypoint /opt/smacx/mcp-venv/bin/python \
  -v /usr/bin/docker:/usr/bin/docker:ro \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD:/workspace:ro" -w /workspace -e PYTHONPATH=/workspace/src \
  -e SMACX_TEST_GAME_SOURCE=/absolute/host/path/to/your/game \
  -e SMACX_TEST_CONTROL_IMAGE=smacx-agent-control:test \
  -e SMACX_TEST_MCP_IMAGE=smacx-agent-control:test \
  -e SMACX_TEST_WORKER_IMAGE=smacx-agent-worker:test \
  -e SMACX_TEST_HERMES_IMAGE=smacx-agent-harness:test \
  smacx-agent-control:test scripts/control_worker_mcp_live_test.py
```

Validate packaged imports before launching: source-mounted contracts cannot prove
that newly added modules were copied into the image. The control Dockerfile now
imports the installed MCP/intent/export modules during its build. For failed-run
inspection, `SMACX_TEST_KEEP_ON_FAILURE=1` retains test resources and pauses the
test control service; preserve evidence and remove only that run's labeled
resources after inspection. This flag does not pause every native container.

To add the managed-provider gate, also set `SMACX_TEST_PROVIDER_URL` and
`SMACX_TEST_PROVIDER_MODEL` through the local environment. The provider run is
successful only after a journal-observed semantic revision changes; merely
starting Hermes or allowing native time to pass is not success. The test also
checks exact-seat sidecar binding, the 15-tool managed surface, checkpoint and
backup integrity, native crash recovery, a fresh recovery-side sovereign lease,
low-reasoning profile selection, and clean parking. Output is content-free.

Every autonomous-play benchmark must set the native multiplayer turn clock to
**None**. A timed game can advance on the model's behalf and is not evidence of
successful agent control. Generate a content-free causal report with:

```bash
python3 scripts/agent_simulation_report.py \
  --campaign-root /path/to/control/campaigns \
  --portal-db /path/to/portal.sqlite3 \
  --match-id match-... \
  --output docs/benchmarks/results/<date>-<label>.json

python3 scripts/hermes_session_audit.py \
  --database /path/to/profile/state.db \
  --output docs/benchmarks/results/<date>-<label>-hermes.json
```

The first report counts only journaled actions and observed before/after turn
changes as causal success. The second reports aggregate tool frequency,
malformed records, exact repetition, compression health, handoff compliance,
and token totals. Neither tool emits prompts, responses, chat, reasoning text,
arguments, endpoints, secrets, game assets, or saves.

The strategic-world rebuild additionally has a deterministic 64K/256K and Huge
map acceptance report in
[Strategic world and provider context](benchmarks/2026-09-03-strategic-world-rebuild.md).
The first published gameplay comparison using this procedure is the
[bounded-runtime no-timer smoke test](benchmarks/2026-09-02-bounded-runtime.md).

## Repository boundaries

Before opening a pull request, confirm that the diff contains none of the
following:

- game executables, DLLs, saves, movies, voices, or proprietary data files;
- downloaded/cleaned reference prose or embeddings;
- provider credentials, session tokens, cookies, passwords, or private host
  addresses;
- generated Wine prefixes, raw logs, screenshots, backups, caches, saves, or
  private test data;
- reference-machine validation diaries, temporary roadmaps, or coding-agent
  notes.

Public test documentation should describe repeatable procedures. Sanitized,
reproducible benchmark aggregates may live under `docs/benchmarks/results`;
reference-machine diaries, raw transcripts, and maintainer backlog remain
outside the repository.

## Sovereign checkpoint acceptance

Run `scripts/sovereign_hardening_contract_test.py` and
`scripts/sovereign_geography_acceptance_test.py` with `PYTHONPATH=src` alongside
the existing geography, world, observation, fair-play, movement, runtime,
attention, rollback and semantic-choice contracts. The new fixtures exercise
actual service queries, cache invalidation/reuse, omitted geography discovery,
60-base global awareness, distinct theater crises, field predicates, Survey
entitlement and transport-dependent repair/staging.

The isolated `control_worker_mcp_live_test.py` additionally enables the dual-gated
base-site receipt stress endpoint. It temporarily installs 512 owned base input
rows, requests 32 sites with 21-square radii, restores the input state, and checks
native/UI wall and probe time against the unchanged 500 ms law and a 256 KB
receipt ceiling. This is a native read-path stress fixture, not a gameplay proof
that 512 bases were legally founded. Never enable acceptance endpoints in a
production worker. See the checkpoint report for executed evidence and limitations.

Checkpoint 3 adds `spatial_scope_contract_test.py`, `spatial_scope_scale_test.py`,
`milestone_contract_test.py`, and `plan_health_contract_test.py`. Use the MCP
container Python for imports that need `mcp`; the specialist supervisor contract
still runs on the host. The scope scale fixture exercises the actual journal,
SQLite watch service and private geometry at 400, 40,000 and 65,536 squares.

The isolated native MCP test calls `intent_checkpoint_live_test.py` to express
a journaled plan, scope and milestones through the existing managed tools. It
uses the actual native production routine for repeated units, a facility, a
Secret Project and an unavailable-project interruption. Native fixture state is
isolated and dual-gated; ordinary managed choices acknowledge reviewed notices.
The test then verifies runtime attention delivery, checkpoint recovery and
discarding old-timeline watches. Its response hook simulates the trusted
provider-response boundary; it does not claim model inference. Consult the
checkpoint acceptance ledger for which runs have passed.

Checkpoint 4 adds `counterfactual_contract_test.py` and
`counterfactual_choice_contract_test.py`. Run both with the MCP container Python:

```sh
docker run --rm --entrypoint /opt/smacx/mcp-venv/bin/python \
  -v "$PWD:/workspace:ro" -w /workspace -e PYTHONPATH=/workspace/src \
  smacx-agent-control:dev scripts/counterfactual_contract_test.py
docker run --rm --entrypoint /opt/smacx/mcp-venv/bin/python \
  -v "$PWD:/workspace:ro" -w /workspace -e PYTHONPATH=/workspace/src \
  smacx-agent-control:dev scripts/counterfactual_choice_contract_test.py
```

These prove attainable distinct-worker outputs against exhaustive enumeration,
stale-input qualification, routing composition, total response budgets and
opaque preview scope/revision/consumption rules. They do not prove agreement
with native economic outcomes. The isolated native MCP campaign additionally
calls `counterfactual_checkpoint_live_test.py` to compare predictions with
managed actions and observed effects. Controlled production timing invokes
real native production upkeep without adding minerals; it is not a full
campaign-turn simulation. Native read-safety probes compare state before and
after hypotheses and test hidden-mirror/foreign-worker independence. All such
native inputs and probes are restricted to isolated dual-gated acceptance
installations. The [checkpoint-4 ledger](benchmarks/2026-09-05-counterfactual.md)
distinguishes passing comparisons from outstanding acceptance.

## Final sovereign integration

`managed_runtime_readiness_test.py` blocks the first observation and verifies
that runtime readiness is not advertised until it finishes. The two-seat
`control_lan_live_test.py` additionally exercises a staged Social Engineering
choice, native pending-model/charge replication, and selected-policy recovery.
`platform_controller_test.py` checks that geographic speech stays untrusted
text with no structured map payload; this does not certify stock Pact map
exchange or refresh behavior.

Run the single-player and LAN scripts with distinct test-created installations
to exercise concurrent startup. Run the heavy collector benchmark separately
when recording its dedicated latency gate, preserving failures under contention
as separate evidence. Do not raise the 30-second collection or 500 ms probe
thresholds to hide a failure. Checkpoint evidence records the workload and any
remaining limitations.

`observation_batch_recovery_test.py` injects failures before, during and after
batched observation-cache commits. Canonical journal events precede the cache
transaction; frozen-publication replay reconstructs cache rows and preserves
exactly-once events. `observation_collector_benchmark.py` prints measured case
values in latency assertion failures without relaxing its existing gates.

## Focused semantic consumer acceptance

The [PR #48 correction ledger](benchmarks/2026-09-05-peer-review-corrections.md)
lists the exact 30 scripts executed and their evidence classes. New focused
contracts cover direct physical watches, local theater membership, managed
route/cache/scope/watch/area chains, paginated omitted references, event-time
episodes, native-backed receipt lifetime, threshold transitions and journal plan
reactivation. `plan_dependency_publication_test.py` injects a failure after the
positive transition is durable but before publication acknowledgement.

Run `active_scope_benchmark.py` and `active_scope_collector_benchmark.py` in the
MCP container. Set `SMACX_SCOPE_BENCH_WIDTH=320` and
`SMACX_SCOPE_BENCH_HEIGHT=160` for 25,600 squares; defaults are 6,400. Both use
nine simultaneous scopes and four watches. Their independent probe is a
production-pipeline responsiveness check, not a measurement of a running game
UI. `native_event_time_contract_test.py` compiles the production event adapter on
the host with controlled native-shaped inputs; also cross-build the bridge.
The unchanged full collector gates and failed timing evidence remain explicit.

## Final hostile-review transaction and lifecycle gates

`publication_transaction_test.py` exercises candidate-N birth/garrison/field/
scope effects and ten crash boundaries with both unchanged and reversed native
state. It reconstructs service instances, including the journal and SQLite store,
and interrupts acknowledgement after the durable stage write. The frozen N
transaction must finish before native N+1 is drained.

`transient_episode_publication_test.py` covers entirely between-snapshot episodes,
256-event page boundaries, stage restart and post-head retry. `derived_lifecycle_test.py`
checks actual land/ocean version/split/merge changes before history refresh and
normal cold/warm route and area inspection promotion. `query_pin_consumers_test.py`
checks each pin type separately. `query_history_scaling_test.py` seeds 100/1,000/
5,000 distinct historical receipt rows and measures cleanup, watch, inspection
and runtime work; this is storage/resolution evidence, not thousands of live
provider executions.

`collector_tail_benchmark.py` prints all three repeated 25,600-square runs before
asserting unchanged 30-second/500-ms gates. Preserve the whole distribution,
including failures. `journal_idempotency_index_test.py` verifies canonical marker
loss/restart/external-writer recovery and that new keys do not reparse all prior
events. Selected journal sections remain detached copies and hash-chain checks
are unchanged. See the final hostile-review ledger for exact executions.

## H1–H5 hostile-review acceptance

Run `publication_visibility_test.py`, `cross_publication_episode_test.py`, `journal_internal_crash_test.py` and `inspection_action_lifetime_test.py` alongside the existing integrated contracts. The publication transaction matrix now has eleven boundaries × native unchanged/reversed (22 cases), including an exception injected inside canonical event-to-manifest persistence. The internal journal test also covers malformed suffixes and concurrent replay under the journal lock. Existing synthetic attention fixtures must assign evidence to an installed publication; raw cache assertions remain separate from provider-visible history assertions.

Use the container MCP Python environment for the integrated scripts. Run `native_event_time_contract_test.py` on the host for the compiled adapter. The native bridge cross-build remains a separate check. No fixture success is a running-game comparison.

For exact active-scope tail acceptance, run `active_scope_collector_benchmark.py` five times sequentially with `SMACX_SCOPE_BENCH_WIDTH=320` and `SMACX_SCOPE_BENCH_HEIGHT=160` in the same container environment. Each run uses 25,600 squares, nine scopes, four watches and an observed crossing followed by loss. It prints the complete result before asserting the unchanged 30-second/500-ms gates, including failures. Keep initial collection and scope creation timings, and do not run other heavy tests concurrently. The probe is an independent Python thread, not the native game UI thread. Preserve prior distributions; see the [H-review ledger](benchmarks/2026-09-05-h-review.md) and JSON evidence for exact results.

The installed Hermes MCP child-watcher race can be checked with `docker run --rm --entrypoint /opt/hermes/.venv/bin/python -v "$PWD:/workspace:ro" smacx-agent-harness:dev /workspace/scripts/hermes_mcp_watcher_test.py`. It verifies one RPC/watcher, cancellation and reconnect on child exit, synchronous stubs, and no orphan tasks/coroutines.

Run `scripts/mcp_argument_validation_test.py` in the container MCP environment to verify that unknown top-level arguments are rejected before dispatch and recorded, while declared nested data/defaults still work. The specialist provider capture test additionally exercises rejection and recovery through actual Hermes and the frozen MCP instruments.

`PYTHONPATH=src python3 scripts/ecology_attention_contract_test.py` replays inactive sunspot ticks, active countdown, start/end and other ecology changes through the collector and verifies both retained world state and critical-attention selection.

The Hermes provider capture test also emits a truncated (`length`) response and enforces exclusive runtime episode ownership. It requires cancellation before the real Hermes continuation can obtain its context. Runtime context tests assert the current native session and action revision remain available for guarded cognition writes. See `benchmarks/gameplay-episode-lifecycle.json` for the live trigger and validation limits.

For an authorized stopped-campaign deployment, authenticated `POST /api/v1/matches/{match_id}/recover` with `{"refresh_runtime":true}` selects the current prepared worker image and restores the verified native/AI checkpoint. Ordinary recovery retains the pinned image. The HTTP contract requires literal boolean true; strings do not opt in. Inspect the running worker/DLL and returned semantics after deployment rather than assuming an image tag update changed a pinned campaign.

Direct-schema acceptance: `hermes_provider_capture_test.py` requires exactly 15 gameplay and 9 communication schemas on initial/resumed requests and executes a direct MCP call through its negative guard response. Run `harness_context_policy_test.py` in the built Hermes image with `SMACX_TEST_DIRECT_SERVER=smacx`, `smacx_communication`, and unset for retained legacy wrappers. All three must preserve typed receipts, request-only context, namespace isolation and bounded million-token GC. Direct mode explicitly disables MCP resource/prompt helpers.

Native-source changes also require an explicit doctrine compatibility review. Inspect the complete diff from the prior registered build, validate it, then use `scripts/doctrine_engine_contract.py --register-reviewed`; the ordinary invocation checks that source and registration match. This is never an automatic runtime approval. Recompilation must continue rejecting unknown builds and unsupported rule overrides.

### Semantic preflight and resume checkpoint

The stopped turn-21 resume reported raw ~198,817-token preflight compression despite send-time semantic GC. Pinned Hermes preflight reads durable history before the sanitizer; its small-context floor also raised the configured 50% ratio to 75%. The managed adapter now estimates its copy-only semantic projection plus tool schemas and bounded runtime reserve without fetching runtime state or leasing attention. Profiles supply the supported absolute threshold cap. Irreducible durable history still fails closed.

The real receiving-provider Hermes test resumes SQLite containing a deliberately oversized old tool result: exactly one resumed provider request, no summarization, original durable result preserved, disposable marker absent from wire. Existing direct-schema, runtime lease, generation and prompt-integrity cases pass. Installed-Hermes context tests cover no runtime fetch, input immutability, irreducible failure and effective 64K/256K caps. Live restore/deployment remains pending; the verified turn-21 checkpoint predates the unwanted compaction. See `docs/benchmarks/gameplay-semantic-preflight.json`.

Deployed preflight acceptance: restored verified turn 21 and matching AI history into `timeline-restore-07b030faf15046d19cbd2ec3`; new run `run-548df7b0ff4248d09b0d0ebebca27001` has effective profile cap 131,072. Actual semantic preflight projected 412 durable rows to 62, estimated 55,150 including 32,768 runtime reserve, and did not fetch runtime context. Provider request `4d21ba0908a84f2885a0968ff95f21db` followed without the previous generic summarization. Portal unpaused; sustained gameplay acceptance continues.

### Native collection transition race checkpoint

Live turn-21 Auto Explore executed successfully, then request-time collection rejected mixed native revisions (`world_changed_during_collection`, event `ac98b9f1bf804b8499884edea446edca`). The snapshot guard is correct; treating this first transient rejection as a fatal Hermes context failure is brittle. The private handler now retries that exact condition at most three times, before runtime services or attention acquisition. No mixed snapshot is returned; other failures and exhausted retries still return 409. Deferred attempts remain visible in diagnostics and human traces.

Actual HTTP handler tests verify two races followed by one context/lease, exhausted races with none, and immediate unrelated-error rejection. Collector crash-stage recovery and cold-readiness contracts pass. Native+AI checkpoint `checkpoint-4108d3755f6744e892a792fbdcb4c672` verifies turn 22 after the successful order. Live repaired retry remains pending, separately tracked in `benchmarks/gameplay-runtime-collection-retry.json`.

### Recent diagnostics database window checkpoint

The live unordered telemetry limit followed the category index: its 10,000 rows contained attention/collector data and none of the 2,730 runtime-context records. Capped state exports now explicitly select most-recent timestamps with deterministic ties and advertise retained counts/order/timestamp bounds. Older omitted records still produce a row-limit gap; raw stream capture policy is unchanged. Regression covers adversarial category/insertion ordering and a newer foreign-match row. A read-only export against the live database produces a valid ZIP with 10,000 recent records, including 975 runtime-context entries; this proves backend selection, not browser download. Evidence: `benchmarks/gameplay-diagnostics-recent-window.json`.

Collection-race repair deployment: restored turn 22 into `timeline-restore-21dddfdc676747d789a229c9`; sovereign resumed and queried both production and citizens through managed choices. No repeated live revision-race has yet exercised the new retry, so that proof remains controlled-handler coverage.

### Production quote delivery checkpoint

The sovereign switched to Colony Pod at turn 22, then repeated production queries because hurry disappeared. Native already supplied current production and hurry metadata at catalog top level, but the managed frame omitted it. An operator native read verifies Colony Pod 30 minerals, 2 accumulated, +2 surplus; hurry legal but unaffordable at 95 credits versus 58 available. Managed production frames now retain an allowlisted bounded `production_context` with current/queue/hurry facts; missing values remain absent, and no action or legality changes. Native entity slot IDs in execution receipts are removed through the existing semantic translator after execution/journaling, preserving effect fields and opaque choice linkage.

Managed entry-point adapter, opaque execution, staged paths, failure circuit and semantic binding tests pass. Native quote verification is separate from the adapter fixture; actual provider delivery remains pending. Verified native+AI turn-22 checkpoint `checkpoint-0d35c5d505a34342b5a31dd1fede56ba` retains the production switch. Evidence: `benchmarks/gameplay-production-context-delivery.json`.

Live acceptance through turn 23: corrected end-turn receipt and sovereign handoff observed. Actual request `dc431aed58ee4f00a7e938c921d27ffa` contains three Scouts, two Auto Explore/one none, Colony Pod production, zero missing/noncurrent force fields, and explicit overlapping-capability/home-support qualifiers. Deployed authenticated backend archive `...8b8a64553b74474c8a79f42f77ae81cc.zip` is 15,225,068 bytes, ZIP-valid, with 897 recent runtime-context telemetry records in the bounded window. The first startup export returned HTTP409 without retained response body; cause is unknown, and the next request succeeded. Browser delivery is still unverified. See recent-window and force-summary live evidence files.

### Diagnostic export error capture checkpoint

The first live export attempt returned409, but authenticated GET artifact creation was outside the existing lifecycle error-capture scope. The endpoint now enables the same safe operation/code capture after authentication; no raw exception text, credentials or anonymous probes enter the archive. Actual HTTP regression verifies anonymous401/no new record and authenticated409/one scoped record. The original startup409 cause remains unknown; subsequent export succeeded. See `benchmarks/gameplay-export-error-capture.json`. This control-API-only change does not require native/MCP replacement.

Live research acceptance: delivered owned technology count is 2 at turn24 (request `63dd9ddad64949198117772ac67f0634`) and 3 at25 (`abf6f0a23a414d13b72264d299724745`), with Social Psych present. The sovereign’s acquired-Build-tech statement is supported; it is not diagnosed from counters alone. Request `f46e2491892f4d9289336d8a1f5b4d0a` explicitly contains selected Build preference, category/target distinction, and hidden next target with null name/ID. Authenticated export-error capture is deployed on healthy control API; native/MCP were not restarted for that endpoint-only patch. Evidence files retain controlled versus live distinctions.
