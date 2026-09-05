#!/usr/bin/env python3
"""Contained native-effect regression for an atomic semantic energy gift."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from smacx_controller import BridgeUnavailable, bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command", timeout=10,
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], **arguments,
    )


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 90
    amount = 125
    submitted = False
    energy_before = -1
    while time.monotonic() < deadline:
        try:
            current = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if not current:
            continue
        interaction = current.get("interaction", {})
        label = interaction.get("popup_label", "")
        if label in {"COUNTER1", "OFFERENERGY"} and not submitted:
            choices = bridge_request("semantic_choices", kind="interaction")
            emit("gift_choices", choices)
            gift = next((item for item in choices.get("choices", [])
                         if item.get("command") == "give_energy_gift"), None)
            if not gift or int(gift.get("amount_min", -1)) != 1 \
                    or int(gift.get("amount_max", -1)) != 500 \
                    or gift.get("amount_options") != [500, 250, 125, 50, 25]:
                emit("failure", {"stage": "gift_bounds", "choices": choices})
                return 3
            energy_before = int(current.get("faction", {}).get("energy_credits", -1))
            result = command(choices, command="give_energy_gift", amount=amount)
            emit("gift_submission", result)
            if not result.get("ok"):
                return 4
            if int(result.get("player_energy_before", -1)) != energy_before \
                    or int(result.get("player_energy_after", -1)) != energy_before - amount \
                    or not result.get("native_amount_prompt_seen"):
                emit("failure", {"stage": "atomic_result", "result": result})
                return 5
            submitted = True
            continue
        if label:
            handled, result = handle_interaction(current)
            if not handled:
                emit("failure", {"stage": "interaction", "label": label, "result": result})
                return 6
            continue
        if submitted and interaction.get("kind") == "turn":
            energy_after = int(current.get("faction", {}).get("energy_credits", -1))
            if energy_before - energy_after == amount:
                emit("pass", {
                    "gift_amount": amount,
                    "native_energy_payment": True,
                    "native_nested_dialogs_driven_semantically": True,
                    "pixels_or_ui_input_used": False,
                })
                return 0
            emit("failure", {"stage": "native_effect", "before": energy_before,
                             "after": energy_after})
            return 7
        time.sleep(0.05)
    emit("failure", {"stage": "deadline", "submitted": submitted})
    return 8


if __name__ == "__main__":
    sys.exit(main())
