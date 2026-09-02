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
PYTHONPATH=src python3 scripts/opaque_choice_execution_test.py
PYTHONPATH=src python3 scripts/semantic_progress_contract_test.py
```

The provider-wire context policy must run inside the built Hermes image because
it deliberately tests the pinned harness's private message-construction hooks:

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

## Native game integration

Native semantic tests require the user's installed game, the built bridge, and
the prepared Wine/Proton environment. Relevant entry points include:

```bash
./scripts/build_bridge.sh
PYTHONPATH=src python3 scripts/native_automation_turn_test.py
PYTHONPATH=src python3 scripts/save_load_test.py
PYTHONPATH=src python3 scripts/full_endgame_pipeline_test.py
```

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
