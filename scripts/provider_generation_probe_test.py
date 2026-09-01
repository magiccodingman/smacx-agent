#!/usr/bin/env python3
"""Exercise the provider-generation acceptance boundary without a real model."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading

from smacx_control import ControlPlane
from smacx_store import SmacxStore


def main() -> int:
    captured: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            data = json.dumps({"data": [{"id": "test/model", "context_length": 131072}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            captured.append(body)
            if "unsupported_parameter" in body:
                data = b'{"error":{"message":"unsupported"}}'
                self.send_response(400)
            else:
                data = json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="smacx-provider-probe-") as temporary:
            root = Path(temporary)
            control = ControlPlane(SmacxStore(root / "state.sqlite3"), root / "secrets")
            provider = control.configure_provider(
                "Probe endpoint", f"http://127.0.0.1:{server.server_port}/v1",
            )
            provider = control.discover_provider(provider["provider_id"])
            accepted = control.probe_provider_generation(
                provider["provider_id"], "test/model", "low",
                {
                    "preset": "qwen38-low", "temperature": 0.42,
                    "extra_parameters": {"chat_template_kwargs": {
                        "enable_thinking": True, "preserve_thinking": False,
                    }},
                },
            )
            if accepted["accepted"] is not True or accepted["semantic_effect_verified"] is not False:
                raise AssertionError(accepted)
            request = captured[-1]
            if request.get("temperature") != 0.42 or request.get("reasoning_effort") != "low":
                raise AssertionError(request)
            if request.get("chat_template_kwargs", {}).get("preserve_thinking") is not False:
                raise AssertionError(request)
            rejected = control.probe_provider_generation(
                provider["provider_id"], "test/model", "none",
                {"preset": "custom", "extra_parameters": {"unsupported_parameter": True}},
            )
            if rejected["accepted"] is not False or rejected["http_status"] != 400:
                raise AssertionError(rejected)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    print(json.dumps({"event": "pass", "payload": {
        "explicit_fields_sent": True,
        "reasoning_sent_separately": True,
        "acceptance_is_not_semantic_verification": True,
        "provider_rejection_reported": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
