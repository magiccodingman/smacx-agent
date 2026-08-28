#!/usr/bin/env python3
"""Contained native regression for a priced joint-Vendetta counteroffer."""

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

    deadline = time.monotonic() + 90
    counteroffer: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        interaction = snapshot.get("interaction", {})
        label = interaction.get("popup_label", "")
        if label == "MAYBEWARPRICE" or str(label).startswith("MAYBEWARTECH"):
            counteroffer = bridge_request("semantic_choices", kind="interaction")
            break
        if interaction.get("kind") not in {"turn", "waiting_for_engine", "waiting_for_turn"}:
            handled, reason = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"reason": reason, "interaction": interaction})
                return 3
        time.sleep(0.05)
    if not counteroffer:
        emit("failure", {"reason": "joint_attack_counteroffer_not_reached"})
        return 3

    terms = next(
        (item for item in counteroffer.get("choices", [])
         if item.get("offer_type") == "joint_attack_counteroffer"),
        None,
    )
    accept = next(
        (item for item in counteroffer.get("choices", [])
         if item.get("id") == "joint_attack_counteroffer:accept"),
        None,
    )
    reject = next(
        (item for item in counteroffer.get("choices", [])
         if item.get("id") == "joint_attack_counteroffer:reject"),
        None,
    )
    if not terms or not accept or not reject:
        emit("failure", {"reason": "counteroffer_contract", "choices": counteroffer})
        return 4
    if int(terms.get("target_faction_id", -1)) < 1:
        emit("failure", {"reason": "missing_target", "terms": terms})
        return 4

    before = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
    accepted = command(counteroffer, command="respond_to_diplomatic_offer", response="accept")
    emit("accepted", {"result": accepted, "terms": terms})
    if not accepted.get("ok"):
        return 5

    saw_native_outcome = False
    final_snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        interaction = snapshot.get("interaction", {})
        label = str(interaction.get("popup_label", ""))
        if label.startswith("INCITED") or label.startswith("VENDETTA"):
            saw_native_outcome = True
        if interaction.get("kind") == "turn":
            final_snapshot = snapshot
            break
        if interaction.get("kind") == "popup":
            handled, reason = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"reason": reason, "interaction": interaction})
                return 6
        time.sleep(0.05)
    if not final_snapshot:
        emit("failure", {"reason": "native_continuation_did_not_finish"})
        return 6

    if terms.get("payment_type") == "energy":
        price = int(terms.get("energy_credits", -1))
        before_energy = int(before.get("faction", {}).get("energy_credits", -1))
        after_energy = int(final_snapshot.get("faction", {}).get("energy_credits", -1))
        if price < 0 or before_energy - after_energy != price:
            emit("failure", {
                "reason": "native_energy_delta",
                "price": price,
                "before": before_energy,
                "after": after_energy,
            })
            return 7

    emit("passed", {
        "payment_type": terms.get("payment_type"),
        "target_faction_id": terms.get("target_faction_id"),
        "native_outcome_notice_seen": saw_native_outcome,
        "native_continuation_completed": True,
        "pixels_or_input_used": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
