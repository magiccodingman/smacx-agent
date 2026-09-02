#!/usr/bin/env python3
from __future__ import annotations

import json

import smacx_mcp


def main() -> int:
    original_call = smacx_mcp._call
    original_gate = smacx_mcp._match_briefing_gate
    original_gap = smacx_mcp._pending_capability_gap
    original_journal = smacx_mcp.controller_record_campaign_action
    calls: list[tuple[str, dict]] = []
    attempts = 0
    try:
        smacx_mcp._match_briefing_gate = lambda *_: None
        smacx_mcp._pending_capability_gap = lambda: None
        smacx_mcp.controller_record_campaign_action = lambda *args, **kwargs: {"ok": True}

        def bridge(operation: str, **arguments: object) -> dict:
            nonlocal attempts
            calls.append((operation, dict(arguments)))
            if operation == "semantic_command":
                attempts += 1
                if attempts == 1:
                    return {"ok": False, "error": {"code": "stale_state"},
                            "current_revision": "r2"}
                return {"ok": True, "command": "disband_unit", "completed": True}
            if operation == "semantic_choices":
                return {
                    "ok": True, "match_id": "match-rebase", "session_id": "session-rebase",
                    "revision": "r2", "choices": [{
                        "command": "disband_unit", "unit_id": 15,
                        "requires": {"confirm_disband": 1},
                    }],
                }
            if operation == "semantic_snapshot":
                return {"ok": True, "snapshot": {"revision": "r3", "turn": 1, "year": 2101}}
            raise AssertionError(operation)

        smacx_mcp._call = bridge
        decision_id, choices = smacx_mcp._cache_decision_choices(
            {"match_id": "match-rebase", "session_id": "session-rebase", "revision": "r1"},
            [{"command": "disband_unit", "unit_id": 15,
              "requires": {"confirm_disband": 1}}], choice_kind="unit_actions",
            choice_arguments={"unit_id": 15, "target_tile_id": -1, "target_unit_id": -1},
            focus={"kind": "ready_unit", "unit": {"id": 15}}, turn=1, year=2101,
            phase="turn",
        )
        result = smacx_mcp.smac_execute_choice(decision_id, choices[0]["choice_id"])
        if not result.get("ok") or result.get("guard_revalidated") is not True or attempts != 2:
            raise AssertionError(result)
        command_revisions = [payload.get("expected_revision") for operation, payload in calls
                             if operation == "semantic_command"]
        if command_revisions != ["r1", "r2"]:
            raise AssertionError(command_revisions)
        confirmations = [payload.get("confirm_disband") for operation, payload in calls
                         if operation == "semantic_command"]
        if confirmations != [1, 1]:
            raise AssertionError(confirmations)
        print(json.dumps({"event": "pass", "payload": {
            "one_server_side_rebase": True, "model_retry_required": False,
            "revision_churn_hidden": True, "private_confirmation_preserved": True,
        }}, separators=(",", ":")))
    finally:
        smacx_mcp._call = original_call
        smacx_mcp._match_briefing_gate = original_gate
        smacx_mcp._pending_capability_gap = original_gap
        smacx_mcp.controller_record_campaign_action = original_journal
        smacx_mcp.DECISION_CACHE.clear()
        smacx_mcp.ACTION_PROGRESS.clear()
        smacx_mcp.RUNTIME_CIRCUITS.clear()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
