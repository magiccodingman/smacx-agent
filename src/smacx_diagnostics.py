"""Non-authoritative, bounded campaign diagnostic events.

Writers never infer gameplay effects or mutate journal/sovereign state. Each
process writes its own stream; correlation IDs join streams during export.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
import uuid
from typing import Any, Mapping

SCHEMA = "smacx.diagnostic-event.v1"
_PRIVATE = re.compile(r"^(authorization|cookie|set-cookie|password|api_key|access_token|refresh_token|secret)$", re.I)
_SAFE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


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
                 max_bytes: int = 128 * 1024 * 1024,
                 max_event_bytes: int = 2 * 1024 * 1024):
        if not _SAFE.fullmatch(match_id) or not _SAFE.fullmatch(actor):
            raise ValueError("invalid_diagnostic_scope")
        if max_bytes < 1024 or max_event_bytes < 256:
            raise ValueError("invalid_diagnostic_budget")
        self.directory = Path(root) / match_id
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.stream_id = uuid.uuid4().hex
        self.path = self.directory / f"{actor}-{self.stream_id}.jsonl"
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
        with self._lock:
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
            if self._bytes + len(line) > self.max_bytes:
                self._exhausted = True
                event["kind"] = "capture_gap"
                event["payload"] = {"reason": "stream_byte_limit", "capture_status": "incomplete"}
                line = json.dumps(event, separators=(",", ":")).encode() + b"\n"
            # A unique file per writer avoids cross-process append interleaving.
            # The single terminal gap record is allowed beyond the data budget.
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                with os.fdopen(fd, "ab") as stream:
                    stream.write(line)
                    stream.flush()
            except Exception:
                # Never report a diagnostic record as captured when its write failed.
                raise
            self._bytes += len(line)
            return {"ok": not self._exhausted, "event_id": event["event_id"],
                    "stream_id": self.stream_id, "sequence": self._sequence,
                    "capture_status": "incomplete" if self._exhausted else "recorded"}


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
        correlations = {"batch_id": batch_id, "task_id": str(effective_task_id),
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
