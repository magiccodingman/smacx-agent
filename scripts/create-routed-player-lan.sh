#!/usr/bin/env bash
set -euo pipefail

: "${SMACX_LAN_NETWORK:=smacx-routed-player-lan}"
: "${SMACX_PLAYER_LAN_SUBNET:?set a dedicated, non-overlapping IPv4 CIDR}"

python3 - "${SMACX_PLAYER_LAN_SUBNET}" <<'PY'
import ipaddress
import sys
network = ipaddress.ip_network(sys.argv[1], strict=True)
if network.version != 4 or network.prefixlen < 16 or network.prefixlen > 28:
    raise SystemExit("player subnet must be an IPv4 /16 through /28")
for reserved in (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("169.254.0.0/16")):
    if network.overlaps(reserved):
        raise SystemExit(f"player subnet overlaps reserved range {reserved}")
PY

if docker network inspect "${SMACX_LAN_NETWORK}" >/dev/null 2>&1; then
  echo "Docker network already exists; validating it without changing it."
else
  docker network create --driver bridge \
    --subnet "${SMACX_PLAYER_LAN_SUBNET}" \
    --label io.smacx.player-lan=true \
    --label io.smacx.transport=tailscale-routed \
    "${SMACX_LAN_NETWORK}" >/dev/null
  echo "Created dedicated routed player LAN: ${SMACX_LAN_NETWORK}"
fi

export SMACX_LAN_NETWORK SMACX_PLAYER_LAN_SUBNET
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/tailscale-player-lan-up.sh"
