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
    if any(_semantic_progress_fingerprint(item) == original
           for item in (advanced, resolved, resource_change)):
        raise AssertionError("meaningful gameplay change was hidden from the supervisor")
    print(json.dumps({"event": "pass", "payload": {
        "revision_churn_ignored": True,
        "turn_change_detected": True,
        "interaction_change_detected": True,
        "resource_change_detected": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
