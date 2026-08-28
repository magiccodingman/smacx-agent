#!/usr/bin/env python3
"""Contained regression for guarded, semantic terrain-improvement destruction."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import BridgeUnavailable, bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def guarded(source: dict[str, Any], command: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        timeout=15,
        command=command,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def find_demolition_choice() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    units = bridge_request("list_units", scope="own", limit=300).get("items", [])
    for unit in units:
        if not unit.get("ready"):
            continue
        choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=int(unit["id"]),
        )
        actions = [
            item for item in choices.get("choices", [])
            if item.get("command") == "destroy_terrain_improvement"
        ]
        blocked = next(
            (item for item in choices.get("choices", [])
             if item.get("id") == "terrain_destruction:pact_blocked"),
            None,
        )
        if actions or blocked:
            return unit, choices, blocked
    raise AssertionError("fixture did not expose a demolition action or Pact rule status")


def current_tile_features(unit: dict[str, Any]) -> list[str]:
    result = bridge_request(
        "list_tiles", center_tile_id=int(unit["tile_id"]), radius=0,
    )
    tile = next(
        (item for item in result.get("items", [])
         if int(item["tile_id"]) == int(unit["tile_id"])),
    )
    if not tile.get("visible_now"):
        raise AssertionError(f"fixture tile is not currently visible: {tile}")
    return list(tile.get("features", []))


def main() -> int:
    mode = os.environ.get("SMACX_AGENT_TEST_TERRAIN_DESTRUCTION", "1")
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

    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if not snapshot:
            time.sleep(0.1)
            continue
        if snapshot.get("interaction", {}).get("kind") != "turn":
            handled = handle_interaction(snapshot)
            if handled:
                emit("interaction", handled)
            time.sleep(0.1)
            continue

        try:
            unit, choices, blocked = find_demolition_choice()
        except AssertionError as exc:
            emit("waiting", str(exc))
            time.sleep(0.1)
            continue

        initial_features = current_tile_features(unit)
        if mode == "pact":
            if blocked is None or blocked.get("reason") != "pact_forbids_hostile_action":
                emit("failure", {"stage": "pact_choice_contract", "choices": choices})
                return 3
            former_ids = blocked.get("former_ids_present", [])
            if 1 not in former_ids:
                emit("failure", {"stage": "pact_fixture_items", "blocked": blocked})
                return 4
            fabricated = guarded(
                choices,
                "destroy_terrain_improvement",
                unit_id=int(unit["id"]),
                former_id=1,
                confirm_destruction=1,
                confirm_hostility=1,
            )
            emit("pact_guard", fabricated)
            if fabricated.get("error", {}).get("code") != "pact_forbids_terrain_destruction":
                return 5
            emit("pass", {
                "pact_choice_suppressed": True,
                "fabricated_action_rejected": True,
                "features_unchanged": current_tile_features(unit) == initial_features,
                "pixels_or_ui_input_used": False,
            })
            return 0

        actions = [
            item for item in choices.get("choices", [])
            if item.get("command") == "destroy_terrain_improvement"
        ]
        by_former = {int(item["former_id"]): item for item in actions}
        # Native layering: the enricher subsumes its farm and the magtube
        # subsumes its road until the upper layer is removed.
        if not {1, 6, 9}.issubset(by_former) or 0 in by_former or 5 in by_former:
            emit("failure", {"stage": "native_layering_contract", "actions": actions})
            return 6
        action = by_former[1]
        is_foreign = mode == "foreign"
        if action.get("confirm_destruction") != 1 \
                or bool(action.get("confirm_hostility")) != is_foreign \
                or not action.get("destructive"):
            emit("failure", {"stage": "confirmation_contract", "action": action})
            return 7

        refused = guarded(
            choices,
            "destroy_terrain_improvement",
            unit_id=int(unit["id"]),
            former_id=1,
        )
        emit("destruction_guard", refused)
        if refused.get("error", {}).get("code") != "terrain_destruction_confirmation_required":
            return 8

        hidden_layer = guarded(
            choices,
            "destroy_terrain_improvement",
            unit_id=int(unit["id"]),
            former_id=0,
            confirm_destruction=1,
            confirm_hostility=1,
        )
        emit("hidden_layer_guard", hidden_layer)
        if hidden_layer.get("error", {}).get("code") != "terrain_improvement_unavailable":
            return 9

        if is_foreign:
            hostility_refused = guarded(
                choices,
                "destroy_terrain_improvement",
                unit_id=int(unit["id"]),
                former_id=1,
                confirm_destruction=1,
            )
            emit("hostility_guard", hostility_refused)
            if hostility_refused.get("error", {}).get("code") \
                    != "hostility_confirmation_required":
                return 10

        queued = guarded(
            choices,
            "destroy_terrain_improvement",
            unit_id=int(unit["id"]),
            former_id=1,
            confirm_destruction=1,
            confirm_hostility=1 if is_foreign else 0,
        )
        emit("queued", {"choice": action, "result": queued})
        if not queued.get("ok") or not queued.get("queued"):
            return 11

        queued_snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if queued_snapshot.get("revision") == choices.get("revision"):
            emit("failure", {"stage": "queued_revision_did_not_change"})
            return 12

        while time.monotonic() < deadline:
            status = bridge_request("action_status", action_id=int(queued["action_id"]))
            action_status = status.get("action", {})
            if action_status.get("status") == "pending":
                nested_snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
                if nested_snapshot.get("interaction", {}).get("kind") != "turn":
                    handle_interaction(nested_snapshot)
                time.sleep(0.1)
                continue
            emit("completed", status)
            if action_status.get("status") != "completed" \
                    or action_status.get("resolution") \
                    != "native_terrain_improvement_destroyed":
                return 13
            break
        else:
            return 14

        final_features = current_tile_features(unit)
        if "soil_enricher" in final_features or "farm" not in final_features:
            emit("failure", {
                "stage": "exact_native_effect",
                "before": initial_features,
                "after": final_features,
            })
            return 15
        if is_foreign:
            factions = bridge_request("list_factions").get("items", [])
            owner = int(action["territory_owner_faction_id"])
            relation = next((item for item in factions if int(item["id"]) == owner), None)
            if relation is None or not relation.get("relations", {}).get("vendetta"):
                emit("failure", {"stage": "native_diplomatic_effect", "factions": factions})
                return 16
        emit("pass", {
            "mode": mode,
            "native_layering_enforced": True,
            "destruction_confirmation_guarded": True,
            "hostility_confirmation_guarded": is_foreign,
            "exact_improvement_removed": True,
            "underlying_farm_preserved": True,
            "native_diplomacy_applied": is_foreign,
            "pixels_or_ui_input_used": False,
        })
        return 0

    emit("failure", {"stage": "timeout"})
    return 17


if __name__ == "__main__":
    raise SystemExit(main())
