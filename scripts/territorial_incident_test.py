#!/usr/bin/env python3
"""Contained regression for semantic treaty-break attack confirmation."""

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


def wait_for_label(label: str, deadline: float) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("popup_label") == label:
            return snapshot
        time.sleep(0.05)
    return None


def wait_action(action_id: int, deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        action = snapshot.get("last_deferred_action") or {}
        if int(action.get("action_id", -1)) == action_id \
                and action.get("status") in {"completed", "rejected"}:
            return action
        kind = snapshot.get("interaction", {}).get("kind")
        if kind == "popup":
            handled, _ = handle_interaction(snapshot)
            if not handled:
                return {"status": "unsupported_interaction", "snapshot": snapshot}
        time.sleep(0.05)
    return {"status": "timeout"}


def attack_choice(attacker_id: int, target_tile_id: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    choices = bridge_request(
        "semantic_choices", kind="unit_actions", unit_id=attacker_id,
    )
    move = next(
        (item for item in choices.get("choices", [])
         if item.get("command") == "move_unit"
         and int(item.get("target_tile_id", -1)) == target_tile_id),
        None,
    )
    return choices, move


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

    units_response = bridge_request("list_units", scope="visible", limit=200)
    units = units_response.get("items", [])
    enemy = next((item for item in units if item.get("owner") != 4), None)
    if not enemy:
        emit("failure", {"stage": "defender_fixture", "response": units_response,
                         "units": units})
        return 5
    target_tile_id = int(enemy["tile_id"])
    attacker = None
    move = None
    for candidate in (item for item in units if item.get("owner") == 4):
        candidate_choices, candidate_move = attack_choice(int(candidate["id"]), target_tile_id)
        if candidate_move:
            attacker = candidate
            move = candidate_move
            break
    if not attacker:
        emit("failure", {"stage": "attacker_fixture", "response": units_response,
                         "units": units})
        return 4
    attacker_id = int(attacker["id"])
    origin_tile_id = int(attacker["tile_id"])

    choices, refreshed_move = attack_choice(attacker_id, target_tile_id)
    move = refreshed_move or move
    if not move:
        emit("failure", {"stage": "attack_choice", "choices": choices})
        return 6
    queued = command(choices, command="move_unit", unit_id=attacker_id,
                     target_tile_id=target_tile_id)
    if not queued.get("ok"):
        emit("failure", {"stage": "queue_cancel_path", "result": queued})
        return 7
    if not wait_for_label("BREAKINGTREATY", deadline):
        emit("failure", {"stage": "first_treaty_warning"})
        return 8
    incident = bridge_request("semantic_choices", kind="interaction")
    context = next(
        (item for item in incident.get("choices", []) if item.get("incident_type")), None,
    )
    declare = next(
        (item for item in incident.get("choices", [])
         if item.get("response") == "declare_vendetta"), None,
    )
    if not context or context.get("counterpart_faction_id") != enemy.get("owner") \
            or not declare or declare.get("confirm_hostility") != 1:
        emit("failure", {"stage": "structured_warning", "choices": incident})
        return 9
    refused = command(incident, command="respond_to_territorial_incident",
                      response="declare_vendetta")
    if refused.get("error", {}).get("code") != "hostility_confirmation_required":
        emit("failure", {"stage": "confirmation_gate", "result": refused})
        return 10
    cancelled = command(incident, command="respond_to_territorial_incident", response="cancel")
    action = wait_action(int(queued.get("action_id", -1)), deadline)
    emit("cancelled", {"result": cancelled, "action": action})
    if not cancelled.get("ok") or action.get("status") != "rejected":
        return 11
    current_units = bridge_request("list_units", scope="own", limit=200).get("items", [])
    attacker = next((item for item in current_units if int(item.get("id", -1)) == attacker_id), None)
    factions = bridge_request("list_factions").get("items", [])
    relation = next((item for item in factions if item.get("id") == enemy.get("owner")), None)
    if not attacker or int(attacker["tile_id"]) != origin_tile_id \
            or not relation or not relation.get("relations", {}).get("treaty") \
            or relation.get("relations", {}).get("vendetta"):
        emit("failure", {"stage": "cancel_preserved_state", "attacker": attacker,
                         "relation": relation})
        return 12

    attacker_id = int(attacker["id"])
    choices, move = attack_choice(attacker_id, target_tile_id)
    if not move:
        emit("failure", {"stage": "second_attack_choice", "choices": choices})
        return 13
    queued = command(choices, command="move_unit", unit_id=attacker_id,
                     target_tile_id=target_tile_id)
    if not queued.get("ok") or not wait_for_label("BREAKINGTREATY", deadline):
        emit("failure", {"stage": "queue_declare_path", "result": queued})
        return 14
    incident = bridge_request("semantic_choices", kind="interaction")
    declared = command(incident, command="respond_to_territorial_incident",
                       response="declare_vendetta", confirm_hostility=1)
    action = wait_action(int(queued.get("action_id", -1)), deadline)
    factions = bridge_request("list_factions").get("items", [])
    relation = next((item for item in factions if item.get("id") == enemy.get("owner")), None)
    emit("declared", {"result": declared, "action": action, "relation": relation})
    if not declared.get("ok") or action.get("status") != "completed" \
            or not relation or not relation.get("relations", {}).get("vendetta"):
        return 15

    emit("pass", {
        "cancel_preserved_treaty": True,
        "explicit_hostility_confirmation": True,
        "native_vendetta_and_attack": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
