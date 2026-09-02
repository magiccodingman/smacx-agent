"""Fail-closed provider-facing system-prompt override for managed Hermes.

The derived image imports this module from an executable venv ``.pth`` line
before the Hermes console entry point. The official pinned runtime remains
responsible for conversations, tools, compression, and provider transport;
only prompt assembly is replaced.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re


_COMPLETED_THINK_BLOCK = re.compile(
    r"<think(?:\s[^>]*)?>.*?</think\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_UNFINISHED_THINK_BLOCK = re.compile(
    r"<think(?:\s[^>]*)?>.*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)
_HANDOFF_SECTION = re.compile(
    r"(?im)^\s*(?:[-#]\s*)?\*{0,2}"
    r"(Outcome|Reasoning|What changed|Next turn|Uncertainty)"
    r"\s*:?[\s*]*:?\s*"
)
_HANDOFF_SECTIONS = (
    "Outcome", "Reasoning", "What changed", "Next turn", "Uncertainty",
)
_HANDOFF_MAX_WORDS = 120
_HANDOFF_SECTION_WORDS = 19


def _without_historical_thinking(content: str) -> tuple[str, int]:
    """Remove serialized reasoning while retaining the assistant's final answer.

    Some OpenAI-compatible providers return reasoning separately, but Hermes
    persists it as a leading ``<think>`` block in assistant ``content``.  The
    provider's ``preserve_thinking`` chat-template flag is too late to prevent
    that already-serialized block from occupying the next request.  Only
    completed historical assistant turns pass through this function; the
    current assistant/tool chain remains untouched.
    """
    compacted, complete_count = _COMPLETED_THINK_BLOCK.subn("", content)
    compacted, unfinished_count = _UNFINISHED_THINK_BLOCK.subn("", compacted)
    if complete_count or unfinished_count:
        compacted = compacted.lstrip(" \t\r\n")
    return compacted, complete_count + unfinished_count


def _compact_turn_handoff(content: str) -> str:
    """Enforce the durable handoff ceiling without touching ordinary replies.

    The system prompt asks the model to target 80–90 words, but a probabilistic
    model can still overshoot. A turn handoff becomes historical context, so
    this final local guard retains all five semantic fields while preventing an
    unbounded narrative from defeating context control.
    """
    if not isinstance(content, str) or not content.lstrip().upper().startswith(
            "TURN HANDOFF") or len(content.split()) <= _HANDOFF_MAX_WORDS:
        return content
    matches = list(_HANDOFF_SECTION.finditer(content))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        sections[match.group(1).lower()] = content[match.end():end].strip()
    if len(sections) < len(_HANDOFF_SECTIONS):
        # Malformed but still bounded: distribute the model's own ordered body
        # rather than inventing strategic content.
        body = content[content.upper().find("TURN HANDOFF") + len("TURN HANDOFF"):]
        words = body.split()
        sections = {
            label.lower(): " ".join(words[index * _HANDOFF_SECTION_WORDS:
                                           (index + 1) * _HANDOFF_SECTION_WORDS])
            for index, label in enumerate(_HANDOFF_SECTIONS)
        }
    lines = ["TURN HANDOFF"]
    for label in _HANDOFF_SECTIONS:
        words = sections.get(label.lower(), "").split()
        value = " ".join(words[:_HANDOFF_SECTION_WORDS]).strip(" -*\n\t")
        lines.append(f"{label}: {value or 'Not specified.'}")
    return "\n".join(lines)


def _install() -> None:
    if os.environ.get("SMACX_STRICT_SYSTEM_PROMPT") != "1":
        return
    path_value = os.environ.get("SMACX_SYSTEM_PROMPT_FILE", "")
    expected = os.environ.get("SMACX_SYSTEM_PROMPT_SHA256", "")
    if not path_value or len(expected) != 64:
        raise RuntimeError("smacx_strict_prompt_configuration_missing")
    path = Path(path_value)

    def load() -> str:
        try:
            value = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("smacx_strict_prompt_unavailable") from exc
        actual = hashlib.sha256(value.encode("utf-8")).hexdigest()
        if actual != expected:
            raise RuntimeError("smacx_strict_prompt_integrity_failure")
        if not value.strip():
            raise RuntimeError("smacx_strict_prompt_empty")
        return value

    # Importing at interpreter startup ensures later ``from ... import`` sites
    # receive these functions rather than Hermes's additive prompt builder.
    import agent.system_prompt as system_prompt  # type: ignore

    def build_parts(agent, system_message=None):  # noqa: ANN001,ARG001
        return {"stable": load(), "context": "", "volatile": ""}

    def build(agent, system_message=None):  # noqa: ANN001,ARG001
        value = load()
        agent._cached_system_prompt_static = value
        return value

    system_prompt.build_system_prompt_parts = build_parts
    system_prompt.build_system_prompt = build

    # Hermes deliberately defaults reasoning echo off for unknown custom
    # providers. Managed Qwen profiles opt in through config, and this wire
    # policy keeps only the current genuine user episode's reasoning. It also
    # removes superseded, repetitive game-state payloads without altering the
    # durable Hermes transcript.
    import run_agent  # type: ignore

    original_sanitize = run_agent.AIAgent._sanitize_api_messages
    logger = logging.getLogger("smacx.context")

    def compact_managed_context(messages):  # noqa: ANN001
        sanitized = original_sanitize(messages)
        if not isinstance(sanitized, list):
            return sanitized
        last_user = max(
            (index for index, message in enumerate(sanitized)
             if isinstance(message, dict) and message.get("role") == "user"),
            default=-1,
        )
        compacted_reasoning = compacted_think_blocks = 0
        compacted_frames = compacted_boundaries = 0
        tool_names: dict[str, str] = {}
        state_rows: list[int] = []
        for index, message in enumerate(sanitized):
            if not isinstance(message, dict):
                continue
            if message.get("role") == "assistant":
                if index < last_user:
                    for field in ("reasoning", "reasoning_content", "reasoning_details"):
                        if field in message:
                            message.pop(field, None)
                            compacted_reasoning += 1
                    content = message.get("content")
                    if isinstance(content, str):
                        content, removed = _without_historical_thinking(content)
                        if removed:
                            message["content"] = content
                            compacted_think_blocks += removed
                for call in message.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function")
                    if isinstance(function, dict) and isinstance(call.get("id"), str):
                        tool_names[call["id"]] = str(function.get("name") or "")
            elif message.get("role") == "tool":
                name = tool_names.get(str(message.get("tool_call_id") or ""), "")
                if name in {
                    "smac_decision", "smac_choices", "smac_wait",
                    "smac_execute_choice", "smac_match_briefing",
                }:
                    state_rows.append(index)
            elif message.get("role") == "user":
                content = message.get("content")
                if index < last_user and isinstance(content, str) \
                        and content.startswith("[SMACX_EPISODE_BOUNDARY"):
                    message["content"] = "[Superseded managed gameplay episode boundary.]"
                    compacted_boundaries += 1
        for index in state_rows[:-1]:
            message = sanitized[index]
            message["content"] = json.dumps({
                "ok": True,
                "superseded_runtime_state": True,
                "instruction": "Use the newest decision/state tool result.",
            }, separators=(",", ":"))
            compacted_frames += 1
        if compacted_reasoning or compacted_think_blocks \
                or compacted_frames or compacted_boundaries:
            logger.debug(
                "SMACX request compaction reasoning_fields=%d think_blocks=%d "
                "state_frames=%d episode_boundaries=%d",
                compacted_reasoning, compacted_think_blocks,
                compacted_frames, compacted_boundaries,
            )
        return sanitized

    run_agent.AIAgent._sanitize_api_messages = staticmethod(compact_managed_context)

    # The prompt is the primary policy. These two post-processing hooks are a
    # narrow deterministic backstop for the one durable response type whose
    # size is part of the runtime contract. They affect neither tool-bearing
    # assistant messages nor ordinary terminal/operator responses.
    original_strip_think_blocks = run_agent.AIAgent._strip_think_blocks
    original_build_assistant_message = run_agent.AIAgent._build_assistant_message

    def bounded_strip_think_blocks(self, content):  # noqa: ANN001
        return _compact_turn_handoff(original_strip_think_blocks(self, content))

    def bounded_build_assistant_message(self, assistant_message, finish_reason):  # noqa: ANN001
        message = original_build_assistant_message(self, assistant_message, finish_reason)
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            message["content"] = _compact_turn_handoff(message["content"])
        return message

    run_agent.AIAgent._strip_think_blocks = bounded_strip_think_blocks
    run_agent.AIAgent._build_assistant_message = bounded_build_assistant_message


_install()
