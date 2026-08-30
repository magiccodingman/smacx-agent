# Troubleshooting

Start with the persistent services:

```bash
docker compose ps
docker compose logs --tail=150 control-api control-center
curl --fail http://127.0.0.1:8080/healthz
```

Do not delete named volumes to fix an ordinary launch problem. They contain the
authoritative installation/match state and portal accounts.

## Docker permission denied / engine unavailable

The host user must access Docker:

```bash
groups
docker ps
stat -c '%g %a %n' /var/run/docker.sock
```

After adding the user to the Docker group, start a new login shell (or
`newgrp docker`). Always start the platform through:

```bash
./scripts/control-center-up.sh
```

That script exports the socket's numeric group ID for the non-root control
container. A manual bare `docker compose up` may recreate it with group `0` and
produce `docker engine unavailable`. If manually recreating:

```bash
SMACX_DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
export SMACX_DOCKER_GID
docker compose up -d --force-recreate control-api control-center
```

## The machine freezes during build

Check the previous boot for OOM evidence:

```bash
journalctl -k -b -1 | grep -Ei 'oom|out of memory|killed process'
free -h
```

Do not run control, worker, and portal builds concurrently. The launcher now
forces `COMPOSE_PARALLEL_LIMIT=1` and serializes them. A known failure occurred
with 8 GiB RAM/1 GiB swap during concurrent Docker/Blazor optimization. The
verified continuation VM has 16 GiB RAM and 16 GiB swap. Around 12 GiB RAM plus
4 GiB swap is a sensible source-build target.

## First-run token is unknown

```bash
docker compose exec -T control-center dotnet Smacx.Portal.dll bootstrap-token
```

It prints the existing one-time token only while setup remains incomplete. The
token is not a password and is revoked after bootstrap.

## Administrator password is lost

```bash
docker compose exec -T control-center dotnet Smacx.Portal.dll admin-reset-token admin
```

Use the printed 30-minute ticket on the Reset access page. This keeps matches,
profiles, analytics, and accounts intact.

## Portal loads but authenticated pages loop or look empty

Confirm both services use the current images and the browser is not holding an
old WebAssembly asset:

```bash
./scripts/control-center-up.sh
```

Then hard reload the page. Authenticated WebAssembly routes deliberately use
no prerender so cookie state is resolved in the browser. Check the browser
console and `control-center` logs for a failed API response.

## Game installation validation fails

Use the absolute directory that directly contains `terranx.exe`. The validator
rejects symlinked trees, missing/non-PE executables, and unsafe paths. It does
not download or repair the game.

The source is mounted read-only. If a mod changes files, validate it as a new
source so fingerprints and private mechanics knowledge remain distinct.

## Proton import fails

Point at the root of a complete Proton distribution, not a running Steam prefix.
The manager copies it to an installation-owned writable volume because Proton
uses `dist.lock` and mutates prefix state. Ensure the source has enough readable
files and Docker has disk space:

```bash
docker system df
df -h
```

Do not mount Steam's live Proton runtime read-write into workers.

## LAN hosting returns to the main menu

DirectPlay is missing or not registered in the isolated prefix. Confirm the
startup environment points at the official February 2010 redistributable and
re-import/recreate the affected worker runtime through the portal. The project
verifies the archived redistributable before installing its 32-bit components.

Stock Debian Wine is only a diagnostic fallback; the reference game stalled at
the Firaxis presentation screen there. Use imported Proton.

## Browser stream is blank or reconnecting

Check the seat worker and portal proxy:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker compose logs --tail=150 control-center
```

The portal must proxy both HTTP and WebSocket paths; never browse directly to a
worker's random port. A `Watch` spectator ticket cannot accept input by design.
Use the assigned seat's **Play** link for interactive control.

If the game process is healthy but the browser was suspended, reload the stream
page. The same managed seat/worker remains assigned. If the worker crashed,
use **Recover** only when the lobby reports a verified checkpoint.

## Keyboard or mouse does nothing

Confirm the stream page says interactive/Play rather than observer/Watch. Only
the seat member or administrator controlling their own assigned seat receives
an interactive ticket. Anonymous and cross-seat observation are always
read-only at the worker transport.

Click once inside the stream to focus it. Browser-reserved shortcuts may not be
forwarded; ordinary game shortcuts and text input are.

## Human native client cannot see the lobby

The default private Docker bridge is intentionally not reachable from the
physical LAN. External/native seats require a labeled macvlan/ipvlan or routed
player network supplied as `SMACX_LAN_NETWORK` and
`SMACX_PLAYER_LAN_SUBNET`.

Validate:

- the lobby page's host address belongs to the intended LAN subnet;
- the address is outside DHCP conflicts;
- host firewall allows the LAN path;
- the client uses TCP/IP multiplayer and the exact displayed session/handle;
- every name is unique and matches case-insensitively; and
- a resumed player selects the recorded faction.

Wi-Fi often refuses extra macvlan MAC addresses. Prefer ipvlan or
`scripts/create-routed-player-lan.sh` on that host. The Linux host cannot always
reach its own macvlan children; this does not imply another physical LAN client
cannot, but the deferred physical certification must test it.

## Direct/native player disconnects

Legacy DirectPlay may not transparently continue after a native participant
leaves. Create/use a verified checkpoint, park the match, ask every native
participant to rejoin the restored lobby with their exact handle/faction, then
continue. Managed browser reconnect normally does not require a native restart.

## AI profile will not start

Check:

- provider health/model discovery in Administration;
- the selected profile is active;
- endpoint is reachable from Docker, not merely the host browser;
- API key exists when required;
- the exact match seat has a managed worker/MCP; and
- no other live harness run owns the same match+agent seat.

```bash
docker compose logs --tail=200 control-api
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

Managed AI does not depend on the host Hermes dashboard. Restarting or killing
`hermes dashboard` is normally irrelevant.

## AI repeatedly tries visual clicks

That is a configuration regression. Managed runs must show toolsets
`smacx,web`; the MCP surface contains no computer/screenshot/mouse/keyboard or
terminal tools. Stop the run and inspect its versioned profile/container
configuration. Do not “fix” a semantic gap by granting desktop tools.

For the legacy host-local development MCP only, start a fresh Hermes
conversation after a tool schema change so discovery is refreshed.

## Park reports “native checkpoint not currently legal”

The stock engine is in a native phase/modal where saving is illegal. The safe
park sequence stops Hermes first, then attempts the checkpoint; if saving fails,
the worker is retained and the match returns to `running` with `park_failed`.

Resolve or wait out the mandatory semantic state and retry. Do not destroy the
worker or force a filesystem copy as a substitute for a native save.

If a park call ever creates a replacement Hermes run during checkpoint, treat
it as a regression: the portal must hold status `parking`, and the control API
also stops active runs before worker teardown.

## Recover is unavailable

Recovery requires a verified native checkpoint. Inspect Match history and the
lobby error. A process crash before the first legal checkpoint cannot be
invented into a safe save.

Recovery starts a new process/session/revision. Agent commands captured before
the crash must be discarded; Hermes continues its durable conversation but
must call a fresh observation.

## Turn/year or faction is blank

The supervisor polls a managed healthy worker at a throttled cadence because
native reads share the bridge's serialized request slot with the agent. Wait a
few seconds and refresh. The endpoint should report HTTP 200 in control logs:

```text
GET /api/v1/matches/<match-id>/status
```

Faction is populated from that seat's fair semantic snapshot. External human
factions become authoritative after native lobby/game binding.

## Analytics show zero tokens

Token telemetry begins when a versioned managed Hermes profile advances an
observed turn. It is not estimated for stock bots, pure humans, or legacy
external harnesses. Check for `harness.telemetry` in control logs and verify the
Hermes data volume still exists.

The reader is a short-lived `harness-telemetry` helper. It should exit/remove
immediately, have no network, and mount the matching harness-data volume
read-only. A persistent telemetry helper is a regression.

## Graphiti is unavailable

This is nonfatal. SQLite and FTS5/BM25 remain authoritative. Confirm compatible
chat and embedding endpoints before:

```bash
./scripts/graphiti-up.sh
```

Check `graphiti-db` and `graphiti-projector` logs. Do not point multiple
installations at one namespace without the installation/match/agent/perspective
isolation enforced by the projector.

## The game reaches an unsupported mandatory state

Do not use vision or UI input. The agent calls `smac_report_capability_gap` once
with the native label/state, intended decision, missing observation, and missing
action. Mutation latches closed for that process session.

Capture the match/session/revision and bridge diagnostics, add a typed adapter
with fair-state and rejection tests, rebuild, then recover into a fresh native
session. The capability gap is expected fail-closed behavior.

## Cleanup

Stop persistent services without deleting data:

```bash
docker compose stop control-center control-api
```

Avoid `docker compose down -v`. Dynamic worker resources are ownership-labeled
and are parked/removed by lifecycle operations. Manually deleting arbitrary
`smacx-*` volumes can destroy saves, memory, or Hermes conversations.
