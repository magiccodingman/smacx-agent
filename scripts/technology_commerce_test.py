#!/usr/bin/env python3
"""Contained regression for semantic native technology purchases."""

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
        "semantic_command",
        timeout=10,
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

    before = bridge_request("list_technologies")  # initializes contained fixture
    before_ids = {int(item["id"]) for item in before.get("items", [])}

    offer_snapshot: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("popup_label") in {"BUYTECH0", "BUYTECH1"}:
            offer_snapshot = snapshot
            break
        time.sleep(0.05)
    if not offer_snapshot:
        emit("failure", {"stage": "native_purchase_popup"})
        return 4

    choices = bridge_request("semantic_choices", kind="interaction")
    terms = next(
        (item for item in choices.get("choices", [])
         if item.get("offer_type") == "technology_purchase"), None,
    )
    accept = next(
        (item for item in choices.get("choices", []) if item.get("response") == "accept"),
        None,
    )
    if not terms or not accept or not accept.get("affordable"):
        emit("failure", {"stage": "structured_purchase", "choices": choices})
        return 5
    tech_id = int(terms.get("technology_id", -1))
    price = int(terms.get("energy_credits", -1))
    if tech_id < 0 or tech_id in before_ids or price <= 0:
        emit("failure", {"stage": "purchase_terms", "terms": terms, "before": before})
        return 6

    energy_before = int(offer_snapshot.get("faction", {}).get("energy_credits", -1))
    accepted = command(choices, command="respond_to_diplomatic_offer", response="accept")
    emit("accepted", {"terms": terms, "result": accepted})
    if not accepted.get("ok"):
        return 7

    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        after = bridge_request("list_technologies")
        after_ids = {int(item["id"]) for item in after.get("items", [])}
        if tech_id in after_ids:
            energy_after = int(snapshot.get("faction", {}).get("energy_credits", -1))
            if energy_before - energy_after != price:
                emit("failure", {"stage": "native_energy_payment", "before": energy_before,
                                 "after": energy_after, "price": price})
                return 8
            emit("pass", {
                "native_technology_transfer": True,
                "native_energy_payment": True,
                "technology_id": tech_id,
                "price": price,
                "pixels_or_ui_input_used": False,
            })
            return 0
        time.sleep(0.05)

    emit("failure", {"stage": "purchase_completion", "technology_id": tech_id})
    return 9


if __name__ == "__main__":
    sys.exit(main())
