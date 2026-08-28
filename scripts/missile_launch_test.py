#!/usr/bin/env python3
"""Contained regression for compact, fair-play semantic missile launches."""

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


def unit_choices(unit_id: int, target_tile_id: int = -1) -> dict[str, Any]:
    return bridge_request(
        "semantic_choices", kind="unit_actions", unit_id=unit_id,
        target_tile_id=target_tile_id,
    )


def owned_missiles() -> dict[str, dict[str, Any]]:
    units = bridge_request("list_units", scope="own", limit=300).get("items", [])
    return {
        item.get("roles", {}).get("missile_kind", "none"): item
        for item in units
        if item.get("roles", {}).get("missile")
    }


def visible_hostiles() -> list[dict[str, Any]]:
    items = bridge_request("list_units", scope="visible", limit=300).get("items", [])
    return [item for item in items if item.get("owner") not in {0, 1}]


def resolve_until_turn(deadline: float) -> bool:
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.05)
            continue
        if snapshot.get("interaction", {}).get("kind") == "turn":
            return True
        handled, outcome = handle_interaction(snapshot)
        if not handled:
            emit("unsupported_interaction", {"snapshot": snapshot, "outcome": outcome})
            return False
    return False


def wait_action(action_id: int, deadline: float) -> dict[str, Any]:
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = bridge_request("action_status", action_id=action_id)
        last = status.get("action") or {}
        if last.get("status") != "pending":
            return last
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        kind = snapshot.get("interaction", {}).get("kind")
        if kind not in {"waiting_for_engine", "waiting_for_turn", "turn"}:
            handled, outcome = handle_interaction(snapshot)
            if not handled:
                emit("unsupported_interaction", {"snapshot": snapshot, "outcome": outcome})
                return {"status": "unsupported_interaction", "outcome": outcome}
        time.sleep(0.05)
    return last


def queried_launch(unit_id: int, target_tile_id: int) -> dict[str, Any] | None:
    choices = unit_choices(unit_id, target_tile_id)
    return next(
        (item for item in choices.get("choices", [])
         if item.get("command") == "launch_missile"),
        None,
    )


def find_empty_launch(unit: dict[str, Any], excluded: set[int]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    tiles = bridge_request(
        "list_tiles", center_tile_id=unit["tile_id"], radius=12,
    ).get("items", [])
    for tile in tiles:
        target_tile_id = int(tile["tile_id"])
        if target_tile_id in excluded or not tile.get("visible_now"):
            continue
        features = set(tile.get("features", []))
        if "base" in features or "vehicle" in features:
            continue
        choices = unit_choices(int(unit["id"]), target_tile_id)
        launch = next(
            (item for item in choices.get("choices", [])
             if item.get("command") == "launch_missile"),
            None,
        )
        if launch:
            return choices, launch
    return None


def main() -> int:
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 150
    if not resolve_until_turn(deadline):
        return 3

    missiles = owned_missiles()  # also initializes the contained fixture
    hostiles = visible_hostiles()
    emit("fixture", {"missiles": missiles, "hostiles": hostiles})
    required = {"conventional", "tectonic", "fungal", "planet_buster"}
    if set(missiles) != required or len(hostiles) < 2:
        return 4

    conventional = missiles["conventional"]
    compact = unit_choices(int(conventional["id"]))
    query_contracts = [
        item for item in compact.get("choices", []) if item.get("kind") == "target_query"
    ]
    if len(query_contracts) != 1 or query_contracts[0].get("legal") is not None:
        emit("failure", {"stage": "compact_query_contract", "choices": compact})
        return 5

    enemy = hostiles[0]
    exact = unit_choices(int(conventional["id"]), int(enemy["tile_id"]))
    launch = next(
        (item for item in exact.get("choices", []) if item.get("command") == "launch_missile"),
        None,
    )
    if not launch or launch.get("missile_kind") != "conventional":
        emit("failure", {"stage": "conventional_query", "choices": exact})
        return 6
    queued = command(
        exact, "launch_missile", unit_id=launch["unit_id"],
        target_tile_id=launch["target_tile_id"],
    )
    action = wait_action(int(queued.get("action_id", -1)), deadline)
    emit("conventional", {"queued": queued, "action": action})
    if not queued.get("ok") or action.get("status") != "completed":
        return 7

    missiles = owned_missiles()
    excluded: set[int] = {int(enemy["tile_id"])}
    for kind in ("tectonic", "fungal"):
        unit = missiles.get(kind)
        if not unit:
            return 8
        candidate = find_empty_launch(unit, excluded)
        if not candidate:
            emit("failure", {"stage": f"{kind}_target"})
            return 9
        choices, launch = candidate
        target_tile_id = int(launch["target_tile_id"])
        excluded.add(target_tile_id)
        queued = command(
            choices, "launch_missile", unit_id=launch["unit_id"],
            target_tile_id=target_tile_id,
        )
        action = wait_action(int(queued.get("action_id", -1)), deadline)
        emit(kind, {"target_tile_id": target_tile_id, "queued": queued, "action": action})
        if not queued.get("ok") or action.get("status") != "completed":
            return 10
        missiles = owned_missiles()

    planet_buster = missiles.get("planet_buster")
    remaining_hostiles = visible_hostiles()
    if not planet_buster or not remaining_hostiles:
        emit("failure", {"stage": "planet_buster_fixture", "missiles": missiles,
                         "hostiles": remaining_hostiles})
        return 11
    enemy = remaining_hostiles[-1]
    exact = unit_choices(int(planet_buster["id"]), int(enemy["tile_id"]))
    launch = next(
        (item for item in exact.get("choices", []) if item.get("command") == "launch_missile"),
        None,
    )
    if not launch or launch.get("confirm_atrocity") != 1:
        emit("failure", {"stage": "planet_buster_query", "choices": exact})
        return 12
    refused = command(
        exact, "launch_missile", unit_id=launch["unit_id"],
        target_tile_id=launch["target_tile_id"],
    )
    if refused.get("error", {}).get("code") != "atrocity_confirmation_required":
        emit("failure", {"stage": "planet_buster_confirmation", "result": refused})
        return 13
    launched = command(
        exact, "launch_missile", unit_id=launch["unit_id"],
        target_tile_id=launch["target_tile_id"],
        confirm_atrocity=1,
    )
    action = wait_action(int(launched.get("action_id", -1)), deadline)
    emit("planet_buster", {"refused": refused, "launched": launched, "action": action})
    if not launched.get("ok") or action.get("status") != "completed":
        return 14

    emit("pass", {
        "compact_two_stage_targeting": True,
        "conventional_native_combat": True,
        "tectonic_native_effect": True,
        "fungal_native_effect": True,
        "planet_buster_confirmation": True,
        "planet_buster_native_atrocity": True,
        "pixels_or_ui_input_used": False,
        "target_tile_id_only": True,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
