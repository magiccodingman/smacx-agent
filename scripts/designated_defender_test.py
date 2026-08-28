#!/usr/bin/env python3
"""Contained regression for the native designated-defender role toggle."""

from __future__ import annotations

import json
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


def reach_turn(deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        if snapshot.get("interaction", {}).get("kind") == "turn":
            return snapshot
        handled = handle_interaction(snapshot)
        if handled:
            emit("interaction", handled)
        time.sleep(0.1)
    return {}


def own_unit(unit_id: int) -> dict[str, Any]:
    items = bridge_request("list_units", scope="own", limit=300).get("items", [])
    return next((item for item in items if int(item["id"]) == unit_id), {})


def main() -> int:
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

    deadline = time.monotonic() + 120
    if not reach_turn(deadline):
        return 3
    scout = next(
        (item for item in bridge_request("list_units", scope="own", limit=300).get("items", [])
         if item.get("ready") and item.get("roles", {}).get("combat")),
        None,
    )
    if scout is None:
        return 4
    scout_id = int(scout["id"])
    # The native game may automatically designate its initial garrison shortly
    # after planetfall.  Let that settle, then toggle only the exact opposite
    # state advertised by a fresh choice.
    time.sleep(0.5)
    before = bridge_request("semantic_choices", kind="unit_actions", unit_id=scout_id)
    toggle = next(
        (item for item in before.get("choices", [])
         if item.get("command") == "set_designated_defender"),
        None,
    )
    if toggle is None or toggle.get("consumes_turn") is not False:
        emit("failure", {"stage": "toggle_choice", "choices": before})
        return 5
    initial = bool(toggle.get("current"))
    first_target = int(toggle.get("active", -1))
    if first_target not in (0, 1) or bool(first_target) == initial:
        return 5

    changed = command(
        before, "set_designated_defender", unit_id=scout_id, active=first_target,
    )
    emit("changed", changed)
    state_changed = own_unit(scout_id)
    if not changed.get("ok") or changed.get("ready") is not True \
            or state_changed.get("designated_defender") is not bool(first_target) \
            or state_changed.get("ready") is not True:
        return 6

    if not reach_turn(deadline):
        return 7
    after_change = bridge_request("semantic_choices", kind="unit_actions", unit_id=scout_id)
    restore = next(
        (item for item in after_change.get("choices", [])
         if item.get("command") == "set_designated_defender"
         and item.get("active") == int(initial)),
        None,
    )
    if restore is None:
        emit("failure", {"stage": "restore_choice", "choices": after_change})
        return 8

    restored = command(
        after_change, "set_designated_defender", unit_id=scout_id, active=int(initial),
    )
    emit("restored", restored)
    state_restored = own_unit(scout_id)
    if not restored.get("ok") or state_restored.get("designated_defender") is not initial \
            or state_restored.get("ready") is not True:
        return 9

    stale = command(
        before, "set_designated_defender", unit_id=scout_id, active=first_target,
    )
    emit("stale_guard", stale)
    if stale.get("error", {}).get("code") != "stale_state":
        return 10

    emit("pass", {
        "native_role_toggle": True,
        "state_observable": True,
        "turn_not_consumed": True,
        "opposite_choice_only": True,
        "stale_replay_after_cycle_rejected": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
