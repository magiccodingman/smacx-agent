#!/usr/bin/env python3
"""Contained native-effect regression for buying a negotiated base."""

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
    deadline = time.monotonic() + 140
    offer_snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            current = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        label = current.get("interaction", {}).get("popup_label")
        if label == "PAYBASESWAP":
            offer_snapshot = current
            break
        if label:
            handled, result = handle_interaction(current)
            if not handled:
                emit("failure", {"stage": "opening", "label": label, "result": result})
                return 3
        time.sleep(0.05)
    if not offer_snapshot:
        emit("failure", {"stage": "native_offer"})
        return 4

    choices = bridge_request("semantic_choices", kind="interaction")
    terms = next((item for item in choices.get("choices", [])
                  if item.get("offer_type") == "base_purchase"), None)
    accept = next((item for item in choices.get("choices", [])
                   if item.get("id") == "base_purchase:accept"), None)
    if not terms or not accept or not accept.get("affordable"):
        emit("failure", {"stage": "structured_offer", "choices": choices})
        return 5
    base_id = int(terms.get("target_base_id", -1))
    base_name = str(terms.get("base_name", ""))
    price = int(terms.get("energy_credits", -1))
    energy_before = int(offer_snapshot.get("faction", {}).get("energy_credits", -1))
    result = command(choices, command="respond_to_diplomatic_offer", response="accept")
    if not result.get("ok"):
        emit("failure", {"stage": "submission", "result": result})
        return 6

    while time.monotonic() < deadline:
        current = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        label = current.get("interaction", {}).get("popup_label")
        if label:
            handled, interaction_result = handle_interaction(current)
            if not handled:
                emit("failure", {"stage": "followup", "label": label,
                                 "result": interaction_result})
                return 7
            continue
        if current.get("interaction", {}).get("kind") != "turn":
            time.sleep(0.05)
            continue
        bases = bridge_request("list_bases", limit=200).get("items", [])
        acquired = next((item for item in bases if int(item.get("id", -1)) == base_id), None)
        energy_after = int(current.get("faction", {}).get("energy_credits", -1))
        if acquired and acquired.get("name") == base_name and energy_before - energy_after == price:
            emit("pass", {
                "native_base_transfer": True,
                "native_energy_payment": True,
                "base_id": base_id,
                "base_name": base_name,
                "price": price,
                "pixels_or_ui_input_used": False,
            })
            return 0
        emit("failure", {"stage": "native_effects", "acquired": acquired,
                         "energy_before": energy_before, "energy_after": energy_after,
                         "price": price})
        return 8
    return 9


if __name__ == "__main__":
    sys.exit(main())
