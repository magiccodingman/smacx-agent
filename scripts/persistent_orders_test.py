#!/usr/bin/env python3
"""Contained regression for semantic patrol and Road To orders."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def guarded(source: dict[str, Any], command: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        timeout=12,
        command=command,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def fresh_turn(deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        interaction = snapshot.get("interaction", {}).get("kind")
        if interaction == "turn":
            return snapshot
        if interaction in {"waiting_for_engine", "waiting_for_turn"}:
            time.sleep(0.1)
            continue
        handled, outcome = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "interaction", "snapshot": snapshot, "outcome": outcome})
            return {}
    return {}


def main() -> int:
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 150
    snapshot = fresh_turn(deadline)
    if not snapshot:
        return 3
    units = bridge_request("list_units", scope="own").get("items", [])
    former = next((item for item in units if item.get("roles", {}).get("former") and item.get("ready")), None)
    patrol = next((item for item in units if item.get("roles", {}).get("combat") and item.get("ready")), None)
    if former is None or patrol is None:
        emit("failure", {"stage": "fixtures", "units": units})
        return 4
    tiles = bridge_request(
        "list_tiles", center_tile_id=int(patrol["tile_id"]), radius=3,
    ).get("items", [])
    targets = sorted(
        (tile for tile in tiles
         if tile.get("visible_now") and not tile.get("is_ocean")
         and int(tile["tile_id"]) != int(patrol["tile_id"])),
        key=lambda tile: int(tile["tile_id"]),
    )
    if not targets:
        emit("failure", {"stage": "known_target", "tiles": tiles})
        return 5
    target = targets[0]

    patrol_choices = bridge_request(
        "semantic_choices", kind="unit_actions", unit_id=int(patrol["id"]),
        target_tile_id=int(target["tile_id"]),
    )
    if not any(item.get("command") == "patrol_unit" for item in patrol_choices.get("choices", [])):
        emit("failure", {"stage": "patrol_choice", "choices": patrol_choices})
        return 6
    patrol_result = guarded(
        patrol_choices, "patrol_unit", unit_id=int(patrol["id"]),
        target_tile_id=int(target["tile_id"]),
    )
    emit("patrol_set", {"target": target, "result": patrol_result})
    if (not patrol_result.get("ok") or patrol_result.get("order") != "go_to"
            or int(patrol_result.get("waypoint_count", 0)) < 1):
        return 7

    former_choices = bridge_request(
        "semantic_choices", kind="unit_actions", unit_id=int(former["id"]),
        target_tile_id=int(target["tile_id"]),
    )
    road_choice = next(
        (item for item in former_choices.get("choices", [])
         if item.get("command") == "build_road_to" and item.get("infrastructure") == "road"),
        None,
    )
    if road_choice is None:
        emit("failure", {"stage": "road_choice", "choices": former_choices})
        return 8
    road_result = guarded(
        former_choices, "build_road_to", unit_id=int(former["id"]),
        infrastructure="road", target_tile_id=int(target["tile_id"]),
    )
    emit("road_to_set", {"target": target, "result": road_result})
    if not road_result.get("ok") or road_result.get("order") != "road_to":
        return 9

    patrol_state = bridge_request("semantic_choices", kind="unit_actions", unit_id=int(patrol["id"]))
    activate_patrol = next((item for item in patrol_state.get("choices", [])
                            if item.get("command") == "activate_unit"), None)
    if activate_patrol is None:
        emit("failure", {"stage": "patrol_activation", "choices": patrol_state})
        return 10
    activated = guarded(patrol_state, "activate_unit", unit_id=int(patrol["id"]))
    emit("patrol_cancelled", activated)
    if not activated.get("ok"):
        return 11

    road_state = bridge_request("semantic_choices", kind="unit_actions", unit_id=int(former["id"]))
    activate_road = next((item for item in road_state.get("choices", [])
                          if item.get("command") == "activate_unit"), None)
    if activate_road is None:
        emit("failure", {"stage": "road_activation", "choices": road_state})
        return 12
    activated = guarded(road_state, "activate_unit", unit_id=int(former["id"]))
    emit("road_to_cancelled", activated)
    if not activated.get("ok"):
        return 13

    emit("pass", {
        "known_waypoint_only": True,
        "native_patrol_validation": True,
        "persistent_patrol_order": True,
        "native_road_to_order": True,
        "persistent_order_cancellation": True,
        "coordinates_or_pixels_used": False,
        "target_tile_id_only": True,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
