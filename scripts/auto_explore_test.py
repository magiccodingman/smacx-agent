#!/usr/bin/env python3
"""Contained regression for the native automated Explore order."""

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


def units() -> list[dict[str, Any]]:
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

        before_units = units()
        scout = next(
            (item for item in before_units
             if item.get("ready") and item.get("roles", {}).get("combat")),
            None,
        )
        colony = next(
            (item for item in before_units
             if item.get("ready") and item.get("roles", {}).get("colony")),
            None,
        )
        if scout is None or colony is None:
            emit("waiting", {"reason": "ready scout/colony unavailable", "units": before_units})
            time.sleep(0.1)
            continue

        scout_id = int(scout["id"])
        scout_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=scout_id,
        )
        explore = next(
            (item for item in scout_choices.get("choices", [])
             if item.get("command") == "auto_explore_unit"),
            None,
        )
        if explore is None or not explore.get("persistent") \
                or not explore.get("native_automation"):
            emit("failure", {"stage": "choice_contract", "choices": scout_choices})
            return 3

        game_before = bridge_request("semantic_choices", kind="game_management")
        before_block = next(
            (item for item in game_before.get("choices", [])
             if item.get("id") == "turn:end_blocked"),
            {},
        )
        before_ready = int(before_block.get("ready_unit_count", -1))

        assigned = command(scout_choices, "auto_explore_unit", unit_id=scout_id)
        emit("assigned", assigned)
        if not assigned.get("ok") or assigned.get("ready") is not False:
            return 4

        after_assignment_units = units()
        ordered_scout = next(item for item in after_assignment_units if int(item["id"]) == scout_id)
        ordered_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=scout_id,
        )
        ordered_commands = [item.get("command") for item in ordered_choices.get("choices", [])]
        game_after = bridge_request("semantic_choices", kind="game_management")
        after_block = next(
            (item for item in game_after.get("choices", [])
             if item.get("id") == "turn:end_blocked"),
            {},
        )
        after_ready = int(after_block.get("ready_unit_count", 0))
        emit("persistent_state", {
            "unit": ordered_scout,
            "choices": ordered_choices,
            "ready_before": before_ready,
            "ready_after": after_ready,
        })
        if ordered_scout.get("ready") is not False \
                or ordered_scout.get("order_name") != "auto_explore" \
                or ordered_choices.get("reason") != "auto_explore" \
                or ordered_commands != ["activate_unit"] \
                or before_ready < 1 or after_ready != before_ready - 1:
            return 5

        colony_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=int(colony["id"]),
        )
        fabricated = command(
            colony_choices, "auto_explore_unit", unit_id=int(colony["id"]),
        )
        emit("noncombat_guard", fabricated)
        if fabricated.get("error", {}).get("code") != "auto_explore_unavailable":
            return 6

        activated = command(ordered_choices, "activate_unit", unit_id=scout_id)
        emit("activated", activated)
        if not activated.get("ok") or activated.get("old_automation") != "auto_explore" \
                or activated.get("ready") is not True:
            return 7
        final_unit = next(item for item in units() if int(item["id"]) == scout_id)
        if final_unit.get("ready") is not True or final_unit.get("order_name") != "none":
            emit("failure", {"stage": "activation_effect", "unit": final_unit})
            return 8

        stale = command(scout_choices, "auto_explore_unit", unit_id=scout_id)
        emit("stale_guard", stale)
        if stale.get("error", {}).get("code") != "stale_state":
            return 9

        emit("pass", {
            "native_explore_state": True,
            "decision_gate_suppressed": True,
            "only_activation_while_ordered": True,
            "noncombat_fabrication_rejected": True,
            "native_wake_cancels_order": True,
            "stale_replay_rejected": True,
            "pixels_or_ui_input_used": False,
        })
        return 0
    return 10


if __name__ == "__main__":
    sys.exit(main())
