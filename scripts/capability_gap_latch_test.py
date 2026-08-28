#!/usr/bin/env python3
"""Regression for the session-scoped capability-gap mutation latch."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import smacx_mcp


def main() -> int:
    original_call = smacx_mcp._call
    original_log = smacx_mcp.GAP_LOG
    original_gaps = dict(smacx_mcp.CAPABILITY_GAPS)
    original_launch_game = smacx_mcp.launch_game
    original_new_game = smacx_mcp.new_game
    original_load_saved_game = smacx_mcp.load_saved_game
    calls: list[tuple[str, dict[str, object]]] = []
    lifecycle_calls: list[str] = []
    match_id = "match-gap-test"
    session_id = "session-gap-test"

    def fake_call(operation: str, **arguments: object) -> dict:
        calls.append((operation, arguments))
        if operation == "semantic_snapshot":
            return {"ok": True, "snapshot": {
                "match_id": match_id,
                "session_id": session_id,
                "revision": "revision-gap-test",
                "turn": 7,
                "year": 2107,
            }}
        if operation == "status":
            return {"ok": True, "identity": {
                "match_id": match_id,
                "session_id": session_id,
            }}
        if operation == "semantic_command":
            return {"ok": False, "error": {"code": "fixture_reached_bridge"}}
        raise AssertionError(f"unexpected operation: {operation}")

    try:
        with tempfile.TemporaryDirectory(prefix="smacx-gap-") as directory:
            smacx_mcp._call = fake_call
            smacx_mcp.GAP_LOG = Path(directory) / "capability-gaps.jsonl"
            smacx_mcp.CAPABILITY_GAPS.clear()
            smacx_mcp.launch_game = lambda **kwargs: lifecycle_calls.append("launch") or {"ok": True}
            smacx_mcp.new_game = lambda **kwargs: lifecycle_calls.append("new") or {"ok": True}
            smacx_mcp.load_saved_game = (
                lambda *args, **kwargs: lifecycle_calls.append("load") or {"ok": True}
            )

            report_args = {
                "screen_or_state": "fixture modal",
                "intended_decision": "resolve the fixture",
                "required_observation": "enumerated legal responses",
                "required_action": "respond_to_fixture",
                "why_blocked": "no semantic choice is exposed",
            }
            first = smacx_mcp.smac_report_capability_gap(**report_args)
            second = smacx_mcp.smac_report_capability_gap(**report_args)
            if not first.get("recorded") or first.get("already_latched"):
                raise AssertionError(f"first report did not create a latch: {first}")
            if second.get("recorded") or not second.get("already_latched"):
                raise AssertionError(f"duplicate report was not deduplicated: {second}")
            if first.get("gap", {}).get("gap_id") != second.get("gap", {}).get("gap_id"):
                raise AssertionError("duplicate report did not return the existing gap id")
            lines = smacx_mcp.GAP_LOG.read_text(encoding="utf-8").splitlines()
            if len(lines) != 1 or json.loads(lines[0]).get("turn") != 7:
                raise AssertionError(f"capability-gap audit log invalid: {lines}")

            semantic_calls_before = sum(name == "semantic_command" for name, _ in calls)
            blocked = smacx_mcp.smac_command(
                command="skip_unit", match_id=match_id, session_id=session_id,
                expected_revision="revision-gap-test", unit_id=1,
            )
            semantic_calls_after = sum(name == "semantic_command" for name, _ in calls)
            if blocked.get("error", {}).get("code") != "capability_gap_latched" \
                    or semantic_calls_after != semantic_calls_before:
                raise AssertionError(f"latched command reached the bridge: {blocked}")

            status = smacx_mcp.smac_status()
            if not status.get("gameplay_mutations_blocked") \
                    or status.get("capability_gap_latched", {}).get("session_id") != session_id:
                raise AssertionError(f"status omitted active latch: {status}")

            lifecycle_results = [
                smacx_mcp.smac_launch(),
                smacx_mcp.smac_new_game(),
                smacx_mcp.smac_saves(action="load", match_id=match_id, slot="fixture"),
            ]
            if lifecycle_calls or any(
                item.get("error", {}).get("code") != "capability_gap_latched"
                for item in lifecycle_results
            ):
                raise AssertionError(
                    f"latched lifecycle operation escaped: {lifecycle_results}, {lifecycle_calls}"
                )

            fresh = smacx_mcp.smac_command(
                command="skip_unit", match_id=match_id, session_id="fresh-session",
                expected_revision="fresh-revision", unit_id=1,
            )
            if fresh.get("error", {}).get("code") != "capability_gap_latched":
                raise AssertionError(f"fresh session bypassed unresolved development latch: {fresh}")

            smacx_mcp.CAPABILITY_GAPS.clear()  # Simulates the developer-controlled MCP restart.
            resumed = smacx_mcp.smac_command(
                command="skip_unit", match_id=match_id, session_id="fresh-session",
                expected_revision="fresh-revision", unit_id=1,
            )
            if resumed.get("error", {}).get("code") != "fixture_reached_bridge":
                raise AssertionError(f"developer reset did not restore native guards: {resumed}")

            print(json.dumps({
                "event": "pass",
                "payload": {
                    "session_scoped": True,
                    "duplicate_reports_deduplicated": True,
                    "latched_mutation_never_reached_bridge": True,
                    "status_exposes_latch": True,
                    "launch_new_and_load_blocked": True,
                    "fresh_session_cannot_bypass_unresolved_gap": True,
                    "developer_restart_restores_native_guards": True,
                    "agent_clear_tool_exposed": False,
                },
            }, separators=(",", ":")))
            return 0
    finally:
        smacx_mcp._call = original_call
        smacx_mcp.GAP_LOG = original_log
        smacx_mcp.launch_game = original_launch_game
        smacx_mcp.new_game = original_new_game
        smacx_mcp.load_saved_game = original_load_saved_game
        smacx_mcp.CAPABILITY_GAPS.clear()
        smacx_mcp.CAPABILITY_GAPS.update(original_gaps)


if __name__ == "__main__":
    raise SystemExit(main())
