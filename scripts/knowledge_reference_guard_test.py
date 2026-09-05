#!/usr/bin/env python3
"""Regression for rejecting session-local engine IDs from durable cognition."""

from __future__ import annotations

import json

import smacx_mcp


def main() -> int:
    calls: list[tuple[object, ...]] = []

    def fake_put(*arguments: object, **_keywords: object) -> dict[str, object]:
        calls.append(arguments)
        return {"ok": True, "written": True}

    original_scope = smacx_mcp._bound_scope_identity
    original_notebook = smacx_mcp.controller_campaign_notebook
    smacx_mcp._bound_scope_identity = lambda *args: (
        "match-test", "session-test", "agent-test", "perspective-test"
    )
    smacx_mcp.controller_campaign_notebook = fake_put
    try:
        rejected_values = [
            "Colony Pod (id 26) is near the frontier.",
            "unit_id 26 is currently ready.",
            "base 0 is producing a Former.",
            "prototype-id 74 is our defender.",
        ]
        for value in rejected_values:
            result = smacx_mcp.smac_notebook(
                action="put", match_id="match-test", session_id="session-test",
                observed_revision="revision-test", key="fact", content=value,
            )
            if result.get("error", {}).get("code") \
                    != "session_local_notebook_reference":
                raise AssertionError(f"ephemeral reference was accepted: {value!r}: {result}")
        if calls:
            raise AssertionError(f"rejected values reached storage: {calls}")

        accepted = smacx_mcp.smac_notebook(
            action="put", match_id="match-test", session_id="session-test",
            observed_revision="revision-test", key="faction_behavior", title="Treaty conduct",
            content="The Peacekeepers have honored our Treaty for twelve observed turns.",
        )
        if not accepted.get("ok") or len(calls) != 1:
            raise AssertionError(f"stable knowledge was not forwarded: {accepted}, {calls}")
    finally:
        smacx_mcp._bound_scope_identity = original_scope
        smacx_mcp.controller_campaign_notebook = original_notebook

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
