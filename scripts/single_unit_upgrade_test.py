#!/usr/bin/env python3
"""Contained regression for exact, semantic per-vehicle upgrades."""

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


def owned_units() -> list[dict[str, Any]]:
    return bridge_request("list_units", scope="own", limit=300).get("items", [])


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

    deadline = time.monotonic() + 150
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

        before_units = owned_units()
        selected: tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None = None
        for unit in before_units:
            if not unit.get("ready"):
                continue
            choices = bridge_request(
                "semantic_choices", kind="unit_actions", unit_id=int(unit["id"]),
            )
            action = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "upgrade_unit"),
                None,
            )
            if action:
                selected = unit, choices, action
                break
        if selected is None:
            emit("waiting", {"reason": "fixture upgrade choice not ready", "units": before_units})
            time.sleep(0.1)
            continue

        unit, choices, action = selected
        unit_id = int(unit["id"])
        source_id = int(action["source_prototype_id"])
        target_id = int(action["target_prototype_id"])
        quoted_cost = int(action["energy_cost"])
        before_source_count = sum(int(item.get("prototype_id", -1)) == source_id
                                  for item in before_units)
        before_target_count = sum(int(item.get("prototype_id", -1)) == target_id
                                  for item in before_units)
        if action.get("confirm_upgrade") != 1 or not action.get("consumes_turn") \
                or not action.get("affordable") or before_source_count < 2:
            emit("failure", {"stage": "choice_contract", "action": action,
                             "source_count": before_source_count})
            return 3

        invalid = command(
            choices,
            "upgrade_unit",
            unit_id=unit_id,
            target_prototype_id=source_id,
            confirm_upgrade=1,
        )
        refused = command(
            choices,
            "upgrade_unit",
            unit_id=unit_id,
            target_prototype_id=target_id,
        )
        emit("guards", {"invalid_target": invalid, "confirmation": refused})
        if invalid.get("error", {}).get("code") != "illegal_single_unit_upgrade" \
                or refused.get("error", {}).get("code") != "single_unit_upgrade_confirmation_required" \
                or int(refused.get("energy_cost", -1)) != quoted_cost:
            return 4

        upgraded = command(
            choices,
            "upgrade_unit",
            unit_id=unit_id,
            target_prototype_id=target_id,
            confirm_upgrade=1,
        )
        emit("upgrade", upgraded)
        if not upgraded.get("ok") or int(upgraded.get("energy_spent", -1)) != quoted_cost \
                or not upgraded.get("turn_consumed"):
            return 5

        after_units = owned_units()
        upgraded_unit = next((item for item in after_units if int(item["id"]) == unit_id), None)
        after_source_count = sum(int(item.get("prototype_id", -1)) == source_id
                                 for item in after_units)
        after_target_count = sum(int(item.get("prototype_id", -1)) == target_id
                                 for item in after_units)
        if upgraded_unit is None \
                or int(upgraded_unit.get("prototype_id", -1)) != target_id \
                or upgraded_unit.get("ready") is not False \
                or after_source_count != before_source_count - 1 \
                or after_target_count != before_target_count + 1:
            emit("failure", {"stage": "exact_effect", "upgraded_unit": upgraded_unit,
                             "before_source_count": before_source_count,
                             "after_source_count": after_source_count,
                             "before_target_count": before_target_count,
                             "after_target_count": after_target_count})
            return 6

        stale = command(
            choices,
            "upgrade_unit",
            unit_id=unit_id,
            target_prototype_id=target_id,
            confirm_upgrade=1,
        )
        emit("stale_guard", stale)
        if stale.get("error", {}).get("code") != "stale_state":
            return 7

        emit("pass", {
            "native_candidate_filtering": True,
            "confirmation_guard": True,
            "exact_single_vehicle_changed": True,
            "other_source_vehicle_preserved": True,
            "quoted_energy_charged": True,
            "remaining_turn_consumed": True,
            "stale_replay_rejected": True,
            "pixels_or_ui_input_used": False,
        })
        return 0
    return 8


if __name__ == "__main__":
    sys.exit(main())
