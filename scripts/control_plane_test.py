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
from smacx_store import InvalidRecord, MemoryScope, ScopeViolation, SmacxStore, StoreError


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
            InvalidRecord,
            lambda: control.bootstrap_admin(bootstrap_token, "Short1A"),
            "invalid_admin_password",
        )
        expect(
            AuthenticationError,
            lambda: control.bootstrap_admin("wrong-token", "Passw0rd"),
            "invalid_bootstrap_token",
        )
        admin = control.bootstrap_admin(bootstrap_token, "Passw0rd")
        if admin["username"] != "admin" or token_path.exists():
            raise AssertionError("bootstrap did not create admin and revoke its token")
        if control.ensure_bootstrap_token()["setup_required"]:
            raise AssertionError("setup remained open after admin creation")
        expect(
            AuthenticationError,
            lambda: control.login("admin", "wrong password"),
            "invalid_credentials",
        )

        session = control.login("admin", "Passw0rd")
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
            provider = control.configure_provider(
                "Local Qwen", provider["base_url"], provider_id=provider["provider_id"],
            )
            if not provider["has_api_key"]:
                raise AssertionError("editing without a new API key did not preserve the stored key")
            discovered = control.discover_provider(provider["provider_id"])
            contexts = {model["model_id"]: model["context_length"] for model in discovered["models"]}
            if contexts != {"qwen-test-a": 32768, "qwen-test-b": 65536}:
                raise AssertionError(f"provider context discovery failed: {contexts}")
            if discovered["default_model_id"] is not None:
                raise AssertionError("multiple models were silently auto-selected")
            selected = control.select_provider_model(
                provider["provider_id"], "qwen-test-b", context_length_override=65536,
            )
            if selected["default_model_id"] != "qwen-test-b" \
                    or selected["context_length_override"] != 65536:
                raise AssertionError("explicit provider selection was not stored")

            ModelsHandler.models = [{"id": "only-model", "max_context_length": 131072}]
            single = control.discover_provider(provider["provider_id"])
            if single["default_model_id"] != "only-model":
                raise AssertionError("a sole advertised model was not auto-selected")

            disposable = control.configure_provider(
                "Accidental endpoint", "http://unused-model-box:8000/v1",
                api_key="disposable-provider-key",
            )
            with store.transaction() as connection:
                secret = connection.execute(
                    "SELECT api_key_secret_id FROM model_providers WHERE provider_id=?",
                    (disposable["provider_id"],),
                ).fetchone()
            secret_id = str(secret["api_key_secret_id"])
            deleted = control.delete_provider(disposable["provider_id"])
            if not deleted["deleted"]:
                raise AssertionError("unused provider was not deleted")
            expect(
                ScopeViolation, lambda: control.get_provider(disposable["provider_id"]),
                "unknown_provider_id",
            )
            with store.transaction() as connection:
                secret_status = connection.execute(
                    "SELECT status FROM secret_refs WHERE secret_id=?", (secret_id,),
                ).fetchone()
            if not secret_status or secret_status["status"] != "revoked":
                raise AssertionError("deleted provider API key was not revoked")
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

        expect(
            ScopeViolation,
            lambda: control.create_solo_match("Orphan prevention", "agent-does-not-exist"),
            "unknown_active_agent",
        )
        if control.status()["counts"]["matches"] != 0:
            raise AssertionError("invalid solo match left an orphaned match")
        agent = control.create_agent("Test agent")
        expect(
            InvalidRecord,
            lambda: control.configure_harness_profile(
                agent["agent_id"], provider["provider_id"],
                display_name="Undersized Hermes profile",
                external_profile_id="smacx-undersized-agent", reasoning_effort="low",
                context_length=65_535,
            ),
            "invalid_context_length",
        )
        harness = control.configure_harness_profile(
            agent["agent_id"], provider["provider_id"], display_name="Test Hermes profile",
            external_profile_id="smacx-test-agent", reasoning_effort="low",
            workspace_path=str(root / "workspace"),
        )
        if harness["agent_id"] != agent["agent_id"] or harness["model_id"] != "only-model":
            raise AssertionError("Hermes harness profile was not scoped to its agent/provider")
        if harness["context_length"] != 65_536:
            raise AssertionError("automatic context did not resolve to the configured provider limit")
        storage = control.storage_policy()
        if storage["recent_checkpoints"] != 10 or storage["milestone_interval"] != 25 \
                or storage["retain_full_turn_history"] is not False:
            raise AssertionError("default save retention policy changed unexpectedly")
        updated_storage = control.set_storage_policy(
            recent_checkpoints=14, milestone_interval=50,
            retain_full_turn_history=True,
        )
        if updated_storage["recent_checkpoints"] != 14 \
                or updated_storage["milestone_interval"] != 50 \
                or updated_storage["retain_full_turn_history"] is not True:
            raise AssertionError("save retention policy was not persisted")
        expect(
            StoreError, lambda: control.delete_provider(provider["provider_id"]),
            "provider_in_use_by_harness_profile",
        )
        provider = control.configure_provider(
            "Local Qwen", provider["base_url"], provider_id=provider["provider_id"],
            api_key="", default_model_id="only-model",
        )
        solo = control.create_solo_match("Valid solo match", agent["agent_id"])
        if solo["perspective"]["match_id"] != solo["match"]["match_id"]:
            raise AssertionError("solo match perspective was scoped incorrectly")
        scope = MemoryScope(
            solo["match"]["match_id"], agent["agent_id"],
            solo["perspective"]["perspective_id"],
        )
        instance = store.register_instance(
            instance_id="instance-hermes-contract", worker_kind="container-linux",
            scope=scope, runtime_root="/worker-state",
        )
        bridge_secret = control.vault.put(
            "worker.instance-hermes-contract.bridge_token", "bridge-secret",
        )
        control.put_worker_spec(
            instance["instance_id"], game["game_source_id"], runtime["runtime_id"],
            "smacx-agent-worker:dev", "worker-hermes-contract", "worker-data-contract",
            bridge_secret["secret_id"], network={
                "secret_volume": "worker-secret-contract",
                "mcp_status": "running", "mcp_url": "http://127.0.0.1:48125/mcp",
            },
        )
        control.assign_instance_to_seat(
            scope.match_id, scope.agent_id, scope.perspective_id, instance["instance_id"],
        )
        control.update_worker_observation(
            instance["instance_id"], desired_status="running", observed_status="running",
            instance_status="running",
        )
        descriptor = control.prepare_hermes_profile(
            scope.match_id, provider["provider_id"], reasoning_effort="low",
        )
        if descriptor["instance_id"] != instance["instance_id"] \
                or descriptor["mcp_url"] != "http://127.0.0.1:48125/mcp" \
                or descriptor["provider_requires_api_key"] is not False:
            raise AssertionError("Hermes descriptor was not scoped to the exact seat and worker")
        managed_profile = control.get_harness_profile(descriptor["harness_profile_id"])
        managed_prompt = managed_profile.get("system_prompt", "")
        if descriptor.get("system_prompt_schema") != "smacx.player-system.v1" \
                or descriptor.get("system_prompt_sha256") \
                != managed_profile.get("metadata", {}).get("system_prompt_sha256") \
                or "smac_match_briefing" not in managed_prompt \
                or "Hermes Agent" in managed_prompt \
                or descriptor.get("personality_id") != "none":
            raise AssertionError("SMACX did not own the exact managed player system contract")
        with store.transaction() as connection:
            connection.execute("DELETE FROM worker_specs WHERE instance_id=?", (instance["instance_id"],))
            connection.execute(
                "UPDATE seat_assignments SET instance_id=NULL WHERE instance_id=?",
                (instance["instance_id"],),
            )
            connection.execute("DELETE FROM instances WHERE instance_id=?", (instance["instance_id"],))
        if not control.discard_unstarted_match(
            solo["match"]["match_id"], solo["perspective"]["perspective_id"],
        ) or control.status()["counts"]["matches"] != 0:
            raise AssertionError("unstarted match rollback failed")

        second_agent = control.create_agent("Second LAN agent")
        expect(
            InvalidRecord,
            lambda: control.create_lan_match(
                "Tampered personality", [agent["agent_id"]],
                agent_seats=[{
                    "agent_id": agent["agent_id"],
                    "player_name": "Lady Deirdre Skye",
                    "faction_key": "gaians",
                    "faction_name": "Gaia's Stepdaughters",
                    "faction_choice_id": 0,
                    "personality_id": "builtin:gaians:standard:v1",
                    "personality_prompt": "tampered",
                    "personality_prompt_sha256": "0" * 64,
                }],
            ),
            "invalid_lan_personality_prompt_hash",
        )
        lan = control.create_lan_match(
            "Managed LAN contract", [agent["agent_id"], second_agent["agent_id"]],
        )
        if len(lan["seats"]) != 2 \
                or {seat["seat_index"] for seat in lan["seats"]} != {0, 1} \
                or len({seat["perspective_id"] for seat in lan["seats"]}) != 2:
            raise AssertionError("LAN seats did not receive distinct ordered perspectives")
        if any(seat["match_id"] != lan["match"]["match_id"] for seat in lan["seats"]):
            raise AssertionError("LAN seats escaped their shared durable match")
        progressed = control.record_match_progress(lan["match"]["match_id"], 9, 2109)
        if progressed["last_turn"] != 9 or progressed["last_year"] != 2109:
            raise AssertionError("public native turn/year progress was not persisted")

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
        if control.status()["schema_version"] != 1:
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
                "solo_match_orphan_guard": True,
                "unstarted_match_rollback": True,
                "isolated_harness_profile": True,
                "automatic_context_and_minimum": True,
                "storage_policy": True,
                "exact_hermes_descriptor": True,
                "managed_lan_identity_contract": True,
                "native_progress_mirror": True,
                "immutable_audit": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
