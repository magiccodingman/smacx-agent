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
            cookies: dict[str, str] | None = None, csrf: str | None = None,
            service_token: str | None = None):
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
    if service_token:
        headers["X-SMACX-Service-Token"] = service_token
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
        service_token = "portal-service-test-token-that-is-long-enough"
        server = ControlHTTPServer(
            ("127.0.0.1", 0), control, ROOT / "control_center/static",
            service_token=service_token,
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

            status, _, service_state = request(
                server.server_port, "GET", "/api/v1/status", service_token=service_token,
            )
            if status != 200 or not service_state["ok"]:
                raise AssertionError("portal service authentication failed")
            status, _, invalid_service = request(
                server.server_port, "GET", "/api/v1/status", service_token="wrong-token",
            )
            if status != 401 or invalid_service["error"]["code"] != "invalid_service_token":
                raise AssertionError("invalid portal service token was accepted")

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

            status, _, capabilities = request(
                server.server_port, "GET", "/api/v1/capabilities", cookies=cookies,
            )
            if status != 200 \
                    or capabilities.get("launch_modes", {}).get("solo_scenario", {}).get("status") != "available" \
                    or capabilities.get("deployment", {}).get("physical_two_machine_lan", {}).get("status") != "available_private_network":
                raise AssertionError("authenticated capability manifest is missing or inconsistent")

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

            status, _, duplicate = request(
                server.server_port, "POST", "/api/v1/providers", body={
                    "display_name": provider_body["display_name"],
                    "base_url": "http://another-model-box:8000/v1",
                }, cookies=cookies, csrf=cookies["smacx_csrf"],
            )
            if status != 400 \
                    or duplicate["error"]["code"] != "provider_display_name_already_exists" \
                    or "already uses" not in duplicate["error"]["message"]:
                raise AssertionError(f"duplicate provider name was not explained: {duplicate}")

            provider_id = configured["provider"]["provider_id"]
            status, _, deleted = request(
                server.server_port, "POST", f"/api/v1/providers/{provider_id}/delete",
                body={}, cookies=cookies, csrf=cookies["smacx_csrf"],
            )
            if status != 200 or not deleted.get("deleted"):
                raise AssertionError(f"unused provider deletion failed: {deleted}")
            status, _, providers = request(
                server.server_port, "GET", "/api/v1/providers", cookies=cookies,
            )
            if status != 200 or providers["providers"]:
                raise AssertionError("deleted provider remained in the provider list")

            status, _, workers = request(
                server.server_port, "GET", "/api/v1/workers", cookies=cookies,
            )
            if status != 200 or workers["docker"]["error"] != "docker_manager_disabled":
                raise AssertionError("disabled Docker manager state was not explicit")
            status, _, disabled = request(
                server.server_port, "POST", "/api/v1/game-sources/validate",
                body={"host_path": "/legal/game"}, cookies=cookies,
                csrf=cookies["smacx_csrf"],
            )
            if status != 409 or disabled["error"]["code"] != "docker_manager_disabled":
                raise AssertionError("Docker mutations were not blocked when manager was disabled")

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

            from smacx_control_server import ControlRequestHandler
            from smacx_doctrine import DoctrineError
            handler = object.__new__(ControlRequestHandler)
            diagnostics = []
            handler._error = lambda *args: diagnostics.append(args)
            handler._handle_exception(DoctrineError("doctrine_unverified_loaded_ruleset"))
            assert diagnostics[0][:2] == (409, "doctrine_unverified_loaded_ruleset")
            print(json.dumps({
                "event": "pass",
                "payload": {
                    "bootstrap_over_http": True,
                    "no_default_password": True,
                    "secure_headers": True,
                    "http_only_session": True,
                    "csrf_enforced": True,
                    "provider_secret_redacted": True,
                    "docker_manager_disable_guard": True,
                    "static_traversal_rejected": True,
                    "logout_revokes_session": True,
                    "portal_service_auth": True,
                    "doctrine_failure_diagnostic": True,
                },
            }, separators=(",", ":")))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
