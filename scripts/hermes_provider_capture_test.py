#!/usr/bin/env python3
"""Capture the provider request emitted by the derived Hermes image."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import gzip
from pathlib import Path
import os
import re
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from smacx_hermes import COMMUNICATION_MCP_TOOLS, GAMEPLAY_MCP_TOOLS, configure_profile
from smacx_prompt import compose_player_system_prompt, prompt_sha256
from smacx_diagnostic_summary import result_object


IMAGE = os.environ.get("SMACX_TEST_HARNESS_IMAGE", "smacx-agent-harness:dev")
ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError("captured-provider MCP fixture did not become ready")


def main() -> int:
    captured: list[dict] = []
    mcp_port = _free_port()
    mcp_container = "smacx-provider-capture-mcp-" + uuid.uuid4().hex[:12]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_arguments: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/runtime-context"):
                query = parse_qs(urlsplit(self.path).query)
                episode_id = query.get("episode_id", [""])[0]
                active = getattr(self.server, "active_episode", "")
                if active and active != episode_id:
                    self.send_error(409, "sovereign_invocation_already_active")
                    return
                self.server.active_episode = episode_id
                payload = {
                    "ok": True,
                    "runtime_context": {
                        "schema": "smacx.runtime-context.v1",
                        "identity": {
                            "match_id": self.server.match_id,
                            "perspective_id": self.server.perspective_id,
                        },
                        "episode": {
                            "episode_id": episode_id,
                            "episode_mode": self.server.episode_mode,
                        },
                        "focus": {"focus_id": "focus-contract", "kind": "turn"},
                        "world": {
                            "world_anchor_id": "anchor-contract",
                            "world_revision": 1,
                            "world_epoch": 1,
                            "anchor": {"summary": "provider capture fixture"},
                            "net_deltas": [],
                        },
                        "attention": {"items": [], "remaining_count": 0},
                        "cognition": {},
                        "operations": [],
                    },
                }
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            payload = {"object": "list", "data": [{
                "id": "Qwen/Qwen3.8-27B", "object": "model", "owned_by": "contract",
            }]}
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            captured.append({"path": self.path, "request": request})
            if self.path.endswith("/episode-ended"):
                assert request["episode_id"] == self.server.active_episode
                self.server.active_episode = ""
            payload = {
                "id": "capture-response", "object": "chat.completion",
                "created": 0, "model": "Qwen/Qwen3.8-27B",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "capture complete"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            if self.path.endswith("/chat/completions") and getattr(self.server, "inject_unknown", False) and self.server.unknown_emitted and not getattr(self.server, "direct_emitted", False):
                self.server.direct_emitted = True
                payload["choices"][0].update(message={"role": "assistant", "content": None,
                    "tool_calls": [{"id": "call-direct-guard", "type": "function", "function": {
                        "name": "mcp__smacx__smac_attention_ack",
                        "arguments": '{"attention_lease_id":"missing-contract-lease","through_cursor":1}'}}]},
                    finish_reason="tool_calls")
            elif self.path.endswith("/chat/completions") and getattr(self.server, "inject_unknown", False) and self.server.unknown_emitted and not getattr(self.server, "length_emitted", False):
                self.server.length_emitted = True
                payload["choices"][0].update(message={"role": "assistant", "content": "Partial response before truncation."}, finish_reason="length")
            elif self.path.endswith("/chat/completions") and getattr(self.server, "inject_unknown", False) and not self.server.unknown_emitted:
                self.server.unknown_emitted = True
                payload["choices"][0].update(message={"role": "assistant", "content": None,
                    "tool_calls": [{"id": "call-unknown-validation", "type": "function", "function": {
                        "name": "unknown_diagnostic_tool", "arguments": '{"purpose":"validation fixture"}'}}]},
                    finish_reason="tool_calls")
            if request.get("stream"):
                delta = dict(payload["choices"][0]["message"])
                if delta.get("tool_calls"):
                    delta["tool_calls"] = [{"index": index, **call} for index, call in enumerate(delta["tool_calls"])]
                events = [{
                    "id": "capture-response", "object": "chat.completion.chunk",
                    "created": 0, "model": "Qwen/Qwen3.8-27B",
                    "choices": [{
                        "index": 0,
                        "delta": delta,
                        "finish_reason": None,
                    }],
                }, {
                    "id": "capture-response", "object": "chat.completion.chunk",
                    "created": 0, "model": "Qwen/Qwen3.8-27B",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": payload["choices"][0]["finish_reason"]}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }]
                data = ("".join(f"data: {json.dumps(event)}\n\n" for event in events)
                        + "data: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            else:
                data = json.dumps(payload).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        launched = subprocess.run([
            "docker", "run", "-d", "--rm", "--network", "host",
            "--name", mcp_container,
            "-v", f"{ROOT}:/repo:ro",
            "-e", "SMACX_MANAGED_ATTACHED=1",
            "-e", "SMACX_AGENT_MATCH_ID=match-provider-capture",
            "-e", "SMACX_AGENT_SESSION_ID=session-provider-capture",
            "-e", "SMACX_AGENT_ID=agent-provider-capture",
            "-e", "SMACX_PERSPECTIVE_ID=perspective-provider-capture",
            "--entrypoint", "/bin/bash", IMAGE, "-lc",
            "PYTHONPATH=/repo/src exec /opt/hermes/.venv/bin/python -c "
            + repr(
                "from smacx_mcp import mcp; "
                f"mcp.run('streamable-http',host='127.0.0.1',port={mcp_port},"
                "streamable_http_path='/mcp',json_response=True,stateless_http=True)"
            ),
        ], text=True, capture_output=True, check=False)
        if launched.returncode != 0:
            raise AssertionError(f"could not launch MCP capture fixture: {launched.stderr}")
        _wait_port(mcp_port)
        with tempfile.TemporaryDirectory(prefix="smacx-hermes-capture-") as temporary:
            root = Path(temporary)
            captured_tool_names: dict[str, set[str]] = {}
            for reasoning_effort, episode_mode in (
                ("low", "gameplay"), ("medium", "gameplay"),
                ("high", "gameplay"), ("low", "communication"),
            ):
                suffix = reasoning_effort
                if episode_mode == "communication":
                    suffix += "-communication"
                agent_id = f"agent-provider-capture-{suffix}"
                match_id = f"match-provider-capture-{suffix}"
                profile_id = f"smacx-provider-capture-{suffix}"
                from smacx_doctrine import compile_doctrine
                from doctrine_content_contract_test import fixtures
                prompt = compose_player_system_prompt(
                    gameplay_doctrine=compile_doctrine(fixtures()["stock-blind"])["text"],
                    agent_name="Provider Capture Player", agent_id=agent_id,
                    match_id=match_id, match_name="Provider capture",
                    perspective_id=f"perspective-provider-capture-{suffix}", ruleset_id="smacx",
                    seat_index=0,
                )
                profile = configure_profile(
                    hermes_root=root, runtime_hermes_root=Path("/opt/data"),
                    agent_id=agent_id, agent_name="Provider Capture Player",
                    match_id=match_id, mcp_url=f"http://127.0.0.1:{mcp_port}/mcp",
                    provider_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                    model_id="Qwen/Qwen3.8-27B", profile_id=profile_id,
                    reasoning_effort=reasoning_effort, system_prompt=prompt,
                    generation_settings={
                        "preset": "qwen38-low",
                        "temperature": 1.0, "top_p": 0.95,
                        "presence_penalty": 0.0,
                        "top_k": 20, "min_p": 0.0, "repetition_penalty": 1.0,
                        "extra_parameters": {"chat_template_kwargs": {
                            "enable_thinking": True, "preserve_thinking": False,
                        }},
                    },
                )
                config_path = root / "profiles" / profile["profile_id"] / "config.yaml"
                config = json.loads(config_path.read_text(encoding="utf-8"))
                communication = config.get("mcp_servers", {}).get(
                    "smacx-communication", {})
                if config.get("platform_toolsets", {}).get("cli") != ["smacx"] or \
                        config.get("mcp_servers", {}).get("smacx") != {
                            "url": f"http://127.0.0.1:{mcp_port}/mcp", "enabled": True,
                            "tools": {"include": list(GAMEPLAY_MCP_TOOLS), "resources": False, "prompts": False},
                        } or set(communication.get("tools", {}).get("include", ())) != set(
                            COMMUNICATION_MCP_TOOLS):
                    raise AssertionError(f"managed gameplay tool boundary drifted: {config}")
                config["display"]["streaming"] = False
                config_path.write_text(json.dumps(config), encoding="utf-8")
                prompt_path = f"/opt/data/profiles/{profile['profile_id']}/SYSTEM.md"
                runtime_token = root / "runtime-context-token"
                runtime_token.write_text("provider-capture-token\n", encoding="utf-8")
                server.match_id = match_id
                server.perspective_id = f"perspective-provider-capture-{suffix}"
                server.episode_mode = episode_mode
                server.inject_unknown = suffix == "low"
                server.unknown_emitted = False
                before = len(captured)
                command = [
                    "docker", "run", "--rm", "--network", "host",
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "-v", f"{root}:/opt/data",
                    "-e", "HOME=/opt/data", "-e", "HERMES_HOME=/opt/data",
                    "-e", "SMACX_STRICT_SYSTEM_PROMPT=1",
                    "-e", "SMACX_DIAGNOSTICS_ENABLED=1",
                    "-e", "SMACX_DIAGNOSTICS_ROOT=/opt/data/diagnostics",
                    "-e", f"SMACX_SYSTEM_PROMPT_FILE={prompt_path}",
                    "-e", f"SMACX_SYSTEM_PROMPT_SHA256={prompt_sha256(prompt)}",
                    "-e", f"SMACX_RUNTIME_CONTEXT_URL=http://127.0.0.1:{server.server_port}/runtime-context",
                    "-e", "SMACX_RUNTIME_CONTEXT_TOKEN_FILE=/opt/data/runtime-context-token",
                    "-e", f"SMACX_AGENT_MATCH_ID={match_id}",
                    "-e", f"SMACX_AGENT_ID={agent_id}",
                    "-e", f"SMACX_PERSPECTIVE_ID=perspective-provider-capture-{suffix}",
                    "-e", f"SMACX_EPISODE_MODE={episode_mode}",
                    "-e", "SMACX_CONTEXT_LENGTH=65536",
                    "--entrypoint", "/opt/hermes/.venv/bin/hermes", IMAGE,
                    "-p", profile["profile_id"], "chat", "--continue", match_id,
                    "--create-if-missing", "--in", profile["workspace"],
                    "--toolsets", "smacx-communication" if episode_mode == "communication"
                    else "smacx",
                    "--reasoning", reasoning_effort,
                    "--max-turns", "5", "--query",
                    "Begin or resume this managed match now. Follow the system contract's opening "
                    "briefing protocol, then continue autonomous play until the operator stops the run "
                    "or a semantic capability gap is reported.", "--quiet",
                ]
                completed = subprocess.run(
                    command, text=True, capture_output=True, timeout=90, check=False,
                )
                if completed.returncode != 0:
                    raise AssertionError(
                        f"derived Hermes capture failed ({completed.returncode}):\n"
                        f"stdout={completed.stdout}\nstderr={completed.stderr}"
                    )
                # Recompile the exact prompt while retaining the real Hermes
                # SQLite session, then exercise its actual --continue path.
                # Hermes may reuse its persisted system row instead of calling
                # build_system_prompt again; the wire hook must still replace it.
                if suffix == "low":
                    revised_prompt = prompt + "\nManaged prompt revision acceptance marker."
                    (root / "profiles" / profile["profile_id"] / "SYSTEM.md").write_text(
                        revised_prompt, encoding="utf-8")
                    resumed_command = [
                        f"SMACX_SYSTEM_PROMPT_SHA256={prompt_sha256(revised_prompt)}"
                        if part.startswith("SMACX_SYSTEM_PROMPT_SHA256=") else part
                        for part in command
                    ]
                    # Exercise real resumed SQLite history, not just the wire
                    # sanitizer. This old result exceeds the raw preflight cap.
                    database = root / "profiles" / profile["profile_id"] / "state.db"
                    assert database.is_file(), list(root.rglob("*.db"))
                    oversized_result = "DISPOSABLE_PREFLIGHT_MARKER " + "old state " * 150000
                    with sqlite3.connect(database) as db:
                        old_result = db.execute(
                            "SELECT id FROM messages WHERE role='tool' ORDER BY id LIMIT 1"
                        ).fetchone()
                        assert old_result
                        db.execute("UPDATE messages SET content=? WHERE id=?",
                                   (oversized_result, old_result[0]))
                    resume_before = len(captured)
                    resumed = subprocess.run(resumed_command, text=True, capture_output=True,
                                             timeout=90, check=False)
                    assert resumed.returncode == 0, resumed.stderr
                    resumed_requests = [item["request"] for item in captured[resume_before:]
                                        if item["path"].endswith("/chat/completions")]
                    assert len(resumed_requests) == 1
                    resumed_messages = resumed_requests[0]["messages"]
                    assert [m["content"] for m in resumed_messages if m["role"] == "system"] == [revised_prompt]
                    assert any(m.get("content") == "capture complete" for m in resumed_messages), "resume discarded prior conversation"
                    assert "DISPOSABLE_PREFLIGHT_MARKER" not in json.dumps(resumed_messages)
                    assert "Compacting context" not in resumed.stdout + resumed.stderr
                    with sqlite3.connect(database) as db:
                        assert db.execute("SELECT content FROM messages WHERE id=?",
                                          (old_result[0],)).fetchone()[0] == oversized_result
                    captured_resume = resumed_requests[0]
                    # The normal checks below still inspect the first request;
                    # diagnostics must contain both exact receiving-side bodies.
                else:
                    captured_resume = None
                requests = [item["request"] for item in captured[before:]
                            if item["path"].endswith("/chat/completions")]
                if len(requests) != (5 if captured_resume else 1):
                    raise AssertionError(f"unexpected provider request count: {len(requests)} for {suffix}")
                request = requests[0]
                diagnostic_events=[]
                for path in (root/"diagnostics"/match_id).glob("*.jsonl.gz"):
                    with gzip.open(path,"rt") as stream:
                        diagnostic_events.extend(json.loads(line) for line in stream)
                submitted=[row for row in diagnostic_events if row["kind"]=="provider_request_submitted"]
                assert len(submitted)==(5 if captured_resume else 1), [row["kind"] for row in diagnostic_events]
                submitted.sort(key=lambda row: row["recorded_unix"])
                assert submitted[0]["payload"]["body"]==request
                if captured_resume:
                    assert submitted[4]["payload"]["body"]==captured_resume
                    direct_results = [row for row in diagnostic_events if row["kind"] == "tool_returned"
                                      and row["correlation"].get("call_id") == "call-direct-guard"]
                    assert len(direct_results) == 1
                    direct_result = result_object(direct_results[0]["payload"]["content"])
                    assert direct_result.get("ok") is False and direct_result.get("error"), direct_result
                    releases = [item["request"] for item in captured[before:]
                                if item["path"].endswith("/episode-ended")]
                    assert any(item["committed"] is False for item in releases), releases
                    rejected = [row for row in diagnostic_events if row["kind"] == "tool_validation_rejected"]
                    assert len(rejected) == 1 and rejected[0]["correlation"]["call_id"] == "call-unknown-validation"
                    assert rejected[0]["payload"]["arguments"] == {"purpose": "validation fixture"}
                    assert rejected[0]["payload"]["native_action_executed"] is False
                    assert rejected[0]["correlation"]["request_id"] == submitted[0]["correlation"]["request_id"]
                    assert not any(row["kind"] == "tool_requested" and row["payload"].get("managed_name") == "unknown_diagnostic_tool" for row in diagnostic_events)
                assert submitted[0]["correlation"].get("runtime_context_sha256")
                responses=[row for row in diagnostic_events if row["kind"] in
                           {"provider_response_body","provider_response_stream"}]
                assert responses and any(row["correlation"]==submitted[0]["correlation"] for row in responses)
                messages = request.get("messages")
                systems = [item.get("content") for item in messages or []
                           if item.get("role") == "system"]
                if systems != [prompt]:
                    raise AssertionError(f"provider system message was not exact: {systems}")
                runtime_rows = [
                    (index, item) for index, item in enumerate(messages or [])
                    if "<SMACX_RUNTIME_CONTEXT" in str(item.get("content", ""))
                ]
                if len(runtime_rows) != 1 or runtime_rows[0][0] != len(messages) - 1:
                    raise AssertionError(
                        f"runtime context was not a single terminal tail augmentation: {messages}"
                    )
                if runtime_rows[0][1].get("role") != "user":
                    raise AssertionError(f"initial runtime context did not augment user tail: {messages}")
                tools = request.get("tools") or []
                names = {
                    str(item.get("function", {}).get("name") or "")
                    for item in tools if isinstance(item, dict)
                }
                assert not names & {"tool_search", "tool_describe", "tool_call"}, names
                assert all(name.startswith("mcp__") for name in names), names
                discovered = {name.rsplit("__", 1)[-1] for name in names}
                assert all(item["function"].get("parameters", {}).get("type") == "object"
                           for item in tools), "direct parameter schemas missing"
                if captured_resume:
                    assert captured_resume["tools"] == request["tools"], "resume removed tool schemas"
                captured_tool_names[episode_mode] = discovered
                if episode_mode == "gameplay":
                    assert discovered == set(GAMEPLAY_MCP_TOOLS) and len(discovered) == 15, discovered
                    if not {"smac_decision", "smac_execute_choice"} <= discovered:
                        raise AssertionError(
                            f"gameplay provider catalog lacks guarded actions: {discovered}"
                        )
                else:
                    expected = set(COMMUNICATION_MCP_TOOLS)
                    if discovered != expected:
                        raise AssertionError(
                            f"communication provider registry drifted: {discovered} != {expected}"
                        )
                    forbidden = {"smac_decision", "smac_choices", "smac_command",
                                 "smac_execute_choice", "smac_wait", "smac_turn_handoff"}
                    if discovered & forbidden:
                        raise AssertionError(
                            f"communication provider advertised gameplay authority: {discovered}"
                        )
                serialized = json.dumps(request)
                for forbidden in ("Hermes Agent", "FORBIDDEN UPSTREAM ADDITION", "AGENTS.md"):
                    if forbidden in serialized:
                        raise AssertionError(f"upstream prompt material reached provider: {forbidden}")
                expected_generation = {
                    "temperature": 1.0, "top_p": 0.95, "top_k": 20,
                    "min_p": 0.0, "presence_penalty": 0.0,
                    "repetition_penalty": 1.0,
                    "chat_template_kwargs": {
                        "enable_thinking": True, "preserve_thinking": False,
                    },
                }
                for key, expected in expected_generation.items():
                    if request.get(key) != expected:
                        raise AssertionError(
                            f"generation setting {key} did not reach provider: {request}"
                        )
                reasoning = request.get("reasoning_effort")
                nested_reasoning = request.get("reasoning")
                if reasoning != reasoning_effort and not (
                    isinstance(nested_reasoning, dict) and
                    nested_reasoning.get("effort") == reasoning_effort
                ):
                    raise AssertionError(
                        f"{reasoning_effort} reasoning did not reach provider: {request}"
                    )
    finally:
        subprocess.run(["docker", "rm", "-f", mcp_container],
                       text=True, capture_output=True, check=False)
        server.shutdown()
        server.server_close()
        thread.join(2)
    print(json.dumps({
        "event": "pass",
        "payload": {
            "real_derived_image": True,
            "provider_request_captured": True,
            "production_diagnostic_matches_received_request": True,
            "production_response_capture_correlated": True,
            "exact_single_system_message": True,
            "recompiled_prompt_replaces_saved_prompt_on_real_resume": True,
            "resume_preserves_conversation": True,
            "oversized_durable_history_resume_uses_semantic_preflight": True,
            "unknown_tool_rejection_captured_before_executor": True,
            "truncated_response_releases_lease_before_real_hermes_continuation": True,
            "request_only_runtime_tail": True,
            "upstream_scaffold_absent": True,
            "generation_settings_reached_provider": True,
            "low_medium_high_reasoning_reached_provider": True,
            "managed_gameplay_prompt_and_tool_boundary": True,
            "captured_gameplay_and_communication_tool_lists": True,
            "full_direct_schemas_present_initial_and_resumed": True,
            "direct_call_reaches_mcp_and_returns_guard_error": True,
            "communication_provider_has_no_gameplay_mutation_schema": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
