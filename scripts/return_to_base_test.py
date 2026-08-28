#!/usr/bin/env python3
"""Contained regression for coordinate-free native return-to-base orders."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], name: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        command=name,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def main() -> int:
    started = new_game(
        wait_seconds=60,
        difficulty=0,
        world_size=0,
        faction_id=1,
        blind_research=True,
        initial_research_priority=1,
        narrative_ui=False,
        tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        if snapshot.get("interaction", {}).get("kind") != "turn":
            handled = handle_interaction(snapshot)
            if handled:
                emit("interaction", handled)
            time.sleep(0.1)
            continue

        bases = bridge_request("list_bases", limit=200).get("items", [])
        base_tiles = {int(base["tile_id"]) for base in bases}
        units = bridge_request("list_units", scope="own", limit=300).get("items", [])
        returning = next(
            (unit for unit in units
             if unit.get("ready") and unit.get("triad") == "land"
             and unit.get("roles", {}).get("combat")
             and int(unit["tile_id"]) not in base_tiles),
            None,
        )
        at_base = next(
            (unit for unit in units
             if unit.get("ready")
             and int(unit["tile_id"]) in base_tiles),
            None,
        )
        if returning is None or at_base is None:
            time.sleep(0.1)
            continue

        unit_id = int(returning["id"])
        choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=unit_id)
        return_choice = next(
            (item for item in choices.get("choices", [])
             if item.get("command") == "return_to_base"),
            None,
        )
        if return_choice is None or not return_choice.get("persistent") \
                or not return_choice.get("native_route_selection") \
                or "x" in return_choice or "y" in return_choice \
                or "parameters" in return_choice:
            emit("failure", {"stage": "choice_contract", "choices": choices})
            return 3

        base_id = int(return_choice["base_id"])
        at_base_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=int(at_base["id"]),
        )
        if any(item.get("command") == "return_to_base"
               for item in at_base_choices.get("choices", [])):
            emit("failure", {"stage": "at_base_suppression", "choices": at_base_choices})
            return 4

        before_management = bridge_request("semantic_choices", kind="game_management")
        before_block = next(
            (item for item in before_management.get("choices", [])
             if item.get("id") == "turn:end_blocked"),
            {},
        )
        ready_before = int(before_block.get("ready_unit_count", -1))

        fabricated = command(
            choices, "return_to_base", unit_id=unit_id, base_id=base_id + 10000,
        )
        emit("fabricated_guard", fabricated)
        if fabricated.get("error", {}).get("code") != "invalid_return_base":
            return 5

        assigned = command(choices, "return_to_base", unit_id=unit_id, base_id=base_id)
        emit("assigned", assigned)
        if not assigned.get("ok") or assigned.get("destination_base_id") != base_id \
                or assigned.get("ready") is not False:
            return 6

        ordered_units = bridge_request("list_units", scope="own", limit=300).get("items", [])
        ordered = next(item for item in ordered_units if int(item["id"]) == unit_id)
        ordered_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=unit_id,
        )
        ordered_commands = [item.get("command") for item in ordered_choices.get("choices", [])]
        after_management = bridge_request("semantic_choices", kind="game_management")
        after_block = next(
            (item for item in after_management.get("choices", [])
             if item.get("id") == "turn:end_blocked"),
            {},
        )
        ready_after = int(after_block.get("ready_unit_count", -1))
        emit("persistent_state", {
            "unit": ordered,
            "choices": ordered_choices,
            "ready_before": ready_before,
            "ready_after": ready_after,
        })
        if ordered.get("order_name") != "go_to" or ordered.get("ready") is not False \
                or ordered_choices.get("reason") != "persistent_order" \
                or ordered_commands != ["activate_unit"] \
                or ready_before < 1 or ready_after != ready_before - 1:
            return 7

        activated = command(ordered_choices, "activate_unit", unit_id=unit_id)
        emit("activated", activated)
        if not activated.get("ok") or activated.get("ready") is not True:
            return 8

        stale = command(choices, "return_to_base", unit_id=unit_id, base_id=base_id)
        emit("stale_guard", stale)
        if stale.get("error", {}).get("code") != "stale_state":
            return 9

        emit("pass", {
            "native_console_go_home": True,
            "known_base_only": True,
            "coordinate_free_command": True,
            "fabricated_base_rejected": True,
            "persistent_decision_gate": True,
            "activation_cancels": True,
            "stale_replay_rejected": True,
            "pixels_or_ui_input_used": False,
        })
        return 0
    return 10


if __name__ == "__main__":
    sys.exit(main())
