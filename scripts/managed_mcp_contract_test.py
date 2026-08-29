#!/usr/bin/env python3
"""Contained contract for the managed, worker-attached MCP mode."""

from __future__ import annotations

import json
import os


os.environ["SMACX_MANAGED_ATTACHED"] = "1"
os.environ["SMACX_BRIDGE_HOST"] = "game-worker.invalid"
os.environ["SMACX_BRIDGE_PORT"] = "47814"
os.environ["SMACX_AGENT_TOKEN_FILE"] = "/run/secrets/bridge-token"

import smacx_controller as controller  # noqa: E402
import smacx_mcp as managed  # noqa: E402


def assert_blocked(result: dict) -> None:
    if result.get("error", {}).get("code") != "managed_lifecycle_operator_only":
        raise AssertionError(f"managed lifecycle escaped to host control: {result}")


def main() -> int:
    if controller.BRIDGE_HOST != "game-worker.invalid" or controller.BRIDGE_PORT != 47814:
        raise AssertionError("worker-specific bridge endpoint was not taken from the environment")
    if str(controller.TOKEN_FILE) != "/run/secrets/bridge-token":
        raise AssertionError("worker bridge token was not file-scoped")
    assert_blocked(managed.smac_launch())
    assert_blocked(managed.smac_new_game())
    assert_blocked(managed.smac_saves(action="load", match_id="match-managed", slot="autosave"))
    assert_blocked(managed.smac_stop())
    print(json.dumps({
        "event": "pass",
        "payload": {
            "bridge_endpoint_environment_scoped": True,
            "bridge_secret_file_scoped": True,
            "launch_operator_only": True,
            "new_game_operator_only": True,
            "load_operator_only": True,
            "stop_operator_only": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
