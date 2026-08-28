#!/usr/bin/env python3
"""Contained native-combat regression for the semantic USENERVE decision."""

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
            emit("failure", {"stage": "opening", "result": result, "snapshot": snapshot})
            return None
        time.sleep(0.04)
    return None


def fixture_units(deadline: float) -> dict[str, Any]:
    """Read the fixture after resolving any first-contact phase it creates.

    Placing a previously unmet faction next to the player is itself a native
    game event.  The first read can therefore finish fixture construction just
    as the engine opens COMM.  Production reads never synthesize units, so this
    bounded retry belongs in the contained regression rather than the bridge.
    """
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = bridge_request("list_units", scope="visible", limit=200)
        if last.get("ok"):
            return last
        snapshot = bridge_request("semantic_snapshot", timeout=8).get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") != "popup":
            return last
        handled, result = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "fixture_phase_transition",
                             "snapshot": snapshot, "result": result})
            return last
        time.sleep(0.04)
    return last


def attack_choice(attacker_id: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=attacker_id)
    attack = next(
        (item for item in choices.get("choices", [])
         if item.get("command") == "move_unit"
         and item.get("may_initiate_combat_or_contact")),
        None,
    )
    if not attack:
        attack = next(
            (item for item in choices.get("choices", [])
             if item.get("command") == "move_unit"), None,
        )
    return choices, attack


def wait_for_nerve_gas(deadline: float) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        interaction = snapshot.get("interaction", {})
        label = interaction.get("popup_label", "")
        if label == "USENERVE":
            return snapshot
        if label in {"BADIDEA", "GOODIDEA", "GOODIDEA2", "HASTY"}:
            choices = bridge_request("semantic_choices", kind="interaction")
            result = command(
                choices, command="respond_to_combat_confirmation",
                response="proceed", confirm_attack=1,
            )
            if not result.get("ok"):
                emit("failure", {"stage": "combat_confirmation", "result": result})
                return None
        elif interaction.get("kind") == "popup":
            handled, result = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "pre_nerve_popup", "result": result,
                                 "snapshot": snapshot})
                return None
        time.sleep(0.04)
    return None


def wait_for_action(action_id: int, deadline: float) -> dict[str, Any]:
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = bridge_request("action_status", action_id=action_id, timeout=5).get("action", {})
        if last.get("status") in {"completed", "rejected"}:
            return last
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") == "popup":
            handled, result = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "post_nerve_popup", "result": result,
                                 "snapshot": snapshot})
                return {"status": "unsupported_popup"}
        time.sleep(0.04)
    return {**last, "status": "timeout"}


def main() -> int:
    mode = os.environ.get("SMACX_AGENT_TEST_NERVE_GAS", "")
    if mode not in {"conventional", "commit"}:
        raise SystemExit("SMACX_AGENT_TEST_NERVE_GAS must be conventional or commit")
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

    units_response = fixture_units(deadline)
    units = units_response.get("items", [])
    attacker = next((item for item in units if item.get("name") == "Harness Nerve Unit"), None)
    if not attacker:
        try:
            diagnostic = bridge_request("semantic_snapshot", timeout=8)
            diagnostic_choices = bridge_request("semantic_choices", kind="interaction", timeout=8)
        except Exception as exc:  # contained fixture diagnostics only
            diagnostic = {"error": type(exc).__name__, "message": str(exc)}
            diagnostic_choices = {}
        emit("failure", {"stage": "fixture_attacker", "response": units_response,
                         "units": units, "diagnostic": diagnostic,
                         "diagnostic_choices": diagnostic_choices})
        return 4
    attacker_id = int(attacker["id"])
    # First contact can hand control to an AI faction before the fixture read is
    # retried.  Wait for the next guarded human action phase before enumerating
    # unit commands.
    if not wait_for_turn(deadline):
        emit("failure", {"stage": "post_contact_turn"})
        return 5
    choices, attack = attack_choice(attacker_id)
    if not attack:
        emit("failure", {"stage": "attack_choice", "choices": choices})
        return 6
    queued = command(
        choices, command="move_unit", unit_id=attacker_id,
        target_tile_id=int(attack["target_tile_id"]),
    )
    if not queued.get("ok") or not wait_for_nerve_gas(deadline):
        emit("failure", {"stage": "native_nerve_dialog", "queued": queued})
        return 7

    nerve = bridge_request("semantic_choices", kind="interaction")
    conventional = next(
        (item for item in nerve.get("choices", [])
         if item.get("response") == "conventional"), None,
    )
    commit = next(
        (item for item in nerve.get("choices", [])
         if item.get("response") == "commit"), None,
    )
    context = next(
        (item for item in nerve.get("choices", [])
         if item.get("id") == "nerve_gas:context"), None,
    )
    if not conventional or not commit or commit.get("confirm_atrocity") != 1 \
            or not context or context.get("attacker_unit_id") != attacker_id \
            or context.get("target_tile_id") != attack.get("target_tile_id") \
            or int(context.get("action_id", -1)) != int(queued.get("action_id", -2)):
        emit("failure", {"stage": "structured_choices", "choices": nerve,
                         "queued": queued, "attack": attack})
        return 8

    if mode == "commit":
        refused = command(
            nerve, command="respond_to_nerve_gas", response="commit",
        )
        still_open = bridge_request("semantic_snapshot").get("snapshot", {})
        if refused.get("error", {}).get("code") \
                != "nerve_gas_atrocity_confirmation_required" \
                or still_open.get("interaction", {}).get("popup_label") != "USENERVE":
            emit("failure", {"stage": "atrocity_gate", "result": refused,
                             "snapshot": still_open})
            return 9
        resolved = command(
            nerve, command="respond_to_nerve_gas", response="commit",
            confirm_atrocity=1,
        )
    else:
        resolved = command(
            nerve, command="respond_to_nerve_gas", response="conventional",
        )
    action = wait_for_action(int(queued["action_id"]), deadline)
    status = bridge_request("test_nerve_gas_status")
    before = int(status.get("atrocities_before", -1))
    after = int(status.get("atrocities_after", -1))
    expected_effect = after > before if mode == "commit" else after == before
    if not resolved.get("ok") or action.get("status") != "completed" or not expected_effect:
        emit("failure", {"stage": "native_effect", "mode": mode,
                         "result": resolved, "action": action, "status": status})
        return 10

    stale = command(
        nerve, command="respond_to_nerve_gas", response="conventional",
    )
    if stale.get("ok") or stale.get("error", {}).get("code") not in {
        "stale_state", "wrong_choice_phase", "popup_transition_pending",
    }:
        emit("failure", {"stage": "stale_replay", "result": stale})
        return 11
    emit("pass", {
        "native_popup_label": "USENERVE",
        "mode": mode,
        "explicit_atrocity_gate_verified": mode == "commit",
        "native_combat_completed": True,
        "atrocities_before": before,
        "atrocities_after": after,
        "stale_replay_rejected": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
