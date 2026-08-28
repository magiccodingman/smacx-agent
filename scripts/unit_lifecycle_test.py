#!/usr/bin/env python3
"""Contained regression for activation and guarded semantic disbanding."""

from __future__ import annotations

import json
import sys
import time

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(choices: dict, command_name: str, unit_id: int, **arguments: object) -> dict:
    return bridge_request(
        "semantic_command",
        command=command_name,
        unit_id=unit_id,
        match_id=choices["match_id"],
        session_id=choices["session_id"],
        expected_revision=choices["revision"],
        **arguments,
    )


def choices(unit_id: int) -> dict:
    return bridge_request("semantic_choices", kind="unit_actions", unit_id=unit_id)


def main() -> int:
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 70
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        if snapshot["interaction"]["kind"] != "turn":
            handled, outcome = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "interaction", "outcome": outcome})
                return 3
            continue
        units = [unit for unit in bridge_request("list_units", scope="own")["items"]
                 if unit.get("ready")]
        if len(units) < 2:
            time.sleep(0.1)
            continue
        held_id = int(units[0]["id"])
        doomed_id = int(units[1]["id"])
        held = command(choices(held_id), "hold_unit", held_id)
        held_choices = choices(held_id)
        activate_present = any(item.get("command") == "activate_unit"
                               for item in held_choices.get("choices", []))
        activated = command(held_choices, "activate_unit", held_id)

        doomed_choices = choices(doomed_id)
        refused = command(doomed_choices, "disband_unit", doomed_id)
        disbanded = command(doomed_choices, "disband_unit", doomed_id, confirm_disband=1)
        remaining = bridge_request("list_units", scope="own")["items"]
        activated_unit = next((unit for unit in remaining if int(unit["id"]) == held_id), None)
        emit("results", {
            "held": held,
            "activate_present": activate_present,
            "activated": activated,
            "refused": refused,
            "disbanded": disbanded,
            "remaining": remaining,
        })
        passed = (
            held.get("ok")
            and activate_present
            and activated.get("ok")
            and activated.get("ready")
            and refused.get("error", {}).get("code") == "disband_confirmation_required"
            and disbanded.get("ok")
            and len(remaining) == len(units) - 1
            and activated_unit is not None
            and activated_unit["order_name"] == "none"
            and activated_unit["ready"]
        )
        if not passed:
            return 4
        emit("pass", {"activation": True, "confirmation_guard": True})
        return 0
    return 5


if __name__ == "__main__":
    sys.exit(main())
