# Operator guide

The Control Center is the ordinary way to run SMACX Agent. It is a persistent
.NET 10 Blazor private-host application backed by a private Python control API. You do
not need an existing Hermes installation or dashboard: AI seats receive an
isolated, digest-pinned Hermes runtime automatically.

This guide assumes Linux and begins with localhost and trusted-LAN deployment.
Use [Getting started](lan-installation.md) for a concise first installation,
[Network access and play modes](network-access.md) for the complete access
matrix, and [Internet hosting](internet-hosting.md) when inviting known friends
over HTTPS. The service is not public matchmaking or anonymous streaming.

## 1. Start the platform once

Prerequisites:

- Docker Engine with Compose v2;
- the current account can run `docker ps` without `sudo`;
- a legal Alien Crossfire installation directory containing `terranx.exe`; and
- outbound access to the pinned upstream release URLs during the first worker
  image build.

```bash
SMACX_GAME_SOURCE="/absolute/path/to/Sid Meier's Alpha Centauri" \
  ./scripts/control-center-up.sh
```

The script checks the game directory and Docker socket, adds the socket's
actual group ID to the private control container, serializes memory-intensive
image builds, seals checksum-pinned GE-Proton and DirectPlay into the worker,
builds the portal and SMACX prompt-owned image from the digest-pinned official
Hermes runtime, validates the game inside that worker, and starts five persistent
services:

- `knowledge-service`: private mechanics acquisition, indexing, search, and
  shared embedding runtime;
- `control-api`: private native/Docker authority, not host-published;
- `control-center`: authenticated portal/API, private behind the edge;
- `edge`: Caddy at LAN HTTP port 8080 and, when configured, Internet HTTPS port
  443; and
- `ddns`: an idle-or-configured dynamic-DNS updater.

The persistent services use `restart: unless-stopped`. Named volumes retain
accounts, match identity, saves, memory, provider configuration, and agent conversations. Leave
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
are intentionally sequential. If the kernel terminates a build for lack of
memory, increase the host's available memory or swap and rerun the launcher.
Runtime demand is lower and grows mainly with active browser/game/AI seats.

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
**Administration → Players** and can promote another account.

Any signed-in member who knows their current password can change it directly
from **Your account → Change password**. No reset ticket or administrator is
needed for an ordinary password change.

Registration is deliberately lightweight for a friendly LAN: no email is
required. The username is the private sign-in identity; the public display name
is what other humans and agents see in lobbies, chat, history, and the native
game. Both compare case-insensitively. Public display names use characters
accepted by the native game, must be unique, and may be changed from **Your
account** while the old name is not reserved by an unfinished match. Inviting a
display name to a lobby reserves a passwordless provisional account;
registering that display name later claims the existing seat and history.

## 3. Open on a trusted LAN

The included Caddy edge publishes port 8080 for localhost and private-network
play. Players browse to `http://HOST_LAN_IP:8080`; the portal itself remains
unpublished behind that edge. Caddy and the DDNS helper are part of every
deployment, but public TLS and address updates remain idle until configured.

To continue the same installation for invitation-only remote friends, follow
[Internet hosting for friends](internet-hosting.md). Do not forward the plain
HTTP LAN port; remote sign-in requires the Caddy-managed HTTPS hostname. The
exact LAN, Internet-browser, physical-native, and virtual-LAN differences are in
[Network access and play modes](network-access.md).

Browser seats need only the website. Stream traffic remains behind the portal;
worker ports are never shared as public credentials.

### Install on a desktop or mobile device

Open **Install app** in the portal. The host itself can install from the
loopback URL. Other LAN devices need a trusted HTTPS origin before browsers can
offer reliable PWA installation; ordinary HTTP browsing continues to work but
is not treated as installable. See [installable-app.md](installable-app.md) for
the prompt fallback, manifest, icon, and no-offline-cache boundaries.

## 4. Managed game platform

The ordinary deployment has one host input: `SMACX_GAME_SOURCE`. The startup
script rejects a directory without `terranx.exe`; after containers start, the
control plane validates the executable as a Windows PE file inside the worker,
records its checksum and private mechanics index, and mounts the source
read-only into every disposable seat.

**Administration → Game runtime** is deliberately read-only. It reports the
validated legal-copy fingerprint, private knowledge-build state, sealed worker
image, GE-Proton/DirectPlay readiness, and source path used at startup. Lobby
creators never choose paths or runtimes, and a half-configured portal cannot be
used to create a match.

The worker image downloads GE-Proton10-34 from its upstream GitHub release and
the original Microsoft February 2010 DirectX redistributable from a fixed
Internet Archive capture. Both URLs and cryptographic digests are pinned in
`worker/Dockerfile`. The build fails closed on a mismatch. Each managed seat
then receives an isolated prefix, bridge, save volume, and stream; neither the
game source nor a writable compatibility tree is copied from another seat.

The proprietary game and its extracted reference material never enter the
repository or a distributed project artifact. Steam does not need to run after
the source directory exists.

## 5. Configure model providers and AI profiles

Go to **Administration → Providers & AI profiles**.

1. Add an OpenAI-compatible base URL such as
   `http://model-host:8000/v1`.
2. Add an API key only if the provider requires one.
3. Choose **Save endpoint & discover models**. Saving a provider does not yet
   create a lobby player.
4. Select a discovered model and create a named **AI player profile** with a
   starting template, explicit sampling/request parameters, reasoning intent
   controls, optional context override, and experiment notes. The profile—not
   the raw endpoint—is what appears in the lobby seat picker.

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
with different reasoning, sampling, and context settings. Editing updates the
stable named profile in place and keeps its analytics identity. Deactivation is
reversible; names remain reserved so old history cannot silently attach to a
new identity. Use separate names when several configurations should remain
selectable for an A/B run.

The profile editor can also export or import a portable JSON template. The
template contains the model ID, reasoning, context, generation settings, and
operator note; it deliberately excludes endpoint IDs, API keys, agent IDs, and
history. Import it after choosing an endpoint on the new installation. The UI
only preselects the model when that endpoint actually advertises the same model
ID.

**Provider defaults** is the compatibility-first blank template. It sends no
sampling override unless the operator explicitly adds one. Every template is
an editable starting point, never a runtime macro or locked preset. Profiles
may set temperature, top-p, top-k, min-p, presence/frequency/repetition
penalties, maximum output tokens, and seed. Additional rows accept JSON-typed
provider extensions—including nested chat-template arguments—without baking a
specific model family into the generic request path. These exact displayed
values survive import/export and are passed through Hermes and direct Graphiti
requests. Blank values are omitted.

Qwen3.8 models expose two convenience presets based on the project's official
recommendations: thinking (temperature 1.0, top-p 0.95, top-k 20, min-p 0,
presence penalty 0, repetition penalty 1) and non-thinking (temperature 0.7,
top-p 0.8, top-k 20, min-p 0, presence penalty 1.5, repetition penalty 1).
Qwen3.8 templates are exposed as Instant, Low, Medium, High, and XHigh. Instant
disables thinking and uses `none`; Low, Medium, and XHigh select Qwen's
documented levels. High is retained as a clearly marked provider-dependent
Hermes option rather than represented as an official Qwen level. Every built-in
Qwen3.8 template visibly adds `chat_template_kwargs` with
`preserve_thinking=false`, so old
reasoning traces do not balloon later prompts while the current tool loop still
retains its active reasoning state. Operators may edit or remove any template
value. Reasoning remains a separate advanced control because gameplay and direct
services serialize the same intent differently: Hermes owns the gameplay
adapter, while Graphiti sends top-level `reasoning_effort`. Model-specific chat
template controls remain explicit JSON parameters.
See the [Qwen3.8-27B model card](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/README.md)
for the upstream recommendations.

Use **Test provider acceptance** on a saved profile to send one bounded request
through the configured endpoint. The resulting status distinguishes untested,
accepted, rejected, and stale configurations. An accepted request proves the
stored fields were serialized and the endpoint returned success; it does not
prove that the server honored every extension semantically. The UI deliberately
keeps that distinction visible instead of treating an HTTP 200 as proof of
semantic behavior.

The default gameplay context is **Automatic** and uses the selected model's
advertised context length. A manual override must be at least 65,536 tokens and
cannot exceed the advertised maximum. Durable facts, goals, relationships, and
chat live in match-scoped MCP memory instead of depending on an ever-growing
transcript.

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
- a versioned native-configuration briefing and exact acknowledgement gate;
  unchanged recoveries reuse the durable acknowledgement, while real rule or
  seat changes return a compact delta and relock play;
- the lobby-selected faction personality layer (Standard, Random, None, or one
  of four authored variants for that leader); and
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
- authenticated non-player spectators (off by default for games with human
  players; automatic for running AI-only simulations), managed-clients-only,
  and Graphiti.

The portal validates named values and sends typed native settings. It does not
drive setup menus with clicks. Scenarios use a catalog built from the validated
game source and their native faction restrictions.

Every current match is recorded as `unranked`; attempts to create `ranked`
matches are rejected. Each AI seat independently selects a faction and
Standard, Random, None, or one of that leader's authored personality variants.

### Who should host?

Hosting is automatic. If the table contains an AI, the first AI seat owns the
native session, including when the same profile is assigned to several seats.
Each seat still receives a distinct runtime identity, perspective, worker,
MCP, Hermes workspace, and memory scope. Without an AI, the first managed
browser human hosts. This gives the supervisor reliable save, park, recovery,
reconnect, and stream authority without asking a lobby creator to understand a
1999 network topology.

**Create waiting lobby** records only the campaign-wide world, rules, and access
policy. It does not start a game process or advertise a native session. The
creator begins in seat 1 as a browser player, making the ordinary path ready to
host without another setup decision. The staging room remains the one
authoritative roster editor: the owner can move or leave that seat, reserve a
human, add an AI or stock computer, configure any assignment, or remove it
explicitly. Leaving the seat lets the owner remain an observer. **Start match**
is the single action that provisions players and begins native session
advertising.

A self-claimed browser seat in a waiting lobby is presence-bound. When its
player's last lobby tab disconnects, every connected viewer sees a 30-second
reconnect countdown on that seat. Any tab reconnecting as the same account
cancels the countdown; otherwise the seat returns to the open roster. Multiple
tabs are counted together. Explicit player reservations, direct/native seats,
and every seat after the match starts are deliberately excluded. Set
`SMACX_STAGING_SEAT_GRACE_SECONDS` to 10–300 seconds to change the default.

Regular members may own at most five waiting lobbies. Joined or invited rooms
do not count, and starting or closing a room immediately frees a slot.
Administrators are unlimited for simulation work. The create page shows the
current quota and links to rooms that must be started or closed. A waiting room
remains live while any signed-in human has that exact staging page open. When
its final viewer leaves, a persisted 30-minute expiration begins; returning to
the page cancels it. `SMACX_WAITING_LOBBY_ABANDON_MINUTES` may set 5–1440
minutes. The lobby directory does not keep rooms alive.

Reserved public display names are case-insensitive seat reservations; they do not
send notifications. A matching existing or future local account receives the
exclusive **Claim seat** action; the reconnect timer begins only after that
explicit claim. Reserving stock computer opponents is also optional: it
prevents those seats being claimed while a lobby waits. Every seat still open
at launch becomes a stock game-controlled faction automatically.

Lobby Comms exists before any managed AI process is launched, so staging-room
messages remain a human coordination channel and are not retroactively inserted
into an agent conversation. After launch, portal chat crosses the native game
transport and becomes visible to AI players through their fair-play semantic
chat state.

Game-source and compatibility-runtime IDs remain recorded internally for
recovery and audit history, but are not ordinary lobby controls. The startup
contract owns one validated game directory and one sealed compatibility stack.

## 7. Human play modes

### Managed browser seat

Select the assigned lobby seat and **Play**. Selkies streams the real game with
audio and accepts ordinary mouse, keyboard, shortcuts, text, and fullscreen.
The transport reconnects to the same worker after a browser refresh. A user can
leave the browser and return without changing their native faction.

The managed desktop is fixed for one worker lifetime, but the portal supports a
managed native catalog from 800×600 through 5120×1440. Selkies scales the
current desktop locally while preserving its aspect ratio, so ordinary browser
resizing and phone rotation are instant and never disturb another player.
Landscape and fullscreen are strongly recommended on a small touch screen.

Open the game's plain root **MENU** to reveal the human-only managed-play rail.
Its Display panel recommends 800×600 for a small touch device, 1024×768 for a
tablet, or the largest suitable desktop profile. CSS fitting is immediate.
**Apply natively** creates a persisted match proposal; after any required peer
vote, the platform waits for a stable checkpoint, parks the workers, updates
the native profile, and recovers the same factions. The rail disappears before
any native submenu or modal and is never available to an agent seat. Display
preferences and the optional profile lock are stored on that browser/device.

Remote display resizing remains disabled inside Selkies. The stream server
locks its manual dimensions to the worker framebuffer, and the worker writes
matching Thinker custom-window dimensions before launch. This prevents the
first viewer or a spectator from resizing/cropping the actual game.

Interactive stream tickets are short-lived and seat-scoped. Only the seat's
member or an administrator can request one. Spectator tickets are always
read-only at the worker transport—not merely disabled in JavaScript.

### Direct/native game client

The lobby page shows the exact host address, native session name/ID, required
public display name, seat, and recorded faction. Launch the user's own Alien
Crossfire client, choose TCP/IP multiplayer, and join with that exact display
name. DirectPlay participants are matched case-insensitively. An unexpected
name, a faction-leader name reserved for an agent, or a later duplicate cannot
take somebody else's portal seat. In an agent-hosted lobby the native host
removes that participant and reports the rejection; a human-hosted lobby blocks
finalization because the managed process does not own the native host.

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
SMACX_GAME_SOURCE="/absolute/path/to/Sid Meier's Alpha Centauri" \
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

The lobby and in-game control center share one durable chat history. Broadcast
to everyone, privately message a currently contacted faction, or create a named
private group. Every invited group member must accept before it becomes active.
The native transport receives one private copy per group recipient, while the
portal and AI memory preserve one logical message with per-recipient delivery
status. Native chat is imported with both public display name and faction name, even
when it arrives outside the local player's turn.

Private/group bodies are returned only to their authorized participants;
SignalR announces a change without broadcasting those bodies. Authenticated
spectators receive only the globally visible chat their observation policy
permits. See [managed-play.md](managed-play.md) for the exact consent and
delivery model.

An AI is prompted to treat chat as communication from other players, not as
higher-priority instructions. It may agree, refuse, investigate, ally, feud,
trade, or betray according to its own game state and resolved personality
layer; a `None` seat still remains an autonomous game player.
Facts, beliefs, suspicions, relationship scores, commitments, and goals remain
separate memory records.

## 9. Spectating

Non-participating administrators can watch managed seats for household support
and debugging. A lobby owner can opt into authenticated non-player spectating;
it is disabled by default for games containing human players. Running AI-only
simulations are always visible to signed-in viewers who have never occupied a
faction in that campaign. Eligible viewers can open the observation deck and
switch among permitted seats.
Every spectator stream is read-only. A campaign participant remains excluded
from enemy views after leaving a seat, including when that account is an
administrator.

The observation deck is also the main debugging surface for AI games: it pairs
the visible native screen with public match/faction/turn health. Private
semantic faction state and secrets are not sent to spectators.

## 10. Votes, checkpoint, park, recover

Native resolution, temporary computer control for a disconnected browser
player, seat reclaim, host transfer, park, and end are governed operations. The
other connected humans vote; a majority passes, one remaining peer decides,
and a solo human needs no ceremonial self-vote. Eligibility and votes are
persisted. Resolution changes have a five-minute multiplayer cooldown which
players can vote to waive; browser fitting never waits.

A passed proposal still cannot bypass native safety. The portal keeps the game
interactive while it waits for synchronized semantic samples and only blocks
the stream after a verified checkpoint has been captured.

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
restores exact seat/faction ownership, starts only the MCP/Hermes sidecars
assigned to agent seats, and continues each scoped conversation. Agents must
re-observe; stale commands from the old process are rejected. Browser players
reconnect to the same portal route.

A browser disconnect does not retire its faction. After 30 seconds, connected
humans may vote to delegate that browser-managed seat temporarily to the stock
game AI. A returning owner can reclaim it through another stable-checkpoint
vote. Returning to the native main menu or losing a worker triggers automatic
recovery from the most recent verified checkpoint; without one, the match stops
for operator review rather than risking unsaved turns.

If every human seat is a managed browser seat and all of them remain absent for
the idle window, the supervisor applies the same checkpoint-first park. It does
not auto-park AI-only simulations or infer presence for direct clients.

AI-only campaigns continue unattended by design, but never become
unmanageable or opaque. Their owner or an administrator can open the lobby
detail page at any time to **Save checkpoint**, **Stop & park**, or **End
campaign**, and authenticated nonparticipants can always observe their managed
seats. Park is reversible and
retains the verified save, seat assignments, AI conversations, semantic
memory, and analytics. End is available only after parking, is deliberately
irreversible, releases the large disposable game prefix plus runtime secrets,
and keeps the campaign record, events, outcomes, metrics, chat, durable
knowledge, and compact Hermes conversation evidence for history and reports.

If an AI reports a semantic capability gap, the match remains durably marked
**AI paused · needs attention** even after the detailed report is dismissed.
The native worker and diagnostic state stay preserved. After installing a fix,
the owner or an administrator can choose **Retry from verified checkpoint**.
That explicit action replaces the old worker, bridge, and MCP layers with the
current managed images, restores the last verified save, and starts one fresh
Hermes run. The incident is cleared only after native recovery succeeds; a
failed attempt stays visible and fail-closed. **Stop & park** remains available
when the operator does not want to retry yet.

Retry is a durable background maintenance operation. The click returns as soon
as the operation is queued; closing or refreshing that browser does not stop
it. The lobby and observation deck display its current phase. Portal restarts
requeue an interrupted operation, native-state reconciliation recognizes work
that completed across a disconnected HTTP request, and worker/MCP lifecycle
mutations are serialized so the supervisor cannot race a deliberate rebuild.
Once native recovery is verified, normal supervision launches the missing
Hermes run and stream views remount against the new runtime generation.

The Campaign Library is paginated and searchable, with active, resumable, and
completed filters. Completed games disappear from the public lobby directory
but remain visible to participants and administrators. This keeps years of
campaigns navigable without loading the full archive into every browser.

Portal and control-plane lifecycle states are reconciled after a service or
host restart. A parked or completed campaign cannot silently revive a game or
Hermes worker; terminal seat state is repaired to `worker_stopped` or
`retired` before it is shown again. Large per-seat Wine prefixes and ephemeral
worker secrets are released only for completed campaigns, never parked ones.

The **Operations & recovery** page controls native-save retention. Automatic
defaults keep ten recent saves, periodic milestones, and the verified recovery
slot. Parked saves are zstd-compressed. Completing a campaign preserves one
final verified save in the persistent control archive before releasing its
worker volumes; analytics and semantic history are retained independently.

AI profile context is automatic by default. The selected model's advertised
context is used unchanged and recorded by the control plane. A manual value
must satisfy Hermes' 65,536-token minimum and may not exceed the endpoint's
advertised model limit. The portal never silently reduces it.

## 11. Analytics and reports

The Analytics page scopes ordinary users to matches they joined; administrators
see the installation. It records:

- completed/active/recoverable matches and recovery evidence;
- per-turn duration excluding errored turns;
- model/provider/reasoning/generation preset/profile identity;
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

The same page includes a separate embedding observatory for the private rules
encyclopedia and optional Graphiti memory. Purpose rows distinguish initial
wiki construction, refresh, search, memory projection, memory recall, and the
semantic quality canary. Counts, latency, effective throughput, error totals,
model/dimensions, and the embedding-space fingerprint are retained; source
text, vectors, credentials, chat, and model reasoning are explicitly not.

## 12. Campaign journal and Graphiti

Every managed AI perspective has an append-only, hash-linked campaign journal.
It records bounded strategic state, decisions, outcomes, diplomacy, notes,
checkpoint references, and lifecycle events. A small local Git repository
commits meaningful turn and checkpoint boundaries, providing inspectable
history without committing native saves, model scratch reasoning, or secrets.
The journal is authoritative for agent memory; its SQLite/FTS working index and
Graphiti graph are disposable projections that can be rebuilt from it.

Graphiti is an optional derived temporal projection and can be toggled per
match. Configure the shared embedding mode
under **Models & AI profiles**, then select a separate active extraction
profile on the Operations page. Selecting it enables Graphiti immediately;
editing it synchronizes the projector automatically; and turning Graphiti off
clears the selection. The page's structured-extraction test performs a real
non-mutating Graphiti-format request and records whether it succeeded. Then start:

```bash
./scripts/graphiti-up.sh
```

If Graphiti is disabled or unavailable, gameplay, journal memory, chat,
checkpointing, and recovery continue normally. Projection namespaces include
installation + match + agent + perspective + timeline, preventing
cross-agent/game/branch mixing. See [graphiti.md](graphiti.md) and
[storage-lifecycle.md](storage-lifecycle.md).

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

To stop the persistent stack while retaining all named volumes:

```bash
docker compose stop
```

Start them again with the normal script so Docker socket permissions and images
are validated:

```bash
SMACX_GAME_SOURCE="/absolute/path/to/Sid Meier's Alpha Centauri" \
  ./scripts/control-center-up.sh
```

Do not add `-v` to `docker compose down` unless you intentionally want to delete
portal accounts and the authoritative control state. Dynamic workers are safe
to recreate; named platform volumes are the durable system.

## Security summary

- Caddy edge: the only ordinary browser entry point—HTTP 8080 for
  localhost/trusted LAN and HTTPS 443 for a configured invited-friends hostname.
- Control API, Docker socket, MCP endpoints, Graphiti/FalkorDB, bridge tokens, and
  stream credentials: private.
- Blazor client authorization: convenience only; controllers enforce every
  account/role/seat policy server-side.
- Stream tickets: short-lived, seat-scoped, mode-scoped, revocable.
- Agent tools: semantic gameplay and web research only; lifecycle is
  operator-owned.
- Invitation-gated HTTPS ingress is supported; open registration, anonymous
  streaming, public matchmaking, and publicly forwarded DirectPlay are not.

For failures, continue with [troubleshooting.md](troubleshooting.md). For exact
trust boundaries, see [architecture.md](architecture.md).
