"""Fail-closed provider-facing system-prompt override for managed Hermes.

The derived image imports this module from an executable venv ``.pth`` line
before the Hermes console entry point. The official pinned runtime remains
responsible for conversations, tools, compression, and provider transport;
only prompt assembly is replaced.
"""

from __future__ import annotations

import hashlib
import copy
import json
import logging
import os
from pathlib import Path
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from smacx_context_policy import (
    HERMES_COMPRESSION_THRESHOLD_RATIO, hermes_compression_trigger_tokens,
    semantic_gc_ceiling_tokens,
)


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
    r"(Outcome|Rationale|Changed conclusions|Next intent|Uncertainty)"
    r"\s*:?[\s*]*:?\s*"
)
_HANDOFF_SECTIONS = (
    "Outcome", "Rationale", "Changed conclusions", "Next intent", "Uncertainty",
)
_HANDOFF_MAX_WORDS = 120
_HANDOFF_SECTION_WORDS = 19
_STATE_TOOL_NAMES = frozenset({
    "smac_decision", "smac_choices", "smac_wait",
    "smac_execute_choice", "smac_match_briefing", "smac_snapshot", "smac_observe",
})
_DISPOSABLE_TOOL_NAMES = frozenset({
    *_STATE_TOOL_NAMES, "smac_world", "smac_investigate", "smac_list",
    "smac_memory", "smac_memory_update", "smac_notebook",
})
_COGNITION_TOOL_NAMES = frozenset({"smac_memory_update", "smac_notebook"})
_SMACX_MCP_PREFIX = "mcp__smacx__"
_RUNTIME_OPEN = '<SMACX_RUNTIME_CONTEXT schema="smacx.runtime-context.v1">'
_RUNTIME_CLOSE = "</SMACX_RUNTIME_CONTEXT>"
_RUNTIME_STATE = threading.local()


def _runtime_token() -> str:
    path = Path(os.environ.get(
        "SMACX_RUNTIME_CONTEXT_TOKEN_FILE", "/run/secrets/runtime-context-token",
    ))
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError("smacx_runtime_context_token_unavailable") from exc
    if not value or len(value) > 4096 or "\x00" in value:
        raise RuntimeError("smacx_runtime_context_token_invalid")
    return value


def _episode_id(messages) -> str:  # noqa: ANN001
    last_user = next((
        (index, message.get("content", ""))
        for index, message in reversed(list(enumerate(messages)))
        if isinstance(message, dict) and message.get("role") == "user"
    ), (-1, ""))
    material = "\x1f".join((
        os.environ.get("SMACX_AGENT_MATCH_ID", ""),
        os.environ.get("SMACX_AGENT_ID", ""),
        str(last_user[0]), str(last_user[1]),
    ))
    return "episode-" + hashlib.sha256(material.encode()).hexdigest()[:32]


def _fetch_runtime_context(messages) -> tuple[dict, str]:  # noqa: ANN001
    url = os.environ.get("SMACX_RUNTIME_CONTEXT_URL", "")
    if not url:
        raise RuntimeError("smacx_runtime_context_url_missing")
    episode_id = _episode_id(messages)
    gc_metrics = getattr(_RUNTIME_STATE, "gc_metrics", {})
    query = urlencode({
        "episode_id": episode_id,
        "episode_mode": os.environ.get("SMACX_EPISODE_MODE", "gameplay"),
        "context_length": os.environ.get("SMACX_CONTEXT_LENGTH", "65536"),
        "request_tokens_before_gc": int(gc_metrics.get("before", 0)),
        "request_tokens_after_gc": int(gc_metrics.get("after", 0)),
        "semantic_gc_removed_rows": int(gc_metrics.get("removed_rows", 0)),
    })
    request = Request(url + "?" + query, headers={
        "Authorization": "Bearer " + _runtime_token(), "Accept": "application/json",
    })
    try:
        with urlopen(request, timeout=10) as response:
            value = json.loads(response.read(4_000_001))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("smacx_runtime_context_unavailable") from exc
    payload = value.get("runtime_context") if isinstance(value, dict) else None
    if not value.get("ok") or not isinstance(payload, dict):
        raise RuntimeError("smacx_runtime_context_invalid")
    expected = {
        "match_id": os.environ.get("SMACX_AGENT_MATCH_ID", ""),
        "perspective_id": os.environ.get("SMACX_PERSPECTIVE_ID", ""),
    }
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    if any(expected[key] and identity.get(key) != expected[key] for key in expected):
        raise RuntimeError("smacx_runtime_context_scope_mismatch")
    if payload.get("schema") != "smacx.runtime-context.v1" \
            or payload.get("episode", {}).get("episode_id") != episode_id:
        raise RuntimeError("smacx_runtime_context_contract_mismatch")
    return payload, episode_id


def _escape_untrusted_runtime_tags(value: str) -> str:
    return value.replace("<SMACX_RUNTIME_CONTEXT", "&lt;SMACX_RUNTIME_CONTEXT") \
        .replace("</SMACX_RUNTIME_CONTEXT>", "&lt;/SMACX_RUNTIME_CONTEXT&gt;")


def _append_runtime_context(messages):  # noqa: ANN001
    """Append exactly one trusted envelope to the latest eligible existing row."""
    payload, episode_id = _fetch_runtime_context(messages)
    tail = next((index for index in range(len(messages) - 1, -1, -1)
                 if isinstance(messages[index], dict)
                 and messages[index].get("role") in {"tool", "user"}), -1)
    if tail < 0:
        raise RuntimeError("smacx_runtime_context_tail_missing")
    for message in messages:
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            message["content"] = _escape_untrusted_runtime_tags(message["content"])
    envelope = _RUNTIME_OPEN + "\n" + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n" + _RUNTIME_CLOSE
    original = messages[tail].get("content")
    if not isinstance(original, str):
        original = json.dumps(original, ensure_ascii=False, separators=(",", ":"))
    messages[tail]["content"] = original + "\n\n" + envelope
    if not messages[tail]["content"].endswith(envelope):
        raise RuntimeError("smacx_runtime_context_terminal_validation_failed")
    lease_id = payload.get("attention", {}).get("attention_lease_id")
    _RUNTIME_STATE.attention_lease_id = lease_id if isinstance(lease_id, str) else ""
    _RUNTIME_STATE.episode_id = episode_id
    return messages


def _mark_runtime_responded() -> None:
    lease_id = getattr(_RUNTIME_STATE, "attention_lease_id", "")
    url = os.environ.get("SMACX_RUNTIME_CONTEXT_URL", "")
    if not lease_id or not url:
        return
    endpoint = url.rsplit("/runtime-context", 1)[0] + "/runtime-context/responded"
    body = json.dumps({"attention_lease_id": lease_id}, separators=(",", ":")).encode()
    request = Request(endpoint, data=body, method="POST", headers={
        "Authorization": "Bearer " + _runtime_token(), "Content-Type": "application/json",
    })
    try:
        with urlopen(request, timeout=5) as response:
            response.read(4096)
    except (HTTPError, URLError, TimeoutError, OSError):
        # Response commitment is retried/idempotent; never falsify attention acknowledgement.
        logging.getLogger("smacx.context").warning("attention response marker unavailable")


def _end_runtime_episode(*, committed: bool) -> None:
    episode_id = getattr(_RUNTIME_STATE, "episode_id", "")
    url = os.environ.get("SMACX_RUNTIME_CONTEXT_URL", "")
    if not episode_id or not url:
        return
    endpoint = url.rsplit("/runtime-context", 1)[0] + "/runtime-context/episode-ended"
    body = json.dumps({"episode_id": episode_id, "committed": committed},
                      separators=(",", ":")).encode()
    request = Request(endpoint, data=body, method="POST", headers={
        "Authorization": "Bearer " + _runtime_token(), "Content-Type": "application/json",
    })
    try:
        with urlopen(request, timeout=5) as response:
            response.read(4096)
        _RUNTIME_STATE.episode_id = ""
        _RUNTIME_STATE.attention_lease_id = ""
    except (HTTPError, URLError, TimeoutError, OSError):
        logging.getLogger("smacx.context").warning("sovereign episode release unavailable")


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


def _managed_tool_name(call: object) -> str:
    """Return the semantic SMACX name from direct or Hermes-dispatched calls.

    Hermes exposes MCP through one generic ``tool_call`` function. The actual
    operation is a nested ``name`` argument such as
    ``mcp__smacx__smac_decision``. Keeping direct-name support makes the hook
    tolerant of upstream transport changes, while requiring the namespace on
    dispatched calls prevents unrelated tools from being treated as game
    state merely because an untrusted argument resembles a SMACX operation.
    """
    if not isinstance(call, dict):
        return ""
    function = call.get("function")
    if not isinstance(function, dict):
        return ""
    outer_name = function.get("name")
    if not isinstance(outer_name, str):
        return ""
    if outer_name.startswith(_SMACX_MCP_PREFIX):
        return outer_name.removeprefix(_SMACX_MCP_PREFIX)
    if outer_name != "tool_call":
        return outer_name
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return ""
    if not isinstance(arguments, dict):
        return ""
    dispatched_name = arguments.get("name")
    if not isinstance(dispatched_name, str) \
            or not dispatched_name.startswith(_SMACX_MCP_PREFIX):
        return ""
    return dispatched_name.removeprefix(_SMACX_MCP_PREFIX)


def _managed_tool_arguments(call: object) -> dict | None:
    if not isinstance(call, dict):
        return None
    function = call.get("function")
    if not isinstance(function, dict) or function.get("name") != "tool_call":
        return None
    outer = function.get("arguments")
    if isinstance(outer, str):
        try:
            outer = json.loads(outer)
        except json.JSONDecodeError:
            return None
    if not isinstance(outer, dict) or not str(outer.get("name") or "").startswith(
            _SMACX_MCP_PREFIX):
        return None
    arguments = outer.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    return arguments if isinstance(arguments, dict) else None


def _replace_managed_tool_arguments(call: object, arguments: dict) -> None:
    if not isinstance(call, dict):
        return
    function = call.get("function")
    if not isinstance(function, dict) or function.get("name") != "tool_call":
        return
    outer = function.get("arguments")
    outer_was_text = isinstance(outer, str)
    if outer_was_text:
        try:
            outer = json.loads(outer)
        except json.JSONDecodeError:
            return
    if not isinstance(outer, dict):
        return
    outer["arguments"] = arguments
    function["arguments"] = json.dumps(
        outer, sort_keys=True, separators=(",", ":"),
    ) if outer_was_text else outer


def _request_tokens(messages) -> int:  # noqa: ANN001
    return max(1, (len(json.dumps(
        messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")) + 3) // 4)


def _semantic_ceiling(context_length: int) -> tuple[int, int]:
    """Return effective ceiling and cleanup target in estimated provider tokens."""
    configured_ratio = float(os.environ.get(
        "SMACX_HERMES_COMPRESSION_THRESHOLD_RATIO",
        str(HERMES_COMPRESSION_THRESHOLD_RATIO),
    ))
    if abs(configured_ratio - HERMES_COMPRESSION_THRESHOLD_RATIO) > 1e-9:
        raise RuntimeError("smacx_hermes_compression_policy_mismatch")
    output_reserve = int(os.environ.get("SMACX_OUTPUT_TOKEN_RESERVE", "8192"))
    reasoning_reserve = int(os.environ.get(
        "SMACX_REASONING_TOKEN_RESERVE",
        "8192" if context_length < 131072 else "32768",
    ))
    system_tool_reserve = int(os.environ.get("SMACX_SYSTEM_TOOL_TOKEN_RESERVE", "12000"))
    effective = semantic_gc_ceiling_tokens(
        context_length, output_reserve=output_reserve,
        reasoning_reserve=reasoning_reserve,
        system_tool_reserve=system_tool_reserve,
    )
    return effective, max(4096, int(effective * 0.70))


def _collect_old_disposable_pairs(messages, tool_names, *, keep: int = 24):  # noqa: ANN001
    """Return indices for old complete SMACX assistant/tool protocol pairs.

    Pair removal occurs only after Hermes sanitization and always removes both
    sides, preserving provider sequencing. Recent pairs retain current-episode
    reasoning continuity; authoritative mechanical outcomes remain in the
    journal/world model and current runtime context.
    """
    tool_row_by_id = {
        str(message.get("tool_call_id")): index
        for index, message in enumerate(messages)
        if isinstance(message, dict) and message.get("role") == "tool"
    }
    groups: list[set[int]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        calls = message.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            continue
        ids = [str(call.get("id") or "") for call in calls if isinstance(call, dict)]
        if not ids or any(call_id not in tool_row_by_id for call_id in ids):
            continue
        if any(tool_names.get(call_id) not in _DISPOSABLE_TOOL_NAMES for call_id in ids):
            continue
        groups.append({index, *(tool_row_by_id[call_id] for call_id in ids)})
    removable = groups[:-keep] if len(groups) > keep else []
    return set().union(*removable) if removable else set()


def _install() -> None:
    sovereign_mode = os.environ.get("SMACX_STRICT_SYSTEM_PROMPT") == "1"
    specialist_mode = os.environ.get("SMACX_SPECIALIST_STRICT_PROMPT") == "1"
    if not sovereign_mode and not specialist_mode:
        return
    if sovereign_mode and specialist_mode:
        raise RuntimeError("smacx_prompt_mode_conflict")
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

    # Specialists use the same Hermes runtime but must never receive the
    # sovereign request-tail context, attention lease, or semantic-GC hooks.
    # Their system prompt is still replaced fail-closed by the hash above.
    if specialist_mode:
        return

    # Hermes deliberately defaults reasoning echo off for unknown custom
    # providers. Managed Qwen profiles opt in through config, and this wire
    # policy keeps only the current genuine user episode's reasoning. It also
    # removes superseded, repetitive game-state payloads without altering the
    # durable Hermes transcript.
    import run_agent  # type: ignore

    original_sanitize = run_agent.AIAgent._sanitize_api_messages
    logger = logging.getLogger("smacx.context")

    def compact_managed_context(messages):  # noqa: ANN001
        # Hermes's sanitizer may return a shallow list whose message mappings
        # are still the durable transcript objects. All semantic GC and trusted
        # runtime augmentation are provider-wire transformations only.
        sanitized = original_sanitize(copy.deepcopy(messages))
        if not isinstance(sanitized, list):
            return sanitized
        last_user = max(
            (index for index, message in enumerate(sanitized)
             if isinstance(message, dict) and message.get("role") == "user"),
            default=-1,
        )
        compacted_reasoning = compacted_think_blocks = 0
        compacted_frames = compacted_boundaries = 0
        pruned_tool_calls = pruned_tool_results = 0
        tool_names: dict[str, str] = {}
        tool_signatures: dict[str, str] = {}
        tool_calls_by_id: dict[str, dict] = {}
        historical_tool_call_ids: set[str] = set()
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
                    if isinstance(call.get("id"), str):
                        tool_names[call["id"]] = _managed_tool_name(call)
                        tool_calls_by_id[call["id"]] = call
                        function = call.get("function") if isinstance(call.get("function"), dict) else {}
                        tool_signatures[call["id"]] = json.dumps({
                            "name": tool_names[call["id"]],
                            "arguments": function.get("arguments"),
                        }, sort_keys=True, separators=(",", ":"), default=str)
                        if index < last_user:
                            historical_tool_call_ids.add(call["id"])
            elif message.get("role") == "tool":
                name = tool_names.get(str(message.get("tool_call_id") or ""), "")
                if name in _STATE_TOOL_NAMES:
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
        # Query evidence is retained within the current episode until it is
        # actually superseded. Only earlier identical world/reference queries
        # are collapsed; provider-valid assistant/tool pairing remains intact.
        newest_query: dict[str, int] = {}
        for index, message in enumerate(sanitized):
            if not isinstance(message, dict) or message.get("role") != "tool":
                continue
            call_id = str(message.get("tool_call_id") or "")
            if tool_names.get(call_id) not in {"smac_world", "smac_investigate"}:
                continue
            signature = tool_signatures.get(call_id, call_id)
            prior = newest_query.get(signature)
            if prior is not None:
                sanitized[prior]["content"] = json.dumps({
                    "ok": True, "semantic_gc": "superseded_query_evidence",
                    "retention": "Use the later identical query result.",
                }, separators=(",", ":"))
                compacted_frames += 1
            newest_query[signature] = index
        # A managed user boundary is emitted only after the prior native-turn
        # episode has yielded its durable TURN HANDOFF. Retain that ordinary
        # assistant summary, but remove the completed protocol pairs that led
        # to it. Otherwise every opaque choice and full JSON result is replayed
        # forever even though the journal and handoff already preserve the
        # durable outcome. Current-episode pairs remain untouched so provider
        # tool-call ordering stays valid while the turn is in progress.
        filtered = []
        for index, message in enumerate(sanitized):
            if index < last_user and isinstance(message, dict):
                if message.get("role") == "assistant" and message.get("tool_calls"):
                    pruned_tool_calls += len(message.get("tool_calls") or [])
                    continue
                if message.get("role") == "tool" and str(
                        message.get("tool_call_id") or "") in historical_tool_call_ids:
                    pruned_tool_results += 1
                    continue
            filtered.append(message)
        if compacted_reasoning or compacted_think_blocks \
                or compacted_frames or compacted_boundaries \
                or pruned_tool_calls or pruned_tool_results:
            logger.debug(
                "SMACX request compaction reasoning_fields=%d think_blocks=%d "
                "state_frames=%d episode_boundaries=%d tool_calls=%d tool_results=%d",
                compacted_reasoning, compacted_think_blocks,
                compacted_frames, compacted_boundaries,
                pruned_tool_calls, pruned_tool_results,
            )
        context_length = int(os.environ.get("SMACX_CONTEXT_LENGTH", "65536"))
        semantic_ceiling, cleanup_target = _semantic_ceiling(context_length)
        predicted_tokens = _request_tokens(filtered)
        if predicted_tokens > semantic_ceiling:
            # Successful durable cognition writes need not replay their full
            # arguments/results during one pathological long turn.  Compact
            # older pairs to journal receipts before considering pair eviction.
            cognition_rows = [
                message for message in filtered
                if isinstance(message, dict) and message.get("role") == "tool"
                and tool_names.get(str(message.get("tool_call_id") or ""))
                in _COGNITION_TOOL_NAMES
            ]
            for message in cognition_rows[:-4]:
                call_id = str(message.get("tool_call_id") or "")
                call = tool_calls_by_id.get(call_id)
                arguments = _managed_tool_arguments(call) if call else None
                tool_name = tool_names.get(call_id)
                is_durable_write = tool_name == "smac_memory_update" \
                    or (tool_name == "smac_notebook"
                        and isinstance(arguments, dict)
                        and arguments.get("action") in {"put", "delete"})
                if not is_durable_write:
                    continue
                try:
                    result = json.loads(str(message.get("content") or "{}"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(result, dict) or result.get("ok") is not True:
                    continue
                message["content"] = json.dumps({
                    "ok": True,
                    "semantic_gc": "durable_cognition_receipt",
                    "tool": tool_name,
                    "journal_event_id": result.get("journal_event_id"),
                    "retention": "Durably committed; use runtime cognition or targeted recall.",
                }, separators=(",", ":"))
                if call and isinstance(arguments, dict):
                    retained = {key: arguments.get(key) for key in (
                        "action", "match_id", "collection", "key",
                    ) if arguments.get(key) not in (None, "")}
                    retained["semantic_gc_receipt"] = True
                    _replace_managed_tool_arguments(call, retained)
            # Emergency semantic trimming still preserves the current focus,
            # anchor, attention, and cognition because those arrive after GC
            # in the trusted runtime envelope.
            query_tool_rows = [
                message for message in filtered
                if isinstance(message, dict) and message.get("role") == "tool"
                and tool_names.get(str(message.get("tool_call_id") or ""))
                in {"smac_world", "smac_investigate"}
            ]
            for message in query_tool_rows[:-1]:
                message["content"] = json.dumps({
                    "ok": True, "semantic_gc": "context_pressure_query_eviction",
                    "retention": "Requery only if current consequential work still needs it.",
                }, separators=(",", ":"))
            removed_indices = _collect_old_disposable_pairs(filtered, tool_names)
            removed_row_count = len(removed_indices)
            if removed_indices:
                filtered = [message for index, message in enumerate(filtered)
                            if index not in removed_indices]
            # If one pass remains above target, retain only the newest eight
            # complete disposable pairs. Pinned current truth is injected after
            # this cleanup and can never be evicted here.
            if _request_tokens(filtered) > cleanup_target:
                removed_indices = _collect_old_disposable_pairs(filtered, tool_names, keep=8)
                removed_row_count += len(removed_indices)
                if removed_indices:
                    filtered = [message for index, message in enumerate(filtered)
                                if index not in removed_indices]
            after_tokens = _request_tokens(filtered)
            logger.warning(
                "SMACX semantic GC pressure predicted_tokens=%d ceiling_tokens=%d "
                "target_tokens=%d after_tokens=%d",
                predicted_tokens, semantic_ceiling, cleanup_target, after_tokens,
            )
            if after_tokens > semantic_ceiling:
                raise RuntimeError("context_budget_exhausted:durable_provider_history")
        else:
            after_tokens = predicted_tokens
            removed_row_count = 0
        _RUNTIME_STATE.gc_metrics = {
            "before": predicted_tokens, "after": after_tokens,
            "removed_rows": removed_row_count,
        }
        return _append_runtime_context(filtered)

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
        _mark_runtime_responded()
        if isinstance(message, dict) and not message.get("tool_calls") \
                and finish_reason in {"stop", "end_turn"}:
            _end_runtime_episode(committed=True)
        return message

    run_agent.AIAgent._strip_think_blocks = bounded_strip_think_blocks
    run_agent.AIAgent._build_assistant_message = bounded_build_assistant_message


_install()
