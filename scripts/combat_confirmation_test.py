#!/usr/bin/env python3
"""Contained regression for semantic native combat-odds confirmation."""

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
            emit("failure", {"stage": "opening_interaction", "result": result,
                             "snapshot": snapshot})
            return None
        time.sleep(0.05)
    return None


def wait_for_badidea(deadline: float) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        label = snapshot.get("interaction", {}).get("popup_label")
        if label == "BADIDEA":
            return snapshot
        if label in {"BREAKINGTREATY", "BREAKINGTRUCE", "BEGINVENDETTA"}:
            choices = bridge_request("semantic_choices", kind="interaction")
            result = command(
                choices, command="respond_to_territorial_incident",
                response="declare_vendetta", confirm_hostility=1,
            )
            if not result.get("ok"):
                emit("failure", {"stage": "hostility_transition", "result": result})
                return None
        time.sleep(0.04)
    return None


def wait_action(action_id: int, deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        result = bridge_request("action_status", action_id=action_id, timeout=5)
        action = result.get("action", {})
        if action.get("status") in {"completed", "rejected"}:
            return action
        time.sleep(0.04)
    return {"status": "timeout"}


def attack_choices(attacker_id: int, enemy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=attacker_id)
    attack = next(
        (item for item in choices.get("choices", [])
         if item.get("command") == "move_unit"
         and item.get("target_tile_id") == enemy.get("tile_id")),
        None,
    )
    return choices, attack


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 120
    if not wait_for_turn(deadline):
        return 3

    units_response = bridge_request("list_units", scope="visible", limit=200)
    units = units_response.get("items", [])
    enemy = next((item for item in units if item.get("owner") != 4), None)
    attacker = None
    for candidate in (item for item in units if item.get("owner") == 4):
        if enemy and attack_choices(int(candidate["id"]), enemy)[1]:
            attacker = candidate
            break
    if not enemy or not attacker:
        emit("failure", {"stage": "fixture", "response": units_response,
                         "units": units})
        return 4
    attacker_id = int(attacker["id"])

    choices, attack = attack_choices(attacker_id, enemy)
    queued = command(
        choices, command="move_unit", unit_id=attacker_id,
        target_tile_id=int(enemy["tile_id"]),
    ) if attack else {"ok": False}
    if not queued.get("ok") or not wait_for_badidea(deadline):
        emit("failure", {"stage": "first_warning", "queued": queued})
        return 5
    warning = bridge_request("semantic_choices", kind="interaction")
    context = next(
        (item for item in warning.get("choices", [])
         if item.get("id") == "combat_odds:context"), None,
    )
    proceed = next(
        (item for item in warning.get("choices", [])
         if item.get("response") == "proceed"), None,
    )
    if not context or context.get("risk_assessment") != "strongly_against" \
            or context.get("attacker_unit_id") != attacker_id \
            or not proceed or proceed.get("confirm_attack") != 1:
        emit("failure", {"stage": "structured_warning", "choices": warning})
        return 6
    refused = command(
        warning, command="respond_to_combat_confirmation", response="proceed",
    )
    if refused.get("error", {}).get("code") != "combat_confirmation_required":
        emit("failure", {"stage": "confirmation_gate", "result": refused})
        return 7
    cancelled = command(
        warning, command="respond_to_combat_confirmation", response="cancel",
    )
    cancelled_action = wait_action(int(queued["action_id"]), deadline)
    if not cancelled.get("ok") or cancelled_action.get("status") != "rejected":
        emit("failure", {"stage": "cancel", "result": cancelled,
                         "action": cancelled_action})
        return 8

    choices, attack = attack_choices(attacker_id, enemy)
    queued = command(
        choices, command="move_unit", unit_id=attacker_id,
        target_tile_id=int(enemy["tile_id"]),
    ) if attack else {"ok": False}
    if not queued.get("ok") or not wait_for_badidea(deadline):
        emit("failure", {"stage": "second_warning", "queued": queued,
                         "choices": choices})
        return 9
    warning = bridge_request("semantic_choices", kind="interaction")
    accepted = command(
        warning, command="respond_to_combat_confirmation", response="proceed",
        confirm_attack=1,
    )
    resolved_action = wait_action(int(queued["action_id"]), deadline)
    if not accepted.get("ok") or resolved_action.get("status") != "completed":
        emit("failure", {"stage": "proceed", "result": accepted,
                         "action": resolved_action})
        return 10

    emit("pass", {
        "native_popup_label": "BADIDEA",
        "cancel_path_verified": True,
        "explicit_attack_confirmation_verified": True,
        "native_combat_resolved": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
