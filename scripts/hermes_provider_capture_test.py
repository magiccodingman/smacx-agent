#!/usr/bin/env python3
"""Capture the provider request emitted by the derived Hermes image."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import os
import subprocess
import tempfile
import threading

from smacx_hermes import configure_profile
from smacx_prompt import compose_player_system_prompt, prompt_sha256


IMAGE = "smacx-agent-harness:dev"


def main() -> int:
    captured: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_arguments: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
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
        with tempfile.TemporaryDirectory(prefix="smacx-hermes-capture-") as temporary:
            root = Path(temporary)
            for reasoning_effort in ("low", "medium", "high"):
                suffix = reasoning_effort
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
                    match_id=match_id, mcp_url="http://127.0.0.1:9/mcp",
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
                if config.get("platform_toolsets", {}).get("cli") != ["smacx"] or \
                        config.get("mcp_servers", {}).get("smacx") != {
                            "url": "http://127.0.0.1:9/mcp", "enabled": True,
                        }:
                    raise AssertionError(f"managed gameplay tool boundary drifted: {config}")
                config["platform_toolsets"]["cli"] = []
                config["mcp_servers"] = {}
                config["display"]["streaming"] = False
                config_path.write_text(json.dumps(config), encoding="utf-8")
                prompt_path = f"/opt/data/profiles/{profile['profile_id']}/SYSTEM.md"
                before = len(captured)
                command = [
                    "docker", "run", "--rm", "--network", "host",
                    "--user", f"{os.getuid()}:{os.getgid()}",
                    "-v", f"{root}:/opt/data",
                    "-e", "HOME=/opt/data", "-e", "HERMES_HOME=/opt/data",
                    "-e", "SMACX_STRICT_SYSTEM_PROMPT=1",
                    "-e", f"SMACX_SYSTEM_PROMPT_FILE={prompt_path}",
                    "-e", f"SMACX_SYSTEM_PROMPT_SHA256={prompt_sha256(prompt)}",
                    "--entrypoint", "/opt/hermes/.venv/bin/hermes", IMAGE,
                    "-p", profile["profile_id"], "chat", "--continue", match_id,
                    "--create-if-missing", "--in", profile["workspace"],
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
        server.shutdown()
        server.server_close()
        thread.join(2)
    print(json.dumps({
        "event": "pass",
        "payload": {
            "real_derived_image": True,
            "provider_request_captured": True,
            "exact_single_system_message": True,
            "upstream_scaffold_absent": True,
            "generation_settings_reached_provider": True,
            "low_medium_high_reasoning_reached_provider": True,
            "managed_gameplay_prompt_and_tool_boundary": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
