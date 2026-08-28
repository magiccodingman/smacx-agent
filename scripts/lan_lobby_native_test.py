#!/usr/bin/env python3
"""Contained semantic DirectPlay host-to-stock-lobby probe."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from smacx_controller import bridge_request, launch_game


def main() -> int:
    os.environ["SMACX_AGENT_TEST_MODE"] = "1"
    os.environ["SMACX_AGENT_TEST_LAN_HOST"] = "1"
    launched = launch_game(wait_seconds=45)
    if not launched.get("ok"):
        raise AssertionError(f"menu launch failed: {launched}")

    hosted = bridge_request(
        "test_lan_host_fixture",
        timeout=60,
        session_name="SMACX Agent Lobby Test",
        player_name="Semantic Host",
        max_stage=5,
        launch_lobby=True,
    )
    if hosted.get("error", {}).get("code") == "game_timeout":
        screenshot = Path(__file__).resolve().parents[1] / "runtime" / "lan-lobby-modal.png"
        subprocess.run(
            ["gnome-screenshot", "-f", str(screenshot)],
            check=False,
            timeout=10,
        )
        deadline = time.monotonic() + 8
        late = {}
        while time.monotonic() < deadline:
            late = bridge_request(
                "test_lan_host_fixture", timeout=15, action="status",
            )
            if late.get("completed_stage") == 5:
                hosted = {
                    "ok": True,
                    "lobby_launch_queued": (
                        late.get("preconnected")
                        or late.get("lobby_pending")
                        or late.get("multiplayer_active")
                    ),
                    "native_player_id": None,
                }
                break
            time.sleep(1)
    if not hosted.get("ok") or not hosted.get("lobby_launch_queued"):
        raise AssertionError(f"host bootstrap failed: {hosted}")

    deadline = time.monotonic() + 20
    state = {}
    while time.monotonic() < deadline:
        state = bridge_request("status", timeout=10)
        if state.get("state", {}).get("multiplayer_active"):
            break
        time.sleep(0.5)
    if not state.get("state", {}).get("multiplayer_active"):
        raise AssertionError(f"stock multiplayer lifecycle did not activate: {state}")

    screenshot = Path(__file__).resolve().parents[1] / "runtime" / "lan-lobby-active.png"
    subprocess.run(
        ["gnome-screenshot", "-f", str(screenshot)],
        check=False,
        timeout=10,
    )

    print(json.dumps({
        "event": "pass",
        "native_player_id": hosted.get("native_player_id"),
        "stock_multiplayer_active": True,
        "state": state.get("state"),
        "match_id": state.get("identity", {}).get("match_id"),
        "session_id": state.get("identity", {}).get("session_id"),
        "pixels_or_ui_input_used": False,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
