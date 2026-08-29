# Control Center

The Control Center is the always-on operator service. Starting or stopping a
match will not require taking this service down. Its current foundation owns:

- first-run administrator bootstrap and authenticated browser sessions;
- the authoritative SQLite identity/memory database;
- a permission-restricted local secret vault;
- OpenAI-compatible provider discovery and explicit model/context selection;
- container-validated legal game sources and checksummed private Proton imports;
- durable agent, solo-match, perspective, instance, and process-session IDs;
- isolated worker provisioning, health checks, parking, and resume;
- optional password-protected view-only spectators for each worker;
- one private MCP sidecar per running game worker;
- exact Hermes agent/match profile descriptors and host profile setup; and
- durable recurring schedules, worker/MCP supervision, native recovery
  checkpoints, and verified platform backups.

Managed LAN supports two to seven total seats and at least one agent. Seat zero
may be an agent or an explicitly named human host; every other seat may be an
isolated agent or named human player. Agent-only games stay on the private
Docker network. Mixed games are accepted only on an operator-created
non-internal macvlan/ipvlan network.

## Start once

```bash
./scripts/control-center-up.sh
docker compose exec control-center smacx-control bootstrap-token
```

Open `http://127.0.0.1:8080`, leave the username as `admin`, paste the one-time
token, and choose a password of at least 12 characters. The token is stored in
a mode-0600 file, is printed only by the explicit command above, and is deleted
after successful setup. There is no default password. Later restarts preserve
the database and secret vault in `smacx-control-data` and return directly to
the sign-in page.

The start helper reads the Docker socket group ID, builds both service images,
and starts only the always-on Control Center. It does not start a game. On a
newly configured Linux account, sign out and back in (or use `newgrp docker`)
before running it so the Docker group applies to that shell.

In **Runtime assets**, provide the directory containing the legally installed
`terranx.exe`, then provide a Proton installation directory. Validation runs
in a disposable, no-network container against a read-only bind. Proton is
copied into a checksummed named volume; its source is read-only. Create a
durable agent, select the two validated assets, choose the native faction slot,
difficulty, and map size, and provision a solo match. Starting the worker runs
the real game in its isolated virtual display and waits for the authenticated
semantic bridge to become healthy. No screenshot, mouse, or keyboard input is
part of this lifecycle.

The importer redirects Proton's otherwise distribution-local `dist.lock` into
each worker's private tmpfs. That small generated patch is included in the
runtime fingerprint and lets the shared Proton volume remain read-only without
sharing a mutable lock or installation tree across concurrent seats.

**Park** gracefully stops the game and removes only its disposable container.
The data volume, private runtime, match ID, perspective, and memory remain.
**Resume** creates a fresh native process session for that same match. This is
the intended always-on flow: the Control Center remains up while games come and
go.

## Long-running operations and recovery

The **Durability** panel is part of the always-on Control Center. It can create
recurring installation backups or per-match native checkpoints without taking
the service down. Schedule claims and finished results are durable; a finished
operation record is immutable, and concurrent service processes cannot claim
the same due run twice.

**Recovery checkpoint** requests the ordinary guarded `save_game` action from
the native bridge. It is available only while the managed agent is the real
native host and saving is currently legal. The resulting slot, turn, year, and
host instance are recorded only after the engine confirms success. If that
worker later disappears, the supervisor parks the broken process set and
resumes the recorded slot into a fresh native process session and MCP sidecar.
It never starts a new game and calls it recovery. A missing/unverified save, or
a human-owned native host, becomes an explicit operator-required incident.

An unhealthy or missing MCP sidecar is recreated without restarting its healthy
game worker. Native startup has a short reconciliation grace period, and live
volume backup holds the same operations lock as crash reconciliation; this
prevents the supervisor from mistaking an intentional startup/backup transition
for a crash.

A recovery set contains:

- a SQLite online-backup snapshot with an integrity check;
- every active mode-0600 secret when selected;
- one SHA-256-identified archive per managed worker volume when selected;
- one SHA-256-identified archive per provisioned Hermes conversation volume;
  and
- a hash-bound manifest tied to the installation ID.

Running workers and harnesses are paused at Docker's process boundary only
while their volume is archived, then unpaused in a guaranteed cleanup path.
Backup helpers have no network, mount the source read-only, run as that source
volume's private UID, and are deleted after use. Verification checks the
manifest, database, installation identity, and every worker/conversation
archive before the set is accepted.

Restore is deliberately offline and requires the exact installation ID. Stop
Control Center, then run:

```bash
docker compose stop control-center
docker compose run --rm control-center smacx-control backup list
docker compose run --rm control-center smacx-control backup verify --backup-id BACKUP_ID
docker compose run --rm control-center smacx-control restore \
  --backup-id BACKUP_ID --confirm-installation INSTALLATION_ID
docker compose up -d control-center
```

Restore first creates an emergency rollback set. It restores the authoritative
database and vault; worker and harness-volume restore is intentionally a
separate operator action because overwriting a volume is destructive and
requires every referenced process to be parked. The backup itself already
contains and verifies those archives.

Enable **view-only spectator** while provisioning a solo or LAN worker when a
human should watch that seat. **Watch** asks the authenticated Control Center
for the password, copies it to the operator clipboard when the browser permits,
and opens noVNC. `x11vnc` is started with `-viewonly`: neither the browser nor
the agent can send game input. Spectators bind to `127.0.0.1` by default. On a
trusted LAN, `SMACX_VIEW_PUBLISH_IP=0.0.0.0` publishes the randomly selected
ports; use HTTPS before doing this on any network you do not fully trust.

## Start or resume the AI player

Starting a managed game worker also starts a dedicated MCP sidecar on the same
private Docker network. The sidecar receives only that seat's bridge secret,
worker state, perspective, and authoritative SQLite scope. Its HTTP port is
published on a random loopback-only host port. It exposes all 19 semantic
gameplay/memory tools, but mechanically refuses agent requests to launch, load,
stop, or create games.

In **Run a managed Hermes player**, select the running match, exact agent seat,
model provider, and reasoning level. The Control Center checks that both the
real game worker and exact MCP sidecar are healthy, provisions the agent's
private Hermes data and provider-secret volumes, and starts the digest-pinned
official Hermes container. The browser receives the run identity and status,
never a provider credential. Stop and Resume retain the same profile and
`--continue <match-id>` conversation; the supervisor can restart bounded
process exits until the operator-specified limit.

The older host-profile adapter remains available for an unkeyed local provider
or for development. Run its command from the repository root:

```bash
./scripts/smacx-hermes configure-from-control \
  --control-url http://127.0.0.1:8080 \
  --match-id MATCH_ID --provider-id PROVIDER_ID \
  --reasoning low --start
```

The helper prompts for the Control Center password without putting it in shell
history. It creates `~/.hermes/profiles/smacx-<agent-hash>` and a separate
`workspace/matches/<match-id>` directory. The Hermes profile belongs to the
durable agent, while `--continue <match-id>` preserves a separate conversation
for every match. Its normal filesystem/terminal/computer-use tools and general
Hermes memory are disabled; match knowledge goes through scoped MCP memory.
Web lookup remains available, but in-game speech is explicitly treated as
untrusted player communication rather than operator instruction.

The existing Hermes dashboard can continue running. The new named profile is
stored under the normal Hermes profile root and can be selected there after it
has been configured; no restart of the dashboard or the legacy MCP service is
required. Parking the match removes its sidecar, so the agent receives a clear
connection failure rather than accidentally attaching to another game.

The host adapter intentionally supports only providers that do not require an
API key. The managed runtime supports keyed OpenAI-compatible providers: the
vault value is copied into a private purpose-labeled Docker volume, mounted
read-only at `/run/secrets`, and read by a tiny launcher into the Hermes
process environment. The profile stores only `key_env`; the credential is not
placed in Docker `Env`, command arguments, profile files, HTTP responses, or
browser state. Reprovisioning rotates the secret volume, including when a
profile changes from keyed to unkeyed.

The official runtime is pinned by tag and digest in `compose.yaml`. Override
`SMACX_HERMES_IMAGE` only as an explicit operator choice. The Control Center
container has Docker access; harness containers do not. They run as UID 10000
with a read-only root filesystem, all Linux capabilities dropped, no Docker
socket, and only their own data/secret volumes plus the match network.

## Managed LAN

Create at least two total seats and at least one durable agent. The native host
may be the first selected agent or an exact named external human. Every selected
agent receives its own perspective, data/secret volume, game worker, MCP
sidecar, and later Hermes profile. Joining workers use the host's exact private
IPv4 address and only a freshly returned DirectPlay session GUID.

For an agent host, the Control Center:

1. waits for every stock Multiplayer Setup lobby;
2. applies the guarded `small_easy` profile (Citizen, Small random map);
3. waits until every client observes the synchronized settings;
4. readies each client using the game's named native action;
5. starts only after the host observes every client ready; and
6. records each resulting native faction against its durable seat perspective.

The operation uses no screenshots or synthetic input. Agents may then be bound
to the same match one seat at a time by selecting both the match and agent in
the Hermes section. Their native process sessions and faction perspectives are
distinct, while memory and chat remain isolated by `(match, agent,
perspective)`.

**Park all seats** stops every disposable worker and MCP sidecar. At this
point preserves identities, memory, chat, worker volumes, and host save files.
Multiplayer checkpoint creation is semantic and host-only: the bridge identifies
the actual DirectPlay host, exposes `save_game` only on that seat, and writes
into the match-scoped directory in its persistent worker volume. Use **Resume
checkpoint…** and enter that exact slot. The Control Center opens the stock
**Load Multiplayer Game** lobby, rejoins each managed client, validates the
loaded faction binding, and starts only after every participant is ready.

### Let human players join or host

Legacy DirectPlay embeds peer addresses and cannot be reliably published by
ordinary Docker port translation. Give each game worker a real LAN address:

```bash
# Choose the parent interface, subnet, gateway, and an unused range that your
# DHCP server will never allocate. These values are examples only.
docker network create -d macvlan \
  --subnet=192.168.1.0/24 --gateway=192.168.1.1 \
  --ip-range=192.168.1.224/28 -o parent=enp3s0 smacx-player-lan

SMACX_LAN_NETWORK=smacx-player-lan \
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
./scripts/control-center-up.sh
```

The helper automatically includes `compose.lan.yaml`, attaches the always-on
Control Center to that external network, and tells dynamic workers/sidecars to
use it. This is a one-time deployment choice; creating and parking games does
not take the Control Center down.

For an AI-hosted mixed match:

1. Create the match with one agent host, optional additional agents, and one
   exact in-game name for each human.
2. The first **Start** opens and configures the native lobby and shows its LAN
   address/session name. It does not wait inside one long HTTP request.
3. Each human launches their own legal game, chooses Multiplayer TCP/IP, joins
   that address, enters the assigned name exactly, chooses their recorded
   faction when resuming, and marks Ready.
4. **Check humans & start** reads the native lobby. Unknown or duplicate names,
   missing readiness, participant-count changes, and wrong saved factions fail
   closed. Once valid, the AI host starts the game.

For a human-hosted match:

1. Choose **External human player** as Native lobby host, enter the host's exact
   player name, and select one or more agents. Additional named human clients
   are optional.
2. **Prepare now** starts only the managed agent clients. On the human's legal
   game copy, create a new TCP/IP lobby or load a multiplayer checkpoint.
3. Choose **Find human lobby**, enter the host game's reachable IPv4 address,
   and select the exact freshly discovered session if more than one exists.
4. Control Center joins every managed agent under its deterministic player
   name, restores its recorded faction in a loaded lobby, and marks it Ready.
   It validates that the expected named human really owns the native host seat.
5. The human reviews settings/seats and presses Start in the game. **Check human
   Start** observes the transition and durably binds every visible player name
   and faction; it never issues Start from an agent client.

For a human-hosted checkpoint, the human host owns the save file and reopens it
through the game's **Load Multiplayer Game** path. Parking retains every agent's
worker volume, match memory, and recorded faction. After the human reopens the
save, repeat discovery/join; each managed client must reclaim its exact saved
faction before it can Ready. This supports recovery even though the host save
is intentionally outside the platform's storage boundary.

Human menu interaction remains human input; no model screenshots, clicks, or
keyboard tools are introduced. During play, chat and paired diplomacy identify
the connected native player/faction, and each agent retains its own fair-play
perspective and memory scope.

Macvlan/ipvlan is Linux-first and depends on the physical network accepting
additional MAC/IP identities. Wi-Fi, some managed switches, VPNs, and Docker
Desktop/WSL2 may block it. Windows external-LAN deployment is therefore not yet
certified; run the Linux host or a Linux VM with bridged networking for the
predictable path.

For encrypted play between remote networks, keep those per-worker addresses
and add the durable Tailscale subnet-router overlay. It uses explicit host-IP
join rather than broadcast discovery, publishes no DirectPlay ports, and stays
authenticated across games. See [Encrypted remote player LAN](virtual-lan.md).

The default port publication is loopback-only. To listen on a trusted home LAN:

```bash
SMACX_CONTROL_PUBLISH=0.0.0.0:8080 docker compose up -d
```

Plain HTTP exposes the login exchange to devices that can observe that network.
For anything beyond a trusted LAN, place the service behind an HTTPS reverse
proxy and set `SMACX_SECURE_COOKIES=1`. Do not expose it directly to the public
Internet.

## Provider behavior

Enter the complete OpenAI-compatible base URL, normally ending in `/v1`, such
as `http://10.26.26.20:8000/v1`. Discovery requests `GET /models`. If the
endpoint advertises exactly one usable model, it becomes the selected model.
If it advertises more than one, the operator must select one. An optional
context override takes precedence over discovered context metadata.

API keys are written to the secret volume as mode-0600 files and represented
in SQLite only by a reference and SHA-256 integrity fingerprint. List and
status responses expose only `has_api_key`; they never return the value.

## Security model

- Passwords use scrypt with a unique 256-bit salt.
- Browser session and CSRF values are random; only their SHA-256 digests are
  stored in SQLite.
- Session cookies are HttpOnly and SameSite Strict. Cookie-authenticated
  mutations require a matching CSRF header and server-side digest.
- Login/setup attempts are rate-limited per source address.
- Control actions have an append-only audit table protected by SQLite triggers.
- The container runs as UID 10001 with all capabilities dropped, a read-only
  root filesystem, and writable state only in its named volume.
- Bridge bearer values are written to per-worker read-only secret volumes and
  are never placed in worker environment variables or HTTP responses.
- Every Docker object is named and labeled with the installation ID and a
  purpose. The manager refuses to mutate an object that does not carry the
  expected ownership labels.
- Game workers run as UID 10001 with a read-only root filesystem, all
  capabilities dropped, and `no-new-privileges`. The game source is always
  read-only and the Steam Proton tree is never mounted into a game worker.

The worker manager necessarily receives access to the Docker daemon and is
therefore a high-authority component despite running as a non-root Unix user.
Treat Control Center administrator access as host-administrator access. Keep
the default loopback publication, use it only on a trusted LAN, and never make
it a public Internet service. Its Docker client exposes no generic endpoint to
the browser; request schemas, resource names, labels, mounts, and lifecycle
operations are fixed in code.

## Runtime settings

The helper accepts normal Compose environment overrides:

```bash
SMACX_CONTROL_PUBLISH=0.0.0.0:8080 \
SMACX_VIEW_PUBLISH_IP=0.0.0.0 \
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
./scripts/control-center-up.sh
```

`SMACX_DIRECTX_REDIST` is an optional host path. When present, the game worker
uses it only to initialize DirectPlay inside that worker's private prefix. It
is not redistributed by this project. `SMACX_WORKER_IMAGE` and
`SMACX_DOCKER_NETWORK` may be overridden for an advanced deployment, but the
default Compose project supplies deterministic values.
