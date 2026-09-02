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
forces `COMPOSE_PARALLEL_LIMIT=1` and serializes them. If the kernel terminates
a build for lack of memory, increase the host's available memory or swap and
rerun the launcher. Runtime demand is lower than a full source build.

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

## Network and Internet access

First identify the route that is failing:

- host: `http://127.0.0.1:8080`;
- trusted LAN: `http://HOST-LAN-IP:8080`;
- invited Internet: `https://PUBLIC-HOSTNAME`; or
- native DirectPlay: the private physical/virtual player LAN.

These routes have different requirements. Review [Network access and play
modes](network-access.md) before changing firewall rules.

### A LAN device cannot open the portal

Confirm the edge listens on all host interfaces and the host has the expected
private address:

```bash
docker compose ps edge control-center
hostname -I
curl --fail http://127.0.0.1:8080/healthz
```

Use `http://`, the host's LAN address, and port 8080. Allow TCP 8080 from the
trusted LAN in the host firewall. Do not solve a LAN problem by forwarding 8080
on the Internet router.

### The public hostname does not connect

Check each layer in order:

```bash
getent ahostsv4 planet.example.net
docker compose ps edge ddns control-center
docker compose logs --tail=200 edge ddns
curl --fail --show-error https://planet.example.net/healthz
```

Replace the example hostname. Its A record must resolve to the current public
IPv4 address. An AAAA record must be removed unless public IPv6 actually reaches
the host. The router must forward public TCP 443 to the Docker host's TCP 443,
and the Linux firewall must allow it.

Test from a phone with Wi-Fi disabled. Some routers cannot loop their public
hostname back into the LAN even though remote access works.

Compare `curl -4 https://icanhazip.com` with the router's WAN IPv4. A mismatch
usually means double NAT or carrier-grade NAT. Forward through both privately
owned routers, or request a public address from the ISP. Ordinary port
forwarding cannot cross ISP carrier-grade NAT.

Never forward TCP 8080, DirectPlay TCP 47624/TCP+UDP 2300–2400, worker stream
ports, control/MCP/database ports, or the Docker socket. Remote browser play
needs only the Caddy HTTPS edge on TCP 443.

### Caddy cannot obtain a certificate

Confirm that `SMACX_PUBLIC_HOSTNAME` contains only the hostname—no scheme, port,
path, or trailing slash—and that public DNS already points at this host. Then:

```bash
./scripts/control-center-up.sh
docker compose logs --tail=250 edge
```

Caddy's data volume persists certificates and renewal state. Do not delete it
while troubleshooting. A DNS record pointing elsewhere, blocked inbound TCP
443, another service occupying host port 443, or a stale AAAA record are the
usual causes.

### Dynamic DNS does not update

```bash
docker compose logs --since=30m ddns
ls -l runtime/edge-secrets/ddns-token
```

The selected provider, hostname, and token must all be present. The token file
must contain the provider's update credential and be readable by the configured
secret group. Keep the secret out of `.env` and logs. Verify the result with
`getent ahostsv4 PUBLIC-HOSTNAME` after DNS caches refresh.

### An invitation is invalid or opens only a local address

An Internet invitation should begin with the configured `https://` public
hostname. If **Administration → Network access** says no public hostname is
configured, fix `.env` and restart before creating the invitation.

Invitations are single-use, expire after 24 hours, and may be revoked. Create a
new invitation rather than attempting to reuse its secret. The complete link,
including the `#invite=...` fragment, must be preserved.

### Remote sign-in asks for installation verification

That is expected once per account. Use a desktop browser and select the
directory that directly contains `terranx.exe`. The browser hashes the selected
files locally; game content is not uploaded. After success, that account can log
in from phones, tablets, and other browsers without repeating the check.

An administrator may approve an otherwise legitimate unrecognized release from
**Administration → Players**. Do not disable or weaken the remote verification
boundary to work around one unsupported fingerprint.

### A LAN user is treated as remote

Check **Administration → Network access** for the address the portal classified
as trusted. Routed private networks outside the defaults must be added explicitly
to `SMACX_TRUSTED_NETWORKS` as CIDRs. If another reverse proxy sits in front of
Caddy, also configure only that real proxy network in
`SMACX_TRUSTED_PROXY_NETWORKS`; never trust arbitrary forwarded headers.

For the complete host sequence, continue with [Internet hosting for invited
friends](internet-hosting.md). For the player sequence, use [Joining a SMACX
Agent server](joining-a-server.md).

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
an interactive ticket. Unauthenticated users receive no stream ticket;
authenticated spectator and cross-seat observation tickets are always
read-only at the worker transport.

Click once inside the stream to focus it. Browser-reserved shortcuts may not be
forwarded; ordinary game shortcuts and text input are.

### A LAN stream asks for HTTPS

Refresh the play or spectator page after updating the platform. Plain
`http://HOST_LAN_IP:8080` automatically uses JPEG/WebSocket video because
WebCodecs is unavailable on a non-loopback HTTP origin. The stream remains
interactive for a player and read-only for a spectator. A small portal notice
explains that game audio and PWA installation still require the configured
trusted HTTPS address. Seeing Selkies' old fatal “requires a secure connection”
message means the active worker predates this compatibility build; park and
resume that campaign, or recreate the worker from the current image.

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
cannot. Test the displayed worker address from the actual client device.

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
action. Mutation latches closed for that process session. Before normal harness
reconciliation can continue the conversation, the operations supervisor:

1. creates a durable `operator_required` incident scoped to the exact match,
   native session, instance, and gap ID;
2. stops the affected Hermes run and disables automatic restart;
3. leaves the native worker at the failing state for operator inspection; and
4. creates a bounded diagnostic ZIP in the persistent control volume.

Lobby and managed-player pages show **The AI stopped safely**. Download the ZIP,
open the prefilled issue at
<https://github.com/magiccodingman/smacx-agent/issues>, and attach the file. The
bundle contains the redacted gap, environment fingerprints, match settings,
pseudonymized seat map, bounded semantic/runtime logs, checksums, and up to three
newest distinct managed saves when available. It deliberately excludes game
binaries and assets, credentials, private provider addresses and filesystem
paths, user/account data, chat, and full model reasoning.

GitHub does not permit the local portal to attach a downloaded file to a new
issue automatically. Drag the ZIP into the issue after the prefilled page opens.
Add a typed adapter with fair-state and rejection tests, rebuild, then recover
into a fresh native session. A capability gap is expected fail-closed behavior,
not permission to fall back to screenshots or mouse input.

## Cleanup

Stop persistent services without deleting data:

```bash
docker compose stop
```

Avoid `docker compose down -v`. Dynamic worker resources are ownership-labeled
and are parked/removed by lifecycle operations. Manually deleting arbitrary
`smacx-*` volumes can destroy saves, memory, or Hermes conversations.
