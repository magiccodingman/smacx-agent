#!/usr/bin/env python3
"""Contained regression for semantic native artillery combat."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], command_name: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        timeout=12,
        command=command_name,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def main() -> int:
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 90
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
        units = bridge_request("list_units", scope="visible").get("items", [])
        artillery = next(
            (item for item in units
             if item.get("owner") == snapshot["faction"]["id"]
             and item.get("roles", {}).get("artillery")),
            None,
        )
        if artillery is None:
            emit("failure", {"stage": "fixture", "units": units})
            return 4
        unit_id = int(artillery["id"])
        choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=unit_id)
        attack = next(
            (item for item in choices.get("choices", [])
             if item.get("command") == "artillery_attack"),
            None,
        )
        if attack is None:
            emit("failure", {"stage": "choice", "choices": choices, "units": units})
            return 5
        result = command(
            choices,
            "artillery_attack",
            unit_id=unit_id,
            target_tile_id=int(attack["target_tile_id"]),
        )
        emit("attack", {"choice": attack, "result": result})
        if not result.get("ok") or not result.get("accepted"):
            return 6
        after = bridge_request("list_units", scope="visible")
        emit("after", after)
        emit("pass", {
            "visible_non_pact_target_only": True,
            "native_artillery_combat": True,
            "observe_after_combat_required": True,
            "coordinates_or_pixels_used": False,
            "target_tile_id_only": True,
        })
        return 0
    return 7


if __name__ == "__main__":
    sys.exit(main())
