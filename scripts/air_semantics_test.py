#!/usr/bin/env python3
"""Contained regression for coordinate-free aircraft recovery and air defense."""

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
    last_diagnostic = 0.0
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

        bases = bridge_request("list_bases", limit=300).get("items", [])
        near_base = next(
            (base for base in bases if base.get("name") == "Harness Air Recovery"),
            None,
        )
        unsafe_base = next(
            (base for base in bases if base.get("name") == "Harness Unsafe Air Base"),
            None,
        )
        units = bridge_request("list_units", scope="own", limit=300).get("items", [])
        interceptor = next(
            (unit for unit in units
             if unit.get("name") == "Harness Interceptor" and unit.get("ready")),
            None,
        )
        nonair = next(
            (unit for unit in units if unit.get("ready") and unit.get("triad") != "air"),
            None,
        )
        if near_base is None or unsafe_base is None or interceptor is None or nonair is None:
            if time.monotonic() - last_diagnostic >= 10:
                emit("waiting_for_fixture", {
                    "bases": [{"id": item.get("id"), "name": item.get("name")}
                              for item in bases],
                    "units": [{"id": item.get("id"), "name": item.get("name"),
                               "triad": item.get("triad"), "ready": item.get("ready")}
                              for item in units],
                })
                last_diagnostic = time.monotonic()
            time.sleep(0.1)
            continue

        unit_id = int(interceptor["id"])
        choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=unit_id)
        query = next(
            (item for item in choices.get("choices", [])
             if item.get("kind") == "base_target_query"),
            None,
        )
        air_defense_choice = next(
            (item for item in choices.get("choices", [])
             if item.get("command") == "automate_air_defense"),
            None,
        )
        if query is None or query.get("legal", "missing") is not None \
                or query.get("parameters") != ["base_id"] \
                or air_defense_choice is None:
            emit("failure", {"stage": "initial_choices", "choices": choices})
            return 3

        unsafe_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=unit_id,
            base_id=int(unsafe_base["id"]),
        )
        unsafe_query = next(
            (item for item in unsafe_choices.get("choices", [])
             if item.get("kind") == "base_target_query"),
            {},
        )
        if unsafe_query.get("legal") is not False \
                or "fuel" not in str(unsafe_query.get("reason", "")) \
                or any(item.get("command") == "go_to_base"
                       for item in unsafe_choices.get("choices", [])):
            emit("failure", {"stage": "unsafe_fuel_choice", "choices": unsafe_choices})
            return 4

        unsafe_command = command(
            unsafe_choices, "go_to_base", unit_id=unit_id,
            base_id=int(unsafe_base["id"]),
        )
        emit("unsafe_fuel_guard", unsafe_command)
        if unsafe_command.get("error", {}).get("code") != "invalid_go_to_base" \
                or "fuel" not in unsafe_command.get("error", {}).get("message", ""):
            return 5

        near_id = int(near_base["id"])
        route_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=unit_id, base_id=near_id,
        )
        route = next(
            (item for item in route_choices.get("choices", [])
             if item.get("command") == "go_to_base"),
            None,
        )
        if route is None or route.get("base_id") != near_id \
                or route.get("fuel_safe") is not True \
                or not route.get("persistent") \
                or "x" in route or "y" in route or "parameters" in route:
            emit("failure", {"stage": "exact_route", "choices": route_choices})
            return 6

        routed = command(route_choices, "go_to_base", unit_id=unit_id, base_id=near_id)
        emit("routed", routed)
        if not routed.get("ok") or routed.get("destination_base_id") != near_id \
                or routed.get("fuel_safe") is not True \
                or routed.get("ready") is not False \
                or "x" in routed or "y" in routed:
            return 7

        routed_state = own_unit(unit_id)
        routed_choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=unit_id)
        if routed_state.get("order_name") != "go_to" or routed_state.get("ready") is not False \
                or routed_choices.get("reason") != "persistent_order" \
                or [item.get("command") for item in routed_choices.get("choices", [])] != ["activate_unit"]:
            emit("failure", {"stage": "route_gate", "unit": routed_state,
                             "choices": routed_choices})
            return 8

        activated = command(routed_choices, "activate_unit", unit_id=unit_id)
        if not activated.get("ok") or activated.get("ready") is not True:
            emit("failure", {"stage": "route_activation", "result": activated})
            return 9

        tiles = bridge_request(
            "list_tiles", center_tile_id=int(interceptor["tile_id"]), radius=8,
        ).get("items", [])
        standalone_airbase = next(
            (tile for tile in tiles
             if "airbase" in tile.get("features", [])
             and "base" not in tile.get("features", [])
             and tile.get("visible_now") and int(tile.get("owner", -99)) == 1),
            None,
        )
        if standalone_airbase is None:
            emit("failure", {"stage": "standalone_airbase_fixture", "tiles": tiles})
            return 10

        unsafe_tile_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=unit_id,
            target_tile_id=int(unsafe_base["tile_id"]),
        )
        unsafe_tile_status = next(
            (item for item in unsafe_tile_choices.get("choices", [])
             if item.get("id") == "tile_target:invalid"),
            {},
        )
        if "fuel" not in str(unsafe_tile_status.get("reason", "")) \
                or any(item.get("command") == "go_to"
                       for item in unsafe_tile_choices.get("choices", [])):
            emit("failure", {
                "stage": "generic_go_to_fuel_choice_bypass", "choices": unsafe_tile_choices,
            })
            return 11
        unsafe_tile_command = command(
            unsafe_tile_choices, "go_to", unit_id=unit_id,
            target_tile_id=int(unsafe_base["tile_id"]),
        )
        emit("generic_go_to_fuel_guard", unsafe_tile_command)
        if unsafe_tile_command.get("error", {}).get("code") \
                != "invalid_go_to_destination" \
                or "fuel" not in unsafe_tile_command.get("error", {}).get("message", ""):
            return 12

        airbase_tile_id = int(standalone_airbase["tile_id"])
        airbase_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=unit_id,
            target_tile_id=airbase_tile_id,
        )
        airbase_route = next(
            (item for item in airbase_choices.get("choices", [])
             if item.get("command") == "go_to"),
            None,
        )
        if airbase_route is None or airbase_route.get("target_tile_id") != airbase_tile_id \
                or airbase_route.get("fuel_safe") is not True \
                or airbase_route.get("destination_refuels") is not True \
                or airbase_route.get("route_kind") != "air_recovery" \
                or "x" in airbase_route or "y" in airbase_route:
            emit("failure", {"stage": "standalone_airbase_choice",
                             "choices": airbase_choices})
            return 13
        airbase_routed = command(
            airbase_choices, "go_to", unit_id=unit_id,
            target_tile_id=airbase_tile_id,
        )
        emit("standalone_airbase_route", airbase_routed)
        if not airbase_routed.get("ok") \
                or airbase_routed.get("target_tile_id") != airbase_tile_id \
                or airbase_routed.get("destination_refuels") is not True \
                or airbase_routed.get("route_kind") != "air_recovery" \
                or airbase_routed.get("ready") is not False:
            return 14
        airbase_ordered_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=unit_id,
        )
        airbase_activated = command(
            airbase_ordered_choices, "activate_unit", unit_id=unit_id,
        )
        if not airbase_activated.get("ok") or airbase_activated.get("ready") is not True:
            emit("failure", {"stage": "airbase_route_activation",
                             "result": airbase_activated})
            return 15

        fresh = bridge_request("semantic_choices", kind="unit_actions", unit_id=unit_id)
        automated = command(fresh, "automate_air_defense", unit_id=unit_id)
        emit("air_defense", automated)
        air_state = own_unit(unit_id)
        air_choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=unit_id)
        if not automated.get("ok") or air_state.get("order_name") != "auto_air_defense" \
                or air_state.get("order_auto_type") != 12 or air_state.get("ready") is not False \
                or air_choices.get("reason") != "auto_air_defense" \
                or [item.get("command") for item in air_choices.get("choices", [])] != ["activate_unit"]:
            emit("failure", {"stage": "air_defense_state", "unit": air_state,
                             "choices": air_choices})
            return 10

        nonair_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=int(nonair["id"]),
        )
        nonair_attempt = command(
            nonair_choices, "automate_air_defense", unit_id=int(nonair["id"]),
        )
        emit("nonair_guard", nonair_attempt)
        if nonair_attempt.get("error", {}).get("code") != "air_defense_unavailable":
            return 11

        cancelled = command(air_choices, "activate_unit", unit_id=unit_id)
        if not cancelled.get("ok") or cancelled.get("old_automation") != "auto_air_defense" \
                or cancelled.get("ready") is not True:
            emit("failure", {"stage": "air_defense_activation", "result": cancelled})
            return 12

        stale = command(fresh, "automate_air_defense", unit_id=unit_id)
        if stale.get("error", {}).get("code") != "stale_state":
            emit("failure", {"stage": "stale_guard", "result": stale})
            return 13

        emit("pass", {
            "object_id_base_routing": True,
            "coordinate_free_route_choice_and_command": True,
            "aircraft_fuel_safety_guard": True,
            "generic_go_to_fuel_bypass_closed": True,
            "standalone_airbase_recovery": True,
            "native_air_defense_mode_12": True,
            "persistent_decision_gates": True,
            "invalid_unit_and_stale_guards": True,
            "pixels_or_ui_input_used": False,
        })
        return 0
    return 14


if __name__ == "__main__":
    sys.exit(main())
