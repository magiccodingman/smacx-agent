# SMACX Agent

**The modern LAN table, browser launcher, and fair-play AI player for Sid
Meier's Alpha Centauri: Alien Crossfire.**

SMACX Agent makes an increasingly awkward 1999 multiplayer game pleasant to
run again. A host can build a lobby in a polished web interface, invite people
who play from a browser or their own native client, add autonomous LLM players,
watch permitted seats, park a campaign, and bring everybody back later.

The AI does not play by staring at screenshots or guessing mouse coordinates.
A Thinker-derived DLL runs inside the real `terranx.exe` and exposes typed,
fair-play observations and guarded native actions. The ordinary game still
renders, humans still play the ordinary game, and the model sees only what its
faction is allowed to know.

> Linux-first and LAN-only. This application does not include or distribute
> Sid Meier's Alpha Centauri, Alien Crossfire, or other proprietary game
> assets. Users provide their own installation.

## What it feels like

- Open one local website and create a normal, custom, scenario, human, AI, or
  mixed lobby.
- Play a managed seat directly in the browser with video, audio, keyboard,
  mouse, fullscreen, and reconnect support—or join from a native game client.
- Let Qwen or another OpenAI-compatible model run through managed Hermes with
  no separately installed Hermes dashboard.
- Talk in faction-attributed public or private lobby/game chat.
- Watch AI-only games, switch seats as an administrator, or opt a lobby into
  anonymous read-only LAN spectating.
- Park an unfinished campaign. The platform stops its agents, verifies a
  native save, tears down disposable workers, and later restores the same
  seats and durable political memory.
- Compare versioned model profiles by turn time and authoritative Hermes input,
  output, cache, reasoning, and API-call telemetry.

This is meant to produce memorable players, not merely stronger stock bots.
An agent can remember promises, debts, betrayals, suspicions, relationships,
goals, territorial plans, and chat history under an exact match identity. Its
facts remain distinct from beliefs and attitudes; optional Graphiti adds a
temporal graph without replacing the authoritative SQLite record.

## The important engineering boundary

```text
browser or native human                     OpenAI-compatible model
          |                                           |
          v                                           v
  Blazor LAN portal  <---- lifecycle ---->  managed Hermes seat
          |                                      semantic MCP only
          v                                           |
  isolated Proton game worker <---- fair bridge ----> |
          |
          +---- Selkies stream (interactive or read-only)
```

Every managed seat has a separate game process, Proton prefix, MCP sidecar,
native session, perspective, and memory scope. The portal owns accounts,
lobbies, stream tickets, and reports. The private Python control plane owns
Docker, native matches, saves, secrets, workers, and harness runs. Neither
service writes the other's database.

Fair play is enforced below the prompt:

- fog of war and ownership are filtered in the native bridge;
- actions carry match, process-session, perspective, and revision guards;
- hidden coordinates never enter the agent protocol;
- destructive or diplomatic commitments require explicit typed confirmation;
- unsupported mandatory states fail closed and can be reported as capability
  gaps; and
- managed Hermes receives only the `smacx` toolset—no web, computer, screenshot,
  keyboard, mouse, terminal, or Docker tool.
- SMACX replaces Hermes prompt assembly with one integrity-checked provider
  system contract; live settings must be read and exactly acknowledged before
  the decision/command surface unlocks.

## Verified today

- Real single-player and DirectPlay LAN games run in isolated Linux/Proton
  workers while remaining visible.
- Typed setup covers world size, difficulty, planet traits, timer, victory
  conditions, advanced rules, scenarios, and saved-game recovery.
- Browser video/audio/input/fullscreen and reconnect use Selkies; observer and
  anonymous spectator paths are mechanically read-only.
- AI-hosted, human-hosted, mixed human/AI, save/park/reload, exact faction
  restoration, native chat, and private/public portal chat paths are
  implemented and locally native-tested.
- A real Qwen3.8-27B low-reasoning run autonomously played through turn 13/year
  2113 using semantic tools only, handled native opening states, searched the
  rules corpus, moved units, maintained goals/facts, and then survived a
  checkpoint-first park.
- The same run exposed 5,785,165 input tokens, 38,224 output tokens, 21,743
  reasoning tokens, and 97 provider calls through Hermes's authoritative
  counters; the portal integration records future deltas by turn/profile.
- Durable political/strategic memory, scoped FTS5/BM25 recall, optional
  Graphiti projection, scheduling, verified backups, and crash recovery are
  implemented.
- The distributable rules corpus contains 52 independently written mechanics
  documents, not copied manual text or strategy/cheese guides. First game-source
  validation automatically generated 672 private records on the reference copy,
  including exact technologies, facilities, components, factions, settings,
  prerequisites, and unlock relations. Canonical citations have fixed verified
  Internet Archive fallbacks; neither website is required at runtime.

Physical two-computer LAN, remote Tailscale peers, and Windows/WSL2 operation
remain external certification work; they are not claimed by the Linux-local
evidence above. Ranked play and authored personality cards are intentionally
not enabled. The schema contains only the `None` personality selection.

## Quick start

Requirements:

- Linux, Docker Engine, and Docker Compose;
- a user account able to access `/var/run/docker.sock`;
- an existing Alien Crossfire directory containing `terranx.exe`;
- a Proton distribution directory;
- the February 2010 DirectX redistributable for DirectPlay; and
- optionally, an OpenAI-compatible model endpoint for AI seats.

Start the persistent platform:

```bash
cd /path/to/smacx-agent
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
  ./scripts/control-center-up.sh
```

Read the one-time first-run token:

```bash
docker compose exec -T control-center dotnet Smacx.Portal.dll bootstrap-token
```

Open <http://127.0.0.1:8080>, sign in as `admin`, enter that token, and choose
your own password. There is no default password.

In **Administration**:

1. validate the existing Alien Crossfire directory;
2. import Proton into an installation-owned volume;
3. add an OpenAI-compatible provider and discover its models; and
4. create a versioned AI player profile—the raw provider alone cannot occupy
   an agent seat.

Then choose **New lobby**, select native rules and seats, and launch. AI hosting
is the recommended default; the first selected AI profile owns the native
session while you may add yourself as a human player. A browser
human clicks **Play**. A direct/native human uses the host address, native
session name/ID, assigned player handle, and faction shown on the lobby page.
An AI profile starts automatically in the isolated SMACX-derived Hermes
container built from the digest-pinned official runtime.

**Save without starting** creates a waiting portal lobby only. **Launch game
now** also provisions the players, starts the native game, and advertises the
joinable session. A single validated game installation and Proton runtime are
chosen automatically; advanced selectors appear only when alternatives exist.

To publish the portal on a trusted private LAN:

```bash
SMACX_PORTAL_PUBLISH=0.0.0.0:8080 \
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
  ./scripts/control-center-up.sh
```

For native clients, also configure the documented macvlan/ipvlan player
network. Browser players do not need a local game installation and do not need
the worker's stream port exposed directly; the authenticated portal proxies it.

The platform uses `restart: unless-stopped` and named volumes, so it stays up
between games and through host restarts. Do not take Compose down merely to
create another lobby.

## Everyday lifecycle

1. A human creates a waiting lobby in the portal.
2. The selected managed host creates the real native game. This can be a human
   browser seat or the first AI profile.
3. Browser seats connect through the portal; native seats join using the exact
   details displayed there. Managed agents join automatically.
4. The portal mirrors public turn/year, factions, chat, presence, runtime
   health, and model telemetry without exposing private faction state.
5. **Checkpoint** verifies a native save. **Park** first stops autonomous
   callers, then checkpoints and tears down the disposable game workers.
6. **Recover** restores the verified save, exact factions, MCP sidecars, and
   the same Hermes conversation/memory scope.

If all managed browser humans leave an unfinished match, the supervisor parks
it after the idle window. Direct/native players are never inferred absent from
browser presence. AI-only simulations continue unattended until explicitly
parked or completed.

## Administration and recovery

Generate a 30-minute reset ticket for the original administrator from the
host, even if the password is lost:

```bash
docker compose exec -T control-center dotnet Smacx.Portal.dll admin-reset-token admin
```

Other members can self-register without email. Invited handles are reserved as
case-insensitive provisional accounts; the player claims the same durable seat
and history when they choose a password.

Model provider keys are written to purpose-scoped secrets and mounted into
Hermes at runtime. They do not appear in Docker inspect output or the portal
database. Provider profiles are versioned and deactivated rather than deleted,
preserving the meaning of historical analytics.

Source builds are intentionally serialized. A source build that compiles the
native worker and optimizes Blazor benefits from roughly 12 GiB RAM plus 4 GiB
swap; the verified development VM uses 16 GiB RAM and 16 GiB swap. Ordinary
runtime use is substantially lighter and scales mainly with active game/AI
seats.

## Knowledge and memory

Agents search the authored handbook and exact private Alien Crossfire entities
through typed lookup, relations, and FTS5/BM25. Game-source validation builds
the private installation index automatically and the project does not
distribute it. Strategy guides and scenario solutions are deliberately
excluded.

Per-match memory supports immutable events, facts, beliefs, relationships,
commitments, goals, summaries, compression budgets, multi-record recall, and
chat history. Every operation is scoped to match + agent + perspective, so
parallel agents and later games cannot contaminate one another.

## Documentation

- [Operator guide](docs/control-center.md) — first run, LAN publication,
  browser/native seats, providers, lobbies, recovery, users, and secrets.
- [Architecture](docs/architecture.md) — trust boundaries, native bridge,
  streaming, portal/control split, and memory model.
- [Project status](docs/project-status.md) — exact implemented, verified, and
  externally unverified boundaries.
- [Rules knowledge](docs/reference-knowledge.md) — original corpus, private
  extraction, provenance, and copyright guard.
- [Testing](docs/testing.md) — .NET, Python, Docker, browser, native, and model
  evidence.
- [Troubleshooting](docs/troubleshooting.md) — Docker permissions, setup,
  DirectPlay, streams, checkpoints, and capability gaps.
- [Coverage](docs/coverage.md), [agent loop](docs/agent-loop.md), and [MCP tool
  reference](docs/tools.md) — semantic gameplay details.
- [Graphiti](docs/graphiti.md) — optional temporal projection and isolation.
- [ADR 0003](docs/adr/0003-lan-browser-platform.md) — accepted LAN browser
  platform design.

## Repository layout

- `bridge/` — Thinker-derived 32-bit native bridge DLL.
- `worker/` — isolated Proton game worker with Selkies streaming.
- `src/` — control plane, MCP, memory, worker/Hermes managers, and Graphiti
  projection.
- `portal/` — .NET 10 Blazor Web App, WebAssembly client, controllers, SignalR,
  Identity, stream proxy, and tests.
- `knowledge/` — distributable original mechanics corpus only.
- `scripts/` — reproducible contracts, native tests, and operations.
- `docs/` — product, operator, architecture, test, and protocol documentation.

SMACX Agent is licensed under Apache License 2.0. Thinker-derived code retains
its MIT notice; see [NOTICE.md](NOTICE.md).
