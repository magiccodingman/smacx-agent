#!/usr/bin/env python3
"""Contained DirectPlay TCP/IP service/create/join lifecycle probe."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from smacx_controller import bridge_request, launch_game


def main() -> int:
    max_stage = int(os.environ.get("SMACX_LAN_TEST_STAGE", "5"))
    os.environ["SMACX_AGENT_TEST_MODE"] = "1"
    os.environ["SMACX_AGENT_TEST_LAN_HOST"] = "1"
    launched = launch_game(wait_seconds=45)
    if not launched.get("ok"):
        raise AssertionError(f"menu launch failed: {launched}")
    state = launched.get("state", {}).get("state", {})
    if state.get("in_game") or state.get("multiplayer_active"):
        raise AssertionError(f"fixture did not begin at an inactive menu: {launched}")

    result = bridge_request(
        "test_lan_host_fixture", timeout=30,
        session_name="SMACX Agent Native Test",
        player_name="Semantic Host",
        max_stage=max_stage,
        direct_open_diagnostic=os.environ.get("SMACX_LAN_DIRECT_OPEN") == "1",
    )
    if result.get("error", {}).get("code") == "game_timeout":
        if os.environ.get("SMACX_LAN_CAPTURE_MODAL") == "1":
            screenshot = Path(__file__).resolve().parents[1] / "runtime" / "lan-host-modal.png"
            subprocess.run(
                ["gnome-screenshot", "-f", str(screenshot)],
                check=False,
                timeout=10,
            )
        time.sleep(12)
        late_status = bridge_request(
            "test_lan_host_fixture", timeout=10, action="status",
        )
        raise AssertionError(
            f"DirectPlay host lifecycle timed out; late status={late_status}",
        )
    if not result.get("ok"):
        raise AssertionError(f"DirectPlay host lifecycle failed: {result}")
    if result.get("setup_status") != 0 \
            or result.get("completed_stage") != max_stage \
            or not result.get("closed_after_fixture"):
        raise AssertionError(f"DirectPlay lifecycle contract differs: {result}")
    if max_stage >= 2 and result.get("init_status") != 0:
        raise AssertionError(f"DirectPlay initialization failed: {result}")
    if max_stage >= 3 and result.get("service_status") != 0:
        raise AssertionError(f"DirectPlay service selection failed: {result}")
    if max_stage >= 4 and not result.get("session_created"):
        raise AssertionError(f"DirectPlay session creation failed: {result}")
    if max_stage >= 5 and not result.get("join_status"):
        raise AssertionError(f"DirectPlay host self-join failed: {result}")

    final_state = bridge_request("status")
    if final_state.get("state", {}).get("multiplayer_active"):
        raise AssertionError(f"fixture leaked active multiplayer state: {final_state}")
    print(json.dumps({
        "event": "pass",
        "tcp_ip_service_joined": True,
        "session_created": True,
        "host_joined_own_session": True,
        "completed_stage": max_stage,
        "native_player_id": result.get("native_player_id"),
        "closed_after_fixture": True,
        "lobby_or_game_started": False,
        "pixels_or_ui_input_used": False,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
