#!/usr/bin/env python3
"""Contained regression for the semantic end-turn decision gate."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from smacx_controller import bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 120
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") == "turn" \
                and int(snapshot.get("faction", {}).get("ready_units", 0)) > 0:
            break
        handled, result = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "wait_for_turn", "snapshot": snapshot, "result": result})
            return 3
        time.sleep(0.05)
    else:
        emit("failure", {"stage": "turn_timeout", "snapshot": snapshot})
        return 4

    choices = bridge_request("semantic_choices", kind="game_management")
    end_choice = next(
        (choice for choice in choices.get("choices", [])
         if choice.get("command") == "end_turn"), None,
    )
    blocked = next(
        (choice for choice in choices.get("choices", [])
         if choice.get("id") == "turn:end_blocked"), None,
    )
    ready_count = int(snapshot["faction"]["ready_units"])
    if end_choice or not blocked or int(blocked.get("ready_unit_count", -1)) != ready_count:
        emit("failure", {"stage": "choice_gate", "snapshot": snapshot, "choices": choices})
        return 5

    attempted = bridge_request(
        "semantic_command", timeout=10, command="end_turn",
        match_id=choices["match_id"], session_id=choices["session_id"],
        expected_revision=choices["revision"],
    )
    if attempted.get("ok") or attempted.get("error", {}).get("code") != "units_still_ready":
        emit("failure", {"stage": "execution_gate", "result": attempted})
        return 6

    after = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
    if int(after.get("turn", -1)) != int(snapshot["turn"]):
        emit("failure", {"stage": "turn_mutated", "before": snapshot, "after": after})
        return 7
    emit("pass", {
        "ready_unit_count": ready_count,
        "end_turn_not_enumerated": True,
        "forced_command_rejected": True,
        "turn_unchanged": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
