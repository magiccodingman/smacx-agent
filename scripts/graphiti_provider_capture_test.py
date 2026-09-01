#!/usr/bin/env python3
"""Capture Graphiti's exact direct provider request through the production adapter."""

from __future__ import annotations

import argparse
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading


IMAGE = "smacx-agent-graphiti:dev"


async def inside(base_url: str) -> int:
    from pydantic import BaseModel
    from graphiti_core.prompts.models import Message
    from smacx_graphiti import GraphitiRuntimeConfig, create_graphiti_llm_client

    class Fact(BaseModel):
        subject: str
        relationship: str
        object: str

    config = GraphitiRuntimeConfig(
        fingerprint="capture", profile_id="profile-capture", display_name="Capture",
        llm_base_url=base_url, llm_api_key="local", llm_model="Qwen/Qwen3.8-27B",
        reasoning_effort="medium",
        generation_settings={
            "preset": "qwen38-medium", "temperature": 1.0, "top_p": 0.95,
            "extra_parameters": {
                "top_k": 20,
                "chat_template_kwargs": {
                    "enable_thinking": True, "preserve_thinking": False,
                },
            },
        },
        embed_base_url="http://unused/v1", embed_api_key="local",
        embed_model="unused", embed_dim=2048,
    )
    client, _ = create_graphiti_llm_client(config)
    result = await client.generate_response(
        [Message(role="system", content="Extract one public relationship."),
         Message(role="user", content="Deirdre has a treaty with Lal.")],
        response_model=Fact, max_tokens=256, prompt_name="capture",
    )
    await client.client.close()
    if result != {"subject": "Deirdre", "relationship": "treaty", "object": "Lal"}:
        raise AssertionError(f"structured response was not decoded: {result}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside")
    arguments = parser.parse_args()
    if arguments.inside:
        return asyncio.run(inside(arguments.inside))

    captured: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_arguments: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            captured.append(request)
            payload = {
                "id": "graphiti-capture", "object": "chat.completion", "created": 0,
                "model": request.get("model"),
                "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant",
                    "content": json.dumps({
                        "subject": "Deirdre", "relationship": "treaty", "object": "Lal",
                    }),
                }}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            data = json.dumps(payload).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        repository = Path(__file__).resolve().parent.parent
        completed = subprocess.run([
            "docker", "run", "--rm", "--network", "host",
            "-v", f"{repository}:/test:ro", "-e", "PYTHONPATH=/test/src",
            "--entrypoint", "python", IMAGE, "/test/scripts/graphiti_provider_capture_test.py",
            "--inside", f"http://127.0.0.1:{server.server_port}/v1",
        ], text=True, capture_output=True, timeout=120, check=False)
        if completed.returncode:
            raise AssertionError(f"Graphiti adapter capture failed: {completed.stdout}\n{completed.stderr}")
    finally:
        server.shutdown(); server.server_close(); thread.join(2)
    if len(captured) != 1:
        raise AssertionError(f"expected one Graphiti provider request, got {len(captured)}")
    request = captured[0]
    expected = {
        "reasoning_effort": "medium", "temperature": 1.0, "top_p": 0.95,
        "top_k": 20,
        "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": False},
    }
    for key, value in expected.items():
        if request.get(key) != value:
            raise AssertionError(f"Graphiti request lost {key}: {request}")
    if request.get("response_format", {}).get("type") != "json_schema":
        raise AssertionError(f"Graphiti did not request structured JSON: {request}")
    print(json.dumps({"event": "pass", "payload": {
        "production_graphiti_adapter": True,
        "reasoning_effort_top_level": True,
        "chat_template_kwargs_preserved": True,
        "structured_json_requested_and_decoded": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
