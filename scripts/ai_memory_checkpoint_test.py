#!/usr/bin/env python3
"""Contained proof that native recovery cannot retain post-checkpoint AI memory."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from smacx_control import ControlPlane
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope, SmacxStore
from smacx_worker_manager import (
    HERMES_CHECKPOINT_SCRIPT, HERMES_CLEAR_SCRIPT, HERMES_RESTORE_SCRIPT,
    SAVE_DIGEST_SCRIPT,
    WorkerManager,
)


def run_script(script: str, environment: dict[str, str]) -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", script], check=False, capture_output=True,
        text=True, env={**os.environ, **environment}, timeout=30,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout.strip().splitlines()[-1])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-memory-checkpoint-") as temporary:
        root = Path(temporary)
        harness = root / "harness"
        control_root = root / "control"
        profile_id = "smacx-checkpoint-test"
        database = harness / "profiles" / profile_id / "state.db"
        database.parent.mkdir(parents=True)
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, title TEXT)")
            connection.execute(
                "CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "session_id TEXT NOT NULL, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO sessions(id,title) VALUES ('match-session','match-checkpoint-test'),"
                "('other-session','match-other-test')"
            )
            connection.execute(
                "INSERT INTO messages(session_id,value) VALUES "
                "('match-session','checkpoint-memory'),('other-session','other-memory')"
            )
        relative = "recovery-snapshots/match-checkpoint-test/checkpoint-test/hermes/test.tar.gz"
        archive = control_root / relative
        archive.parent.mkdir(parents=True)
        snapshot = run_script(HERMES_CHECKPOINT_SCRIPT, {
            "SMACX_HERMES_PROFILE_ID": profile_id,
            "SMACX_MATCH_ID": "match-checkpoint-test",
            "SMACX_CHECKPOINT_RELATIVE": relative,
            "SMACX_SOURCE_ROOT": str(harness),
            "SMACX_CONTROL_ROOT": str(control_root),
        })
        if snapshot.get("database_present") is not True or not archive.is_file():
            raise AssertionError("Hermes checkpoint was not captured")

        # Exercise the manager's archive creation, not only the helper script:
        # production runs the control API and helper as different uids sharing
        # one group, under a restrictive umask.
        manager = object.__new__(WorkerManager)
        manager.control_data_volume = "fixture-control"
        manager.mcp_image = "fixture"
        manager.store = SimpleNamespace(path=control_root / "state.sqlite3",
                                        installation_id=lambda: "fixture")
        manager.control = SimpleNamespace(
            list_harness_runs=lambda: [{"match_id": "match-checkpoint-test",
                                       "harness_profile_id": "harness-test"}],
            get_harness_runtime_spec=lambda _: {"data_volume": "fixture-harness"},
            get_harness_profile=lambda _: {"external_profile_id": profile_id},
        )
        manager.docker = SimpleNamespace(inspect_volume=lambda _: {},
                                        require_owned=lambda *args, **kwargs: None)

        def checkpoint_helper(*args, **kwargs):
            environment = dict(item.split("=", 1) for item in kwargs["environment"])
            target = control_root / environment["SMACX_CHECKPOINT_RELATIVE"]
            assert target.stat().st_mode & 0o777 == 0o660, "helper cannot write archive"
            return run_script(kwargs["script"], {
                **environment, "SMACX_SOURCE_ROOT": str(harness),
                "SMACX_CONTROL_ROOT": str(control_root),
            })

        manager._run_checkpoint_helper = checkpoint_helper
        previous_umask = os.umask(0o027)
        try:
            managed = manager._snapshot_hermes_state("match-checkpoint-test", "checkpoint-umask")
        finally:
            os.umask(previous_umask)
        managed_archive = control_root / managed[0]["archive"]
        assert managed[0]["archive_bytes"] > 0 and managed[0]["database_present"]
        assert managed_archive.stat().st_mode & 0o777 == 0o600
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO messages(session_id,value) VALUES "
                "('match-session','future-memory'),('other-session','later-other-memory')"
            )
        restored = run_script(HERMES_RESTORE_SCRIPT, {
            "SMACX_HERMES_PROFILE_ID": profile_id,
            "SMACX_MATCH_ID": "match-checkpoint-test",
            "SMACX_CHECKPOINT_RELATIVE": relative,
            "SMACX_CONTROL_ROOT": str(control_root),
            "SMACX_TARGET_ROOT": str(harness),
        })
        with sqlite3.connect(database) as connection:
            messages = [tuple(map(str, row)) for row in connection.execute(
                "SELECT session_id,value FROM messages ORDER BY id"
            )]
        if restored.get("database_present") is not True \
                or messages != [
                    ("match-session", "checkpoint-memory"),
                    ("other-session", "other-memory"),
                    ("other-session", "later-other-memory"),
                ]:
            raise AssertionError(f"Hermes future memory survived restore: {messages}")

        store = SmacxStore(root / "smacx.sqlite3")
        control = ControlPlane(store, root / "secrets")
        store.ensure_agent("agent-checkpoint-test", "Checkpoint Agent")
        store.create_match(
            match_id="match-checkpoint-test", display_name="Checkpoint Test", mode="lan",
        )
        store.create_perspective(
            "match-checkpoint-test", "agent-checkpoint-test",
            perspective_id="perspective-checkpoint-test",
        )
        scope = MemoryScope(
            "match-checkpoint-test", "agent-checkpoint-test", "perspective-checkpoint-test",
        )
        journal = CampaignJournal(
            root / "campaigns", timeline_resolver=store.active_timeline_id,
        )
        journal.append(scope, "memory.goal", {
            "record": {"goal_key": "survive", "status": "active"},
        })
        group = store.create_chat_group(scope, "Checkpoint Allies", 1, [
            {"faction_id": 1, "display_name": "Local"},
            {"faction_id": 2, "display_name": "Neighbor"},
        ])
        journal.append(scope, "chat.groups_snapshot", {
            "groups": store.export_chat_groups(scope.match_id),
        })
        checkpoint = journal.append(
            scope, "checkpoint.native", {"turn": 7}, turn=7, year=2107,
        )
        journal.append(scope, "memory.fact", {
            "key": "future", "value": "must disappear",
        }, turn=8, year=2108)
        store.respond_chat_group(scope, group["group_id"], 2, "accepted")
        journal.append(scope, "chat.groups_snapshot", {
            "groups": store.export_chat_groups(scope.match_id),
        })
        old_namespace = store.graph_namespace(scope)
        timeline = "timeline-restore-checkpoint"
        journal.fork_timeline(
            scope, timeline, native_save_sha256="a" * 64,
            from_event_hash=checkpoint["event_hash"],
            parent_timeline_id="timeline-main",
        )
        control.update_match_lifecycle(
            scope.match_id, "parked", metadata={"active_memory_timeline": timeline},
        )
        restored_state = journal.replay(scope)
        if "future" in restored_state["facts"] \
                or restored_state["manifest"]["timeline_id"] != timeline:
            raise AssertionError("journal future crossed the active checkpoint timeline")
        if journal.search(scope, "must disappear"):
            raise AssertionError("journal search exposed post-checkpoint memory")
        restored_groups = journal.chat_groups(scope)["groups"]
        store.replace_chat_groups(scope.match_id, restored_groups)
        projected_group = store.export_chat_groups(scope.match_id)[0]
        if projected_group["status"] != "inviting" or any(
                member["status"] == "accepted" and member["faction_id"] == 2
                for member in projected_group["members"]):
            raise AssertionError("post-checkpoint group state survived projection restore")
        new_namespace = store.graph_namespace(scope)
        if new_namespace == old_namespace:
            raise AssertionError("Graphiti namespace did not rotate with the timeline")
        rebuild = control.request_graphiti_rebuild(
            scope.match_id, scope.agent_id, scope.perspective_id,
            retired_namespaces=[old_namespace], reason="checkpoint_restore",
        )
        with store.transaction() as connection:
            row = connection.execute(
                "SELECT result_json FROM graphiti_rebuild_requests WHERE rebuild_id=?",
                (rebuild["rebuild_id"],),
            ).fetchone()
        request = json.loads(str(row["result_json"]))["request"]
        if request["target_namespace"] != new_namespace \
                or request["retired_namespaces"] != [old_namespace] \
                or request["timeline_id"] != timeline:
            raise AssertionError("Graphiti replacement request lost timeline identity")

        saves = root / "worker" / "game" / "saves"
        saves.mkdir(parents=True)
        save = saves / "control_recovery.sav"
        save.write_bytes(b"native checkpoint bytes")
        digest = run_script(SAVE_DIGEST_SCRIPT, {
            "SMACX_SAVE_SLOT": "control_recovery",
            "SMACX_STATE_ROOT": str(root / "worker"),
        })
        if digest.get("sha256") is None or digest.get("bytes") != save.stat().st_size:
            raise AssertionError("native save identity was not captured")

        run_script(HERMES_CLEAR_SCRIPT, {
            "SMACX_HERMES_PROFILE_ID": profile_id,
            "SMACX_MATCH_ID": "match-checkpoint-test",
            "SMACX_TARGET_ROOT": str(harness),
        })
        with sqlite3.connect(database) as connection:
            remaining = connection.execute(
                "SELECT session_id,value FROM messages ORDER BY id"
            ).fetchall()
        if remaining != [
                ("other-session", "other-memory"),
                ("other-session", "later-other-memory")]:
            raise AssertionError("cold match reset altered unrelated Hermes history")

        print(json.dumps({"event": "pass", "payload": {
            "hermes_sqlite_snapshot_exact": True,
            "post_checkpoint_hermes_memory_removed": True,
            "unrelated_hermes_campaign_preserved": True,
            "journal_restore_uses_new_active_timeline": True,
            "post_checkpoint_journal_memory_inaccessible": True,
            "post_checkpoint_search_memory_inaccessible": True,
            "post_checkpoint_group_memory_inaccessible": True,
            "graphiti_namespace_rotated": True,
            "retired_graphiti_namespace_queued_for_gc": True,
            "native_save_digest_bound": True,
            "cold_restore_discards_only_target_match_history": True,
        }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
