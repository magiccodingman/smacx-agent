#!/usr/bin/env python3
"""Contained semantic regression for persistent native bombing runs."""

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


def own_unit(unit_id: int) -> dict[str, Any]:
    return next(
        (item for item in bridge_request("list_units", scope="own", limit=300).get("items", [])
         if int(item["id"]) == unit_id),
        {},
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

        units = bridge_request("list_units", scope="own", limit=300).get("items", [])
        bomber = next(
            (unit for unit in units
             if unit.get("ready") and unit.get("name") == "Harness Bomber"),
            None,
        )
        if bomber is None:
            time.sleep(0.1)
            continue

        bomber_id = int(bomber["id"])
        choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=bomber_id,
        )
        bombing = next(
            (item for item in choices.get("choices", [])
             if item.get("command") == "set_bombing_run"),
            None,
        )
        if bombing is None or not bombing.get("persistent") \
                or not bombing.get("native_automation") \
                or bombing.get("fuel_policy") != "non_sacrificial_round_trip" \
                or any(key in bombing for key in ("x", "y", "parameters")):
            emit("failure", {"stage": "bombing_choice", "choices": choices})
            return 3

        fabricated = command(
            choices, "set_bombing_run", unit_id=bomber_id,
            target_tile_id=int(bomber["tile_id"]),
        )
        emit("invalid_target_guard", fabricated)
        if fabricated.get("error", {}).get("code") != "invalid_bombing_target" \
                or own_unit(bomber_id).get("order_name") == "bombing_run":
            return 4

        assigned = command(
            choices, "set_bombing_run", unit_id=bomber_id,
            target_tile_id=int(bombing["target_tile_id"]),
        )
        emit("assigned", assigned)
        ordered = own_unit(bomber_id)
        ordered_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=bomber_id,
        )
        ordered_commands = [item.get("command") for item in ordered_choices.get("choices", [])]
        if not assigned.get("ok") or ordered.get("order_name") != "bombing_run" \
                or ordered.get("order_auto_type") != 10 or ordered.get("ready") is not False \
                or ordered_choices.get("reason") != "bombing_run" \
                or ordered_commands != ["activate_unit"]:
            emit("failure", {"stage": "assigned_state", "unit": ordered,
                             "choices": ordered_choices})
            return 5

        activated = command(
            ordered_choices, "activate_unit", unit_id=bomber_id,
        )
        emit("activated", activated)
        if not activated.get("ok") or activated.get("old_automation") != "bombing_run" \
                or activated.get("ready") is not True:
            return 6

        stale = command(
            choices, "set_bombing_run", unit_id=bomber_id,
            target_tile_id=int(bombing["target_tile_id"]),
        )
        if stale.get("error", {}).get("code") != "stale_state":
            emit("failure", {"stage": "stale_replay", "result": stale})
            return 7

        emit("pass", {
            "native_persistent_bombing_run": True,
            "currently_visible_vendetta_base": bombing["target_base_name"],
            "opaque_target_tile_id": bombing["target_tile_id"],
            "non_sacrificial_round_trip_guard": True,
            "invalid_target_rejected_without_mutation": True,
            "activation_cancels_policy": True,
            "stale_replay_rejected": True,
            "coordinate_pixel_or_visual_input_used": False,
        })
        return 0
    return 8


if __name__ == "__main__":
    sys.exit(main())
