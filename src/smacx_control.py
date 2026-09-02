"""Authenticated control-plane state for the always-on SMACX platform.

This module deliberately contains no web framework or Docker dependency.  The
HTTP service, CLI, and future alternative front ends all share these identity,
authentication, secret, and provider-discovery contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import ssl
import tempfile
import threading
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
import uuid

from smacx_store import InvalidRecord, MemoryScope, ScopeViolation, SmacxStore, StoreError
from smacx_prompt import (
    PERSONALITY_NONE, SYSTEM_PROMPT_SCHEMA, compose_player_system_prompt,
    prompt_sha256,
)
from smacx_generation import (
    GenerationSettingsError, direct_reasoning_parameters,
    normalize_generation_settings, openai_extra_body,
)


CONTROL_ID = re.compile(r"^[A-Za-z0-9_-]{8,96}$")
CONTROL_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
MODEL_ID_LIMIT = 512
HERMES_MINIMUM_CONTEXT_LENGTH = 65_536
PROVIDER_RESPONSE_LIMIT = 4 * 1024 * 1024
PASSWORD_MINIMUM_LENGTH = 8
PASSWORD_PARAMETERS = {"algorithm": "scrypt", "n": 32768, "r": 8, "p": 1, "dklen": 32}


class AuthenticationError(StoreError):
    pass


class ProviderError(StoreError):
    pass


def _new_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex}"


def _require_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not CONTROL_ID.fullmatch(value):
        raise InvalidRecord(f"invalid_{field}")
    return value


def _require_key(value: str, field: str) -> str:
    if not isinstance(value, str) or not CONTROL_KEY.fullmatch(value):
        raise InvalidRecord(f"invalid_{field}")
    return value


def _bounded(value: str, field: str, maximum: int, *, minimum: int = 1) -> str:
    if not isinstance(value, str):
        raise InvalidRecord(f"invalid_{field}")
    result = value.strip()
    if len(result) < minimum or len(result) > maximum:
        raise InvalidRecord(f"invalid_{field}")
    return result


def _json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _password_hash(password: str, salt: bytes, parameters: Mapping[str, Any]) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=int(parameters["n"]),
        r=int(parameters["r"]),
        p=int(parameters["p"]),
        dklen=int(parameters["dklen"]),
        maxmem=64 * 1024 * 1024,
    )


def canonical_provider_url(value: str) -> str:
    value = _bounded(value, "provider_base_url", 2048)
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InvalidRecord("invalid_provider_base_url")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise InvalidRecord("invalid_provider_base_url")
    path = parts.path.rstrip("/")
    if not path:
        path = "/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _context_length(model: Mapping[str, Any]) -> int | None:
    candidate_keys = (
        "context_length", "max_context_length", "max_model_len",
        "context_window", "max_position_embeddings",
    )
    queues: list[Mapping[str, Any]] = [model]
    seen = 0
    while queues and seen < 32:
        current = queues.pop(0)
        seen += 1
        for key in candidate_keys:
            value = current.get(key)
            if isinstance(value, int) and 1024 <= value <= 16_777_216:
                return value
            if isinstance(value, str) and value.isdigit():
                parsed = int(value)
                if 1024 <= parsed <= 16_777_216:
                    return parsed
        for value in current.values():
            if isinstance(value, Mapping):
                queues.append(value)
    return None


@dataclass(frozen=True)
class AuthSession:
    auth_session_id: str
    admin_id: str
    username: str
    token: str
    csrf_token: str
    expires_unix: float


class SecretVault:
    """Small local file-secret vault with database-backed integrity metadata."""

    def __init__(self, store: SmacxStore, root: Path | str) -> None:
        self.store = store
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self._lock = threading.RLock()

    def _path(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        if path.parent != self.root:
            raise ScopeViolation("secret_path_outside_vault")
        return path

    def put(self, purpose: str, value: str, *, secret_id: str | None = None) -> dict[str, Any]:
        purpose = _require_key(purpose, "secret_purpose")
        if not isinstance(value, str) or not 1 <= len(value) <= 65_536 or "\x00" in value:
            raise InvalidRecord("invalid_secret_value")
        secret_id = secret_id or _new_id("secret")
        _require_id(secret_id, "secret_id")
        relative_path = f"{secret_id}.secret"
        fingerprint = hashlib.sha256(value.encode("utf-8")).hexdigest()
        now = time.time()
        path = self._path(relative_path)
        with self._lock:
            with self.store.transaction() as connection:
                existing = connection.execute(
                    "SELECT * FROM secret_refs WHERE secret_id = ?", (secret_id,),
                ).fetchone()
                if existing and existing["purpose"] != purpose:
                    raise ScopeViolation("secret_purpose_mismatch")
            descriptor, temporary_name = tempfile.mkstemp(prefix=".secret-", dir=self.root)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, path)
                os.chmod(path, 0o600)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
            with self.store.transaction() as connection:
                connection.execute(
                    "INSERT INTO secret_refs(secret_id, purpose, relative_path, fingerprint, status, "
                    "created_unix, updated_unix) VALUES (?, ?, ?, ?, 'active', ?, ?) "
                    "ON CONFLICT(secret_id) DO UPDATE SET relative_path=excluded.relative_path, "
                    "fingerprint=excluded.fingerprint, status='active', updated_unix=excluded.updated_unix",
                    (secret_id, purpose, relative_path, fingerprint, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM secret_refs WHERE secret_id = ?", (secret_id,),
                ).fetchone()
            return dict(row)

    def read(self, secret_id: str, *, purpose: str | None = None) -> str:
        _require_id(secret_id, "secret_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM secret_refs WHERE secret_id = ? AND status = 'active'", (secret_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_or_revoked_secret")
        if purpose is not None and row["purpose"] != purpose:
            raise ScopeViolation("secret_purpose_mismatch")
        path = self._path(str(row["relative_path"]))
        try:
            value = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StoreError("secret_file_unavailable") from exc
        if hashlib.sha256(value.encode("utf-8")).hexdigest() != row["fingerprint"]:
            raise StoreError("secret_integrity_failure")
        return value

    def path_for_mount(self, secret_id: str, *, purpose: str) -> Path:
        # Validate content and metadata before exposing an exact path to the
        # worker manager.  The secret itself is never returned through HTTP.
        self.read(secret_id, purpose=purpose)
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT relative_path FROM secret_refs WHERE secret_id = ?", (secret_id,),
            ).fetchone()
        assert row is not None
        return self._path(str(row["relative_path"]))

    def revoke(self, secret_id: str) -> bool:
        _require_id(secret_id, "secret_id")
        with self._lock, self.store.transaction() as connection:
            row = connection.execute(
                "SELECT relative_path, status FROM secret_refs WHERE secret_id = ?", (secret_id,),
            ).fetchone()
            if not row or row["status"] == "revoked":
                return False
            connection.execute(
                "UPDATE secret_refs SET status='revoked', updated_unix=? WHERE secret_id=?",
                (time.time(), secret_id),
            )
            path = self._path(str(row["relative_path"]))
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return True


class ControlPlane:
    """Control Center domain service with redacted return values."""

    def __init__(self, store: SmacxStore, secret_root: Path | str) -> None:
        self.store = store
        self.vault = SecretVault(store, secret_root)
        self._bootstrap_lock = threading.RLock()

    def _setting(self, key: str) -> Any | None:
        _require_key(key, "setting_key")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT value_json FROM control_settings WHERE setting_key = ?", (key,),
            ).fetchone()
        return json.loads(row["value_json"]) if row else None

    def _set_setting(self, key: str, value: Any) -> None:
        _require_key(key, "setting_key")
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO control_settings(setting_key, value_json, updated_unix) VALUES (?, ?, ?) "
                "ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json, "
                "updated_unix=excluded.updated_unix",
                (key, json.dumps(value, sort_keys=True, separators=(",", ":")), time.time()),
            )

    def ensure_graphiti_setting(self, *, default_enabled: bool = False) -> dict[str, Any]:
        current = self._setting("graphiti.enabled")
        if current is None:
            self._set_setting("graphiti.enabled", bool(default_enabled))
        if self._setting("embeddings.configuration") is None:
            self._set_setting("embeddings.configuration", {"mode": "local"})
        return self.graphiti_status()

    def set_graphiti_enabled(self, enabled: bool, *, profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise InvalidRecord("invalid_graphiti_enabled")
        if not enabled:
            with self.store.transaction() as connection:
                connection.execute(
                    "DELETE FROM control_settings WHERE setting_key = 'graphiti.profile'"
                )
            self._set_setting("graphiti.enabled", False)
            return self.graphiti_status()
        if profile is not None:
            self._set_setting("graphiti.profile", self._normalize_graphiti_profile(profile))
        if enabled and not isinstance(self._setting("graphiti.profile"), Mapping):
            raise InvalidRecord("graphiti_extraction_profile_required")
        if enabled and self.embedding_configuration()["mode"] == "disabled":
            raise InvalidRecord("graphiti_requires_embeddings")
        self._set_setting("graphiti.enabled", True)
        return self.graphiti_status()

    def _normalize_graphiti_profile(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        provider_id = _require_id(str(profile.get("provider_id", "")), "provider_id")
        model_id = _bounded(str(profile.get("model_id", "")), "model_id", MODEL_ID_LIMIT)
        provider = self.get_provider(provider_id)
        if not any(item["model_id"] == model_id for item in provider["models"]):
            raise ScopeViolation("unknown_provider_model")
        reasoning_effort = str(profile.get("reasoning_effort") or "none").strip().lower()
        try:
            direct_reasoning_parameters(reasoning_effort)
            generation = normalize_generation_settings(
                profile.get("generation_settings")
                if isinstance(profile.get("generation_settings"), Mapping) else None,
            )
        except GenerationSettingsError as exc:
            raise InvalidRecord(str(exc)) from exc
        result = {
            "profile_id": _bounded(str(profile.get("profile_id", "")), "profile_id", 160),
            "display_name": _bounded(
                str(profile.get("display_name", "")), "profile_name", 160,
            ),
            "provider_id": provider_id,
            "model_id": model_id,
            "reasoning_effort": reasoning_effort,
            "generation_settings": generation,
        }
        result["profile_fingerprint"] = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return result

    def sync_graphiti_profile(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Refresh the selected extraction profile without changing enablement."""
        current = self._setting("graphiti.profile")
        requested_id = str(profile.get("profile_id", ""))
        if not isinstance(current, Mapping) or current.get("profile_id") != requested_id:
            return {**self.graphiti_status(), "synced": False, "changed": False}
        normalized = self._normalize_graphiti_profile(profile)
        changed = dict(current) != normalized
        if changed:
            self._set_setting("graphiti.profile", normalized)
        return {**self.graphiti_status(), "synced": True, "changed": changed}

    def probe_graphiti_extraction(self, *, timeout: float = 120.0) -> dict[str, Any]:
        profile = self._setting("graphiti.profile")
        if self._setting("graphiti.enabled") is not True or not isinstance(profile, Mapping):
            raise InvalidRecord("graphiti_not_configured")
        endpoint = os.environ.get(
            "SMACX_GRAPHITI_RECALL_URL", "http://graphiti-projector:8091",
        ).rstrip("/")
        request = Request(
            endpoint + "/probe", data=b"{}",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        status_code: int | None = None
        payload: dict[str, Any] = {}
        try:
            with urlopen(request, timeout=min(max(float(timeout), 1.0), 180.0)) as response:
                status_code = int(response.status)
                raw = response.read(PROVIDER_RESPONSE_LIMIT + 1)
        except HTTPError as exc:
            status_code = int(exc.code)
            raw = exc.read(PROVIDER_RESPONSE_LIMIT + 1)
        except (URLError, TimeoutError, OSError) as exc:
            raw = b""
            payload = {"error": f"graphiti_probe_unavailable:{type(exc).__name__}"}
        if raw:
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, dict):
                    payload = decoded
            except json.JSONDecodeError:
                payload = {"error": "graphiti_probe_invalid_response"}
        accepted = bool(status_code and 200 <= status_code < 300 and payload.get("ok") is True)
        state = "accepted" if accepted else str(payload.get("state") or "rejected")
        result = {
            "ok": True,
            "accepted": accepted,
            "state": state,
            "http_status": status_code,
            "profile_id": profile.get("profile_id"),
            "profile_fingerprint": profile.get("profile_fingerprint"),
            "reasoning_effort": payload.get("reasoning_effort", profile.get("reasoning_effort")),
            "structured_output": bool(payload.get("structured_output")),
            "duration_ms": payload.get("duration_ms"),
            "message": str(payload.get("message") or payload.get("error") or (
                "The active Graphiti adapter produced a valid structured extraction."
                if accepted else "The active Graphiti adapter did not complete its extraction probe."
            ))[:1000],
            "tested_unix": time.time(),
        }
        self._set_setting("graphiti.last_probe", result)
        return result

    def clear_graphiti_profile(self, profile_id: str) -> dict[str, Any]:
        profile_id = _require_id(profile_id, "profile_id")
        cleared = False
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT value_json FROM control_settings WHERE setting_key = 'graphiti.profile'"
            ).fetchone()
            current = json.loads(row["value_json"]) if row else None
            if isinstance(current, Mapping) and current.get("profile_id") == profile_id:
                connection.execute(
                    "DELETE FROM control_settings WHERE setting_key = 'graphiti.profile'"
                )
                connection.execute(
                    "INSERT INTO control_settings(setting_key, value_json, updated_unix) "
                    "VALUES ('graphiti.enabled', 'false', ?) "
                    "ON CONFLICT(setting_key) DO UPDATE SET value_json='false', "
                    "updated_unix=excluded.updated_unix",
                    (time.time(),),
                )
                cleared = True
        result = self.graphiti_status()
        result["cleared"] = cleared
        return result

    def embedding_configuration(self) -> dict[str, Any]:
        configured = self._setting("embeddings.configuration")
        if not isinstance(configured, Mapping):
            configured = {"mode": "local"}
        result = dict(configured)
        result.setdefault("mode", "local")
        if result["mode"] == "external" and result.get("provider_id"):
            try:
                provider = self.get_provider(str(result["provider_id"]))
                result["provider_name"] = provider["display_name"]
                result["provider_status"] = provider["status"]
            except StoreError:
                result["provider_status"] = "missing"
        return result

    def set_embedding_configuration(self, *, mode: str, provider_id: str | None = None,
                                    model_id: str | None = None, dimensions: int | None = None,
                                    space_id: str | None = None) -> dict[str, Any]:
        if mode not in {"local", "external", "disabled"}:
            raise InvalidRecord("invalid_embedding_mode")
        result: dict[str, Any] = {"mode": mode}
        if mode == "external":
            provider_id = _require_id(provider_id or "", "provider_id")
            model_id = _bounded(model_id or "", "model_id", MODEL_ID_LIMIT)
            dimensions = int(dimensions or 0)
            if not 1 <= dimensions <= 65_536:
                raise InvalidRecord("invalid_embedding_dimensions")
            space_id = _bounded(space_id or "", "embedding_space_id", 240)
            provider = self.get_provider(provider_id)
            if not any(item["model_id"] == model_id for item in provider["models"]):
                raise ScopeViolation("unknown_provider_model")
            result.update({
                "provider_id": provider_id, "model_id": model_id,
                "dimensions": dimensions, "space_id": space_id,
            })
        self._set_setting("embeddings.configuration", result)
        if mode == "disabled":
            self.set_graphiti_enabled(False)
        return self.embedding_configuration()

    def storage_policy(self) -> dict[str, Any]:
        configured = self._setting("storage.save_retention")
        if not isinstance(configured, Mapping):
            configured = {}
        campaign_root = self.store.path.parent / "campaigns"
        archive_files = [item for item in campaign_root.rglob("*") if item.is_file()] \
            if campaign_root.is_dir() else []
        final_saves = [item for item in archive_files if item.name.endswith(".sav.zst")]
        return {
            "ok": True,
            "recent_checkpoints": int(configured.get("recent_checkpoints", 10)),
            "milestone_interval": int(configured.get("milestone_interval", 25)),
            "retain_full_turn_history": configured.get("retain_full_turn_history") is True,
            "compression": "zstd",
            "completed_match_saves": 1,
            "completed_archive_files": len(archive_files),
            "completed_archive_saves": len(final_saves),
            "completed_campaigns": len({item.parents[1] for item in final_saves}),
            "completed_archive_bytes": sum(item.stat().st_size for item in archive_files),
        }

    def set_storage_policy(self, *, recent_checkpoints: int,
                           milestone_interval: int,
                           retain_full_turn_history: bool) -> dict[str, Any]:
        recent = int(recent_checkpoints)
        milestone = int(milestone_interval)
        if not 1 <= recent <= 250:
            raise InvalidRecord("invalid_recent_checkpoint_retention")
        if not 0 <= milestone <= 10_000:
            raise InvalidRecord("invalid_milestone_interval")
        if not isinstance(retain_full_turn_history, bool):
            raise InvalidRecord("invalid_full_turn_history_setting")
        self._set_setting("storage.save_retention", {
            "recent_checkpoints": recent,
            "milestone_interval": milestone,
            "retain_full_turn_history": retain_full_turn_history,
        })
        return self.storage_policy()

    def graphiti_status(self) -> dict[str, Any]:
        enabled = self._setting("graphiti.enabled") is True
        profile = self._setting("graphiti.profile")
        last_probe = self._setting("graphiti.last_probe")
        with self.store.transaction() as connection:
            state = connection.execute(
                "SELECT * FROM graphiti_runtime_state WHERE singleton=1"
            ).fetchone()
            queued = int(connection.execute(
                "SELECT count(*) FROM graphiti_rebuild_requests "
                "WHERE status IN ('queued','running')"
            ).fetchone()[0])
            scopes = [dict(row) for row in connection.execute(
                "SELECT p.match_id, m.display_name AS match_name, p.agent_id, "
                "a.display_name AS agent_name, p.perspective_id, p.status "
                "FROM perspectives p JOIN matches m ON m.match_id=p.match_id "
                "JOIN agents a ON a.agent_id=p.agent_id "
                "ORDER BY m.created_unix DESC, a.display_name"
            ).fetchall()]
        runtime = dict(state) if state else {
            "status": "stopped", "backend": "falkordb", "projected_events": 0,
            "failed_events": 0, "active_scopes": 0, "last_heartbeat_unix": None,
            "last_projection_unix": None, "last_error": None, "metadata_json": "{}",
        }
        runtime["metadata"] = json.loads(runtime.pop("metadata_json"))
        if isinstance(last_probe, Mapping) and isinstance(profile, Mapping) and \
                last_probe.get("profile_fingerprint") != profile.get("profile_fingerprint"):
            last_probe = {
                **dict(last_probe), "accepted": None, "state": "stale",
                "message": "The extraction profile changed after its last Graphiti probe.",
            }
        return {"ok": True, "enabled": enabled, "configured": isinstance(profile, Mapping),
                "profile": profile if isinstance(profile, Mapping) else None,
                "last_probe": last_probe if isinstance(last_probe, Mapping) else None,
                "embeddings": self.embedding_configuration(), "runtime": runtime,
                "queued_rebuilds": queued, "scopes": scopes,
                "sqlite_authoritative": True}

    def request_graphiti_rebuild(self, match_id: str, agent_id: str,
                                 perspective_id: str, *,
                                 admin_id: str | None = None) -> dict[str, Any]:
        scope = MemoryScope(match_id, agent_id, perspective_id)
        self.store.require_scope(scope)
        identifier = _new_id("rebuild")
        now = time.time()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO graphiti_rebuild_requests(rebuild_id, match_id, agent_id, "
                "perspective_id, status, requested_by_admin_id, created_unix) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                (identifier, match_id, agent_id, perspective_id, admin_id, now),
            )
        return {"rebuild_id": identifier, "match_id": match_id, "agent_id": agent_id,
                "perspective_id": perspective_id, "status": "queued", "created_unix": now}

    def admin_exists(self) -> bool:
        with self.store.transaction() as connection:
            return connection.execute(
                "SELECT 1 FROM control_admins WHERE status='active' LIMIT 1"
            ).fetchone() is not None

    def ensure_bootstrap_token(self) -> dict[str, Any]:
        """Create the first-run token, returning metadata but never its value."""
        with self._bootstrap_lock:
            if self.admin_exists():
                return {"setup_required": False, "bootstrap_token_path": None, "created": False}
            secret_id = self._setting("bootstrap_secret_id")
            created = False
            if not isinstance(secret_id, str):
                raw = secrets.token_urlsafe(32)
                record = self.vault.put("control.bootstrap", raw)
                secret_id = record["secret_id"]
                self._set_setting("bootstrap_secret_id", secret_id)
                created = True
            path = self.vault.path_for_mount(secret_id, purpose="control.bootstrap")
            return {
                "setup_required": True,
                "bootstrap_token_path": str(path),
                "created": created,
            }

    def reveal_bootstrap_token(self) -> str:
        """CLI-only operation; web routes must never call this method."""
        state = self.ensure_bootstrap_token()
        if not state["setup_required"]:
            raise AuthenticationError("setup_already_completed")
        secret_id = self._setting("bootstrap_secret_id")
        if not isinstance(secret_id, str):
            raise AuthenticationError("bootstrap_token_unavailable")
        return self.vault.read(secret_id, purpose="control.bootstrap")

    def bootstrap_admin(self, bootstrap_token: str, password: str, *, username: str = "admin") -> dict[str, Any]:
        if not USERNAME.fullmatch(username):
            raise InvalidRecord("invalid_admin_username")
        if not isinstance(password, str) or not PASSWORD_MINIMUM_LENGTH <= len(password) <= 1024:
            raise InvalidRecord("invalid_admin_password")
        with self._bootstrap_lock:
            if self.admin_exists():
                raise AuthenticationError("setup_already_completed")
            secret_id = self._setting("bootstrap_secret_id")
            if not isinstance(secret_id, str):
                self.ensure_bootstrap_token()
                secret_id = self._setting("bootstrap_secret_id")
            assert isinstance(secret_id, str)
            expected = self.vault.read(secret_id, purpose="control.bootstrap")
            if not hmac.compare_digest(expected, bootstrap_token):
                raise AuthenticationError("invalid_bootstrap_token")
            salt = secrets.token_bytes(32)
            password_hash = _password_hash(password, salt, PASSWORD_PARAMETERS)
            admin_id = _new_id("admin")
            now = time.time()
            with self.store.transaction() as connection:
                if connection.execute("SELECT 1 FROM control_admins LIMIT 1").fetchone():
                    raise AuthenticationError("setup_already_completed")
                connection.execute(
                    "INSERT INTO control_admins(admin_id, username, password_salt, password_hash, "
                    "password_parameters_json, status, created_unix, updated_unix) "
                    "VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
                    (admin_id, username, salt, password_hash, _json(PASSWORD_PARAMETERS), now, now),
                )
                self._append_audit(
                    connection, admin_id, "control.bootstrap", "admin", admin_id, "success", {}, None,
                )
            self.vault.revoke(secret_id)
            self._set_setting("bootstrap_completed", {"admin_id": admin_id, "completed_unix": now})
            return {"admin_id": admin_id, "username": username, "status": "active"}

    def login(self, username: str, password: str, *, ttl_seconds: int = 43_200,
              remote_address: str | None = None) -> AuthSession:
        ttl_seconds = min(max(int(ttl_seconds), 900), 604_800)
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM control_admins WHERE username = ? COLLATE NOCASE AND status='active'",
                (username,),
            ).fetchone()
        valid = False
        if row and isinstance(password, str) and len(password) <= 1024:
            try:
                parameters = json.loads(row["password_parameters_json"])
                candidate = _password_hash(password, bytes(row["password_salt"]), parameters)
                valid = hmac.compare_digest(candidate, bytes(row["password_hash"]))
            except (KeyError, TypeError, ValueError):
                valid = False
        if not valid or not row:
            self.audit(None, "auth.login", "admin", None, "denied", {}, remote_address)
            raise AuthenticationError("invalid_credentials")
        token = secrets.token_urlsafe(48)
        csrf_token = secrets.token_urlsafe(32)
        auth_session_id = _new_id("auth")
        now = time.time()
        expires = now + ttl_seconds
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO control_sessions(auth_session_id, admin_id, token_hash, csrf_hash, "
                "created_unix, last_seen_unix, expires_unix) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (auth_session_id, row["admin_id"], _digest(token), _digest(csrf_token), now, now, expires),
            )
            self._append_audit(
                connection, row["admin_id"], "auth.login", "admin", row["admin_id"],
                "success", {}, remote_address,
            )
        return AuthSession(auth_session_id, row["admin_id"], row["username"], token, csrf_token, expires)

    def authenticate(self, token: str, *, touch: bool = True) -> dict[str, Any]:
        if not isinstance(token, str) or len(token) > 512:
            raise AuthenticationError("invalid_session")
        now = time.time()
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT s.*, a.username, a.status AS admin_status FROM control_sessions s "
                "JOIN control_admins a ON a.admin_id=s.admin_id WHERE s.token_hash=?",
                (_digest(token),),
            ).fetchone()
            if not row or row["revoked_unix"] is not None or row["expires_unix"] <= now \
                    or row["admin_status"] != "active":
                raise AuthenticationError("invalid_session")
            if touch and now - float(row["last_seen_unix"]) >= 30:
                connection.execute(
                    "UPDATE control_sessions SET last_seen_unix=? WHERE auth_session_id=?",
                    (now, row["auth_session_id"]),
                )
            return {
                "auth_session_id": row["auth_session_id"],
                "admin_id": row["admin_id"],
                "username": row["username"],
                "expires_unix": row["expires_unix"],
            }

    def require_csrf(self, auth_session_id: str, csrf_token: str) -> None:
        _require_id(auth_session_id, "auth_session_id")
        if not isinstance(csrf_token, str) or len(csrf_token) > 512:
            raise AuthenticationError("invalid_csrf_token")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT csrf_hash FROM control_sessions WHERE auth_session_id=? AND revoked_unix IS NULL",
                (auth_session_id,),
            ).fetchone()
        if not row or not hmac.compare_digest(_digest(csrf_token), bytes(row["csrf_hash"])):
            raise AuthenticationError("invalid_csrf_token")

    def logout(self, auth_session_id: str) -> bool:
        _require_id(auth_session_id, "auth_session_id")
        with self.store.transaction() as connection:
            changed = connection.execute(
                "UPDATE control_sessions SET revoked_unix=? WHERE auth_session_id=? AND revoked_unix IS NULL",
                (time.time(), auth_session_id),
            ).rowcount
        return bool(changed)

    def _append_audit(self, connection: Any, admin_id: str | None, action: str,
                      object_kind: str, object_id: str | None, outcome: str,
                      details: Mapping[str, Any], remote_address: str | None) -> str:
        audit_id = _new_id("audit")
        connection.execute(
            "INSERT INTO control_audit(audit_id, admin_id, action, object_kind, object_id, "
            "remote_address, outcome, details_json, created_unix) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (audit_id, admin_id, action, object_kind, object_id, remote_address,
             outcome, _json(details), time.time()),
        )
        return audit_id

    def audit(self, admin_id: str | None, action: str, object_kind: str,
              object_id: str | None, outcome: str, details: Mapping[str, Any],
              remote_address: str | None) -> str:
        action = _require_key(action, "audit_action")
        object_kind = _require_key(object_kind, "audit_object_kind")
        outcome = _require_key(outcome, "audit_outcome")
        with self.store.transaction() as connection:
            return self._append_audit(
                connection, admin_id, action, object_kind, object_id, outcome, details, remote_address,
            )

    def configure_provider(self, display_name: str, base_url: str, *,
                           api_key: str | None = None, provider_id: str | None = None,
                           default_model_id: str | None = None,
                           context_length_override: int | None = None,
                           metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        display_name = _bounded(display_name, "provider_name", 160)
        base_url = canonical_provider_url(base_url)
        provider_id = provider_id or _new_id("provider")
        _require_id(provider_id, "provider_id")
        if default_model_id is not None:
            default_model_id = _bounded(default_model_id, "default_model_id", MODEL_ID_LIMIT)
        if context_length_override is not None and not 1024 <= int(context_length_override) <= 16_777_216:
            raise InvalidRecord("invalid_context_length_override")
        with self.store.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM model_providers WHERE provider_id=?", (provider_id,),
            ).fetchone()
            name_owner = connection.execute(
                "SELECT provider_id FROM model_providers WHERE display_name=?", (display_name,),
            ).fetchone()
        if name_owner and str(name_owner["provider_id"]) != provider_id:
            raise InvalidRecord("provider_display_name_already_exists")
        secret_id = existing["api_key_secret_id"] if existing else None
        if api_key is not None:
            if not isinstance(api_key, str) or len(api_key) > 65_536:
                raise InvalidRecord("invalid_provider_api_key")
            if api_key:
                secret = self.vault.put(
                    f"provider.{provider_id}.api_key", api_key,
                    secret_id=str(secret_id) if secret_id else None,
                )
                secret_id = secret["secret_id"]
            elif secret_id:
                self.vault.revoke(str(secret_id))
                secret_id = None
        now = time.time()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO model_providers(provider_id, display_name, provider_kind, base_url, "
                "api_key_secret_id, default_model_id, context_length_override, status, metadata_json, "
                "created_unix, updated_unix) VALUES (?, ?, 'openai-compatible', ?, ?, ?, ?, "
                "'configured', ?, ?, ?) ON CONFLICT(provider_id) DO UPDATE SET "
                "display_name=excluded.display_name, base_url=excluded.base_url, "
                "api_key_secret_id=excluded.api_key_secret_id, default_model_id=excluded.default_model_id, "
                "context_length_override=excluded.context_length_override, status='configured', "
                "last_error=NULL, metadata_json=excluded.metadata_json, updated_unix=excluded.updated_unix",
                (provider_id, display_name, base_url, secret_id, default_model_id,
                 context_length_override, _json(metadata), now, now),
            )
        return self.get_provider(provider_id)

    def get_provider(self, provider_id: str) -> dict[str, Any]:
        _require_id(provider_id, "provider_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM model_providers WHERE provider_id=?", (provider_id,),
            ).fetchone()
            if not row:
                raise ScopeViolation("unknown_provider_id")
            models = connection.execute(
                "SELECT model_id, display_name, context_length, capabilities_json, discovered_unix "
                "FROM provider_models WHERE provider_id=? ORDER BY model_id", (provider_id,),
            ).fetchall()
        result = dict(row)
        result["has_api_key"] = bool(result.pop("api_key_secret_id"))
        result["metadata"] = json.loads(result.pop("metadata_json"))
        result["models"] = [
            {**dict(item), "capabilities": json.loads(item["capabilities_json"])}
            for item in models
        ]
        for model in result["models"]:
            model.pop("capabilities_json", None)
        return result

    def list_providers(self) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            identifiers = [row["provider_id"] for row in connection.execute(
                "SELECT provider_id FROM model_providers ORDER BY display_name"
            )]
        return [self.get_provider(str(identifier)) for identifier in identifiers]

    def delete_provider(self, provider_id: str) -> dict[str, Any]:
        """Remove an unused provider and revoke its stored API key, if any.

        Harness profiles are immutable historical configuration.  Refusing to
        remove a provider they reference keeps old match and telemetry records
        intelligible instead of leaving a dangling provider identity.
        """
        _require_id(provider_id, "provider_id")
        with self.store.transaction() as connection:
            provider = connection.execute(
                "SELECT api_key_secret_id FROM model_providers WHERE provider_id=?",
                (provider_id,),
            ).fetchone()
            if not provider:
                raise ScopeViolation("unknown_provider_id")
            if connection.execute(
                "SELECT 1 FROM harness_profiles WHERE provider_id=? LIMIT 1",
                (provider_id,),
            ).fetchone():
                raise StoreError("provider_in_use_by_harness_profile")
            secret_id = provider["api_key_secret_id"]
            connection.execute("DELETE FROM provider_models WHERE provider_id=?", (provider_id,))
            connection.execute("DELETE FROM model_providers WHERE provider_id=?", (provider_id,))
        if secret_id:
            self.vault.revoke(str(secret_id))
        return {"ok": True, "provider_id": provider_id, "deleted": True}

    def discover_provider(self, provider_id: str, *, timeout: float = 10.0,
                          ssl_context: ssl.SSLContext | None = None) -> dict[str, Any]:
        _require_id(provider_id, "provider_id")
        with self.store.transaction() as connection:
            provider = connection.execute(
                "SELECT * FROM model_providers WHERE provider_id=?", (provider_id,),
            ).fetchone()
        if not provider:
            raise ScopeViolation("unknown_provider_id")
        headers = {"Accept": "application/json", "User-Agent": "SMACX-Agent-Control/1"}
        if provider["api_key_secret_id"]:
            headers["Authorization"] = "Bearer " + self.vault.read(
                str(provider["api_key_secret_id"]), purpose=f"provider.{provider_id}.api_key",
            )
        request = Request(str(provider["base_url"]).rstrip("/") + "/models", headers=headers)
        try:
            with urlopen(request, timeout=min(max(float(timeout), 1.0), 30.0), context=ssl_context) as response:
                body = response.read(PROVIDER_RESPONSE_LIMIT + 1)
                if len(body) > PROVIDER_RESPONSE_LIMIT:
                    raise ProviderError("provider_response_too_large")
                payload = json.loads(body)
        except HTTPError as exc:
            self._provider_failure(provider_id, f"http_status_{exc.code}")
            raise ProviderError(f"provider_http_status_{exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            self._provider_failure(provider_id, "provider_unreachable")
            raise ProviderError("provider_unreachable") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._provider_failure(provider_id, "invalid_provider_response")
            raise ProviderError("invalid_provider_response") from exc
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, list):
            self._provider_failure(provider_id, "invalid_provider_models_shape")
            raise ProviderError("invalid_provider_models_shape")
        discovered: dict[str, dict[str, Any]] = {}
        for item in data:
            if not isinstance(item, Mapping):
                continue
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > MODEL_ID_LIMIT:
                continue
            model_id = model_id.strip()
            discovered[model_id] = {
                "model_id": model_id,
                "display_name": str(item.get("name") or model_id)[:MODEL_ID_LIMIT],
                "context_length": _context_length(item),
                "capabilities": item.get("capabilities") if isinstance(item.get("capabilities"), Mapping) else {},
                "raw": dict(item),
            }
        if not discovered:
            self._provider_failure(provider_id, "provider_returned_no_models")
            raise ProviderError("provider_returned_no_models")
        now = time.time()
        current_default = provider["default_model_id"]
        if current_default not in discovered:
            current_default = next(iter(discovered)) if len(discovered) == 1 else None
        with self.store.transaction() as connection:
            connection.execute("DELETE FROM provider_models WHERE provider_id=?", (provider_id,))
            for model in discovered.values():
                connection.execute(
                    "INSERT INTO provider_models(provider_id, model_id, display_name, context_length, "
                    "capabilities_json, raw_metadata_json, discovered_unix) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (provider_id, model["model_id"], model["display_name"], model["context_length"],
                     _json(model["capabilities"]), json.dumps(model["raw"], ensure_ascii=False,
                     sort_keys=True, separators=(",", ":")), now),
                )
            connection.execute(
                "UPDATE model_providers SET status='healthy', last_error=NULL, default_model_id=?, "
                "discovered_unix=?, updated_unix=? WHERE provider_id=?",
                (current_default, now, now, provider_id),
            )
        return self.get_provider(provider_id)

    def probe_provider_generation(self, provider_id: str, model_id: str,
                                  reasoning_effort: str,
                                  generation_settings: Mapping[str, Any] | None,
                                  *, timeout: float = 60.0,
                                  ssl_context: ssl.SSLContext | None = None) -> dict[str, Any]:
        """Send one bounded request and report transport acceptance honestly.

        HTTP success proves only that the endpoint accepted the serialized
        request. It cannot prove that a server applied every extension field.
        """
        _require_id(provider_id, "provider_id")
        model_id = _bounded(model_id, "model_id", MODEL_ID_LIMIT)
        if reasoning_effort not in {
            "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
        }:
            raise InvalidRecord("invalid_reasoning_effort")
        normalized = normalize_generation_settings(generation_settings)
        with self.store.transaction() as connection:
            provider = connection.execute(
                "SELECT * FROM model_providers WHERE provider_id=?", (provider_id,),
            ).fetchone()
            model_exists = connection.execute(
                "SELECT 1 FROM provider_models WHERE provider_id=? AND model_id=?",
                (provider_id, model_id),
            ).fetchone()
        if not provider:
            raise ScopeViolation("unknown_provider_id")
        if not model_exists:
            raise InvalidRecord("unknown_provider_model")
        profile_body = openai_extra_body(normalized)
        request_body: dict[str, Any] = {
            "model": model_id,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "stream": False,
            **profile_body,
        }
        if reasoning_effort != "none":
            request_body["reasoning_effort"] = reasoning_effort
        probe_only_fields: list[str] = []
        if "max_tokens" not in request_body:
            request_body["max_tokens"] = 8
            probe_only_fields.append("max_tokens")
        headers = {
            "Accept": "application/json", "Content-Type": "application/json",
            "User-Agent": "SMACX-Agent-Control/1",
        }
        if provider["api_key_secret_id"]:
            headers["Authorization"] = "Bearer " + self.vault.read(
                str(provider["api_key_secret_id"]), purpose=f"provider.{provider_id}.api_key",
            )
        request = Request(
            str(provider["base_url"]).rstrip("/") + "/chat/completions",
            data=json.dumps(request_body, ensure_ascii=False, separators=(",", ":")).encode(),
            headers=headers, method="POST",
        )
        accepted = False
        status_code: int | None = None
        message = "The endpoint accepted the request. Individual parameter behavior remains unverified."
        try:
            with urlopen(request, timeout=min(max(float(timeout), 1.0), 90.0),
                         context=ssl_context) as response:
                status_code = int(response.status)
                response.read(PROVIDER_RESPONSE_LIMIT + 1)
                accepted = 200 <= status_code < 300
        except HTTPError as exc:
            status_code = int(exc.code)
            message = f"The endpoint rejected the request with HTTP {status_code}."
        except (URLError, TimeoutError, OSError) as exc:
            message = f"The endpoint could not complete the acceptance probe ({type(exc).__name__})."
        sent_fields = sorted(key for key in request_body if key not in {"model", "messages", "stream"})
        return {
            "ok": True,
            "accepted": accepted,
            "state": "accepted" if accepted else "rejected",
            "http_status": status_code,
            "sent_fields": sent_fields,
            "profile_fields": sorted(set(profile_body) | ({"reasoning_effort"} if reasoning_effort != "none" else set())),
            "probe_only_fields": probe_only_fields,
            "semantic_effect_verified": False,
            "message": message,
        }

    def _provider_failure(self, provider_id: str, error: str) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE model_providers SET status='unreachable', last_error=?, updated_unix=? "
                "WHERE provider_id=?", (error[:512], time.time(), provider_id),
            )

    def select_provider_model(self, provider_id: str, model_id: str,
                              *, context_length_override: int | None = None) -> dict[str, Any]:
        _require_id(provider_id, "provider_id")
        model_id = _bounded(model_id, "model_id", MODEL_ID_LIMIT)
        if context_length_override is not None and not 1024 <= int(context_length_override) <= 16_777_216:
            raise InvalidRecord("invalid_context_length_override")
        with self.store.transaction() as connection:
            if not connection.execute(
                "SELECT 1 FROM provider_models WHERE provider_id=? AND model_id=?", (provider_id, model_id),
            ).fetchone():
                raise ScopeViolation("unknown_provider_model")
            connection.execute(
                "UPDATE model_providers SET default_model_id=?, context_length_override=?, "
                "updated_unix=? WHERE provider_id=?",
                (model_id, context_length_override, time.time(), provider_id),
            )
        return self.get_provider(provider_id)

    def register_game_source(self, display_name: str, host_path: str, executable_sha256: str,
                             *, game_source_id: str | None = None,
                             metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        display_name = _bounded(display_name, "game_source_name", 160)
        if not isinstance(host_path, str) or not Path(host_path).is_absolute() or len(host_path) > 4096:
            raise InvalidRecord("invalid_game_source_host_path")
        if not SHA256.fullmatch(executable_sha256):
            raise InvalidRecord("invalid_game_source_fingerprint")
        game_source_id = game_source_id or _new_id("game-source")
        _require_id(game_source_id, "game_source_id")
        now = time.time()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO game_sources(game_source_id, display_name, host_path, executable_sha256, "
                "status, metadata_json, validated_unix, created_unix, updated_unix) "
                "VALUES (?, ?, ?, ?, 'validated', ?, ?, ?, ?) ON CONFLICT(game_source_id) DO UPDATE SET "
                "display_name=excluded.display_name, host_path=excluded.host_path, "
                "executable_sha256=excluded.executable_sha256, status='validated', "
                "metadata_json=excluded.metadata_json, validated_unix=excluded.validated_unix, "
                "updated_unix=excluded.updated_unix",
                (game_source_id, display_name, host_path, executable_sha256, _json(metadata), now, now, now),
            )
            row = connection.execute(
                "SELECT * FROM game_sources WHERE game_source_id=?", (game_source_id,),
            ).fetchone()
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def register_runtime(self, display_name: str, storage_kind: str, storage_ref: str, *,
                         runtime_id: str | None = None, runtime_kind: str = "proton",
                         source_path: str | None = None, content_fingerprint: str | None = None,
                         status: str = "ready", metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        display_name = _bounded(display_name, "runtime_name", 160)
        storage_kind = _require_key(storage_kind, "runtime_storage_kind")
        runtime_kind = _require_key(runtime_kind, "runtime_kind")
        storage_ref = _bounded(storage_ref, "runtime_storage_ref", 4096)
        if status not in ("importing", "ready", "invalid", "disabled"):
            raise InvalidRecord("invalid_runtime_status")
        if content_fingerprint is not None and not SHA256.fullmatch(content_fingerprint):
            raise InvalidRecord("invalid_runtime_fingerprint")
        runtime_id = runtime_id or _new_id("runtime")
        _require_id(runtime_id, "runtime_id")
        now = time.time()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO runtime_assets(runtime_id, runtime_kind, display_name, source_path, "
                "storage_kind, storage_ref, content_fingerprint, status, metadata_json, created_unix, "
                "updated_unix) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(runtime_id) DO UPDATE SET display_name=excluded.display_name, "
                "source_path=excluded.source_path, storage_kind=excluded.storage_kind, "
                "storage_ref=excluded.storage_ref, content_fingerprint=excluded.content_fingerprint, "
                "status=excluded.status, metadata_json=excluded.metadata_json, updated_unix=excluded.updated_unix",
                (runtime_id, runtime_kind, display_name, source_path, storage_kind, storage_ref,
                 content_fingerprint, status, _json(metadata), now, now),
            )
            row = connection.execute(
                "SELECT * FROM runtime_assets WHERE runtime_id=?", (runtime_id,),
            ).fetchone()
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def list_game_sources(self) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM game_sources ORDER BY display_name, game_source_id"
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            results.append(item)
        return results

    def get_game_source(self, game_source_id: str) -> dict[str, Any]:
        _require_id(game_source_id, "game_source_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM game_sources WHERE game_source_id=?", (game_source_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_game_source_id")
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def list_runtimes(self) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM runtime_assets ORDER BY display_name, runtime_id"
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            results.append(item)
        return results

    def get_runtime(self, runtime_id: str) -> dict[str, Any]:
        _require_id(runtime_id, "runtime_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_assets WHERE runtime_id=?", (runtime_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_runtime_id")
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def put_worker_spec(self, instance_id: str, game_source_id: str, runtime_id: str,
                        image_ref: str, container_name: str, data_volume: str,
                        bridge_secret_id: str, *, autostart: Mapping[str, Any] | None = None,
                        network: Mapping[str, Any] | None = None,
                        view_secret_id: str | None = None) -> dict[str, Any]:
        for value, field in (
            (instance_id, "instance_id"), (game_source_id, "game_source_id"),
            (runtime_id, "runtime_id"), (bridge_secret_id, "bridge_secret_id"),
        ):
            _require_id(value, field)
        image_ref = _bounded(image_ref, "worker_image_ref", 512)
        container_name = _bounded(container_name, "worker_container_name", 255)
        data_volume = _bounded(data_volume, "worker_data_volume", 255)
        now = time.time()
        with self.store.transaction() as connection:
            if not connection.execute("SELECT 1 FROM instances WHERE instance_id=?", (instance_id,)).fetchone():
                raise ScopeViolation("unknown_instance_id")
            if not connection.execute(
                "SELECT 1 FROM game_sources WHERE game_source_id=? AND status='validated'", (game_source_id,),
            ).fetchone():
                raise ScopeViolation("game_source_not_validated")
            if not connection.execute(
                "SELECT 1 FROM runtime_assets WHERE runtime_id=? AND status='ready'", (runtime_id,),
            ).fetchone():
                raise ScopeViolation("runtime_not_ready")
            if not connection.execute(
                "SELECT 1 FROM secret_refs WHERE secret_id=? AND status='active'", (bridge_secret_id,),
            ).fetchone():
                raise ScopeViolation("bridge_secret_not_active")
            if view_secret_id is not None and not connection.execute(
                "SELECT 1 FROM secret_refs WHERE secret_id=? AND status='active'", (view_secret_id,),
            ).fetchone():
                raise ScopeViolation("view_secret_not_active")
            connection.execute(
                "INSERT INTO worker_specs(instance_id, game_source_id, runtime_id, image_ref, "
                "container_name, data_volume, bridge_secret_id, view_secret_id, desired_status, "
                "observed_status, autostart_json, network_json, created_unix, updated_unix) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'stopped', 'provisioned', ?, ?, ?, ?)",
                (instance_id, game_source_id, runtime_id, image_ref, container_name, data_volume,
                 bridge_secret_id, view_secret_id, _json(autostart), _json(network), now, now),
            )
        return self.get_worker_spec(instance_id)

    def get_worker_spec(self, instance_id: str) -> dict[str, Any]:
        _require_id(instance_id, "instance_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT w.*, i.match_id, i.agent_id, i.perspective_id, i.status AS instance_status, "
                "i.bridge_host, i.bridge_port FROM worker_specs w JOIN instances i "
                "ON i.instance_id=w.instance_id WHERE w.instance_id=?", (instance_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_worker_instance")
        result = dict(row)
        result["autostart"] = json.loads(result.pop("autostart_json"))
        result["network"] = json.loads(result.pop("network_json"))
        return result

    def list_worker_specs(self) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            identifiers = [row["instance_id"] for row in connection.execute(
                "SELECT instance_id FROM worker_specs ORDER BY created_unix"
            )]
        return [self.get_worker_spec(str(identifier)) for identifier in identifiers]

    def worker_for_match(self, match_id: str, agent_id: str | None = None) -> dict[str, Any]:
        _require_id(match_id, "match_id")
        if agent_id is not None:
            _require_id(agent_id, "agent_id")
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT instance_id FROM worker_specs WHERE instance_id IN "
                "(SELECT instance_id FROM instances WHERE match_id=? "
                "AND (? IS NULL OR agent_id=?)) ORDER BY created_unix",
                (match_id, agent_id, agent_id),
            ).fetchall()
        if not rows:
            raise ScopeViolation("match_has_no_worker")
        if len(rows) != 1:
            raise ScopeViolation("match_worker_must_be_selected")
        return self.get_worker_spec(str(rows[0]["instance_id"]))

    def update_worker_observation(self, instance_id: str, *, desired_status: str | None = None,
                                  observed_status: str | None = None,
                                  last_error: str | None = None,
                                  bridge_host: str | None = None,
                                  bridge_port: int | None = None,
                                  instance_status: str | None = None) -> dict[str, Any]:
        _require_id(instance_id, "instance_id")
        if desired_status is not None and desired_status not in ("stopped", "running", "parked", "retired"):
            raise InvalidRecord("invalid_worker_desired_status")
        if observed_status is not None:
            observed_status = _bounded(observed_status, "worker_observed_status", 64)
        if last_error is not None:
            last_error = last_error[:2000]
        if bridge_port is not None and not 1 <= int(bridge_port) <= 65535:
            raise InvalidRecord("invalid_bridge_port")
        now = time.time()
        with self.store.transaction() as connection:
            current = connection.execute(
                "SELECT 1 FROM worker_specs WHERE instance_id=?", (instance_id,),
            ).fetchone()
            if not current:
                raise ScopeViolation("unknown_worker_instance")
            fields = ["updated_unix=?"]
            values: list[Any] = [now]
            for name, value in (
                ("desired_status", desired_status), ("observed_status", observed_status),
                ("last_error", last_error),
            ):
                if value is not None:
                    fields.append(f"{name}=?")
                    values.append(value)
            values.append(instance_id)
            connection.execute(
                f"UPDATE worker_specs SET {', '.join(fields)} WHERE instance_id=?", values,
            )
            instance_fields = ["updated_unix=?", "bridge_host=?", "bridge_port=?"]
            instance_values: list[Any] = [now, bridge_host, bridge_port]
            if instance_status is not None:
                instance_fields.append("status=?")
                instance_values.append(instance_status)
            instance_values.append(instance_id)
            connection.execute(
                f"UPDATE instances SET {', '.join(instance_fields)} WHERE instance_id=?", instance_values,
            )
        return self.get_worker_spec(instance_id)

    def update_worker_image_ref(self, instance_id: str, image_ref: str) -> dict[str, Any]:
        """Move a stopped worker specification onto a newly prepared runtime image.

        Campaign state remains in the worker's durable data volume.  Only the
        immutable runtime/game layer changes, which is required when an
        operator retries a preserved match after updating the semantic bridge.
        """
        _require_id(instance_id, "instance_id")
        image_ref = _bounded(image_ref, "worker_image_ref", 512)
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT desired_status, observed_status FROM worker_specs WHERE instance_id=?",
                (instance_id,),
            ).fetchone()
            if not row:
                raise ScopeViolation("unknown_worker_instance")
            if row["observed_status"] == "running" or row["desired_status"] == "running":
                raise InvalidRecord("worker_image_refresh_requires_stopped_worker")
            connection.execute(
                "UPDATE worker_specs SET image_ref=?, updated_unix=? WHERE instance_id=?",
                (image_ref, time.time(), instance_id),
            )
        return self.get_worker_spec(instance_id)

    def update_worker_network(self, instance_id: str, network: Mapping[str, Any]) -> dict[str, Any]:
        _require_id(instance_id, "instance_id")
        encoded = _json(network)
        with self.store.transaction() as connection:
            if not connection.execute(
                "SELECT 1 FROM worker_specs WHERE instance_id=?", (instance_id,),
            ).fetchone():
                raise ScopeViolation("unknown_worker_instance")
            connection.execute(
                "UPDATE worker_specs SET network_json=?, updated_unix=? WHERE instance_id=?",
                (encoded, time.time(), instance_id),
            )
        return self.get_worker_spec(instance_id)

    def update_worker_autostart(self, instance_id: str,
                                autostart: Mapping[str, Any]) -> dict[str, Any]:
        """Replace the validated worker launch policy before its next start."""
        _require_id(instance_id, "instance_id")
        encoded = _json(autostart)
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE worker_specs SET autostart_json=?, updated_unix=? WHERE instance_id=?",
                (encoded, time.time(), instance_id),
            )
            if cursor.rowcount != 1:
                raise ScopeViolation("unknown_worker_instance")
        return self.get_worker_spec(instance_id)

    def status(self) -> dict[str, Any]:
        with self.store.transaction() as connection:
            counts = {
                table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in (
                    "agents", "matches", "instances", "model_providers", "game_sources",
                    "runtime_assets", "harness_profiles", "harness_runs",
                    "operation_schedules", "backup_sets", "supervision_incidents",
                )
            }
        return {
            "ok": True,
            "schema_version": self.store.schema_version(),
            "installation_id": self.store.installation_id(),
            "setup_required": not self.admin_exists(),
            "counts": counts,
        }

    def create_agent(self, display_name: str, *, agent_id: str | None = None,
                     personality_ref: str | None = None,
                     metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        agent_id = agent_id or _new_id("agent")
        return self.store.ensure_agent(
            agent_id, display_name, personality_ref=personality_ref, metadata=metadata,
        )

    def create_solo_match(self, display_name: str, agent_id: str | None = None, *,
                          match_id: str | None = None,
                          faction_id: int = 1, faction_name: str | None = None,
                          controller_kind: str = "agent",
                          human_player_name: str | None = None,
                          ruleset_id: str = "smacx-default",
                          metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if controller_kind not in {"agent", "human"}:
            raise InvalidRecord("invalid_solo_controller_kind")
        if controller_kind == "human":
            if not isinstance(human_player_name, str) or not 1 <= len(human_player_name) <= 31 \
                    or not all(32 <= ord(character) <= 126 for character in human_player_name):
                raise InvalidRecord("invalid_solo_human_player_name")
            agent_id = _new_id("human")
            self.store.ensure_agent(agent_id, human_player_name, metadata={
                "managed_human": True, "match_id": match_id, "solo": True,
            })
        if agent_id is None:
            raise InvalidRecord("solo_agent_required")
        _require_id(agent_id, "agent_id")
        if not 1 <= int(faction_id) <= 7:
            raise InvalidRecord("invalid_faction_id")
        with self.store.transaction() as connection:
            if not connection.execute(
                "SELECT 1 FROM agents WHERE agent_id=? AND status='active'", (agent_id,),
            ).fetchone():
                raise ScopeViolation("unknown_active_agent")
        match = self.store.create_match(
            match_id=match_id, display_name=display_name, mode="singleplayer",
            ruleset_id=ruleset_id, metadata=metadata,
        )
        perspective = self.store.create_perspective(
            match["match_id"], agent_id, faction_id=int(faction_id),
            faction_name=faction_name, controller_kind=controller_kind,
        )
        now = time.time()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO seat_assignments(seat_id, match_id, seat_index, controller_kind, "
                "agent_id, perspective_id, faction_id, faction_name, status, metadata_json, "
                "created_unix, updated_unix) VALUES (?, ?, 0, ?, ?, ?, ?, ?, 'assigned', ?, ?, ?)",
                (_new_id("seat"), match["match_id"], controller_kind, agent_id,
                 perspective["perspective_id"], int(faction_id), faction_name, _json({
                     "managed": True,
                     "external_player_name": human_player_name if controller_kind == "human" else None,
                 }), now, now),
            )
        return {"match": match, "perspective": perspective}

    def create_lan_match(self, display_name: str, agent_ids: list[str], *,
                         agent_seats: list[Mapping[str, Any]] | None = None,
                         human_player_names: list[str] | None = None,
                         managed_human_player_names: list[str] | None = None,
                         human_seat_preferences: list[Mapping[str, Any]] | None = None,
                         faction_roster_choice_ids: list[int] | None = None,
                         host_controller_kind: str = "agent",
                         human_host_name: str | None = None,
                         human_host_managed: bool = False,
                         match_id: str | None = None,
                         ruleset_id: str = "smacx-small-easy",
                         metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        display_name = _bounded(display_name, "match_name", 160)
        human_player_names = list(human_player_names or [])
        managed_human_player_names = list(managed_human_player_names or [])
        human_seat_preferences = list(human_seat_preferences or [])
        faction_roster_choice_ids = list(faction_roster_choice_ids or [])
        agent_seats = list(agent_seats or [])
        if agent_seats:
            parsed_agent_ids = [str(item.get("agent_id") or "") for item in agent_seats]
            if parsed_agent_ids != agent_ids:
                raise InvalidRecord("lan_agent_seat_order_mismatch")
            for item in agent_seats:
                player_name = item.get("player_name")
                choice = item.get("faction_choice_id")
                personality_id = item.get("personality_id", PERSONALITY_NONE)
                personality_prompt = item.get("personality_prompt")
                personality_hash = item.get("personality_prompt_sha256")
                if not isinstance(player_name, str) or not 1 <= len(player_name) <= 31 \
                        or any(ord(character) < 32 or ord(character) > 126 for character in player_name):
                    raise InvalidRecord("invalid_lan_agent_player_name")
                if not isinstance(choice, int) or not 0 <= choice <= 13:
                    raise InvalidRecord("invalid_lan_agent_faction_choice")
                faction_name = item.get("faction_name")
                if not isinstance(faction_name, str) or not 1 <= len(faction_name) <= 63:
                    raise InvalidRecord("invalid_lan_agent_faction_name")
                if not isinstance(personality_id, str) or not 1 <= len(personality_id) <= 96:
                    raise InvalidRecord("invalid_lan_personality_id")
                if personality_prompt is not None and (
                        not isinstance(personality_prompt, str)
                        or len(personality_prompt) > 32768):
                    raise InvalidRecord("invalid_lan_personality_prompt")
                if personality_id != PERSONALITY_NONE and not isinstance(personality_prompt, str):
                    raise InvalidRecord("lan_personality_prompt_required")
                if personality_prompt is not None and (
                        not isinstance(personality_hash, str)
                        or not SHA256.fullmatch(personality_hash)
                        or prompt_sha256(personality_prompt) != personality_hash):
                    raise InvalidRecord("invalid_lan_personality_prompt_hash")
        if host_controller_kind not in {"agent", "human"}:
            raise InvalidRecord("invalid_lan_host_controller_kind")
        if not isinstance(agent_ids, list) or len(agent_ids) > 7:
            raise InvalidRecord("lan_allows_up_to_seven_agents")
        if host_controller_kind == "agent" and not agent_ids:
            raise InvalidRecord("agent_host_requires_an_agent")
        all_human_names = list(human_player_names)
        all_human_names.extend(managed_human_player_names)
        if host_controller_kind == "human":
            if not isinstance(human_host_name, str):
                raise InvalidRecord("human_lan_host_name_required")
            all_human_names.insert(0, human_host_name)
            if not isinstance(human_host_managed, bool):
                raise InvalidRecord("invalid_human_host_managed")
        elif human_host_name is not None:
            raise InvalidRecord("agent_lan_host_cannot_have_human_host_name")
        elif human_host_managed:
            raise InvalidRecord("agent_lan_host_cannot_be_managed_human")
        if not all(isinstance(name, str) and 1 <= len(name) <= 31
                   and all(32 <= ord(character) <= 126 for character in name)
                   for name in all_human_names):
            raise InvalidRecord("invalid_lan_human_player_name")
        preference_by_name: dict[str, dict[str, Any]] = {}
        for item in human_seat_preferences:
            player_name = item.get("player_name")
            choice = item.get("faction_choice_id")
            if not isinstance(player_name, str) or not isinstance(choice, int) \
                    or not 0 <= choice <= 13:
                raise InvalidRecord("invalid_lan_human_faction_preference")
            key = player_name.casefold()
            if key in preference_by_name:
                raise InvalidRecord("duplicate_lan_human_faction_preference")
            preference_by_name[key] = {
                "requested_faction_key": item.get("faction_key"),
                "requested_faction_name": item.get("faction_name"),
                "requested_faction_choice_id": choice,
            }
        if preference_by_name and set(preference_by_name) != {
                name.casefold() for name in all_human_names}:
            raise InvalidRecord("lan_human_faction_preferences_do_not_match_seats")
        if faction_roster_choice_ids and (
                len(faction_roster_choice_ids) != 7
                or len(set(faction_roster_choice_ids)) != 7
                or any(not isinstance(choice, int) or not 0 <= choice <= 13
                       for choice in faction_roster_choice_ids)):
            raise InvalidRecord("invalid_lan_faction_roster")
        requested_choices = [
            int(item["faction_choice_id"]) for item in agent_seats
            if isinstance(item.get("faction_choice_id"), int)
        ] + [
            int(item["requested_faction_choice_id"])
            for item in preference_by_name.values()
        ]
        if len(requested_choices) != len(set(requested_choices)):
            raise InvalidRecord("duplicate_lan_faction_reservation")
        if faction_roster_choice_ids and not set(requested_choices).issubset(
                set(faction_roster_choice_ids)):
            raise InvalidRecord("lan_faction_reservation_outside_roster")
        if not 2 <= len(agent_ids) + len(all_human_names) <= 7:
            raise InvalidRecord("lan_requires_two_to_seven_total_seats")
        normalized_human_names = {name.casefold() for name in all_human_names}
        if len(normalized_human_names) != len(all_human_names):
            raise InvalidRecord("duplicate_lan_human_player_name")
        first_agent_seat = 0 if host_controller_kind == "agent" else 1
        reserved_names = {
            str(agent_seats[offset]["player_name"]) if agent_seats else
            ("Semantic Host" if seat_index == 0 else f"Semantic Agent {seat_index + 1}")
            for offset, seat_index in enumerate(
                range(first_agent_seat, first_agent_seat + len(agent_ids)))
        }
        if {name.casefold() for name in reserved_names}.intersection(normalized_human_names):
            raise InvalidRecord("reserved_lan_human_player_name")
        for agent_id in agent_ids:
            _require_id(agent_id, "agent_id")
        active: set[str] = set()
        if agent_ids:
            with self.store.transaction() as connection:
                active = {
                    str(row["agent_id"]) for row in connection.execute(
                        "SELECT agent_id FROM agents WHERE status='active' AND agent_id IN (%s)"
                        % ",".join("?" for _ in agent_ids),
                        agent_ids,
                    )
                }
        if active != set(agent_ids):
            raise ScopeViolation("unknown_active_lan_agent")
        match_metadata = dict(metadata or {})
        match_metadata["host_controller_kind"] = host_controller_kind
        if faction_roster_choice_ids:
            match_metadata["faction_roster_choice_ids"] = faction_roster_choice_ids
        match = self.store.create_match(
            match_id=match_id, display_name=display_name, mode="lan",
            ruleset_id=ruleset_id, metadata=match_metadata,
        )
        seats: list[dict[str, Any]] = []
        try:
            if host_controller_kind == "human":
                if human_host_managed:
                    self._create_managed_human_lan_seat(
                        match["match_id"], 0, str(human_host_name), role="host",
                        faction_preference=preference_by_name.get(str(human_host_name).casefold()),
                    )
                else:
                    now = time.time()
                    with self.store.transaction() as connection:
                        connection.execute(
                            "INSERT INTO seat_assignments(seat_id, match_id, seat_index, controller_kind, "
                            "status, metadata_json, created_unix, updated_unix) "
                            "VALUES (?, ?, 0, 'human', 'assigned', ?, ?, ?)",
                            (_new_id("seat"), match["match_id"], _json({
                                "role": "host",
                                "external_player_name": human_host_name,
                                "managed": False,
                                "network_join_pending": False,
                                **preference_by_name.get(str(human_host_name).casefold(), {}),
                            }), now, now),
                        )
                seats.append(self.get_seat(match["match_id"], 0))
            for offset, agent_id in enumerate(agent_ids):
                seat_index = first_agent_seat + offset
                requested = dict(agent_seats[offset]) if agent_seats else {}
                perspective = self.store.create_perspective(
                    match["match_id"], agent_id, controller_kind="agent",
                    metadata={"seat_index": seat_index, "native_faction_pending": True,
                              "requested_faction_key": requested.get("faction_key"),
                              "requested_faction_name": requested.get("faction_name"),
                              "requested_faction_choice_id": requested.get("faction_choice_id")},
                )
                now = time.time()
                with self.store.transaction() as connection:
                    connection.execute(
                        "INSERT INTO seat_assignments(seat_id, match_id, seat_index, controller_kind, "
                        "agent_id, perspective_id, status, metadata_json, created_unix, updated_unix) "
                        "VALUES (?, ?, ?, 'agent', ?, ?, 'assigned', ?, ?, ?)",
                        (_new_id("seat"), match["match_id"], seat_index, agent_id,
                         perspective["perspective_id"], _json({
                             "role": "host" if seat_index == 0 else "client",
                             "player_name": requested.get("player_name"),
                             "leader_name": requested.get("leader_name"),
                             "requested_faction_key": requested.get("faction_key"),
                             "requested_faction_name": requested.get("faction_name"),
                             "requested_faction_choice_id": requested.get("faction_choice_id"),
                             "personality_id": requested.get("personality_id", "none"),
                             "personality_name": requested.get("personality_name"),
                             "personality_prompt": requested.get("personality_prompt"),
                             "personality_prompt_sha256": requested.get("personality_prompt_sha256"),
                         }),
                         now, now),
                    )
                seats.append(self.get_seat(match["match_id"], seat_index))
            for offset, player_name in enumerate(managed_human_player_names):
                seat_index = first_agent_seat + len(agent_ids) + offset
                self._create_managed_human_lan_seat(
                    match["match_id"], seat_index, player_name, role="client",
                    faction_preference=preference_by_name.get(player_name.casefold()),
                )
                seats.append(self.get_seat(match["match_id"], seat_index))
            for offset, player_name in enumerate(human_player_names):
                seat_index = (
                    first_agent_seat + len(agent_ids)
                    + len(managed_human_player_names) + offset
                )
                now = time.time()
                with self.store.transaction() as connection:
                    connection.execute(
                        "INSERT INTO seat_assignments(seat_id, match_id, seat_index, controller_kind, "
                        "status, metadata_json, created_unix, updated_unix) "
                        "VALUES (?, ?, ?, 'human', 'assigned', ?, ?, ?)",
                        (_new_id("seat"), match["match_id"], seat_index,
                         _json({
                             "role": "client",
                             "external_player_name": player_name,
                             "managed": False,
                             "network_join_pending": True,
                             **preference_by_name.get(player_name.casefold(), {}),
                         }), now, now),
                    )
                seats.append(self.get_seat(match["match_id"], seat_index))
        except Exception:
            with self.store.transaction() as connection:
                connection.execute("DELETE FROM seat_assignments WHERE match_id=?", (match["match_id"],))
                connection.execute("DELETE FROM perspectives WHERE match_id=?", (match["match_id"],))
                connection.execute("DELETE FROM matches WHERE match_id=? AND status='created'", (match["match_id"],))
            raise
        return {"match": match, "seats": seats}

    def _create_managed_human_lan_seat(
            self, match_id: str, seat_index: int, player_name: str, *, role: str,
            faction_preference: Mapping[str, Any] | None = None) -> None:
        """Create a browser human identity without granting it an agent harness."""
        human_id = _new_id("human")
        self.store.ensure_agent(
            human_id, player_name,
            metadata={"managed_human": True, "match_id": match_id},
        )
        perspective = self.store.create_perspective(
            match_id, human_id, controller_kind="human",
            metadata={"seat_index": seat_index, "browser_managed": True},
        )
        now = time.time()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO seat_assignments(seat_id, match_id, seat_index, controller_kind, "
                "agent_id, perspective_id, status, metadata_json, created_unix, updated_unix) "
                "VALUES (?, ?, ?, 'human', ?, ?, 'assigned', ?, ?, ?)",
                (_new_id("seat"), match_id, seat_index, human_id,
                 perspective["perspective_id"], _json({
                     "role": role,
                     "external_player_name": player_name,
                     "managed": True,
                     "network_join_pending": role != "host",
                     **dict(faction_preference or {}),
                 }), now, now),
            )

    def get_seat(self, match_id: str, seat_index: int) -> dict[str, Any]:
        _require_id(match_id, "match_id")
        if not 0 <= int(seat_index) <= 7:
            raise InvalidRecord("invalid_seat_index")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM seat_assignments WHERE match_id=? AND seat_index=?",
                (match_id, int(seat_index)),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_match_seat")
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def list_seats(self, match_id: str) -> list[dict[str, Any]]:
        _require_id(match_id, "match_id")
        with self.store.transaction() as connection:
            indexes = [int(row["seat_index"]) for row in connection.execute(
                "SELECT seat_index FROM seat_assignments WHERE match_id=? ORDER BY seat_index",
                (match_id,),
            )]
        return [self.get_seat(match_id, index) for index in indexes]

    def update_lan_seat(self, match_id: str, seat_index: int, *,
                        faction_id: int | None = None, faction_name: str | None = None,
                        metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        seat = self.get_seat(match_id, seat_index)
        if faction_id is not None and not 1 <= int(faction_id) <= 7:
            raise InvalidRecord("invalid_faction_id")
        merged = dict(seat["metadata"])
        merged.update(dict(metadata or {}))
        now = time.time()
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE seat_assignments SET faction_id=COALESCE(?, faction_id), "
                "faction_name=COALESCE(?, faction_name), metadata_json=?, updated_unix=? "
                "WHERE match_id=? AND seat_index=?",
                (faction_id, faction_name, _json(merged), now, match_id, int(seat_index)),
            )
            if faction_id is not None:
                connection.execute(
                    "UPDATE perspectives SET faction_id=?, faction_name=COALESCE(?, faction_name), "
                    "metadata_json=json_set(metadata_json, '$.native_faction_pending', json('false')) "
                    "WHERE perspective_id=? AND match_id=?",
                    (int(faction_id), faction_name, seat["perspective_id"], match_id),
                )
        return self.get_seat(match_id, seat_index)

    def update_match_lifecycle(self, match_id: str, status: str, *,
                               host_instance_id: str | None = None,
                               metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        _require_id(match_id, "match_id")
        if status not in ("created", "starting", "lobby", "running", "parked", "error", "completed"):
            raise InvalidRecord("invalid_match_status")
        with self.store.transaction() as connection:
            row = connection.execute("SELECT metadata_json FROM matches WHERE match_id=?", (match_id,)).fetchone()
            if not row:
                raise ScopeViolation("unknown_match")
            merged = json.loads(str(row["metadata_json"]))
            merged.update(dict(metadata or {}))
            connection.execute(
                "UPDATE matches SET status=?, host_instance_id=COALESCE(?, host_instance_id), "
                "metadata_json=?, updated_unix=? WHERE match_id=?",
                (status, host_instance_id, _json(merged), time.time(), match_id),
            )
            updated = connection.execute("SELECT * FROM matches WHERE match_id=?", (match_id,)).fetchone()
        result = dict(updated)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def record_match_progress(self, match_id: str, turn: int, year: int) -> dict[str, Any]:
        """Mirror bridge-observed public progress without changing lifecycle authority."""
        _require_id(match_id, "match_id")
        if not 0 <= int(turn) <= 1_000_000 or not -1_000_000 <= int(year) <= 1_000_000:
            raise InvalidRecord("invalid_match_progress")
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE matches SET last_turn=?, last_year=?, updated_unix=? WHERE match_id=?",
                (int(turn), int(year), time.time(), match_id),
            )
            if cursor.rowcount != 1:
                raise ScopeViolation("unknown_match")
        return self.get_match(match_id)

    def discard_unstarted_match(self, match_id: str, perspective_id: str) -> bool:
        """Remove only a just-created match that never acquired runtime state."""
        _require_id(match_id, "match_id")
        _require_id(perspective_id, "perspective_id")
        with self.store.transaction() as connection:
            match = connection.execute(
                "SELECT status FROM matches WHERE match_id=?", (match_id,),
            ).fetchone()
            perspective = connection.execute(
                "SELECT match_id FROM perspectives WHERE perspective_id=?", (perspective_id,),
            ).fetchone()
            if not match or not perspective:
                return False
            if match["status"] != "created" or perspective["match_id"] != match_id:
                raise StoreError("match_not_discardable")
            if connection.execute(
                "SELECT 1 FROM instances WHERE match_id=? LIMIT 1", (match_id,),
            ).fetchone():
                raise StoreError("match_has_runtime_state")
            connection.execute("DELETE FROM seat_assignments WHERE match_id=?", (match_id,))
            connection.execute(
                "DELETE FROM perspectives WHERE perspective_id=? AND match_id=?",
                (perspective_id, match_id),
            )
            connection.execute(
                "DELETE FROM matches WHERE match_id=? AND status='created'", (match_id,),
            )
        return True

    def assign_instance_to_seat(self, match_id: str, agent_id: str,
                                perspective_id: str, instance_id: str) -> dict[str, Any]:
        for value, field in (
            (match_id, "match_id"), (agent_id, "agent_id"),
            (perspective_id, "perspective_id"), (instance_id, "instance_id"),
        ):
            _require_id(value, field)
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE seat_assignments SET instance_id=?, updated_unix=? "
                "WHERE match_id=? AND agent_id=? AND perspective_id=? AND status='assigned'",
                (instance_id, time.time(), match_id, agent_id, perspective_id),
            )
            if cursor.rowcount != 1:
                raise ScopeViolation("unknown_seat_assignment")
            row = connection.execute(
                "SELECT * FROM seat_assignments WHERE match_id=? AND agent_id=? AND perspective_id=?",
                (match_id, agent_id, perspective_id),
            ).fetchone()
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def configure_harness_profile(self, agent_id: str, provider_id: str, *,
                                  display_name: str, external_profile_id: str,
                                  model_id: str | None = None,
                                  reasoning_effort: str = "low",
                                  context_length: int | None = None,
                                  generation_settings: Mapping[str, Any] | None = None,
                                  workspace_path: str | None = None,
                                  system_prompt: str = "",
                                  harness_profile_id: str | None = None,
                                  metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        _require_id(agent_id, "agent_id")
        _require_id(provider_id, "provider_id")
        display_name = _bounded(display_name, "harness_profile_name", 160)
        external_profile_id = _bounded(external_profile_id, "external_profile_id", 80)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", external_profile_id):
            raise InvalidRecord("invalid_external_profile_id")
        if reasoning_effort not in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            raise InvalidRecord("invalid_reasoning_effort")
        if context_length is not None and not HERMES_MINIMUM_CONTEXT_LENGTH <= int(context_length) <= 16_777_216:
            raise InvalidRecord("invalid_context_length")
        if workspace_path is not None and (not Path(workspace_path).is_absolute() or len(workspace_path) > 4096):
            raise InvalidRecord("invalid_workspace_path")
        if not isinstance(system_prompt, str) or len(system_prompt) > 65_536:
            raise InvalidRecord("invalid_system_prompt")
        generation = normalize_generation_settings(generation_settings)
        provider = self.get_provider(provider_id)
        selected_model = model_id or provider.get("default_model_id")
        selected_metadata = next(
            (item for item in provider["models"] if item["model_id"] == selected_model), None,
        )
        if not selected_model or not selected_metadata:
            raise ScopeViolation("harness_model_not_selected")
        effective_context = context_length or provider.get("context_length_override") \
            or selected_metadata.get("context_length")
        if effective_context is None:
            raise InvalidRecord("provider_context_length_unavailable")
        if int(effective_context) < HERMES_MINIMUM_CONTEXT_LENGTH:
            raise InvalidRecord("model_context_below_hermes_minimum")
        advertised_context = provider.get("context_length_override") \
            or selected_metadata.get("context_length")
        if context_length is not None and advertised_context is not None \
                and int(context_length) > int(advertised_context):
            raise InvalidRecord("context_length_exceeds_model_limit")
        with self.store.transaction() as connection:
            if not connection.execute(
                "SELECT 1 FROM agents WHERE agent_id=? AND status='active'", (agent_id,),
            ).fetchone():
                raise ScopeViolation("unknown_active_agent")
            existing = connection.execute(
                "SELECT harness_profile_id FROM harness_profiles WHERE agent_id=? AND adapter_kind='hermes'",
                (agent_id,),
            ).fetchone()
            identifier = str(existing["harness_profile_id"]) if existing else (
                harness_profile_id or _new_id("harness")
            )
            _require_id(identifier, "harness_profile_id")
            now = time.time()
            connection.execute(
                "INSERT INTO harness_profiles(harness_profile_id, display_name, adapter_kind, provider_id, "
                "model_id, reasoning_effort, context_length, workspace_path, system_prompt, status, "
                "metadata_json, created_unix, updated_unix, agent_id, external_profile_id) "
                "VALUES (?, ?, 'hermes', ?, ?, ?, ?, ?, ?, 'configured', ?, ?, ?, ?, ?) "
                "ON CONFLICT(harness_profile_id) DO UPDATE SET display_name=excluded.display_name, "
                "provider_id=excluded.provider_id, model_id=excluded.model_id, "
                "reasoning_effort=excluded.reasoning_effort, context_length=excluded.context_length, "
                "workspace_path=excluded.workspace_path, system_prompt=excluded.system_prompt, "
                "status='configured', metadata_json=excluded.metadata_json, "
                "external_profile_id=excluded.external_profile_id, updated_unix=excluded.updated_unix",
                (identifier, display_name, provider_id, selected_model, reasoning_effort,
                 effective_context, workspace_path, system_prompt, _json({
                     **dict(metadata or {}), "generation_settings": generation,
                 }), now, now,
                 agent_id, external_profile_id),
            )
        return self.get_harness_profile(identifier)

    def prepare_hermes_profile(self, match_id: str, provider_id: str, *,
                               agent_id: str | None = None,
                               reasoning_effort: str = "low",
                               model_id: str | None = None,
                               context_length: int | None = None,
                               generation_settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Resolve one match seat to its exact provider, worker, and MCP endpoint.

        The returned descriptor is intentionally secret-free.  A host adapter
        may use it to materialize an isolated Hermes profile without learning
        Docker secrets or provider credentials.
        """
        _require_id(match_id, "match_id")
        _require_id(provider_id, "provider_id")
        if agent_id is not None:
            _require_id(agent_id, "agent_id")
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT s.agent_id, s.perspective_id, s.instance_id, s.seat_index, "
                "s.faction_id, s.faction_name, a.display_name AS agent_name, "
                "a.personality_ref, s.metadata_json AS seat_metadata_json, "
                "a.status AS agent_status, m.display_name AS match_name, m.status AS match_status, "
                "m.ruleset_id, m.metadata_json AS match_metadata_json, "
                "w.observed_status, w.network_json FROM seat_assignments s "
                "JOIN agents a ON a.agent_id=s.agent_id JOIN matches m ON m.match_id=s.match_id "
                "JOIN worker_specs w ON w.instance_id=s.instance_id "
                "WHERE s.match_id=? AND s.controller_kind='agent' AND s.status='assigned' "
                "AND (? IS NULL OR s.agent_id=?) "
                "ORDER BY s.seat_index",
                (match_id, agent_id, agent_id),
            ).fetchall()
        if not rows:
            raise ScopeViolation("match_has_no_agent_worker")
        if len(rows) != 1:
            raise ScopeViolation("match_agent_seat_must_be_selected")
        seat = dict(rows[0])
        if seat["agent_status"] != "active":
            raise ScopeViolation("agent_not_active")
        if seat["observed_status"] != "running":
            raise ScopeViolation("game_worker_not_running")
        network = json.loads(str(seat.pop("network_json")))
        mcp_url = network.get("mcp_url")
        if network.get("mcp_status") != "running" or not isinstance(mcp_url, str):
            raise ScopeViolation("managed_mcp_not_running")
        provider = self.get_provider(provider_id)
        model_id = model_id or provider.get("default_model_id")
        model = next(
            (item for item in provider["models"] if item["model_id"] == model_id), None,
        )
        if not model_id or not model:
            raise ScopeViolation("harness_model_not_selected")
        generation = normalize_generation_settings(generation_settings)
        # Template identifiers are provenance only. Explicit stored values and
        # the independently selected Hermes reasoning effort are authoritative.
        context_length = context_length or provider.get("context_length_override") \
            or model.get("context_length")
        if context_length is None:
            raise InvalidRecord("provider_context_length_unavailable")
        if int(context_length) < HERMES_MINIMUM_CONTEXT_LENGTH:
            raise InvalidRecord("model_context_below_hermes_minimum")
        advertised_context = provider.get("context_length_override") \
            or model.get("context_length")
        if advertised_context is not None and int(context_length) > int(advertised_context):
            raise InvalidRecord("context_length_exceeds_model_limit")
        external_profile_id = "smacx-" + hashlib.sha256(
            str(seat["agent_id"]).encode("utf-8")
        ).hexdigest()[:20]
        seat_metadata = json.loads(str(seat.pop("seat_metadata_json")))
        requested_choice = seat_metadata.get("requested_faction_choice_id")
        observed_choice = seat_metadata.get("native_new_game_faction_choice_id")
        loaded_choice = seat_metadata.get("native_loaded_faction_choice_id")
        scenario_choice = seat_metadata.get("native_scenario_faction_choice_id")
        if isinstance(requested_choice, int) \
                and not isinstance(loaded_choice, int) \
                and not isinstance(scenario_choice, int) \
                and (not isinstance(observed_choice, int)
                     or observed_choice != requested_choice):
            raise ScopeViolation("native_faction_identity_does_not_match_resolved_agent")
        requested_faction_name = seat_metadata.get("requested_faction_name")
        observed_faction_name = seat.get("faction_name")
        if isinstance(requested_faction_name, str) \
                and (not isinstance(observed_faction_name, str)
                     or requested_faction_name.strip().casefold()
                     != observed_faction_name.strip().casefold()):
            raise ScopeViolation("native_faction_identity_does_not_match_resolved_agent")
        personality_id = str(
            seat_metadata.get("personality_id") or seat.get("personality_ref")
            or PERSONALITY_NONE
        )
        personality_prompt = seat_metadata.get("personality_prompt")
        if personality_id != PERSONALITY_NONE and not isinstance(personality_prompt, str):
            raise ScopeViolation("personality_card_content_not_available")
        match_metadata = json.loads(str(seat.pop("match_metadata_json")))
        policy_keys = (
            "host_controller_kind", "graphiti_enabled", "lan_profile",
            "scenario_id", "ranking_mode", "managed_clients_only",
        )
        match_policy = {
            key: match_metadata[key] for key in policy_keys if key in match_metadata
        }
        match_policy.update({
            "public_leader_identity": seat_metadata.get("player_name"),
            "requested_faction_key": seat_metadata.get("requested_faction_key"),
            "requested_faction_name": seat_metadata.get("requested_faction_name"),
            "requested_faction_choice_id": seat_metadata.get("requested_faction_choice_id"),
            "personality_name": seat_metadata.get("personality_name"),
        })
        public_agent_name = str(seat_metadata.get("player_name") or seat["agent_name"])
        system_prompt = compose_player_system_prompt(
            agent_name=public_agent_name, agent_id=str(seat["agent_id"]),
            match_id=match_id, match_name=str(seat["match_name"]),
            perspective_id=str(seat["perspective_id"]),
            ruleset_id=str(seat["ruleset_id"]), seat_index=int(seat["seat_index"]),
            match_policy=match_policy, personality_id=personality_id,
            personality_prompt=(str(personality_prompt)
                                if isinstance(personality_prompt, str) else None),
        )
        system_hash = prompt_sha256(system_prompt)
        profile = self.configure_harness_profile(
            str(seat["agent_id"]), provider_id,
            display_name=f"{seat['agent_name']} · Hermes",
            external_profile_id=external_profile_id,
            model_id=str(model_id), reasoning_effort=reasoning_effort,
            context_length=context_length,
            generation_settings=generation,
            system_prompt=system_prompt,
            metadata={
                "active_match_id": match_id,
                "perspective_id": seat["perspective_id"],
                "instance_id": seat["instance_id"],
                "mcp_url": mcp_url,
                "system_prompt_schema": SYSTEM_PROMPT_SCHEMA,
                "system_prompt_sha256": system_hash,
                "personality_id": personality_id,
                "generation_settings": generation,
            },
        )
        return {
            "schema": "smacx.hermes-descriptor.v1",
            "harness_profile_id": profile["harness_profile_id"],
            "external_profile_id": external_profile_id,
            "agent_id": seat["agent_id"],
            "agent_name": public_agent_name,
            "match_id": match_id,
            "match_name": seat["match_name"],
            "perspective_id": seat["perspective_id"],
            "instance_id": seat["instance_id"],
            "mcp_url": mcp_url,
            "provider_id": provider_id,
            "provider_base_url": provider["base_url"],
            "provider_requires_api_key": provider["has_api_key"],
            "model_id": model_id,
            "context_length": context_length,
            "reasoning_effort": reasoning_effort,
            "generation_settings": generation,
            "system_prompt_schema": SYSTEM_PROMPT_SCHEMA,
            "system_prompt_sha256": system_hash,
            "personality_id": personality_id,
        }

    def provider_api_key(self, provider_id: str) -> str | None:
        """Internal runtime-only credential access; never expose through HTTP."""
        _require_id(provider_id, "provider_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT api_key_secret_id FROM model_providers WHERE provider_id=? ",
                (provider_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_provider_id")
        secret_id = row["api_key_secret_id"]
        if not secret_id:
            return None
        return self.vault.read(
            str(secret_id), purpose=f"provider.{provider_id}.api_key",
        )

    def put_harness_runtime_spec(self, harness_profile_id: str, *, image_ref: str,
                                 data_volume: str, secret_volume: str,
                                 container_name: str,
                                 metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        _require_id(harness_profile_id, "harness_profile_id")
        for value, field, maximum in (
            (image_ref, "harness_image_ref", 512),
            (data_volume, "harness_data_volume", 255),
            (secret_volume, "harness_secret_volume", 255),
            (container_name, "harness_container_name", 255),
        ):
            _bounded(value, field, maximum)
        now = time.time()
        with self.store.transaction() as connection:
            if not connection.execute(
                "SELECT 1 FROM harness_profiles WHERE harness_profile_id=?",
                (harness_profile_id,),
            ).fetchone():
                raise ScopeViolation("unknown_harness_profile")
            connection.execute(
                "INSERT INTO harness_runtime_specs(harness_profile_id, image_ref, data_volume, "
                "secret_volume, container_name, observed_status, metadata_json, created_unix, updated_unix) "
                "VALUES (?, ?, ?, ?, ?, 'provisioned', ?, ?, ?) "
                "ON CONFLICT(harness_profile_id) DO UPDATE SET image_ref=excluded.image_ref, "
                "data_volume=excluded.data_volume, secret_volume=excluded.secret_volume, "
                "container_name=excluded.container_name, metadata_json=excluded.metadata_json, "
                "updated_unix=excluded.updated_unix",
                (harness_profile_id, image_ref, data_volume, secret_volume, container_name,
                 _json(metadata), now, now),
            )
        return self.get_harness_runtime_spec(harness_profile_id)

    def get_harness_runtime_spec(self, harness_profile_id: str) -> dict[str, Any]:
        _require_id(harness_profile_id, "harness_profile_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM harness_runtime_specs WHERE harness_profile_id=?",
                (harness_profile_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_harness_runtime")
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def create_harness_run(self, harness_profile_id: str, *, match_id: str,
                           initial_prompt: str,
                           continuation_prompt: str,
                           restart_policy: Mapping[str, Any],
                           run_id: str | None = None) -> dict[str, Any]:
        for value, field in ((harness_profile_id, "harness_profile_id"),
                             (match_id, "match_id")):
            _require_id(value, field)
        if not isinstance(initial_prompt, str) or not 1 <= len(initial_prompt) <= 65_536:
            raise InvalidRecord("invalid_harness_initial_prompt")
        if not isinstance(continuation_prompt, str) or not 1 <= len(continuation_prompt) <= 16_384:
            raise InvalidRecord("invalid_harness_continuation_prompt")
        profile = self.get_harness_profile(harness_profile_id)
        metadata = profile.get("metadata", {})
        if metadata.get("active_match_id") != match_id:
            raise ScopeViolation("harness_profile_match_mismatch")
        identifier = run_id or _new_id("run")
        _require_id(identifier, "run_id")
        now = time.time()
        with self.store.transaction() as connection:
            seat = connection.execute(
                "SELECT agent_id, perspective_id, instance_id FROM seat_assignments "
                "WHERE match_id=? AND agent_id=? AND controller_kind='agent'",
                (match_id, profile["agent_id"]),
            ).fetchone()
            if not seat or not seat["instance_id"]:
                raise ScopeViolation("harness_run_seat_unavailable")
            native = connection.execute(
                "SELECT session_id FROM sessions WHERE instance_id=? AND status='running' "
                "ORDER BY started_unix DESC LIMIT 1", (seat["instance_id"],),
            ).fetchone()
            try:
                connection.execute(
                    "INSERT INTO harness_runs(run_id, harness_profile_id, match_id, agent_id, "
                    "perspective_id, instance_id, native_session_id, status, initial_prompt, "
                    "continuation_prompt, restart_policy_json, metadata_json, created_unix, updated_unix) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, '{}', ?, ?)",
                    (identifier, harness_profile_id, match_id, seat["agent_id"],
                     seat["perspective_id"], seat["instance_id"],
                     native["session_id"] if native else None, initial_prompt, continuation_prompt,
                     _json(restart_policy), now, now),
                )
            except sqlite3.IntegrityError as exc:
                if "harness_runs.match_id" in str(exc):
                    raise ScopeViolation("harness_run_already_active_for_seat") from exc
                raise
        return self.get_harness_run(identifier)

    def update_harness_run(self, run_id: str, *, status: str | None = None,
                           desired_status: str | None = None,
                           container_name: str | None = None,
                           external_session_id: str | None = None,
                           last_error: str | None = None,
                           exit_code: int | None = None,
                           increment_restart: bool = False,
                           heartbeat: bool = False,
                           metadata_update: Mapping[str, Any] | None = None) -> dict[str, Any]:
        _require_id(run_id, "run_id")
        if status is not None and status not in {
                "queued", "starting", "running", "restarting", "stopped", "completed", "error"}:
            raise InvalidRecord("invalid_harness_run_status")
        if desired_status is not None and desired_status not in {"running", "stopped"}:
            raise InvalidRecord("invalid_harness_desired_status")
        now = time.time()
        fields = ["updated_unix=?"]
        values: list[Any] = [now]
        for name, value in (("status", status), ("desired_status", desired_status),
                            ("container_name", container_name),
                            ("external_session_id", external_session_id),
                            ("last_error", last_error), ("exit_code", exit_code)):
            if value is not None:
                fields.append(f"{name}=?")
                values.append(value)
        if heartbeat:
            fields.append("last_heartbeat_unix=?")
            values.append(now)
        if increment_restart:
            fields.append("restart_count=restart_count+1")
        if status == "running":
            fields.append("started_unix=COALESCE(started_unix, ?)")
            values.append(now)
        if status in {"stopped", "completed", "error"}:
            fields.append("stopped_unix=?")
            values.append(now)
        with self.store.transaction() as connection:
            if metadata_update is not None:
                if not isinstance(metadata_update, Mapping):
                    raise InvalidRecord("invalid_harness_run_metadata")
                row = connection.execute(
                    "SELECT metadata_json FROM harness_runs WHERE run_id=?", (run_id,),
                ).fetchone()
                if not row:
                    raise ScopeViolation("unknown_harness_run")
                metadata = json.loads(str(row["metadata_json"]))
                metadata.update(dict(metadata_update))
                fields.append("metadata_json=?")
                values.append(_json(metadata))
            values.append(run_id)
            cursor = connection.execute(
                f"UPDATE harness_runs SET {', '.join(fields)} WHERE run_id=?", values,
            )
            if cursor.rowcount != 1:
                raise ScopeViolation("unknown_harness_run")
        return self.get_harness_run(run_id)

    def get_harness_run(self, run_id: str) -> dict[str, Any]:
        _require_id(run_id, "run_id")
        with self.store.transaction() as connection:
            row = connection.execute("SELECT * FROM harness_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ScopeViolation("unknown_harness_run")
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        result["restart_policy"] = json.loads(result.pop("restart_policy_json"))
        return result

    def list_harness_runs(self) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            identifiers = [str(row[0]) for row in connection.execute(
                "SELECT run_id FROM harness_runs ORDER BY created_unix DESC"
            )]
        return [self.get_harness_run(identifier) for identifier in identifiers]

    def record_supervision_incident(
        self, instance_id: str, incident_kind: str, status: str,
        details: Mapping[str, Any],
    ) -> dict[str, Any]:
        _require_id(instance_id, "instance_id")
        if status not in {"open", "recovered", "operator_required", "closed"}:
            raise InvalidRecord("invalid_supervision_incident_status")
        if not isinstance(incident_kind, str) or not incident_kind or len(incident_kind) > 120:
            raise InvalidRecord("invalid_supervision_incident_kind")
        spec = self.get_worker_spec(instance_id)
        now = time.time()
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT incident_id FROM supervision_incidents WHERE instance_id=? "
                "AND incident_kind=? AND status IN ('open','operator_required') "
                "ORDER BY first_seen_unix DESC LIMIT 1", (instance_id, incident_kind),
            ).fetchone()
            if row:
                incident_id = str(row["incident_id"])
                connection.execute(
                    "UPDATE supervision_incidents SET status=?, details_json=?, "
                    "last_seen_unix=? WHERE incident_id=?",
                    (status, _json(details), now, incident_id),
                )
            else:
                incident_id = _new_id("incident")
                connection.execute(
                    "INSERT INTO supervision_incidents(incident_id, match_id, instance_id, "
                    "incident_kind, status, details_json, first_seen_unix, last_seen_unix) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (incident_id, spec["match_id"], instance_id, incident_kind,
                     status, _json(details), now, now),
                )
        return {"incident_id": incident_id, "status": status}

    def get_supervision_incident(self, incident_id: str) -> dict[str, Any]:
        _require_id(incident_id, "incident_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM supervision_incidents WHERE incident_id=?", (incident_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_supervision_incident")
        result = dict(row)
        result["details"] = json.loads(result.pop("details_json"))
        return result

    def list_supervision_incidents(
        self, *, match_id: str | None = None, active_only: bool = False,
    ) -> list[dict[str, Any]]:
        if match_id is not None:
            _require_id(match_id, "match_id")
        clauses: list[str] = []
        values: list[Any] = []
        if match_id is not None:
            clauses.append("match_id=?")
            values.append(match_id)
        if active_only:
            clauses.append("status IN ('open','operator_required')")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.store.transaction() as connection:
            identifiers = [str(row[0]) for row in connection.execute(
                "SELECT incident_id FROM supervision_incidents" + where
                + " ORDER BY last_seen_unix DESC, incident_id DESC", values,
            ).fetchall()]
        return [self.get_supervision_incident(identifier) for identifier in identifiers]

    def recover_supervision_incidents(
        self, match_id: str, *, kinds: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        """Mark selected active incident kinds recovered after verified recovery.

        Prefixes ending in ``:`` intentionally match namespaced incidents such
        as ``capability_gap:<gap-id>``.  This method does not clear unrelated
        operational failures for the same match.
        """
        _require_id(match_id, "match_id")
        if not kinds or any(not isinstance(kind, str) or not kind for kind in kinds):
            raise InvalidRecord("incident_recovery_kind_required")
        now = time.time()
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT incident_id, incident_kind FROM supervision_incidents "
                "WHERE match_id=? AND status IN ('open','operator_required')",
                (match_id,),
            ).fetchall()
            identifiers = [
                str(row["incident_id"])
                for row in rows
                if any(
                    str(row["incident_kind"]).startswith(kind)
                    if kind.endswith(":") else str(row["incident_kind"]) == kind
                    for kind in kinds
                )
            ]
            if identifiers:
                placeholders = ",".join("?" for _ in identifiers)
                connection.execute(
                    f"UPDATE supervision_incidents SET status='recovered', "
                    f"recovered_unix=?, last_seen_unix=? WHERE incident_id IN ({placeholders})",
                    (now, now, *identifiers),
                )
        return [self.get_supervision_incident(identifier) for identifier in identifiers]

    def get_harness_profile(self, harness_profile_id: str) -> dict[str, Any]:
        _require_id(harness_profile_id, "harness_profile_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM harness_profiles WHERE harness_profile_id=?", (harness_profile_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_harness_profile")
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    def list_harness_profiles(self) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            identifiers = [row["harness_profile_id"] for row in connection.execute(
                "SELECT harness_profile_id FROM harness_profiles ORDER BY display_name, harness_profile_id"
            )]
        return [self.get_harness_profile(str(identifier)) for identifier in identifiers]

    def list_agents(self) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT agent_id, display_name, status, profile_ref, personality_ref, "
                "metadata_json, created_unix, updated_unix FROM agents ORDER BY display_name, agent_id"
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            results.append(item)
        return results

    def list_matches(self) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            rows = connection.execute("SELECT * FROM matches ORDER BY created_unix DESC").fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            results.append(item)
        return results

    def get_match(self, match_id: str) -> dict[str, Any]:
        _require_id(match_id, "match_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM matches WHERE match_id=?", (match_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_match")
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result
