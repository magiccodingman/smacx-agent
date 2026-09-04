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
PYTHONPATH=src python3 scripts/global_world_pipeline_test.py
PYTHONPATH=src python3 scripts/movement_mechanics_contract_test.py
PYTHONPATH=src python3 scripts/runtime_context_contract_test.py
PYTHONPATH=src python3 scripts/notebook_scale_test.py
PYTHONPATH=src python3 scripts/attention_communication_contract_test.py
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
usage. Live scripts print only content-free usage/cache/latency aggregates.
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
larger than the native ring. It also rejects action revisions coupled to native
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
remaining movement. It routes own, Pact, truce, hostile and epistemically
unknown subjects under subject-relative access. The amphibious benchmark
separately enforces bounded coast/port Pareto frontiers, transport ownership and
diplomatic access, capacity/boarding legality, and conditional opposed landing
on a 4,096-square custom world.

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
