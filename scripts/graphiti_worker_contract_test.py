#!/usr/bin/env python3
"""Contained contract for Graphiti scheduling, scoping, and secret handling."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from smacx_control import ControlPlane
from smacx_graphiti import _environment_secret
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

        control.set_graphiti_enabled(True)
        if not _enabled(store):
            raise AssertionError("Control Center enable policy was not persisted")

        print(json.dumps({"event": "pass", "payload": {
            "default_disabled": True,
            "exact_scope_rebuild": True,
            "cross_scope_rejected": True,
            "failure_isolated_and_observable": True,
            "file_secret_supported": True,
            "canonical_schema_revision": store.schema_version(),
        }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
