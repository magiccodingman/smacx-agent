#!/usr/bin/env python3
"""Contained regression for semantic Global Energy Market initiation."""

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


def snapshot(deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        try:
            value = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if value:
            return value
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
        current = snapshot(deadline)
        if current.get("interaction", {}).get("kind") == "turn":
            break
        handled, result = handle_interaction(current)
        if not handled:
            emit("failure", {"stage": "opening", "result": result, "snapshot": current})
            return 3
    else:
        return 3

    choices = bridge_request("semantic_choices", kind="game_management")
    initiate = next((item for item in choices.get("choices", [])
                     if item.get("command") == "corner_global_energy_market"), None)
    if not initiate or initiate.get("confirm_corner_market") != 1 \
            or int(initiate.get("cost", 0)) < 1000 \
            or int(initiate.get("available_energy", 0)) < int(initiate.get("cost", 0)):
        emit("failure", {"stage": "initiation_choice", "choices": choices})
        return 4

    refused = command(choices, "corner_global_energy_market")
    if refused.get("error", {}).get("code") != "corner_market_confirmation_required":
        emit("failure", {"stage": "confirmation_gate", "result": refused})
        return 5
    initiated = command(
        choices, "corner_global_energy_market", confirm_corner_market=1,
    )
    if not initiated.get("ok") \
            or int(initiated.get("cost", -1)) != int(initiate["cost"]) \
            or int(initiated.get("completion_turn", 0)) <= 0:
        emit("failure", {"stage": "initiation", "result": initiated,
                         "choice": initiate})
        return 6

    notice: dict[str, Any] = {}
    while time.monotonic() < deadline:
        current = snapshot(deadline)
        if current.get("interaction", {}).get("popup_label") == "CORNERING":
            notice = bridge_request("semantic_choices", kind="interaction")
            break
        time.sleep(0.05)
    acknowledgement = next((item for item in notice.get("choices", [])
                            if item.get("command") == "acknowledge_popup"), None)
    if not acknowledgement:
        emit("failure", {"stage": "native_notice", "choices": notice})
        return 7
    acknowledged = command(notice, "acknowledge_popup")
    if not acknowledged.get("ok"):
        emit("failure", {"stage": "notice_acknowledgement", "result": acknowledged})
        return 8

    while time.monotonic() < deadline:
        current = snapshot(deadline)
        if current.get("interaction", {}).get("kind") != "turn":
            time.sleep(0.05)
            continue
        status = bridge_request("semantic_choices", kind="game_management")
        active = next((item for item in status.get("choices", [])
                       if item.get("id") == "economic_victory:active"), None)
        if active and int(active.get("completion_turn", -1)) == int(initiated["completion_turn"]):
            emit("pass", {
                "quoted_cost": initiate["cost"],
                "confirmation_gate_verified": True,
                "native_cornering_notice": True,
                "completion_turn": active["completion_turn"],
                "completion_year": active["completion_year"],
                "active_plan_observable": True,
                "pixels_or_ui_input_used": False,
            })
            return 0
        time.sleep(0.05)
    return 9


if __name__ == "__main__":
    sys.exit(main())
