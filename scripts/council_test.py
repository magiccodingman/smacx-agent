#!/usr/bin/env python3
"""Contained regression for native, semantic Planetary Council control."""

from __future__ import annotations

import json
import os
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
        timeout=12,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def main() -> int:
    desired_proposal_id = 3 if os.environ.get("SMACX_AGENT_TEST_COUNCIL_POLICY") == "1" else 0
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
    convened = False
    convened_at = 0.0
    proposal_chosen = False
    call_acknowledged = False
    ballot_cast = False
    proposal_id = -1
    seen: set[tuple[str, str]] = set()

    while time.monotonic() < deadline:
        try:
            envelope = bridge_request("semantic_snapshot", timeout=5)
        except BridgeUnavailable:
            time.sleep(0.15)
            continue
        snapshot = envelope.get("snapshot", {})
        if not snapshot:
            emit("failure", envelope)
            return 3
        interaction = snapshot["interaction"]
        kind = interaction["kind"]
        label = interaction.get("popup_label", "")
        state_key = (kind, label)
        if state_key not in seen:
            emit("state", {"kind": kind, "label": label, "turn": snapshot["turn"]})
            seen.add(state_key)

        if kind in {"waiting_for_engine", "waiting_for_turn"}:
            time.sleep(0.1)
            continue

        if kind == "turn" and not convened:
            choices = bridge_request("semantic_choices", kind="council")
            emit("council_choices", choices)
            option = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "convene_council"),
                None,
            )
            if option is None:
                emit("failure", {"reason": "council_not_available", "choices": choices})
                return 4
            result = command(choices, command="convene_council")
            emit("convene", result)
            if not result.get("ok"):
                return 5
            convened = True
            convened_at = time.monotonic()
            continue

        if kind == "popup" and label == "COUNCILISSUES":
            if proposal_chosen:
                time.sleep(0.08)
                continue
            choices = bridge_request("semantic_choices", kind="interaction")
            emit("proposal_choices", choices)
            option = next(
                (item for item in choices.get("choices", [])
                 if item.get("proposal_id") == desired_proposal_id),
                None,
            )
            if option is None:
                emit("failure", {"reason": "desired_proposal_missing", "choices": choices})
                return 6
            proposal_id = int(option["proposal_id"])
            if option.get("ballot", {}).get("type") == "candidate":
                candidates = option.get("ballot", {}).get("candidates", [])
                candidate = next(
                    (item for item in candidates if item.get("faction_id") == snapshot["faction"]["id"]),
                    candidates[0] if candidates else None,
                )
                if candidate is None:
                    emit("failure", {"reason": "proposal_ballot_missing", "option": option})
                    return 7
                result = command(
                    choices,
                    command="choose_council_proposal",
                    proposal_id=proposal_id,
                    candidate_faction_id=int(candidate["faction_id"]),
                )
            else:
                result = command(
                    choices,
                    command="choose_council_proposal",
                    proposal_id=proposal_id,
                    response="yea",
                )
            emit("proposal", result)
            if not result.get("ok"):
                return 7
            proposal_chosen = True
            ballot_cast = bool(result.get("ballot_scheduled"))
            continue

        if kind == "popup" and label == "CALLSCOUNCIL":
            choices = bridge_request("semantic_choices", kind="interaction")
            result = command(choices, command="acknowledge_popup")
            emit("call_notice", result)
            if not result.get("ok"):
                return 8
            call_acknowledged = True
            continue

        if kind == "popup" and label == "COUNCILOPEN":
            choices = bridge_request("semantic_choices", kind="interaction")
            result = command(choices, command="acknowledge_popup")
            emit("availability_notice", result)
            if not result.get("ok"):
                return 8
            if not proposal_chosen:
                convened = False
                convened_at = 0.0
            continue

        if kind == "popup" and label == "COUNCILVOTEGOV":
            choices = bridge_request("semantic_choices", kind="interaction")
            emit("candidate_choices", choices)
            option = next(
                (item for item in choices.get("choices", [])
                 if item.get("candidate_faction_id") == snapshot["faction"]["id"]),
                None,
            )
            if option is None:
                option = next(
                    (item for item in choices.get("choices", [])
                     if "candidate_faction_id" in item),
                    None,
                )
            if option is None:
                emit("failure", {"reason": "candidate_missing", "choices": choices})
                return 9
            result = command(
                choices,
                command="cast_council_vote",
                candidate_faction_id=int(option["candidate_faction_id"]),
            )
            emit("ballot", result)
            if not result.get("ok"):
                return 10
            ballot_cast = True
            continue

        if kind == "popup" and label.startswith("COUNCILHOT"):
            choices = bridge_request("semantic_choices", kind="interaction")
            result = command(choices, command="acknowledge_popup")
            emit("result_notice", result)
            if not result.get("ok"):
                return 11
            continue

        if kind == "turn" and convened and proposal_chosen and ballot_cast:
            deferred = snapshot.get("last_deferred_action") or {}
            council_result = snapshot.get("last_council_result") or {}
            if deferred.get("status") != "completed" \
            or council_result.get("proposal_id") != proposal_id \
            or council_result.get("result") not in {"passed", "failed", "vetoed"}:
                emit("failure", {
                    "reason": "council_postcondition_failed",
                    "deferred": deferred,
                    "council_result": council_result,
                })
                return 14
            emit("pass", {
                "proposal_id": proposal_id,
                "call_notice_acknowledged": call_acknowledged,
                "ballot_cast": True,
                "native_action_completed": True,
                "result": council_result,
                "coordinates_or_pixels_used": False,
            })
            return 0

        if kind == "turn" and convened and not proposal_chosen \
        and time.monotonic() - convened_at < 8:
            time.sleep(0.08)
            continue

        if kind == "popup":
            choices = bridge_request("semantic_choices", kind="interaction")
            emit("unexpected_popup", choices)
        handled, outcome = handle_interaction(snapshot)
        emit("other_interaction", outcome)
        if not handled:
            return 12

    emit("failure", {
        "reason": "deadline",
        "convened": convened,
        "proposal_chosen": proposal_chosen,
        "call_acknowledged": call_acknowledged,
        "ballot_cast": ballot_cast,
    })
    return 13


if __name__ == "__main__":
    sys.exit(main())
