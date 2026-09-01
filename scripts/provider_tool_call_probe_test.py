#!/usr/bin/env python3
"""Deterministic parser and classifier tests for provider_tool_call_probe.py."""

from __future__ import annotations

import json

from provider_tool_call_probe import CASES, assemble_stream_tool_calls, validate_calls


def chunks(arguments: dict, *, split: bool = True) -> list[dict]:
    encoded = json.dumps(arguments, separators=(",", ":"))
    pieces = [encoded[: len(encoded) // 2], encoded[len(encoded) // 2 :]] if split else [encoded]
    events = []
    for position, piece in enumerate(pieces):
        fragment = {"index": 0, "function": {"arguments": piece}}
        if position == 0:
            fragment.update({"id": "call-1", "type": "function"})
            fragment["function"]["name"] = "tool_call"
        events.append({"choices": [{"index": 0, "delta": {"tool_calls": [fragment]}}]})
    events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
    return events


def main() -> int:
    case = CASES["command_full"]
    expected = {"name": case.calls[0].name, "arguments": case.calls[0].arguments}
    calls, issues = assemble_stream_tool_calls(chunks(expected))
    passed = validate_calls(case, calls, issues)
    if not passed["passed"]:
        raise AssertionError(passed)

    malformed = {"name": case.calls[0].name, "arguments": {}}
    calls, issues = assemble_stream_tool_calls(chunks(malformed))
    failed = validate_calls(case, calls, issues)
    if failed["passed"] or not failed["provider_side_reproduction"]:
        raise AssertionError(failed)
    missing = failed["issues"][-1].get("missing")
    if missing != ["command", "match_id", "session_id", "expected_revision"]:
        raise AssertionError(f"unexpected missing fields: {failed}")

    flattened = {
        "name": case.calls[0].name,
        "arguments": "",
        **case.calls[0].arguments,
    }
    calls, issues = assemble_stream_tool_calls(chunks(flattened))
    flattened_result = validate_calls(case, calls, issues)
    if not any(
        issue["kind"] == "provider_wire_nested_object_flattening"
        for issue in flattened_result["issues"]
    ):
        raise AssertionError(flattened_result)

    invalid_events = [{"_wire_json_error": "unterminated JSON", "_wire_data": "{"}]
    calls, issues = assemble_stream_tool_calls(invalid_events)
    invalid = validate_calls(case, calls, issues)
    if invalid["passed"] or not any(i["kind"] == "wire_json_error" for i in invalid["issues"]):
        raise AssertionError(invalid)

    print(json.dumps({
        "event": "pass",
        "payload": {
            "stream_fragment_assembly": True,
            "nested_argument_loss_detected": True,
            "nested_object_flattening_detected": True,
            "invalid_sse_json_detected": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
