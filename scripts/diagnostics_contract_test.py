#!/usr/bin/env python3
"""Diagnostic storage bounds and actor isolation, independent of gameplay."""
import concurrent.futures
import json
from pathlib import Path
import tempfile

from smacx_diagnostics import DiagnosticWriter


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        writer = DiagnosticWriter(root, "match-test", "sovereign")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: writer.emit("tool_result", {
                "n": n, "nested": {"authorization": "Bearer private"},
                "token_budget": 1024}, correlation={"call_id": str(n)}), range(64)))
        rows = [json.loads(line) for line in writer.path.read_text().splitlines()]
        assert all(r["ok"] for r in results)
        assert [r["sequence"] for r in rows] == list(range(1, 65))
        assert len({r["event_id"] for r in rows}) == 64
        assert "Bearer private" not in writer.path.read_text()
        assert rows[0]["payload"]["token_budget"] == 1024
        other = DiagnosticWriter(root, "match-test", "reference-specialist")
        other.emit("mission_started", {"objective": "Artifact mechanics"})
        assert other.path != writer.path
        large = DiagnosticWriter(root, "match-test", "native", max_event_bytes=256)
        large.emit("observation", {"text": "x" * 1000})
        omitted = json.loads(large.path.read_text())["payload"]
        assert omitted["reason"] == "event_byte_limit" and omitted["original_bytes"] > 1000
        bounded = DiagnosticWriter(root, "match-test", "runtime", max_bytes=1024)
        for _ in range(20): bounded.emit("context", {"data": "x" * 400})
        bounded_rows = [json.loads(s) for s in bounded.path.read_text().splitlines()]
        assert bounded_rows[-1]["kind"] == "capture_gap"
        assert sum(r["kind"] == "capture_gap" for r in bounded_rows) == 1
        for bad in ("../escape", "match/name", ""):
            try: DiagnosticWriter(root, bad, "sovereign")
            except ValueError: pass
            else: raise AssertionError("unsafe diagnostic scope accepted")
    print(json.dumps({"event": "pass", "payload": {
        "concurrent_records_intact": True, "actor_streams_isolated": True,
        "credential_fields_redacted": True, "explicit_capture_gaps": True,
        "live_capture_not_yet_integrated": True}}))


if __name__ == "__main__":
    main()
