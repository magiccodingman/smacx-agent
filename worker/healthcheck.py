#!/usr/bin/env python3
"""Container health check for the proxied authenticated semantic bridge."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path


def secret_value(name: str) -> str:
    file_name = os.environ.get(f"{name}_FILE", "")
    if file_name:
        try:
            with open(file_name, encoding="utf-8") as stream:
                return stream.read().strip()
        except OSError:
            return ""
    return os.environ.get(name, "")


def main() -> int:
    # The entrypoint owns native bridge startup. Probing the forking socat
    # proxy before that handshake completes can leave one proxy child per
    # timed-out probe and overwhelm a slow first-run Wine prefix.
    ready_marker = Path(os.environ.get(
        "SMACX_READY_MARKER", "/tmp/smacx/bridge-ready",
    ))
    if not ready_marker.is_file():
        return 1
    token = secret_value("SMACX_AGENT_TOKEN")
    port = int(os.environ.get("SMACX_BRIDGE_PROXY_PORT", "47814"))
    if len(token) < 16:
        return 1
    request = json.dumps({"op": "ping", "token": token}, separators=(",", ":")).encode() + b"\n"
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            connection.sendall(request)
            response = connection.recv(4096)
        return 0 if json.loads(response).get("ok") else 1
    except (OSError, json.JSONDecodeError, ValueError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
