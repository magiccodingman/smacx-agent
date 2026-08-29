#!/bin/sh
set -eu

: "${SMACX_PLAYER_LAN_SUBNET:?missing player LAN subnet}"

install_firewall() {
    attempts=0
    until ip link show tailscale0 >/dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 120 ]; then
            echo "tailscale0 did not appear; DirectPlay forward policy was not installed" >&2
            return 1
        fi
        sleep 1
    done
    # Insert the broad reject first, then narrower accepts at position one.
    # The final order permits established replies and only DirectPlay 4's
    # enumeration/gameplay ports from the encrypted interface.
    iptables -I FORWARD 1 -i tailscale0 -d "$SMACX_PLAYER_LAN_SUBNET" -j REJECT
    iptables -I FORWARD 1 -i tailscale0 -d "$SMACX_PLAYER_LAN_SUBNET" -p tcp --dport 47624 -j ACCEPT
    iptables -I FORWARD 1 -i tailscale0 -d "$SMACX_PLAYER_LAN_SUBNET" -p tcp --dport 2300:2400 -j ACCEPT
    iptables -I FORWARD 1 -i tailscale0 -d "$SMACX_PLAYER_LAN_SUBNET" -p udp --dport 2300:2400 -j ACCEPT
    iptables -I FORWARD 1 -s "$SMACX_PLAYER_LAN_SUBNET" -o tailscale0 \
        -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
}

install_firewall &
exec /usr/local/bin/containerboot
