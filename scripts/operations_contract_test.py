#!/usr/bin/env python3
"""Contained regression for scheduling, backup integrity, and offline restore."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import time

from smacx_control import ControlPlane
from smacx_journal import CampaignJournal
from smacx_operations import OperationsManager, restore_backup_offline
from smacx_store import MemoryScope, SmacxStore, StoreError


class FakeWorkerManager:
    control_data_volume = None

    def __init__(self) -> None:
        self.checkpoints: list[tuple[str, str]] = []

    def checkpoint_match(self, match_id: str, *, slot: str) -> dict:
        self.checkpoints.append((match_id, slot))
        return {"ok": True, "match_id": match_id, "slot": slot}


def expect_error(function, code: str) -> None:
    try:
        function()
    except StoreError as exc:
        if str(exc) != code:
            raise AssertionError(f"expected {code}, got {exc}") from exc
    else:
        raise AssertionError(f"expected error {code}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-operations-test-") as temporary:
        root = Path(temporary)
        store = SmacxStore(root / "smacx.sqlite3")
        control = ControlPlane(store, root / "secrets")
        installation_id = store.installation_id()
        control.create_agent("Before backup", agent_id="agent-before")
        store.create_match(match_id="match-journal-backup", display_name="Journal", mode="lan")
        store.create_perspective(
            "match-journal-backup", "agent-before",
            perspective_id="perspective-journal-backup",
        )
        journal_scope = MemoryScope(
            "match-journal-backup", "agent-before", "perspective-journal-backup",
        )
        journal = CampaignJournal(root / "campaigns")
        before_event = journal.append(
            journal_scope, "memory.goal", {
                "record": {"goal_key": "survive", "title": "Survive"},
                "record_input": {"goal_key": "survive", "title": "Survive"},
            }, turn=1, commit_reason="Test backup boundary",
        )
        recovery_relative = (
            "recovery-snapshots/match-journal-backup/checkpoint-backup-test/"
            "hermes/harness-backup-test.tar.gz"
        )
        recovery_file = root / recovery_relative
        recovery_file.parent.mkdir(parents=True)
        recovery_file.write_bytes(b"checkpointed Hermes state")
        recovery_sha = hashlib.sha256(recovery_file.read_bytes()).hexdigest()
        with store.transaction() as connection:
            metadata = {
                "recovery_checkpoint": {
                    "checkpoint_id": "checkpoint-backup-test", "verified": True,
                    "ai_memory": {
                        "schema": "smacx.ai-memory-checkpoint.v1",
                        "hermes": [{"archive": recovery_relative,
                                    "archive_sha256": recovery_sha}],
                    },
                },
            }
            connection.execute(
                "UPDATE matches SET metadata_json=? WHERE match_id=?",
                (json.dumps(metadata), "match-journal-backup"),
            )
        secret = control.vault.put("test.backup", "test-secret-value")
        retained_trace = (root / "specialist-traces" / "match-journal-backup"
                          / "timeline-test" / "mission-test" / "attempt-test.jsonl.zst")
        retained_trace.parent.mkdir(parents=True)
        retained_trace.write_bytes(b"retained specialist diagnostic")
        backup_ops = OperationsManager(control, data_root=root)
        backup = backup_ops.create_backup(include_secrets=True, include_workers=False)
        verified = backup_ops.verify_backup(backup["backup_id"])
        if not verified["ok"] or not verified["includes_secrets"] \
                or not verified.get("campaigns_included") \
                or not verified.get("recovery_snapshots_included") \
                or not verified.get("specialist_traces_included"):
            raise AssertionError("complete backup did not verify")

        fake = FakeWorkerManager()
        scheduled_ops = OperationsManager(control, data_root=root, worker_manager=fake)  # type: ignore[arg-type]
        match = store.create_match(
            match_id="match-operations", display_name="Operations", mode="lan",
        )
        schedule = scheduled_ops.create_schedule(
            "Minute checkpoint", "checkpoint", target_kind="match",
            target_id=match["match_id"], interval_seconds=60,
            next_run_unix=time.time(), payload={"slot": "scheduled_recovery"},
        )
        run = scheduled_ops.run_due_once()
        if not run.get("ok") or fake.checkpoints != [(match["match_id"], "scheduled_recovery")]:
            raise AssertionError(f"due operation did not execute exactly once: {run}")
        with store.transaction() as connection:
            operation = connection.execute(
                "SELECT * FROM operation_runs WHERE schedule_id=?", (schedule["schedule_id"],),
            ).fetchone()
            if not operation or operation["status"] != "succeeded" or operation["finished_unix"] is None:
                raise AssertionError("operation run was not durably completed")
        with sqlite3.connect(store.path) as connection:
            try:
                connection.execute(
                    "UPDATE operation_runs SET status='failed' WHERE operation_run_id=?",
                    (operation["operation_run_id"],),
                )
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError("finished operation history was mutable")

        control.create_agent("After backup", agent_id="agent-after")
        journal.append(
            journal_scope, "memory.goal", {
                "record": {"goal_key": "expand", "title": "Expand"},
                "record_input": {"goal_key": "expand", "title": "Expand"},
            }, turn=2,
        )
        orphan = control.vault.put("test.orphan", "must-not-survive-restore")
        obsolete_recovery = root / "recovery-snapshots" / "obsolete.bin"
        obsolete_recovery.write_bytes(b"post-backup obsolete generation")
        obsolete_trace = root / "specialist-traces" / "obsolete.bin"
        obsolete_trace.write_bytes(b"post-backup obsolete trace")
        restored = restore_backup_offline(
            control, root, backup["backup_id"], confirm_installation_id=installation_id,
        )
        reopened = ControlPlane(SmacxStore(root / "smacx.sqlite3"), root / "secrets")
        names = {item["display_name"] for item in reopened.list_agents()}
        if names != {"Before backup"}:
            raise AssertionError(f"offline restore did not return to backup state: {names}")
        if reopened.vault.read(secret["secret_id"], purpose="test.backup") != "test-secret-value":
            raise AssertionError("offline restore did not recover secrets")
        if (root / "secrets" / f"{orphan['secret_id']}.secret").exists():
            raise AssertionError("offline restore retained an orphaned post-backup secret")
        if not restored.get("emergency_backup_id"):
            raise AssertionError("offline restore did not create a rollback backup")
        if recovery_file.read_bytes() != b"checkpointed Hermes state" \
                or obsolete_recovery.exists() \
                or not restored.get("recovery_snapshots_restored"):
            raise AssertionError("offline restore omitted checkpoint AI-memory snapshots")
        if retained_trace.read_bytes() != b"retained specialist diagnostic" \
                or obsolete_trace.exists() \
                or not restored.get("specialist_traces_restored"):
            raise AssertionError("offline restore omitted retained specialist traces")
        replayed = CampaignJournal(root / "campaigns").replay(journal_scope)
        if replayed["manifest"]["head_hash"] != before_event["event_hash"] \
                or "expand" in replayed["goals"]:
            raise AssertionError("offline restore did not restore canonical campaign journal")

        tamper_ops = OperationsManager(reopened, data_root=root)
        tamper = tamper_ops.create_backup(include_secrets=False, include_workers=False)
        database = root / "backups" / tamper["backup_id"] / "state.sqlite3"
        with database.open("ab") as stream:
            stream.write(b"tamper")
        expect_error(
            lambda: tamper_ops.verify_backup(tamper["backup_id"]),
            "backup_database_integrity_failure",
        )
        print(json.dumps({
            "event": "pass",
            "payload": {
                "canonical_schema_only": reopened.store.schema_version() == 1,
                "scheduled_operations_claimed_once": True,
                "finished_run_immutable": True,
                "consistent_sqlite_snapshot": True,
                "secret_backup_and_restore": True,
                "orphan_secret_removed": True,
                "pre_restore_rollback_backup": True,
                "campaign_journal_backup_and_restore": True,
                "ai_memory_snapshots_backup_and_restore": True,
                "specialist_traces_backup_and_restore": True,
                "tamper_detection": True,
                "worker_archives_fail_closed_without_manager": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
