#!/usr/bin/env python3
"""Run inside the managed Hermes image to verify request-wire context policy."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile


def dispatched_call(identifier: str, name: str) -> dict:
    """Mirror Hermes's real generic MCP dispatcher envelope."""
    return {
        "id": identifier,
        "type": "function",
        "function": {
            "name": "tool_call",
            "arguments": json.dumps({
                "name": f"mcp__smacx__{name}",
                "arguments": {},
            }, separators=(",", ":")),
        },
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-context-") as temporary:
        prompt = Path(temporary) / "SYSTEM.md"
        prompt.write_text("managed test prompt\n", encoding="utf-8")
        os.environ.update({
            "SMACX_STRICT_SYSTEM_PROMPT": "1",
            "SMACX_SYSTEM_PROMPT_FILE": str(prompt),
            "SMACX_SYSTEM_PROMPT_SHA256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
        })
        import smacx_strict_prompt
        importlib.reload(smacx_strict_prompt)
        from run_agent import AIAgent

        messages = [
            {"role": "user", "content": "old episode"},
            {"role": "assistant",
             "content": "<think>serialized old tool thought</think>",
             "reasoning_content": "old thought",
             "tool_calls": [dispatched_call("old", "smac_decision")]},
            {"role": "tool", "tool_call_id": "old", "content": "{\"old_state\":true}"},
            {"role": "assistant",
             "content": "<think>serialized old final thought</think>\nPrior final answer.",
             "reasoning_content": "old final thought"},
            {"role": "user", "content": "[SMACX_EPISODE_BOUNDARY kind=resume] continue"},
            {"role": "assistant",
             "content": "<think>serialized current thought</think>",
             "reasoning_content": "current thought",
             "tool_calls": [dispatched_call("new", "smac_decision")]},
            {"role": "tool", "tool_call_id": "new", "content": "{\"current_state\":true}"},
            {"role": "assistant", "content": "", "tool_calls": [
                dispatched_call("execute", "smac_execute_choice"),
            ]},
            {"role": "tool", "tool_call_id": "execute", "content": "{\"executed\":true}"},
            {"role": "assistant", "content": "", "tool_calls": [
                dispatched_call("latest", "smac_decision"),
            ]},
            {"role": "tool", "tool_call_id": "latest", "content": "{\"latest_state\":true}"},
        ]
        wire = AIAgent._sanitize_api_messages(messages)
        by_tool_call_id = {
            str(item.get("tool_call_id")): item for item in wire
            if isinstance(item, dict) and item.get("role") == "tool"
        }
        current_reasoning = next(
            item for item in wire
            if isinstance(item, dict) and item.get("reasoning_content") == "current thought"
        )
        historical_final = next(
            item for item in wire
            if isinstance(item, dict) and item.get("content") == "Prior final answer."
        )
        if "reasoning_content" in historical_final \
                or current_reasoning.get("reasoning_content") != "current thought":
            raise AssertionError("current-episode reasoning continuity is incorrect")
        if "serialized current thought" not in current_reasoning.get("content", ""):
            raise AssertionError("serialized current-episode thinking policy is incorrect")
        if any(item.get("tool_call_id") == "old" for item in wire) \
                or any(any(call.get("id") == "old" for call in item.get("tool_calls") or [])
                       for item in wire if isinstance(item, dict)):
            raise AssertionError("completed historical tool protocol was retained")
        if "superseded_runtime_state" not in by_tool_call_id["new"]["content"] \
                or "superseded_runtime_state" not in by_tool_call_id["execute"]["content"] \
                or "latest_state" not in by_tool_call_id["latest"]["content"]:
            raise AssertionError("state-frame compaction is incorrect")
        # A realistic multi-turn transcript must stay bounded on the provider
        # wire even though Hermes preserves the full durable history in SQLite.
        long_history = [{"role": "user", "content": "opening episode"}]
        for index in range(48):
            identifier = f"state-{index}"
            long_history.extend((
                {"role": "assistant", "content": "", "tool_calls": [
                    dispatched_call(identifier, "smac_decision"),
                ]},
                {"role": "tool", "tool_call_id": identifier,
                 "content": json.dumps({"state": "x" * 8192})},
            ))
        bounded_history = AIAgent._sanitize_api_messages(long_history)
        encoded_history = json.dumps(
            bounded_history, separators=(",", ":"), ensure_ascii=False,
        )
        if len(encoded_history) >= 64_000:
            raise AssertionError("real Hermes wrapper history exceeded the wire ceiling")
        if sum("superseded_runtime_state" in str(item.get("content", ""))
               for item in bounded_history) != 47:
            raise AssertionError("not every superseded wrapped state was compacted")
        if "x" * 8192 not in bounded_history[-1]["content"]:
            raise AssertionError("latest wrapped state was not preserved")
        untrusted_dispatch = dispatched_call("foreign", "smac_decision")
        untrusted_dispatch["function"]["arguments"] = json.dumps({
            "name": "smac_decision", "arguments": {},
        })
        if smacx_strict_prompt._managed_tool_name(untrusted_dispatch):
            raise AssertionError("unscoped dispatcher argument was trusted")
        oversized = "TURN HANDOFF\n" + "\n".join(
            f"{label}: " + " ".join(f"{label.replace(' ', '_')}{index}" for index in range(35))
            for label in ("Outcome", "Reasoning", "What changed", "Next turn", "Uncertainty")
        )
        bounded = smacx_strict_prompt._compact_turn_handoff(oversized)
        if len(bounded.split()) > 120 or any(
                f"{label}:" not in bounded for label in
                ("Outcome", "Reasoning", "What changed", "Next turn", "Uncertainty")):
            raise AssertionError("turn handoff deterministic ceiling is incorrect")
        ordinary = "This ordinary operator response must remain untouched."
        if smacx_strict_prompt._compact_turn_handoff(ordinary) != ordinary:
            raise AssertionError("ordinary assistant response was modified")
        helper = Path("/opt/hermes/agent/chat_completion_helpers.py").read_text(encoding="utf-8")
        if "SMACX streaming repetition fuse" not in helper:
            raise AssertionError("early streaming repetition fuse is absent")
        print(json.dumps({"event": "pass", "payload": {
            "historical_reasoning_removed": True,
            "historical_serialized_thinking_removed": True,
            "historical_tool_protocol_pruned": True,
            "current_tool_chain_reasoning_retained": True,
            "superseded_state_compacted": True,
            "superseded_execution_result_compacted": True,
            "latest_state_retained": True,
            "real_hermes_dispatcher_compacted": True,
            "provider_wire_growth_bounded": True,
            "unscoped_dispatcher_rejected": True,
            "turn_handoff_hard_ceiling": True,
            "ordinary_response_untouched": True,
            "streaming_repetition_fuse_installed": True,
        }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
