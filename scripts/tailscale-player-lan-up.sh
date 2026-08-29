#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${SMACX_LAN_NETWORK:?set the existing macvlan/ipvlan Docker network name}"
: "${SMACX_PLAYER_LAN_SUBNET:?set its exact IPv4 CIDR}"

python3 - "${SMACX_PLAYER_LAN_SUBNET}" <<'PY'
import ipaddress
import sys

network = ipaddress.ip_network(sys.argv[1], strict=True)
if network.version != 4 or network.prefixlen < 16 or network.prefixlen > 29:
    raise SystemExit("SMACX_PLAYER_LAN_SUBNET must be an IPv4 /16 through /29")
reserved = ipaddress.ip_network("100.64.0.0/10")
if network.overlaps(reserved) or network.is_loopback or network.is_link_local:
    raise SystemExit("SMACX_PLAYER_LAN_SUBNET overlaps a reserved transport range")
PY

network_json="$(docker network inspect "${SMACX_LAN_NETWORK}")"
python3 - "${SMACX_PLAYER_LAN_SUBNET}" "${network_json}" <<'PY'
import ipaddress
import json
import sys

expected = ipaddress.ip_network(sys.argv[1], strict=True)
record = json.loads(sys.argv[2])[0]
driver = record.get("Driver")
labels = record.get("Labels") or {}
routed = driver == "bridge" and labels.get("io.smacx.player-lan") == "true" and labels.get("io.smacx.transport") == "tailscale-routed"
if driver not in {"macvlan", "ipvlan"} and not routed:
    raise SystemExit(f"player LAN must use macvlan/ipvlan or the labeled routed bridge, got {driver!r}")
if record.get("Internal"):
    raise SystemExit("player LAN cannot be an internal Docker network")
subnets = {
    ipaddress.ip_network(item["Subnet"], strict=True)
    for item in record.get("IPAM", {}).get("Config", []) if item.get("Subnet")
}
if expected not in subnets:
    raise SystemExit(f"{expected} is not an exact subnet on the Docker network")
PY

export SMACX_VIRTUAL_LAN=tailscale
"${repo_root}/scripts/control-center-up.sh"

compose=(docker compose -f "${repo_root}/compose.yaml" -f "${repo_root}/compose.lan.yaml" -f "${repo_root}/compose.tailscale.yaml")
if ! "${compose[@]}" exec -T tailscale-router tailscale status >/dev/null 2>&1; then
  echo
  echo "The durable router is waiting for first-time Tailscale authentication."
  echo "Open the login URL shown below, approve only the advertised player-LAN route, then rerun this script."
  "${compose[@]}" logs --tail=80 tailscale-router
  exit 3
fi

echo "Encrypted player-LAN router is authenticated."
"${compose[@]}" exec -T tailscale-router tailscale status
echo "Remote players join by the exact worker IPv4 shown in the Control Center; broadcast discovery is not required."
