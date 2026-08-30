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
                "id": "capture-model", "object": "model", "owned_by": "contract",
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
                "created": 0, "model": "capture-model",
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
                    "created": 0, "model": "capture-model",
                    "choices": [{
                        "index": 0,
                        "delta": {"role": "assistant", "content": "capture complete"},
                        "finish_reason": None,
                    }],
                }, {
                    "id": "capture-response", "object": "chat.completion.chunk",
                    "created": 0, "model": "capture-model",
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
            prompt = compose_player_system_prompt(
                agent_name="Provider Capture Player", agent_id="agent-provider-capture",
                match_id="match-provider-capture", match_name="Provider capture",
                perspective_id="perspective-provider-capture", ruleset_id="smacx",
                seat_index=0,
            )
            profile = configure_profile(
                hermes_root=root, runtime_hermes_root=Path("/opt/data"),
                agent_id="agent-provider-capture", agent_name="Provider Capture Player",
                match_id="match-provider-capture", mcp_url="http://127.0.0.1:9/mcp",
                provider_base_url=f"http://127.0.0.1:{server.server_port}/v1",
                model_id="capture-model", profile_id="smacx-provider-capture",
                reasoning_effort="none", system_prompt=prompt,
            )
            config_path = root / "profiles" / profile["profile_id"] / "config.yaml"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["platform_toolsets"]["cli"] = []
            config["mcp_servers"] = {}
            config["display"]["streaming"] = False
            config_path.write_text(json.dumps(config), encoding="utf-8")
            prompt_path = f"/opt/data/profiles/{profile['profile_id']}/SYSTEM.md"
            command = [
                "docker", "run", "--rm", "--network", "host",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{root}:/opt/data",
                "-e", "HOME=/opt/data", "-e", "HERMES_HOME=/opt/data",
                "-e", "SMACX_STRICT_SYSTEM_PROMPT=1",
                "-e", f"SMACX_SYSTEM_PROMPT_FILE={prompt_path}",
                "-e", f"SMACX_SYSTEM_PROMPT_SHA256={prompt_sha256(prompt)}",
                "--entrypoint", "/opt/hermes/.venv/bin/hermes", IMAGE,
                "-p", profile["profile_id"], "chat", "--continue", "match-provider-capture",
                "--create-if-missing", "--in", profile["workspace"], "--reasoning", "none",
                "--max-turns", "1", "--query", "Start signal only.", "--quiet",
            ]
            completed = subprocess.run(
                command, text=True, capture_output=True, timeout=90, check=False,
            )
            if completed.returncode != 0:
                raise AssertionError(
                    f"derived Hermes capture failed ({completed.returncode}):\n"
                    f"stdout={completed.stdout}\nstderr={completed.stderr}"
                )
            requests = [item["request"] for item in captured
                        if item["path"].endswith("/chat/completions")]
            if len(requests) != 1:
                raise AssertionError(f"expected one provider request, captured={captured}")
            messages = requests[0].get("messages")
            systems = [item.get("content") for item in messages or []
                       if item.get("role") == "system"]
            if systems != [prompt]:
                raise AssertionError(f"provider system message was not exact: {systems}")
            serialized = json.dumps(requests[0])
            for forbidden in ("Hermes Agent", "FORBIDDEN UPSTREAM ADDITION", "AGENTS.md"):
                if forbidden in serialized:
                    raise AssertionError(f"upstream prompt material reached provider: {forbidden}")
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
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
