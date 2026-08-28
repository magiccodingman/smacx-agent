#!/usr/bin/env python3
"""Contained regression for Supreme Leader and final-score decisions."""

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
        "semantic_command", timeout=10, command=command_name,
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], **arguments,
    )


def observe(deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if snapshot:
            return snapshot
    return {}


def wait_for_label(deadline: float, label: str) -> dict[str, Any]:
    while time.monotonic() < deadline:
        snapshot = observe(deadline)
        if snapshot.get("interaction", {}).get("popup_label") == label:
            return snapshot
        time.sleep(0.05)
    return {}


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 140

    while time.monotonic() < deadline:
        snapshot = observe(deadline)
        if snapshot.get("interaction", {}).get("kind") == "turn":
            break
        handled, result = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "opening", "result": result, "snapshot": snapshot})
            return 3
    else:
        return 3

    if not wait_for_label(deadline, "ACCEDE"):
        emit("failure", {"stage": "supreme_leader_popup"})
        return 4
    council = bridge_request("semantic_choices", kind="interaction")
    accede = next((item for item in council.get("choices", [])
                   if item.get("response") == "accede"), None)
    defy = next((item for item in council.get("choices", [])
                 if item.get("response") == "defy"), None)
    context = next((item for item in council.get("choices", [])
                    if item.get("id") == "supreme_leader:context"), None)
    if not accede or not defy or defy.get("confirm_defiance") != 1 or not context:
        emit("failure", {"stage": "supreme_leader_choices", "choices": council})
        return 5

    refused = command(council, "respond_to_supreme_leader", response="defy")
    if refused.get("error", {}).get("code") != "defiance_confirmation_required":
        emit("failure", {"stage": "defiance_gate", "result": refused})
        return 6
    accepted = command(
        council, "respond_to_supreme_leader", response="defy", confirm_defiance=1,
    )
    if not accepted.get("ok"):
        emit("failure", {"stage": "defiance_submission", "result": accepted})
        return 7

    if not wait_for_label(deadline, "GAMEOVERMAN"):
        emit("failure", {"stage": "game_over_popup"})
        return 8
    game_over = bridge_request("semantic_choices", kind="interaction")
    finish = next((item for item in game_over.get("choices", [])
                   if item.get("response") == "finish"), None)
    keep_playing = next((item for item in game_over.get("choices", [])
                         if item.get("response") == "continue"), None)
    if not finish or not keep_playing:
        emit("failure", {"stage": "game_over_choices", "choices": game_over})
        return 9
    continued = command(game_over, "respond_to_game_over", response="continue")
    if not continued.get("ok"):
        emit("failure", {"stage": "continue_submission", "result": continued})
        return 10

    while time.monotonic() < deadline:
        snapshot = observe(deadline)
        if snapshot.get("interaction", {}).get("popup_label") != "GAMEOVERMAN":
            emit("pass", {
                "supreme_leader_context": context,
                "defiance_requires_confirmation": True,
                "final_score_finish_and_continue_exposed": True,
                "continued_after_final_score": True,
                "pixels_or_ui_input_used": False,
            })
            return 0
        time.sleep(0.05)
    return 11


if __name__ == "__main__":
    sys.exit(main())
