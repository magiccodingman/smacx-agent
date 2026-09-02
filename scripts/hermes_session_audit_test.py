#!/usr/bin/env python3
"""Regression coverage for privacy-safe Hermes aggregate parsing."""

from __future__ import annotations

import json

from hermes_session_audit import decode_calls, result_error_code


def main() -> int:
    inner = json.dumps({
        "ok": False,
        "error": {"code": "decision_conflict", "message": "private detail"},
        "decision_id": "private-id",
    }, indent=2)
    wrapped = (
        '<untrusted_tool_result source="mcp__smacx__smac_execute_choice">\n'
        "External data warning.\n\n" + json.dumps({"result": inner}) +
        "\n</untrusted_tool_result>"
    )
    if result_error_code(wrapped) != "decision_conflict":
        raise AssertionError("wrapped MCP error code was not recovered")
    if result_error_code(json.dumps({"ok": True, "result": "accepted"})) is not None:
        raise AssertionError("successful result was counted as an error")
    calls, malformed = decode_calls(json.dumps([{
        "function": {"name": "tool_call", "arguments": json.dumps({
            "name": "mcp__smacx__smac_decision", "arguments": {},
        })},
    }]))
    if calls != [("smac_decision", calls[0][1])] or malformed:
        raise AssertionError("Hermes dispatcher aggregation changed")
    print(json.dumps({"event": "pass", "payload": {
        "wrapped_mcp_error_parsed": True,
        "private_payload_not_returned": True,
        "dispatcher_name_recovered": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
