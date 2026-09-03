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
        "smac_attention_ack", "smac_cognition", "smac_investigate",
        "smac_memory", "smac_memory_update", "smac_notebook",
        "smac_chat", "smac_group_chat", "smac_report_capability_gap",
    }
    if not required <= names:
        raise AssertionError(f"managed surface omitted tools: {sorted(required - names)}")
    if "smac_list" in names:
        raise AssertionError("legacy flat-list world surface leaked into managed provider tools")
    retired = {"smac_reference", "smac_specialist", "smac_knowledge"}
    if names & retired:
        raise AssertionError(f"retired specialist instruments leaked: {sorted(names & retired)}")
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
    chat_calls: list[dict] = []
    memory_calls: list[dict] = []
    original_chat = smacx_mcp.controller_semantic_chat
    original_memory = smacx_mcp.read_platform_memory
    smacx_mcp.controller_semantic_chat = lambda *_args, **kwargs: (
        chat_calls.append(kwargs) or {"ok": True, "items": []}
    )
    smacx_mcp.read_platform_memory = lambda *_args, **kwargs: (
        memory_calls.append(kwargs) or {"ok": True, "items": []}
    )
    try:
        assert smacx_mcp.smac_chat(action="list", acknowledge=True).get("ok")
        assert smacx_mcp.smac_memory(
            action="chat", match_id="", acknowledge=True,
        ).get("ok")
    finally:
        smacx_mcp.controller_semantic_chat = original_chat
        smacx_mcp.read_platform_memory = original_memory
    if chat_calls[0].get("acknowledge") is not False \
            or memory_calls[0].get("acknowledge") is not False:
        raise AssertionError("managed chat read bypassed attention acknowledgement")
    print(json.dumps({"event": "pass", "payload": {
        "tool_count": len(names), "opaque_executor_only": True,
        "lifecycle_hidden": True, "unbounded_snapshots_hidden": True,
        "immutable_managed_scope": True,
        "managed_chat_acknowledgement_authority": "smac_attention_ack_only",
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
