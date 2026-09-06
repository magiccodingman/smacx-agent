"""Non-authoritative, bounded campaign diagnostic events.

Writers never infer gameplay effects or mutate journal/sovereign state. Each
process writes its own stream; correlation IDs join streams during export.
"""
from __future__ import annotations

import hashlib
import json
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
