# Linux game worker

One worker runs exactly one user-supplied Alien Crossfire installation in
one private compatibility prefix and Xvfb display. The image builds the
open-source semantic bridge but contains no game assets, Microsoft
redistributables, saves, or credentials.

Required mounts and secrets:

- `/game-source:ro`: an installation containing `terranx.exe` and `alphax.txt`.
- `/var/lib/smacx`: a unique writable volume for this worker's copied game, Wine prefix, saves, logs, and metadata.
- `/run/secrets/bridge-token`: unique bridge credential, at least 16 characters,
  selected with `SMACX_AGENT_TOKEN_FILE`. An environment value is accepted only
  as a development fallback.
- exact `SMACX_AGENT_MATCH_ID`, `SMACX_AGENT_SESSION_ID`, `SMACX_AGENT_ID`, `SMACX_PERSPECTIVE_ID`, and `SMACX_INSTANCE_ID` values assigned by the control plane.

The first start validates and copies the read-only game directory. A changed source executable requires a new volume; it is never silently merged over an existing worker. The bridge binaries are overlaid from the image after import.

For native LAN, mount Microsoft's February 2010 DirectX redistributable at `/redist/directx_feb2010_redist.exe:ro`. Its SHA-256 is verified before DirectPlay files are installed. Set `SMACX_REQUIRE_DIRECTPLAY=1` when a missing redistributable must fail startup.

The image includes stock Wine as an explicitly uncertified fallback. The
certified Linux runtime is a Proton compatibility bundle mounted at `/proton`, with
`SMACX_PROTON_BIN=/proton/proton`. The worker invokes Proton's `runinprefix`
contract so prefix preparation and bundled DLL search paths are preserved
without requiring a Steam client inside the worker. A
Proton distribution needs a writable `dist.lock`; do not give a worker write
access to Steam's installation. The Control Center runtime manager owns a
private, checksummed runtime copy. No Proton bundle is copied into this
repository or image.

Workers default to a `win64` prefix because Proton runs this 32-bit game through
WoW64 and native DirectPlay belongs in `syswow64`. The prefix architecture is
recorded in the private volume and cannot be changed in place.

The DLL remains loopback-only. `socat` exposes it as port 47814 only to the
private container network; every request still requires the unique token.

When `SMACX_VIEW_ENABLE=1`, Selkies is the primary single-port browser stream.
It carries video, audio, and—only for `SMACX_VIEW_MODE=interactive`—ordinary
human input. `view-only` disables input at the worker transport. A generated
credential is mounted through the configured secret file and never placed in
the container environment. The Blazor portal authenticates the user, obtains a
short-lived seat-scoped access descriptor from control, and proxies HTTP plus
WebSocket traffic; users should not connect to the random worker port directly.
noVNC remains a view-only fallback if Selkies cannot start. Agents never receive
stream credentials or input tools.

Each worker has a private display, so multiple workers cannot see or click one another's windows. Real DirectPlay LAN games should give each worker its own LAN-reachable IP through macvlan/ipvlan or an equivalent virtual-LAN network. Ordinary Docker port translation is not assumed to preserve legacy DirectPlay addressing.
