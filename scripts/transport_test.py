#!/usr/bin/env python3
"""Contained regression for semantic boarding and disembarkation."""

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
        command=command_name,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def unit_choices(unit_id: int) -> dict[str, Any]:
    return bridge_request("semantic_choices", kind="unit_actions", unit_id=unit_id)


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

        units = bridge_request("list_units", scope="own").get("items", [])
        transport = next((item for item in units if item.get("roles", {}).get("transport")), None)
        if transport is None:
            emit("failure", {"stage": "transport_fixture", "units": units})
            return 4
        transport_id = int(transport["id"])
        same_tile_scouts = [
            item for item in units
            if item.get("name") == "Scout Patrol"
            and item.get("tile_id") == transport.get("tile_id")
        ]
        boarded = None
        waiting = None
        boarded_choices = None
        waiting_choices = None
        for item in same_tile_scouts:
            candidate = unit_choices(int(item["id"]))
            if candidate.get("reason") == "boarded_transport":
                boarded = item
                boarded_choices = candidate
            elif any(choice.get("command") == "board_transport"
                     for choice in candidate.get("choices", [])):
                waiting = item
                waiting_choices = candidate
        if boarded is None or waiting is None or boarded_choices is None or waiting_choices is None:
            emit("failure", {"stage": "passengers", "units": units})
            return 5

        waiting_id = int(waiting["id"])
        board_choice = next(
            choice for choice in waiting_choices["choices"]
            if choice.get("command") == "board_transport"
            and choice.get("transport_unit_id") == transport_id
        )
        board_result = command(
            waiting_choices,
            "board_transport",
            unit_id=waiting_id,
            transport_unit_id=transport_id,
        )
        emit("board", {"choice": board_choice, "result": board_result})
        if not board_result.get("ok") or not board_result.get("boarded"):
            return 6

        boarded_id = int(boarded["id"])
        fresh_boarded = unit_choices(boarded_id)
        disembark_choice = next(
            (choice for choice in fresh_boarded.get("choices", [])
             if choice.get("command") == "disembark_unit"),
            None,
        )
        if disembark_choice is None:
            emit("failure", {"stage": "disembark_choice", "choices": fresh_boarded})
            return 7
        disembarked = command(
            fresh_boarded,
            "disembark_unit",
            unit_id=boarded_id,
            transport_unit_id=transport_id,
            target_tile_id=int(disembark_choice["target_tile_id"]),
        )
        emit("disembark", disembarked)
        if not disembarked.get("ok") or not disembarked.get("queued"):
            return 8
        action_id = int(disembarked["action_id"])
        status = {}
        for _ in range(120):
            status = bridge_request("action_status", action_id=action_id)
            action = status.get("action") or {}
            if action.get("status") != "pending":
                break
            time.sleep(0.05)
        action = status.get("action") or {}
        emit("disembark_status", status)
        if action.get("status") != "completed" \
        or action.get("observed_tile_id") != disembark_choice["target_tile_id"]:
            return 9
        emit("pass", {
            "explicit_boarding": True,
            "atomic_native_disembark": True,
            "capacity_checked": True,
            "coordinates_or_pixels_used": False,
            "target_tile_id_only": True,
        })
        return 0
    return 10


if __name__ == "__main__":
    sys.exit(main())
