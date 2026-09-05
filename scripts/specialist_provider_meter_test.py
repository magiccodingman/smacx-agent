#!/usr/bin/env python3
"""Deterministic hard-lease tests for the specialist provider boundary."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from smacx_provider_meter import AttemptProviderProxy, ProviderLeaseMeter


def _post(base: str, payload: dict, path: str = "/chat/completions") -> tuple[int, bytes]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    try:
        with urlopen(Request(base + path, data=body, method="POST",
                             headers={"Content-Type": "application/json"}), timeout=5) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    upstream_calls: list[dict] = []
    upstream_paths: list[str] = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            upstream_calls.append(payload)
            upstream_paths.append(self.path)
            index = len(upstream_calls)
            usage = {"prompt_tokens": 100 + index, "completion_tokens": 10,
                     "total_tokens": 110 + index}
            if payload.get("stream"):
                chunks = [
                    {"id": "x", "object": "chat.completion.chunk", "choices": [{
                        "index": 0, "delta": {"role": "assistant", "content": "ok"},
                        "finish_reason": None}]},
                    {"id": "x", "object": "chat.completion.chunk", "choices": [],
                     "usage": usage},
                ]
                body = b"".join(b"data: " + json.dumps(item).encode() + b"\n\n"
                                for item in chunks) + b"data: [DONE]\n\n"
                content_type = "text/event-stream"
            else:
                body = json.dumps({
                    "id": "x", "object": "chat.completion", "choices": [{
                        "index": 0, "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop"}], "usage": usage,
                }).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    meter = ProviderLeaseMeter(call_budget=2, token_budget=10_000,
                               context_ceiling=8_000, output_ceiling=500)
    proxy = AttemptProviderProxy(f"http://127.0.0.1:{upstream.server_port}/v1", meter)
    proxy.start()
    try:
        base = proxy.base_url
        status, _ = _post(base, {"model": "fixture", "messages": [{"role": "user",
                                  "content": "first"}], "max_tokens": 100})
        assert status == 200
        status, body = _post(base, {"model": "fixture", "messages": [{"role": "user",
                                      "content": "first"}, {"role": "tool",
                                      "content": "second"}], "max_tokens": 100,
                                      "stream": True}, path="/v1/chat/completions")
        assert status == 200 and b"[DONE]" in body
        status, body = _post(base, {"model": "fixture", "messages": [],
                                    "max_tokens": 100})
        assert status == 429 and b"provider_call_budget_exhausted" in body
        usage = meter.snapshot()
        assert usage.provider_calls == 2
        assert usage.provider_tokens == 223
        assert usage.peak_context_tokens == 102
        assert len(upstream_calls) == 2
        assert upstream_paths == ["/v1/chat/completions", "/v1/chat/completions"]
    finally:
        proxy.close()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    blocked = ProviderLeaseMeter(call_budget=5, token_budget=2_000,
                                 context_ceiling=1_500, output_ceiling=400)
    blocked_proxy = AttemptProviderProxy(
        f"http://127.0.0.1:{upstream.server_port}/v1", blocked,
    )
    blocked_proxy.start()
    try:
        status, body = _post(blocked_proxy.base_url, {
            "model": "fixture", "messages": [{"role": "user", "content": "x" * 600}],
            "max_tokens": 400,
        })
        assert status == 429 and b"provider_context_ceiling_exceeded" in body
        assert blocked.snapshot().provider_calls == 0
    finally:
        blocked_proxy.close()

    print(json.dumps({"passed": True, "provider_calls": 2,
                      "provider_tokens": 223, "peak_context_tokens": 102},
                     separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
