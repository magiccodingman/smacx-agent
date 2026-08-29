# SMACX Agent

SMACX Agent lets a language model play **Sid Meier's Alpha Centauri: Alien Crossfire** through structured MCP tools while the normal game window remains visible. It uses a Thinker-derived in-process bridge for fair-play state and game-thread actions, plus a persistent local MCP service for Hermes.

This installation is isolated from the Steam game directory. The working game copy and Proton prefix live under `runtime/`; the original Steam files are not modified.

## What works now

- Launch and stop the isolated visible spectator game.
- Start a configured random single-player match through the game's native setup path, without menu input.
- Host a native DirectPlay LAN session, discover and join one exact session by opaque ID, synchronize the guarded `small_easy` lobby profile, ready the client, and start the match semantically.
- Read fair-play own-faction economy, per-counterpart loan balances/payments, detailed base yields/citizens/facilities/queues, units, visible enemy units, and known/visible tiles through object IDs and opaque match-local `tile_id` references.
- Enumerate and execute typed research, energy allocation, Social Engineering, production/rushing/queues, base renaming/governors (including all named advanced production permissions)/workers/specialists, confirmation-gated facility recycling, native nerve stapling, and two-stage native base obliteration, adjacent movement, native automated exploration, On Alert, Air Defense, typed Former automation, persistent safe-round-trip bombing runs against visible Vendetta bases, defender designation, coordinate-free land/sea return-to-base, guarded all-triad routing to a known owned `base_id`, standalone-airbase recovery, safe-round-trip aircraft routing, object-targeted carrier recovery with deck reservations/holds/boarding/refueling, go-to/patrol/Road To persistent orders, activation, skip/hold/sentry, confirmation-gated skip-all-ready turn completion, exact single-vehicle upgrades, native probe missions including fair staged targeted sabotage, Psi Gate transfers, transport boarding/disembarkation, air drops, artillery bombardment, confirmation-gated reactor self-destruct with fair blast context, confirmation-gated terrain-improvement destruction, conventional/tectonic/fungal/Planet Buster missile launches, support rehoming, Pact-territory unit transfers, resource convoys, confirmation-gated disbanding, rule-explained base-founding, and terraforming.
- Open contacted AI diplomacy through the native engine; handle greetings, exact-amount native energy gifts, technology/map exchanges, guarded technology purchases/sales, exact one-to-four-technology and energy demands, native energy/reciprocal-technology demand counteroffers and their follow-ups, borrowing/lending offers, joint-Vendetta requests and exact native energy/one-to-four-technology counteroffers, treaties/truces/Pacts, territorial-withdrawal demands, treaty/truce/Vendetta confirmations, combat odds, reduced-strength assault decisions, and the conventional-versus-Nerve-Gas combat decision semantically. In LAN, a local AI `COMM`/`COMMDIPLO` channel can now be accepted, its greeting and passive relationship notice continued, a displayed technology trade/demand or relationship offer safely rejected, and the native conversation finished while the peer remains nonmodal.
- Open a paired human LAN diplomacy channel; atomically compose and commit prerequisite-guarded Treaty, Pact, or Blood Truce offers, exact owned-technology transfers, or a bounded caller-selected amount of owned energy credits; let the peer inspect the complete synchronized structure and accept or decline; resolve recipient technology presentation semantically; and enforce a post-transmission engine-settlement phase before later actions. Native two-way LAN chat is also semantic.
- Convene the Planetary Council or respond when another faction calls it, enumerate native rule-filtered proposals/ballots, negotiate outbound vote commitments, accept or reject confirmation-gated incoming energy/two-technology Governor-vote offers, watch the visible Council chamber resolve, and read the public result semantically.
- Use the Unit Workshop semantically: inspect unlocked component catalogs and owned designs, create native prototypes, confirmation-gate safe retirement, quote/confirm whole-prototype upgrades, and select exact per-unit upgrades from a ready vehicle's native-filtered choices.
- Save into match-scoped named slots and reload with the same durable match identity and a fresh process session.
- Handle opening setup, first-base naming, structured base-status and production-completion notices, support warnings, monolith choices, semantic Unit Workshop handoff/deferral, probe incidents, and incoming-contact prompts semantically.
- Traverse the production victory presentation stack semantically—native victory interludes, credits, score report, Quayle rating, Hall of Fame, and replay—then explicitly finish or continue from the final-score decision and expose the native process-exit state.
- Resolve the native Alien Artifact menu semantically: keep the Artifact, confirmation-gated linking for a technology, or confirmation-gated contribution to the exact active Secret Project/unprototyped unit.
- Assign durable installation, match, agent, perspective, instance, and process-session identities. SQLite now owns immutable events, chat, versioned facts/beliefs/relationships/commitments/goals/summaries, correction history, and scoped FTS5/BM25 recall; the former JSON ledger is a compatibility mirror/import source.
- Run an authenticated, always-on Control Center that discovers OpenAI-compatible providers, validates a legal game source without modifying it, imports a private checksummed Proton runtime, provisions isolated solo workers, and parks/resumes the same durable match with a fresh process session.
- Attach one dedicated semantic MCP sidecar to each running worker and one isolated Hermes profile to each durable agent. Managed MCP cannot launch, load, or stop the game; lifecycle remains operator-owned. The Control Center resolves the exact match/seat/worker/sidecar/model binding and the host adapter starts Hermes with low reasoning plus only `smacx` and optional web tools.
- Bundle the mandatory next decision in `smac_decision`: one stable match/session/revision frame containing an active modal, selected ready unit, wait/gap directive, or end-turn/game-management choices. Compact detail is the default; a full snapshot is opt-in for occasional strategic analysis.
- Treat every returned `revision` as a single-decision capability: choices from an old revision, match, session, object owner, or turn phase are rejected, even if later actions restore identical game state. `end_turn` is withheld until all ready-unit decisions are resolved.
- Reject commands from the wrong match/session or a stale observation before mutating game state.
- Reject every non-interaction mutation at the bridge boundary while a popup, endgame presentation, research/Council decision, engine handoff, or another faction's turn is active, even if the client fabricates the command with a fresh guard.
- Serialize popup-to-engine transitions by exact native window identity, rejecting duplicate submission while allowing a following modal (such as the Council chamber) to become a fresh semantic state.
- Expose `end_turn` only after every ready unit has a semantic decision or persistent order, reject a fabricated early end-turn command, or let the model deliberately copy one exact-revision `skip_all_ready_units` tuple that lists and spends every remaining unit. The batch is confirmation-gated, cannot contain arbitrary subcommands, and may let native auto-end preferences begin the next turn immediately. Native `REALLYOVER` remains semantic and duplicate commands are suppressed while a confirmed transition finishes.
- Assign deferred native actions an `action_id`; movement, combat, and diplomacy entry expose pending/completed/rejected status, and MCP waits for a definitive engine result instead of treating a queued window message as success.
- Stop and record a structured capability request when a semantic interaction is not implemented.

The MCP deliberately exposes no screenshot, mouse, keyboard, native map-coordinate, coordinate-click, or arbitrary UI fallback. Map observations and actions use opaque match-local `tile_id` references; the bridge resolves native coordinates internally. Optional external victory movies when `narrative_ui=true`, human map/joint-attack bargaining, uncommon scenario interactions, and some specialized unit abilities remain incomplete. Autonomous launches disable those external movies by default. See [Coverage and limits](docs/coverage.md).

`tile_id` values are identifiers, not coordinates. Agents must obtain them from fresh observations or choices and must not calculate with or invent them. Both the MCP schema and the authenticated native bridge reject x/y-driven play.

## Start using it

For the new containerized operator flow, run:

```bash
./scripts/control-center-up.sh
docker compose exec control-center smacx-control bootstrap-token
```

Then open `http://127.0.0.1:8080`. See [Control Center](docs/control-center.md)
for legal game/Proton registration, security, LAN publication, and worker
lifecycle details. After starting a worker, use **Bind Hermes to a running
match**. It validates the complete binding and generates one command that
prompts for the administrator password, creates the agent's isolated Hermes
profile, and starts or resumes a conversation named for that match. Your
existing Hermes dashboard does not need to be restarted. The legacy
single-instance MCP service below remains available and is not modified by the
managed flow.

The MCP service is enabled as a user service and normally starts automatically:

```bash
systemctl --user status smacx-agent-mcp.service
hermes mcp test smacx
```

Your existing Hermes configuration already contains:

```text
smacx -> http://127.0.0.1:47814/mcp
```

Start a **new Hermes conversation** so its tool list is refreshed. From the CLI:

```bash
cd /path/to/smacx-agent
hermes
```

Good first prompts:

```text
Play a new Quick Start game of Alien Crossfire. Use the SMACX tools, explain major strategic decisions briefly, and keep playing turn by turn. Never use hidden information.
```

```text
Play Alien Crossfire using only the SMACX semantic tools. If a required observation or action is missing, report one capability gap and stop; never click, type into, or inspect screenshots of the game window. Reporting a gap latches the current MCP process: commands, launch, new-game, and load operations are refused until development is complete, the developer restarts MCP, and play starts in a fresh native session.
```

You do **not** have to start a supported random single-player match manually; `smac_new_game` performs the setup. For LAN, `smac_lan` can host, discover/join, apply the currently validated `small_easy` profile, ready, and start through the native lobby. Other menu profiles and unsupported LAN choices require future semantic coverage—they are never silently delegated to coordinate clicks.

See [Safe semantic play loop](docs/agent-loop.md) for the primary decision-frame loop and the lower-level snapshot/choice/guarded-command order.

## Service and maintenance commands

```bash
# MCP status
./scripts/status.sh

# Restart after changing Python MCP code
systemctl --user restart smacx-agent-mcp.service

# Stop/start the MCP service
systemctl --user stop smacx-agent-mcp.service
systemctl --user start smacx-agent-mcp.service

# Rebuild and deploy the Windows bridge
./scripts/build_bridge.sh

# Reinstall native DirectPlay into only this project's Proton prefix
./scripts/install_directplay.sh

# Recreate/refresh the persistent user service
./scripts/install_mcp_service.sh
```

The MCP service listens only on `127.0.0.1:47814`. The in-game bridge listens only on `127.0.0.1:47813` and requires a random token stored with mode `0600` in `runtime/agent-token`.

## Layout

- `bridge/` — Thinker-derived 32-bit Windows DLL and the fair-play socket bridge.
- `worker/` — isolated non-root Linux game-worker image and runtime contract.
- `control_center/` — authenticated always-on operator service and web UI.
- `src/smacx_controller.py` — Proton launcher, bridge client, match identity, and save/load lifecycle.
- `src/smacx_mcp.py` — persistent Streamable-HTTP MCP server.
- `scripts/` — build, DirectPlay setup, and service helpers.
- `systemd/` — reproducible persistent MCP user-service definition.
- `docs/` — architecture, tool protocol, coverage, and troubleshooting.
- `runtime/` — ignored local game copy, Proton prefix, token, logs, and screenshots.

See [Architecture](docs/architecture.md), [Platform roadmap](docs/platform-roadmap.md), [Control Center](docs/control-center.md), [identity and memory ADR](docs/adr/0001-identities-and-authoritative-memory.md), [control-plane ADR](docs/adr/0002-control-plane-and-runtime-boundary.md), [optional Graphiti projection](docs/graphiti.md), [Tool reference](docs/tools.md), [Testing](docs/testing.md), and [Troubleshooting](docs/troubleshooting.md).

## Provenance

SMACX Agent is licensed under Apache License 2.0; see [LICENSE](LICENSE). The bridge is a modified build of [Thinker](https://github.com/induktio/thinker) at commit `4aef5be73bda4eb22ffa8db424eb91780c4a51fa`, whose upstream code is MIT-licensed. The game assets and Microsoft DirectPlay redistributable are local runtime dependencies and are not part of this project's source distribution. See [NOTICE.md](NOTICE.md) for provenance and attribution.
