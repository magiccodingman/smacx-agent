# Encrypted remote player LAN

SMACX Agent supports a Linux-first Tailscale subnet-router deployment for
private games across the Internet. It does not publish DirectPlay or the
Control Center to the public Internet. Each game worker keeps a distinct IP on
the operator's macvlan/ipvlan player subnet, and remote Tailscale members join
the exact worker IPv4 shown by the Control Center.

This is routed Layer 3 transport, not broadcast extension. That is intentional:
the bridge already discovers or joins one explicit IPv4 and exact DirectPlay
session identity. DirectPlay 4 enumeration targets TCP 47624 and subsequent
traffic uses TCP/UDP 2300–2400, as documented by Microsoft's
[DirectPlay 4 transport specification](https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-dpl4cs/e61a374b-be48-42a1-9fd4-c7a6efbd8b4b).

## One-time Linux setup

Create an IPv4 macvlan or ipvlan network on a wired interface. Reserve its
address range outside DHCP. This example must be adapted to the real network:

```bash
docker network create -d macvlan \
  --subnet=192.168.1.0/24 --gateway=192.168.1.1 \
  --ip-range=192.168.1.224/28 -o parent=enp3s0 smacx-player-lan
```

Start the durable router and Control Center:

```bash
export SMACX_LAN_NETWORK=smacx-player-lan
export SMACX_PLAYER_LAN_SUBNET=192.168.1.0/24
export SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe
./scripts/tailscale-player-lan-up.sh
```

On first launch, the script prints Tailscale's interactive login URL. Sign in,
then approve only the exact advertised player subnet in the Tailscale admin
console. Tailscale state is stored in a named volume, so neither authentication
nor the platform needs to be recreated for each game. Rerun the command to
validate status; it is idempotent.

Remote humans install Tailscale normally, join the same tailnet, accept subnet
routes, and use the exact host worker address. Remote SMACX Agent installations
may each advertise a different, non-overlapping player subnet and use
`--accept-routes`, which the packaged router already requests. Tailscale ACLs
should restrict membership and the advertised subnet to intended players.

The deployment follows Tailscale's documented
[subnet-router](https://tailscale.com/docs/features/subnet-routers) and
[Docker](https://tailscale.com/docs/features/containers/docker/docker-params)
model. Kernel mode is required because the game needs ordinary TCP and UDP,
not an HTTP/SOCKS proxy. The router alone receives `/dev/net/tun`, `NET_ADMIN`,
and `NET_RAW`; game, MCP, harness, and Control Center containers do not.

## Validation boundary

`scripts/virtual_lan_contract_test.py` checks the digest pin, kernel tunnel,
capability boundary, durable state, exact route, absence of published ports,
and explicit-IP contract. `scripts/virtual_lan_route_live_test.sh` creates two
temporary routed subnets and passes real TCP 47624 and UDP 2350 traffic across
their router before deleting only its test resources.

Those tests certify the deployable topology and DirectPlay port path on Linux.
Completing an actual Internet game additionally depends on an operator's
Tailscale account, ACL/route approval, ISP path, and a second machine. That
external certification cannot be honestly inferred from a one-host test; use
the physical checklist in [Testing](testing.md) before labeling a particular
home-lab or Windows deployment certified.

Do not use Tailscale Funnel, public port forwarding, or a public Control Center
for gameplay. The supported remote path is a private tailnet with explicit
participants.
