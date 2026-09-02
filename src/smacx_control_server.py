"""Dependency-free authenticated HTTP service for the SMACX Control Center."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import fcntl
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import signal
import secrets
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit
import hmac

from smacx_capabilities import capability_manifest
from smacx_control import AuthenticationError, ControlPlane, ProviderError
from smacx_docker import DockerClient, DockerError, DockerUnavailable
from smacx_harness_manager import HERMES_IMAGE, HarnessManager
from smacx_operations import OperationsManager, restore_backup_offline
from smacx_reference import read_reference
from smacx_store import InvalidRecord, MemoryScope, ScopeViolation, SmacxStore, StoreError
from smacx_worker_manager import LAN_PROFILES, WorkerManager, WorkerManagerError


MAX_REQUEST_BODY = 1024 * 1024
SESSION_COOKIE = "smacx_session"
CSRF_COOKIE = "smacx_csrf"
PROVIDER_PATH = re.compile(r"^/api/v1/providers/([A-Za-z0-9_-]{8,96})/(discover|select|delete|probe-generation)$")
WORKER_PATH = re.compile(
    r"^/api/v1/workers/([A-Za-z0-9_-]{8,96})/"
    r"(start|park|status|spectator|chat|group-chat|human-ui)$"
)
MATCH_PATH = re.compile(
    r"^/api/v1/matches/([A-Za-z0-9_-]{8,96})/"
    r"(start|park|complete|status|discover-external-host|join-external-host|finalize-external-host)$"
)
MATCH_DETAIL_PATH = re.compile(r"^/api/v1/matches/([A-Za-z0-9_-]{8,96})$")
MATCH_STATUS_PATH = re.compile(r"^/api/v1/matches/([A-Za-z0-9_-]{8,96})/status$")
SCHEDULE_PATH = re.compile(r"^/api/v1/schedules/([A-Za-z0-9_-]{8,96})/(activate|pause|disable)$")
BACKUP_PATH = re.compile(r"^/api/v1/backups/([A-Za-z0-9_-]{8,96})/verify$")
RECOVERY_PATH = re.compile(
    r"^/api/v1/matches/([A-Za-z0-9_-]{8,96})/"
    r"(checkpoint|recover|retry-after-update)$"
)
RESOLUTION_PATH = re.compile(
    r"^/api/v1/matches/([A-Za-z0-9_-]{8,96})/resolution$"
)
MATCH_CONTROLLER_PATH = re.compile(
    r"^/api/v1/matches/([A-Za-z0-9_-]{8,96})/(seat-controller|host-seat)$"
)
HARNESS_RUN_PATH = re.compile(
    r"^/api/v1/harness-runs/([A-Za-z0-9_-]{8,96})/(start|stop|status|telemetry)$"
)
SCENARIO_CATALOG_PATH = re.compile(r"^/api/v1/game-sources/([A-Za-z0-9_-]{8,96})/scenarios$")
INCIDENT_PATH = re.compile(r"^/api/v1/incidents/([A-Za-z0-9_-]{8,96})$")


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
                 static_root: Path, *, secure_cookies: bool = False,
                 worker_manager: WorkerManager | None = None,
                 operations: OperationsManager | None = None,
                 harness_manager: HarnessManager | None = None,
                 service_token: str | None = None) -> None:
        super().__init__(address, ControlRequestHandler)
        self.control = control
        self.static_root = static_root.resolve()
        self.secure_cookies = secure_cookies
        self.worker_manager = worker_manager
        self.operations = operations
        self.harness_manager = harness_manager
        self.service_token = service_token
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
        service_token = self.headers.get("X-SMACX-Service-Token", "")
        if service_token:
            expected = self.server.service_token
            if not expected or not hmac.compare_digest(service_token, expected):
                raise AuthenticationError("invalid_service_token")
            return {
                "admin_id": None,
                "username": "portal-service",
                "auth_session_id": "service",
                "service": True,
            }, "service"
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
        parts = urlsplit(self.path)
        path = parts.path
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
            if path == "/api/v1/capabilities":
                self._authentication()
                self._json(200, capability_manifest())
                return
            if path == "/api/v1/providers":
                self._authentication()
                self._json(200, {"ok": True, "providers": self.server.control.list_providers()})
                return
            if path == "/api/v1/agents":
                self._authentication()
                self._json(200, {"ok": True, "agents": self.server.control.list_agents()})
                return
            if path == "/api/v1/matches":
                self._authentication()
                self._json(200, {"ok": True, "matches": self.server.control.list_matches()})
                return
            match_status = MATCH_STATUS_PATH.fullmatch(path)
            if match_status:
                self._authentication()
                self._json(200, self._manager().lan_match_status(match_status.group(1)))
                return
            match_detail = MATCH_DETAIL_PATH.fullmatch(path)
            if match_detail:
                self._authentication()
                match_id = match_detail.group(1)
                self._json(200, {
                    "ok": True,
                    "match": self.server.control.get_match(match_id),
                    "seats": self.server.control.list_seats(match_id),
                })
                return
            if path == "/api/v1/workers":
                self._authentication()
                self._json(200, {
                    "ok": True,
                    "docker": self.server.worker_manager.health() if self.server.worker_manager else {
                        "ok": False, "error": "docker_manager_disabled",
                    },
                    "workers": [self._redact_worker(item) for item in self.server.control.list_worker_specs()],
                })
                return
            if path == "/api/v1/game-sources":
                self._authentication()
                self._json(200, {"ok": True, "game_sources": self.server.control.list_game_sources()})
                return
            scenario_catalog = SCENARIO_CATALOG_PATH.fullmatch(path)
            if scenario_catalog:
                self._authentication()
                if not self.server.worker_manager:
                    raise WorkerManagerError("docker_manager_disabled")
                self._json(200, self.server.worker_manager.list_scenarios(
                    scenario_catalog.group(1),
                ))
                return
            if path == "/api/v1/runtimes":
                self._authentication()
                self._json(200, {"ok": True, "runtimes": self.server.control.list_runtimes()})
                return
            if path == "/api/v1/harness-profiles":
                self._authentication()
                self._json(200, {
                    "ok": True,
                    "harness_profiles": self.server.control.list_harness_profiles(),
                })
                return
            if path == "/api/v1/harness-runs":
                self._authentication()
                self._json(200, {
                    "ok": True, "harness_runs": self.server.control.list_harness_runs(),
                })
                return
            if path == "/api/v1/graphiti":
                self._authentication()
                self._json(200, self.server.control.graphiti_status())
                return
            if path == "/api/v1/embeddings":
                self._authentication()
                self._json(200, {"ok": True, "configuration": self.server.control.embedding_configuration()})
                return
            if path == "/api/v1/operations/status":
                self._authentication()
                self._json(200, self._operations().status())
                return
            if path == "/api/v1/incidents":
                self._authentication()
                query = parse_qs(parts.query, keep_blank_values=True)
                match_id = query.get("match_id", [None])[0]
                active_only = query.get("active_only", ["false"])[0].lower() in {
                    "1", "true", "yes",
                }
                self._json(200, {"ok": True, "incidents":
                    self.server.control.list_supervision_incidents(
                        match_id=match_id, active_only=active_only,
                    )})
                return
            incident_path = INCIDENT_PATH.fullmatch(path)
            if incident_path:
                self._authentication()
                self._json(200, {"ok": True, "incident":
                    self.server.control.get_supervision_incident(incident_path.group(1))})
                return
            if path == "/api/v1/storage-policy":
                self._authentication()
                self._json(200, self.server.control.storage_policy())
                return
            if path == "/api/v1/schedules":
                self._authentication()
                self._json(200, {"ok": True, "schedules": self._operations().list_schedules()})
                return
            if path == "/api/v1/backups":
                self._authentication()
                self._json(200, {"ok": True, "backups": self._operations().list_backups()})
                return
            if path == "/api/v1/reference/topics":
                self._authentication()
                self._json(200, read_reference(self.server.control.store, "topics"))
                return
            if path == "/api/v1/reference/tree":
                self._authentication()
                query = parse_qs(parts.query, keep_blank_values=True)
                include_documents = query.get("include_documents", ["false"])[0].lower() in {"1", "true", "yes"}
                self._json(200, read_reference(
                    self.server.control.store, "tree", include_documents=include_documents,
                ))
                return
            if path.startswith("/api/v1/reference/collections/") and path.endswith("/documents"):
                self._authentication()
                collection_id = unquote(path.removeprefix("/api/v1/reference/collections/").removesuffix("/documents"))
                self._json(200, read_reference(
                    self.server.control.store, "collection_documents", document_id=collection_id,
                ))
                return
            if path == "/api/v1/reference/status":
                self._authentication()
                self._json(200, read_reference(self.server.control.store, "status"))
                return
            if path == "/api/v1/reference/audit":
                self._authentication()
                self._json(200, read_reference(self.server.control.store, "audit"))
                return
            if path == "/api/v1/reference/search":
                self._authentication()
                query = parse_qs(parts.query, keep_blank_values=True)
                try:
                    limit = min(max(int(query.get("limit", ["8"])[0]), 1), 30)
                    max_query_tokens = min(max(int(query.get("max_query_tokens", ["1024"])[0]), 32), 4096)
                except (TypeError, ValueError) as exc:
                    raise InvalidRecord("invalid_reference_limit") from exc
                self._json(200, read_reference(
                    self.server.control.store, "search",
                    query=query.get("q", [""])[0], topic=query.get("topic", [""])[0],
                    limit=limit, include_body=False, max_query_tokens=max_query_tokens,
                ))
                return
            if path.startswith("/api/v1/reference/documents/"):
                self._authentication()
                document_id = unquote(path.removeprefix("/api/v1/reference/documents/"))
                self._json(200, read_reference(
                    self.server.control.store, "get", document_id=document_id,
                ))
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
            if path == "/api/v1/agents":
                auth = self._authorize_mutation()
                body = self._body()
                agent = self.server.control.create_agent(
                    str(body.get("display_name", "")), agent_id=body.get("agent_id"),
                    personality_ref=body.get("personality_ref"),
                )
                self.server.control.audit(
                    auth["admin_id"], "agent.create", "agent", agent["agent_id"],
                    "success", {"display_name": agent["display_name"]}, self.client_address[0],
                )
                self._json(201, {"ok": True, "agent": agent})
                return
            if path == "/api/v1/game-sources/validate":
                auth = self._authorize_mutation()
                manager = self._manager()
                body = self._body()
                source = manager.validate_game_source(
                    str(body.get("host_path", "")),
                    display_name=str(body.get("display_name", "Alien Crossfire")),
                )
                self.server.control.audit(
                    auth["admin_id"], "game_source.validate", "game_source",
                    source["game_source_id"], "success",
                    {"executable_sha256": source["executable_sha256"]}, self.client_address[0],
                )
                self._json(201, {"ok": True, "game_source": source})
                return
            if path == "/api/v1/runtimes/import-proton":
                auth = self._authorize_mutation()
                manager = self._manager()
                body = self._body()
                runtime = manager.import_proton(
                    str(body.get("source_host_path", "")),
                    display_name=str(body.get("display_name", "Managed Proton")),
                )
                self.server.control.audit(
                    auth["admin_id"], "runtime.import", "runtime", runtime["runtime_id"],
                    "success", {"content_fingerprint": runtime["content_fingerprint"]},
                    self.client_address[0],
                )
                self._json(201, {"ok": True, "runtime": runtime})
                return
            if path == "/api/v1/matches/solo":
                auth = self._authorize_mutation()
                manager = self._manager()
                body = self._body()
                faction_id = body.get("faction_id", 1)
                if isinstance(faction_id, bool):
                    raise InvalidRecord("invalid_faction_id")
                try:
                    faction_id = int(faction_id)
                except (TypeError, ValueError) as exc:
                    raise InvalidRecord("invalid_faction_id") from exc
                controller_kind = str(body.get("controller_kind", "agent"))
                created = self.server.control.create_solo_match(
                    str(body.get("display_name", "")),
                    (str(body.get("agent_id", "")) if controller_kind == "agent" else None),
                    match_id=(str(body["match_id"]) if body.get("match_id") else None),
                    faction_id=faction_id,
                    faction_name=body.get("faction_name"),
                    controller_kind=controller_kind,
                    human_player_name=(str(body["human_player_name"])
                                       if body.get("human_player_name") else None),
                    metadata={"graphiti_enabled": body.get("graphiti_enabled", True) is True},
                )
                perspective = created["perspective"]
                try:
                    worker = manager.provision_worker(
                        MemoryScope(created["match"]["match_id"], perspective["agent_id"],
                                    perspective["perspective_id"]),
                        str(body.get("game_source_id", "")), str(body.get("runtime_id", "")),
                        autostart=body.get("autostart") if isinstance(body.get("autostart"), dict) else None,
                        view_enabled=body.get("view_enabled") is True,
                        view_mode=("interactive" if controller_kind == "human" else "view-only"),
                        controller_kind=controller_kind,
                    )
                except Exception:
                    self.server.control.discard_unstarted_match(
                        created["match"]["match_id"], perspective["perspective_id"],
                    )
                    raise
                self.server.control.audit(
                    auth["admin_id"], "match.create_solo", "match", created["match"]["match_id"],
                    "success", {"instance_id": worker["instance_id"]}, self.client_address[0],
                )
                self._json(201, {
                    "ok": True, **created, "worker": self._redact_worker(worker),
                })
                return
            if path == "/api/v1/matches/lan":
                auth = self._authorize_mutation()
                manager = self._manager()
                body = self._body()
                if body.get("game_settings") is not None \
                        and not isinstance(body.get("game_settings"), dict):
                    raise InvalidRecord("invalid_lan_game_settings")
                agent_ids = body.get("agent_ids")
                if not isinstance(agent_ids, list) or not all(isinstance(item, str) for item in agent_ids):
                    raise InvalidRecord("invalid_lan_agent_ids")
                agent_seats = body.get("agent_seats")
                if agent_seats is not None and (
                        not isinstance(agent_seats, list)
                        or not all(isinstance(item, dict) for item in agent_seats)):
                    raise InvalidRecord("invalid_lan_agent_seats")
                human_player_names = body.get("human_player_names", [])
                if not isinstance(human_player_names, list) \
                        or not all(isinstance(item, str) for item in human_player_names):
                    raise InvalidRecord("invalid_lan_human_player_names")
                managed_human_player_names = body.get("managed_human_player_names", [])
                if not isinstance(managed_human_player_names, list) \
                        or not all(isinstance(item, str) for item in managed_human_player_names):
                    raise InvalidRecord("invalid_lan_managed_human_player_names")
                human_seat_preferences = body.get("human_seat_preferences", [])
                if not isinstance(human_seat_preferences, list) \
                        or not all(isinstance(item, dict) for item in human_seat_preferences):
                    raise InvalidRecord("invalid_lan_human_seat_preferences")
                faction_roster_choice_ids = body.get("faction_roster_choice_ids", [])
                if not isinstance(faction_roster_choice_ids, list) \
                        or not all(isinstance(item, int) for item in faction_roster_choice_ids):
                    raise InvalidRecord("invalid_lan_faction_roster")
                profile = str(body.get("profile", "small_easy"))
                if profile not in LAN_PROFILES:
                    raise InvalidRecord("unsupported_lan_profile")
                created = self.server.control.create_lan_match(
                    str(body.get("display_name", "")), list(agent_ids),
                    human_player_names=list(human_player_names),
                    managed_human_player_names=list(managed_human_player_names),
                    human_seat_preferences=list(human_seat_preferences),
                    faction_roster_choice_ids=list(faction_roster_choice_ids),
                    host_controller_kind=str(body.get("host_controller_kind", "agent")),
                    human_host_name=(str(body["human_host_name"])
                                     if body.get("human_host_name") is not None else None),
                    human_host_managed=body.get("human_host_managed") is True,
                    agent_seats=(list(agent_seats) if agent_seats is not None else None),
                    match_id=(str(body["match_id"])
                              if body.get("match_id") is not None else None),
                    metadata={
                        "lan_profile": profile,
                        "lan_session_name": str(body.get("session_name", "SMACX Managed LAN")),
                        "graphiti_enabled": body.get("graphiti_enabled", True) is True,
                    },
                )
                workers = []
                try:
                    for seat in created["seats"]:
                        managed = seat["controller_kind"] == "agent" or (
                            seat["controller_kind"] == "human"
                            and seat.get("metadata", {}).get("managed") is True
                        )
                        if not managed:
                            continue
                        workers.append(manager.provision_worker(
                            MemoryScope(
                                created["match"]["match_id"], str(seat["agent_id"]),
                                str(seat["perspective_id"]),
                            ),
                            str(body.get("game_source_id", "")), str(body.get("runtime_id", "")),
                            autostart={
                                "enabled": False,
                                "faction_roster": list(faction_roster_choice_ids),
                            },
                            view_enabled=body.get("view_enabled") is True,
                            view_mode=("interactive" if seat["controller_kind"] == "human"
                                       else "view-only"),
                            controller_kind=str(seat["controller_kind"]),
                        ))
                except Exception as exc:
                    self.server.control.update_match_lifecycle(
                        created["match"]["match_id"], "error",
                        metadata={"last_lan_error": str(exc)[:1000]},
                    )
                    raise
                created["seats"] = self.server.control.list_seats(
                    created["match"]["match_id"],
                )
                self.server.control.audit(
                    auth["admin_id"], "match.create_lan", "match",
                    created["match"]["match_id"], "success",
                    {
                        "seat_count": len(created["seats"]),
                        "agent_seat_count": len(workers),
                        "human_seat_count": len([
                            seat for seat in created["seats"]
                            if seat["controller_kind"] == "human"
                        ]),
                        "host_controller_kind": str(
                            body.get("host_controller_kind", "agent")
                        ),
                    }, self.client_address[0],
                )
                result: dict[str, Any] = {
                    "ok": True, **created,
                    "workers": [self._redact_worker(item) for item in workers],
                }
                if body.get("start_now") is True:
                    result["started"] = manager.start_lan_match(
                        created["match"]["match_id"],
                        session_name=(str(body["session_name"])
                                      if body.get("session_name") is not None else None),
                        profile=profile,
                        scenario_id=(str(body["scenario_id"])
                                     if body.get("scenario_id") is not None else None),
                        game_settings=(body.get("game_settings")
                                       if isinstance(body.get("game_settings"), dict) else None),
                    )
                self._json(201, result)
                return
            if path == "/api/v1/harness-profiles/hermes":
                auth = self._authorize_mutation()
                manager = self._manager()
                body = self._body()
                match_id = str(body.get("match_id", ""))
                agent_id = str(body.get("agent_id", "")) or None
                worker = self.server.control.worker_for_match(match_id, agent_id=agent_id)
                observed = manager.worker_status(worker["instance_id"])
                mcp = observed.get("mcp") if isinstance(observed.get("mcp"), dict) else {}
                if not observed.get("running") or observed.get("health") != "healthy":
                    raise WorkerManagerError("game_worker_not_healthy")
                if not mcp.get("running") or mcp.get("health") != "healthy":
                    raise WorkerManagerError("managed_mcp_not_healthy")
                descriptor = self.server.control.prepare_hermes_profile(
                    match_id, str(body.get("provider_id", "")),
                    agent_id=agent_id,
                    reasoning_effort=str(body.get("reasoning_effort", "low")),
                    model_id=(str(body["model_id"]) if body.get("model_id") else None),
                    context_length=body.get("context_length"),
                    generation_settings=(body.get("generation_settings")
                                         if isinstance(body.get("generation_settings"), dict)
                                         else None),
                )
                self.server.control.audit(
                    auth["admin_id"], "harness.prepare_hermes", "match", match_id,
                    "success", {
                        "agent_id": descriptor["agent_id"],
                        "instance_id": descriptor["instance_id"],
                        "external_profile_id": descriptor["external_profile_id"],
                    }, self.client_address[0],
                )
                self._json(200, {"ok": True, "descriptor": descriptor})
                return
            if path == "/api/v1/harness-runs":
                auth = self._authorize_mutation()
                body = self._body()
                match_id = str(body.get("match_id", ""))
                agent_id = str(body.get("agent_id", "")) or None
                manager = self._manager()
                worker = self.server.control.worker_for_match(match_id, agent_id=agent_id)
                observed = manager.worker_status(worker["instance_id"])
                mcp = observed.get("mcp") if isinstance(observed.get("mcp"), dict) else {}
                if not observed.get("running") or observed.get("health") != "healthy":
                    raise WorkerManagerError("game_worker_not_healthy")
                if not mcp.get("running") or mcp.get("health") != "healthy":
                    raise WorkerManagerError("managed_mcp_not_healthy")
                try:
                    budget = int(body.get("run_budget_seconds", 3600))
                    max_turns = int(body.get("max_turns", 256))
                    restart_limit = int(body.get("restart_limit", 1000))
                except (TypeError, ValueError) as exc:
                    raise InvalidRecord("invalid_harness_run_limits") from exc
                descriptor = self.server.control.prepare_hermes_profile(
                    match_id, str(body.get("provider_id", "")), agent_id=agent_id,
                    reasoning_effort=str(body.get("reasoning_effort", "low")),
                    model_id=(str(body["model_id"]) if body.get("model_id") else None),
                    context_length=body.get("context_length"),
                    generation_settings=(body.get("generation_settings")
                                         if isinstance(body.get("generation_settings"), dict)
                                         else None),
                )
                run = self._harness_manager().create_run(
                    descriptor,
                    initial_prompt=(str(body["initial_prompt"])
                                    if body.get("initial_prompt") else None),
                    run_budget_seconds=budget,
                    max_turns=max_turns,
                    restart_limit=restart_limit,
                )
                self.server.control.audit(
                    auth["admin_id"], "harness.start", "harness_run", run["run_id"],
                    "success", {"match_id": match_id, "agent_id": descriptor["agent_id"],
                                "provider_secret_injected": descriptor["provider_requires_api_key"]},
                    self.client_address[0],
                )
                self._json(201, {"ok": True, "run": run})
                return
            if path == "/api/v1/graphiti":
                auth = self._authorize_mutation()
                body = self._body()
                if not isinstance(body.get("enabled"), bool):
                    raise InvalidRecord("invalid_graphiti_enabled")
                result = self.server.control.set_graphiti_enabled(
                    body["enabled"],
                    profile=body.get("profile") if isinstance(body.get("profile"), dict) else None,
                )
                self.server.control.audit(
                    auth["admin_id"], "graphiti.configure", "installation", None,
                    "success", {"enabled": body["enabled"]}, self.client_address[0],
                )
                self._json(200, result)
                return
            if path == "/api/v1/graphiti/clear-profile":
                auth = self._authorize_mutation()
                body = self._body()
                profile_id = str(body.get("profile_id", ""))
                result = self.server.control.clear_graphiti_profile(profile_id)
                self.server.control.audit(
                    auth["admin_id"], "graphiti.profile.clear", "agent_profile", profile_id,
                    "success", {"cleared": result["cleared"]}, self.client_address[0],
                )
                self._json(200, result)
                return
            if path == "/api/v1/graphiti/sync-profile":
                auth = self._authorize_mutation()
                body = self._body()
                if not isinstance(body.get("profile"), dict):
                    raise InvalidRecord("invalid_graphiti_profile")
                result = self.server.control.sync_graphiti_profile(body["profile"])
                self.server.control.audit(
                    auth["admin_id"], "graphiti.profile.sync", "agent_profile",
                    str(body["profile"].get("profile_id", "")), "success",
                    {"synced": result["synced"], "changed": result["changed"]},
                    self.client_address[0],
                )
                self._json(200, result)
                return
            if path == "/api/v1/graphiti/probe":
                auth = self._authorize_mutation()
                self._body()
                result = self.server.control.probe_graphiti_extraction()
                self.server.control.audit(
                    auth["admin_id"], "graphiti.probe", "installation", None,
                    "success" if result["accepted"] else "failure",
                    {"accepted": result["accepted"], "state": result["state"],
                     "profile_id": result["profile_id"]}, self.client_address[0],
                )
                self._json(200, result)
                return
            if path == "/api/v1/embeddings":
                auth = self._authorize_mutation()
                body = self._body()
                result = self.server.control.set_embedding_configuration(
                    mode=str(body.get("mode", "")),
                    provider_id=str(body["provider_id"]) if body.get("provider_id") else None,
                    model_id=str(body["model_id"]) if body.get("model_id") else None,
                    dimensions=int(body["dimensions"]) if body.get("dimensions") is not None else None,
                    space_id=str(body["space_id"]) if body.get("space_id") else None,
                )
                self.server.control.audit(
                    auth["admin_id"], "embeddings.configure", "installation", None,
                    "success", {key: value for key, value in result.items() if key != "api_key"},
                    self.client_address[0],
                )
                self._json(200, {"ok": True, "configuration": result})
                return
            if path == "/api/v1/storage-policy":
                auth = self._authorize_mutation()
                body = self._body()
                result = self.server.control.set_storage_policy(
                    recent_checkpoints=int(body.get("recent_checkpoints", 10)),
                    milestone_interval=int(body.get("milestone_interval", 25)),
                    retain_full_turn_history=body.get("retain_full_turn_history") is True,
                )
                self.server.control.audit(
                    auth["admin_id"], "storage.configure", "installation", None,
                    "success", result, self.client_address[0],
                )
                self._json(200, result)
                return
            if path == "/api/v1/graphiti/rebuild":
                auth = self._authorize_mutation()
                body = self._body()
                result = self.server.control.request_graphiti_rebuild(
                    str(body.get("match_id", "")), str(body.get("agent_id", "")),
                    str(body.get("perspective_id", "")), admin_id=auth["admin_id"],
                )
                self.server.control.audit(
                    auth["admin_id"], "graphiti.rebuild", "perspective",
                    result["perspective_id"], "success",
                    {"rebuild_id": result["rebuild_id"]}, self.client_address[0],
                )
                self._json(202, {"ok": True, "rebuild": result})
                return
            harness_run_match = HARNESS_RUN_PATH.fullmatch(path)
            if harness_run_match:
                auth = self._authorize_mutation()
                self._body()
                run_id, action = harness_run_match.groups()
                if action == "stop":
                    result = self._harness_manager().stop_run(run_id)
                elif action == "start":
                    current = self.server.control.update_harness_run(
                        run_id, desired_status="running", status="queued",
                    )
                    result = self._harness_manager().start_run(str(current["run_id"]))
                elif action == "telemetry":
                    result = self._harness_manager().telemetry(run_id)
                else:
                    result = self._harness_manager().status(run_id)
                self.server.control.audit(
                    auth["admin_id"], f"harness.{action}", "harness_run", run_id,
                    "success", {}, self.client_address[0],
                )
                self._json(200, {"ok": True, "result": result})
                return
            if path == "/api/v1/schedules":
                auth = self._authorize_mutation()
                body = self._body()
                try:
                    interval_seconds = int(body.get("interval_seconds", 0))
                except (TypeError, ValueError) as exc:
                    raise InvalidRecord("invalid_schedule_interval") from exc
                schedule = self._operations().create_schedule(
                    str(body.get("display_name", "")), str(body.get("operation_kind", "")),
                    target_kind=str(body.get("target_kind", "")),
                    target_id=(str(body["target_id"]) if body.get("target_id") is not None else None),
                    interval_seconds=interval_seconds,
                    next_run_unix=body.get("next_run_unix"),
                    payload=body.get("payload") if isinstance(body.get("payload"), dict) else None,
                )
                self.server.control.audit(
                    auth["admin_id"], "schedule.create", "schedule", schedule["schedule_id"],
                    "success", {"operation_kind": schedule["operation_kind"]},
                    self.client_address[0],
                )
                self._json(201, {"ok": True, "schedule": schedule})
                return
            schedule_match = SCHEDULE_PATH.fullmatch(path)
            if schedule_match:
                auth = self._authorize_mutation()
                self._body()
                schedule_id, action = schedule_match.groups()
                status = {"activate": "active", "pause": "paused", "disable": "disabled"}[action]
                schedule = self._operations().set_schedule_status(schedule_id, status)
                self.server.control.audit(
                    auth["admin_id"], f"schedule.{action}", "schedule", schedule_id,
                    "success", {}, self.client_address[0],
                )
                self._json(200, {"ok": True, "schedule": schedule})
                return
            if path == "/api/v1/backups":
                auth = self._authorize_mutation()
                body = self._body()
                backup = self._operations().create_backup(
                    include_secrets=body.get("include_secrets", True) is True,
                    include_workers=body.get("include_workers", True) is True,
                )
                self.server.control.audit(
                    auth["admin_id"], "backup.create", "backup", backup["backup_id"],
                    "success", {"worker_count": backup["worker_count"]}, self.client_address[0],
                )
                self._json(201, {"ok": True, "backup": backup})
                return
            backup_match = BACKUP_PATH.fullmatch(path)
            if backup_match:
                auth = self._authorize_mutation()
                self._body()
                backup_id = backup_match.group(1)
                verified = self._operations().verify_backup(backup_id)
                self.server.control.audit(
                    auth["admin_id"], "backup.verify", "backup", backup_id,
                    "success", {"size_bytes": verified["size_bytes"]}, self.client_address[0],
                )
                self._json(200, verified)
                return
            recovery_match = RECOVERY_PATH.fullmatch(path)
            if recovery_match:
                auth = self._authorize_mutation()
                manager = self._manager()
                body = self._body()
                match_id, action = recovery_match.groups()
                if action == "checkpoint":
                    result = manager.checkpoint_match(
                        match_id, slot=str(body.get("slot", "control_recovery")),
                    )
                elif action == "retry-after-update":
                    result = manager.retry_match_after_update(
                        match_id, str(body.get("incident_id", "")),
                    )
                else:
                    result = manager.recover_match(match_id)
                self.server.control.audit(
                    auth["admin_id"], f"match.{action}", "match", match_id,
                    "success", {"managed_recovery": True}, self.client_address[0],
                )
                self._json(200, result)
                return
            resolution_match = RESOLUTION_PATH.fullmatch(path)
            if resolution_match:
                auth = self._authorize_mutation()
                manager = self._manager()
                body = self._body()
                match_id = resolution_match.group(1)
                result = manager.set_match_resolution(
                    match_id, str(body.get("profile_id", "")),
                )
                self.server.control.audit(
                    auth["admin_id"], "match.resolution", "match", match_id,
                    "success", {"profile_id": result["profile_id"]},
                    self.client_address[0],
                )
                self._json(200, result)
                return
            controller_match = MATCH_CONTROLLER_PATH.fullmatch(path)
            if controller_match:
                auth = self._authorize_mutation()
                manager = self._manager()
                body = self._body()
                match_id, action = controller_match.groups()
                seat_index = int(body.get("seat_index", -1))
                if action == "host-seat":
                    result = manager.set_match_host(match_id, seat_index)
                else:
                    result = manager.set_match_seat_delegation(
                        match_id, seat_index, delegated=body.get("delegated") is True,
                    )
                self.server.control.audit(
                    auth["admin_id"], f"match.{action}", "match", match_id,
                    "success", {"seat_index": seat_index}, self.client_address[0],
                )
                self._json(200, result)
                return
            match = PROVIDER_PATH.fullmatch(path)
            if match:
                auth = self._authorize_mutation()
                provider_id, action = match.groups()
                if action == "probe-generation":
                    body = self._body()
                    result = self.server.control.probe_provider_generation(
                        provider_id, str(body.get("model_id", "")),
                        str(body.get("reasoning_effort", "none")),
                        body.get("generation"),
                    )
                    self.server.control.audit(
                        auth["admin_id"], "provider.probe_generation", "provider", provider_id,
                        "success" if result["accepted"] else "rejected",
                        {"model_id": body.get("model_id"), "http_status": result["http_status"]},
                        self.client_address[0],
                    )
                    self._json(200, result)
                    return
                if action == "delete":
                    self._body()
                    result = self.server.control.delete_provider(provider_id)
                    self.server.control.audit(
                        auth["admin_id"], "provider.delete", "provider", provider_id,
                        "success", {}, self.client_address[0],
                    )
                    self._json(200, result)
                    return
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
            worker_match = WORKER_PATH.fullmatch(path)
            if worker_match:
                auth = self._authorize_mutation()
                manager = self._manager()
                body = self._body()
                instance_id, action = worker_match.groups()
                if action == "start":
                    result = manager.start_worker(instance_id)
                elif action == "park":
                    result = manager.park_worker(instance_id)
                elif action == "spectator":
                    result = manager.spectator_access(
                        instance_id, interactive=body.get("interactive") is True,
                        compatibility=body.get("compatibility") is True,
                    )
                elif action == "chat":
                    result = manager.portal_chat(
                        instance_id,
                        action=str(body.get("action", "list")),
                        text=(str(body["text"]) if body.get("text") is not None else None),
                        recipient_faction_id=int(body.get("recipient_faction_id", 0)),
                        client_message_id=(str(body["client_message_id"])
                                           if body.get("client_message_id") else None),
                        after_sequence=int(body.get("after_sequence", 0)),
                    )
                elif action == "human-ui":
                    result = manager.human_ui_state(instance_id)
                elif action == "group-chat":
                    result = manager.portal_group_chat(
                        instance_id, action=str(body.get("action", "list")),
                        group_id=str(body.get("group_id", "")),
                        display_name=str(body.get("display_name", "")),
                        member_faction_ids=[int(item) for item in body.get(
                            "member_faction_ids", [])],
                        response=str(body.get("response", "")),
                        text=str(body.get("text", "")),
                    )
                else:
                    result = manager.worker_status(instance_id)
                self.server.control.audit(
                    auth["admin_id"], f"worker.{action}", "instance", instance_id,
                    "success", {"status": result.get("status") or result.get("health")},
                    self.client_address[0],
                )
                self._json(200, result)
                return
            match_action = MATCH_PATH.fullmatch(path)
            if match_action:
                auth = self._authorize_mutation()
                manager = self._manager()
                body = self._body()
                if body.get("game_settings") is not None \
                        and not isinstance(body.get("game_settings"), dict):
                    raise InvalidRecord("invalid_lan_game_settings")
                match_id, action = match_action.groups()
                if action == "start":
                    current_match = self.server.control.get_match(match_id)
                    if current_match["mode"] == "singleplayer":
                        worker = self.server.control.worker_for_match(match_id)
                        started = manager.start_worker(str(worker["instance_id"]))
                        running = self.server.control.update_match_lifecycle(
                            match_id, "running", host_instance_id=str(worker["instance_id"]),
                        )
                        result = {"ok": True, "match": running, "worker": started}
                    else:
                        result = manager.start_lan_match(
                            match_id,
                            session_name=str(body.get("session_name", "SMACX Managed LAN")),
                            profile=str(body.get("profile", "small_easy")),
                            resume_slot=(str(body["resume_slot"])
                                         if body.get("resume_slot") is not None else None),
                            scenario_id=(str(body["scenario_id"])
                                         if body.get("scenario_id") is not None else None),
                            game_settings=(body.get("game_settings")
                                           if isinstance(body.get("game_settings"), dict) else None),
                        )
                elif action == "park":
                    # Stop autonomous callers before touching native runtime
                    # state.  This keeps direct API users as safe as the portal
                    # and makes park idempotent across supervisor retries.
                    for run in self.server.control.list_harness_runs():
                        if run.get("match_id") == match_id and run.get("status") in {
                            "queued", "starting", "running", "restarting",
                        }:
                            self._harness_manager().stop_run(str(run["run_id"]))
                    result = manager.park_match(match_id)
                elif action == "complete":
                    # Native victory detection can mark the match completed
                    # before the portal observes it. Retiring that campaign
                    # must still stop autonomous callers before releasing its
                    # worker volumes and preserving the final checkpoint.
                    for run in self.server.control.list_harness_runs():
                        if run.get("match_id") == match_id and run.get("status") in {
                            "queued", "starting", "running", "restarting",
                        }:
                            self._harness_manager().stop_run(str(run["run_id"]))
                    result = manager.complete_match(match_id)
                elif action == "status":
                    result = manager.lan_match_status(match_id)
                elif action == "discover-external-host":
                    result = manager.discover_human_hosted_lan_match(
                        match_id, host_address=str(body.get("host_address", "")),
                    )
                elif action == "join-external-host":
                    result = manager.join_human_hosted_lan_match(
                        match_id,
                        host_address=str(body.get("host_address", "")),
                        network_session_id=str(body.get("network_session_id", "")),
                    )
                else:
                    result = manager.finalize_human_hosted_lan_match(match_id)
                self.server.control.audit(
                    auth["admin_id"], f"match.{action}", "match", match_id,
                    "success", {"managed_lan": True}, self.client_address[0],
                )
                self._json(200, result)
                return
            self._error(404, "not_found")
        except Exception as exc:
            self._handle_exception(exc)

    def _rate_limit(self, operation: str) -> None:
        key = f"{operation}:{self.client_address[0]}"
        if not self.server.login_limiter.allow(key):
            raise AuthenticationError("too_many_attempts")

    def _manager(self) -> WorkerManager:
        if self.server.worker_manager is None:
            raise WorkerManagerError("docker_manager_disabled")
        return self.server.worker_manager

    def _operations(self) -> OperationsManager:
        if self.server.operations is None:
            raise StoreError("operations_manager_disabled")
        return self.server.operations

    def _harness_manager(self) -> HarnessManager:
        if self.server.harness_manager is None:
            raise StoreError("harness_manager_disabled")
        return self.server.harness_manager

    @staticmethod
    def _redact_worker(worker: dict[str, Any]) -> dict[str, Any]:
        result = dict(worker)
        result.pop("bridge_secret_id", None)
        result.pop("view_secret_id", None)
        return result

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
            code = str(exc)
            messages = {
                "provider_display_name_already_exists":
                    "A model endpoint already uses that display name. Choose a different name.",
            }
            self._error(400, code, messages.get(code))
        elif isinstance(exc, ProviderError):
            self._error(502, str(exc))
        elif isinstance(exc, DockerUnavailable):
            self._error(503, "docker_engine_unavailable")
        elif isinstance(exc, DockerError):
            self._error(502, "docker_operation_failed")
        elif isinstance(exc, WorkerManagerError):
            self._error(409, str(exc))
        elif isinstance(exc, StoreError):
            code = str(exc)
            messages = {
                "provider_in_use_by_harness_profile":
                    "This model endpoint is used by historical harness configuration and cannot be removed.",
            }
            self._error(409, code, messages.get(code))
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
    store = SmacxStore(database)
    control = ControlPlane(store, secret_root)
    control.ensure_graphiti_setting(
        default_enabled=os.environ.get("SMACX_GRAPHITI_DEFAULT_ENABLED", "0") == "1",
    )
    return control


def ensure_portal_service_token(data_root: Path) -> str:
    configured = os.environ.get("SMACX_PORTAL_SERVICE_TOKEN_FILE")
    token_path = Path(configured) if configured else data_root / "secrets" / "portal-service-token"
    token_path = token_path.expanduser().resolve()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if not token_path.exists():
        token_path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        if os.name != "nt":
            token_path.chmod(0o600)
    token = token_path.read_text(encoding="utf-8").strip()
    if len(token) < 32 or len(token) > 256:
        raise SystemExit("invalid_portal_service_token")
    return token


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
    backup = commands.add_parser("backup", help="create or verify an offline-safe backup")
    backup.add_argument("action", choices=("create", "list", "verify"))
    backup.add_argument("--backup-id")
    restore = commands.add_parser("restore", help="restore control state while the server is stopped")
    restore.add_argument("--backup-id", required=True)
    restore.add_argument("--confirm-installation", required=True)
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
    if command == "backup":
        operations = OperationsManager(control, data_root=Path(arguments.data_root))
        if arguments.action == "create":
            result = operations.create_backup(include_secrets=True, include_workers=False)
        elif arguments.action == "list":
            result = {"ok": True, "backups": operations.list_backups()}
        else:
            if not arguments.backup_id:
                raise SystemExit("--backup-id is required for verify")
            result = operations.verify_backup(arguments.backup_id)
        print(json.dumps(result, sort_keys=True))
        return 0
    if command == "restore":
        lock_path = Path(arguments.data_root).expanduser().resolve() / ".control-server.lock"
        lock_path.touch(mode=0o600, exist_ok=True)
        with lock_path.open("r+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SystemExit("control_server_must_be_stopped_for_restore") from exc
            result = restore_backup_offline(
                control, Path(arguments.data_root), arguments.backup_id,
                confirm_installation_id=arguments.confirm_installation,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    lock_path = Path(arguments.data_root).expanduser().resolve() / ".control-server.lock"
    lock_path.touch(mode=0o600, exist_ok=True)
    server_lock = lock_path.open("r+")
    try:
        fcntl.flock(server_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        server_lock.close()
        raise SystemExit("control_server_already_running") from exc
    control.ensure_bootstrap_token()
    worker_manager = None
    harness_manager = None
    if os.environ.get("SMACX_DOCKER_ENABLED", "0") == "1":
        worker_manager = WorkerManager(
            control,
            DockerClient(os.environ.get("SMACX_DOCKER_SOCKET", "/var/run/docker.sock")),
            worker_image=os.environ.get("SMACX_WORKER_IMAGE", "smacx-agent-worker:dev"),
            mcp_image=os.environ.get("SMACX_MCP_IMAGE", "smacx-agent-control:dev"),
            network_name=os.environ.get("SMACX_DOCKER_NETWORK") or None,
            control_data_volume=os.environ.get("SMACX_CONTROL_DATA_VOLUME") or None,
            directx_redist_host_path=os.environ.get("SMACX_DIRECTX_REDIST_HOST") or None,
            view_publish_ip=os.environ.get("SMACX_VIEW_PUBLISH_IP", "127.0.0.1"),
        )
        # Ordinary operation has one compatibility stack owned by Docker.  A
        # user supplies only their legal Alien Crossfire directory; startup
        # validates it and records both assets idempotently before serving UI.
        worker_manager.ensure_bundled_runtime()
        configured_game_source = os.environ.get("SMACX_GAME_SOURCE")
        if configured_game_source:
            known_source = next((
                item for item in control.list_game_sources()
                if item.get("host_path") == configured_game_source
            ), None)
            # Re-hash on service startup so changed game/mod files select a new
            # prepared image instead of silently reusing a stale legal-source
            # layer. The stable source id keeps lobbies and history intact.
            worker_manager.validate_game_source(
                configured_game_source,
                display_name=str(known_source.get("display_name", "Alien Crossfire"))
                    if known_source else "Alien Crossfire",
                game_source_id=str(known_source["game_source_id"]) if known_source else None,
            )
        elif os.environ.get("SMACX_REQUIRE_GAME_SOURCE", "1") == "1":
            raise SystemExit("SMACX_GAME_SOURCE must point to a directory containing terranx.exe")
        harness_manager = HarnessManager(
            control, worker_manager.docker, worker_manager,
            image_ref=os.environ.get("SMACX_HERMES_IMAGE") or HERMES_IMAGE,
        )
    host = getattr(arguments, "host", os.environ.get("SMACX_CONTROL_HOST", "127.0.0.1"))
    port = getattr(arguments, "port", int(os.environ.get("SMACX_CONTROL_PORT", "8080")))
    static_root = getattr(
        arguments, "static_root",
        str(Path(__file__).resolve().parents[1] / "control_center/static"),
    )
    if not 1 <= port <= 65535:
        raise SystemExit("invalid port")
    operations = OperationsManager(
        control, data_root=Path(arguments.data_root), worker_manager=worker_manager,
        harness_manager=harness_manager,
    )
    operations.start(interval_seconds=float(os.environ.get("SMACX_SUPERVISOR_INTERVAL", "10")))
    server = ControlHTTPServer(
        (host, port), control, Path(static_root),
        secure_cookies=os.environ.get("SMACX_SECURE_COOKIES", "0") == "1",
        worker_manager=worker_manager, operations=operations,
        harness_manager=harness_manager,
        service_token=ensure_portal_service_token(Path(arguments.data_root)),
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
        operations.stop()
        server.server_close()
        server_lock.close()
        print(json.dumps({"event": "control_stopped"}, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
