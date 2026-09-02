# Windows and WSL2 deployment

The game executable is Windows-only, but the managed runtime is intentionally
Linux: the worker image runs the user's legal files with Wine/Proton. On a
Windows 11 machine, WSL2 plus a Linux Docker engine supplies that environment;
Docker does not convert the executable itself.

## Supported architecture

Use an x86-64 WSL2 distribution with either Docker Desktop's WSL integration
or Docker Engine installed inside that distribution. Run every repository and
Control Center command from the same WSL distribution. The browser opens the
Control Center and optional view-only spectator through `localhost`; no agent
receives pixels or desktop input.

Point source validation at a legal game directory. A Windows Steam/GOG path is
visible under `/mnt/c`, but copying the installed directory into WSL's ext4
filesystem gives more predictable permissions and I/O. The platform mounts
the source read-only and stages a private worker copy; it never ships the game.
Run the preflight before setup:

```bash
python3 scripts/platform_preflight.py --require-wsl2 \
  --game-path /mnt/c/Program\ Files\ \(x86\)/Steam/steamapps/common/Alpha\ Centauri
```

It checks WSL2, a Linux Docker engine, Compose v2, x86-64, `/dev/net/tun`, the
legal executable, and an actual read-only Docker bind of that path. GE-Proton
and the bundled DirectPlay redistributable are built into the worker image. It
changes no game files.

Then run `scripts/control-center-up.sh` for solo or same-host AI games. Visit
`http://localhost:8080`, use the one-time token printed by the bootstrap
instructions, configure an optional model endpoint, create AI profiles if
wanted, and leave the Control Center running across games.

## Human and remote multiplayer

Docker Desktop does not support Linux macvlan. Create a dedicated labeled
bridge and advertise only that subnet through the packaged, firewalled
Tailscale router:

```bash
export SMACX_LAN_NETWORK=smacx-routed-player-lan
export SMACX_PLAYER_LAN_SUBNET=172.29.50.0/24
./scripts/create-routed-player-lan.sh
```

The router permits inbound tailnet forwarding only to DirectPlay TCP 47624 and
TCP/UDP 2300–2400; other routed access to Control Center/MCP/container ports is
rejected. Its state volume persists authentication. Approve the exact subnet
route in Tailscale, then join the worker IPv4 displayed by the Control Center.

WSL2 networking and Docker Desktop versions change independently. Run the
packaged preflight on the target host and confirm that its routed DirectPlay
path and private portal are reachable before starting a campaign. Treat failed
preflight or route checks as a host-configuration problem rather than bypassing
them.
