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
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from smacx_context_policy import (
    HERMES_COMPRESSION_THRESHOLD_RATIO, hermes_compression_trigger_tokens,
    semantic_gc_ceiling_tokens,
    validate_managed_context,
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
    "smac_decision", "smac_wait", "smac_snapshot", "smac_observe",
})
_QUERY_TOOL_NAMES = frozenset({"smac_choices", "smac_world", "smac_investigate"})
_DISPOSABLE_TOOL_NAMES = frozenset({
    *_STATE_TOOL_NAMES, *_QUERY_TOOL_NAMES, "smac_execute_choice", "smac_match_briefing", "smac_list",
    "smac_memory", "smac_memory_update", "smac_notebook",
})
_COGNITION_TOOL_NAMES = frozenset({"smac_memory_update", "smac_notebook"})
_SMACX_MCP_PREFIX = "mcp__smacx__"
_SMACX_MCP_PREFIXES = (_SMACX_MCP_PREFIX, "mcp__smacx_communication__")
_RUNTIME_OPEN = '<SMACX_RUNTIME_CONTEXT schema="smacx.runtime-context.v1">'
_RUNTIME_CLOSE = "</SMACX_RUNTIME_CONTEXT>"
_RUNTIME_STATE = threading.local()
# Fresh native collection can take ~24 seconds on the validated Huge fixture;
# the server retries up to three rejected revision cuts before assembling.
# Keep the original provider history while waiting for that bounded operation.
# Exhaustion still fails closed; no cached context or new episode is substituted.
_RUNTIME_CONTEXT_TIMEOUT_SECONDS = 120


class _AuthorityHeartbeat:
    """Private episode liveness, independent of provider latency and progress."""
    def __init__(self, receipt):
        self.receipt = dict(receipt)
        self.stopped = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(target=self._run, name="smacx-authority-heartbeat", daemon=True)

    def beat(self):
        endpoint = os.environ["SMACX_RUNTIME_CONTEXT_URL"].rsplit("/runtime-context", 1)[0] + "/runtime-context/heartbeat"
        body = {**self.receipt, "run_id": os.environ.get("SMACX_HARNESS_RUN_ID", ""),
                "session_id": os.environ.get("SMACX_AGENT_SESSION_ID", "")}
        request = Request(endpoint, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": "Bearer " + _runtime_token(), "Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            if not json.loads(response.read(4096)).get("ok"):
                raise RuntimeError("sovereign_episode_authority_lost")

    def _run(self):
        misses = 0
        while not self.stopped.wait(30):
            try:
                self.beat()
                misses = 0
            except HTTPError:
                self.lost.set()
                return
            except (URLError, TimeoutError, OSError, ValueError, RuntimeError):
                misses += 1
                if misses >= 3:
                    self.lost.set()
                    return


def _authority_handoff():
    from smacx_diagnostics import record
    record("sovereign_authority_handoff", {"reason": "episode_authority_lost",
           "automatic_action_retry": False}, actor="sovereign")
    _end_runtime_episode(committed=False)
    # Clean CLI termination invokes the supervisor's existing fresh-state
    # admission and bounded no-progress-yield policy, not a model-written retry.
    raise SystemExit(0)


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
        # Tool-pair GC changes absolute row indices within one invocation.
        # User-boundary ordinal remains stable under that wire-only cleanup.
        str(sum(isinstance(message, dict) and message.get("role") == "user"
                for message in messages)), str(last_user[1]),
    ))
    return "episode-" + hashlib.sha256(material.encode()).hexdigest()[:32]


def _fetch_runtime_context(messages) -> tuple[dict, str]:  # noqa: ANN001
    heartbeat = getattr(_RUNTIME_STATE, "heartbeat", None)
    if heartbeat and heartbeat.lost.is_set():
        _authority_handoff()
    url = os.environ.get("SMACX_RUNTIME_CONTEXT_URL", "")
    if not url:
        raise RuntimeError("smacx_runtime_context_url_missing")
    episode_id = _episode_id(messages)
    gc_metrics = getattr(_RUNTIME_STATE, "gc_metrics", {})
    query = urlencode({
        "episode_id": episode_id,
        "episode_mode": os.environ.get("SMACX_EPISODE_MODE", "gameplay"),
        "run_id": os.environ.get("SMACX_HARNESS_RUN_ID", ""),
        "session_id": os.environ.get("SMACX_AGENT_SESSION_ID", ""),
        "context_length": os.environ.get("SMACX_CONTEXT_LENGTH", "65536"),
        "request_tokens_before_gc": int(gc_metrics.get("before", 0)),
        "request_tokens_after_gc": int(gc_metrics.get("after", 0)),
        "semantic_gc_removed_rows": int(gc_metrics.get("removed_rows", 0)),
    })
    request = Request(url + "?" + query, headers={
        "Authorization": "Bearer " + _runtime_token(), "Accept": "application/json",
    })
    started = time.monotonic()
    try:
        with urlopen(request, timeout=_RUNTIME_CONTEXT_TIMEOUT_SECONDS) as response:
            value = json.loads(response.read(4_000_001))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        if isinstance(exc, HTTPError) and exc.code == 409:
            try:
                error = str(json.loads(exc.read(4096)).get("error", ""))
            except (ValueError, OSError):
                error = ""
            if error.startswith("sovereign_episode_"):
                _authority_handoff()
        # This failure happens before provider submission, so the HTTPX
        # provider hook cannot observe it. Never include the private endpoint,
        # authorization header or an exception message that might echo them.
        try:
            from smacx_diagnostics import record
            timed_out = isinstance(exc, TimeoutError) or (
                isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError))
            record("runtime_context_fetch_failed", {
                "error": {"code": "runtime_context_timeout" if timed_out
                          else "runtime_context_http_error" if isinstance(exc, HTTPError)
                          else "runtime_context_fetch_error"},
                "exception_type": type(exc).__name__,
                "http_status": exc.code if isinstance(exc, HTTPError) else None,
                "elapsed_ms": (time.monotonic() - started) * 1000,
                "timeout_seconds": _RUNTIME_CONTEXT_TIMEOUT_SECONDS,
                "provider_context_issued": False,
            }, actor="sovereign", correlation={"episode_id": episode_id})
        except Exception:
            logging.getLogger(__name__).warning("Runtime-context failure capture unavailable")
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
    receipt = value.get("authority_heartbeat")
    if os.environ.get("SMACX_HARNESS_RUN_ID"):
        if not isinstance(receipt, dict) or receipt.get("episode_id") != episode_id or not receipt.get("token"):
            raise RuntimeError("smacx_authority_heartbeat_receipt_missing")
        if not heartbeat or heartbeat.receipt != receipt:
            if heartbeat:
                heartbeat.stopped.set()
            heartbeat = _AuthorityHeartbeat(receipt)
            _RUNTIME_STATE.heartbeat = heartbeat
            heartbeat.thread.start()
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
    heartbeat = getattr(_RUNTIME_STATE, "heartbeat", None)
    if heartbeat:
        heartbeat.stopped.set()
        _RUNTIME_STATE.heartbeat = None
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

    Current profiles expose direct schemas. Historical Hermes dispatch calls
    still contain a nested namespaced operation. Both gameplay and the
    restricted communication catalog use the same semantic GC policy.
    """
    if not isinstance(call, dict):
        return ""
    function = call.get("function")
    if not isinstance(function, dict):
        return ""
    outer_name = function.get("name")
    if not isinstance(outer_name, str):
        return ""
    for prefix in _SMACX_MCP_PREFIXES:
        if outer_name.startswith(prefix):
            return outer_name.removeprefix(prefix)
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
    if isinstance(dispatched_name, str):
        for prefix in _SMACX_MCP_PREFIXES:
            if dispatched_name.startswith(prefix):
                return dispatched_name.removeprefix(prefix)
    return ""


def _managed_tool_arguments(call: object) -> dict | None:
    if not isinstance(call, dict):
        return None
    function = call.get("function")
    if not isinstance(function, dict):
        return None
    outer = function.get("arguments")
    if isinstance(outer, str):
        try:
            outer = json.loads(outer)
        except json.JSONDecodeError:
            return None
    if str(function.get("name") or "").startswith(_SMACX_MCP_PREFIXES):
        return outer if isinstance(outer, dict) else None
    if function.get("name") != "tool_call" or not isinstance(outer, dict) \
            or not str(outer.get("name") or "").startswith(_SMACX_MCP_PREFIXES):
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
    if not isinstance(function, dict):
        return
    if str(function.get("name") or "").startswith(_SMACX_MCP_PREFIXES):
        function["arguments"] = json.dumps(arguments, sort_keys=True, separators=(",", ":")) \
            if isinstance(function.get("arguments"), str) else arguments
        return
    if function.get("name") != "tool_call":
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


def _managed_tool_result(content: object) -> dict | None:
    """Decode direct or Hermes-wrapped MCP JSON for wire-only compaction."""
    if not isinstance(content, str):
        return content if isinstance(content, dict) else None
    candidates = [content]
    if "<untrusted_tool_result" in content and "</untrusted_tool_result>" in content:
        body = content.split(">", 1)[-1].rsplit("</untrusted_tool_result>", 1)[0]
        candidates.extend(reversed(body.split("\n\n")))
    for candidate in candidates:
        try:
            value = json.loads(candidate.strip())
        except (json.JSONDecodeError, AttributeError):
            continue
        for _ in range(2):
            if not isinstance(value, dict) or not isinstance(value.get("result"), str):
                break
            try:
                value = json.loads(value["result"])
            except json.JSONDecodeError:
                break
        if isinstance(value, dict):
            return value
    return None


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
        results = [_managed_tool_result(messages[tool_row_by_id[call_id]].get("content")) for call_id in ids]
        if any(not isinstance(result, dict) or result.get("ok") is not True
               or any(result.get(k) for k in ("queued", "persistent", "order", "incident", "gameplay_mutations_blocked"))
               for result in results):
            continue
        if message.get("content"):
            continue  # Prose may hold the only record of strategic intent.
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
        # Validate while building a request, after the override is installed.
        # Python's .pth loader swallows startup exceptions; validating there
        # could leave Hermes's additive builder active after a budget failure.
        if sovereign_mode:
            reserve = validate_managed_context(value, int(os.environ.get("SMACX_CONTEXT_LENGTH", "65536")))
            os.environ["SMACX_SYSTEM_TOOL_TOKEN_RESERVE"] = str(max(
                reserve, int(os.environ.get("SMACX_SYSTEM_TOOL_TOKEN_RESERVE", "12000")),
            ))
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

    def canonical_system(messages):  # noqa: ANN001
        # Resume can reuse Hermes's persisted system prompt without invoking
        # its builder. Enforce the approved, hash-validated prompt on every
        # provider request as well. This changes the wire copy, not history.
        value = load()
        return [{"role": "system", "content": value}, *(
            message for message in messages
            if not isinstance(message, dict) or message.get("role") != "system"
        )]

    # Specialists use the same Hermes runtime but must never receive the
    # sovereign request-tail context, attention lease, or semantic-GC hooks.
    # Their provider wire retains only the newest assistant reasoning segment:
    # this preserves immediate tool-loop continuity while preventing each
    # disposable investigation from replaying every earlier scratch trace.
    # Full provider trajectories remain available in the diagnostic trace.
    if specialist_mode:
        import run_agent  # type: ignore

        original_specialist_sanitize = run_agent.AIAgent._sanitize_api_messages

        def compact_specialist_context(messages):  # noqa: ANN001
            sanitized = original_specialist_sanitize(copy.deepcopy(messages))
            if not isinstance(sanitized, list):
                return sanitized
            sanitized = canonical_system(sanitized)
            assistant_rows = [
                index for index, message in enumerate(sanitized)
                if isinstance(message, dict) and message.get("role") == "assistant"
            ]
            newest = assistant_rows[-1] if assistant_rows else -1
            for index in assistant_rows:
                if index == newest:
                    continue
                message = sanitized[index]
                for field in ("reasoning", "reasoning_content", "reasoning_details"):
                    message.pop(field, None)
                content = message.get("content")
                if isinstance(content, str):
                    message["content"] = _without_historical_thinking(content)[0]
            return sanitized

        run_agent.AIAgent._sanitize_api_messages = staticmethod(
            compact_specialist_context
        )
        return

    # Hermes deliberately defaults reasoning echo off for unknown custom
    # providers. Managed Qwen profiles opt in through config, and this wire
    # policy keeps only the current genuine user episode's reasoning. It also
    # removes superseded, repetitive game-state payloads without altering the
    # durable Hermes transcript.
    import run_agent  # type: ignore

    if os.environ.get("SMACX_DIAGNOSTICS_ENABLED") == "1":
        from smacx_diagnostics import DiagnosticWriter, install_hermes_capture, install_httpx_capture
        diagnostic_writer = DiagnosticWriter(
            Path(os.environ.get("SMACX_DIAGNOSTICS_ROOT", "/opt/data/diagnostics")),
            os.environ["SMACX_AGENT_MATCH_ID"], "sovereign", compress=True, human_log=True,
        )
        install_hermes_capture(run_agent.AIAgent, diagnostic_writer)
        import httpx
        install_httpx_capture(httpx.Client, diagnostic_writer)

    original_sanitize = run_agent.AIAgent._sanitize_api_messages
    logger = logging.getLogger("smacx.context")

    def compact_managed_context(messages, *, include_runtime=True):  # noqa: ANN001
        # Inspect only this invocation's results. Historical authority loss is
        # evidence, not an instruction to terminate a newly admitted episode.
        last_user_index = max((i for i, row in enumerate(messages)
            if isinstance(row, dict) and row.get("role") == "user"), default=-1)
        if include_runtime:
            for row in messages[last_user_index + 1:]:
                if isinstance(row, dict) and row.get("role") == "tool":
                    result = _managed_tool_result(row.get("content"))
                    if isinstance(result, dict) and result.get("episode_restart_required") is True:
                        _authority_handoff()
        # Hermes's sanitizer may return a shallow list whose message mappings
        # are still the durable transcript objects. All semantic GC and trusted
        # runtime augmentation are provider-wire transformations only.
        sanitized = original_sanitize(copy.deepcopy(messages))
        if not isinstance(sanitized, list):
            return sanitized
        sanitized = canonical_system(sanitized)
        last_user = max(
            (index for index, message in enumerate(sanitized)
             if isinstance(message, dict) and message.get("role") == "user"),
            default=-1,
        )
        compacted_reasoning = compacted_think_blocks = 0
        # Keep one reasoning segment across tool calls. Older private reasoning
        # remains in the durable transcript; visible prose and tool evidence
        # are governed independently below. Never mutate transcript objects.
        latest_reasoning = max((i for i, row in enumerate(sanitized)
            if isinstance(row, dict) and row.get("role") == "assistant"
            and i >= last_user and (any(str(row.get(k) or "").strip() for k in
                ("reasoning", "reasoning_content", "reasoning_details"))
                or "<think>" in str(row.get("content") or ""))), default=-1)
        compacted_frames = compacted_boundaries = 0
        compacted_queries = evicted_queries = 0
        pruned_tool_calls = pruned_tool_results = 0
        tool_names: dict[str, str] = {}
        tool_signatures: dict[str, str] = {}
        tool_calls_by_id: dict[str, dict] = {}
        historical_tool_call_ids: set[str] = set()
        pending_tool_ids: set[str] = set()
        state_rows: list[int] = []
        decision_rows: dict[str, int] = {}
        consumed_decision_ids: set[str] = set()
        for index, message in enumerate(sanitized):
            if not isinstance(message, dict):
                continue
            if message.get("role") == "assistant":
                if index >= last_user:
                    # The latest assistant tool batch has not yet received a
                    # following assistant response. Every one of its results
                    # must reach the provider before it can be superseded or
                    # evicted. A later ordinary response clears this protection.
                    pending_tool_ids = {
                        call["id"] for call in message.get("tool_calls") or []
                        if isinstance(call, dict) and isinstance(call.get("id"), str)
                    }
                if index < last_user or index != latest_reasoning:
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
                call_id = str(message.get("tool_call_id") or "")
                name = tool_names.get(call_id, "")
                if name in _STATE_TOOL_NAMES:
                    state_rows.append(index)
                result = _managed_tool_result(message.get("content"))
                if name == "smac_execute_choice" and isinstance(result, dict):
                    nested = (result.get("post_action_decision") or {}).get("frame")
                    if isinstance(nested, dict) and isinstance(nested.get("decision_id"), str):
                        decision_rows[nested["decision_id"]] = index
                if name in {"smac_decision", "smac_choices"} and isinstance(result, dict) \
                        and isinstance(result.get("decision_id"), str):
                    decision_rows[result["decision_id"]] = index
                elif name == "smac_execute_choice" and isinstance(result, dict) \
                        and result.get("decision_consumed") is True:
                    arguments = _managed_tool_arguments(tool_calls_by_id.get(call_id)) or {}
                    decision_id = arguments.get("decision_id")
                    if isinstance(decision_id, str):
                        consumed_decision_ids.add(decision_id)
            elif message.get("role") == "user":
                content = message.get("content")
                if index < last_user and isinstance(content, str) \
                        and content.startswith("[SMACX_EPISODE_BOUNDARY"):
                    message["content"] = "[Superseded managed gameplay episode boundary.]"
                    compacted_boundaries += 1
        superseded_consumed_rows: set[int] = set()
        for decision_id in consumed_decision_ids:
            index = decision_rows.get(decision_id)
            if index is None or str(sanitized[index].get("tool_call_id") or "") in pending_tool_ids:
                continue
            original_result = _managed_tool_result(sanitized[index].get("content"))
            if isinstance(original_result, dict) and original_result.get("post_action_decision"):
                original_result["post_action_decision"] = {
                    "schema": "smacx.post-action-decision.v1",
                    "frame": {"superseded_runtime_state": True, "decision_consumed": True}}
                sanitized[index]["content"] = json.dumps(original_result, separators=(",", ":"))
                compacted_frames += 1
                continue
            sanitized[index]["content"] = json.dumps({
                "ok": True,
                "superseded_runtime_state": True,
                "decision_consumed": True,
                "instruction": "This decision was consumed by a later execution receipt. Never reuse its decision_id or choice_id; use the newest execution or recovery result and current native focus.",
            }, separators=(",", ":"))
            superseded_consumed_rows.add(index)
            compacted_frames += 1
        for index in state_rows:
            if index == state_rows[-1] and index >= last_user:
                continue
            message = sanitized[index]
            if index in superseded_consumed_rows \
                    or str(message.get("tool_call_id") or "") in pending_tool_ids:
                continue
            message["content"] = json.dumps({
                "ok": True,
                "superseded_runtime_state": True,
                "instruction": "Use the newest decision/state tool result.",
            }, separators=(",", ":"))
            compacted_frames += 1
        # Query evidence is retained within the current episode until it is
        # actually superseded. Choices for different families/subjects are
        # complementary evidence, not interchangeable current-state frames.
        # Only earlier identical choice/world/reference queries
        # are collapsed; provider-valid assistant/tool pairing remains intact.
        newest_query: dict[str, int] = {}
        for index, message in enumerate(sanitized):
            if not isinstance(message, dict) or message.get("role") != "tool":
                continue
            if index in superseded_consumed_rows:
                continue
            call_id = str(message.get("tool_call_id") or "")
            if tool_names.get(call_id) not in _QUERY_TOOL_NAMES:
                continue
            signature = tool_signatures.get(call_id, call_id)
            prior = newest_query.get(signature)
            if prior is not None and str(sanitized[prior].get("tool_call_id") or "") not in pending_tool_ids:
                sanitized[prior]["content"] = json.dumps({
                    "ok": True, "semantic_gc": "superseded_query_evidence",
                    "retention": "Use the later identical query result.",
                }, separators=(",", ":"))
                compacted_queries += 1
            newest_query[signature] = index
        # A managed user boundary is emitted only after the prior native-turn
        # episode has yielded its durable TURN HANDOFF. Retain that ordinary
        # assistant summary, but remove the completed protocol pairs that led
        # to it. Otherwise every opaque choice and full JSON result is replayed
        # forever even though the journal and handoff already preserve the
        # durable outcome. Current-episode pairs remain untouched so provider
        # tool-call ordering stays valid while the turn is in progress.
        from smacx_continuation import preserve_continuation
        continuation_metrics = {}
        if os.environ.get("SMACX_CONSERVATIVE_CONTINUATION", "1") != "0":
            filtered, continuation_metrics = preserve_continuation(
                sanitized, last_user, tool_names, _managed_tool_result,
                protected=pending_tool_ids)
            pruned_tool_calls = pruned_tool_results = continuation_metrics["settled_protocol_pairs_removed"]
        else:
            filtered = sanitized
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
                if call_id in pending_tool_ids:
                    continue
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
                in _QUERY_TOOL_NAMES
            ]
            for message in query_tool_rows[:-1]:
                if str(message.get("tool_call_id") or "") in pending_tool_ids:
                    continue
                previous = _managed_tool_result(message.get("content"))
                if not isinstance(previous, dict) or previous.get("ok") is not True:
                    continue
                message["content"] = json.dumps({
                    "ok": True, "semantic_gc": "context_pressure_query_eviction",
                    "retention": "Requery only if current consequential work still needs it.",
                }, separators=(",", ":"))
                evicted_queries += 1
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
        from smacx_diagnostics import record
        record("history_compaction", {**_RUNTIME_STATE.gc_metrics, **continuation_metrics,
            "reasoning_fields": compacted_reasoning, "think_blocks": compacted_think_blocks,
            "state_frames": compacted_frames, "episode_boundaries": compacted_boundaries,
            "query_results_superseded": compacted_queries,
            "query_results_evicted_for_budget": evicted_queries,
            "historical_tool_calls": pruned_tool_calls, "historical_tool_results": pruned_tool_results,
            "protected_latest_batch_results": len(pending_tool_ids),
        }, actor="runtime-context-builder", correlation={"episode_id": _episode_id(filtered)})
        return _append_runtime_context(filtered) if include_runtime else filtered

    run_agent.AIAgent._sanitize_api_messages = staticmethod(compact_managed_context)

    # Resume/idle preflight runs before the send-time sanitizer. Counting the
    # durable transcript there triggers lossy generic summarization of state
    # that semantic GC would remove from the wire. Reuse the same copy-only
    # projection without acquiring an attention lease or fetching game state.
    import agent.turn_context as turn_context  # type: ignore
    from smacx_runtime_context import RUNTIME_BUDGETS

    def managed_preflight_tokens(agent, messages, system_prompt):  # noqa: ANN001,ARG001
        projected = compact_managed_context(messages, include_runtime=False)
        context_length = int(os.environ.get("SMACX_CONTEXT_LENGTH", "65536"))
        tier = "64k" if context_length < 131072 else "256k"
        estimate = turn_context.estimate_request_tokens_rough(
            projected, tools=getattr(agent, "tools", None) or None,
        ) + RUNTIME_BUDGETS[tier]["total"]
        from smacx_diagnostics import record
        record("semantic_preflight", {
            "estimated_request_tokens": estimate,
            "runtime_token_reserve": RUNTIME_BUDGETS[tier]["total"],
            "source_rows": len(messages), "wire_rows": len(projected),
            "runtime_context_fetched": False,
        }, actor="runtime-context-builder", correlation={"episode_id": _episode_id(projected)})
        return estimate

    turn_context._preflight_request_tokens = managed_preflight_tokens

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
        from smacx_diagnostics import record
        record("sovereign_response", {"message": message, "finish_reason": finish_reason},
               actor="sovereign", correlation={"episode_id": getattr(_RUNTIME_STATE, "episode_id", ""),
                 "attention_lease_id": getattr(_RUNTIME_STATE, "attention_lease_id", "")})
        heartbeat = getattr(_RUNTIME_STATE, "heartbeat", None)
        if heartbeat and heartbeat.lost.is_set():
            _authority_handoff()
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            message["content"] = _compact_turn_handoff(message["content"])
        _mark_runtime_responded()
        if isinstance(message, dict) and not message.get("tool_calls") \
                and finish_reason in {"stop", "end_turn"}:
            _end_runtime_episode(committed=True)
        elif isinstance(message, dict) and not message.get("tool_calls") \
                and finish_reason in {"length", "incomplete", "content_filter"}:
            # Hermes may append a new user continuation after these responses.
            # Release this invocation without claiming a completed handoff.
            _end_runtime_episode(committed=False)
        return message

    run_agent.AIAgent._strip_think_blocks = bounded_strip_think_blocks
    run_agent.AIAgent._build_assistant_message = bounded_build_assistant_message


_install()
