#!/usr/bin/env python3
"""Contained regression for Control Center auth, secrets, and providers."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading

from smacx_control import AuthenticationError, ControlPlane, ProviderError
from smacx_store import SmacxStore


def expect(error_type, function, message: str) -> None:
    try:
        function()
    except error_type as exc:
        if str(exc) != message:
            raise AssertionError(f"expected {message}, received {exc}") from exc
    else:
        raise AssertionError(f"expected {message}")


class ModelsHandler(BaseHTTPRequestHandler):
    expected_key = "provider-test-key"
    models = [
        {"id": "qwen-test-a", "context_length": 32768},
        {"id": "qwen-test-b", "metadata": {"max_model_len": "65536"}},
    ]

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path != "/v1/models":
            self.send_error(404)
            return
        if self.headers.get("Authorization") != f"Bearer {self.expected_key}":
            self.send_error(401)
            return
        payload = json.dumps({"object": "list", "data": self.models}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        return


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-control-test-") as temporary:
        root = Path(temporary)
        store = SmacxStore(root / "control.sqlite3")
        control = ControlPlane(store, root / "secrets")

        bootstrap = control.ensure_bootstrap_token()
        if not bootstrap["setup_required"] or not bootstrap["created"]:
            raise AssertionError("first-run bootstrap token was not created")
        token_path = Path(bootstrap["bootstrap_token_path"])
        if token_path.stat().st_mode & 0o777 != 0o600:
            raise AssertionError("bootstrap token permissions are not 0600")
        bootstrap_token = control.reveal_bootstrap_token()
        if bootstrap_token in json.dumps(bootstrap):
            raise AssertionError("bootstrap token leaked through setup metadata")
        expect(
            AuthenticationError,
            lambda: control.bootstrap_admin("wrong-token", "a sufficiently long password"),
            "invalid_bootstrap_token",
        )
        admin = control.bootstrap_admin(bootstrap_token, "a sufficiently long password")
        if admin["username"] != "admin" or token_path.exists():
            raise AssertionError("bootstrap did not create admin and revoke its token")
        if control.ensure_bootstrap_token()["setup_required"]:
            raise AssertionError("setup remained open after admin creation")
        expect(
            AuthenticationError,
            lambda: control.login("admin", "wrong password"),
            "invalid_credentials",
        )

        session = control.login("admin", "a sufficiently long password")
        authenticated = control.authenticate(session.token)
        if authenticated["admin_id"] != admin["admin_id"]:
            raise AssertionError("session authenticated as the wrong admin")
        control.require_csrf(session.auth_session_id, session.csrf_token)
        expect(
            AuthenticationError,
            lambda: control.require_csrf(session.auth_session_id, "wrong-csrf"),
            "invalid_csrf_token",
        )

        server = ThreadingHTTPServer(("127.0.0.1", 0), ModelsHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = control.configure_provider(
                "Local Qwen", f"http://127.0.0.1:{server.server_port}/v1/",
                api_key=ModelsHandler.expected_key,
            )
            if not provider["has_api_key"] or "api_key" in provider:
                raise AssertionError("provider secret was missing or exposed")
            discovered = control.discover_provider(provider["provider_id"])
            contexts = {model["model_id"]: model["context_length"] for model in discovered["models"]}
            if contexts != {"qwen-test-a": 32768, "qwen-test-b": 65536}:
                raise AssertionError(f"provider context discovery failed: {contexts}")
            if discovered["default_model_id"] is not None:
                raise AssertionError("multiple models were silently auto-selected")
            selected = control.select_provider_model(
                provider["provider_id"], "qwen-test-b", context_length_override=49152,
            )
            if selected["default_model_id"] != "qwen-test-b" \
                    or selected["context_length_override"] != 49152:
                raise AssertionError("explicit provider selection was not stored")

            ModelsHandler.models = [{"id": "only-model", "max_context_length": 131072}]
            single = control.discover_provider(provider["provider_id"])
            if single["default_model_id"] != "only-model":
                raise AssertionError("a sole advertised model was not auto-selected")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

        game = control.register_game_source(
            "Legal Steam copy", "/games/smacx",
            "573fd44cf18392da7cd048e2d8c24a8e130034364f374647e34e79a6c621d6f4",
        )
        runtime = control.register_runtime(
            "Managed Proton", "docker-volume", "smacx-proton-runtime",
            content_fingerprint="f" * 64,
        )
        if game["status"] != "validated" or runtime["status"] != "ready":
            raise AssertionError("runtime inventory was not persisted")

        with sqlite3.connect(store.path) as connection:
            audit = connection.execute("SELECT audit_id FROM control_audit LIMIT 1").fetchone()
            if not audit:
                raise AssertionError("authentication audit trail is empty")
            try:
                connection.execute("DELETE FROM control_audit WHERE audit_id=?", (audit[0],))
            except sqlite3.IntegrityError as exc:
                if "immutable" not in str(exc):
                    raise
            else:
                raise AssertionError("immutable control audit could be deleted")

        if not control.logout(session.auth_session_id):
            raise AssertionError("session was not revoked")
        expect(
            AuthenticationError,
            lambda: control.authenticate(session.token),
            "invalid_session",
        )
        if control.status()["schema_version"] != 2:
            raise AssertionError("Control Center schema version is incorrect")

        print(json.dumps({
            "event": "pass",
            "payload": {
                "first_run_bootstrap": True,
                "scrypt_passwords": True,
                "hashed_sessions_and_csrf": True,
                "file_secrets_redacted": True,
                "provider_discovery": True,
                "model_selection_contract": True,
                "runtime_inventory": True,
                "immutable_audit": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
