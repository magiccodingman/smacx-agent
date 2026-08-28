#!/usr/bin/env python3
"""Contained exact-bundle regression for native technology demands."""

from __future__ import annotations

import json
import time
from typing import Any

from smacx_controller import BridgeUnavailable, bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def guarded(source: dict[str, Any], response: str) -> dict[str, Any]:
    return bridge_request(
        "semantic_command", timeout=10, command="respond_to_diplomatic_offer",
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], response=response,
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

    deadline = time.monotonic() + 100
    initialized = False
    demand_snapshot: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        label = snapshot.get("interaction", {}).get("popup_label")
        if label == "DEMANDTECH15":
            demand_snapshot = snapshot
            break
        if snapshot.get("interaction", {}).get("kind") == "turn":
            initialized = True  # semantic_snapshot initializes and posts the fixture.
        elif not initialized:
            handled, result = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "opening", "result": result,
                                 "snapshot": snapshot})
                return 3
        time.sleep(0.05)
    if not demand_snapshot:
        emit("failure", {"stage": "demand_popup_not_reached"})
        return 4

    fixture = bridge_request("test_technology_demand_status")
    demanded_ids = [int(value) for value in fixture.get("demanded_technology_ids", [])]
    distractor = int(fixture.get("distractor_technology_id", -1))
    choices = bridge_request("semantic_choices", kind="interaction")
    terms = next(
        (item for item in choices.get("choices", [])
         if item.get("offer_type") == "technology_demand"), None,
    )
    accept = next(
        (item for item in choices.get("choices", [])
         if item.get("command") == "respond_to_diplomatic_offer"
         and item.get("response") == "accept"), None,
    )
    reject = next(
        (item for item in choices.get("choices", [])
         if item.get("command") == "respond_to_diplomatic_offer"
         and item.get("response") == "reject"), None,
    )
    exposed_ids = [int(item.get("tech_id", -1)) for item in (terms or {}).get("player_gives", [])]
    if not terms or not accept or not reject or not terms.get("context_complete") \
            or int(terms.get("demanded_count", -1)) != 4 \
            or exposed_ids != demanded_ids or distractor in exposed_ids:
        emit("failure", {
            "stage": "exact_bundle", "fixture": fixture, "terms": terms,
            "accept": accept, "reject": reject,
        })
        return 5

    owned_before = {int(item["id"]) for item in bridge_request("list_technologies").get("items", [])}
    accepted = guarded(choices, "accept")
    emit("accepted", {"result": accepted, "demanded_ids": demanded_ids})
    if not accepted.get("ok"):
        return 6

    completion: dict[str, Any] = {}
    while time.monotonic() < deadline:
        completion = bridge_request("test_technology_demand_status")
        if int(completion.get("stage", -1)) == 2:
            break
        time.sleep(0.05)
    owned_after = {int(item["id"]) for item in bridge_request("list_technologies").get("items", [])}
    if int(completion.get("counterpart_demanded_acquired", -1)) != 4 \
            or completion.get("counterpart_distractor_acquired") is not False \
            or not set(demanded_ids).issubset(owned_before & owned_after):
        emit("failure", {
            "stage": "native_effect", "completion": completion,
            "owned_before": sorted(owned_before), "owned_after": sorted(owned_after),
        })
        return 7

    stale = guarded(choices, "reject")
    if stale.get("ok") or stale.get("error", {}).get("code") not in {
        "stale_state", "wrong_choice_phase", "popup_transition_pending",
    }:
        emit("failure", {"stage": "stale_replay", "result": stale})
        return 8

    emit("pass", {
        "native_dialog": "DEMANDTECH15",
        "exact_demanded_bundle": demanded_ids,
        "distractor_suppressed": distractor,
        "context_complete": True,
        "native_counterpart_acquisitions": 4,
        "player_retained_technologies": True,
        "stale_replay_rejected": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
