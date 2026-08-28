#!/usr/bin/env python3
"""Prove the executable's unreachable Script.txt unit row stays unexposed."""

from __future__ import annotations

import json
import time
from typing import Any

from smacx_controller import BridgeUnavailable, bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        timeout=10,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


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

    deadline = time.monotonic() + 30
    choices: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        interaction = snapshot.get("interaction", {})
        if interaction.get("kind") == "popup" and interaction.get("popup_label") == "PROPOSAL":
            choices = bridge_request("semantic_choices", kind="interaction")
            break
        if interaction.get("kind") != "turn":
            handled, reason = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"reason": reason, "interaction": interaction})
                return 3
        time.sleep(0.1)
    if not choices:
        emit("failure", {"reason": "proposal_not_reached"})
        return 3

    leaked = next(
        (item for item in choices.get("choices", [])
         if item.get("option") == "offer_units"
         or item.get("id") == "diplomacy:offer_units_unavailable"
         or item.get("command") == "give_unit"),
        None,
    )
    if leaked is not None:
        emit("failure", {"reason": "unsafe_choice_contract", "choices": choices})
        return 4

    fabricated = command(
        choices,
        command="choose_diplomacy_option",
        option="offer_units",
    )
    if fabricated.get("error", {}).get("code") != "proposal_unit_offer_unreachable":
        emit("failure", {"reason": "fabricated_command_not_rejected", "result": fabricated})
        return 5

    fresh = bridge_request("semantic_choices", kind="interaction")
    cancel = next(
        (item for item in fresh.get("choices", []) if item.get("option") == "cancel"),
        None,
    )
    if cancel is None:
        emit("failure", {"reason": "safe_exit_missing", "choices": fresh})
        return 6
    cancelled = command(fresh, command="choose_diplomacy_option", option="cancel")
    if not cancelled.get("ok"):
        emit("failure", {"reason": "safe_exit_failed", "result": cancelled})
        return 7

    emit("passed", {
        "unreachable_script_row_not_exposed": True,
        "fabricated_command_rejected": True,
        "native_cancel_succeeded": True,
        "pixels_or_input_used": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
