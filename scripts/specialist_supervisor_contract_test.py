#!/usr/bin/env python3
"""Deterministic process, isolation, leash, retry, and trace contracts."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from smacx_journal import CampaignJournal
from smacx_specialist_supervisor import SpecialistSupervisor
from smacx_specialists import SpecialistService, SpecialistTraceStore
from smacx_store import MemoryScope, SmacxStore
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, canonical_json
import smacx_reference


FAKE_HERMES = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
args = sys.argv[1:]
prompt = json.loads(args[args.index("-z") + 1])
usage = pathlib.Path(args[args.index("--usage-file") + 1])
root = pathlib.Path(os.environ["HERMES_HOME"])
capture = pathlib.Path(os.environ["SMACX_TEST_CAPTURE"])
row = {
    "home": str(root), "argv": args,
    "config": json.loads((root / "config.yaml").read_text()),
    "system": (root / "specialist-system.txt").read_text(),
    "workspace_files": sorted(p.name for p in (root / "workspace").iterdir()),
    "specialist": os.environ.get("SMACX_SPECIALIST_STRICT_PROMPT"),
    "sovereign": os.environ.get("SMACX_STRICT_SYSTEM_PROMPT"),
}
with capture.open("a") as stream: stream.write(json.dumps(row) + "\n")
mode = os.environ.get("SMACX_TEST_FAKE_MODE", "success")
if mode == "sleep": time.sleep(30)
if mode == "brief_sleep": time.sleep(2)
if mode == "provider_fail":
    usage.write_text(json.dumps({"api_calls": 1, "total_tokens": 25, "failed": True}))
    print("provider unavailable", file=sys.stderr)
    raise SystemExit(3)
if mode == "mcp_fail":
    usage.write_text(json.dumps({"api_calls": 1, "total_tokens": 25, "failed": True}))
    print("specialist_mcp transport unavailable", file=sys.stderr)
    raise SystemExit(4)
if mode == "invalid_schema":
    usage.write_text(json.dumps({"api_calls": 1, "total_tokens": 25}))
    print("not-json")
    raise SystemExit(0)
if mode == "invalid_citation" and "schema_repair" not in prompt:
    usage.write_text(json.dumps({"api_calls": 1, "total_tokens": 25}))
    print(json.dumps({
        "mission_id": prompt["mission_id"], "answer": "Bad citation fixture.",
        "claims": [{"claim": "Unsupported.", "citations": ["invented-evidence"],
                    "epistemic_status": "derived"}],
        "limitations": [], "unresolved_questions": []
    }))
    raise SystemExit(0)
if mode == "token_budget":
    usage.write_text(json.dumps({"api_calls": 2, "total_tokens": 999999999}))
    print(json.dumps({
        "mission_id": prompt["mission_id"], "answer": "Would otherwise be valid.",
        "claims": [], "limitations": [], "unresolved_questions": []
    }))
    raise SystemExit(0)
usage.write_text(json.dumps({"api_calls": 2, "total_tokens": 321,
                             "peak_context_tokens": 1234}))
print(json.dumps({
    "mission_id": prompt["mission_id"], "answer": "Bounded evidence summary.",
    "claims": [], "limitations": [], "unresolved_questions": []
}))
'''


class ReferenceFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if not self.path.startswith("/api/export/"):
            self.send_error(404)
            return
        revision = unquote(self.path.removeprefix("/api/export/"))
        body = canonical_json({
            "revision": revision,
            "collections": [{"collection_id": "rules", "title": "Rules"}],
            "documents": [{"document_id": "movement", "title": "Movement",
                           "description": "Fixture", "collection_id": "rules",
                           "collection_path": "Rules", "source_hash": "fixture",
                           "body": "Road movement is mechanically bounded."}],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


def seed(root: Path) -> tuple[SmacxStore, WorldStore, MemoryScope, SpecialistService]:
    store = SmacxStore(root / "state.sqlite3")
    store.ensure_agent("agent-specialist", "Sovereign Secret Personality")
    store.create_match(match_id="match-specialist", display_name="Test", mode="solo")
    store.create_perspective(
        "match-specialist", "agent-specialist", perspective_id="perspective-specialist",
    )
    scope = MemoryScope("match-specialist", "agent-specialist", "perspective-specialist")
    journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
    identity = WorldIdentity(
        scope.match_id, scope.perspective_id, journal.timeline_id(scope), "world-specialist",
    )
    projection = PerspectiveProjector(identity).project({
        "turn": 1, "map": {"width": 4, "height": 2, "horizontal_wrap": False},
        "tiles": [{"tile_id": 0, "x": 0, "y": 0, "visible_now": True,
                   "terrain": "land", "features": []}],
        "bases": [], "units": [], "factions": [], "global": [],
    }, observation_sequence=1)
    worlds = WorldStore(store, root / "snapshots")
    worlds.replace_projection(
        scope, identity, projection["objects"], observation_cursor=1,
        action_revision="test", continuity="complete", journal_head_hash="0" * 64,
    )
    now = time.time()
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO model_providers(provider_id,display_name,provider_kind,base_url,"
            "default_model_id,status,metadata_json,created_unix,updated_unix) "
            "VALUES('provider-test','Test endpoint','openai_compatible','http://provider.invalid/v1',"
            "'test-model','healthy','{}',?,?)", (now, now),
        )
        connection.execute(
            "INSERT INTO provider_models(provider_id,model_id,display_name,context_length,"
            "capabilities_json,raw_metadata_json,discovered_unix) "
            "VALUES('provider-test','test-model','Test model',65536,'{}','{}',?)", (now,),
        )
        profile = {
            "profile_id": "helper-test", "display_name": "Helper test",
            "provider_id": "provider-test", "model_id": "test-model",
            "reasoning_effort": "low", "context_length": 65536,
            "generation_settings": {"temperature": 0.1, "reasoning_continuity": "current_episode"},
        }
        connection.execute(
            "INSERT INTO control_settings(setting_key,value_json,updated_unix) "
            "VALUES('specialist.profile',?,?)", (canonical_json(profile), now),
        )
        policy = {
            "installation_concurrency": 1, "seat_concurrency": 1,
            "automatic_retries": 1, "schema_repairs": 1,
            # Leave process-start headroom under a loaded CI/Docker host. The
            # explicit timeout fixture below shortens its own deadline, so the
            # contract does not depend on this setup value being razor-thin.
            "synthesis": {"wall_seconds": 15}, "investigation": {"wall_seconds": 15},
        }
        connection.execute(
            "INSERT INTO control_settings(setting_key,value_json,updated_unix) "
            "VALUES('specialist.policy',?,?)", (canonical_json(policy), now),
        )
    return store, worlds, scope, SpecialistService(store, worlds, scope, journal=journal)


def supervisor(root: Path, store: SmacxStore, worlds: WorldStore) -> SpecialistSupervisor:
    return SpecialistSupervisor(
        database=store.path, secret_root=root / "secrets", snapshot_root=worlds.root,
        trace_root=root / "traces", reference_url="http://reference.invalid", poll_seconds=0.1,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-specialist-supervisor-") as raw:
        root = Path(raw)
        reference_server = ThreadingHTTPServer(("127.0.0.1", 0), ReferenceFixtureHandler)
        reference_thread = threading.Thread(
            target=reference_server.serve_forever, daemon=True,
        )
        reference_thread.start()
        previous_reference_url = smacx_reference.REFERENCE_URL
        smacx_reference.REFERENCE_URL = (
            f"http://127.0.0.1:{reference_server.server_address[1]}"
        )
        store, worlds, scope, service = seed(root)
        fake = root / "fake_hermes.py"
        fake.write_text(FAKE_HERMES, encoding="utf-8")
        fake.chmod(0o755)
        capture = root / "capture.jsonl"
        prior = {name: os.environ.get(name) for name in (
            "SMACX_HERMES_EXECUTABLE", "SMACX_TEST_CAPTURE", "SMACX_TEST_FAKE_MODE",
            "SMACX_SPECIALIST_TEST_USAGE_FALLBACK",
        )}
        os.environ["SMACX_HERMES_EXECUTABLE"] = str(fake)
        os.environ["SMACX_TEST_CAPTURE"] = str(capture)
        os.environ["SMACX_SPECIALIST_TEST_USAGE_FALLBACK"] = "1"
        try:
            first = service.commission(faculty="world", objective="Analyze the first area")
            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            accepted = service.get(first["mission_id"])
            assert accepted["status"] == "accepted"
            first_capture = json.loads(capture.read_text().splitlines()[0])
            config = first_capture["config"]
            servers = config["mcp_servers"]
            assert list(servers) == ["specialist-world"]
            assert config["platform_toolsets"]["cli"] == ["specialist-world"]
            assert config["memory"] == {"memory_enabled": False, "user_profile_enabled": False}
            # Published-result size and provider-call reasoning headroom are
            # separate hard bounds. A small final JSON ceiling must not starve
            # the disposable Hermes tool/reasoning loop.
            assert config["model"]["max_tokens"] == 16_384
            with store._connect() as connection:
                first_mission_row = connection.execute(
                    "SELECT output_token_budget FROM specialist_missions WHERE mission_id=?",
                    (first["mission_id"],),
                ).fetchone()
            assert int(first_mission_row["output_token_budget"]) < config["model"]["max_tokens"]
            serialized = canonical_json(first_capture)
            assert "Sovereign Secret Personality" not in serialized
            assert "SMACX_RUNTIME_CONTEXT" not in serialized
            assert first_capture["workspace_files"] == []
            assert first_capture["specialist"] == "1" and first_capture["sovereign"] == "0"

            second = service.commission(faculty="world", objective="Analyze another area")
            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            captures = [json.loads(line) for line in capture.read_text().splitlines()]
            assert len(captures) == 2 and captures[0]["home"] != captures[1]["home"]
            assert service.get(second["mission_id"])["status"] == "accepted"

            # The MCP call counter is a durable hard leash, not a prompt hint.
            bounded = service.commission(
                faculty="world", objective="Tool budget boundary",
                execution_class="synthesis",
            )
            with store.transaction() as connection:
                connection.execute(
                    "UPDATE specialist_missions SET tool_budget=2 WHERE mission_id=?",
                    (bounded["mission_id"],),
                )
            bounded_attempt = service.begin_attempt(bounded["mission_id"], "tool-owner")
            assert service.claim_tool_call(bounded_attempt["attempt_id"]) == 1
            assert service.claim_tool_call(bounded_attempt["attempt_id"]) == 2
            try:
                service.claim_tool_call(bounded_attempt["attempt_id"])
            except Exception as exc:
                assert str(exc) == "specialist_tool_budget_exhausted"
            else:
                raise AssertionError("specialist tool budget did not fail closed")
            service.fail_attempt(
                bounded["mission_id"], bounded_attempt["attempt_id"],
                "tool_budget_exhausted", "specialist_tool_budget_exhausted",
                allow_retry=False,
            )
            assert service.get(bounded["mission_id"])["status"] == "failed"

            os.environ["SMACX_TEST_FAKE_MODE"] = "token_budget"
            token_bounded = service.commission(
                faculty="world", objective="Provider token budget boundary",
            )
            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            assert service.get(token_bounded["mission_id"])["status"] == "failed"
            with store._connect() as connection:
                token_attempt = connection.execute(
                    "SELECT status FROM specialist_attempts WHERE mission_id=?",
                    (token_bounded["mission_id"],),
                ).fetchone()
            assert token_attempt["status"] == "token_budget_exhausted"

            os.environ["SMACX_TEST_FAKE_MODE"] = "provider_fail"
            failed = service.commission(faculty="reference", objective="Transient failure")
            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            assert service.get(failed["mission_id"])["status"] == "mission_pending"
            with store._connect() as connection:
                attempt = connection.execute(
                    "SELECT status FROM specialist_attempts WHERE mission_id=?",
                    (failed["mission_id"],),
                ).fetchone()
            assert attempt["status"] == "provider_failed"

            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            assert service.get(failed["mission_id"])["status"] == "failed"

            os.environ["SMACX_TEST_FAKE_MODE"] = "mcp_fail"
            mcp_failed = service.commission(
                faculty="world", objective="Transient MCP failure",
            )
            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            assert service.get(mcp_failed["mission_id"])["status"] == "mission_pending"
            with store._connect() as connection:
                mcp_attempt = connection.execute(
                    "SELECT status FROM specialist_attempts WHERE mission_id=?",
                    (mcp_failed["mission_id"],),
                ).fetchone()
            assert mcp_attempt["status"] == "mcp_failed"
            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            assert service.get(mcp_failed["mission_id"])["status"] == "failed"

            os.environ["SMACX_TEST_FAKE_MODE"] = "invalid_schema"
            malformed = service.commission(faculty="world", objective="Malformed schema")
            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            assert service.get(malformed["mission_id"])["status"] == "mission_pending"
            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            assert service.get(malformed["mission_id"])["status"] == "failed"

            # A strict evidence/citation rejection is an invalid result, not a
            # provider outage. One fresh bounded repair attempt receives only
            # the typed rejection reason and must independently investigate.
            os.environ["SMACX_TEST_FAKE_MODE"] = "invalid_citation"
            repaired = service.commission(
                faculty="world", objective="Repair an invalid evidence citation",
            )
            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            assert service.get(repaired["mission_id"])["status"] == "mission_pending"
            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            assert service.get(repaired["mission_id"])["status"] == "accepted"
            repaired_captures = [
                json.loads(line) for line in capture.read_text().splitlines()
                if json.loads(line)["argv"][
                    json.loads(line)["argv"].index("-z") + 1
                ].find(repaired["mission_id"]) >= 0
            ]
            assert len(repaired_captures) == 2
            repair_prompt = json.loads(repaired_captures[-1]["argv"][
                repaired_captures[-1]["argv"].index("-z") + 1
            ])
            assert "specialist_claim_uses_unretrieved_evidence" in repair_prompt["schema_repair"]

            os.environ["SMACX_TEST_FAKE_MODE"] = "sleep"
            timeout = service.commission(faculty="world", objective="Hard timeout")
            with store.transaction() as connection:
                connection.execute(
                    "UPDATE specialist_missions SET deadline_unix=? WHERE mission_id=?",
                    (time.time() + 0.5, timeout["mission_id"]),
                )
            started = time.monotonic()
            runner = supervisor(root, store, worlds)
            runner.run(once=True)
            runner.shutdown()
            assert time.monotonic() - started < 8
            assert service.get(timeout["mission_id"])["status"] == "failed"

            # Parent cancellation wins the mission CAS and hard-reaps the
            # owned subprocess; its late attempt cannot publish anything.
            cancelled = service.commission(faculty="world", objective="Cancel active child")
            runner = supervisor(root, store, worlds)
            thread = threading.Thread(target=lambda: runner.run(once=True), daemon=True)
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with runner.children_lock:
                    if runner.children:
                        break
                time.sleep(0.05)
            with runner.children_lock:
                assert runner.children
            service.cancel(cancelled["mission_id"])
            thread.join(8)
            runner.shutdown()
            assert not thread.is_alive()
            assert service.get(cancelled["mission_id"])["status"] == "cancelled"
            with store._connect() as connection:
                cancelled_attempt = connection.execute(
                    "SELECT status FROM specialist_attempts WHERE mission_id=?",
                    (cancelled["mission_id"],),
                ).fetchone()
            assert cancelled_attempt["status"] == "cancelled"

            orphan = service.commission(faculty="world", objective="Orphan recovery")
            attempt = service.begin_attempt(orphan["mission_id"], "dead-owner", heartbeat_seconds=10)
            with store.transaction() as connection:
                connection.execute(
                    "UPDATE specialist_attempts SET heartbeat_expires_unix=? WHERE attempt_id=?",
                    (time.time() - 1, attempt["attempt_id"]),
                )
            assert service.reconcile_orphans("new-owner") == 1
            assert service.get(orphan["mission_id"])["status"] == "mission_pending"
            service.cancel(orphan["mission_id"])

            # Two read-only faculties may execute concurrently while a per-seat
            # cap and round-robin admission prevent one seat from starving another.
            store.ensure_agent("agent-specialist-two", "Second sovereign")
            store.create_perspective(
                "match-specialist", "agent-specialist-two",
                perspective_id="perspective-specialist-two",
            )
            second_scope = MemoryScope(
                "match-specialist", "agent-specialist-two", "perspective-specialist-two",
            )
            second_identity = WorldIdentity(
                second_scope.match_id, second_scope.perspective_id,
                store.active_timeline_id(second_scope), "world-specialist-two",
            )
            second_projection = PerspectiveProjector(second_identity).project({
                "turn": 1, "map": {"width": 4, "height": 2,
                                     "horizontal_wrap": False},
                "tiles": [{"tile_id": 0, "x": 0, "y": 0, "visible_now": True,
                           "terrain": "land", "features": []}],
                "bases": [], "units": [], "factions": [], "global": [],
            }, observation_sequence=1)
            worlds.replace_projection(
                second_scope, second_identity, second_projection["objects"],
                observation_cursor=1, action_revision="test", continuity="complete",
                journal_head_hash="0" * 64,
            )
            service_two = SpecialistService(store, worlds, second_scope)
            with store.transaction() as connection:
                connection.execute(
                    "UPDATE control_settings SET value_json=? WHERE setting_key='specialist.policy'",
                    (canonical_json({
                        "installation_concurrency": 2, "seat_concurrency": 1,
                        "automatic_retries": 0, "schema_repairs": 0,
                        "synthesis": {"wall_seconds": 8},
                        "investigation": {"wall_seconds": 8},
                    }),),
                )
            seat_one_first = service.commission(
                faculty="world", objective="Fair scheduler seat one first",
            )
            seat_one_second = service.commission(
                faculty="world", objective="Fair scheduler seat one second",
            )
            seat_two = service_two.commission(
                faculty="world", objective="Fair scheduler seat two",
            )
            os.environ["SMACX_TEST_FAKE_MODE"] = "brief_sleep"
            runner = supervisor(root, store, worlds)
            fair_thread = threading.Thread(
                target=lambda: runner.run(once=True), daemon=True,
            )
            fair_thread.start()
            deadline = time.monotonic() + 5
            maximum_children = 0
            while time.monotonic() < deadline and fair_thread.is_alive():
                with runner.children_lock:
                    maximum_children = max(maximum_children, len(runner.children))
                if maximum_children == 2:
                    break
                time.sleep(0.05)
            assert maximum_children == 2
            with store._connect() as connection:
                active_seats = {
                    str(row["perspective_id"]) for row in connection.execute(
                        "SELECT perspective_id FROM specialist_missions WHERE status='active'"
                    ).fetchall()
                }
            assert active_seats == {scope.perspective_id, second_scope.perspective_id}
            fair_thread.join(12)
            runner.shutdown()
            assert not fair_thread.is_alive()
            assert service.get(seat_one_first["mission_id"])["status"] == "accepted"
            assert service_two.get(seat_two["mission_id"])["status"] == "accepted"
            assert service.get(seat_one_second["mission_id"])["status"] == "mission_pending"
            service.cancel(seat_one_second["mission_id"])

            trace_store = SpecialistTraceStore(root / "traces")
            with store._connect() as connection:
                traces = [dict(row) for row in connection.execute(
                    "SELECT * FROM specialist_trace_manifests"
                ).fetchall()]
            assert traces
            for row in traces:
                path = Path(row["content_path"])
                assert path.exists()
                assert hashlib.sha256(path.read_bytes()).hexdigest() == row["content_sha256"]
                decoded = subprocess.run(
                    ["zstd", "-q", "-d", "-c", str(path)], capture_output=True,
                    check=True,
                ).stdout.decode("utf-8")
                assert "Sovereign Secret Personality" not in decoded
            scrubbed = trace_store.write(
                {"match_id": scope.match_id, "timeline_id": store.active_timeline_id(scope),
                 "mission_id": "mission-scrub-contract"},
                "attempt-scrub-contract",
                [{"authorization": "Bearer test-secret-material-123456789",
                  "nested": {"api_key": "test-secret-value-123456789",
                             "safe": "retained"}}],
                outcome="failed", generation=0,
            )
            scrubbed_text = subprocess.run(
                ["zstd", "-q", "-d", "-c", scrubbed["content_path"]],
                capture_output=True, check=True,
            ).stdout.decode("utf-8")
            assert "super_secret" not in scrubbed_text and "secret-value" not in scrubbed_text
            assert scrubbed_text.count("[REDACTED]") == 2
            # Generation retention: old successful diagnostics collect first;
            # failed evidence retains its longer floor. A protected set above
            # the byte ceiling emits an operator warning instead of deletion.
            with store.transaction() as connection:
                accepted_traces = connection.execute(
                    "SELECT t.attempt_id FROM specialist_trace_manifests t JOIN "
                    "specialist_missions m ON m.mission_id=t.mission_id "
                    "WHERE m.status='accepted' ORDER BY t.created_unix",
                ).fetchall()
                failed_traces = connection.execute(
                    "SELECT t.attempt_id FROM specialist_trace_manifests t JOIN "
                    "specialist_missions m ON m.mission_id=t.mission_id "
                    "WHERE m.status='failed' ORDER BY t.created_unix",
                ).fetchall()
                assert len(accepted_traces) >= 2 and failed_traces
                connection.execute(
                    "UPDATE specialist_trace_manifests SET checkpoint_generation=0 "
                    "WHERE attempt_id=?", (accepted_traces[0]["attempt_id"],),
                )
                connection.execute(
                    "UPDATE specialist_trace_manifests SET checkpoint_generation=30 "
                    "WHERE attempt_id=?", (accepted_traces[-1]["attempt_id"],),
                )
                connection.execute(
                    "UPDATE specialist_trace_manifests SET checkpoint_generation=20 "
                    "WHERE attempt_id=?", (failed_traces[0]["attempt_id"],),
                )
            # Disposable world snapshots are not campaign checkpoints and do
            # not age traces. Only completed recovery boundaries advance the
            # authoritative monotonic generation.
            generation_before = store.checkpoint_generation(scope.match_id)
            for index in range(5):
                projection = worlds.load(scope, store.active_timeline_id(scope))
                assert projection is not None
                identity = WorldIdentity(**projection["identity"])
                worlds.snapshot(
                    scope, identity, journal_head_hash="0" * 64,
                    journal_sequence=index,
                    calculator_versions={"fixture": "1"},
                )
            assert store.checkpoint_generation(scope.match_id) == generation_before
            for generation in range(40):
                store.complete_checkpoint_generation(
                    scope.match_id, f"checkpoint-retention-{generation}",
                )
            gc = trace_store.gc(store, success_generations=10, failed_generations=25,
                                byte_ceiling=16 * 1024 * 1024)
            assert gc["ok"] and gc["removed"] >= 1
            warning = trace_store.gc(
                store, success_generations=10, failed_generations=25,
                byte_ceiling=1,
            )
            assert warning["warning"] == (
                "specialist_trace_byte_ceiling_blocked_by_protected_failed_or_recent_traces"
            )
            retained_before = warning["bytes_retained"]
            high = trace_store.gc(
                store, success_generations=0, failed_generations=0,
                byte_ceiling=1, high_retention=True,
            )
            assert high["removed"] == 0 and high["bytes_retained"] == retained_before
        finally:
            reference_server.shutdown()
            reference_server.server_close()
            reference_thread.join(2)
            smacx_reference.REFERENCE_URL = previous_reference_url
            for name, value in prior.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        print(json.dumps({"event": "pass", "payload": {
            "fresh_disposable_processes": True, "exact_one_mcp": True,
            "sovereign_state_isolated": True, "bounded_retry": True,
            "mcp_failure_retry_bounded": True,
            "schema_repair_bounded": True, "hard_timeout_kill": True,
            "hard_cancellation_kill": True, "hard_tool_budget": True,
            "hard_provider_token_budget": True,
            "orphan_reconciliation": True, "compressed_trace_manifest": True,
            "trace_hash_and_redaction": True, "trace_generation_retention": True,
            "trace_byte_ceiling_warning": True, "trace_high_retention": True,
            "bounded_parallelism": True, "fair_cross_seat_scheduling": True,
        }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
