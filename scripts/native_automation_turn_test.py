#!/usr/bin/env python3
"""One-turn lifecycle regression for persistent native player automation."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], name: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        command=name,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def main() -> int:
    automation_kind = os.environ.get("SMACX_TEST_NATIVE_AUTOMATION_KIND", "former")
    air_defense = automation_kind == "air_defense"
    bombing_run = automation_kind == "bombing_run"
    if automation_kind not in {"former", "air_defense", "bombing_run"}:
        raise ValueError(
            "SMACX_TEST_NATIVE_AUTOMATION_KIND must be former, air_defense, or bombing_run"
        )
    expected_order = (
        "bombing_run" if bombing_run
        else "auto_air_defense" if air_defense
        else "auto_former_full"
    )
    expected_auto_type = 10 if bombing_run else 12 if air_defense else 0
    assignment_command = (
        "set_bombing_run" if bombing_run
        else "automate_air_defense" if air_defense
        else "automate_former"
    )
    started = new_game(
        wait_seconds=60,
        difficulty=0,
        world_size=0,
        faction_id=1,
        blind_research=True,
        initial_research_priority=1,
        narrative_ui=False,
        tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 150
    automated_id = -1
    start_turn = -1
    original = {}
    assignment_choices: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        if snapshot.get("interaction", {}).get("kind") != "turn":
            handled = handle_interaction(snapshot)
            if handled:
                emit("interaction", handled)
            time.sleep(0.1)
            continue

        if automated_id < 0:
            units = bridge_request("list_units", scope="own", limit=300).get("items", [])
            candidate = next(
                (unit for unit in units if unit.get("ready") and (
                    unit.get("name") == "Harness Bomber" if bombing_run
                    else unit.get("name") == "Harness Interceptor" if air_defense
                    else unit.get("roles", {}).get("former"))),
                None,
            )
            if candidate is None:
                time.sleep(0.1)
                continue
            automated_id = int(candidate["id"])
            original = {"tile_id": int(candidate["tile_id"]),
                        "order": int(candidate["order"])}
            start_turn = int(snapshot["turn"])
            assignment_choices = bridge_request(
                "semantic_choices", kind="unit_actions", unit_id=automated_id,
            )
            arguments: dict[str, object] = {"unit_id": automated_id}
            if bombing_run:
                bombing_choice = next(
                    (item for item in assignment_choices.get("choices", [])
                     if item.get("command") == "set_bombing_run"),
                    None,
                )
                if bombing_choice is None:
                    emit("failure", {"stage": "bombing_choice", "choices": assignment_choices})
                    return 3
                arguments["target_tile_id"] = int(bombing_choice["target_tile_id"])
            elif not air_defense:
                arguments["automation_mode"] = "full"
            assigned = command(assignment_choices, assignment_command, **arguments)
            emit("assigned", assigned)
            if not assigned.get("ok"):
                return 3
            continue

        if int(snapshot["turn"]) > start_turn:
            units = bridge_request("list_units", scope="own", limit=300).get("items", [])
            candidates = [
                unit for unit in units
                if unit.get("name") == "Harness Bomber"
            ] if bombing_run else [
                unit for unit in units
                if unit.get("name") == "Harness Interceptor"
            ] if air_defense else [
                unit for unit in units if unit.get("roles", {}).get("former")
            ]
            if (air_defense or bombing_run) and not candidates:
                stale = command(
                    assignment_choices, assignment_command, unit_id=automated_id,
                )
                emit("native_aircraft_resolution", {"units": units, "stale_guard": stale})
                if stale.get("error", {}).get("code") != "stale_state":
                    return 4
                emit("pass", {
                    "automation_kind": automation_kind,
                    "native_turn_processed": int(snapshot["turn"]),
                    "aircraft_survived_native_policy": False,
                    "aircraft_no_longer_owned_or_alive": True,
                    "end_turn_confirmation_resolved_semantically": True,
                    "stale_replay_rejected": True,
                    "pixels_or_ui_input_used": False,
                })
                return 0
            if len(candidates) != 1:
                emit("failure", {"stage": "automation_identity", "candidates": candidates,
                                 "units": units})
                return 4
            automated = candidates[0]
            automated_id = int(automated["id"])
            choices = bridge_request(
                "semantic_choices", kind="unit_actions", unit_id=automated_id,
            )
            commands = [item.get("command") for item in choices.get("choices", [])]
            emit("next_turn_state", {"unit": automated, "choices": choices, "original": original})
            if automated.get("order_name") != expected_order \
                    or automated.get("order_auto_type") != expected_auto_type \
                    or automated.get("ready") is not False \
                    or choices.get("reason") != expected_order \
                    or commands != ["activate_unit"]:
                return 5
            cancelled = command(choices, "activate_unit", unit_id=automated_id)
            emit("cancelled_next_turn", cancelled)
            if cancelled.get("error", {}).get("code") in {
                "stale_state", "game_timeout", "superseded_request",
            }:
                time.sleep(0.1)
                continue
            if not cancelled.get("ok") \
                    or cancelled.get("old_automation") != expected_order:
                return 6
            stale_arguments: dict[str, object] = {"unit_id": automated_id}
            if bombing_run:
                stale_arguments["target_tile_id"] = int(next(
                    item["target_tile_id"] for item in assignment_choices.get("choices", [])
                    if item.get("command") == "set_bombing_run"
                ))
            elif not air_defense:
                stale_arguments["automation_mode"] = "full"
            stale = command(assignment_choices, assignment_command, **stale_arguments)
            emit("stale_guard", stale)
            if stale.get("error", {}).get("code") != "stale_state":
                return 7
            emit("pass", {
                "automation_persisted_across_turn": True,
                "automation_kind": automation_kind,
                "native_turn_processed": int(snapshot["turn"]),
                "underlying_order_after_processing": automated.get("order"),
                "tile_after_processing": automated.get("tile_id"),
                "decision_gate_suppressed": True,
                "fresh_activation_cancelled": True,
                "pixels_or_ui_input_used": False,
            })
            return 0

        ready_refs = snapshot.get("ready_unit_refs", [])
        if ready_refs:
            ref = ready_refs[0]
            ref_id = int(ref["id"])
            if ref_id == automated_id:
                emit("failure", {"stage": "automated_former_in_ready_refs", "snapshot": snapshot})
                return 8
            choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=ref_id)
            skip = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "skip_unit"),
                None,
            )
            if skip is None:
                emit("failure", {"stage": "resolve_other_unit", "choices": choices})
                return 9
            resolved = command(choices, "skip_unit", unit_id=ref_id)
            if resolved.get("error", {}).get("code") == "stale_state":
                continue
            if not resolved.get("ok"):
                emit("failure", {"stage": "skip_other_unit", "result": resolved})
                return 10
            continue

        management = bridge_request("semantic_choices", kind="game_management")
        end = next(
            (item for item in management.get("choices", [])
             if item.get("command") == "end_turn"),
            None,
        )
        if end is None:
            time.sleep(0.1)
            continue
        ended = command(management, "end_turn")
        emit("ended_turn", ended)
        if ended.get("error", {}).get("code") == "stale_state":
            continue
        if ended.get("error", {}).get("code") == "game_timeout":
            time.sleep(0.2)
            continue
        if not ended.get("ok"):
            return 11
        time.sleep(0.1)
    return 12


if __name__ == "__main__":
    sys.exit(main())
