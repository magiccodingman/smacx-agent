#!/usr/bin/env python3
"""Contained regression for a native pre-vote Council bargain."""

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
    deadline = time.monotonic() + 150
    offer_snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            current = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        label = current.get("interaction", {}).get("popup_label", "")
        if label.startswith("BUYVOTE") and label != "BUYVOTE0":
            offer_snapshot = current
            break
        if label:
            handled, result = handle_interaction(current)
            if not handled:
                emit("failure", {"stage": "opening", "label": label, "result": result})
                return 3
        time.sleep(0.05)
    if not offer_snapshot:
        emit("failure", {"stage": "native_vote_offer"})
        return 4

    choices = bridge_request("semantic_choices", kind="interaction")
    terms = next((item for item in choices.get("choices", [])
                  if item.get("offer_type") == "council_vote_bargain"), None)
    energy = next((item for item in choices.get("choices", [])
                   if item.get("payment") == "energy"), None)
    reject = next((item for item in choices.get("choices", [])
                   if item.get("payment") == "none"), None)
    if not terms or not energy or not reject or not energy.get("affordable"):
        emit("failure", {"stage": "structured_bargain", "choices": choices})
        return 5
    price = int(terms.get("energy_credits", -1))
    energy_before = int(offer_snapshot.get("faction", {}).get("energy_credits", -1))
    paid = command(
        choices, command="respond_to_council_vote_bargain", payment="energy",
    )
    if not paid.get("ok"):
        emit("failure", {"stage": "submission", "result": paid})
        return 6

    while time.monotonic() < deadline:
        current = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        label = current.get("interaction", {}).get("popup_label", "")
        if label:
            handled, result = handle_interaction(current)
            if not handled:
                emit("failure", {"stage": "followup", "label": label, "result": result})
                return 7
            continue
        if current.get("interaction", {}).get("kind") == "turn":
            energy_after = int(current.get("faction", {}).get("energy_credits", -1))
            if energy_before - energy_after != price:
                emit("failure", {"stage": "native_payment", "before": energy_before,
                                 "after": energy_after, "price": price})
                return 8
            emit("pass", {
                "requested_ballot": terms.get("requested_ballot"),
                "counterpart_faction_id": terms.get("counterpart_faction_id"),
                "quoted_price": price,
                "native_energy_payment": True,
                "technology_alternative_count": len(terms.get("technologies", [])),
                "pixels_or_ui_input_used": False,
            })
            return 0
        time.sleep(0.05)
    return 9


if __name__ == "__main__":
    sys.exit(main())
