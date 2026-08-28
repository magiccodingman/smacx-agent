#!/usr/bin/env python3
"""Contained regression for the native credits-to-menu victory pipeline."""

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


def command(source: dict[str, Any], command_name: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command", timeout=10, command=command_name,
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], **arguments,
    )


def observe(deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        try:
            value = bridge_request("semantic_snapshot", timeout=5)
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        snapshot = value.get("snapshot", {})
        if snapshot:
            return snapshot
        time.sleep(0.05)
    return {}


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 170
    phases: list[str] = []
    stale_source: dict[str, Any] | None = None
    initial_unit_id = -1
    mutation_guard_verified = False

    while time.monotonic() < deadline:
        current = observe(deadline)
        if not current:
            continue
        interaction = current.get("interaction", {})
        kind = interaction.get("kind")
        label = interaction.get("popup_label", "")

        if kind == "turn":
            # The first actionable observation arms the contained full-victory
            # fixture. A later turn would mean the production stack returned
            # without honoring the requested finish path.
            refs = current.get("ready_unit_refs", [])
            if refs and initial_unit_id < 0:
                initial_unit_id = int(refs[0].get("id", -1))
            time.sleep(0.05)
            continue

        if kind == "endgame_presentation":
            choices = bridge_request("semantic_choices", kind="interaction")
            advance = next((item for item in choices.get("choices", [])
                            if item.get("command") == "advance_endgame_presentation"), None)
            if not advance or not advance.get("phase"):
                emit("failure", {"stage": "presentation_choice", "choices": choices})
                return 3
            phase = str(advance["phase"])
            phases.append(phase)
            if not mutation_guard_verified and initial_unit_id >= 0:
                blocked = command(
                    choices, "disband_unit", unit_id=initial_unit_id,
                    confirm_disband=1,
                )
                if blocked.get("error", {}).get("code") != "not_actionable":
                    emit("failure", {"stage": "presentation_mutation_guard",
                                     "result": blocked})
                    return 4
                mutation_guard_verified = True
            stale_source = choices
            result = command(choices, "advance_endgame_presentation", phase=phase)
            if not result.get("ok"):
                emit("failure", {"stage": "presentation_advance", "phase": phase,
                                 "result": result})
                return 5
            continue

        if label == "GAMEOVERMAN":
            choices = bridge_request("semantic_choices", kind="interaction")
            finish = next((item for item in choices.get("choices", [])
                           if item.get("response") == "finish"), None)
            if not finish:
                emit("failure", {"stage": "finish_choice", "choices": choices})
                return 6
            finished = command(choices, "respond_to_game_over", response="finish")
            if not finished.get("ok"):
                emit("failure", {"stage": "finish", "result": finished})
                return 7
            break

        handled, result = handle_interaction(current)
        if not handled:
            emit("failure", {"stage": "unhandled_interaction", "result": result,
                             "snapshot": current})
            return 8
    else:
        emit("failure", {"stage": "pipeline_timeout", "phases": phases})
        return 9

    expected = ["credits", "score_report", "quayle_rating", "hall_of_fame", "replay"]
    if os.environ.get("SMACX_AGENT_TEST_FULL_ENDGAME") == "narrative":
        expected.insert(0, "victory_interlude")
    settle_deadline = time.monotonic() + 15
    final: dict[str, Any] = {}
    while time.monotonic() < settle_deadline:
        try:
            final = bridge_request("test_full_endgame_status", timeout=3).get(
                "test_full_endgame", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if final.get("final_score_done") and int(final.get("control_turn_a", 0)) == 1:
            break
        time.sleep(0.05)
    if phases != expected or not mutation_guard_verified \
            or not final.get("final_score_done") \
            or int(final.get("control_turn_a", 0)) != 1 \
            or int(final.get("control_turn_b", 0)) != 1:
        emit("failure", {"stage": "final_state", "phases": phases, "status": final})
        return 10

    stale_rejected = False
    if stale_source:
        try:
            stale = command(
                stale_source, "advance_endgame_presentation",
                phase=next((item.get("phase") for item in stale_source.get("choices", [])
                            if item.get("command") == "advance_endgame_presentation"), ""),
            )
            stale_rejected = stale.get("error", {}).get("code") in {
                "stale_state", "not_in_game", "endgame_presentation_changed",
            }
        except BridgeUnavailable:
            stale_rejected = True
    if not stale_rejected:
        emit("failure", {"stage": "stale_replay"})
        return 11

    emit("pass", {
        "native_pipeline_phases": phases,
        "final_score_done": True,
        "native_exit_path_requested": True,
        "presentation_mutation_guard_verified": True,
        "stale_replay_rejected": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
