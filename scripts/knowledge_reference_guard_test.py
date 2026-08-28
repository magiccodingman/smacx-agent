#!/usr/bin/env python3
"""Regression for rejecting session-local engine IDs from durable knowledge."""

from __future__ import annotations

import json

import smacx_mcp


def main() -> int:
    calls: list[tuple[object, ...]] = []

    def fake_put(*arguments: object, **_keywords: object) -> dict[str, object]:
        calls.append(arguments)
        return {"ok": True, "written": True}

    original = smacx_mcp.put_match_knowledge
    smacx_mcp.put_match_knowledge = fake_put
    try:
        rejected_values = [
            "Colony Pod (id 26) is near the frontier.",
            "unit_id 26 is currently ready.",
            "base 0 is producing a Former.",
            "prototype-id 74 is our defender.",
        ]
        for value in rejected_values:
            result = smacx_mcp.smac_knowledge(
                action="put", match_id="match-test", session_id="session-test",
                observed_revision="revision-test", key="fact", value=value,
            )
            if result.get("error", {}).get("code") \
                    != "session_local_knowledge_reference":
                raise AssertionError(f"ephemeral reference was accepted: {value!r}: {result}")
        if calls:
            raise AssertionError(f"rejected values reached storage: {calls}")

        accepted = smacx_mcp.smac_knowledge(
            action="put", match_id="match-test", session_id="session-test",
            observed_revision="revision-test", key="faction_behavior",
            value="The Peacekeepers have honored our Treaty for twelve observed turns.",
        )
        if not accepted.get("ok") or len(calls) != 1:
            raise AssertionError(f"stable knowledge was not forwarded: {accepted}, {calls}")
    finally:
        smacx_mcp.put_match_knowledge = original

    print(json.dumps({
        "event": "pass",
        "payload": {
            "ephemeral_references_rejected": 4,
            "rejected_writes_reached_storage": False,
            "stable_named_fact_accepted": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
