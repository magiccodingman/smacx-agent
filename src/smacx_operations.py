"""Durable scheduling, supervision, and backup services for Control Center.

The module keeps policy and history in the canonical SQLite store.  It never
guesses at native game state: automatic recovery is permitted only from a
checkpoint that the bridge successfully created and recorded for the match.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from typing import Any, Mapping
import uuid
from typing import TYPE_CHECKING

from smacx_docker import DockerNotFound
from smacx_store import InvalidRecord, ScopeViolation, StoreError
from smacx_worker_manager import WorkerManager, WorkerManagerError

if TYPE_CHECKING:
    from smacx_harness_manager import HarnessManager


IDENTITY = re.compile(r"^[A-Za-z0-9_-]{8,96}$")
BACKUP_DIRECTORY = re.compile(r"^backup-[a-f0-9]{32}$")
OPERATION_KINDS = {"backup", "checkpoint", "match_start", "match_resume"}
TARGET_KINDS = {"installation", "match"}


def _new_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex}"


def _require_id(value: str, field: str) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise InvalidRecord(f"invalid_{field}")
    return value


def _json(value: Mapping[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


class OperationsManager:
    """One-process coordinator backed by cross-process-safe SQLite claims."""

    def __init__(self, control, *, data_root: Path | str,
                 worker_manager: WorkerManager | None = None,
                 harness_manager: "HarnessManager | None" = None) -> None:
        self.control = control
        self.store = control.store
        self.worker_manager = worker_manager
        self.harness_manager = harness_manager
        self.data_root = Path(data_root).expanduser().resolve()
        self.backup_root = (self.data_root / "backups").resolve()
        if self.backup_root.parent != self.data_root:
            raise StoreError("invalid_backup_root")
        self.backup_root.mkdir(parents=True, exist_ok=True, mode=0o750)
        os.chmod(self.backup_root, 0o750)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._operation_lock = threading.RLock()

    def create_schedule(self, display_name: str, operation_kind: str, *,
                        target_kind: str, target_id: str | None,
                        interval_seconds: int, next_run_unix: float | None = None,
                        payload: Mapping[str, Any] | None = None,
                        schedule_id: str | None = None) -> dict[str, Any]:
        display_name = str(display_name).strip()
        if not 1 <= len(display_name) <= 160:
            raise InvalidRecord("invalid_schedule_name")
        if operation_kind not in OPERATION_KINDS:
            raise InvalidRecord("invalid_operation_kind")
        if target_kind not in TARGET_KINDS:
            raise InvalidRecord("invalid_target_kind")
        if target_kind == "match":
            _require_id(str(target_id or ""), "target_id")
            self.control.get_match(str(target_id))
        elif target_id is not None:
            raise InvalidRecord("installation_target_must_be_null")
        interval = int(interval_seconds)
        if not 60 <= interval <= 2_592_000:
            raise InvalidRecord("invalid_schedule_interval")
        if operation_kind in {"checkpoint", "match_start", "match_resume"} \
                and target_kind != "match":
            raise InvalidRecord("match_operation_requires_match_target")
        identifier = schedule_id or _new_id("schedule")
        _require_id(identifier, "schedule_id")
        now = time.time()
        next_run = float(next_run_unix if next_run_unix is not None else now + interval)
        if next_run < now - 60 or next_run > now + 31_536_000:
            raise InvalidRecord("invalid_schedule_next_run")
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO operation_schedules(schedule_id, display_name, operation_kind, "
                "target_kind, target_id, interval_seconds, payload_json, status, next_run_unix, "
                "created_unix, updated_unix) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                (identifier, display_name, operation_kind, target_kind, target_id, interval,
                 _json(payload), next_run, now, now),
            )
        return self.get_schedule(identifier)

    def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        _require_id(schedule_id, "schedule_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM operation_schedules WHERE schedule_id=?", (schedule_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_schedule")
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def list_schedules(self) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            identifiers = [str(row[0]) for row in connection.execute(
                "SELECT schedule_id FROM operation_schedules ORDER BY next_run_unix, schedule_id"
            )]
        return [self.get_schedule(identifier) for identifier in identifiers]

    def set_schedule_status(self, schedule_id: str, status: str) -> dict[str, Any]:
        _require_id(schedule_id, "schedule_id")
        if status not in {"active", "paused", "disabled"}:
            raise InvalidRecord("invalid_schedule_status")
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE operation_schedules SET status=?, updated_unix=? WHERE schedule_id=?",
                (status, time.time(), schedule_id),
            )
            if cursor.rowcount != 1:
                raise ScopeViolation("unknown_schedule")
        return self.get_schedule(schedule_id)

    def _backup_path(self, backup_id: str) -> Path:
        _require_id(backup_id, "backup_id")
        name = backup_id
        if not BACKUP_DIRECTORY.fullmatch(name):
            raise InvalidRecord("invalid_backup_id")
        path = (self.backup_root / name).resolve()
        if path.parent != self.backup_root:
            raise ScopeViolation("backup_path_outside_root")
        return path

    def _copy_secrets(self, destination: Path) -> int:
        destination.mkdir(mode=0o700)
        copied = 0
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT secret_id, relative_path, fingerprint FROM secret_refs "
                "WHERE status='active' ORDER BY secret_id"
            ).fetchall()
        for row in rows:
            source = self.control.vault.path_for_mount(
                str(row["secret_id"]), purpose=self._secret_purpose(str(row["secret_id"])),
            )
            target = destination / str(row["relative_path"])
            if target.parent != destination:
                raise ScopeViolation("secret_backup_path_outside_bundle")
            shutil.copyfile(source, target)
            os.chmod(target, 0o600)
            if _sha256(target) != str(row["fingerprint"]):
                raise StoreError("secret_backup_integrity_failure")
            copied += 1
        return copied

    def _secret_purpose(self, secret_id: str) -> str:
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT purpose FROM secret_refs WHERE secret_id=? AND status='active'", (secret_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_or_revoked_secret")
        return str(row["purpose"])

    def _backup_managed_volume(self, backup_relative: str, identity: str,
                               volume_name: str, *, volume_purpose: str,
                               archive_relative: str,
                               active_container: str | None = None,
                               active_purpose: str | None = None,
                               source_user: str = "10001:10001") -> None:
        manager = self.worker_manager
        if manager is None or not manager.control_data_volume:
            raise WorkerManagerError("worker_backup_requires_managed_control_volume")
        resource = manager.docker.inspect_volume(volume_name)
        manager.docker.require_owned(
            resource, manager.installation_id, purpose=volume_purpose,
        )
        helper_name = manager._name("backup", f"{backup_relative}:{identity}")  # noqa: SLF001
        target_relative = f"backups/{backup_relative}/{archive_relative}"
        target_path = (self.data_root / target_relative).resolve()
        if self.data_root not in target_path.parents:
            raise ScopeViolation("backup_archive_path_outside_data_root")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target_path.parent, 0o770)
        target_path.touch(mode=0o660, exist_ok=False)
        os.chmod(target_path, 0o660)
        script = (
            "import pathlib,tarfile;"
            "p=pathlib.Path('/control/')/" + repr(target_relative) + ";"
            "t=tarfile.open(p,'w:gz');t.add('/source',arcname='.');t.close()"
        )
        config = {
            "Image": manager.mcp_image,
            "Entrypoint": ["python3"],
            "Cmd": ["-c", script],
            "User": source_user,
            "Labels": manager._labels("backup-helper", **{  # noqa: SLF001
                "io.smacx.backup-source": identity,
            }),
            "HostConfig": {
                "ReadonlyRootfs": True,
                "NetworkMode": "none",
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Mounts": [
                    {"Type": "volume", "Source": volume_name, "Target": "/source", "ReadOnly": True},
                    {"Type": "volume", "Source": manager.control_data_volume, "Target": "/control"},
                ],
            },
        }
        identifier: str | None = None
        paused_container: str | None = None
        try:
            if active_container and active_purpose:
                try:
                    running = manager.docker.inspect_container(active_container)
                    manager.docker.require_owned(
                        running, manager.installation_id, purpose=active_purpose,
                    )
                    if running.get("State", {}).get("Running"):
                        manager.docker.pause_container(active_container)
                        paused_container = active_container
                except DockerNotFound:
                    pass
            try:
                old = manager.docker.inspect_container(helper_name)
                manager.docker.require_owned(old, manager.installation_id, purpose="backup-helper")
                if old.get("State", {}).get("Running"):
                    raise WorkerManagerError("backup_helper_already_running")
                manager.docker.remove_container(helper_name)
            except DockerNotFound:
                pass
            identifier = manager.docker.create_container(helper_name, config)
            manager.docker.start_container(identifier)
            state = manager.docker.wait_container(identifier, timeout=1800)
            if int(state.get("State", {}).get("ExitCode", -1)) != 0:
                detail = manager.docker.container_logs(identifier, tail=100).strip()[-2000:]
                raise WorkerManagerError(
                    "worker_backup_helper_failed" + (f":{detail}" if detail else "")
                )
            os.chmod(target_path, 0o600)
        finally:
            if identifier:
                try:
                    manager._cleanup_container(identifier, "backup-helper")  # noqa: SLF001
                except Exception:
                    pass
            if paused_container:
                try:
                    manager.docker.unpause_container(paused_container)
                except Exception as exc:
                    if volume_purpose == "worker-data":
                        self.control.update_worker_observation(
                            identity, observed_status="error",
                            last_error=f"backup_unpause_failed:{str(exc)[:1000]}",
                        )
                    raise WorkerManagerError("worker_backup_unpause_failed") from exc

    def _backup_worker_volume(self, backup_relative: str, instance_id: str,
                              volume_name: str) -> None:
        spec = self.control.get_worker_spec(instance_id)
        self._backup_managed_volume(
            backup_relative, instance_id, volume_name,
            volume_purpose="worker-data",
            archive_relative=f"workers/{instance_id}.tar.gz",
            active_container=str(spec["container_name"]), active_purpose="game-worker",
        )

    def _backup_harness_volume(self, backup_relative: str,
                               harness_profile_id: str, volume_name: str,
                               container_name: str) -> None:
        self._backup_managed_volume(
            backup_relative, harness_profile_id, volume_name,
            volume_purpose="harness-data",
            archive_relative=f"harnesses/{harness_profile_id}.tar.gz",
            active_container=container_name, active_purpose="harness-run",
            source_user="10000:10001",
        )

    def create_backup(self, *, include_secrets: bool = True,
                      include_workers: bool = True) -> dict[str, Any]:
        with self._operation_lock:
            return self._create_backup(
                include_secrets=include_secrets, include_workers=include_workers,
            )

    def _create_backup(self, *, include_secrets: bool,
                       include_workers: bool) -> dict[str, Any]:
        backup_id = _new_id("backup")
        final_path = self._backup_path(backup_id)
        temporary_path = Path(tempfile.mkdtemp(prefix=f".{backup_id}.", dir=self.backup_root))
        # Purpose-specific archive helpers need group traversal only while this
        # hidden staging bundle exists. Every payload is 0600 and the completed
        # bundle is locked back to 0700 before publication.
        os.chmod(temporary_path, 0o750)
        relative_path = backup_id
        now = time.time()
        installation_id = self.store.installation_id()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO backup_sets(backup_id, installation_id, status, relative_path, "
                "includes_secrets, created_unix) VALUES (?, ?, 'creating', ?, ?, ?)",
                (backup_id, installation_id, relative_path, 1 if include_secrets else 0, now),
            )
        try:
            database_path = temporary_path / "state.sqlite3"
            with sqlite3.connect(self.store.path) as source, sqlite3.connect(database_path) as target:
                source.backup(target)
            os.chmod(database_path, 0o600)
            secret_count = self._copy_secrets(temporary_path / "secrets") if include_secrets else 0
            workers: list[dict[str, Any]] = []
            harnesses: list[dict[str, Any]] = []
            if include_workers:
                if self.worker_manager is None:
                    with self.store.transaction() as connection:
                        worker_total = int(connection.execute(
                            "SELECT count(*) FROM worker_specs"
                        ).fetchone()[0])
                    if worker_total:
                        raise WorkerManagerError("worker_backup_manager_unavailable")
                else:
                    for spec in self.control.list_worker_specs():
                        self._backup_worker_volume(temporary_path.name, str(spec["instance_id"]),
                                                   str(spec["data_volume"]))
                        archive = temporary_path / "workers" / f"{spec['instance_id']}.tar.gz"
                        if not archive.is_file():
                            raise StoreError("worker_backup_archive_missing")
                        workers.append({
                            "instance_id": spec["instance_id"],
                            "volume_name": spec["data_volume"],
                            "archive": f"workers/{spec['instance_id']}.tar.gz",
                            "sha256": _sha256(archive),
                            "size_bytes": archive.stat().st_size,
                        })
                    with self.store.transaction() as connection:
                        runtime_rows = connection.execute(
                            "SELECT harness_profile_id, data_volume, container_name "
                            "FROM harness_runtime_specs ORDER BY harness_profile_id"
                        ).fetchall()
                    if runtime_rows and self.harness_manager is None:
                        raise WorkerManagerError("harness_backup_manager_unavailable")
                    if self.harness_manager is not None:
                        for runtime in runtime_rows:
                            profile_id = str(runtime["harness_profile_id"])
                            self._backup_harness_volume(
                                temporary_path.name, profile_id,
                                str(runtime["data_volume"]), str(runtime["container_name"]),
                            )
                            archive = temporary_path / "harnesses" / f"{profile_id}.tar.gz"
                            if not archive.is_file():
                                raise StoreError("harness_backup_archive_missing")
                            harnesses.append({
                                "harness_profile_id": profile_id,
                                "volume_name": runtime["data_volume"],
                                "archive": f"harnesses/{profile_id}.tar.gz",
                                "sha256": _sha256(archive),
                                "size_bytes": archive.stat().st_size,
                            })
            manifest = {
                "schema": "smacx.backup.v1",
                "backup_id": backup_id,
                "installation_id": installation_id,
                "created_unix": now,
                "database": {"path": "state.sqlite3", "sha256": _sha256(database_path)},
                "secrets": {"included": include_secrets, "count": secret_count},
                "workers": workers,
                "harnesses": harnesses,
            }
            manifest_path = temporary_path / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                     encoding="utf-8")
            os.chmod(manifest_path, 0o600)
            manifest_sha = _sha256(manifest_path)
            os.chmod(temporary_path, 0o700)
            os.replace(temporary_path, final_path)
            size = _tree_size(final_path)
            completed = time.time()
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE backup_sets SET status='complete', manifest_sha256=?, database_sha256=?, "
                    "worker_count=?, size_bytes=?, completed_unix=?, last_error=NULL WHERE backup_id=?",
                    (manifest_sha, manifest["database"]["sha256"], len(workers), size,
                     completed, backup_id),
                )
            return self.get_backup(backup_id)
        except Exception as exc:
            shutil.rmtree(temporary_path, ignore_errors=True)
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE backup_sets SET status='invalid', last_error=? WHERE backup_id=?",
                    (str(exc)[:2000], backup_id),
                )
            raise

    def get_backup(self, backup_id: str) -> dict[str, Any]:
        _require_id(backup_id, "backup_id")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM backup_sets WHERE backup_id=?", (backup_id,),
            ).fetchone()
        if not row:
            raise ScopeViolation("unknown_backup")
        return dict(row)

    def list_backups(self) -> list[dict[str, Any]]:
        with self.store.transaction() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM backup_sets ORDER BY created_unix DESC"
            ).fetchall()]

    def verify_backup(self, backup_id: str) -> dict[str, Any]:
        record = self.get_backup(backup_id)
        path = self._backup_path(backup_id)
        manifest_path = path / "manifest.json"
        database_path = path / "state.sqlite3"
        if not path.is_dir() or path.is_symlink() or not manifest_path.is_file() \
                or not database_path.is_file():
            raise StoreError("backup_bundle_incomplete")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != "smacx.backup.v1" \
                or manifest.get("backup_id") != backup_id \
                or manifest.get("installation_id") != record["installation_id"]:
            raise StoreError("backup_manifest_identity_mismatch")
        if record.get("manifest_sha256") and _sha256(manifest_path) != record["manifest_sha256"]:
            raise StoreError("backup_manifest_integrity_failure")
        expected_database = manifest.get("database", {}).get("sha256")
        if not isinstance(expected_database, str) or _sha256(database_path) != expected_database:
            raise StoreError("backup_database_integrity_failure")
        with sqlite3.connect(database_path) as connection:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            installation = connection.execute(
                "SELECT installation_id FROM installations WHERE singleton=1"
            ).fetchone()
        if integrity != "ok" or not installation \
                or str(installation[0]) != record["installation_id"]:
            raise StoreError("backup_database_invalid")
        for worker in manifest.get("workers", []):
            relative = worker.get("archive")
            if not isinstance(relative, str) or not re.fullmatch(
                    r"workers/instance-[A-Za-z0-9_-]{1,87}\.tar\.gz", relative):
                raise StoreError("backup_worker_manifest_invalid")
            archive = (path / relative).resolve()
            if archive.parent != (path / "workers").resolve() or not archive.is_file() \
                    or _sha256(archive) != worker.get("sha256"):
                raise StoreError("backup_worker_integrity_failure")
        for harness in manifest.get("harnesses", []):
            relative = harness.get("archive")
            if not isinstance(relative, str) or not re.fullmatch(
                    r"harnesses/harness-[A-Za-z0-9_-]{1,87}\.tar\.gz", relative):
                raise StoreError("backup_harness_manifest_invalid")
            archive = (path / relative).resolve()
            if archive.parent != (path / "harnesses").resolve() or not archive.is_file() \
                    or _sha256(archive) != harness.get("sha256"):
                raise StoreError("backup_harness_integrity_failure")
        return {
            "ok": True, "backup_id": backup_id, "installation_id": record["installation_id"],
            "worker_count": len(manifest.get("workers", [])),
            "harness_count": len(manifest.get("harnesses", [])),
            "includes_secrets": bool(manifest.get("secrets", {}).get("included")),
            "size_bytes": _tree_size(path),
        }

    def _claim_due(self) -> dict[str, Any] | None:
        now = time.time()
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM operation_schedules WHERE status='active' AND next_run_unix<=? "
                "ORDER BY next_run_unix, schedule_id LIMIT 1", (now,),
            ).fetchone()
            if not row:
                return None
            run_id = _new_id("operation")
            next_run = now + int(row["interval_seconds"])
            connection.execute(
                "UPDATE operation_schedules SET next_run_unix=?, updated_unix=? WHERE schedule_id=?",
                (next_run, now, row["schedule_id"]),
            )
            connection.execute(
                "INSERT INTO operation_runs(operation_run_id, schedule_id, operation_kind, "
                "target_kind, target_id, status, started_unix) VALUES (?, ?, ?, ?, ?, 'running', ?)",
                (run_id, row["schedule_id"], row["operation_kind"], row["target_kind"],
                 row["target_id"], now),
            )
        result = dict(row)
        result["operation_run_id"] = run_id
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def _finish_run(self, schedule: Mapping[str, Any], *, result: Mapping[str, Any] | None = None,
                    error: Exception | None = None) -> None:
        now = time.time()
        succeeded = error is None
        error_code = None if succeeded else str(error)[:2000]
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE operation_runs SET status=?, result_json=?, error_code=?, finished_unix=? "
                "WHERE operation_run_id=? AND finished_unix IS NULL",
                ("succeeded" if succeeded else "failed", _json(result), error_code, now,
                 schedule["operation_run_id"]),
            )
            connection.execute(
                "UPDATE operation_schedules SET last_run_unix=?, last_outcome=?, "
                "consecutive_failures=CASE WHEN ? THEN 0 ELSE consecutive_failures+1 END, "
                "last_error=?, updated_unix=? WHERE schedule_id=?",
                (now, "succeeded" if succeeded else "failed", 1 if succeeded else 0,
                 error_code, now, schedule["schedule_id"]),
            )

    def _execute_schedule(self, schedule: Mapping[str, Any]) -> dict[str, Any]:
        kind = str(schedule["operation_kind"])
        payload = schedule.get("payload") if isinstance(schedule.get("payload"), Mapping) else {}
        if kind == "backup":
            return self.create_backup(
                include_secrets=payload.get("include_secrets", True) is True,
                include_workers=payload.get("include_workers", True) is True,
            )
        if self.worker_manager is None:
            raise WorkerManagerError("scheduled_game_operation_requires_worker_manager")
        match_id = str(schedule.get("target_id") or "")
        if kind == "checkpoint":
            return self.worker_manager.checkpoint_match(
                match_id, slot=str(payload.get("slot", "control_recovery")),
            )
        if kind == "match_start":
            return self.worker_manager.start_lan_match(
                match_id, profile=str(payload.get("profile", "small_easy")),
            )
        if kind == "match_resume":
            return self.worker_manager.recover_match(match_id)
        raise InvalidRecord("invalid_operation_kind")

    def run_due_once(self) -> dict[str, Any]:
        schedule = self._claim_due()
        if schedule is None:
            return {"ok": True, "claimed": False}
        try:
            result = self._execute_schedule(schedule)
        except Exception as exc:
            self._finish_run(schedule, error=exc)
            return {"ok": False, "claimed": True, "schedule_id": schedule["schedule_id"],
                    "error": str(exc)}
        self._finish_run(schedule, result=result)
        return {"ok": True, "claimed": True, "schedule_id": schedule["schedule_id"],
                "result": result}

    def _incident(self, instance_id: str, kind: str, status: str,
                  details: Mapping[str, Any]) -> dict[str, Any]:
        return self.control.record_supervision_incident(
            instance_id, kind, status, details,
        )

    def reconcile_once(self) -> dict[str, Any]:
        with self._operation_lock:
            return self._reconcile_once()

    def _reconcile_once(self) -> dict[str, Any]:
        if self.worker_manager is None:
            harness_result = self.harness_manager.reconcile_once() \
                if self.harness_manager is not None else {"checked": 0, "restarted": 0}
            return {"ok": True, "checked": 0, "recovered": 0, "operator_required": 0,
                    "harness": harness_result}
        checked = recovered = operator_required = 0
        for spec in self.control.list_worker_specs():
            if spec["desired_status"] != "running":
                continue
            checked += 1
            # start_worker publishes the healthy game observation immediately
            # before it creates the MCP sidecar. Give that one in-process
            # transition a bounded grace period so the supervisor cannot race
            # the lifecycle request and create a second sidecar.
            if time.time() - float(spec.get("updated_unix") or 0) < 15.0:
                continue
            try:
                observed = self.worker_manager.worker_status(str(spec["instance_id"]))
                if observed.get("running") and observed.get("health") == "healthy":
                    mcp = observed.get("mcp") if isinstance(observed.get("mcp"), Mapping) else {}
                    if self.worker_manager.control_data_volume and (
                            not mcp.get("running") or mcp.get("health") != "healthy"):
                        self.worker_manager.start_mcp_sidecar(str(spec["instance_id"]))
                        self._incident(str(spec["instance_id"]), "mcp_sidecar_lost", "recovered",
                                       {"action": "sidecar_restarted"})
                        recovered += 1
                    continue
                match = self.control.get_match(str(spec["match_id"]))
                checkpoint = match.get("metadata", {}).get("recovery_checkpoint")
                # A browser-managed human host is recoverable in exactly the
                # same way as an agent host: it has an isolated worker and a
                # bridge-verified save. A truly external host can never have
                # produced this managed checkpoint in the first place.
                if isinstance(checkpoint, Mapping) and checkpoint.get("verified") is True:
                    self.worker_manager.recover_match(str(spec["match_id"]))
                    self._incident(str(spec["instance_id"]), "worker_lost", "recovered",
                                   {"action": "checkpoint_resume", "slot": checkpoint.get("slot")})
                    recovered += 1
                else:
                    self.control.update_match_lifecycle(
                        str(spec["match_id"]), "error",
                        metadata={"recovery_required": True,
                                  "recovery_reason": "worker_lost_without_managed_checkpoint"},
                    )
                    self._incident(str(spec["instance_id"]), "worker_lost", "operator_required",
                                   {"checkpoint_available": bool(checkpoint)})
                    operator_required += 1
            except Exception as exc:
                self._incident(str(spec["instance_id"]), "supervisor_error", "operator_required",
                               {"error": str(exc)[:1000]})
                operator_required += 1
        # Restore the native worker and MCP endpoint before restarting a model
        # that depends on them.
        harness_result = self.harness_manager.reconcile_once() \
            if self.harness_manager is not None else {"checked": 0, "restarted": 0}
        return {"ok": True, "checked": checked, "recovered": recovered,
                "operator_required": operator_required, "harness": harness_result}

    def status(self) -> dict[str, Any]:
        with self.store.transaction() as connection:
            open_incidents = int(connection.execute(
                "SELECT count(*) FROM supervision_incidents "
                "WHERE status IN ('open','operator_required')"
            ).fetchone()[0])
            active_schedules = int(connection.execute(
                "SELECT count(*) FROM operation_schedules WHERE status='active'"
            ).fetchone()[0])
        return {
            "ok": True,
            "running": bool(self._thread and self._thread.is_alive()),
            "active_schedules": active_schedules,
            "open_incidents": open_incidents,
        }

    def start(self, *, interval_seconds: float = 10.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        interval = min(max(float(interval_seconds), 2.0), 60.0)
        self._stop.clear()

        def loop() -> None:
            next_reconcile = 0.0
            while not self._stop.wait(0.5):
                now = time.monotonic()
                if now >= next_reconcile:
                    try:
                        self.reconcile_once()
                    except Exception:
                        pass
                    next_reconcile = now + interval
                try:
                    self.run_due_once()
                except Exception:
                    pass

        self._thread = threading.Thread(target=loop, name="smacx-operations", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def restore_backup_offline(control, data_root: Path | str, backup_id: str,
                           *, confirm_installation_id: str) -> dict[str, Any]:
    """Restore control state and secrets while the HTTP service is stopped.

    Worker-volume restoration remains a separate explicit operation because it
    requires every referenced worker to be parked and Docker ownership checks.
    """
    manager = OperationsManager(control, data_root=data_root)
    verified = manager.verify_backup(backup_id)
    if not verified["includes_secrets"]:
        raise StoreError("restore_requires_secret_complete_backup")
    if verified["installation_id"] != confirm_installation_id \
            or control.store.installation_id() != confirm_installation_id:
        raise ScopeViolation("restore_installation_confirmation_mismatch")
    source_root = manager._backup_path(backup_id)  # noqa: SLF001
    source_database = source_root / "state.sqlite3"
    emergency = manager.create_backup(include_secrets=True, include_workers=False)
    database_target = control.store.path
    descriptor, temporary = tempfile.mkstemp(prefix=".restore-", dir=database_target.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(source_database, temporary)
        os.chmod(temporary, 0o600)
        os.replace(temporary, database_target)
        # A stopped WAL-mode service may leave exact sidecars behind. They
        # belong to the database generation being replaced and must never be
        # replayed over the restored snapshot.
        for suffix in ("-wal", "-shm"):
            try:
                Path(str(database_target) + suffix).unlink()
            except FileNotFoundError:
                pass
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    restored_store = type(control.store)(database_target)
    restored_control = type(control)(restored_store, control.vault.root)
    secret_source = source_root / "secrets"
    if secret_source.is_dir():
        with restored_store.transaction() as connection:
            rows = connection.execute(
                "SELECT relative_path FROM secret_refs WHERE status='active'"
            ).fetchall()
        active_relative_paths = {str(row["relative_path"]) for row in rows}
        for existing in restored_control.vault.root.iterdir():
            if existing.is_file() and existing.name.endswith(".secret") \
                    and existing.name not in active_relative_paths:
                existing.unlink()
        for row in rows:
            relative = str(row["relative_path"])
            source = (secret_source / relative).resolve()
            target = (restored_control.vault.root / relative).resolve()
            if source.parent != secret_source.resolve() or target.parent != restored_control.vault.root:
                raise ScopeViolation("restore_secret_path_outside_vault")
            if not source.is_file():
                raise StoreError("restore_secret_missing")
            shutil.copyfile(source, target)
            os.chmod(target, 0o600)
    with restored_store.transaction() as connection:
        connection.execute(
            "UPDATE backup_sets SET status='restored', restored_unix=? WHERE backup_id=?",
            (time.time(), backup_id),
        )
    return {"ok": True, "backup_id": backup_id,
            "emergency_backup_id": emergency["backup_id"], "workers_restored": False}
