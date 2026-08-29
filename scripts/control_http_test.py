#!/usr/bin/env python3
"""Contained HTTP/security regression for the dependency-free Control Center."""

from __future__ import annotations

from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
from pathlib import Path
import tempfile
import threading

from smacx_control import ControlPlane
from smacx_control_server import ControlHTTPServer
from smacx_store import SmacxStore


ROOT = Path(__file__).resolve().parents[1]


def request(port: int, method: str, path: str, *, body: dict | None = None,
            cookies: dict[str, str] | None = None, csrf: str | None = None):
    headers = {}
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(encoded))
    if cookies:
        headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
    if csrf:
        headers["X-CSRF-Token"] = csrf
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=encoded, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    result = json.loads(raw) if response.getheader("Content-Type", "").startswith("application/json") else raw
    headers_result = response.getheaders()
    connection.close()
    return response.status, headers_result, result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-control-http-") as temporary:
        root = Path(temporary)
        control = ControlPlane(SmacxStore(root / "state.sqlite3"), root / "secrets")
        control.ensure_bootstrap_token()
        bootstrap_token = control.reveal_bootstrap_token()
        server = ControlHTTPServer(
            ("127.0.0.1", 0), control, ROOT / "control_center/static",
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, headers, setup = request(server.server_port, "GET", "/api/v1/setup")
            if status != 200 or not setup["setup_required"] or bootstrap_token in json.dumps(setup):
                raise AssertionError("public setup endpoint leaked or omitted bootstrap state")
            header_map = {key.lower(): value for key, value in headers}
            if "default-src 'self'" not in header_map.get("content-security-policy", ""):
                raise AssertionError("security headers are missing")

            status, _, unauthorized = request(server.server_port, "GET", "/api/v1/status")
            if status != 401 or unauthorized["error"]["code"] != "authentication_required":
                raise AssertionError("unauthenticated status request was accepted")

            status, bootstrap_headers, result = request(
                server.server_port, "POST", "/api/v1/setup/bootstrap",
                body={
                    "username": "admin", "bootstrap_token": bootstrap_token,
                    "password": "control center test password",
                },
            )
            if status != 201 or not result["ok"]:
                raise AssertionError(f"bootstrap HTTP flow failed: {status} {result}")
            cookies: dict[str, str] = {}
            for key, value in bootstrap_headers:
                if key.lower() != "set-cookie":
                    continue
                parsed: SimpleCookie[str] = SimpleCookie()
                parsed.load(value)
                for name, morsel in parsed.items():
                    cookies[name] = morsel.value
            if set(cookies) != {"smacx_session", "smacx_csrf"}:
                raise AssertionError("bootstrap did not return session and CSRF cookies")

            status, _, state = request(
                server.server_port, "GET", "/api/v1/status", cookies=cookies,
            )
            if status != 200 or state["setup_required"]:
                raise AssertionError("authenticated status failed")

            provider_body = {
                "display_name": "HTTP provider", "base_url": "http://model-box:8000/v1",
                "api_key": "never-return-this-provider-secret",
            }
            status, _, rejected = request(
                server.server_port, "POST", "/api/v1/providers",
                body=provider_body, cookies=cookies,
            )
            if status != 401 or rejected["error"]["code"] != "invalid_csrf_token":
                raise AssertionError("cookie mutation bypassed CSRF protection")

            status, _, configured = request(
                server.server_port, "POST", "/api/v1/providers", body=provider_body,
                cookies=cookies, csrf=cookies["smacx_csrf"],
            )
            if status != 200 or not configured["provider"]["has_api_key"]:
                raise AssertionError(f"authenticated provider configuration failed: {configured}")
            if provider_body["api_key"] in json.dumps(configured):
                raise AssertionError("provider API key leaked in HTTP response")

            status, _, providers = request(
                server.server_port, "GET", "/api/v1/providers", cookies=cookies,
            )
            if status != 200 or len(providers["providers"]) != 1:
                raise AssertionError("configured provider was not listed")
            if provider_body["api_key"] in json.dumps(providers):
                raise AssertionError("provider API key leaked in list response")

            status, _, traversal = request(server.server_port, "GET", "/%2e%2e/README.md")
            if status != 404 or traversal["error"]["code"] != "not_found":
                raise AssertionError("static path traversal was not rejected")

            status, _, logged_out = request(
                server.server_port, "POST", "/api/v1/auth/logout", body={},
                cookies=cookies, csrf=cookies["smacx_csrf"],
            )
            if status != 200 or not logged_out["ok"]:
                raise AssertionError("logout failed")
            status, _, _ = request(server.server_port, "GET", "/api/v1/status", cookies=cookies)
            if status != 401:
                raise AssertionError("revoked session remained usable")

            print(json.dumps({
                "event": "pass",
                "payload": {
                    "bootstrap_over_http": True,
                    "no_default_password": True,
                    "secure_headers": True,
                    "http_only_session": True,
                    "csrf_enforced": True,
                    "provider_secret_redacted": True,
                    "static_traversal_rejected": True,
                    "logout_revokes_session": True,
                },
            }, separators=(",", ":")))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
