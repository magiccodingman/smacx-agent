#!/usr/bin/env python3
"""Contained contract for the SemanticKnowledge HTTP retrieval boundary."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _write(self, payload):
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/api/status":
            self._write({"enabled": True, "state": {"state": "ready"}, "mode": "local"})
        elif self.path == "/api/topics":
            self._write({"topics": [{"id": "rules", "title": "Core rules", "document_count": 20}]})
        elif self.path == "/api/documents/doc-001":
            self._write({"document_id": "doc-001", "title": "Treaties", "body": "Treaties are diplomatic agreements."})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if self.path != "/api/search":
            self.send_error(404)
            return
        self._write({
            "query": body["query"],
            "results": [{"document_id": "doc-001", "title": "Treaties", "description": "Diplomatic agreements", "score": .91}],
            "evidence": ([{"document_id": "doc-001", "field": "body", "content": "Treaties are diplomatic agreements.", "token_count": 7}]
                         if body.get("includeContent") else []),
        })


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["SMACX_REFERENCE_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        from smacx_reference import read_reference
        if not read_reference(None, "status").get("ok"):
            raise AssertionError("knowledge health was not observable")
        if not read_reference(None, "topics").get("topics"):
            raise AssertionError("collection hierarchy was not returned")
        compact = read_reference(None, "search", query="Treaty Pact", include_body=False)
        if not compact.get("results") or compact.get("evidence"):
            raise AssertionError("compact search did not remain compact")
        focused = read_reference(None, "lookup", entity_kind="diplomacy", entity_key="treaty", include_body=True)
        if not focused.get("entities", [{}])[0].get("evidence"):
            raise AssertionError("focused semantic lookup omitted bounded evidence")
        full = read_reference(None, "get", document_id="doc-001")
        if not full.get("document", {}).get("body"):
            raise AssertionError("selected-document retrieval failed")
        print(json.dumps({"event": "pass", "payload": {
            "semantic_service_boundary": True, "compact_then_evidence": True,
            "collection_hierarchy": True, "service_health": True,
        }}, separators=(",", ":")))
        return 0
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
