#!/usr/bin/env python3
"""Exercise the real managed choice wrapper with adverse native-shaped receipts."""
from __future__ import annotations

import smacx_mcp as m


def main() -> None:
    m._sovereign_gameplay_gate = lambda operation: None
    m._pending_capability_gap = lambda: None
    m._match_briefing_gate = lambda *args: None
    m.controller_record_campaign_action = lambda *args, **kwargs: {"ok": True}
    reports = []
    m.smac_report_capability_gap = lambda **kwargs: reports.append(kwargs) or {"gap": {"recorded": True}}
    dispatched = []

    def bridge(operation, **arguments):
        if operation == "semantic_command":
            dispatched.append(arguments)
            return {"ok": False, "error": {"code": "native_action_rejected"}}
        if operation == "semantic_snapshot":
            return {"ok": True, "snapshot": {"match_id": "match-budget", "session_id": "session-budget", "turn": 4}}
        raise AssertionError(operation)

    m._call = bridge
    key = ("match-budget", "session-budget")

    def frame(target):
        return m._cache_decision_choices(
            {"match_id": key[0], "session_id": key[1], "revision": f"r{target}"},
            [{"command": "move_unit", "unit_id": 10, "target_tile_id": target}],
            choice_kind="unit", choice_arguments={}, focus={"own_unit_ref": "own-unit-1"},
            turn=4, year=2104, phase="turn")

    first_id, first_choices = frame(2835)
    first = m.smac_execute_choice(first_id, first_choices[0]["choice_id"])
    assert first["decision_consumed"] is True and first["execution_status"] == "rejected", first
    assert first["required_next"]["tool"] == "smac_decision", first
    replay = m.smac_execute_choice(first_id, first_choices[0]["choice_id"])
    assert replay["execution_status"] == "not_dispatched" and replay["native_action_executed"] is False, replay
    second_id, second_choices = frame(2756)
    second = m.smac_execute_choice(second_id, second_choices[0]["choice_id"])
    assert second["failure_budget"]["consecutive_failures"] == 3, second
    # Fabricated decision has no cache identity: bind it to the managed seat.
    m.MANAGED_ATTACHED = True
    m._managed_scope_identity = lambda: (*key, "agent-budget", "perspective-budget")
    stopped = m.smac_execute_choice("invented-decision", "invented-choice")
    assert stopped["error"]["code"] == "failure_circuit_open", stopped
    assert stopped["native_action_executed"] is False and stopped["required_next"]["stop_after"], stopped
    assert len(dispatched) == 2 and len(reports) == 1, (dispatched, reports)
    blocked = m.smac_execute_choice(second_id, second_choices[0]["choice_id"])
    assert blocked["required_next"]["stop_after"] and len(dispatched) == 2, blocked
    # A new native session must not inherit the old session's budget/latch.
    m._managed_scope_identity = lambda: (key[0], "new-session", "agent-budget", "perspective-budget")
    fresh = m.smac_execute_choice("new-unknown", "new-choice")
    assert fresh["failure_budget"]["consecutive_failures"] == 1, fresh
    assert fresh["decision_consumed"] is None, fresh
    # A successful guarded submission resets only this failure budget.
    m.MANAGED_ATTACHED = False
    m.RUNTIME_CIRCUITS.clear()
    m.ACTION_PROGRESS.clear()
    m.FAILED_CHOICE_ATTEMPTS.clear()
    fail_id, fail_choices = frame(2715)
    m.smac_execute_choice(fail_id, fail_choices[0]["choice_id"])
    m._call = lambda operation, **arguments: ({"ok": True, "changed": True}
        if operation == "semantic_command" else bridge(operation, **arguments))
    success_id, success_choices = frame(2794)
    success = m.smac_execute_choice(success_id, success_choices[0]["choice_id"])
    assert success["ok"] and success["decision_consumed"] is True, success
    assert key not in m.FAILED_CHOICE_ATTEMPTS
    m._call = bridge
    fail_id, fail_choices = frame(2756)
    after_success = m.smac_execute_choice(fail_id, fail_choices[0]["choice_id"])
    assert after_success["failure_budget"]["consecutive_failures"] == 1, after_success
    print("failed choice budget tests passed: alternating targets, consumed/unknown IDs, dispatch containment, session isolation")


if __name__ == "__main__":
    main()
