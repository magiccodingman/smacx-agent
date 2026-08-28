#!/usr/bin/env python3
"""Contained semantic counteroffer/follow-up regression for technology demands."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from smacx_controller import BridgeUnavailable, bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], response: str, payment: str = "") -> dict[str, Any]:
    return bridge_request(
        "semantic_command", timeout=10, command="respond_to_diplomatic_offer",
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], response=response, payment=payment,
    )


def wait_for_label(deadline: float, label: str, opening: bool) -> dict[str, Any] | None:
    initialized = not opening
    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if snapshot.get("interaction", {}).get("popup_label") == label:
            return snapshot
        if opening and snapshot.get("interaction", {}).get("kind") == "turn":
            initialized = True
        elif opening and not initialized:
            handled, result = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "opening", "result": result,
                                 "snapshot": snapshot})
                return None
        time.sleep(0.05)
    return None


def main() -> int:
    mode = os.environ.get("SMACX_AGENT_TEST_TECH_DEMAND", "")
    if mode not in {"energy", "tech"}:
        raise SystemExit("SMACX_AGENT_TEST_TECH_DEMAND must be energy or tech")
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 100
    if not wait_for_label(deadline, "DEMANDTECH9A", opening=True):
        emit("failure", {"stage": "initial_counter_dialog", "mode": mode})
        return 3

    fixture = bridge_request("test_technology_demand_status")
    demanded_ids = [int(value) for value in fixture.get("demanded_technology_ids", [])]
    choices = bridge_request("semantic_choices", kind="interaction")
    payment = "energy" if mode == "energy" else "technologies"
    terms = next(
        (item for item in choices.get("choices", [])
         if item.get("offer_type") == "technology_demand"), None,
    )
    counter = next(
        (item for item in choices.get("choices", [])
         if item.get("response") == "counter" and item.get("payment") == payment), None,
    )
    exposed_ids = [int(item.get("tech_id", -1)) for item in (terms or {}).get("player_gives", [])]
    if not terms or not counter or demanded_ids != exposed_ids \
            or int(terms.get("demanded_count", -1)) != 1 \
            or not terms.get("context_complete"):
        emit("failure", {"stage": "counter_choice", "mode": mode,
                         "fixture": fixture, "choices": choices})
        return 4
    if mode == "energy" and int(counter.get("energy_credits_requested", -1)) != 125:
        emit("failure", {"stage": "energy_quote", "counter": counter})
        return 5
    if mode == "tech" and int(counter.get("technology_id", -1)) < 0:
        emit("failure", {"stage": "reciprocal_technology", "counter": counter})
        return 6

    countered = command(choices, "counter", payment)
    emit("countered", {"mode": mode, "result": countered})
    if not countered.get("ok"):
        return 7

    followup_label = "DEMANDTECHAGAIN1" if mode == "energy" else "DEMANDTECHAGAIN2"
    if not wait_for_label(min(deadline, time.monotonic() + 10),
                          followup_label, opening=False):
        emit("failure", {
            "stage": "followup_dialog", "mode": mode,
            "fixture": bridge_request("test_technology_demand_status"),
            "snapshot": bridge_request("semantic_snapshot"),
        })
        return 8
    followup = bridge_request("semantic_choices", kind="interaction")
    followup_terms = next(
        (item for item in followup.get("choices", [])
         if item.get("offer_type") == "technology_demand_followup"), None,
    )
    accept = next(
        (item for item in followup.get("choices", []) if item.get("response") == "accept"),
        None,
    )
    reject = next(
        (item for item in followup.get("choices", []) if item.get("response") == "reject"),
        None,
    )
    if not followup_terms or not accept or not reject \
            or followup_terms.get("rejected_counter_type") != (
                "energy" if mode == "energy" else "technology") \
            or [int(item.get("tech_id", -1))
                for item in followup_terms.get("player_gives", [])] != demanded_ids:
        emit("failure", {"stage": "followup_choices", "mode": mode,
                         "choices": followup})
        return 9

    accepted = command(followup, "accept")
    emit("accepted", {"mode": mode, "result": accepted})
    if not accepted.get("ok"):
        return 10
    completion: dict[str, Any] = {}
    while time.monotonic() < deadline:
        completion = bridge_request("test_technology_demand_status")
        if int(completion.get("stage", -1)) == 2:
            break
        time.sleep(0.05)
    if int(completion.get("counterpart_demanded_acquired", -1)) != 1 \
            or completion.get("counterpart_distractor_acquired") is not False:
        emit("failure", {"stage": "followup_native_effect", "mode": mode,
                         "completion": completion})
        return 11

    stale = command(followup, "reject")
    if stale.get("ok") or stale.get("error", {}).get("code") not in {
        "stale_state", "wrong_choice_phase", "popup_transition_pending",
    }:
        emit("failure", {"stage": "stale_followup", "result": stale})
        return 12
    emit("pass", {
        "counter_type": mode,
        "initial_dialog": "DEMANDTECH9A",
        "followup_dialog": followup_label,
        "exact_single_technology_demand": demanded_ids,
        "native_concession_after_rejected_counter": True,
        "stale_replay_rejected": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
