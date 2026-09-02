#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("SMACX_MANAGED_ATTACHED", "1")
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
        "smac_decision", "smac_execute_choice", "smac_list", "smac_choices",
        "smac_memory", "smac_memory_update", "smac_notebook", "smac_reference",
        "smac_chat", "smac_group_chat", "smac_report_capability_gap",
    }
    if not required <= names:
        raise AssertionError(f"managed surface omitted tools: {sorted(required - names)}")
    print(json.dumps({"event": "pass", "payload": {
        "tool_count": len(names), "opaque_executor_only": True,
        "lifecycle_hidden": True, "unbounded_snapshots_hidden": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
