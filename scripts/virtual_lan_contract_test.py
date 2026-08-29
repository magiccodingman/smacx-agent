#!/usr/bin/env python3
"""Contained validation of routed DirectPlay transport configuration."""

from __future__ import annotations

import json
from pathlib import Path
import re


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    overlay = (root / "compose.tailscale.yaml").read_text(encoding="utf-8")
    launcher = (root / "scripts/tailscale-player-lan-up.sh").read_text(encoding="utf-8")
    firewall = (root / "transport/tailscale-router-entrypoint.sh").read_text(encoding="utf-8")
    dockerfile = (root / "transport/Dockerfile").read_text(encoding="utf-8")
    required = {
        "digest_pinned_image": bool(re.search(r"tailscale:stable@sha256:[a-f0-9]{64}", dockerfile)),
        "kernel_tun": "TS_USERSPACE: \"false\"" in overlay and "/dev/net/tun:/dev/net/tun" in overlay,
        "bounded_caps": "NET_ADMIN" in overlay and "NET_RAW" in overlay,
        "persistent_identity": "smacx-tailscale-state:/var/lib/tailscale" in overlay,
        "exact_route": "TS_ROUTES: ${SMACX_PLAYER_LAN_SUBNET" in overlay,
        "no_published_ports": "ports:" not in overlay,
        "accept_remote_routes": "--accept-routes" in overlay,
        "network_guard": "macvlan" in launcher and "ipvlan" in launcher,
        "explicit_ip_join": "exact worker IPv4" in launcher,
        "directplay_only_firewall": all(value in firewall for value in (
            "--dport 47624", "--dport 2300:2400", "-j REJECT",
        )),
    }
    if not all(required.values()):
        raise AssertionError(required)
    print(json.dumps({"event": "pass", "payload": {
        **required,
        "directplay_transport": {
            "enumeration": "TCP 47624 to exact host IPv4",
            "gameplay": "TCP/UDP 2300-2400",
            "broadcast_required": False,
        },
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
