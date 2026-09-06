#!/usr/bin/env python3
"""Verify the audit observes actual HTTP bytes without altering transport."""
import json
from pathlib import Path
import tempfile

import httpx

from smacx_diagnostics import DiagnosticWriter, install_httpx_capture


def main():
    with tempfile.TemporaryDirectory() as tmp:
        writer = DiagnosticWriter(Path(tmp), "match-test", "sovereign")
        # Subclass keeps the test hook isolated from unrelated HTTP clients.
        class Client(httpx.Client):
            pass
        install_httpx_capture(Client, writer)
        install_httpx_capture(Client, writer)
        received = []
        def transport(request):
            received.append(request.content)
            if request.headers.get("x-test-fail"):
                raise httpx.ConnectError("private transport details", request=request)
            return httpx.Response(200, json={"id": "response-test"})
        body = {"model": "test", "messages": [{"role": "user", "content":
                "question\n<SMACX_RUNTIME_CONTEXT>final-state</SMACX_RUNTIME_CONTEXT>"}],
                "tools": [], "stream": True}
        with Client(transport=httpx.MockTransport(transport)) as client:
            response = client.post("https://provider.invalid/v1/chat/completions?secret=hidden",
                headers={"Authorization": "Bearer hidden-key"}, json=body)
            assert response.json()["id"] == "response-test"
            client.get("https://provider.invalid/health")
            try:
                client.post("https://provider.invalid/v1/chat/completions",
                            headers={"x-test-fail": "yes"}, json=body)
            except httpx.ConnectError:
                pass
            else:
                raise AssertionError("transport failure swallowed")
        text = writer.path.read_text()
        rows = [json.loads(s) for s in text.splitlines()]
        assert [r["kind"] for r in rows] == ["provider_request_submitted",
            "provider_response_headers", "provider_request_submitted", "provider_transport_failed"]
        assert rows[0]["payload"]["body"] == json.loads(received[0]) == body
        assert rows[0]["correlation"] == rows[1]["correlation"]
        assert rows[0]["correlation"] != rows[2]["correlation"]
        assert rows[1]["payload"]["completion_verified"] is False
        assert "hidden-key" not in text and "secret=hidden" not in text
        assert "private transport details" not in text
    print(json.dumps({"event": "pass", "payload": {
        "http_serialized_body_matches_capture": True, "transport_unchanged": True,
        "headers_and_url_credentials_excluded": True,
        "failure_and_header_receipts_correlated": True,
        "live_provider_acceptance_pending": True}}))


if __name__ == "__main__":
    main()
