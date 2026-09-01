#!/usr/bin/env python3
"""Contained contract for Graphiti scheduling, scoping, and secret handling."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from smacx_control import ControlPlane
from smacx_graphiti import _environment_secret, load_runtime_config
from smacx_graphiti_worker import _enabled, _scopes, _state
from smacx_store import ScopeViolation, SmacxStore


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-graphiti-worker-") as temporary:
        root = Path(temporary)
        store = SmacxStore(root / "smacx.sqlite3")
        control = ControlPlane(store, root / "secrets")
        status = control.ensure_graphiti_setting(default_enabled=False)
        if status["enabled"] or _enabled(store):
            raise AssertionError("Graphiti did not default to disabled")

        store.ensure_agent("agent-graph-001", "Graph Agent")
        store.create_match(match_id="match-graph-001", display_name="Graph Match", mode="lan")
        store.create_perspective(
            "match-graph-001", "agent-graph-001",
            perspective_id="perspective-graph-001",
        )
        with store.transaction() as connection:
            connection.execute(
                "UPDATE matches SET status='provisioned' WHERE match_id='match-graph-001'"
            )
        if len(_scopes(store)) != 1:
            raise AssertionError("active projection scope was not discovered")

        queued = control.request_graphiti_rebuild(
            "match-graph-001", "agent-graph-001", "perspective-graph-001",
        )
        if queued["status"] != "queued" or control.graphiti_status()["queued_rebuilds"] != 1:
            raise AssertionError("exact-scope rebuild was not queued")
        try:
            control.request_graphiti_rebuild(
                "match-graph-001", "agent-graph-001", "perspective-does-not-exist",
            )
        except ScopeViolation:
            pass
        else:
            raise AssertionError("cross-scope rebuild was accepted")

        _state(store, "degraded", active_scopes=1, failed=1, error="contained failure")
        runtime = control.graphiti_status()["runtime"]
        if runtime["status"] != "degraded" or runtime["failed_events"] != 1:
            raise AssertionError("projector failure was not observable")

        secret = root / "key"
        secret.write_text("not-exposed-in-env\n", encoding="utf-8")
        os.chmod(secret, 0o600)
        os.environ["CONTAINED_GRAPHITI_KEY_FILE"] = str(secret)
        try:
            if _environment_secret("CONTAINED_GRAPHITI_KEY") != "not-exposed-in-env":
                raise AssertionError("file secret was not loaded")
        finally:
            os.environ.pop("CONTAINED_GRAPHITI_KEY_FILE", None)

        provider = control.configure_provider("Graph extraction", "http://model.test/v1")
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO provider_models(provider_id,model_id,display_name,context_length,"
                "capabilities_json,raw_metadata_json,discovered_unix) VALUES(?,?,?,?,?,?,0)",
                (provider["provider_id"], "extract-model", "Extract model", 65536, "{}", "{}"),
            )
        control.set_graphiti_enabled(True, profile={
            "profile_id": "profile-graph-001",
            "display_name": "Graph extraction",
            "provider_id": provider["provider_id"], "model_id": "extract-model",
            "reasoning_effort": "none",
            "generation_settings": {"preset": "provider-default"},
        })
        runtime_config = load_runtime_config(store)
        if runtime_config.llm_model != "extract-model" \
                or runtime_config.embed_model != "smacx-local-embeddings" \
                or runtime_config.embed_dim != 2048:
            raise AssertionError("Graphiti did not resolve its selected profile and shared embedding endpoint")
        if not _enabled(store):
            raise AssertionError("Control Center enable policy was not persisted")
        control.set_graphiti_enabled(False)
        disabled = control.graphiti_status()
        if disabled["enabled"] or disabled["configured"] or disabled["profile"] is not None:
            raise AssertionError("disabling Graphiti did not clear its extraction profile")

        print(json.dumps({"event": "pass", "payload": {
            "default_disabled": True,
            "exact_scope_rebuild": True,
            "cross_scope_rejected": True,
            "failure_isolated_and_observable": True,
            "file_secret_supported": True,
            "extraction_profile_required": True,
            "disable_clears_extraction_profile": True,
            "canonical_schema_revision": store.schema_version(),
        }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
