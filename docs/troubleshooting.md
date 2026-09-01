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

## Compatibility image build fails

The worker image downloads checksum-pinned GE-Proton and DirectPlay assets from
their upstream locations. No host Proton import is required. If the build
cannot fetch or verify either asset, confirm outbound HTTPS, available disk,
and that a filtering proxy is not replacing downloads:

```bash
docker system df
df -h
```

Never bypass a digest mismatch or mount Steam's live Proton runtime read-write
into workers. A changed upstream artifact requires a reviewed pin update and
the live compatibility suite.

## LAN hosting returns to the main menu

DirectPlay is missing or not registered in the isolated prefix. Rebuild the
worker with the normal launcher and inspect the failed seat's logs. The image
verifies the archived original Microsoft redistributable before installing its
32-bit components, and the worker must report `directplay_ready` before native
hosting begins.

Stock Debian Wine is only a diagnostic fallback; it launches the game but the
semantic bridge does not complete its authenticated opening. Use the sealed
GE-Proton runtime selected by the platform.

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

## Game is too small, clipped, or the wrong aspect ratio

Open the native root **MENU**, then open the managed control rail's **Display**
tab. **Fit browser now** is immediate and does not pause the match. Automatic
mode recommends 800×600 for a small touch screen, 1024×768 for a tablet, or a
larger profile for the available desktop pixels. Fullscreen landscape is the
most readable phone layout.

**Apply natively** is different: it waits for a verified stable checkpoint and
may require connected-player approval. While waiting, keep playing; the portal
will not restart the game in the middle of an unsafe interaction. In
multiplayer, another native resolution request inside five minutes needs a
separate cooldown-waiver vote. The device profile lock is local to that
browser—turn it off if a prior monitor's preference is inappropriate.

If an 800×600 worker is clipping a 1024×768 game, inspect `thinker.ini` inside
the worker. Current images must contain `video_mode=1`, `window_width=800`, and
`window_height=600`; rebuild the worker image if it predates managed profiles.

## Keyboard or mouse does nothing

Confirm the stream page says interactive/Play rather than observer/Watch. Only
the seat member or administrator controlling their own assigned seat receives
an interactive ticket. Anonymous and cross-seat observation are always
read-only at the worker transport.

Click once inside the stream to focus it. Browser-reserved shortcuts may not be
forwarded; ordinary game shortcuts and text input are.

If the same seat is open in two tabs, only one is the controller. The other
shows a read-only card. Select **Take control here** to move input deliberately;
the previous stream connection is revoked and its page becomes view-only. If a
tab was suspended for more than 30 seconds, reload or reacquire control.

## Install app is unavailable

Open the portal's **Install app** page. A host browsing `localhost` or
`127.0.0.1` qualifies for the loopback secure-context exception. A phone,
tablet, or another LAN computer needs a trusted HTTPS URL; plain
`http://HOST_LAN_IP` can use the portal but browsers need not offer PWA
installation there.

Chromium exposes a one-use install event. If it was dismissed, use the browser
menu instructions on the page or revisit after the browser offers it again.
Safari/iOS does not expose the same event and uses **Share → Add to Home
Screen**. Installation never makes live matches available offline.

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

## A parked/completed campaign appears active after a restart

Run the current images and allow the portal supervisor one startup cycle:

```bash
./scripts/control-center-up.sh
docker compose logs --tail=150 control-api control-center
```

The supervisor reconciles durable portal state into the control plane and
stops stale Hermes/game workers. Do not manually launch an old harness run or
delete the portal/control volumes. A parked campaign should show
`worker_stopped` and remain resumable; a completed campaign should show
`retired` and remain review-only in the Campaign Library.

## Browser player is disconnected or temporarily computer-controlled

Closing or refreshing the page leaves the faction reserved. After the browser
has been absent for 30 seconds, another connected human can propose temporary
stock-AI control from the Session tab. The proposal does nothing until the peer
vote and stable checkpoint both pass. When the player returns, use **Propose
player reclaim**; the platform rehosts from another verified checkpoint and
returns the same faction.

Use the rail or Session tab's **Exit game view** command. Browser back, refresh,
close, and external navigation display a leave warning. If a player selects the
game's own Quit/Exit command, the exact `REALLYQUIT` popup is canceled
semantically and the portal explains the managed alternatives. If a different
or rare native path still returns to the main menu, the supervisor records
`returned_to_menu` and recovers all managed seats from the latest verified
checkpoint. A match with no verified checkpoint stops for operator review
instead of discarding unsaved actions.

When every human seat is browser-managed and all streams are absent, the lobby
shows a ten-minute countdown. Any reconnect cancels the idle condition. At the
end, the supervisor waits for a verified checkpoint and parks the campaign.
AI-only matches continue, while matches containing direct/native humans do not
use browser presence as proof that everyone left.

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
`smacx`; the MCP surface contains no web/computer/screenshot/mouse/keyboard or
terminal tools. Stop the run and inspect its assigned profile/container
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

Token telemetry begins when a managed Hermes profile advances an
observed turn. It is not estimated for stock bots, pure humans, or legacy
external harnesses. Check for `harness.telemetry` in control logs and verify the
Hermes data volume still exists.

The reader is a short-lived `harness-telemetry` helper. It should exit/remove
immediately, have no network, and mount the matching harness-data volume
read-only. A persistent telemetry helper is a regression.

## Graphiti is unavailable

This is nonfatal. SQLite and scoped FTS5/BM25 match memory remain authoritative.
Choose an active extraction AI profile under Operations; under **Models & AI
profiles**, keep the default shared local embedding runtime or configure one
compatible external embedding space before:

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
