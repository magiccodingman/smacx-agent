# SMACX Agent

**A fair-play, persistent AI player for Sid Meier's Alpha Centauri: Alien
Crossfire—running inside the real game, remembering the politics, and playing
without screenshots or mouse automation.**

SMACX Agent gives an LLM structured observations and guarded actions through
MCP while the ordinary Alien Crossfire window remains visible to spectators.
The model can build, explore, research, negotiate, chat, remember promises,
form grudges, host LAN matches, save campaigns, and return later with the same
match identity.

This is not a replacement rules engine and it is not a stronger stock bot. The
goal is an AI **player**: one that can become an ally, rival, opportunist, or
long-term political character whose decisions are grounded in what it has
actually observed.

> Linux-first. Bring your own legal Alien Crossfire installation. Game assets,
> Proton, and Microsoft redistributables are never shipped by this repository.

## Why this is different

- **It plays the real game.** A Thinker-derived DLL runs inside
  `terranx.exe`; actions execute on the native game thread and the visible game
  continues to render normally.
- **No vision tax.** The agent receives typed game state and legal choices
  instead of repeatedly interpreting pixels or guessing click coordinates.
- **Fair play is enforced below the prompt.** Fog of war, perspective,
  ownership, match/session identity, stale observations, and destructive
  confirmations are checked by the bridge and MCP—not merely requested in a
  system prompt.
- **Political memory survives context windows.** SQLite stores immutable
  events, chat, facts, beliefs, relationships, commitments, goals, and
  summaries per match, agent, and perspective. Scoped FTS5/BM25 recall is
  always available; Graphiti can optionally project that history into a
  temporal graph.
- **It can share a real LAN game.** Isolated AI seats can host, join, chat,
  negotiate, save, park, reload, reclaim their exact factions, and continue.
  Named human seats are staged through the native lobby and validated before
  the AI host may start.
- **The operator stays in control.** An authenticated Control Center owns
  game/runtime registration, model selection, worker lifecycle, match seats,
  and optional password-protected view-only spectators. Agents never receive
  Docker, launch, provider-secret, or spectator credentials.

## The experience this enables

Imagine a campaign where an AI remembers that you honored a technology deal
twenty turns ago, distrusts a faction whose explanations no longer match its
actions, asks for help in native chat, revises an expansion plan after a border
crisis, and reloads the same campaign days later with that political history
intact. Personality cards can eventually shape how each agent interprets those
events without replacing the factual record.

That is the north star: not merely an AI that can optimize Alpha Centauri, but
one that makes a multiplayer table more surprising, coherent, and fun.

## How it works

```text
authenticated operator
        |
        v
  Control Center  ---- durable identities/events/memory ----> SQLite
        |                                                    |
        | creates one isolated seat                          +--> optional Graphiti projection
        v
  Hermes or another harness <---- private MCP sidecar
        |                              |
        | typed decisions              | authenticated semantic bridge
        v                              v
  model context                  Proton + terranx.exe + Thinker-derived DLL
                                          |
                                          +--> visible, view-only spectator
```

Each agent seat has a distinct game worker, native process session, MCP
endpoint, perspective, and memory scope. SQLite is authoritative. Graphiti is a
derived, disposable projection and cannot widen what an agent is allowed to
know.

## Current status

| Area | Status |
| --- | --- |
| Fair-play single-player semantic control | Implemented; 100-turn live soaks completed without UI input |
| Native diplomacy, Council, production, units, bases, research, and endgame | Broadly implemented; exact remaining gaps are fail-closed |
| Durable chat and political/strategic memory | Implemented and contained-tested |
| Authenticated Control Center and isolated Docker workers | Implemented and live-tested on Linux |
| OpenAI-compatible provider discovery and Hermes adapter | Implemented; unkeyed local-provider path tested with Qwen/Hermes |
| Agent-only managed DirectPlay LAN | Implemented and live-tested with two real workers |
| Multiplayer save, park, stock reload, faction restoration, and continue | Implemented and live-tested |
| Named external human LAN seats | Implemented and contract-tested; physical two-machine certification remains |
| View-only noVNC spectators | Implemented and live-tested |
| Optional Graphiti projector | Core adapter/isolation/replay implemented; deployment automation and real backend evaluation remain |
| Curated Alien Crossfire wiki/RAG corpus | Not yet delivered |
| Windows/WSL2 and Internet-LAN deployment | Not yet certified |
| Every obscure scenario, menu profile, and LAN mutation | Not claimed; unsupported states stop and report a capability gap |

See [Project status](docs/project-status.md) for the exact boundary between
delivered, validated, optional/manual, and planned work. See
[Coverage and limits](docs/coverage.md) for the detailed semantic action list.

## Proof, not just architecture

The current regression suite has demonstrated:

- Qwen3.8-27B using only six semantic SMACX tools to start, play, save, and stop
  a real game with no browser, terminal, screenshot, mouse, or keyboard path;
- multiple 75–100-turn semantic playthroughs crossing exploration, expansion,
  production, research, combat warnings, diplomacy, Council events, and defeat
  recovery;
- two isolated Proton workers completing native DirectPlay host/discover/join,
  lobby synchronization, faction-separated play, host-only checkpointing,
  complete parking, stock multiplayer reload, exact faction restoration, and
  a second entry into gameplay;
- a real password-protected noVNC endpoint whose server is forced into
  view-only mode and whose secret is absent from container configuration; and
- adversarial scope, stale-revision, ownership, hidden-information, chat,
  memory, Graphiti cursor, authentication, CSRF, Docker-ownership, and cleanup
  tests.

The final managed-LAN regression used
`pixels_or_ui_input_used=false` throughout.

## Quick start: Control Center

Requirements:

- Linux with Docker Engine and Compose;
- a legal Alien Crossfire game directory;
- a local Proton distribution;
- the February 2010 DirectX redistributable for native DirectPlay; and
- an OpenAI-compatible model endpoint. Hermes is the current reference
  harness, but game/memory contracts are harness-neutral.

Start the persistent operator service:

```bash
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
./scripts/control-center-up.sh
```

Open `http://127.0.0.1:8080`. The first visit creates the administrator account;
there is deliberately no default password. The UI then guides you through:

1. registering and probing the model provider;
2. validating the legal game source;
3. importing a private, checksummed Proton runtime;
4. creating durable agents;
5. creating a solo or managed LAN match;
6. starting isolated workers and optional view-only spectators; and
7. binding Hermes to the exact running agent seat.

The Control Center stays up between games. Workers are disposable processes;
their match identity, saves, chat, and memory are durable.

For named human LAN seats, read [Control Center: Let human players
join](docs/control-center.md#let-human-players-join) before creating the Docker
macvlan/ipvlan network. The default private bridge deliberately does not expose
legacy DirectPlay to the physical LAN.

## Agent contract

The preferred loop is `smac_decision` → execute at most one returned guarded
choice → discard the frame → observe again. Every executable choice is bound
to the current match, process session, perspective, and revision.

The MCP deliberately exposes no screenshot, mouse, keyboard, native map
coordinate, coordinate click, arbitrary memory scope, or raw-UI fallback.
Opaque match-local `tile_id` values are identifiers, not coordinates. If the
game reaches an unsupported mandatory interaction, the model calls
`smac_report_capability_gap`; the session then rejects further mutation until a
typed handler is developed and a fresh native session is started.

Read [Safe semantic play loop](docs/agent-loop.md) and [Tool
reference](docs/tools.md) before authoring another harness adapter or agent
prompt.

## Legacy single-instance development flow

The original host-local MCP service remains available for bridge development
and focused testing:

```bash
systemctl --user status smacx-agent-mcp.service
hermes mcp test smacx
```

Its default MCP URL is `http://127.0.0.1:47814/mcp`. Start a new Hermes
conversation after changing the tool surface so the harness refreshes its tool
list. The managed Control Center path does not require restarting an existing
Hermes dashboard.

## Documentation map

- [Project status](docs/project-status.md) — what is delivered, what is merely
  implemented, and what remains.
- [Control Center](docs/control-center.md) — operator setup, match lifecycle,
  Hermes binding, external LAN, and security.
- [Architecture](docs/architecture.md) — trust boundaries and why the project
  extends the original executable instead of replacing it.
- [Identity and memory ADR](docs/adr/0001-identities-and-authoritative-memory.md)
  — durable scope and memory authority.
- [Control-plane ADR](docs/adr/0002-control-plane-and-runtime-boundary.md) —
  container, secret, and harness boundaries.
- [Optional Graphiti projection](docs/graphiti.md) — implemented adapter,
  configuration, isolation, and current integration status.
- [Coverage and limits](docs/coverage.md) — exact gameplay coverage and gaps.
- [Testing](docs/testing.md) — contained, native, Docker, Hermes, and LAN
  validation commands.
- [Troubleshooting](docs/troubleshooting.md) — fail-closed recovery guidance.
- [Platform roadmap](docs/platform-roadmap.md) — remaining deployment and
  product milestones.

## Repository layout

- `bridge/` — Thinker-derived 32-bit Windows DLL and fair-play bridge.
- `worker/` — isolated non-root Linux game-worker image and runtime contract.
- `control_center/` — authenticated operator service and web UI.
- `src/smacx_store.py` — authoritative SQLite identities, events, and memory.
- `src/smacx_worker_manager.py` — contained worker and LAN lifecycle.
- `src/smacx_mcp.py` — semantic MCP surface.
- `src/smacx_graphiti.py` — optional temporal-graph projector.
- `src/smacx_hermes.py` — current reference harness adapter.
- `scripts/` — builds, deployment helpers, and regressions.
- `docs/` — architecture, protocols, status, operations, and evidence.

## License and provenance

SMACX Agent is licensed under Apache License 2.0; see [LICENSE](LICENSE). The
bridge is a modified build of [Thinker](https://github.com/induktio/thinker) at
commit `4aef5be73bda4eb22ffa8db424eb91780c4a51fa`, whose upstream code is
MIT-licensed. Game assets, Proton, and the Microsoft redistributable are local
runtime dependencies and are not distributed here. See [NOTICE.md](NOTICE.md)
for provenance and attribution.
