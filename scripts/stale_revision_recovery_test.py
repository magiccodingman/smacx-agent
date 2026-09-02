#!/usr/bin/env python3
"""Contract for bounded revalidation of harmless stale unit commands."""

from __future__ import annotations

import json

import smacx_mcp


def main() -> int:
    original_call = smacx_mcp._call
    original_gate = smacx_mcp._match_briefing_gate
    calls: list[tuple[str, dict[str, object]]] = []
    stale_count = 0

    def fake_call(operation: str, **arguments: object) -> dict:
        nonlocal stale_count
        calls.append((operation, dict(arguments)))
        if operation == "semantic_choices":
            return {
                "ok": True,
                "match_id": "match-test",
                "session_id": "session-test",
                "revision": "revision-fresh",
                "choices": [{"command": "skip_unit", "unit_id": 15}],
            }
        if operation == "semantic_command":
            if arguments["expected_revision"] == "revision-old":
                stale_count += 1
                return {
                    "ok": False,
                    "error": {"code": "stale_state"},
                    "current_revision": "revision-fresh",
                }
            return {"ok": True, "command": arguments["command"], "unit_id": 15}
        raise AssertionError(f"unexpected operation: {operation}")

    try:
        smacx_mcp._call = fake_call
        smacx_mcp._match_briefing_gate = lambda match_id, session_id: None
        smacx_mcp.CAPABILITY_GAPS.clear()
        result = smacx_mcp.smac_command(
            command="skip_unit",
            match_id="match-test",
            session_id="session-test",
            expected_revision="revision-old",
            unit_id=15,
        )
        command_calls = [item for item in calls if item[0] == "semantic_command"]
        choice_calls = [item for item in calls if item[0] == "semantic_choices"]
        if not result.get("ok") or result.get("guard_revalidated") is not True \
                or result.get("executed_revision") != "revision-fresh" \
                or len(command_calls) != 2 or len(choice_calls) != 1 \
                or command_calls[-1][1].get("expected_revision") != "revision-fresh" \
                or choice_calls[0][1] != {
                    "kind": "unit_actions", "unit_id": 15,
                    "target_tile_id": -1, "target_unit_id": -1,
                }:
            raise AssertionError(f"safe stale revalidation failed: {result}, calls={calls}")

        calls.clear()

        def still_stale(operation: str, **arguments: object) -> dict:
            calls.append((operation, dict(arguments)))
            if operation == "semantic_choices":
                return {
                    "ok": True,
                    "match_id": "match-test", "session_id": "session-test",
                    "revision": "revision-newer",
                    "choices": [{"command": "skip_unit", "unit_id": 15}],
                }
            return {
                "ok": False, "error": {"code": "stale_state"},
                "current_revision": "revision-newest",
            }

        smacx_mcp._call = still_stale
        unsettled = smacx_mcp.smac_command(
            command="skip_unit",
            match_id="match-test",
            session_id="session-test",
            expected_revision="revision-old",
            unit_id=15,
        )
        if unsettled.get("transient") is not True \
                or unsettled.get("capability_gap") is not False \
                or len([item for item in calls if item[0] == "semantic_command"]) != 2:
            raise AssertionError(f"unsettled retry contract failed: {unsettled}, calls={calls}")

        print(json.dumps({
            "event": "pass",
            "payload": {
                "fresh_choice_revalidated": True,
                "exact_unit_preserved": True,
                "single_retry_bound": True,
                "remaining_churn_marked_transient": True,
                "capability_gap_forbidden": True,
            },
        }, separators=(",", ":")))
        return 0
    finally:
        smacx_mcp._call = original_call
        smacx_mcp._match_briefing_gate = original_gate


if __name__ == "__main__":
    raise SystemExit(main())
