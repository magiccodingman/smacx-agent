#!/usr/bin/env python3
"""Contained semantic regression for guarded facility recycling."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


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
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        interaction = snapshot.get("interaction", {}).get("kind")
        if interaction != "turn":
            if interaction in {"waiting_for_engine", "waiting_for_turn"}:
                time.sleep(0.1)
                continue
            handled, outcome = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "interaction", "snapshot": snapshot, "outcome": outcome})
                return 3
            continue
        bases_before = bridge_request("list_bases").get("items", [])
        if not bases_before:
            return 4
        base_before = bases_before[0]
        base_id = int(base_before["id"])
        choices = bridge_request("semantic_choices", kind="base_management", base_id=base_id)
        recycle = next(
            (item for item in choices.get("choices", [])
             if item.get("command") == "recycle_facility"
             and item.get("facility_name") == "Recreation Commons"),
            None,
        )
        if recycle is None or recycle.get("confirm_recycle") != 1:
            emit("failure", {"stage": "choice", "choices": choices})
            return 5
        if any(item.get("command") == "recycle_facility" and item.get("facility_id") == 1
               for item in choices.get("choices", [])):
            emit("failure", {"stage": "headquarters_exposed", "choices": choices})
            return 6
        facility_id = int(recycle["facility_id"])
        refund = int(recycle["energy_refund"])
        energy_before = int(snapshot["faction"]["energy_credits"])
        refused = guarded(
            choices, "recycle_facility", base_id=base_id, facility_id=facility_id,
        )
        emit("recycle_guard", refused)
        if refused.get("error", {}).get("code") != "recycle_confirmation_required":
            return 7
        recycled = guarded(
            choices,
            "recycle_facility",
            base_id=base_id,
            facility_id=facility_id,
            confirm_recycle=1,
        )
        emit("recycled", {"choice": recycle, "result": recycled})
        if not recycled.get("ok") or int(recycled.get("energy_refund", -1)) != refund:
            return 8
        bases_after = bridge_request("list_bases").get("items", [])
        base_after = next((item for item in bases_after if int(item.get("id", -1)) == base_id), None)
        if base_after is None:
            return 9
        facilities_after = base_after.get("facilities", [])
        if any(int(item.get("facility_id", -1)) == facility_id for item in facilities_after):
            emit("failure", {"stage": "facility_not_removed", "base": base_after})
            return 10
        if not base_after.get("facility_recycled_this_turn"):
            emit("failure", {"stage": "turn_limit_flag", "base": base_after})
            return 11
        if int(recycled.get("energy_credits", -1)) != energy_before + refund:
            emit("failure", {"stage": "refund", "before": energy_before, "result": recycled})
            return 12
        fresh = bridge_request("semantic_choices", kind="base_management", base_id=base_id)
        if any(item.get("command") == "recycle_facility" for item in fresh.get("choices", [])):
            emit("failure", {"stage": "second_recycle_exposed", "choices": fresh})
            return 13
        emit("pass", {
            "explicit_destructive_confirmation": True,
            "native_refund_formula_verified": refund,
            "facility_removed": True,
            "one_per_base_per_turn_enforced": True,
            "headquarters_excluded": True,
            "coordinates_or_pixels_used": False,
        })
        return 0
    return 14


if __name__ == "__main__":
    sys.exit(main())
