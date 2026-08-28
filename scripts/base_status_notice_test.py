#!/usr/bin/env python3
"""Contained regression for structured base-status information notices."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from smacx_controller import BridgeUnavailable, bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], command_name: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        timeout=10,
        command=command_name,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def wait_for_turn(deadline: float) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if snapshot.get("interaction", {}).get("kind") == "turn":
            return snapshot
        handled, result = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "opening_interaction", "snapshot": snapshot,
                             "result": result})
            return None
        time.sleep(0.05)
    return None


def main() -> int:
    started = new_game(
        wait_seconds=60,
        difficulty=1,
        world_size=0,
        faction_id=4,
        blind_research=True,
        initial_research_priority=1,
        narrative_ui=False,
        tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 120
    if not wait_for_turn(deadline):
        return 3

    bases = bridge_request("list_bases", limit=20).get("items", [])
    if not bases:
        emit("failure", {"stage": "base_fixture"})
        return 4
    base = bases[0]

    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("popup_label") == "STARVE":
            break
        time.sleep(0.05)
    else:
        emit("failure", {"stage": "starve_popup", "snapshot": snapshot})
        return 5

    choices = bridge_request("semantic_choices", kind="interaction")
    acknowledgement = next(
        (item for item in choices.get("choices", [])
         if item.get("command") == "acknowledge_popup"),
        None,
    )
    context = next(
        (item for item in choices.get("choices", [])
         if item.get("event") == "starvation_population_loss"),
        None,
    )
    if not acknowledgement or not context \
            or int(context.get("base_id", -1)) != int(base["id"]) \
            or context.get("base_name") != base.get("name"):
        emit("failure", {"stage": "structured_context", "choices": choices,
                         "base": base})
        return 6

    acknowledged = command(choices, "acknowledge_popup")
    duplicate = command(choices, "acknowledge_popup")
    duplicate_code = duplicate.get("error", {}).get("code")
    if not acknowledged.get("ok") \
            or duplicate.get("ok") \
            or duplicate_code not in {"stale_state", "popup_transition_pending"} \
            or not wait_for_turn(deadline):
        emit("failure", {"stage": "acknowledge", "result": acknowledged,
                         "duplicate": duplicate})
        return 7

    emit("pass", {
        "native_popup_label": "STARVE",
        "event": context["event"],
        "base_id": context["base_id"],
        "base_name": context["base_name"],
        "duplicate_submission_rejected": duplicate_code,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
