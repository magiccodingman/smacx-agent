#!/usr/bin/env python3
"""Contained native-upkeep regression for production-completion notices."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from smacx_controller import bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], command_name: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command", timeout=10, command=command_name,
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], **arguments,
    )


def wait_for_turn(deadline: float) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") == "turn":
            return snapshot
        handled, result = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "wait_for_turn", "snapshot": snapshot, "result": result})
            return None
        time.sleep(0.05)
    return None


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 150
    if not wait_for_turn(deadline):
        return 3

    bases: list[dict[str, Any]] = []
    fixture_deadline = time.monotonic() + 5
    while time.monotonic() < fixture_deadline:
        bases = bridge_request("list_bases", limit=20).get("items", [])
        if bases and bases[0].get("production_name") == "Recreation Commons":
            break
        time.sleep(0.05)
    if not bases or bases[0].get("production_name") != "Recreation Commons":
        emit("failure", {"stage": "fixture_not_staged", "bases": bases})
        return 4
    base = bases[0]
    emit("fixture_staged", {
        "base_id": base["id"],
        "base_name": base["name"],
        "production_id": base["production_id"],
        "production_name": base["production_name"],
        "minerals_accumulated": base["minerals"]["accumulated"],
        "governor": base["governor"],
    })

    while True:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") != "turn":
            break
        ready = bridge_request("list_units", scope="own", limit=200).get("items", [])
        ready = [item for item in ready if item.get("ready")]
        if not ready:
            break
        choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=ready[0]["id"])
        skipped = next(
            (item for item in choices.get("choices", [])
             if item.get("command") == "skip_unit"), None,
        )
        if not skipped or not command(choices, "skip_unit", unit_id=ready[0]["id"]).get("ok"):
            emit("failure", {"stage": "skip_ready_unit", "unit": ready[0], "choices": choices})
            return 6

    if snapshot.get("interaction", {}).get("kind") == "turn":
        game_choices = bridge_request("semantic_choices", kind="game_management")
        ended = command(game_choices, "end_turn")
        if not ended.get("ok"):
            emit("failure", {"stage": "end_turn", "result": ended})
            return 7

    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("popup_label", "").startswith("PRODUCE"):
            break
        time.sleep(0.05)
    else:
        emit("failure", {"stage": "production_popup", "snapshot": snapshot})
        return 8

    choices = bridge_request("semantic_choices", kind="interaction")
    context = next(
        (item for item in choices.get("choices", [])
         if item.get("event") == "production_completed"), None,
    )
    acknowledgement = next(
        (item for item in choices.get("choices", [])
         if item.get("command") == "acknowledge_popup"), None,
    )
    if not context or not acknowledgement \
            or int(context.get("base_id", -1)) != int(base["id"]) \
            or context.get("base_name") != base.get("name") \
            or not context.get("item_name") \
            or context.get("governor_managed") is not True:
        emit("failure", {"stage": "structured_context", "choices": choices, "base": base})
        return 9

    acknowledged = command(choices, "acknowledge_popup")
    if not acknowledged.get("ok"):
        emit("failure", {"stage": "acknowledge", "result": acknowledged})
        return 10
    emit("pass", {
        "popup_label": snapshot["interaction"]["popup_label"],
        "context": context,
        "native_upkeep_completed_build": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
