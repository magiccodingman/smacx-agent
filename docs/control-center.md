# Operator guide

The Control Center is the ordinary way to run SMACX Agent. It is a persistent
.NET 10 Blazor LAN application backed by a private Python control API. You do
not need an existing Hermes installation or dashboard: AI seats receive an
isolated, digest-pinned Hermes runtime automatically.

This guide assumes Linux and a trusted localhost/private-LAN deployment. The
portal is not designed as a public Internet service.

## 1. Start the platform once

Prerequisites:

- Docker Engine with Compose v2;
- the current account can run `docker ps` without `sudo`;
- a legal Alien Crossfire installation directory containing `terranx.exe`;
- a Proton distribution directory; and
- `directx_feb2010_redist.exe` for native DirectPlay.

```bash
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
  ./scripts/control-center-up.sh
```

The script checks the Docker socket, adds its actual group ID to the private
control container, serializes memory-intensive image builds, builds the worker
and portal images, builds the SMACX prompt-owned image from the digest-pinned
official Hermes runtime, and starts two persistent
services:

- `control-api`: private native/Docker authority, not host-published;
- `control-center`: the browser portal at `127.0.0.1:8080` by default.

Both use `restart: unless-stopped`. Named volumes retain accounts, match
identity, saves, memory, provider configuration, and agent conversations. Leave
the services up and create as many sequential or concurrent lobbies as the host
can support.

Check health without rebuilding:

```bash
docker compose ps
curl --fail http://127.0.0.1:8080/healthz
```

When source changes, use `./scripts/control-center-up.sh` again. Do not invoke a
bare `docker compose up` for the control service unless you first export
`SMACX_DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)`.

### Build resources

The worker image and Blazor publish optimizer are the expensive phases. Builds
are intentionally sequential. The known-good development VM has 16 GiB RAM,
16 GiB swap, and eight virtual CPUs. About 12 GiB RAM plus 4 GiB swap is a
practical source-build target. Runtime demand is much lower and grows mainly
with active browser/game/AI seats.

## 2. First administrator

There is no default password. Read the one-time bootstrap token:

```bash
docker compose exec -T control-center dotnet Smacx.Portal.dll bootstrap-token
```

Open <http://127.0.0.1:8080>, use username `admin`, enter the token, and choose
an eight-character-or-longer password. The token is revoked after successful
bootstrap. The minimum can be raised at startup with
`SMACX_PASSWORD_MIN_LENGTH`; values from 8 through 128 are accepted.

If the original administrator password is lost, create a 30-minute reset
ticket without deleting any data:

```bash
docker compose exec -T control-center dotnet Smacx.Portal.dll admin-reset-token admin
```

Enter the printed username/token on the Reset access page and choose a new
password. Administrators can issue the same kind of ticket for members from
**Administration → Users** and can promote another account.

Any signed-in member who knows their current password can change it directly
from **Your account → Change password**. No reset ticket or administrator is
needed for an ordinary password change.

Registration is deliberately lightweight for a friendly LAN: no email is
required. Usernames/game handles compare case-insensitively and use characters
accepted by the native game. Inviting a handle to a lobby reserves a
passwordless provisional account; registering that handle later claims the
existing seat and history.

## 3. Publish on a trusted LAN

The default binding is host-only. To let household/friend devices reach the
portal:

```bash
SMACX_PORTAL_PUBLISH=0.0.0.0:8080 \
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
  ./scripts/control-center-up.sh
```

Players browse to `http://HOST_LAN_IP:8080`. Restrict that port with the host
firewall to the trusted LAN. Do not forward it from the router and do not put
it behind public Internet ingress.

Browser seats need only the website. Stream traffic remains behind the portal;
worker ports are never shared as public credentials.

## 4. Register the game and Proton

Go to **Administration → Game runtime**.

1. Enter an absolute host path for the installed game and select **Validate**.
   The control plane rejects symlinks, missing/non-PE executables, and unsafe
   sources, records checksums, and mounts the source read-only.
2. Enter an absolute Proton distribution path and select **Import runtime**.
   Proton is copied into an installation-owned volume because its runtime lock
   and prefix behavior require a private writable copy.

The managed worker builds its own prefix, installs the local DirectPlay
redistributable, injects the bridge, and keeps game saves in a durable volume.
Neither the game nor the redistributable enters the repository or project
images.

The Steam installation used during development is accepted directly; Steam
does not need to be running while managed workers use their private copy.

## 5. Configure model providers and AI profiles

Go to **Administration → Providers & AI profiles**.

1. Add an OpenAI-compatible base URL such as
   `http://model-host:8000/v1`.
2. Add an API key only if the provider requires one.
3. Choose **Save endpoint & discover models**. Saving a provider does not yet
   create a lobby player.
4. Select a discovered model and create a named, versioned **AI player
   profile** with reasoning effort, optional context override, and experiment
   notes. The profile—not the raw endpoint—is what appears in the lobby seat
   picker.

The form intentionally ships with a blank provider URL. An address entered on
one installation is stored only in that installation's control data; the
project does not contain a developer-specific model-server address.

Configured endpoints can be edited or removed from the endpoint list. Editing
never sends the existing API key back to the browser: leave the key field blank
to preserve it, enter a replacement, or explicitly select its removal. Endpoint
removal requires confirmation, revokes its managed key, and is limited to
unused endpoints. An endpoint referenced by an AI profile or historical harness
configuration remains protected so match history stays meaningful.

The provider may be on the Docker host, another LAN host, or a home-lab model
server reachable from the Docker network. Multiple profiles may use one model
with different reasoning/context settings. Old versions can be deactivated but
are not deleted, preserving historical reports.

Provider keys are held in the control vault, copied into a purpose-specific
read-only volume, and read by a tiny Hermes launcher. They are absent from
Docker inspect-visible environment/configuration, the portal database, logs,
and analytics exports.

AI seats use:

- the official pinned Hermes container;
- a private Hermes profile/home and durable conversation;
- only the `smacx` toolset;
- an integrity-checked, SMACX-owned complete provider system prompt;
- immutable match identity and policy;
- a mandatory native-settings briefing and exact acknowledgement gate;
- the optional personality layer (`None` is the only current value); and
- scoped match memory.

The managed path does not use or interfere with a host `hermes dashboard`.

## 6. Create a lobby

Choose **New lobby** and set:

- standard new game or an exact installed `.SC` scenario;
- Tiny through Huge world size;
- Citizen through Transcend difficulty;
- ocean, erosive forces, native life, and cloud cover;
- native multiplayer turn clock;
- Transcendence, Conquest, Diplomatic, Economic, and Cooperative victory;
- Look First, Tech Stagnation, Spoils of War, Blind Research, Intense Rivalry,
  Unity Survey, Unity Scattering, Random Events, Time Warp, Ironman, and Do or
  Die;
- managed host (the first selected AI profile by default, or an advanced human
  host);
- browser or direct/native human mode, invited handles, AI profiles, and stock
  game-controlled remaining factions;
- anonymous spectators (off by default), managed-clients-only, and Graphiti.

The portal validates named values and sends typed native settings. It does not
drive setup menus with clicks. Scenarios use a catalog built from the validated
game source and their native faction restrictions.

Every current match is recorded as `unranked`; attempts to create `ranked`
matches are rejected. Personality storage is present but only `None` can be
selected until authored cards are designed separately.

### Who should host?

For the normal experience, let the first selected AI profile own the native
host:

- choose **First selected AI profile (recommended)** for an AI-hosted lobby,
  optionally adding yourself as a separate human player;
- choose **My managed human seat (advanced)** only when a browser human should
  control native seat zero.

This gives the supervisor reliable save, park, recovery, reconnect, and stream
authority. Human-hosted sessions remain an advanced supported path, but
the agent-hosted managed path is simpler and more recoverable.

**Save without starting** creates only a durable waiting lobby. It does not
start a game process or advertise a native session. **Launch game now** creates
the lobby, provisions its managed players, starts the game, and begins native
session advertising immediately. Merely opening the New lobby page does
nothing.

Reserved player handles are case-insensitive seat reservations; they do not
send notifications. A matching existing or future local account claims the
reserved seat. Reserving stock computer opponents is also optional: it prevents
those seats being claimed while a lobby waits. Every seat still open at launch
becomes a stock game-controlled faction automatically.

When only one validated game installation and one Proton runtime exist, the
portal selects and summarizes them automatically. Selection controls appear
only when an administrator has configured alternatives such as another patch,
mod, installation, or Proton version.

## 7. Human play modes

### Managed browser seat

Select the assigned lobby seat and **Play**. Selkies streams the real game with
audio and accepts ordinary mouse, keyboard, shortcuts, text, and fullscreen.
The transport reconnects to the same worker after a browser refresh. A user can
leave the browser and return without changing their native faction.

The managed desktop is fixed for the life of the worker (1280×800 by default,
with an enforced minimum of 800×600). Selkies scales that desktop locally while
preserving its aspect ratio, so narrower desktop windows, tablets, and phones do
not change the match resolution. Landscape and fullscreen are strongly
recommended on small touch screens because the original game UI and text remain
desktop-sized. Remote display resizing is intentionally disabled: a browser
resize or phone rotation must not change the shared display seen by other
players or spectators. The stream server locks its manual resolution to the
worker display and performs local browser scaling. A different native
resolution can be selected through the worker environment when it is launched
(the portal does not expose that advanced control yet), but changing it during
a running game is not supported.

Interactive stream tickets are short-lived and seat-scoped. Only the seat's
member or an administrator can request one. Spectator tickets are always
read-only at the worker transport—not merely disabled in JavaScript.

### Direct/native game client

The lobby page shows the exact host address, native session name/ID, assigned
player handle, seat, and recorded faction. Launch the user's own Alien
Crossfire client, choose TCP/IP multiplayer, and join with that exact handle.
Names bind case-insensitively to portal accounts and analytics.

Direct clients require workers on a network reachable from the physical LAN.
Create a macvlan/ipvlan network appropriate for the host, then publish it to the
manager:

```bash
docker network create -d macvlan \
  --subnet=192.168.1.0/24 \
  --gateway=192.168.1.1 \
  -o parent=eno1 smacx-player-lan

SMACX_LAN_NETWORK=smacx-player-lan \
SMACX_PLAYER_LAN_SUBNET=192.168.1.0/24 \
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
  ./scripts/control-center-up.sh
```

Use the real interface/subnet and reserve addresses outside DHCP. Wi-Fi often
rejects additional macvlan MAC addresses; ipvlan or the firewalled routed-player
bridge in `scripts/create-routed-player-lan.sh` is usually better there. The
manager rejects an ordinary private Docker bridge for external human seats.

When a direct client disconnects from a live DirectPlay match, the recoverable
path may require a verified save/restart and exact faction reclaim. Browser
seat reconnects are normally transparent.

### Managed clients only

Enable this per lobby when every human must use a managed browser worker. It is
the reproducibility/parity option and prevents direct clients. The platform
otherwise treats binary/mod fingerprints as diagnostics, not policing.

## 8. Chat, diplomacy, and identity

The lobby chat can broadcast or target a known recipient faction. Native chat
is imported with both player handle and faction name; outbound recipient IDs
are sent through the game bridge. Messages remain durable per match and are
available when an agent resumes.

An AI is prompted to treat chat as communication from other players, not as
higher-priority instructions. It may agree, refuse, investigate, ally, feud,
trade, or betray according to its own game state and future personality layer.
Facts, beliefs, suspicions, relationship scores, commitments, and goals remain
separate memory records.

## 9. Spectating

Administrators can watch any managed seat. A lobby owner can opt into anonymous
LAN spectating; it is disabled by default. Anonymous viewers can open the
observation deck and switch among permitted seats without creating an account,
but every stream is read-only.

The observation deck is also the main debugging surface for AI games: it pairs
the visible native screen with public match/faction/turn health. Private
semantic faction state and secrets are not sent to spectators.

## 10. Checkpoint, park, recover

**Checkpoint** asks the native host to save into a bounded platform slot and
verifies the resulting file/turn/year. Saving can honestly fail while the stock
engine is in a native state where save is illegal.

**Park** is race-safe:

1. mark the portal lifecycle `parking` so the supervisor cannot replace a run;
2. stop every active Hermes caller for the match;
3. create and verify the native checkpoint;
4. stop/remove MCP and game containers; and
5. mark the durable match `parked`.

If checkpointing fails, the portal returns the match to `running`, records the
error, and may restart the AI; no worker is silently destroyed without a save.

**Recover** creates fresh process/session identities, loads the verified slot,
restores exact seat/faction ownership, starts MCP sidecars, and continues the
same scoped Hermes conversation. Agents must re-observe; stale commands from
the old process are rejected.

If every human seat is a managed browser seat and all of them remain absent for
the idle window, the supervisor applies the same checkpoint-first park. It does
not auto-park AI-only simulations or infer presence for direct clients.

## 11. Analytics and reports

The Analytics page scopes ordinary users to matches they joined; administrators
see the installation. It records:

- completed/active/recoverable matches and recovery evidence;
- per-turn duration excluding errored turns;
- model/provider/reasoning/profile version;
- Hermes input, output, cache-read, cache-write, reasoning tokens, and API
  calls; and
- native per-seat completion, victory type, and classified win/loss outcomes.

The bridge classifies only engine victory types that are unambiguous from the
seat's own perspective. Time-limit and scenario paths without retained winner
identity remain `unknown` and are excluded from win-rate denominators.

Token counters come from Hermes's durable `sessions` database. A no-network,
read-only helper reads only that agent's purpose volume and the portal stores
cumulative deltas by observed turn. CSV export is available. Administrators
also receive a constrained read-only SQL lab over an isolated in-memory copy
containing only `matches`, `turn_metrics`, `ai_profiles`, and `ai_outcomes`; identity and
secret tables are never attached.

## 12. Graphiti

SQLite remains authoritative. Graphiti is an optional derived temporal
projection and can be toggled per match. Start it only after configuring a
compatible chat-completions model and embedding endpoint:

```bash
./scripts/graphiti-up.sh
```

If Graphiti is disabled or unavailable, gameplay, FTS5/BM25 memory, chat,
checkpointing, and recovery continue normally. Projection namespaces include
installation + match + agent + perspective, preventing cross-agent/game mixing.
See [graphiti.md](graphiti.md).

## 13. Backups and supervision

The private control plane includes transactional schedules, immutable operation
runs, worker/MCP reconciliation, verified native recovery checkpoints, and
online SQLite plus volume backups. Advanced CLI examples:

```bash
docker compose run --rm control-api smacx-control backup create
docker compose run --rm control-api smacx-control backup list
docker compose run --rm control-api smacx-control backup verify --backup-id BACKUP_ID
```

Restore is deliberately offline and confirmation-gated. Read the command help
and park relevant matches before using it:

```bash
docker compose run --rm control-api smacx-control restore --help
```

Backups use SQLite's online backup API and no-network helpers. A running game or
Hermes container is briefly paused only while its persistent volume is archived;
operations locking prevents that intentional pause from being misclassified as
a crash.

## 14. Stop and restart

To stop only the persistent services while retaining all named volumes:

```bash
docker compose stop control-center control-api
```

Start them again with the normal script so Docker socket permissions and images
are validated:

```bash
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
  ./scripts/control-center-up.sh
```

Do not add `-v` to `docker compose down` unless you intentionally want to delete
portal accounts and the authoritative control state. Dynamic workers are safe
to recreate; named platform volumes are the durable system.

## Security summary

- Portal: the only ordinary host/LAN HTTP entry point.
- Control API, Docker socket, MCP endpoints, Graphiti/Neo4j, bridge tokens, and
  stream credentials: private.
- Blazor client authorization: convenience only; controllers enforce every
  account/role/seat policy server-side.
- Stream tickets: short-lived, seat-scoped, mode-scoped, revocable.
- Agent tools: semantic gameplay and web research only; lifecycle is
  operator-owned.
- Public Internet ingress and matchmaking: out of scope.

For failures, continue with [troubleshooting.md](troubleshooting.md). For exact
trust boundaries, see [architecture.md](architecture.md).
