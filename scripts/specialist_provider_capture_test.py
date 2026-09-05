#!/usr/bin/env python3
"""Exercise real Hermes specialist loops against a deterministic captured provider.

No generated content is persisted by the outer report. The test proves the
wire/tool boundary, iterative MCP use, process freshness, and transcript
isolation using the actual derived Hermes image.
"""

from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from typing import Any


IMAGE = os.environ.get("SMACX_HERMES_IMAGE", "smacx-agent-harness:rebuild")


def _seed_and_run(root: Path, base_url: str, reference_url: str) -> int:
    import smacx_reference
    from smacx_specialist_supervisor import SpecialistSupervisor
    from smacx_specialists import SpecialistService
    from smacx_store import MemoryScope, SmacxStore
    from smacx_world_model import PerspectiveProjector
    from smacx_world_store import WorldStore
    from smacx_world_types import WorldIdentity, canonical_json

    # Commission freezes the corpus before the disposable process starts, so
    # bind the revision export endpoint in this isolated test process too.
    smacx_reference.REFERENCE_URL = reference_url.rstrip("/")

    store = SmacxStore(root / "state.sqlite3")
    store.ensure_agent("agent-capture", "PRIVATE SOVEREIGN PERSONALITY")
    store.create_match(match_id="match-capture", display_name="Capture", mode="solo")
    store.create_perspective(
        "match-capture", "agent-capture", perspective_id="perspective-capture",
    )
    scope = MemoryScope("match-capture", "agent-capture", "perspective-capture")
    identity = WorldIdentity(
        scope.match_id, scope.perspective_id, store.active_timeline_id(scope), "world-capture",
    )
    projected = PerspectiveProjector(identity).project({
        "turn": 7,
        "map": {"width": 8, "height": 4, "horizontal_wrap": False},
        "tiles": [
            {"tile_id": 0, "x": 0, "y": 0, "visible_now": True, "terrain": "land"},
            {"tile_id": 1, "x": 2, "y": 0, "visible_now": True, "terrain": "land"},
        ],
        "bases": [{"id": 1, "base_ref": "base-home", "tile_id": 0,
                   "owned": True, "name": "Home"}],
        "units": [], "factions": [], "global": [],
    }, observation_sequence=7)
    worlds = WorldStore(store, root / "world-snapshots")
    worlds.replace_projection(
        scope, identity, projected["objects"], observation_cursor=7,
        action_revision="capture", continuity="complete", journal_head_hash="0" * 64,
    )
    now = __import__("time").time()
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO model_providers(provider_id,display_name,provider_kind,base_url,"
            "default_model_id,status,metadata_json,created_unix,updated_unix) "
            "VALUES('provider-capture','Capture','openai_compatible',?,'capture-model',"
            "'healthy','{}',?,?)", (base_url, now, now),
        )
        connection.execute(
            "INSERT INTO provider_models(provider_id,model_id,display_name,context_length,"
            "capabilities_json,raw_metadata_json,discovered_unix) "
            "VALUES('provider-capture','capture-model','Capture model',65536,'{}','{}',?)",
            (now,),
        )
        connection.execute(
            "INSERT INTO control_settings(setting_key,value_json,updated_unix) "
            "VALUES('specialist.profile',?,?)", (canonical_json({
                "profile_id": "profile-capture", "provider_id": "provider-capture",
                "model_id": "capture-model", "reasoning_effort": "none",
                "generation_settings": {
                    "temperature": 0,
                    "extra_parameters": {"chat_template_kwargs": {
                        "enable_thinking": False, "preserve_thinking": False,
                    }},
                },
            }), now),
        )
        connection.execute(
            "INSERT INTO control_settings(setting_key,value_json,updated_unix) "
            "VALUES('specialist.policy',?,?)", (canonical_json({
                "installation_concurrency": 1, "seat_concurrency": 1,
                "automatic_retries": 0, "schema_repairs": 0,
                "synthesis": {"tool_budget": 4, "provider_call_budget": 4,
                              "provider_token_budget": 96000, "wall_seconds": 45},
                "investigation": {"tool_budget": 8, "provider_call_budget": 8,
                                  "provider_token_budget": 96000, "wall_seconds": 45},
            }), now),
        )
    service = SpecialistService(store, worlds, scope)
    mission_ids: list[str] = []
    homes: list[str] = []
    missions = [
        ("world", "Compare the local base and its surrounding known area.", None),
        ("world", "Independently inspect the same local theater.", None),
        ("reference", "Investigate amphibious assault mechanics across transports, "
         "artillery, air refueling, sensors, defensive facilities, and Psi combat.",
         "fixture-revision"),
    ]
    for faculty, objective, corpus_revision in missions:
        mission = service.commission(faculty=faculty, objective=objective,
                                     subject_refs=["base-home"] if faculty == "world" else [],
                                     corpus_revision=corpus_revision,
                                     execution_class="investigation")
        mission_ids.append(str(mission["mission_id"]))
        supervisor = SpecialistSupervisor(
            database=store.path, secret_root=root / "secrets",
            snapshot_root=worlds.root, trace_root=root / "specialist-traces",
            reference_url=reference_url, poll_seconds=0.1,
        )
        supervisor.run(once=True)
        supervisor.shutdown()
        result = service.get(str(mission["mission_id"]))
        if result.get("status") != "accepted":
            with store._connect() as connection:
                diagnostic = [dict(row) for row in connection.execute(
                    "SELECT status,failure_reason,provider_calls,tool_calls,provider_tokens,"
                    "trace_path FROM specialist_attempts WHERE mission_id=?",
                    (mission["mission_id"],),
                ).fetchall()]
            trace_text = ""
            trace_path = next((str(row.get("trace_path") or "") for row in diagnostic
                               if row.get("trace_path")), "")
            if trace_path:
                trace_text = subprocess.run(
                    ["zstdcat", trace_path], text=True, capture_output=True, check=False,
                ).stdout[-8000:]
            raise AssertionError(
                f"real Hermes specialist was not accepted: {result}; "
                f"attempts={diagnostic}; trace={trace_text}"
            )
    with store._connect() as connection:
        attempts = [dict(row) for row in connection.execute(
            "SELECT mission_id,tool_calls,provider_calls,provider_tokens,trace_path "
            "FROM specialist_attempts ORDER BY started_unix"
        ).fetchall()]
    if len(attempts) != 3 or [int(row["tool_calls"]) for row in attempts] != [3, 3, 4]:
        raise AssertionError(f"specialists did not perform iterative queries: {attempts}")
    if any(not row["trace_path"] or not Path(str(row["trace_path"])).is_file()
           for row in attempts):
        raise AssertionError("real specialist traces were not retained")
    for row in attempts:
        decoded = subprocess.run(
            ["zstd", "-q", "-d", "-c", str(row["trace_path"])],
            text=True, capture_output=True, check=True,
        ).stdout
        trace_rows = [json.loads(line) for line in decoded.splitlines() if line.strip()]
        envelope = next((item for item in trace_rows
                         if item.get("kind") == "mission_envelope"), None)
        outcome = next((item for item in trace_rows
                        if item.get("kind") == "attempt_outcome"), None)
        validated = next((item for item in trace_rows
                          if item.get("kind") == "validated_result"), None)
        exchanges = outcome.get("provider_exchanges", []) if outcome else []
        if not envelope or not validated or len(exchanges) < 3:
            raise AssertionError("trace omitted mission/provider trajectory/validated result")
        if not envelope["mission"].get("system_prompt_hash"):
            raise AssertionError("trace omitted exact specialist prompt hash")
        exchange_text = json.dumps(exchanges, separators=(",", ":"))
        if '"tool_calls"' not in exchange_text or '"content"' not in exchange_text:
            raise AssertionError("trace omitted assistant tool/reasoning/final trajectory")
        if any(secret in decoded for secret in (
                "PRIVATE SOVEREIGN PERSONALITY", "Bearer test", "sk-test")):
            raise AssertionError("trace leaked sovereign or credential material")
    print(json.dumps({
        "passed": True, "mission_ids": mission_ids,
        "tool_calls": [int(row["tool_calls"]) for row in attempts],
        "provider_calls": [int(row["provider_calls"]) for row in attempts],
        "provider_tokens": [int(row["provider_tokens"]) for row in attempts],
    }, separators=(",", ":")))
    return 0


def _completion(request: dict[str, Any], mission: dict[str, Any], call_count: int) -> dict[str, Any]:
    mission_id = str(mission.get("mission_id") or "unknown-mission")
    faculty = str(mission.get("faculty") or "world")
    query_plan = (
        [
            {"action": "search", "query": "amphibious assault transport artillery"},
            {"action": "get", "document_id": "transport-doc"},
            {"action": "search", "query": "air refuel sensors defense psi native"},
            {"action": "get", "document_id": "combined-doc"},
        ] if faculty == "reference" else [
            {"mode": "base", "subject_refs": ["invented-world-ref"]},
            {"mode": "overview"},
            {"mode": "area", "origin_ref": "base-home", "radius": 2},
        ]
    )
    if call_count < len(query_plan):
        arguments = query_plan[call_count]
        tool_name = str((request.get("tools") or [{}])[0].get("function", {}).get(
            "name") or "world_query")
        return {
            "id": f"capture-{mission_id}-{call_count}", "object": "chat.completion",
            "created": 0, "model": request.get("model"),
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "reasoning_content": f"specialist-reasoning-{mission_id}-{call_count}",
                "tool_calls": [{"id": f"call-{mission_id}-{call_count}",
                                "type": "function", "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(arguments, separators=(",", ":")),
                                }}],
            }}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        }
    citations: list[str] = []
    for message in request.get("messages") or []:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(str(message.get("content") or "{}"))
        except json.JSONDecodeError:
            continue
        citations.extend(str(item) for item in payload.get("evidence_refs") or [])
        if any(key in payload for key in (
                "dependency_hash", "dependency_refs", "valid_while", "cache")):
            raise AssertionError("specialist provider received internal world dependency metadata")
    citation = next((item for item in citations if item.startswith("world-query:")),
                    citations[0] if citations else "")
    result = {
        "mission_id": mission_id,
        "answer": ("The cross-mechanic amphibious evidence was retrieved through several "
                   "terminology-following calls." if faculty == "reference" else
                   "Home and its local known area were inspected through two bounded "
                   "deterministic queries."),
        "claims": ([{"claim": "The local theater was queried mechanically.",
                     "citations": [citation], "epistemic_status": "derived"}]
                   if citation else []),
        "limitations": ["Unknown terrain was not inferred."],
        "unresolved_questions": [],
    }
    return {
        "id": f"capture-{mission_id}-final", "object": "chat.completion",
        "created": 0, "model": request.get("model"),
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": json.dumps(result),
                                 "reasoning_content":
                                     f"specialist-reasoning-{mission_id}-final"}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside-root")
    parser.add_argument("--base-url")
    parser.add_argument("--reference-url")
    args = parser.parse_args()
    if args.inside_root:
        return _seed_and_run(Path(args.inside_root), str(args.base_url),
                             str(args.reference_url))

    captured: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/status":
                payload: dict[str, Any] = {"ok": True, "state": {
                    "revision": "fixture-revision",
                }}
            elif self.path == "/api/export/fixture-revision":
                payload = {
                    "revision": "fixture-revision",
                    "collections": [{"collection_id": "mechanics",
                                     "title": "Mechanics", "path": "Mechanics"}],
                    "documents": [{
                        "document_id": "transport-doc", "title": "Transport mechanics",
                        "description": "Movement and embarkation.",
                        "collection_id": "mechanics", "collection_path": "Mechanics",
                        "source_hash": "transport-fixture",
                        "body": "Transports carry ground units. Boarding and disembarking are guarded actions.",
                    }, {
                        "document_id": "combined-doc", "title": "Combat modifiers",
                        "description": "Air, sensors, facilities, and psi.",
                        "collection_id": "mechanics", "collection_path": "Mechanics",
                        "source_hash": "combat-fixture",
                        "body": "Air units require range and refueling. Sensors and defensive facilities modify engagements. Psi combat uses morale rules.",
                    }],
                }
            elif self.path.startswith("/api/documents/"):
                document_id = self.path.rsplit("/", 1)[-1]
                payload = {
                    "document_id": document_id, "title": "Combined mechanics",
                    "content": ("Transports carry ground units. Artillery has distinct combat. "
                                "Air units require range and refueling. Sensors and defensive "
                                "facilities modify engagements. Psi combat uses morale rules."),
                }
            else:
                payload = {"object": "list", "data": [{
                    "id": "capture-model", "object": "model", "owned_by": "contract",
                    "context_length": 65536,
                }]}
            body = json.dumps(payload).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if self.path == "/api/search":
                payload = {
                    "ok": True, "results": [{
                        "document_id": "transport-doc", "title": "Transport mechanics",
                        "content": "Transport terminology links to artillery and air refueling.",
                    }, {
                        "document_id": "combined-doc", "title": "Combat modifiers",
                        "content": "Sensors, defensive facilities, and Psi require separate rules.",
                    }],
                }
                body = json.dumps(payload).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body)
                return
            captured.append(request)
            messages = request.get("messages") or []
            mission = next((json.loads(item["content"]) for item in messages
                            if item.get("role") == "user" and
                            str(item.get("content") or "").startswith("{")), {})
            calls = sum(1 for item in messages if item.get("role") == "tool")
            completion = _completion(request, mission, calls)
            if not request.get("stream"):
                body = json.dumps(completion).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers()
                self.wfile.write(body)
                return
            choice = completion["choices"][0]
            message = choice["message"]
            first = {
                "id": completion["id"], "object": "chat.completion.chunk",
                "created": 0, "model": completion["model"],
                "choices": [{"index": 0, "delta": {
                    "role": "assistant",
                    "reasoning_content": message.get("reasoning_content") or "",
                    **({"tool_calls": message["tool_calls"]}
                       if message.get("tool_calls") else
                       {"content": message.get("content") or ""}),
                }, "finish_reason": None}],
            }
            finish = {
                "id": completion["id"], "object": "chat.completion.chunk",
                "created": 0, "model": completion["model"],
                "choices": [{"index": 0, "delta": {},
                             "finish_reason": choice["finish_reason"]}],
            }
            usage = {
                "id": completion["id"], "object": "chat.completion.chunk",
                "created": 0, "model": completion["model"], "choices": [],
                "usage": completion["usage"],
            }
            body = b"".join(
                b"data: " + json.dumps(item).encode() + b"\n\n"
                for item in (first, finish, usage)
            ) + b"data: [DONE]\n\n"
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    repository = Path(__file__).resolve().parent.parent
    try:
        with tempfile.TemporaryDirectory(prefix="smacx-specialist-wire-") as raw:
            completed = subprocess.run([
                "docker", "run", "--rm", "--network", "host",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{repository}:/work:ro", "-v", f"{raw}:/state",
                "-e", "PYTHONPATH=/work/src", "--entrypoint", "/opt/hermes/.venv/bin/python",
                IMAGE, "/work/scripts/specialist_provider_capture_test.py",
                "--inside-root", "/state",
                "--base-url", f"http://127.0.0.1:{server.server_port}/v1",
                "--reference-url", f"http://127.0.0.1:{server.server_port}",
            ], text=True, capture_output=True, timeout=180, check=False)
            if completed.returncode:
                raise AssertionError(
                    f"real Hermes specialist capture failed; captured={captured}:\n"
                    f"{completed.stdout}\n{completed.stderr}"
                )
    finally:
        server.shutdown(); server.server_close(); thread.join(2)

    if len(captured) != 13:
        raise AssertionError(f"expected thirteen provider calls, got {len(captured)}")
    seen_missions: list[str] = []
    mission_calls: dict[str, int] = {}
    stable_prefixes: dict[str, set[str]] = {"world": set(), "reference": set()}
    for index, request in enumerate(captured):
        tools = request.get("tools") or []
        names = [item.get("function", {}).get("name") for item in tools]
        mission = next((json.loads(item["content"]) for item in request.get("messages") or []
                        if item.get("role") == "user" and
                        str(item.get("content") or "").startswith("{")), {})
        faculty = str(mission.get("faculty") or "")
        if len(names) != 1 or not names[0].endswith(f"__{faculty}_query"):
            raise AssertionError(f"specialist provider tool surface was not exact: {names}")
        serialized = json.dumps(request)
        for forbidden in ("PRIVATE SOVEREIGN PERSONALITY", "SMACX_RUNTIME_CONTEXT",
                          "smac_execute_choice", "delegate_task"):
            if forbidden in serialized:
                raise AssertionError(f"specialist provider request leaked {forbidden}")
        if any(any(marker in name for marker in ("terminal", "file", "web", "memory",
                                                  "delegate", "smac_"))
               for name in names):
            raise AssertionError(f"specialist received a forbidden instrument: {names}")
        systems = [item.get("content") for item in request.get("messages") or []
                   if item.get("role") == "system"]
        expected_prompt = ("disposable SMACX mechanics researcher" if faculty == "reference"
                           else "disposable SMACX mechanical world analyst")
        if len(systems) != 1 or expected_prompt not in systems[0]:
            raise AssertionError("specialist system prompt was not exact and isolated")
        stable_prefixes[faculty].add(hashlib.sha256(json.dumps({
            "system": systems[0], "tools": tools,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        mission_id = str(mission.get("mission_id") or "")
        mission_call = mission_calls.get(mission_id, 0)
        reasoning_markers = [
            f"specialist-reasoning-{mission_id}-{prior}"
            for prior in range(mission_call)
        ]
        present_markers = [marker for marker in reasoning_markers if marker in serialized]
        permitted_markers = reasoning_markers[-1:] if reasoning_markers else []
        if any(marker not in permitted_markers for marker in present_markers):
            raise AssertionError(
                "specialist provider wire retained superseded reasoning: "
                f"call={mission_call} present={present_markers} permitted={permitted_markers}"
            )
        mission_calls[mission_id] = mission_call + 1
        if any(previous in serialized for previous in seen_missions if previous != mission_id):
            raise AssertionError("disposable mission inherited a prior mission transcript")
        if mission_id and mission_id not in seen_missions:
            seen_missions.append(mission_id)
    if any(len(values) != 1 for values in stable_prefixes.values()):
        raise AssertionError(
            f"specialist stable system/tool prefixes drifted: {stable_prefixes}"
        )
    print(json.dumps({"event": "pass", "payload": {
        "actual_hermes_loop": True, "iterative_world_queries": True,
        "iterative_reference_research": True,
        "exact_one_specialist_instrument": True, "no_sovereign_state": True,
        "trace_derived_citations": True, "sequential_process_state_isolation": True,
        "stable_child_prefixes": True,
        "compressed_provider_trajectory_traces": True,
        "failed_lookup_not_evidence_or_staleness": True,
        "captured_provider_calls": len(captured),
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
