#!/usr/bin/env python3
"""Regression for semantic-stall detection that ignores native pump churn."""

from __future__ import annotations

import json

from smacx_worker_manager import _semantic_progress_fingerprint


def main() -> int:
    baseline = {
        "match_id": "match-progress-test",
        "session_id": "session-progress-test",
        "revision": "revision-1",
        "timestamp": 100.0,
        "turn": 4,
        "year": 2120,
        "protocol": {"phase": "interaction", "required_action": "respond"},
        "interaction": {"kind": "popup", "popup_label": "COOPERATE"},
        "faction": {"energy_credits": 31, "ready_units": 0},
    }
    pump_churn = {
        **baseline,
        "revision": "revision-9000",
        "timestamp": 9000.0,
        "observed_unix": 123456.0,
        "heartbeat": 81,
        "protocol": {**baseline["protocol"], "revision_hint": "volatile"},
    }
    if _semantic_progress_fingerprint(baseline) != _semantic_progress_fingerprint(pump_churn):
        raise AssertionError("revision/timestamp churn was mistaken for gameplay progress")
    advanced = {**baseline, "turn": 5, "year": 2130}
    resolved = {
        **baseline,
        "protocol": {"phase": "turn", "required_action": "choose_unit_action"},
        "interaction": {"kind": "none"},
    }
    resource_change = {
        **baseline,
        "faction": {"energy_credits": 46, "ready_units": 0},
    }
    original = _semantic_progress_fingerprint(baseline)
    # AI - 2 alternated rejected destinations. Neither changing the receipt
    # nor clearing it may reset the supervisor's no-progress window.
    for status in ("queued", "running", "rejected", "completed"):
        for attempt, target in enumerate((2835, 2756, 2715, 2794), start=1):
            receipt_only = {
                **baseline,
                "last_deferred_action": {
                    "action_id": attempt, "command": "move_unit", "status": status,
                    "native_result": 0, "origin_tile_id": 2755,
                    "target_tile_id": target, "observed_tile_id": 2755,
                },
            }
            if _semantic_progress_fingerprint(receipt_only) != original:
                raise AssertionError("action receipt was mistaken for gameplay progress")
    if _semantic_progress_fingerprint({**baseline, "last_deferred_action": None}) != original:
        raise AssertionError("cleared receipt was mistaken for gameplay progress")
    unit_state = {**baseline, "units": [{"own_unit_ref": "own-unit-1",
                  "location_ref": "location-2755", "moves_remaining": 3, "ready": True}]}
    for change in ({"location_ref": "location-2794"}, {"moves_remaining": 0}, {"ready": False}):
        changed_unit = {**unit_state, "units": [{**unit_state["units"][0], **change}]}
        if _semantic_progress_fingerprint(changed_unit) == _semantic_progress_fingerprint(unit_state):
            raise AssertionError("observed unit effect was hidden from the supervisor")
    if any(_semantic_progress_fingerprint(item) == original
           for item in (advanced, resolved, resource_change)):
        raise AssertionError("meaningful gameplay change was hidden from the supervisor")
    print(json.dumps({"event": "pass", "payload": {
        "revision_churn_ignored": True,
        "turn_change_detected": True,
        "interaction_change_detected": True,
        "resource_change_detected": True,
        "action_receipt_churn_ignored": True,
        "observed_unit_effects_detected": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
