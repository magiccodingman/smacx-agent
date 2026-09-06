#!/usr/bin/env python3
"""Diagnostic storage bounds and actor isolation, independent of gameplay."""
import concurrent.futures
import json
import gzip
from pathlib import Path
import tempfile
from types import SimpleNamespace

from smacx_diagnostics import DiagnosticWriter, install_hermes_capture


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
        compressed = DiagnosticWriter(root, "match-compressed", "sovereign", compress=True, max_match_bytes=1024)
        for n in range(8): compressed.emit("context", {"n":n,"body":"large repeated context "*1000})
        restored = [json.loads(line) for line in gzip.decompress(compressed.path.read_bytes()).splitlines()]
        assert restored[0]["payload"]["body"].startswith("large repeated")
        assert restored[-1]["kind"] == "capture_gap"
        restarted = DiagnosticWriter(root, "match-compressed", "sovereign", compress=True, max_match_bytes=1024)
        assert restarted.emit("context", {})["reason"] == "match_byte_limit"
        assert not restarted.path.exists(), "restart bypassed aggregate retention limit"
        for bad in ("../escape", "match/name", ""):
            try: DiagnosticWriter(root, bad, "sovereign")
            except ValueError: pass
            else: raise AssertionError("unsafe diagnostic scope accepted")
        # The caller boundary must retain failures that never reach MCP.
        class Agent:
            def _execute_tool_calls(self, assistant, messages, task, index=0):
                messages.append({"role": "tool", "tool_call_id": "call-test",
                                 "content": "Tool does not exist; not invoked"})
                return "unchanged"
        traced = DiagnosticWriter(root, "match-test", "dispatch")
        install_hermes_capture(Agent, traced)
        install_hermes_capture(Agent, traced)  # no double wrapping
        message = SimpleNamespace(tool_calls=[SimpleNamespace(id="call-test",
            function=SimpleNamespace(name="tool_call", arguments=json.dumps({
                "name": "smac_memory", "arguments": {"wrong_parameter": True}})))])
        history = []
        assert Agent()._execute_tool_calls(message, history, "task-test", 4) == "unchanged"
        capture = [json.loads(s) for s in traced.path.read_text().splitlines()]
        assert [r["kind"] for r in capture] == ["tool_requested", "tool_returned", "tool_batch_finished"]
        assert capture[0]["payload"]["managed_name"] == "smac_memory"
        assert capture[1]["payload"]["content"] == history[0]["content"]
        assert capture[2]["payload"]["missing_result_call_ids"] == []
    print(json.dumps({"event": "pass", "payload": {
        "concurrent_records_intact": True, "actor_streams_isolated": True,
        "credential_fields_redacted": True, "explicit_capture_gaps": True,
        "live_capture_not_yet_integrated": True}}))


if __name__ == "__main__":
    main()
