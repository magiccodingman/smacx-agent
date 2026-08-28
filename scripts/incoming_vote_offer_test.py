#!/usr/bin/env python3
"""Contained contract regression for an AI offer to buy the human Council vote."""

from __future__ import annotations

import json
import os
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
    technology_offer = os.environ.get("SMACX_AGENT_TEST_INCOMING_VOTE_OFFER") == "tech"
    accept_offer = os.environ.get("SMACX_AGENT_TEST_INCOMING_VOTE_ACCEPT") == "1"
    expected_label = "VOTEFORMETECH" if technology_offer else "VOTEFORME"
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
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
        if interaction.get("kind") == "popup" and interaction.get("popup_label") == expected_label:
            choices = bridge_request("semantic_choices", kind="interaction")
            break
        if interaction.get("kind") != "turn":
            handled, reason = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "opening", "reason": reason,
                                 "interaction": interaction})
                return 3
        time.sleep(0.1)
    if not choices:
        emit("failure", {"stage": "offer_not_reached"})
        return 4

    before_snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
    before_technology_ids = {
        int(item["id"]) for item in bridge_request("list_technologies").get("items", [])
    }

    expected_offer_type = (
        "council_vote_for_technologies" if technology_offer
        else "council_vote_for_energy"
    )
    terms = next((item for item in choices.get("choices", [])
                  if item.get("offer_type") == expected_offer_type), None)
    accept = next((item for item in choices.get("choices", [])
                   if item.get("response") == "accept"), None)
    reject = next((item for item in choices.get("choices", [])
                   if item.get("response") == "reject"), None)
    valid_payment = (
        isinstance(terms.get("technologies") if terms else None, list)
        and len(terms.get("technologies", [])) == 2
        and len(accept.get("technology_ids", []) if accept else []) == 2
    ) if technology_offer else int(terms.get("energy_credits_received", -1) if terms else -1) == 125
    if not terms or not accept or not reject or not valid_payment \
            or int(accept.get("candidate_faction_id", -1)) < 1 \
            or int(accept.get("confirm_vote_commitment", 0)) != 1:
        emit("failure", {"stage": "structured_terms", "choices": choices})
        return 5

    unconfirmed = command(
        choices, command="respond_to_incoming_vote_offer", response="accept",
        candidate_faction_id=int(accept["candidate_faction_id"]),
    )
    if unconfirmed.get("error", {}).get("code") != "vote_commitment_confirmation_required":
        emit("failure", {"stage": "confirmation_gate", "result": unconfirmed})
        return 6

    fresh = bridge_request("semantic_choices", kind="interaction")
    if accept_offer:
        accepted = command(
            fresh, command="respond_to_incoming_vote_offer", response="accept",
            candidate_faction_id=int(accept["candidate_faction_id"]),
            confirm_vote_commitment=1,
        )
        if not accepted.get("ok"):
            emit("failure", {"stage": "native_acceptance", "result": accepted})
            return 7
        expected_technology_ids = {int(value) for value in accept.get("technology_ids", [])}
        energy_before = int(before_snapshot.get("faction", {}).get("energy_credits", -1))
        payment_verified = False
        last_after_ids: set[int] = set()
        energy_after = energy_before
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
            if technology_offer:
                interaction = current.get("interaction", {})
                if interaction.get("kind") not in {"turn", "waiting_for_engine", "waiting_for_turn"}:
                    handled, reason = handle_interaction(current)
                    if not handled:
                        emit("failure", {
                            "stage": "technology_payment_notice",
                            "reason": reason,
                            "interaction": interaction,
                        })
                        return 8
                last_after_ids = {
                    int(item["id"])
                    for item in bridge_request("list_technologies").get("items", [])
                }
                payment_verified = bool(expected_technology_ids) \
                    and expected_technology_ids.isdisjoint(before_technology_ids) \
                    and expected_technology_ids.issubset(last_after_ids)
            else:
                energy_after = int(current.get("faction", {}).get("energy_credits", -1))
                payment_verified = energy_after - energy_before == 125
            if payment_verified:
                break
            time.sleep(0.05)
        if not payment_verified:
            emit("failure", {
                "stage": "native_payment_effect",
                "technology_offer": technology_offer,
                "expected_technology_ids": sorted(expected_technology_ids),
                "before_technology_ids": sorted(before_technology_ids),
                "after_technology_ids": sorted(last_after_ids),
                "energy_before": energy_before,
                "energy_after": energy_after,
                "accepted_result": accepted,
            })
            return 8
    else:
        declined = command(
            fresh, command="respond_to_incoming_vote_offer", response="reject",
        )
        if not declined.get("ok"):
            emit("failure", {"stage": "safe_rejection", "result": declined})
            return 9

    emit("passed", {
        "candidate_faction_id": accept["candidate_faction_id"],
        "payment_type": "technologies" if technology_offer else "energy",
        "quoted_payment": accept.get("technology_ids") if technology_offer else 125,
        "confirmation_gate_verified": True,
        "native_rejection_verified": not accept_offer,
        "native_acceptance_and_payment_verified": accept_offer,
        "pixels_or_input_used": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
