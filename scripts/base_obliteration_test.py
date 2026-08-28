#!/usr/bin/env python3
"""Contained native-effect regression for guarded base obliteration."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import BridgeUnavailable, bridge_request, new_game


FIXTURE_NAME = "Harness Oblit Base"


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def guarded(source: dict[str, Any], command: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        timeout=12,
        command=command,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def main() -> int:
    objective_only = os.environ.get("SMACX_AGENT_TEST_BASE_OBLITERATION") == "objective"
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
    queued: dict[str, Any] | None = None
    before_count = -1
    target_base_id = -1
    target_unit_id = -1
    target_tile_id = -1
    native_confirmation_seen = False
    destruction_guard_seen = False
    atrocity_guard_seen = False
    native_notice_seen = False
    atrocity_expected = True

    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if not snapshot:
            time.sleep(0.1)
            continue
        interaction = snapshot.get("interaction", {})
        kind = interaction.get("kind")
        label = interaction.get("popup_label", "")

        if queued is not None and label in {"OBLIT", "OBLITOK"}:
            choices = bridge_request("semantic_choices", kind="interaction")
            cancel = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "respond_to_base_obliteration"
                 and item.get("response") == "cancel"),
                None,
            )
            proceed = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "respond_to_base_obliteration"
                 and item.get("response") == "proceed"),
                None,
            )
            if cancel is None or proceed is None \
                    or proceed.get("confirm_obliteration") != 1 \
                    or bool(proceed.get("confirm_atrocity")) != atrocity_expected \
                    or not proceed.get("destructive") \
                    or not proceed.get("consequential"):
                emit("failure", {"stage": "confirmation_contract", "choices": choices})
                return 5
            native_confirmation_seen = True

            refused = guarded(
                choices,
                "respond_to_base_obliteration",
                response="proceed",
            )
            emit("destruction_guard", refused)
            if refused.get("error", {}).get("code") \
                    != "obliteration_confirmation_required":
                return 5
            destruction_guard_seen = True

            if atrocity_expected:
                refused_atrocity = guarded(
                    choices,
                    "respond_to_base_obliteration",
                    response="proceed",
                    confirm_obliteration=1,
                )
                emit("atrocity_guard", refused_atrocity)
                if refused_atrocity.get("error", {}).get("code") \
                        != "atrocity_confirmation_required":
                    return 6
                atrocity_guard_seen = True

            accepted = guarded(
                choices,
                "respond_to_base_obliteration",
                response="proceed",
                confirm_obliteration=1,
                confirm_atrocity=1,
            )
            emit("native_confirmation", {"label": label, "result": accepted})
            if not accepted.get("ok"):
                return 7
            time.sleep(0.1)
            continue

        if queued is not None and label in {"OBLITTED", "OBLITTED2"}:
            choices = bridge_request("semantic_choices", kind="interaction")
            context = next(
                (item for item in choices.get("choices", [])
                 if item.get("event") == "base_obliterated"),
                None,
            )
            acknowledge = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "acknowledge_popup"),
                None,
            )
            if context is None or acknowledge is None:
                emit("failure", {"stage": "native_notice_contract", "choices": choices})
                return 8
            result = guarded(choices, "acknowledge_popup")
            emit("native_notice", {"label": label, "context": context, "result": result})
            if not result.get("ok"):
                return 9
            native_notice_seen = True
            time.sleep(0.1)
            continue

        if queued is None and kind == "turn":
            bases = bridge_request("list_bases", limit=100).get("items", [])
            if objective_only:
                if not bases:
                    emit("failure", {"stage": "objective_fixture", "bases": bases})
                    return 4
                objective = bases[0]
                objective_choices = bridge_request(
                    "semantic_choices", kind="base_management",
                    base_id=int(objective["id"]),
                )
                if any(item.get("command") == "obliterate_base"
                       for item in objective_choices.get("choices", [])):
                    emit("failure", {"stage": "objective_choice_exposed",
                                     "choices": objective_choices})
                    return 4
                own_units = bridge_request(
                    "list_units", scope="own", limit=200,
                ).get("items", [])
                objective_unit = next(
                    (item for item in own_units
                     if int(item.get("tile_id", -999)) == int(objective["tile_id"])),
                    None,
                )
                if objective_unit is None:
                    emit("failure", {"stage": "objective_unit_fixture", "units": own_units})
                    return 4
                objective_refused = guarded(
                    objective_choices,
                    "obliterate_base",
                    base_id=int(objective["id"]),
                    unit_id=int(objective_unit["id"]),
                    confirm_obliteration=1,
                    confirm_atrocity=1,
                )
                emit("objective_guard", objective_refused)
                if objective_refused.get("error", {}).get("code") \
                        != "objective_base_cannot_be_obliterated":
                    return 4
                emit("pass", {
                    "objective_choice_suppressed": True,
                    "fabricated_objective_obliteration_rejected": True,
                    "base_remained_owned": True,
                    "pixels_or_ui_input_used": False,
                })
                return 0
            target = next((item for item in bases if item.get("name") == FIXTURE_NAME), None)
            if target is None:
                time.sleep(0.1)
                continue
            before_count = len(bases)
            target_base_id = int(target["id"])
            target_tile_id = int(target["tile_id"])
            choices = bridge_request(
                "semantic_choices", kind="base_management", base_id=target_base_id,
            )
            action = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "obliterate_base"),
                None,
            )
            if action is None or not action.get("confirmation_follows") \
                    or action.get("destructive") or action.get("consequential"):
                emit("failure", {"stage": "choice_contract", "choices": choices})
                return 4
            target_unit_id = int(action["unit_id"])
            atrocity_expected = bool(action.get("atrocity_under_current_rules"))

            queued = guarded(
                choices,
                "obliterate_base",
                base_id=target_base_id,
                unit_id=target_unit_id,
            )
            emit("queued", {"choice": action, "result": queued})
            if not queued.get("ok") or not queued.get("queued"):
                return 7
            continue

        if queued is not None:
            status = bridge_request("action_status", action_id=int(queued["action_id"]))
            action_status = status.get("action", {})
            if action_status.get("status") == "completed":
                emit("completed", status)
                if action_status.get("resolution") != "native_base_obliterated":
                    return 10
                bases_after = bridge_request("list_bases", limit=100).get("items", [])
                if len(bases_after) != before_count - 1 \
                        or any(item.get("name") == FIXTURE_NAME for item in bases_after) \
                        or any(int(item.get("tile_id", -999)) == target_tile_id
                               for item in bases_after):
                    emit("failure", {"stage": "native_effect", "bases": bases_after})
                    return 11
                units_after = bridge_request("list_units", scope="own", limit=200).get("items", [])
                initiating_unit_survived = any(
                    int(item.get("id", -1)) == target_unit_id
                    and int(item.get("tile_id", -999)) == target_tile_id
                    for item in units_after
                )
                if not initiating_unit_survived or not native_notice_seen \
                        or not native_confirmation_seen:
                    emit("failure", {
                        "stage": "continuation",
                        "native_confirmation_seen": native_confirmation_seen,
                        "native_notice_seen": native_notice_seen,
                        "initiating_unit_survived": initiating_unit_survived,
                    })
                    return 12
                emit("pass", {
                    "native_confirmation_seen": native_confirmation_seen,
                    "explicit_destruction_confirmation": destruction_guard_seen,
                    "explicit_atrocity_confirmation": atrocity_guard_seen,
                    "objective_base_exclusion_checked_by_separate_mode": True,
                    "native_base_count_delta": -1,
                    "native_obliteration_notice_seen": native_notice_seen,
                    "initiating_unit_survived": initiating_unit_survived,
                    "pixels_or_ui_input_used": False,
                })
                return 0
            if action_status.get("status") == "rejected":
                emit("failure", {"stage": "native_rejection", "status": status})
                return 13

        if kind in {"waiting_for_engine", "waiting_for_turn"}:
            time.sleep(0.1)
            continue
        if kind != "turn":
            handled, outcome = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "interaction", "snapshot": snapshot,
                                 "outcome": outcome})
                return 14
        time.sleep(0.1)

    emit("failure", {"stage": "deadline", "queued": queued})
    return 15


if __name__ == "__main__":
    sys.exit(main())
