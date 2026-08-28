#!/usr/bin/env python3
"""Contained regression for the coordinate-free match-local tile contract."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def guarded(source: dict[str, Any], command: str, **kwargs: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command", command=command,
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], **kwargs,
    )


def contains_coordinate_key(value: object) -> bool:
    if isinstance(value, dict):
        if "x" in value or "y" in value:
            return True
        return any(contains_coordinate_key(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_coordinate_key(item) for item in value)
    return False


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=0, world_size=0, faction_id=1,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 70
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") == "turn":
            break
        handled, reason = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "opening", "reason": reason, "snapshot": snapshot})
            return 3
        time.sleep(0.05)
    if snapshot.get("interaction", {}).get("kind") != "turn":
        return 4

    state_surfaces = {
        "observe": bridge_request("observe"),
        "snapshot": bridge_request("semantic_snapshot"),
        "bases": bridge_request("list_bases", limit=100),
        "units": bridge_request("list_units", scope="own", limit=100),
        "factions": bridge_request("list_factions"),
        "technologies": bridge_request("list_technologies"),
    }
    if any(contains_coordinate_key(value) for value in state_surfaces.values()):
        emit("failure", {"stage": "coordinate_free_state", "surfaces": state_surfaces})
        return 5
    bases = state_surfaces["bases"].get("items", [])
    if bases:
        citizen_choices = bridge_request(
            "semantic_choices", kind="base_citizens", base_id=int(bases[0]["id"]),
        )
        if contains_coordinate_key(citizen_choices):
            emit("failure", {
                "stage": "coordinate_free_citizens", "choices": citizen_choices,
            })
            return 6

    units = state_surfaces["units"].get("items", [])
    ready = [item for item in units if item.get("ready")]
    if len(ready) < 2 or any(int(item.get("tile_id", -1)) < 0 for item in ready):
        emit("failure", {"stage": "tile_ids_missing", "units": units})
        return 7

    mover = next((item for item in ready if item.get("roles", {}).get("combat")), ready[0])
    choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=int(mover["id"]))
    moves = [item for item in choices.get("choices", []) if item.get("command") == "move_unit"]
    if not moves or contains_coordinate_key(choices) \
            or any(int(item.get("target_tile_id", -1)) < 0 for item in moves):
        emit("failure", {"stage": "coordinate_free_choices", "choices": choices})
        return 8

    invalid = bridge_request(
        "semantic_choices", kind="unit_actions", unit_id=int(mover["id"]),
        target_tile_id=2_000_000_000,
    )
    if contains_coordinate_key(invalid) or not any(
        item.get("id") == "tile_target:invalid" for item in invalid.get("choices", [])
    ):
        emit("failure", {"stage": "invalid_tile_guard", "choices": invalid})
        return 9

    move = next(
        (item for item in moves if item.get("visible_now") and not item.get("is_ocean")),
        moves[0],
    )
    coordinate_only = guarded(
        choices, "move_unit", unit_id=int(mover["id"]), x=0, y=0,
    )
    if coordinate_only.get("ok"):
        emit("failure", {"stage": "coordinate_request_accepted", "result": coordinate_only})
        return 10
    moved = guarded(
        choices, "move_unit", unit_id=int(mover["id"]),
        target_tile_id=int(move["target_tile_id"]),
    )
    emit("move", moved)
    if not moved.get("ok") or moved.get("target_tile_id") != move["target_tile_id"]:
        return 11
    stale = guarded(
        choices, "move_unit", unit_id=int(mover["id"]),
        target_tile_id=int(move["target_tile_id"]),
    )
    if stale.get("ok") or stale.get("error", {}).get("code") != "stale_state":
        emit("failure", {"stage": "stale_replay", "result": stale})
        return 12

    action_id = moved.get("action_id")
    if isinstance(action_id, int):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            action = bridge_request("action_status", action_id=action_id).get("action", {})
            if contains_coordinate_key(action):
                emit("failure", {"stage": "coordinate_free_deferred_action", "action": action})
                return 13
            if action.get("status") == "completed":
                break
            if action.get("status") == "rejected":
                emit("failure", {"stage": "native_move_rejected", "action": action})
                return 14
            time.sleep(0.05)

    deadline = time.monotonic() + 8
    observed = None
    while time.monotonic() < deadline:
        current = bridge_request("list_units", scope="own", limit=100).get("items", [])
        observed = next((item for item in current if int(item["id"]) == int(mover["id"])), None)
        if observed is None or int(observed.get("tile_id", -1)) != int(mover["tile_id"]):
            break
        time.sleep(0.05)
    if observed is not None and int(observed.get("tile_id", -1)) == int(mover["tile_id"]):
        emit("failure", {"stage": "move_not_observed", "unit": observed})
        return 15

    center_tile_id = int(observed["tile_id"] if observed is not None else move["target_tile_id"])
    tiles = bridge_request("list_tiles", center_tile_id=center_tile_id, radius=2)
    if tiles.get("center", {}).get("tile_id") != center_tile_id \
            or not tiles.get("items") or contains_coordinate_key(tiles):
        emit("failure", {"stage": "coordinate_free_tile_list", "tiles": tiles})
        return 16

    router = next(item for item in ready if int(item["id"]) != int(mover["id"]))
    route_target = next(
        (item for item in tiles["items"] if int(item["tile_id"]) != int(router["tile_id"])),
        None,
    )
    if route_target is None:
        return 17
    route_choices = bridge_request(
        "semantic_choices", kind="unit_actions", unit_id=int(router["id"]),
        target_tile_id=int(route_target["tile_id"]),
    )
    route = next(
        (item for item in route_choices.get("choices", []) if item.get("command") == "go_to"),
        None,
    )
    if route is None or contains_coordinate_key(route_choices) \
            or route.get("target_tile_id") != route_target["tile_id"]:
        emit("failure", {"stage": "exact_route_choice", "choices": route_choices})
        return 18
    routed = guarded(
        route_choices, "go_to", unit_id=int(router["id"]),
        target_tile_id=int(route_target["tile_id"]),
    )
    if not routed.get("ok") or routed.get("target_tile_id") != route_target["tile_id"]:
        emit("failure", {"stage": "route_execution", "result": routed})
        return 19

    emit("pass", {
        "stable_match_local_tile_ids": True,
        "coordinate_free_state_surfaces": True,
        "coordinate_free_unit_choices": True,
        "coordinate_free_tile_listing": True,
        "coordinate_only_requests_rejected": True,
        "coordinate_free_deferred_actions": True,
        "movement_by_tile_id": True,
        "persistent_route_by_tile_id": True,
        "invalid_and_stale_guards": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
