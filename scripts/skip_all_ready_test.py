#!/usr/bin/env python3
"""Contained regression for the guarded skip-all-ready turn action."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from smacx_controller import bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def fail(stage: str, **details: object) -> int:
    emit("failure", {"stage": stage, **details})
    return 1


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=0, world_size=0, faction_id=1,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 120
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") == "turn" \
                and snapshot.get("ready_unit_refs"):
            break
        handled, result = handle_interaction(snapshot)
        if not handled:
            return fail("wait_for_turn", snapshot=snapshot, result=result)
        time.sleep(0.05)
    else:
        return fail("turn_timeout", snapshot=snapshot)

    choices = bridge_request("semantic_choices", kind="game_management")
    skip_choice = next(
        (choice for choice in choices.get("choices", [])
         if choice.get("command") == "skip_all_ready_units"), None,
    )
    units = bridge_request("list_units", scope="own", limit=256)
    unit_by_ref = {str(item.get("own_unit_ref")): int(item["id"])
                   for item in units.get("items", []) if item.get("own_unit_ref")}
    expected_refs = [str(item["own_unit_ref"]) for item in snapshot["ready_unit_refs"]]
    expected_ids = [unit_by_ref[item] for item in expected_refs]
    if not skip_choice:
        return fail("choice_missing", snapshot=snapshot, choices=choices)
    choice_ids = [int(value) for value in skip_choice.get("ready_unit_ids", [])]
    ready_count = int(skip_choice.get("ready_unit_count", -1))
    if choice_ids != expected_ids or ready_count != len(expected_ids) \
            or skip_choice.get("confirm_skip_all_ready") != 1:
        return fail(
            "choice_set_mismatch", expected_ids=expected_ids,
            ready_count=ready_count, choice=skip_choice,
        )

    guard = {
        "match_id": choices["match_id"],
        "session_id": choices["session_id"],
        "expected_revision": choices["revision"],
    }
    unconfirmed = bridge_request(
        "semantic_command", command="skip_all_ready_units",
        ready_unit_count=ready_count, **guard,
    )
    if unconfirmed.get("ok") or unconfirmed.get("error", {}).get("code") \
            != "skip_all_confirmation_required":
        return fail("unconfirmed_not_rejected", result=unconfirmed)

    wrong_count = bridge_request(
        "semantic_command", command="skip_all_ready_units",
        ready_unit_count=ready_count + 1, confirm_skip_all_ready=1, **guard,
    )
    if wrong_count.get("ok") or wrong_count.get("error", {}).get("code") \
            != "ready_unit_set_changed":
        return fail("wrong_count_not_rejected", result=wrong_count)

    unchanged = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
    if unchanged.get("revision") != snapshot.get("revision") \
            or [str(item["own_unit_ref"])
                for item in unchanged.get("ready_unit_refs", [])] != expected_refs:
        return fail("rejection_mutated_state", before=snapshot, after=unchanged)

    applied = bridge_request(
        "semantic_command", command="skip_all_ready_units",
        ready_unit_count=ready_count, confirm_skip_all_ready=1, **guard,
    )
    if not applied.get("ok") or int(applied.get("skipped_unit_count", -1)) != ready_count \
            or [int(value) for value in applied.get("skipped_unit_ids", [])] != expected_ids:
        return fail("application_failed", result=applied, expected_ids=expected_ids)

    after = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
    source_turn = int(snapshot["turn"])
    resulting_turn = int(after.get("turn", -1))
    auto_advanced = resulting_turn != source_turn \
        or after.get("interaction", {}).get("kind") in {"waiting_for_turn", "waiting_for_engine"}
    end_turn_available = False
    management: dict[str, Any] = {}
    if not auto_advanced:
        management = bridge_request("semantic_choices", kind="game_management")
        end_turn_available = any(
            choice.get("command") == "end_turn"
            for choice in management.get("choices", [])
        )
    if (not auto_advanced and (after.get("ready_unit_refs")
                               or int(after.get("faction", {}).get("ready_units", -1)) != 0
                               or not end_turn_available)):
        return fail("postcondition_failed", snapshot=after, choices=management)

    stale = bridge_request(
        "semantic_command", command="skip_all_ready_units",
        ready_unit_count=ready_count, confirm_skip_all_ready=1, **guard,
    )
    if stale.get("ok") or stale.get("error", {}).get("code") != "stale_state":
        return fail("stale_replay_not_rejected", result=stale)

    emit("passed", {
        "ready_unit_count": ready_count,
        "exact_unit_ids": expected_ids,
        "unconfirmed_rejected_without_mutation": True,
        "wrong_count_rejected_without_mutation": True,
        "all_units_skipped": True,
        "end_turn_unblocked_or_native_transition_started": True,
        "native_turn_advanced": auto_advanced,
        "stale_replay_rejected": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
