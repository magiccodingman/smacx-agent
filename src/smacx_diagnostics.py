"""Non-authoritative, bounded campaign diagnostic events.

Writers never infer gameplay effects or mutate journal/sovereign state. Each
process writes its own stream; correlation IDs join streams during export.
"""
from __future__ import annotations

import hashlib
import gzip
import fcntl
import functools
import inspect
import json
import logging
import os
import sys
from pathlib import Path
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextvars import ContextVar
from typing import Any, Mapping

SCHEMA = "smacx.diagnostic-event.v1"
_PRIVATE = re.compile(r"^(authorization|cookie|set-cookie|password|api_key|access_token|refresh_token|secret)$", re.I)
_SAFE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_WRITERS = {}
_WRITER_LOCK = threading.Lock()
INVOCATION = ContextVar("smacx_diagnostic_invocation", default="")
PROVIDER_CORRELATION = ContextVar("smacx_provider_correlation", default=None)

# Hermes may receive HTTP responses in a worker thread and validate/dispatch
# calls on its conversation thread. ContextVars do not cross that boundary.
_PROVIDER_CALLS = OrderedDict()
_PROVIDER_CALLS_LOCK = threading.Lock()


def _remember_provider_calls(value, correlation):
    if not isinstance(value, dict):
        return
    for choice in value.get("choices", []):
        if not isinstance(choice, dict):
            continue
        message = choice.get("message", choice.get("delta", {}))
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            call_id = call.get("id") if isinstance(call, dict) else None
            if not isinstance(call_id, str) or not call_id:
                continue
            with _PROVIDER_CALLS_LOCK:
                _PROVIDER_CALLS[call_id] = dict(correlation)
                _PROVIDER_CALLS.move_to_end(call_id)
                while len(_PROVIDER_CALLS) > 512:
                    _PROVIDER_CALLS.popitem(last=False)


def _provider_call_correlation(call_id):
    with _PROVIDER_CALLS_LOCK:
        return dict(_PROVIDER_CALLS.get(str(call_id), {}))


def payload_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def record(kind: str, payload: Mapping[str, Any], *, actor: str = "managed-mcp",
           correlation: Mapping[str, str] | None = None,
           match_id: str | None = None) -> None:
    """Best-effort diagnostics; explicit match scope opts control-plane events in."""
    if os.environ.get("SMACX_DIAGNOSTICS_ENABLED") != "1" and match_id is None:
        return
    try:
        match = match_id or os.environ["SMACX_AGENT_MATCH_ID"]
        root = os.environ.get("SMACX_DIAGNOSTICS_ROOT", "/var/lib/smacx/gameplay-diagnostics")
        key = (root, match, actor)
        with _WRITER_LOCK:
            if key not in _WRITERS:
                _WRITERS[key] = DiagnosticWriter(Path(root), match, actor,
                                                 compress=True, human_log=True)
            writer = _WRITERS[key]
        receipt = writer.emit(kind, payload, correlation={**(PROVIDER_CORRELATION.get() or {}), **(correlation or {})})
        if not receipt["ok"]:
            logging.getLogger("smacx.diagnostics").error("Capture incomplete: %s", receipt)
    except Exception as exc:
        logging.getLogger("smacx.diagnostics").error("Capture failed: %s", type(exc).__name__)


def trace_managed_tool(function):
    signature = inspect.signature(function)
    @functools.wraps(function)
    def traced(*args, **kwargs):
        invocation = uuid.uuid4().hex
        correlation = {"invocation_id": invocation}
        bound = signature.bind(*args, **kwargs)
        arguments = dict(bound.arguments)
        for key in ("decision_id", "choice_id", "session_id", "observed_revision"):
            if arguments.get(key): correlation[key] = str(arguments[key])
        record("managed_tool_started", {"tool": function.__name__, "arguments": arguments},
               correlation=correlation)
        started = time.monotonic_ns()
        token = INVOCATION.set(invocation)
        try:
            result = function(*args, **kwargs)
        except BaseException as exc:
            record("managed_tool_exception", {"tool": function.__name__,
                   "exception_type": type(exc).__name__,
                   "elapsed_ms": (time.monotonic_ns() - started) / 1e6}, correlation=correlation)
            raise
        finally:
            INVOCATION.reset(token)
        record("managed_tool_returned", {"tool": function.__name__, "result": result,
               "elapsed_ms": (time.monotonic_ns() - started) / 1e6}, correlation=correlation)
        return result
    return traced


def redact(value: Any) -> Any:
    """Redact credential fields without erasing game token-budget statistics."""
    if isinstance(value, Mapping):
        return {str(k): "[redacted]" if _PRIVATE.match(str(k)) else redact(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("diagnostic_payload_must_be_json")


class DiagnosticWriter:
    def __init__(self, root: Path, match_id: str, actor: str, *,
                 max_bytes: int = 512 * 1024 * 1024,
                 max_event_bytes: int = 2 * 1024 * 1024,
                 compress: bool = False, max_match_bytes: int = 2 * 1024 * 1024 * 1024,
                 human_log: bool = False):
        if not _SAFE.fullmatch(match_id) or not _SAFE.fullmatch(actor):
            raise ValueError("invalid_diagnostic_scope")
        if max_bytes < 1024 or max_event_bytes < 256:
            raise ValueError("invalid_diagnostic_budget")
        self.directory = Path(root) / match_id
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.stream_id = uuid.uuid4().hex
        self.path = self.directory / (f"{actor}-{self.stream_id}.jsonl" + (".gz" if compress else ""))
        self.compress, self.max_match_bytes = compress, max_match_bytes
        self.human_log = human_log
        self.match_id, self.actor = match_id, actor
        self.max_bytes, self.max_event_bytes = max_bytes, max_event_bytes
        self._lock = threading.Lock()
        self._sequence = 0
        self._bytes = 0
        self._exhausted = False

    def emit(self, kind: str, payload: Mapping[str, Any], *,
             correlation: Mapping[str, str] | None = None) -> dict[str, Any]:
        if not _SAFE.fullmatch(kind):
            raise ValueError("invalid_diagnostic_event_kind")
        clean = redact(payload)
        encoded_payload = json.dumps(clean, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()
        if len(encoded_payload) > self.max_event_bytes:
            clean = {"capture_status": "omitted", "reason": "event_byte_limit",
                     "original_bytes": len(encoded_payload),
                     "redacted_payload_sha256": hashlib.sha256(encoded_payload).hexdigest()}
        with self._lock, (self.directory / ".capture.lock").open("a+b") as match_lock:
            fcntl.flock(match_lock.fileno(), fcntl.LOCK_EX)
            if (self.directory / ".capacity-exhausted").exists():
                return {"ok": False, "reason": "match_byte_limit", "stream_id": self.stream_id}
            if self._exhausted:
                return {"ok": False, "reason": "stream_byte_limit", "stream_id": self.stream_id}
            self._sequence += 1
            event = {"schema": SCHEMA, "event_id": uuid.uuid4().hex,
                     "stream_id": self.stream_id, "sequence": self._sequence,
                     "recorded_unix": time.time(), "monotonic_ns": time.monotonic_ns(),
                     "match_id": self.match_id, "actor": self.actor, "kind": kind,
                     "correlation": redact(correlation or {}), "payload": clean}
            line = json.dumps(event, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False).encode() + b"\n"
            packed = gzip.compress(line, compresslevel=3, mtime=0) if self.compress else line
            match_bytes = sum(path.stat().st_size for path in self.directory.glob("*.jsonl*") if path.is_file())
            match_exhausted = match_bytes + len(packed) > self.max_match_bytes
            if self._bytes + len(packed) > self.max_bytes or match_exhausted:
                self._exhausted = True
                event["kind"] = "capture_gap"
                event["payload"] = {"reason": "match_byte_limit" if match_exhausted else "stream_byte_limit", "capture_status": "incomplete"}
                line = json.dumps(event, separators=(",", ":")).encode() + b"\n"
                packed = gzip.compress(line, compresslevel=3, mtime=0) if self.compress else line
                if match_exhausted: (self.directory / ".capacity-exhausted").touch(mode=0o600)
            # A unique file per writer avoids cross-process append interleaving.
            # The single terminal gap record is allowed beyond the data budget.
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                with os.fdopen(fd, "ab") as stream:
                    stream.write(packed)
                    stream.flush()
            except Exception:
                # Never report a diagnostic record as captured when its write failed.
                raise
            self._bytes += len(packed)
            if self.human_log and event["kind"] in {"tool_requested", "tool_returned",
                    "managed_tool_started", "managed_tool_returned", "choice_selected", "capture_gap",
                    "tool_validation_rejected"}:
                from smacx_diagnostic_summary import summary
                rendered = summary(event)
                rendered = re.sub(r"[\x00-\x1f\x7f]", " ", rendered)
                suffix = " [details in diagnostics]" if len(rendered)>1400 else ""
                print(f"SMACX_TRACE [{self.actor}] {rendered[:1400]}{suffix}", file=sys.stderr, flush=True)
            return {"ok": not self._exhausted, "event_id": event["event_id"],
                    "stream_id": self.stream_id, "sequence": self._sequence,
                    "capture_status": "incomplete" if self._exhausted else "recorded"}


def record_unknown_tool_calls(calls, valid_names) -> None:
    """Capture Hermes name validation, which precedes its tool executor."""
    for call in calls:
        name = call.function.name
        if name in valid_names:
            continue
        raw = call.function.arguments
        try:
            arguments = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            arguments = {"unparsed_arguments": raw}
        record("tool_validation_rejected", {
            "dispatch_name": name, "managed_name": name, "arguments": arguments,
            "error": {"code": "unknown_tool_name"},
            "native_action_executed": False,
            "rejection_stage": "before_hermes_tool_executor",
            "available_tools": sorted(valid_names),
        }, actor=("specialist" if os.environ.get("SMACX_SPECIALIST_STRICT_PROMPT") == "1" else "sovereign"),
           correlation={**_provider_call_correlation(getattr(call, "id", "")),
                        "call_id": str(getattr(call, "id", "")), **{
               key: os.environ[env] for key, env in (
                   ("mission_id", "SMACX_SPECIALIST_MISSION_ID"),
                   ("attempt_id", "SMACX_SPECIALIST_ATTEMPT_ID")) if os.environ.get(env)}})


def install_hermes_capture(agent_class, writer: DiagnosticWriter) -> None:
    """Capture emitted calls and all returned rows, including dispatch rejection.

    This outer boundary deliberately does not claim per-tool execution latency:
    parallel batches share an elapsed wall time. Native effects are a separate
    stream. Original arguments, results and exceptions pass through unchanged.
    """
    if getattr(agent_class, "_smacx_diagnostic_capture", False):
        return
    original = agent_class._execute_tool_calls

    def emit(kind, payload, correlation):
        try:
            receipt = writer.emit(kind, payload, correlation=correlation)
            if not receipt["ok"]:
                logging.getLogger("smacx.diagnostics").error("Diagnostic capture incomplete: %s", receipt)
        except Exception as exc:
            # Diagnostic failure must be visible but cannot reinterpret a game action.
            logging.getLogger("smacx.diagnostics").error("Diagnostic capture failed: %s", type(exc).__name__)

    def captured(self, assistant_message, messages, effective_task_id, api_call_count=0):
        batch_id = uuid.uuid4().hex
        started = time.monotonic_ns()
        first_call = next(iter(assistant_message.tool_calls), None)
        correlations = {**(PROVIDER_CORRELATION.get() or {}),
                        **_provider_call_correlation(getattr(first_call, "id", "")), "batch_id": batch_id, "task_id": str(effective_task_id),
                        "provider_call_index": str(api_call_count)}
        names = {}
        for call in assistant_message.tool_calls:
            call_id = str(getattr(call, "id", ""))
            function = call.function
            raw = function.arguments
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else raw
            except (ValueError, TypeError):
                arguments = {"unparsed_arguments": raw}
            managed_name = function.name
            if managed_name == "tool_call" and isinstance(arguments, dict):
                managed_name = str(arguments.get("name") or managed_name)
            names[call_id] = managed_name
            emit("tool_requested", {"dispatch_name": function.name,
                 "managed_name": managed_name, "arguments": arguments},
                 {**correlations, "call_id": call_id})
        error = None
        try:
            return original(self, assistant_message, messages, effective_task_id, api_call_count)
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            seen = set()
            for row in messages:
                if not isinstance(row, dict) or row.get("role") != "tool":
                    continue
                call_id = str(row.get("tool_call_id") or "")
                if call_id not in names or call_id in seen:
                    continue
                seen.add(call_id)
                emit("tool_returned", {"managed_name": names[call_id],
                     "content": row.get("content"),
                     "effect_disposition": row.get("effect_disposition"),
                     "native_execution": "not_inferred_from_hermes_result"},
                     {**correlations, "call_id": call_id})
            emit("tool_batch_finished", {
                "batch_elapsed_ms": (time.monotonic_ns() - started) / 1_000_000,
                "latency_scope": "batch_including_dispatch",
                "requested": len(names), "returned": len(seen),
                "missing_result_call_ids": sorted(set(names) - seen),
                "exception_type": error}, correlations)

    agent_class._execute_tool_calls = captured
    agent_class._smacx_diagnostic_capture = True


def install_httpx_capture(client_class, writer: DiagnosticWriter) -> None:
    """Audit serialized chat-completions requests at the synchronous HTTP boundary.

    No headers, URL credentials or query strings are collected. Response capture
    retains only emitted provider data, including exposed reasoning fields.
    Returning HTTP headers is not proof that a streaming completion finished.
    Other provider protocols remain explicitly outside this adapter's coverage.
    """
    if getattr(client_class, "_smacx_wire_capture", False):
        return
    original = client_class.send

    def send(self, request, *args, **kwargs):
        if request.method != "POST" or not request.url.path.rstrip("/").endswith("/chat/completions"):
            return original(self, request, *args, **kwargs)
        request_id = uuid.uuid4().hex
        PROVIDER_CORRELATION.set({"request_id": request_id})
        started = time.monotonic_ns()
        correlation = {"request_id": request_id}

        def emit(kind, payload, _request_id):
            try:
                receipt = writer.emit(kind, payload, correlation=correlation)
                if not receipt["ok"]:
                    logging.getLogger("smacx.diagnostics").error("Provider audit incomplete: %s", receipt)
            except Exception as exc:
                logging.getLogger("smacx.diagnostics").error("Provider audit failed: %s", type(exc).__name__)

        try:
            raw = request.content
            payload = json.loads(raw)
            for message in reversed(payload.get("messages", [])):
                content = message.get("content") if isinstance(message, dict) else None
                marker = '<SMACX_RUNTIME_CONTEXT schema="smacx.runtime-context.v1">'
                if not isinstance(content, str) or not content.endswith('</SMACX_RUNTIME_CONTEXT>') or marker not in content:
                    continue
                try:
                    context = json.loads(content.rsplit(marker, 1)[1].rsplit('</SMACX_RUNTIME_CONTEXT>', 1)[0])
                    correlation.update({"request_id": request_id,
                        "runtime_context_sha256": payload_sha256(context),
                        "episode_id": str(context.get("episode", {}).get("episode_id", "")),
                        "attention_lease_id": str(context.get("attention", {}).get("attention_lease_id", ""))})
                except (ValueError, TypeError, AttributeError):
                    pass
                break
            PROVIDER_CORRELATION.set(dict(correlation))
            # Capture the serialized body, not an earlier mutable message list.
            emit("provider_request_submitted", {
                "protocol": "chat_completions", "body": payload,
                "serialized_body_bytes": len(raw),
                "serialized_body_sha256": hashlib.sha256(raw).hexdigest(),
                "capture_boundary": "httpx_send",
                "body_redaction": "credential_fields",
            }, request_id)
        except Exception as exc:
            emit("capture_gap", {"reason": "provider_request_unreadable",
                                 "exception_type": type(exc).__name__}, request_id)
        try:
            response = original(self, request, *args, **kwargs)
        except BaseException as exc:
            emit("provider_transport_failed", {
                "exception_type": type(exc).__name__,
                "elapsed_ms": (time.monotonic_ns() - started) / 1_000_000}, request_id)
            raise
        emit("provider_response_headers", {
            "http_status": response.status_code,
            "elapsed_ms": (time.monotonic_ns() - started) / 1_000_000,
            "completion_verified": False,
        }, request_id)
        if response.is_stream_consumed:
            try:
                response_body = response.json()
                _remember_provider_calls(response_body, correlation)
                emit("provider_response_body", {"body": response_body,
                    "transport_body_complete": True,
                    "elapsed_ms": (time.monotonic_ns()-started)/1e6}, request_id)
            except Exception:
                emit("capture_gap", {"reason": "non_json_provider_response",
                    "transport_body_complete": True}, request_id)
        else:
            import httpx
            original_stream = response.stream
            class CapturedStream(httpx.SyncByteStream):
                def __init__(self):
                    self.pending = b""
                    self.chunks = []
                    self.size = 0
                    self.omitted = False
                    self.done = False
                    self.finished = False
                    self.recorded = False
                    self.digest = hashlib.sha256()
                def line(self, line):
                    if not line.startswith(b"data:"): return
                    data = line[5:].strip()
                    if data == b"[DONE]": self.done = True; return
                    try: value = json.loads(data)
                    except ValueError: self.omitted = True; return
                    _remember_provider_calls(value, correlation)
                    if self.size + len(data) <= writer.max_event_bytes // 2:
                        self.chunks.append(value); self.size += len(data)
                    else: self.omitted = True
                def receipt(self):
                    if self.recorded: return
                    self.recorded = True
                    emit("provider_response_stream", {"chunks": self.chunks,
                        "stream_exhausted": self.finished, "done_marker_observed": self.done,
                        "capture_truncated": self.omitted,
                        "stream_sha256": self.digest.hexdigest(),
                        "elapsed_ms": (time.monotonic_ns()-started)/1e6}, request_id)
                def __iter__(self):
                    try:
                        for data in original_stream:
                            self.digest.update(data)
                            self.pending += data
                            while b"\n" in self.pending:
                                line, self.pending = self.pending.split(b"\n", 1)
                                self.line(line)
                            if len(self.pending) > writer.max_event_bytes:
                                self.pending = b""; self.omitted = True
                            yield data
                        if self.pending: self.line(self.pending)
                        self.finished = True
                    finally: self.receipt()
                def close(self):
                    try: original_stream.close()
                    finally: self.receipt()
            response.stream = CapturedStream()
        return response

    client_class.send = send
    client_class._smacx_wire_capture = True
