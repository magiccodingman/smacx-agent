#!/usr/bin/env python3
"""Contained regression for native On Alert and automated-former policies."""

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


def own_unit(unit_id: int) -> dict[str, Any]:
    return next(
        (item for item in bridge_request("list_units", scope="own", limit=300).get("items", [])
         if int(item["id"]) == unit_id),
        {},
    )


def assert_activation_only(unit_id: int, reason: str) -> tuple[bool, dict[str, Any]]:
    choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=unit_id)
    commands = [item.get("command") for item in choices.get("choices", [])]
    return choices.get("reason") == reason and commands == ["activate_unit"], choices


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

        units = bridge_request("list_units", scope="own", limit=300).get("items", [])
        combat = next(
            (unit for unit in units if unit.get("ready") and unit.get("roles", {}).get("combat")),
            None,
        )
        former = next(
            (unit for unit in units if unit.get("ready") and unit.get("roles", {}).get("former")),
            None,
        )
        noncombat = next(
            (unit for unit in units if unit.get("ready")
             and not unit.get("roles", {}).get("combat")
             and not unit.get("roles", {}).get("former")),
            None,
        )
        if combat is None or former is None or noncombat is None:
            time.sleep(0.1)
            continue

        combat_id = int(combat["id"])
        combat_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=combat_id,
        )
        alert_choice = next(
            (item for item in combat_choices.get("choices", [])
             if item.get("command") == "set_unit_on_alert"),
            None,
        )
        if alert_choice is None or not alert_choice.get("persistent") \
                or not alert_choice.get("native_automation"):
            emit("failure", {"stage": "alert_choice", "choices": combat_choices})
            return 3

        noncombat_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=int(noncombat["id"]),
        )
        fabricated_alert = command(
            noncombat_choices, "set_unit_on_alert", unit_id=int(noncombat["id"]),
        )
        emit("noncombat_alert_guard", fabricated_alert)
        if fabricated_alert.get("error", {}).get("code") != "on_alert_unavailable":
            return 4

        alerted = command(combat_choices, "set_unit_on_alert", unit_id=combat_id)
        emit("alerted", alerted)
        alert_state = own_unit(combat_id)
        alert_only, alert_ordered_choices = assert_activation_only(combat_id, "on_alert")
        if not alerted.get("ok") or alert_state.get("order_name") != "on_alert" \
                or alert_state.get("order_auto_type") != 11 \
                or alert_state.get("ready") is not False or not alert_only:
            emit("failure", {"stage": "alert_state", "unit": alert_state,
                             "choices": alert_ordered_choices})
            return 5

        alert_activated = command(
            alert_ordered_choices, "activate_unit", unit_id=combat_id,
        )
        emit("alert_activated", alert_activated)
        if not alert_activated.get("ok") \
                or alert_activated.get("old_automation") != "on_alert" \
                or alert_activated.get("ready") is not True:
            return 6

        stale_alert = command(combat_choices, "set_unit_on_alert", unit_id=combat_id)
        if stale_alert.get("error", {}).get("code") != "stale_state":
            emit("failure", {"stage": "stale_alert", "result": stale_alert})
            return 7

        former_id = int(former["id"])
        former_choices = bridge_request(
            "semantic_choices", kind="unit_actions", unit_id=former_id,
        )
        former_modes = [
            item for item in former_choices.get("choices", [])
            if item.get("command") == "automate_former"
        ]
        full = next(
            (item for item in former_modes if item.get("automation_mode") == "full"),
            None,
        )
        if full is None or any("x" in item or "y" in item or "parameters" in item
                               for item in former_modes):
            emit("failure", {"stage": "former_choices", "choices": former_choices})
            return 8

        fabricated_mode = command(
            former_choices, "automate_former", unit_id=former_id,
            automation_mode="fabricated_mode",
        )
        emit("former_mode_guard", fabricated_mode)
        if fabricated_mode.get("error", {}).get("code") != "former_automation_unavailable":
            return 9

        automated = command(
            former_choices, "automate_former", unit_id=former_id,
            automation_mode="full",
        )
        emit("former_automated", automated)
        former_state = own_unit(former_id)
        former_only, former_ordered_choices = assert_activation_only(
            former_id, "auto_former_full",
        )
        if not automated.get("ok") or former_state.get("order_name") != "auto_former_full" \
                or former_state.get("order_auto_type") != 0 \
                or former_state.get("ready") is not False or not former_only:
            emit("failure", {"stage": "former_state", "unit": former_state,
                             "choices": former_ordered_choices})
            return 10

        former_activated = command(
            former_ordered_choices, "activate_unit", unit_id=former_id,
        )
        emit("former_activated", former_activated)
        if not former_activated.get("ok") \
                or former_activated.get("old_automation") != "auto_former_full" \
                or former_activated.get("ready") is not True:
            return 11

        stale_former = command(
            former_choices, "automate_former", unit_id=former_id,
            automation_mode="full",
        )
        if stale_former.get("error", {}).get("code") != "stale_state":
            emit("failure", {"stage": "stale_former", "result": stale_former})
            return 12

        emit("pass", {
            "native_on_alert": True,
            "native_former_policy": True,
            "typed_currently_eligible_modes": [item["automation_mode"] for item in former_modes],
            "activation_only_while_automated": True,
            "noncombat_and_fabricated_guards": True,
            "stale_replay_rejected": True,
            "coordinate_or_pixel_input_used": False,
        })
        return 0
    return 13


if __name__ == "__main__":
    sys.exit(main())
