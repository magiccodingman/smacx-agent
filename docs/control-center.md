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
- one private MCP sidecar per running game worker;
- exact Hermes agent/match profile descriptors and host profile setup.

The initial managed LAN path supports two to seven isolated agent seats on the
private Docker network. Human-seat publication and multiplayer save/rejoin are
the next APIs built on these contracts.

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

## Start or resume the AI player

Starting a managed game worker also starts a dedicated MCP sidecar on the same
private Docker network. The sidecar receives only that seat's bridge secret,
worker state, perspective, and authoritative SQLite scope. Its HTTP port is
published on a random loopback-only host port. It exposes all 18 semantic
gameplay/memory tools, but mechanically refuses agent requests to launch, load,
stop, or create games.

In **Bind Hermes to a running match**, select the running match, model
provider, and reasoning level. The Control Center checks that both the real
game worker and exact MCP sidecar are healthy before returning a secret-free
descriptor. Run its generated command from the repository root:

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

The initial host adapter supports providers that do not require an API key,
including a trusted local OpenAI-compatible endpoint. Control Center never
returns stored provider keys. Keyed remote providers will be enabled later by
mounting the exact secret directly into a contained harness runtime instead of
exposing it through the browser API.

## Managed agent LAN

Create at least two durable agents, then use **Create an agent LAN match**.
Every selected agent receives its own perspective, data/secret volume, game
worker, MCP sidecar, and later Hermes profile. Seat zero is the native host;
the other workers discover the host by its exact private IPv4 address and join
only the freshly returned DirectPlay session GUID. The Control Center then:

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
milestone, starting a parked LAN creates a fresh native lobby/game rather than
restoring the old campaign. The bridge deliberately reports multiplayer save
as unsupported until the stock host-save/client-rejoin effect path has a real
two-process regression. The UI therefore must not describe LAN parking as a
campaign resume. External human clients likewise cannot yet join the private
Docker-only host; that will require a declared macvlan/host-network/virtual-LAN
publication mode with its own security and compatibility tests.

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
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
./scripts/control-center-up.sh
```

`SMACX_DIRECTX_REDIST` is an optional host path. When present, the game worker
uses it only to initialize DirectPlay inside that worker's private prefix. It
is not redistributed by this project. `SMACX_WORKER_IMAGE` and
`SMACX_DOCKER_NETWORK` may be overridden for an advanced deployment, but the
default Compose project supplies deterministic values.
