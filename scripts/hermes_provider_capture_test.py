#!/usr/bin/env python3
"""Capture the provider request emitted by the derived Hermes image."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import os
import re
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from smacx_hermes import COMMUNICATION_MCP_TOOLS, configure_profile
from smacx_prompt import compose_player_system_prompt, prompt_sha256


IMAGE = "smacx-agent-harness:dev"
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
            if request.get("stream"):
                events = [{
                    "id": "capture-response", "object": "chat.completion.chunk",
                    "created": 0, "model": "Qwen/Qwen3.8-27B",
                    "choices": [{
                        "index": 0,
                        "delta": {"role": "assistant", "content": "capture complete"},
                        "finish_reason": None,
                    }],
                }, {
                    "id": "capture-response", "object": "chat.completion.chunk",
                    "created": 0, "model": "Qwen/Qwen3.8-27B",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
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
                prompt = compose_player_system_prompt(
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
                before = len(captured)
                command = [
                    "docker", "run", "--rm", "--network", "host",
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "-v", f"{root}:/opt/data",
                    "-e", "HOME=/opt/data", "-e", "HERMES_HOME=/opt/data",
                    "-e", "SMACX_STRICT_SYSTEM_PROMPT=1",
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
                    "--max-turns", "1", "--query",
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
                requests = [item["request"] for item in captured[before:]
                            if item["path"].endswith("/chat/completions")]
                if len(requests) != 1:
                    raise AssertionError(f"expected one provider request, captured={captured[before:]}")
                request = requests[0]
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
                # Hermes keeps deferred MCP schemas out of the permanent
                # provider footprint. The authoritative provider-visible
                # registry is therefore the catalog embedded in tool_search,
                # followed by on-demand tool_describe/tool_call. Inspect that
                # real wire representation rather than merely trusting config.
                search = next((item.get("function", {}) for item in tools
                               if item.get("function", {}).get("name") == "tool_search"), {})
                catalog = str(search.get("description") or "")
                discovered = set(re.findall(
                    r"mcp__[^\s:]+__(smac_[A-Za-z0-9_]+)", catalog,
                ))
                captured_tool_names[episode_mode] = discovered
                if episode_mode == "gameplay":
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
            "exact_single_system_message": True,
            "request_only_runtime_tail": True,
            "upstream_scaffold_absent": True,
            "generation_settings_reached_provider": True,
            "low_medium_high_reasoning_reached_provider": True,
            "managed_gameplay_prompt_and_tool_boundary": True,
            "captured_gameplay_and_communication_tool_lists": True,
            "communication_provider_has_no_gameplay_mutation_schema": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
