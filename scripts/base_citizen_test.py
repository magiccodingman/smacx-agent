#!/usr/bin/env python3
"""Contained regression for semantic worker/specialist assignment."""

from __future__ import annotations

import json
import sys
import time

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(choices: dict, command_name: str, **arguments: object) -> dict:
    return bridge_request(
        "semantic_command",
        command=command_name,
        match_id=choices["match_id"],
        session_id=choices["session_id"],
        expected_revision=choices["revision"],
        **arguments,
    )


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
        base = bridge_request("list_bases", limit=1)["items"][0]
        base_id = int(base["id"])
        management = bridge_request("semantic_choices", kind="base_management", base_id=base_id)
        disabled = command(
            management,
            "set_base_governor",
            base_id=base_id,
            active=0,
            manage_citizens=0,
            manage_production=0,
        )
        citizens = bridge_request("semantic_choices", kind="base_citizens", base_id=base_id)
        worked = next((tile for tile in citizens.get("tiles", [])
                       if tile.get("worked") and int(tile["tile_index"]) > 0), None)
        specialist_type = next(iter(citizens.get("available_specialist_types", [])), None)
        if worked is None or specialist_type is None:
            emit("failure", {"stage": "enumeration", "citizens": citizens})
            return 4
        converted = command(
            citizens,
            "convert_worker_to_specialist",
            base_id=base_id,
            tile_index=int(worked["tile_index"]),
            citizen_id=int(specialist_type["citizen_id"]),
        )
        after_convert = bridge_request("semantic_choices", kind="base_citizens", base_id=base_id)
        target = next((tile for tile in after_convert.get("tiles", [])
                       if tile.get("assignable") and not tile.get("worked")), None)
        if target is None or not after_convert.get("specialists"):
            emit("failure", {"stage": "post_conversion", "citizens": after_convert})
            return 5
        assigned = command(
            after_convert,
            "assign_specialist_to_tile",
            base_id=base_id,
            specialist_index=0,
            tile_index=int(target["tile_index"]),
        )
        after_assign = bridge_request("semantic_choices", kind="base_citizens", base_id=base_id)
        emit("results", {
            "disabled": disabled,
            "converted": converted,
            "assigned": assigned,
            "worked_from": worked,
            "worked_to": target,
            "after_assign": after_assign,
        })
        passed = (
            disabled.get("ok")
            and converted.get("ok")
            and converted.get("specialist_total") == 1
            and assigned.get("ok")
            and assigned.get("specialist_total") == 0
            and not after_assign.get("specialists")
            and any(tile.get("worked") and tile["tile_index"] == target["tile_index"]
                    for tile in after_assign.get("tiles", []))
        )
        if not passed:
            return 6
        emit("pass", {"manual_worker_assignment": True, "specialist_assignment": True})
        return 0
    return 7


if __name__ == "__main__":
    sys.exit(main())
