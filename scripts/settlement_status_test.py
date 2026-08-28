#!/usr/bin/env python3
"""Contained regression for explanatory Colony Pod settlement rule status."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(choices: dict[str, Any], name: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command", timeout=10, command=name,
        match_id=choices["match_id"], session_id=choices["session_id"],
        expected_revision=choices["revision"], **arguments,
    )


def wait_for_turn(deadline: float, minimum_turn: int = 0) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") == "turn" \
                and int(snapshot.get("turn", -1)) >= minimum_turn:
            return snapshot
        handled, result = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "wait_for_turn", "snapshot": snapshot, "result": result})
            return None
        time.sleep(0.05)
    return None


def settlement_status(choices: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (choice for choice in choices.get("choices", [])
         if choice.get("id", "").startswith("settlement:unavailable:")), None,
    )


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 120
    first_turn = wait_for_turn(deadline)
    if not first_turn:
        return 3

    units = bridge_request("list_units", scope="own", limit=50).get("items", [])
    colony = next((unit for unit in units if unit.get("roles", {}).get("colony")), None)
    if not colony:
        return 4
    choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=colony["id"])
    at_base = settlement_status(choices)
    move = next((choice for choice in choices.get("choices", [])
                 if choice.get("command") == "move_unit"), None)
    if not at_base or at_base.get("reason") != "current_tile_has_base" or not move:
        emit("failure", {"stage": "base_tile_status", "choices": choices})
        return 5
    moved = command(
        choices, "move_unit", unit_id=colony["id"],
        target_tile_id=move["target_tile_id"],
    )
    if not moved.get("ok"):
        emit("failure", {"stage": "move_colony", "result": moved})
        return 6

    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if int(snapshot.get("turn", -1)) > int(first_turn["turn"]) \
                and snapshot.get("interaction", {}).get("kind") == "turn":
            break
        if snapshot.get("interaction", {}).get("kind") == "turn":
            if snapshot.get("last_deferred_action", {}).get("status") == "pending":
                time.sleep(0.05)
                continue
            ready = [unit for unit in bridge_request(
                "list_units", scope="own", limit=50).get("items", []) if unit.get("ready")]
            if ready:
                unit_choices = bridge_request(
                    "semantic_choices", kind="unit_actions", unit_id=ready[0]["id"])
                skip = next((choice for choice in unit_choices.get("choices", [])
                             if choice.get("command") == "skip_unit"), None)
                if skip:
                    command(unit_choices, "skip_unit", unit_id=ready[0]["id"])
                    continue
            game_choices = bridge_request("semantic_choices", kind="game_management")
            end_turn = next((choice for choice in game_choices.get("choices", [])
                             if choice.get("command") == "end_turn"), None)
            if end_turn:
                command(game_choices, "end_turn")
                continue
            time.sleep(0.05)
            continue
        handled, result = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "advance_turn", "snapshot": snapshot, "result": result})
            return 7
        time.sleep(0.05)
    else:
        return 8

    units = bridge_request("list_units", scope="own", limit=50).get("items", [])
    colony = next((unit for unit in units if unit.get("roles", {}).get("colony")), None)
    if not colony:
        return 9
    choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=colony["id"])
    near_base = settlement_status(choices)
    if not near_base or near_base.get("reason") != "too_close_to_known_base" \
            or int(near_base.get("nearest_known_base_range", 9999)) \
                >= int(near_base.get("minimum_base_range", -1)):
        emit("failure", {"stage": "spacing_status", "choices": choices, "colony": colony})
        return 10
    emit("pass", {
        "base_tile_reason": at_base["reason"],
        "near_base_reason": near_base["reason"],
        "nearest_known_base_range": near_base["nearest_known_base_range"],
        "minimum_base_range": near_base["minimum_base_range"],
        "found_base_not_fabricated": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
