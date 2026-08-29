"""Dependency-free authenticated HTTP service for the SMACX Control Center."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

from smacx_control import AuthenticationError, ControlPlane, ProviderError
from smacx_store import InvalidRecord, ScopeViolation, SmacxStore, StoreError


MAX_REQUEST_BODY = 1024 * 1024
SESSION_COOKIE = "smacx_session"
CSRF_COOKIE = "smacx_csrf"
PROVIDER_PATH = re.compile(r"^/api/v1/providers/([A-Za-z0-9_-]{8,96})/(discover|select)$")


class RequestRateLimiter:
    def __init__(self, attempts: int = 8, window_seconds: float = 60.0) -> None:
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._attempts[key]
            while bucket and bucket[0] <= now - self.window_seconds:
                bucket.popleft()
            if len(bucket) >= self.attempts:
                return False
            bucket.append(now)
            return True


class ControlHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], control: ControlPlane,
                 static_root: Path, *, secure_cookies: bool = False) -> None:
        super().__init__(address, ControlRequestHandler)
        self.control = control
        self.static_root = static_root.resolve()
        self.secure_cookies = secure_cookies
        self.login_limiter = RequestRateLimiter()


class ControlRequestHandler(BaseHTTPRequestHandler):
    server: ControlHTTPServer
    server_version = "SMACX-Control"
    sys_version = ""

    def log_message(self, format_string: str, *args: Any) -> None:
        # Structured, deliberately sparse logs: no request bodies, cookies,
        # authorization headers, provider keys, or bootstrap tokens.
        print(json.dumps({
            "event": "control_http",
            "remote": self.client_address[0],
            "method": self.command,
            "path": urlsplit(self.path).path,
            "message": format_string % args,
        }, separators=(",", ":")), flush=True)

    def _security_headers(self, *, api: bool = True) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        if api:
            self.send_header("Cache-Control", "no-store")

    def _json(self, status: int, payload: Any, *, cookies: list[str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str | None = None) -> None:
        self._json(status, {
            "ok": False,
            "error": {"code": code, "message": message or code.replace("_", " ")},
        })

    def _body(self) -> dict[str, Any]:
        length_text = self.headers.get("Content-Length", "")
        try:
            length = int(length_text)
        except ValueError as exc:
            raise InvalidRecord("invalid_content_length") from exc
        if length < 0 or length > MAX_REQUEST_BODY:
            raise InvalidRecord("request_body_too_large")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise InvalidRecord("json_content_type_required")
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidRecord("invalid_json_body") from exc
        if not isinstance(value, dict):
            raise InvalidRecord("json_object_required")
        return value

    def _cookies(self) -> SimpleCookie[str]:
        cookies: SimpleCookie[str] = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        return cookies

    def _authentication(self) -> tuple[dict[str, Any], str]:
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization[7:].strip()
            return self.server.control.authenticate(token), "bearer"
        morsel = self._cookies().get(SESSION_COOKIE)
        if not morsel:
            raise AuthenticationError("authentication_required")
        return self.server.control.authenticate(morsel.value), "cookie"

    def _authorize_mutation(self) -> dict[str, Any]:
        auth, mode = self._authentication()
        if mode == "cookie":
            csrf = self.headers.get("X-CSRF-Token", "")
            cookie = self._cookies().get(CSRF_COOKIE)
            if not cookie or not csrf or cookie.value != csrf:
                raise AuthenticationError("invalid_csrf_token")
            self.server.control.require_csrf(auth["auth_session_id"], csrf)
        return auth

    def _session_cookies(self, token: str, csrf_token: str, expires_unix: float) -> list[str]:
        max_age = max(0, int(expires_unix - time.time()))
        secure = "; Secure" if self.server.secure_cookies else ""
        common = f"Path=/; SameSite=Strict; Max-Age={max_age}{secure}"
        return [
            f"{SESSION_COOKIE}={token}; {common}; HttpOnly",
            f"{CSRF_COOKIE}={csrf_token}; {common}",
        ]

    def _clear_cookies(self) -> list[str]:
        secure = "; Secure" if self.server.secure_cookies else ""
        return [
            f"{SESSION_COOKIE}=; Path=/; SameSite=Strict; Max-Age=0; HttpOnly{secure}",
            f"{CSRF_COOKIE}=; Path=/; SameSite=Strict; Max-Age=0{secure}",
        ]

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        path = urlsplit(self.path).path
        try:
            if path == "/healthz":
                self._json(200, {"ok": True, "service": "smacx-control"})
                return
            if path == "/api/v1/setup":
                state = self.server.control.ensure_bootstrap_token()
                self._json(200, {
                    "ok": True,
                    "setup_required": state["setup_required"],
                    "username": "admin",
                    "bootstrap_command": "smacx-control bootstrap-token" if state["setup_required"] else None,
                })
                return
            if path == "/api/v1/auth/session":
                auth, _ = self._authentication()
                self._json(200, {"ok": True, "session": auth})
                return
            if path == "/api/v1/status":
                self._authentication()
                self._json(200, self.server.control.status())
                return
            if path == "/api/v1/providers":
                self._authentication()
                self._json(200, {"ok": True, "providers": self.server.control.list_providers()})
                return
            if path == "/api/v1/game-sources":
                self._authentication()
                self._json(200, {"ok": True, "game_sources": self.server.control.list_game_sources()})
                return
            if path == "/api/v1/runtimes":
                self._authentication()
                self._json(200, {"ok": True, "runtimes": self.server.control.list_runtimes()})
                return
            if path.startswith("/api/"):
                self._error(404, "not_found")
                return
            self._static(path)
        except Exception as exc:
            self._handle_exception(exc)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        path = urlsplit(self.path).path
        try:
            if path == "/api/v1/setup/bootstrap":
                self._rate_limit("bootstrap")
                body = self._body()
                self.server.control.bootstrap_admin(
                    str(body.get("bootstrap_token", "")), str(body.get("password", "")),
                    username=str(body.get("username", "admin")),
                )
                session = self.server.control.login(
                    str(body.get("username", "admin")), str(body.get("password", "")),
                    remote_address=self.client_address[0],
                )
                self._json(201, {
                    "ok": True,
                    "session": {"admin_id": session.admin_id, "username": session.username,
                                "expires_unix": session.expires_unix},
                }, cookies=self._session_cookies(session.token, session.csrf_token, session.expires_unix))
                return
            if path == "/api/v1/auth/login":
                self._rate_limit("login")
                body = self._body()
                session = self.server.control.login(
                    str(body.get("username", "")), str(body.get("password", "")),
                    remote_address=self.client_address[0],
                )
                self._json(200, {
                    "ok": True,
                    "session": {"admin_id": session.admin_id, "username": session.username,
                                "expires_unix": session.expires_unix},
                }, cookies=self._session_cookies(session.token, session.csrf_token, session.expires_unix))
                return
            if path == "/api/v1/auth/logout":
                auth = self._authorize_mutation()
                self.server.control.logout(auth["auth_session_id"])
                self._json(200, {"ok": True}, cookies=self._clear_cookies())
                return
            if path == "/api/v1/providers":
                auth = self._authorize_mutation()
                body = self._body()
                provider = self.server.control.configure_provider(
                    str(body.get("display_name", "")), str(body.get("base_url", "")),
                    api_key=body.get("api_key"), provider_id=body.get("provider_id"),
                    default_model_id=body.get("default_model_id"),
                    context_length_override=body.get("context_length_override"),
                )
                self.server.control.audit(
                    auth["admin_id"], "provider.configure", "provider", provider["provider_id"],
                    "success", {"base_url": provider["base_url"], "has_api_key": provider["has_api_key"]},
                    self.client_address[0],
                )
                self._json(200, {"ok": True, "provider": provider})
                return
            match = PROVIDER_PATH.fullmatch(path)
            if match:
                auth = self._authorize_mutation()
                provider_id, action = match.groups()
                if action == "discover":
                    self._body()
                    provider = self.server.control.discover_provider(provider_id)
                    self.server.control.audit(
                        auth["admin_id"], "provider.discover", "provider", provider_id,
                        "success", {"model_count": len(provider["models"])}, self.client_address[0],
                    )
                else:
                    body = self._body()
                    provider = self.server.control.select_provider_model(
                        provider_id, str(body.get("model_id", "")),
                        context_length_override=body.get("context_length_override"),
                    )
                    self.server.control.audit(
                        auth["admin_id"], "provider.select_model", "provider", provider_id,
                        "success", {"model_id": provider["default_model_id"]}, self.client_address[0],
                    )
                self._json(200, {"ok": True, "provider": provider})
                return
            self._error(404, "not_found")
        except Exception as exc:
            self._handle_exception(exc)

    def _rate_limit(self, operation: str) -> None:
        key = f"{operation}:{self.client_address[0]}"
        if not self.server.login_limiter.allow(key):
            raise AuthenticationError("too_many_attempts")

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            relative = "index.html"
        else:
            relative = unquote(path.lstrip("/"))
        candidate = (self.server.static_root / relative).resolve()
        if candidate.parent != self.server.static_root or not candidate.is_file():
            self._error(404, "not_found")
            return
        body = candidate.read_bytes()
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers(api=False)
        self.send_header("Cache-Control", "no-cache" if candidate.name == "index.html" else "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _handle_exception(self, exc: Exception) -> None:
        if isinstance(exc, AuthenticationError):
            status = 429 if str(exc) == "too_many_attempts" else 401
            self._error(status, str(exc))
        elif isinstance(exc, (InvalidRecord, ScopeViolation)):
            self._error(400, str(exc))
        elif isinstance(exc, ProviderError):
            self._error(502, str(exc))
        elif isinstance(exc, StoreError):
            self._error(409, str(exc))
        else:
            print(json.dumps({
                "event": "control_error", "error_type": type(exc).__name__,
                "path": urlsplit(self.path).path,
            }, separators=(",", ":")), file=sys.stderr, flush=True)
            self._error(500, "internal_error")


def build_control(data_root: Path) -> ControlPlane:
    data_root = data_root.expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    database = Path(os.environ.get("SMACX_DB_PATH", data_root / "smacx.sqlite3"))
    secret_root = Path(os.environ.get("SMACX_SECRET_ROOT", data_root / "secrets"))
    return ControlPlane(SmacxStore(database), secret_root)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="smacx-control")
    result.add_argument("--data-root", default=os.environ.get("SMACX_CONTROL_DATA", "/var/lib/smacx"))
    commands = result.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="run the authenticated Control Center")
    serve.add_argument("--host", default=os.environ.get("SMACX_CONTROL_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("SMACX_CONTROL_PORT", "8080")))
    serve.add_argument("--static-root", default=str(Path(__file__).resolve().parents[1] / "control_center/static"))
    commands.add_parser("bootstrap-token", help="print the one-time first-run token")
    commands.add_parser("status", help="print redacted service status")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    command = arguments.command or "serve"
    control = build_control(Path(arguments.data_root))
    if command == "bootstrap-token":
        print(control.reveal_bootstrap_token())
        return 0
    if command == "status":
        print(json.dumps(control.status(), sort_keys=True))
        return 0
    control.ensure_bootstrap_token()
    host = getattr(arguments, "host", os.environ.get("SMACX_CONTROL_HOST", "127.0.0.1"))
    port = getattr(arguments, "port", int(os.environ.get("SMACX_CONTROL_PORT", "8080")))
    static_root = getattr(
        arguments, "static_root",
        str(Path(__file__).resolve().parents[1] / "control_center/static"),
    )
    if not 1 <= port <= 65535:
        raise SystemExit("invalid port")
    server = ControlHTTPServer(
        (host, port), control, Path(static_root),
        secure_cookies=os.environ.get("SMACX_SECURE_COOKIES", "0") == "1",
    )
    print(json.dumps({
        "event": "control_ready", "host": host, "port": server.server_port,
        "setup_required": not control.admin_exists(),
    }, separators=(",", ":")), flush=True)
    stopping = threading.Event()

    def request_stop(*_unused: Any) -> None:
        stopping.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    server.timeout = 0.25
    try:
        while not stopping.is_set():
            server.handle_request()
    finally:
        server.server_close()
        print(json.dumps({"event": "control_stopped"}, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
