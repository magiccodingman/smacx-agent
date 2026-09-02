#!/usr/bin/env python3
"""Run inside the managed Hermes image to verify request-wire context policy."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile


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
             "content": "<think>serialized old thought</think>\nPrior final answer.",
             "reasoning_content": "old thought",
             "tool_calls": [{"id": "old", "type": "function",
                              "function": {"name": "smac_decision", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "old", "content": "{\"old_state\":true}"},
            {"role": "user", "content": "[SMACX_EPISODE_BOUNDARY kind=resume] continue"},
            {"role": "assistant",
             "content": "<think>serialized current thought</think>",
             "reasoning_content": "current thought",
             "tool_calls": [{"id": "new", "type": "function",
                              "function": {"name": "smac_decision", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "new", "content": "{\"current_state\":true}"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "execute", "type": "function", "function": {
                    "name": "smac_execute_choice", "arguments": "{}",
                }},
            ]},
            {"role": "tool", "tool_call_id": "execute", "content": "{\"executed\":true}"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "latest", "type": "function", "function": {
                    "name": "smac_decision", "arguments": "{}",
                }},
            ]},
            {"role": "tool", "tool_call_id": "latest", "content": "{\"latest_state\":true}"},
        ]
        wire = AIAgent._sanitize_api_messages(messages)
        if "reasoning_content" in wire[1] or wire[4].get("reasoning_content") != "current thought":
            raise AssertionError("current-episode reasoning continuity is incorrect")
        if wire[1].get("content") != "Prior final answer." \
                or "serialized current thought" not in wire[4].get("content", ""):
            raise AssertionError("serialized current-episode thinking policy is incorrect")
        if "superseded_runtime_state" not in wire[2]["content"] \
                or "superseded_runtime_state" not in wire[5]["content"] \
                or "superseded_runtime_state" not in wire[7]["content"] \
                or "latest_state" not in wire[9]["content"]:
            raise AssertionError("state-frame compaction is incorrect")
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
            "current_tool_chain_reasoning_retained": True,
            "superseded_state_compacted": True,
            "superseded_execution_result_compacted": True,
            "latest_state_retained": True,
            "turn_handoff_hard_ceiling": True,
            "ordinary_response_untouched": True,
            "streaming_repetition_fuse_installed": True,
        }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
