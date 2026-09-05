#!/usr/bin/env python3
"""Run the actual checkpoint manager/helper with production uid/gid and umask.

The only Docker resource is an ephemeral, network-isolated test container.
No running campaign volume or provider is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace


def inside() -> None:
    from smacx_worker_manager import WorkerManager
    assert os.geteuid() == 0, "run through the isolated test container"
    with tempfile.TemporaryDirectory(prefix="checkpoint-permissions-") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        source = root / "source" / "profiles" / "profile-test"
        source.mkdir(parents=True)
        with sqlite3.connect(source / "state.db") as db:
            db.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT)")
            db.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,value TEXT)")
            db.execute("INSERT INTO sessions VALUES ('session-test','match-test')")
            db.execute("INSERT INTO messages VALUES (1,'session-test','checkpoint memory')")
        target = root / "control"
        target.mkdir()
        os.chown(target, 10001, 10001)
        manager = object.__new__(WorkerManager)
        manager.control_data_volume = "fixture"
        manager.mcp_image = "fixture"
        manager.store = SimpleNamespace(path=target / "state.sqlite3", installation_id=lambda: "fixture")
        manager.control = SimpleNamespace(
            list_harness_runs=lambda: [{"match_id": "match-test", "harness_profile_id": "harness-test"}],
            get_harness_runtime_spec=lambda _: {"data_volume": "fixture"},
            get_harness_profile=lambda _: {"external_profile_id": "profile-test"},
        )
        manager.docker = SimpleNamespace(inspect_volume=lambda _: {}, require_owned=lambda *a, **k: None)

        def helper(*args, **kwargs):
            assert os.geteuid() == 10001
            env = dict(item.split("=", 1) for item in kwargs["environment"])
            os.seteuid(0)
            try:
                result = subprocess.run(
                    [sys.executable, "-c", kwargs["script"]],
                    env={**os.environ, **env, "SMACX_SOURCE_ROOT": str(root / "source"),
                         "SMACX_CONTROL_ROOT": str(target)},
                    user=10000, group=10001, extra_groups=[],
                    capture_output=True, text=True, timeout=30,
                )
            finally:
                os.seteuid(10001)
            if result.returncode:
                raise AssertionError(result.stderr)
            return json.loads(result.stdout)

        manager._run_checkpoint_helper = helper
        previous_umask = os.umask(0o027)
        os.setegid(10001)
        os.seteuid(10001)
        try:
            receipts = manager._snapshot_hermes_state("match-test", "checkpoint-test")
            archive = target / receipts[0]["archive"]
            assert receipts[0]["database_present"] and receipts[0]["archive_bytes"] > 0
            assert archive.stat().st_mode & 0o777 == 0o600
        finally:
            os.seteuid(0)
            os.setegid(0)
            os.umask(previous_umask)
        print(json.dumps({"passed": True, "control_uid": 10001, "helper_uid": 10000,
                          "shared_gid": 10001, "umask": "0027", "final_archive_mode": "0600",
                          "real_helper_sqlite_backup_and_archive": True}))


if __name__ == "__main__":
    if "--inside" in sys.argv:
        inside()
    else:
        repo = Path(__file__).resolve().parents[1]
        subprocess.run([
            "docker", "run", "--rm", "--network", "none", "--user", "0",
            "-v", f"{repo}:/workspace:ro", "-w", "/workspace", "-e", "PYTHONPATH=/workspace/src",
            "--entrypoint", "/opt/smacx/mcp-venv/bin/python", "smacx-agent-control:dev",
            "/workspace/scripts/checkpoint_helper_permissions_live_test.py", "--inside",
        ], check=True)
