#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("SMACX_MANAGED_ATTACHED", "1")
os.environ.setdefault("SMACX_AGENT_MATCH_ID", "match-managed-scope")
os.environ.setdefault("SMACX_AGENT_SESSION_ID", "session-managed-scope")
os.environ.setdefault("SMACX_AGENT_ID", "agent-managed-scope")
os.environ.setdefault("SMACX_PERSPECTIVE_ID", "perspective-managed-scope")
import smacx_mcp


def main() -> int:
    tools = asyncio.run(smacx_mcp.mcp.list_tools())
    names = {tool.name for tool in tools}
    forbidden = {
        "smac_command", "smac_launch", "smac_new_game", "smac_new_scenario",
        "smac_observe", "smac_snapshot", "smac_lan", "smac_saves", "smac_stop",
    }
    if names & forbidden:
        raise AssertionError(f"managed surface leaked tools: {sorted(names & forbidden)}")
    required = {
        "smac_decision", "smac_execute_choice", "smac_choices", "smac_world",
        "smac_attention_ack", "smac_cognition", "smac_specialist",
        "smac_memory", "smac_memory_update", "smac_notebook", "smac_reference",
        "smac_chat", "smac_group_chat", "smac_report_capability_gap",
    }
    if not required <= names:
        raise AssertionError(f"managed surface omitted tools: {sorted(required - names)}")
    if "smac_list" in names:
        raise AssertionError("legacy flat-list world surface leaked into managed provider tools")
    expected_scope = (
        "match-managed-scope", "session-managed-scope",
        "agent-managed-scope", "perspective-managed-scope",
    )
    if smacx_mcp._bound_scope_identity() != expected_scope:
        raise AssertionError("managed empty scope did not bind to immutable seat identity")
    try:
        smacx_mcp._bound_scope_identity(match_id="match-other")
    except ValueError as exc:
        if str(exc) != "managed_match_scope_mismatch":
            raise
    else:
        raise AssertionError("managed tool accepted a caller-nominated foreign scope")
    print(json.dumps({"event": "pass", "payload": {
        "tool_count": len(names), "opaque_executor_only": True,
        "lifecycle_hidden": True, "unbounded_snapshots_hidden": True,
        "immutable_managed_scope": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
