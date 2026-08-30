# Testing and evidence

Tests are layered so ordinary contributors can verify contracts without a game
installation while native/stream/model tests remain explicit and
installation-owned.

## Fast pre-commit checks

```bash
dotnet restore Smacx.Agent.slnx
dotnet test Smacx.Agent.slnx --no-restore

python3 -m compileall -q src worker scripts
PYTHONPATH=src python3 scripts/control_plane_test.py
PYTHONPATH=src python3 scripts/control_http_test.py
PYTHONPATH=src python3 scripts/worker_contract_test.py
PYTHONPATH=src python3 scripts/hermes_adapter_test.py
PYTHONPATH=src python3 scripts/harness_manager_contract_test.py
PYTHONPATH=src python3 scripts/strict_prompt_contract_test.py
PYTHONPATH=src python3 scripts/operations_contract_test.py
PYTHONPATH=src python3 scripts/platform_store_test.py
PYTHONPATH=src python3 scripts/platform_controller_test.py
PYTHONPATH=src python3 scripts/external_lan_contract_test.py
PYTHONPATH=src python3 scripts/human_hosted_lan_contract_test.py
PYTHONPATH=src python3 scripts/virtual_lan_contract_test.py
PYTHONPATH=src python3 scripts/graphiti_projection_test.py
PYTHONPATH=src python3 scripts/graphiti_worker_contract_test.py
PYTHONPATH=src python3 scripts/reference_corpus_test.py
```

The .NET flow creates a new canonical SQLite database, bootstraps the admin,
creates/claims an invited account, creates a lobby, sends private chat, reads
native join metadata, verifies activity/analytics and token dimensions, and
asserts that no `__EFMigrationsHistory` table exists.

Python contracts cover authentication/CSRF, service-token separation, secret
redaction, Docker ownership, worker import safety, match/perspective scope,
native-progress mirroring, managed Hermes secret/tool boundaries, lifecycle
reconciliation, operations, LAN validation, Graphiti cursor isolation, and the
reference corpus.

The MCP package is intentionally isolated in the control image. Run briefing
and decision contracts through that environment:

```bash
docker run --rm --network none \
  --entrypoint /opt/smacx/mcp-venv/bin/python \
  -e PYTHONPATH=/repo/src -v "$PWD:/repo:ro" \
  smacx-agent-control:dev /repo/scripts/match_briefing_contract_test.py
```

## Knowledge and copyright guard

The distributable corpus test verifies 52 unique original documents, hierarchy,
exact entity lookup, metadata/content hashes, fixed archive fallbacks, guide
exclusions, and BM25 retrieval:

```bash
PYTHONPATH=src python3 scripts/reference_corpus_test.py
PYTHONPATH=src python3 scripts/knowledge_reference_guard_test.py
```

Private extraction runs against an operator-owned installation:

```bash
PYTHONPATH=src python3 scripts/private_reference_test.py \
  --game-source /absolute/path/to/legal/game
```

On the reference installation it produced 672 private documents from 18
allowlisted sources, including 87 technologies and all 14 faction headers, and
excluded guides/scenarios/tutorial narrative.

The copyright regression compares normalized sequences without emitting source
passages:

```bash
python3 scripts/reference_copyright_audit.py \
  --source /absolute/path/to/legal/game/Manual.pdf \
  --source /absolute/path/to/legal/game/helpx.txt \
  --source /absolute/path/to/legal/game/alphax.txt
```

The reference run found no shipped sequence of eight or more normalized source
words. Source files and extracted chunks are never copied into the repository
or image.

## Build and container integration

Use the normal serialized launcher:

```bash
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
  ./scripts/control-center-up.sh
docker compose ps
curl --fail http://127.0.0.1:8080/healthz
```

Do not start three image builds in parallel on a small VM. An observed build on
8 GiB RAM/1 GiB swap exhausted memory while native, worker, and Blazor publish
were concurrent. `control-center-up.sh` now sets `COMPOSE_PARALLEL_LIMIT=1` and
builds services sequentially. The continuation run passed with 16 GiB RAM and
16 GiB swap.

Inspect dependency vulnerabilities:

```bash
dotnet list Smacx.Agent.slnx package --vulnerable --include-transitive
```

Container images are digest-pinned where upstream stability matters. Also
inspect the generated SBOM/package lists and run the scanner available in the
target CI/registry; absence of one local scanner is not evidence of no
vulnerabilities.

The strict provider boundary has two levels of proof:

```bash
docker build -f harness/Dockerfile -t smacx-agent-harness:dev .
PYTHONPATH=src python3 scripts/hermes_provider_capture_test.py
```

The capture test runs the real derived Hermes image against a local
OpenAI-compatible recorder and asserts one exact SMACX-owned system message,
with no Hermes scaffold or workspace rule content.

## Browser portal test

Run the browser portal at desktop and mobile widths and verify:

1. first-run/login and responsive navigation;
2. runtime/provider/profile administration;
3. waiting lobby and every typed setup group;
4. human browser seat launch and stream reconnect;
5. game mouse, keyboard shortcut, text input, audio and fullscreen;
6. administrator cross-seat view;
7. observer deck switching and read-only transport;
8. anonymous spectator allowed/denied policy without a session;
9. global/private/consent-group chat, authorization, logical delivery, and
   player/faction label;
10. root-MENU-only human control rail and its disappearance for native
    submenus/modals;
11. connected-player voting, stable-boundary maintenance curtain, checkpoint,
    park, profile change, delegation/reclaim, and recover UI/state;
12. turn/year/faction activity feed; and
13. analytics, native classified/unknown outcomes, CSV, and constrained SQL report.

The 2026-08-29 Chrome run rendered the actual game through Selkies, accepted
browser input, kept observer paths read-only, and verified the desktop/mobile
portal layout. The portal routes use WebAssembly interactivity without
prerender for authenticated cookie consistency; SignalR remains available for
presence/lobby events.

The managed-play run measured a true native 800×600 game at 1280×720,
1024×768, 390×844, and 844×390 browser viewports. Local scaling preserved the
4:3 game aspect ratio, letterboxed the unused area, centered the frame, and
produced no horizontal or vertical page overflow. Phone portrait intentionally
prioritizes a complete view; landscape/fullscreen makes the original text much
more usable.

The same human-only match passed native 800×600 checkpoint → park → profile
update → recovery in about eight seconds on the reference VM. X11 then reported
an exact 1920×1080 game window after a second stable recovery. An unexpected
game-process exit was also observed: the supervised worker restarted from the
verified checkpoint and the already-open browser route resumed its stream.
The worker forces manual stream resolution and matching Thinker custom-window
dimensions so the initial viewer cannot resize or clip the shared game desktop.

The .NET suite exercises connected-human eligibility, solo approval, one-peer
majority, resolution cooldown/waiver, the complete validated profile catalog,
canonical schema creation, and browser API error handling. The platform-store
suite exercises consent-group logical delivery, per-recipient status,
deduplication, and perspective isolation.

A follow-up first-run UX run used a disposable portal database against the real
control catalog. It verified the no-profile warning and disabled launch guard,
the two-step endpoint/profile onboarding path, automatic selection when only
one game source/runtime exists, policy help text, and the save-versus-launch
explanation. Drawer geometry was checked directly: closed is fully off-canvas,
open is flush at the left edge, and route navigation closes it. The disposable
test account, container, and volume were removed afterward.

## Managed worker and semantic AI

The complete worker/MCP/Hermes vertical slice:

```bash
SMACX_TEST_GAME_SOURCE=/absolute/path/to/legal/game \
SMACX_TEST_PROTON_SOURCE=/absolute/path/to/proton \
SMACX_TEST_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
SMACX_TEST_PROVIDER_URL=http://model-host:8000/v1 \
SMACX_TEST_PROVIDER_MODEL=Qwen3.8-27B \
PYTHONPATH=src python3 scripts/control_worker_mcp_live_test.py
```

It provisions a real game worker, MCP sidecar, and SMACX-derived Hermes image,
checks exact identities/secret isolation/toolsets, requires a native semantic
revision advance, and verifies a recovery set including the Hermes
conversation.

The portal-driven certification went further: Qwen3.8-27B at low reasoning
played a Tiny/Citizen Alien Crossfire game through turn 13/year 2113 with only
`smacx`. Logs and native bridge state proved semantic snapshot/decision/
command, rules retrieval, unit movement, goal/fact memory, and stale-revision
re-observation. No screenshot, computer, mouse, keyboard, terminal, or raw UI
tool was available. The live match then passed stop-agent → checkpoint → park.

Hermes telemetry was independently read from its private state through the
same no-network/read-only helper used by production:

```json
{
  "sessions": 1,
  "api_calls": 97,
  "input_tokens": 5785165,
  "output_tokens": 38224,
  "cache_read_tokens": 0,
  "cache_write_tokens": 0,
  "reasoning_tokens": 21743
}
```

## Managed LAN tests

Two managed real game workers:

```bash
SMACX_TEST_GAME_SOURCE=/absolute/path/to/legal/game \
SMACX_TEST_PROTON_SOURCE=/absolute/path/to/proton \
SMACX_TEST_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
PYTHONPATH=src python3 scripts/control_lan_live_test.py
```

This proves native host/discover/join/configure/ready/start, shared match with
distinct sessions/perspectives/factions, one MCP per seat, host-only save,
complete park, stock multiplayer reload, exact faction restoration, and second
entry into gameplay.

Mixed independent native “human” process:

```bash
SMACX_TEST_GAME_SOURCE=/absolute/path/to/legal/game \
SMACX_TEST_PROTON_SOURCE=/absolute/path/to/proton \
SMACX_TEST_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
scripts/mixed-lan-live-test.sh
```

It proves player-name/faction attribution, disconnect/rejoin, saved faction
reclaim, and post-resume chat. The fixture has no seat perspective, MCP, or
agent identity.

External human-host authority:

```bash
SMACX_TEST_GAME_SOURCE=/absolute/path/to/legal/game \
SMACX_TEST_PROTON_SOURCE=/absolute/path/to/proton \
SMACX_TEST_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
scripts/human-hosted-lan-live-test.sh
```

The independent process exclusively owns Host/Configure/Start/Save/Load while
managed clients discover/join/ready/observe. It passed fresh and loaded lobbies,
bidirectional chat, managed reconnect, and exact faction restoration.

Additional typed matrices:

```bash
PYTHONPATH=src python3 scripts/lan_profile_contract_test.py
PYTHONPATH=src python3 scripts/custom_lan_live_test.py
PYTHONPATH=src python3 scripts/lan_scenario_live_test.py
```

These local multi-process tests are strong implementation evidence but are not
the deferred physical two-computer certification.

## Native gameplay regressions

Most focused tests use the nested display wrapper and an operator-provided installation:

```bash
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/TEST_NAME.py
```

The suite includes opening interactions, decision frames, ready-unit guards,
movement/orders/auto-explore/transports/air/combat/probes, base management,
production/facilities/citizens, unit design/upgrade, social engineering,
diplomacy/technology/energy/loan/joint attack, Council, atrocities, endgame,
save/load, and capability-gap latch. See [coverage.md](coverage.md) for exact
test-to-capability mapping.

Every mutating native regression must verify wrong identity, stale revision,
ownership/visibility, confirmation where consequential, and no mutation on
rejection. Managed AI/LAN tests report `pixels_or_ui_input_used=false`.

## Recovery and backup

```bash
PYTHONPATH=src python3 scripts/operations_contract_test.py
PYTHONPATH=src python3 scripts/harness_backup_live_test.py
```

Native crash recovery is accepted only at a bridge-verified checkpoint. Backup
verification checks SQLite integrity, manifests, hashes, secret archive policy,
and worker/Hermes volume archives. Test-owned resources are selected by exact
installation labels and cleaned afterward.

The portal park-race regression is specifically:

1. let a real managed Qwen run issue semantic actions;
2. click **Park** while the match is active;
3. prove the Hermes run reaches stopped before checkpoint;
4. prove checkpoint and park return HTTP 200;
5. prove all dynamic Hermes/MCP/worker containers are absent; and
6. prove the portal displays `parked`, own faction, and last turn/year.

## Graphiti

```bash
PYTHONPATH=src python3 scripts/graphiti_projection_test.py
PYTHONPATH=src python3 scripts/graphiti_worker_contract_test.py
./scripts/graphiti-up.sh
```

Contract tests cover namespace isolation, cursor idempotency, rebuild, and
fail-open-to-SQLite behavior. A backend-live test verifies Neo4j/Graphiti only
when compatible chat and embedding endpoints are configured.

## External certification checklist

Not run by this Linux-local milestone:

- physical Linux host plus second physical native client;
- actual remote Tailscale peer across networks;
- Windows 11/WSL2 Docker Desktop deployment.

When performed, record exact commits/images, interfaces, firewall, game/runtime
hashes, provider/profile, participant names/factions, save/rejoin result, and
whether any pixels/input path was used in
`docs/certification-record.example.md`.
