#!/usr/bin/env python3
"""Contained native-timing regression for an AI-called Council session."""

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
        "semantic_command", timeout=12,
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], **arguments,
    )


def observe(deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if snapshot:
            return snapshot
    return {}


def wait_opening_turn(deadline: float) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        snapshot = observe(deadline)
        interaction = snapshot.get("interaction", {})
        if interaction.get("kind") == "turn" \
                or (interaction.get("kind") == "popup"
                    and interaction.get("popup_label") == "CALLSCOUNCIL"):
            return snapshot
        handled, result = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "opening", "result": result, "snapshot": snapshot})
            return None
        time.sleep(0.05)
    return None


def end_current_turn(deadline: float) -> bool:
    base_established = False
    while time.monotonic() < deadline:
        snapshot = observe(deadline)
        kind = snapshot.get("interaction", {}).get("kind")
        if kind != "turn":
            if kind == "popup":
                label = snapshot.get("interaction", {}).get("popup_label", "")
                choices = bridge_request("semantic_choices", kind="interaction")
                commands = [item.get("command") for item in choices.get("choices", [])]
                emit("pre_end_popup", {"label": label, "commands": commands})
                if label == "CALLSCOUNCIL" or "cast_council_vote" in commands:
                    return True
            handled, result = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "pre_end_interaction", "result": result,
                                 "snapshot": snapshot})
                return False
            time.sleep(0.05)
            continue
        units = bridge_request("list_units", scope="own", limit=300).get("items", [])
        ready = next((unit for unit in units if unit.get("ready")), None)
        if ready:
            choices = bridge_request(
                "semantic_choices", kind="unit_actions", unit_id=int(ready["id"]),
            )
            if ready.get("roles", {}).get("colony") and not base_established:
                found = next(
                    (item for item in choices.get("choices", [])
                     if item.get("command") == "found_base"),
                    None,
                )
                if found:
                    founded = command(
                        choices, command="found_base", unit_id=int(ready["id"]),
                        name="Council Test Nexus",
                    )
                    if not founded.get("ok"):
                        emit("failure", {"stage": "found_base", "result": founded,
                                         "unit": ready})
                        return False
                    base_established = True
                    continue
            skipped = command(choices, command="skip_unit", unit_id=int(ready["id"]))
            if not skipped.get("ok"):
                emit("failure", {"stage": "skip", "result": skipped, "unit": ready})
                return False
            continue
        choices = bridge_request("semantic_choices", kind="game_management")
        bases = bridge_request("list_bases", scope="own", limit=50).get("items", [])
        if not bases:
            emit("failure", {"stage": "pre_end_no_base"})
            return False
        ended = command(choices, command="end_turn")
        if not ended.get("ok"):
            emit("failure", {"stage": "end_turn", "result": ended, "choices": choices})
            return False
        return True
    return False


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 180
    opening = wait_opening_turn(deadline)
    if not opening:
        return 3
    if opening.get("interaction", {}).get("popup_label") != "CALLSCOUNCIL" \
            and not end_current_turn(deadline):
        return 3

    call_snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        call_snapshot = observe(deadline)
        interaction = call_snapshot.get("interaction", {})
        if interaction.get("kind") == "popup" \
                and interaction.get("popup_label") == "CALLSCOUNCIL":
            break
        if interaction.get("kind") == "popup":
            unexpected = bridge_request("semantic_choices", kind="interaction")
            if any(item.get("command") == "cast_council_vote"
                   for item in unexpected.get("choices", [])):
                emit("failure", {"stage": "ballot_before_call_notice",
                                 "snapshot": call_snapshot, "choices": unexpected})
                return 4
        if interaction.get("kind") not in {"waiting_for_engine", "waiting_for_turn"}:
            handled, result = handle_interaction(call_snapshot)
            if not handled:
                emit("failure", {"stage": "await_call", "result": result,
                                 "snapshot": call_snapshot})
                return 4
        time.sleep(0.05)
    else:
        emit("failure", {"stage": "call_notice", "snapshot": call_snapshot})
        return 5

    notice = bridge_request("semantic_choices", kind="interaction")
    acknowledged = command(notice, command="acknowledge_popup")
    duplicate = command(notice, command="acknowledge_popup")
    duplicate_code = duplicate.get("error", {}).get("code")
    if not acknowledged.get("ok") or duplicate.get("ok") \
            or duplicate_code not in {
                "stale_state", "popup_transition_pending", "popup_unavailable",
            }:
        emit("failure", {"stage": "notice_guard", "acknowledged": acknowledged,
                         "duplicate": duplicate})
        return 6

    ballot: dict[str, Any] = {}
    ballot_snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        ballot_snapshot = observe(deadline)
        if ballot_snapshot.get("protocol", {}).get("required_action") == "resolve_interaction":
            ballot = bridge_request("semantic_choices", kind="interaction")
            if any(item.get("command") == "cast_council_vote"
                   for item in ballot.get("choices", [])):
                break
        time.sleep(0.05)
    else:
        emit("failure", {"stage": "ballot", "snapshot": ballot_snapshot,
                         "choices": ballot})
        return 7

    actions = [item for item in ballot.get("choices", [])
               if item.get("command") == "cast_council_vote"]
    selected = next(
        (item for item in actions
         if item.get("candidate_faction_id") == ballot_snapshot["faction"]["id"]),
        None,
    ) or next((item for item in actions if item.get("response") == "yea"), None) \
        or (actions[0] if actions else None)
    if not selected:
        emit("failure", {"stage": "ballot_choice", "choices": ballot})
        return 8
    parameters = {key: selected[key] for key in ("candidate_faction_id", "response")
                  if key in selected}
    cast = command(ballot, command="cast_council_vote", **parameters)
    if not cast.get("ok") or not cast.get("ballot_scheduled"):
        emit("failure", {"stage": "cast", "result": cast, "choice": selected})
        return 9

    final_snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        final_snapshot = observe(deadline)
        result = final_snapshot.get("last_council_result") or {}
        if result.get("result") in {"passed", "failed", "vetoed"}:
            emit("pass", {
                "call_notice_acknowledged_once": True,
                "duplicate_submission_rejected": duplicate_code,
                "separate_council_window_ballot": True,
                "result": result,
                "pixels_or_ui_input_used": False,
            })
            return 0
        time.sleep(0.05)

    emit("failure", {"stage": "result", "snapshot": final_snapshot})
    return 10


if __name__ == "__main__":
    sys.exit(main())
