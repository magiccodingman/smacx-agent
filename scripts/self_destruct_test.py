#!/usr/bin/env python3
"""Contained native-effect regression for semantic reactor self-destruct."""

from __future__ import annotations

import json
import time
from typing import Any

from smacx_controller import BridgeUnavailable, bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")),
          flush=True)


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
            emit("failure", {"stage": "opening", "snapshot": snapshot,
                             "result": result})
            return None
        time.sleep(0.04)
    return None


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=0, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 120
    if not wait_for_turn(deadline):
        return 3
    units = bridge_request("list_units", scope="visible", limit=200).get("items", [])
    attacker = next(
        (item for item in units if item.get("name") == "Harness Overload Unit"), None,
    )
    if not attacker:
        emit("failure", {"stage": "fixture", "units": units})
        return 4
    attacker_id = int(attacker["id"])
    choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=attacker_id)
    overload = next(
        (item for item in choices.get("choices", [])
         if item.get("command") == "self_destruct_unit"), None,
    )
    context = next(
        (item for item in choices.get("choices", [])
         if item.get("id") == "self_destruct:context"), None,
    )
    visible_native = next(
        (item for item in (context or {}).get("known_affected_units", [])
         if int(item.get("owner_faction_id", -1)) == 0), None,
    )
    if not overload or overload.get("confirm_self_destruct") != 1 \
            or not context or not visible_native \
            or not visible_native.get("projected_lethal"):
        emit("failure", {"stage": "structured_choice", "choices": choices})
        return 5
    refused = command(
        choices, command="self_destruct_unit", unit_id=attacker_id,
    )
    still_there = bridge_request("test_self_destruct_status")
    if refused.get("error", {}).get("code") != "self_destruct_confirmation_required" \
            or not still_there.get("attacker_alive") \
            or not still_there.get("visible_native_target_alive"):
        emit("failure", {"stage": "confirmation_gate", "result": refused,
                         "status": still_there})
        return 6
    executed = command(
        choices, command="self_destruct_unit", unit_id=attacker_id,
        confirm_self_destruct=1,
    )
    status = bridge_request("test_self_destruct_status")
    if not executed.get("ok") or status.get("attacker_alive") \
            or status.get("visible_native_target_alive"):
        emit("failure", {"stage": "native_effect", "result": executed,
                         "status": status})
        return 7
    stale = command(
        choices, command="self_destruct_unit", unit_id=attacker_id,
        confirm_self_destruct=1,
    )
    if stale.get("ok") or stale.get("error", {}).get("code") != "stale_state":
        emit("failure", {"stage": "stale_replay", "result": stale})
        return 8
    snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
    if snapshot.get("interaction", {}).get("kind") not in {"turn", "waiting_for_turn"}:
        emit("failure", {"stage": "post_action_state", "snapshot": snapshot})
        return 9
    emit("pass", {
        "command": "self_destruct_unit",
        "explicit_confirmation_verified": True,
        "native_source_destroyed": True,
        "visible_adjacent_target_destroyed": True,
        "blast_damage": status.get("blast_damage"),
        "stale_replay_rejected": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
