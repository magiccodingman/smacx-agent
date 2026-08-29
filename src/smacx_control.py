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
import ssl
import tempfile
import threading
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
import uuid

from smacx_store import InvalidRecord, ScopeViolation, SmacxStore, StoreError


CONTROL_ID = re.compile(r"^[A-Za-z0-9_-]{8,96}$")
CONTROL_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
MODEL_ID_LIMIT = 512
PROVIDER_RESPONSE_LIMIT = 4 * 1024 * 1024
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
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
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

    def status(self) -> dict[str, Any]:
        with self.store.transaction() as connection:
            counts = {
                table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in ("agents", "matches", "instances", "model_providers", "game_sources", "runtime_assets")
            }
        return {
            "ok": True,
            "schema_version": self.store.schema_version(),
            "installation_id": self.store.installation_id(),
            "setup_required": not self.admin_exists(),
            "counts": counts,
        }
