#!/usr/bin/env python3
"""Emit privacy-safe aggregate diagnostics for one Hermes state database.

No message content, reasoning text, tool arguments, provider address, system
prompt, or model output is copied into the report. The audit is intentionally
safe to combine with a public simulation report.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import statistics
from typing import Any


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
TURN_HANDOFF_WORD_LIMIT = 120


def safe_name(value: Any, fallback: str = "unknown") -> str:
    candidate = str(value or "")
    return candidate if SAFE_NAME.fullmatch(candidate) else fallback


def decode_calls(value: Any) -> tuple[list[tuple[str, str]], int]:
    if not value:
        return [], 0
    try:
        rows = json.loads(str(value))
    except json.JSONDecodeError:
        return [], 1
    if not isinstance(rows, list):
        return [], 1
    result: list[tuple[str, str]] = []
    malformed = 0
    for row in rows:
        if not isinstance(row, dict):
            malformed += 1
            continue
        function = row.get("function")
        if not isinstance(function, dict):
            malformed += 1
            continue
        name = safe_name(function.get("name"))
        arguments = function.get("arguments", "{}")
        if isinstance(arguments, str):
            try:
                decoded = json.loads(arguments)
            except json.JSONDecodeError:
                malformed += 1
                decoded = {"_malformed": True}
        else:
            decoded = arguments
        if not isinstance(decoded, dict):
            malformed += 1
        elif name == "tool_call":
            # Hermes deliberately exposes one generic dispatcher. Recover
            # only its safe MCP operation name for useful aggregate reports;
            # nested arguments remain hashed and never leave this process.
            dispatched = safe_name(decoded.get("name"), "")
            if dispatched:
                name = dispatched.removeprefix("mcp__smacx__")
        # A digest identifies exact repetition without exposing arguments.
        digest = hashlib.sha256(json.dumps(
            decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()[:16]
        result.append((name, digest))
    return result, malformed


def result_error_code(content: Any) -> str | None:
    if not content:
        return None
    text_value = str(content)
    try:
        value = json.loads(text_value)
    except json.JSONDecodeError:
        # Hermes wraps MCP output in an untrusted-source envelope and places
        # the actual server response in a JSON ``result`` string. Decode only
        # the structured object; never copy the surrounding content into the
        # report.
        value = None
        decoder = json.JSONDecoder()
        for offset, character in enumerate(text_value):
            if character != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(text_value[offset:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                value = candidate
        if value is None:
            return None
    if not isinstance(value, dict):
        return None
    for _ in range(2):
        nested = value.get("result")
        if not isinstance(nested, str):
            break
        try:
            decoded = json.loads(nested)
        except json.JSONDecodeError:
            break
        if not isinstance(decoded, dict):
            break
        value = decoded
    if value.get("ok") is not False and not value.get("error"):
        return None
    for key in ("code", "error_code", "error"):
        candidate = value.get(key)
        if isinstance(candidate, dict):
            candidate = candidate.get("code") or candidate.get("type")
        if isinstance(candidate, str):
            return safe_name(candidate, "reported_error")
    return "reported_error"


def summarize(numbers: list[int]) -> dict[str, int | float | None]:
    return {
        "samples": len(numbers),
        "minimum": min(numbers) if numbers else None,
        "median": statistics.median(numbers) if numbers else None,
        "maximum": max(numbers) if numbers else None,
        "mean": round(statistics.fmean(numbers), 3) if numbers else None,
    }


def audit(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        sessions = [dict(row) for row in connection.execute(
            "SELECT id,started_at,ended_at,end_reason,message_count,tool_call_count,"
            "input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,"
            "reasoning_tokens,api_call_count,compression_fallback_streak,"
            "compression_ineffective_count FROM sessions ORDER BY started_at"
        )]
        messages = [dict(row) for row in connection.execute(
            "SELECT session_id,role,content,tool_calls,tool_name,finish_reason,"
            "active,compacted FROM messages ORDER BY id"
        )]
    finally:
        connection.close()
    tools: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    finish_reasons: Counter[str] = Counter()
    malformed = 0
    exact_repeat_pairs = 0
    longest_exact_run = 0
    handoff_word_counts: list[int] = []
    prior: tuple[str, str] | None = None
    current_run = 0
    for message in messages:
        content = message.get("content")
        if message.get("role") == "assistant" and isinstance(content, str) \
                and content.lstrip().upper().startswith("TURN HANDOFF"):
            handoff_word_counts.append(len(content.split()))
        finish = safe_name(message.get("finish_reason"), "")
        if finish:
            finish_reasons[finish] += 1
        calls, invalid = decode_calls(message.get("tool_calls"))
        malformed += invalid
        # Repetition is a control-loop property. A user/episode boundary or an
        # ordinary assistant yield deliberately breaks that loop, so identical
        # calls on the two sides of a TURN HANDOFF are not false-positive
        # retries.
        if message.get("role") == "user" or (
                message.get("role") == "assistant" and not calls):
            prior = None
            current_run = 0
        for call in calls:
            tools[call[0]] += 1
            if call == prior:
                exact_repeat_pairs += 1
                current_run += 1
            else:
                current_run = 1
            longest_exact_run = max(longest_exact_run, current_run)
            prior = call
        if message.get("role") == "tool":
            code = result_error_code(message.get("content"))
            if code:
                errors[code] += 1
    token_columns = (
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "reasoning_tokens", "api_call_count",
    )
    return {
        "schema": "smacx.hermes-session-audit.v1",
        "privacy": {
            "message_content_included": False,
            "reasoning_text_included": False,
            "tool_arguments_included": False,
            "provider_endpoint_included": False,
            "system_prompt_included": False,
        },
        "sessions": len(sessions),
        "completed_sessions": sum(row.get("ended_at") is not None for row in sessions),
        "end_reasons": dict(sorted(Counter(
            safe_name(row.get("end_reason"), "unknown") for row in sessions
            if row.get("end_reason")
        ).items())),
        "totals": {
            column: sum(int(row.get(column) or 0) for row in sessions)
            for column in token_columns
        },
        "per_session_input_tokens": summarize([
            int(row.get("input_tokens") or 0) for row in sessions
        ]),
        "per_session_output_tokens": summarize([
            int(row.get("output_tokens") or 0) for row in sessions
        ]),
        "tool_calls": dict(sorted(tools.items())),
        "tool_result_errors": dict(sorted(errors.items())),
        "malformed_tool_call_records": malformed,
        "exact_repeated_tool_call_pairs": exact_repeat_pairs,
        "longest_exact_tool_call_run": longest_exact_run,
        "finish_reasons": dict(sorted(finish_reasons.items())),
        "turn_handoffs": {
            "count": len(handoff_word_counts),
            "word_counts": summarize(handoff_word_counts),
            "maximum_words_contract": TURN_HANDOFF_WORD_LIMIT,
            "all_within_contract": all(
                value <= TURN_HANDOFF_WORD_LIMIT for value in handoff_word_counts
            ),
        },
        "active_messages": sum(bool(row.get("active")) for row in messages),
        "compacted_messages": sum(bool(row.get("compacted")) for row in messages),
        "compression_fallbacks": sum(
            int(row.get("compression_fallback_streak") or 0) for row in sessions
        ),
        "compression_ineffective": sum(
            int(row.get("compression_ineffective_count") or 0) for row in sessions
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    payload = audit(arguments.database.expanduser().resolve())
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
