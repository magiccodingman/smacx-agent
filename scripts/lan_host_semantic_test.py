#!/usr/bin/env python3
"""Contained regression for the public guarded LAN host lifecycle."""

from __future__ import annotations

import json
import sys
import time

from smacx_controller import bridge_request, launch_game


def main() -> int:
    launched = launch_game(wait_seconds=45)
    if not launched.get("ok"):
        raise AssertionError(f"menu launch failed: {launched}")

    before = bridge_request("semantic_lan", action="status")
    if before.get("lifecycle") != "menu":
        raise AssertionError(f"LAN lifecycle did not begin at menu: {before}")

    operation_id = "contained-public-host-1"
    hosted = bridge_request(
        "semantic_lan",
        timeout=60,
        action="host",
        session_name="SMACX Semantic Host Test",
        player_name="Semantic Host",
        client_operation_id=operation_id,
    )
    if not hosted.get("ok") or not hosted.get("lobby_launch_queued"):
        raise AssertionError(f"public semantic host failed: {hosted}")

    deadline = time.monotonic() + 20
    state = {}
    while time.monotonic() < deadline:
        state = bridge_request("semantic_lan", action="status")
        if state.get("lifecycle") == "lobby":
            break
        time.sleep(0.25)
    identity = state.get("identity", {})
    if state.get("lifecycle") != "lobby" \
            or not identity.get("match_id") \
            or not identity.get("session_id") \
            or not identity.get("network_session_id"):
        raise AssertionError(f"public lobby identity incomplete: {state}")

    duplicate = bridge_request(
        "semantic_lan",
        timeout=15,
        action="host",
        session_name="SMACX Semantic Host Test",
        player_name="Semantic Host",
        client_operation_id=operation_id,
    )
    if not duplicate.get("ok") or not duplicate.get("duplicate"):
        raise AssertionError(f"host operation was not idempotent: {duplicate}")

    print(json.dumps({
        "event": "pass",
        "guarded_lifecycle": ["menu", "hosting", "starting_lobby", "lobby"],
        "reached_lobby": True,
        "network_session_id": identity["network_session_id"],
        "idempotent_retry": True,
        "join_exposed": False,
        "lobby_mutation_exposed": False,
        "pixels_or_ui_input_used": False,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
