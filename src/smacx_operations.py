"""Durable scheduling, supervision, and backup services for Control Center.

The module keeps policy and history in the canonical SQLite store.  It never
guesses at native game state: automatic recovery is permitted only from a
checkpoint that the bridge successfully created and recorded for the match.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import tarfile
from typing import Any, Mapping
import uuid
import zipfile
from typing import TYPE_CHECKING

from smacx_docker import DockerNotFound
from smacx_journal import CampaignJournal
from smacx_store import InvalidRecord, ScopeViolation, StoreError
from smacx_worker_manager import WorkerManager, WorkerManagerError

if TYPE_CHECKING:
    from smacx_harness_manager import HarnessManager


IDENTITY = re.compile(r"^[A-Za-z0-9_-]{8,96}$")
BACKUP_DIRECTORY = re.compile(r"^backup-[a-f0-9]{32}$")
OPERATION_KINDS = {"backup", "checkpoint", "match_start", "match_resume"}
TARGET_KINDS = {"installation", "match"}
CAPABILITY_GAP_MAX_BYTES = 8 * 1024 * 1024
DIAGNOSTIC_BUNDLE_MAX_BYTES = 25 * 1024 * 1024
_SENSITIVE_KEY = re.compile(
    r"(?:api.?key|authorization|bearer|password|secret|cookie|csrf|session.?token|"
    r"provider.?url|base.?url|host.?address|join.?address|private.?path)", re.IGNORECASE,
)
_PRIVATE_CONVERSATION_KEY = re.compile(
    r"(?:chat|message|conversation|reasoning|thinking|prompt|response)", re.IGNORECASE,
)
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_IP_ADDRESS = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?![\w.])")
_HOST_PATH = re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\)[^\s\"']+")


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


def _validate_campaign_archive(path: Path) -> list[tarfile.TarInfo]:
    """Reject links, devices, traversal, and unrelated archive roots."""
    members: list[tarfile.TarInfo] = []
    total = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not name.parts \
                        or name.parts[0] != "campaigns" \
                        or not (member.isfile() or member.isdir()):
                    raise StoreError("backup_campaign_archive_unsafe")
                total += max(int(member.size), 0)
                if total > 20 * 1024 * 1024 * 1024:
                    raise StoreError("backup_campaign_archive_too_large")
                members.append(member)
    except (OSError, tarfile.TarError) as exc:
        raise StoreError("backup_campaign_archive_invalid") from exc
    return members


def _validate_recovery_archive(path: Path) -> list[tarfile.TarInfo]:
    """Validate the bounded checkpoint-memory archive used by offline backups."""
    members: list[tarfile.TarInfo] = []
    total = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not name.parts \
                        or name.parts[0] != "recovery-snapshots" \
                        or not (member.isfile() or member.isdir()):
                    raise StoreError("backup_recovery_archive_unsafe")
                total += max(int(member.size), 0)
                if total > 20 * 1024 * 1024 * 1024:
                    raise StoreError("backup_recovery_archive_too_large")
                members.append(member)
    except (OSError, tarfile.TarError) as exc:
        raise StoreError("backup_recovery_archive_invalid") from exc
    return members


def _validate_specialist_trace_archive(path: Path) -> list[tarfile.TarInfo]:
    """Validate retained specialist diagnostics without trusting archive paths."""
    members: list[tarfile.TarInfo] = []
    total = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not name.parts \
                        or name.parts[0] != "specialist-traces" \
                        or not (member.isfile() or member.isdir()):
                    raise StoreError("backup_specialist_trace_archive_unsafe")
                total += max(int(member.size), 0)
                if total > 100 * 1024 * 1024 * 1024:
                    raise StoreError("backup_specialist_trace_archive_too_large")
                members.append(member)
    except (OSError, tarfile.TarError) as exc:
        raise StoreError("backup_specialist_trace_archive_invalid") from exc
    return members


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
        self.diagnostic_root = (self.data_root / "diagnostics").resolve()
        if self.diagnostic_root.parent != self.data_root:
            raise StoreError("invalid_diagnostic_root")
        self.diagnostic_root.mkdir(parents=True, exist_ok=True, mode=0o750)
        os.chmod(self.diagnostic_root, 0o750)
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

    def _archive_recovery_snapshots(self, database_path: Path,
                                    target: Path) -> dict[str, Any]:
        """Archive only checkpoint files referenced by the captured database."""
        referenced: list[tuple[str, Path]] = []
        with sqlite3.connect(database_path) as connection:
            rows = connection.execute("SELECT metadata_json FROM matches").fetchall()
        for row in rows:
            try:
                metadata = json.loads(str(row[0]))
            except json.JSONDecodeError:
                continue
            checkpoint = metadata.get("recovery_checkpoint")
            memory = checkpoint.get("ai_memory") if isinstance(checkpoint, Mapping) else None
            snapshots = memory.get("hermes") if isinstance(memory, Mapping) else None
            if not isinstance(snapshots, list):
                continue
            for snapshot in snapshots:
                if not isinstance(snapshot, Mapping):
                    raise StoreError("backup_recovery_snapshot_manifest_invalid")
                relative = str(snapshot.get("archive") or "")
                if not re.fullmatch(
                        r"recovery-snapshots/match-[A-Za-z0-9_-]+/"
                        r"checkpoint-[A-Za-z0-9_-]+/hermes/[A-Za-z0-9_-]+\.tar\.gz",
                        relative):
                    raise StoreError("backup_recovery_snapshot_path_invalid")
                source = (self.data_root / relative).resolve()
                if self.data_root not in source.parents or not source.is_file() \
                        or _sha256(source) != snapshot.get("archive_sha256"):
                    raise StoreError("backup_recovery_snapshot_integrity_failure")
                referenced.append((relative, source))
        target.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(target, "w:gz", compresslevel=6) as archive:
            root = tarfile.TarInfo("recovery-snapshots")
            root.type = tarfile.DIRTYPE
            root.mode = 0o700
            archive.addfile(root)
            for relative, source in sorted(dict(referenced).items()):
                archive.add(source, arcname=relative, recursive=False)
        return {
            "path": target.name, "sha256": _sha256(target),
            "size_bytes": target.stat().st_size, "file_count": len(referenced),
        }

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
            recovery_archive = temporary_path / "recovery-snapshots.tar.gz"
            recovery_result = self._archive_recovery_snapshots(
                database_path, recovery_archive,
            )
            os.chmod(recovery_archive, 0o600)
            campaign_archive = temporary_path / "campaigns.tar.gz"
            campaign_result = CampaignJournal(
                self.data_root / "campaigns"
            ).archive_to(campaign_archive)
            os.chmod(campaign_archive, 0o600)
            specialist_trace_archive = temporary_path / "specialist-traces.tar.gz"
            specialist_trace_root = self.data_root / "specialist-traces"
            specialist_trace_root.mkdir(parents=True, exist_ok=True, mode=0o750)
            with tarfile.open(specialist_trace_archive, "w:gz") as archive:
                archive.add(specialist_trace_root, arcname="specialist-traces",
                            recursive=True)
            os.chmod(specialist_trace_archive, 0o600)
            specialist_trace_result = {
                "path": "specialist-traces.tar.gz",
                "sha256": _sha256(specialist_trace_archive),
                "size_bytes": specialist_trace_archive.stat().st_size,
            }
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
                "campaigns": {
                    "path": "campaigns.tar.gz",
                    "sha256": campaign_result["sha256"],
                    "size_bytes": campaign_result["size_bytes"],
                },
                "recovery_snapshots": recovery_result,
                "specialist_traces": specialist_trace_result,
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
        campaign_descriptor = manifest.get("campaigns")
        if isinstance(campaign_descriptor, Mapping):
            campaign_archive = path / str(campaign_descriptor.get("path") or "")
            if campaign_archive.parent != path.resolve() or not campaign_archive.is_file() \
                    or campaign_archive.name != "campaigns.tar.gz" \
                    or _sha256(campaign_archive) != campaign_descriptor.get("sha256"):
                raise StoreError("backup_campaign_archive_invalid")
            _validate_campaign_archive(campaign_archive)
        recovery_descriptor = manifest.get("recovery_snapshots")
        if isinstance(recovery_descriptor, Mapping):
            recovery_archive = path / str(recovery_descriptor.get("path") or "")
            if recovery_archive.parent != path.resolve() or not recovery_archive.is_file() \
                    or recovery_archive.name != "recovery-snapshots.tar.gz" \
                    or _sha256(recovery_archive) != recovery_descriptor.get("sha256"):
                raise StoreError("backup_recovery_archive_invalid")
            _validate_recovery_archive(recovery_archive)
        specialist_trace_descriptor = manifest.get("specialist_traces")
        if isinstance(specialist_trace_descriptor, Mapping):
            specialist_trace_archive = path / str(
                specialist_trace_descriptor.get("path") or "")
            if specialist_trace_archive.parent != path.resolve() \
                    or not specialist_trace_archive.is_file() \
                    or specialist_trace_archive.name != "specialist-traces.tar.gz" \
                    or _sha256(specialist_trace_archive) \
                    != specialist_trace_descriptor.get("sha256"):
                raise StoreError("backup_specialist_trace_archive_invalid")
            _validate_specialist_trace_archive(specialist_trace_archive)
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
            "campaigns_included": isinstance(campaign_descriptor, Mapping),
            "recovery_snapshots_included": isinstance(recovery_descriptor, Mapping),
            "specialist_traces_included": isinstance(specialist_trace_descriptor, Mapping),
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

    @staticmethod
    def _redact_text(value: str) -> str:
        text = _URL.sub("<redacted-url>", value)
        text = _IP_ADDRESS.sub("<redacted-ip>", text)
        text = _HOST_PATH.sub("<redacted-path>", text)
        text = re.sub(
            r"(?i)(api.?key|authorization|bearer|password|secret|token)"
            r"\s*[:=]\s*[^\s,;]+", r"\1=<redacted>", text,
        )
        return text[:200_000]

    @classmethod
    def _redact_payload(cls, value: Any, *, key: str = "") -> Any:
        if _SENSITIVE_KEY.search(key):
            return "<redacted>"
        if _PRIVATE_CONVERSATION_KEY.search(key):
            return "<omitted-private-conversation>"
        if isinstance(value, Mapping):
            return {
                str(child_key)[:120]: cls._redact_payload(child, key=str(child_key))
                for child_key, child in list(value.items())[:500]
            }
        if isinstance(value, list):
            return [cls._redact_payload(item, key=key) for item in value[:500]]
        if isinstance(value, str):
            return cls._redact_text(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return cls._redact_text(str(value))

    def _capability_gap_records(self) -> list[dict[str, Any]]:
        path = self.data_root / "capability-gaps.jsonl"
        if not path.is_file():
            return []
        size = path.stat().st_size
        if size > CAPABILITY_GAP_MAX_BYTES:
            with path.open("rb") as stream:
                stream.seek(-CAPABILITY_GAP_MAX_BYTES, os.SEEK_END)
                stream.readline()
                content = stream.read().decode("utf-8", errors="replace")
        else:
            content = path.read_text(encoding="utf-8", errors="replace")
        records: list[dict[str, Any]] = []
        for line in content.splitlines()[-200:]:
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and re.fullmatch(
                    r"gap-[a-f0-9]{32}", str(candidate.get("gap_id", ""))):
                records.append(candidate)
        return records

    def _instance_for_gap(self, report: Mapping[str, Any]) -> str | None:
        match_id = str(report.get("match_id") or "")
        session_id = str(report.get("session_id") or "")
        if not IDENTITY.fullmatch(match_id):
            return None
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT instance_id FROM sessions WHERE session_id=? AND match_id=? "
                "ORDER BY started_unix DESC LIMIT 1", (session_id, match_id),
            ).fetchone() if IDENTITY.fullmatch(session_id) else None
            if row is None:
                row = connection.execute(
                    "SELECT instance_id FROM instances WHERE match_id=? "
                    "ORDER BY created_unix DESC LIMIT 1", (match_id,),
                ).fetchone()
        return str(row["instance_id"]) if row else None

    def _existing_gap_incident(self, gap_id: str) -> dict[str, Any] | None:
        kind = f"capability_gap:{gap_id}"
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT incident_id FROM supervision_incidents WHERE incident_kind=? "
                "ORDER BY first_seen_unix DESC LIMIT 1", (kind,),
            ).fetchone()
        return self.control.get_supervision_incident(str(row[0])) if row else None

    def _stop_gap_harness(self, instance_id: str, gap_id: str) -> list[str]:
        stopped: list[str] = []
        for run in self.control.list_harness_runs():
            if str(run.get("instance_id")) != instance_id or run.get("status") not in {
                    "queued", "starting", "running", "restarting"}:
                continue
            run_id = str(run["run_id"])
            self.control.update_harness_run(
                run_id, desired_status="stopped", last_error=f"capability_gap:{gap_id}",
                metadata_update={"capability_gap_id": gap_id, "operator_attention_required": True},
            )
            if self.harness_manager is not None:
                try:
                    self.harness_manager.stop_run(run_id)
                except Exception:
                    self.control.update_harness_run(
                        run_id, status="error", desired_status="stopped",
                        last_error=f"capability_gap:{gap_id}",
                    )
            stopped.append(run_id)
        return stopped

    def _container_log(self, container_name: str | None, *, tail: int = 300) -> str:
        if not container_name or self.worker_manager is None:
            return "No managed container log was available.\n"
        try:
            resource = self.worker_manager.docker.inspect_container(container_name)
            labels = resource.get("Config", {}).get("Labels", {})
            if labels.get("io.smacx.managed") != "true" or \
                    labels.get("io.smacx.installation") != self.worker_manager.installation_id:
                return "Container ownership could not be verified; log omitted.\n"
            return self._redact_text(
                self.worker_manager.docker.container_logs(container_name, tail=tail)
            ) + "\n"
        except Exception as exc:
            return f"Log unavailable: {self._redact_text(str(exc))}\n"

    def _collect_recent_saves(self, instance_id: str, destination: Path) -> list[dict[str, Any]]:
        manager = self.worker_manager
        if manager is None or not manager.control_data_volume:
            return []
        spec = self.control.get_worker_spec(instance_id)
        volume = manager.docker.inspect_volume(str(spec["data_volume"]))
        manager.docker.require_owned(
            volume, manager.installation_id, purpose="worker-data",
        )
        relative = destination.relative_to(self.data_root).as_posix()
        helper_name = manager._name("diagnostic", f"{instance_id}-{uuid.uuid4().hex}")  # noqa: SLF001
        script = """
import hashlib,json,pathlib,shutil
source=pathlib.Path('/source')
target=pathlib.Path('/control')/TARGET
target.mkdir(parents=True,exist_ok=True)
candidates=[]
for path in source.rglob('*'):
    if not path.is_file(): continue
    lower=path.name.lower()
    if lower.endswith('.sav') or lower.endswith('.sav.zst'):
        try: candidates.append((path.stat().st_mtime,path))
        except OSError: pass
seen=set(); copied=[]; total=0
for _,path in sorted(candidates,reverse=True):
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if digest in seen: continue
    size=path.stat().st_size
    if size > 8*1024*1024 or total+size > 20*1024*1024: continue
    seen.add(digest); total+=size
    safe='save-%d-%s%s' % (len(copied)+1,digest[:12],''.join(path.suffixes).lower())
    shutil.copyfile(path,target/safe)
    copied.append({'path':'saves/'+safe,'sha256':digest,'size_bytes':size})
    if len(copied)>=3: break
print(json.dumps({'ok':True,'saves':copied},separators=(',',':')))
""".replace("TARGET", repr(relative))
        identifier = manager.docker.create_container(helper_name, {
            "Image": manager.mcp_image, "Entrypoint": ["python3"], "Cmd": ["-c", script],
            "User": "10001:10001", "Labels": manager._labels(  # noqa: SLF001
                "diagnostic-helper", **{"io.smacx.instance": instance_id},
            ),
            "HostConfig": {
                "NetworkMode": "none", "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"], "Mounts": [
                    {"Type": "volume", "Source": spec["data_volume"],
                     "Target": "/source", "ReadOnly": True},
                    {"Type": "volume", "Source": manager.control_data_volume,
                     "Target": "/control"},
                ],
            },
        })
        try:
            manager.docker.start_container(identifier)
            stopped = manager.docker.wait_container(identifier, timeout=120.0)
            logs = manager.docker.container_logs(identifier, tail=20).strip()
            if int(stopped.get("State", {}).get("ExitCode", -1)) != 0:
                return []
            line = "{}"
            for item in reversed(logs.splitlines()):
                opening = item.find("{")
                if opening >= 0:
                    line = item[opening:]
                    break
            result = json.loads(line)
            return result.get("saves", []) if isinstance(result.get("saves"), list) else []
        except Exception:
            return []
        finally:
            try:
                manager._cleanup_container(identifier, "diagnostic-helper")  # noqa: SLF001
            except Exception:
                pass

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                        encoding="utf-8")
        os.chmod(path, 0o600)

    def _create_gap_bundle(
        self, incident: Mapping[str, Any], report: Mapping[str, Any],
        instance_id: str, stopped_runs: list[str],
    ) -> dict[str, Any]:
        incident_id = str(incident["incident_id"])
        staging = self.diagnostic_root / f".{incident_id}.staging"
        bundle = self.diagnostic_root / f"smacx-gap-{str(report['gap_id'])[4:]}.zip"
        if bundle.is_file():
            return {"relative_path": bundle.relative_to(self.data_root).as_posix(),
                    "file_name": bundle.name, "size_bytes": bundle.stat().st_size,
                    "sha256": _sha256(bundle)}
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(mode=0o700)
        spec = self.control.get_worker_spec(instance_id)
        match = self.control.get_match(str(spec["match_id"]))
        seats = self.control.list_seats(str(spec["match_id"]))
        public_seats = [{
            "seat_index": seat.get("seat_index"), "controller_kind": seat.get("controller_kind"),
            "faction_id": seat.get("faction_id"), "faction_name": seat.get("faction_name"),
            "instance_id": seat.get("instance_id"),
            "player": f"Player {int(seat.get('seat_index') or 0) + 1}",
        } for seat in seats]
        safe_report = self._redact_payload(report)
        environment = {
            "schema": "smacx.diagnostic.environment.v1",
            "installation_id": self.store.installation_id(),
            "project_revision": os.environ.get("SMACX_BUILD_REVISION", "unavailable"),
            "worker_image": spec.get("image_ref"), "mcp_image": getattr(self.worker_manager, "mcp_image", None),
            "game_source_id": spec.get("game_source_id"), "runtime_id": spec.get("runtime_id"),
            "worker_observed_status": spec.get("observed_status"),
            "worker_container_state": "preserved_for_operator_diagnosis",
        }
        configuration = {
            "schema": "smacx.diagnostic.match.v1", "match_id": match.get("match_id"),
            "display_name": match.get("display_name"), "mode": match.get("mode"),
            "status": match.get("status"), "ruleset_id": match.get("ruleset_id"),
            "last_turn": match.get("last_turn"), "last_year": match.get("last_year"),
            "settings": self._redact_payload(match.get("metadata", {}).get("game_settings", {})),
            "scenario_id": match.get("metadata", {}).get("scenario_id"),
        }
        self._write_json(staging / "incident" / "capability-gap.json", safe_report)
        self._write_json(staging / "incident" / "environment.json", environment)
        self._write_json(staging / "incident" / "match-configuration.json", configuration)
        self._write_json(staging / "incident" / "seat-map.json", {"seats": public_seats})
        trace = {"event": "capability_gap", "gap": safe_report,
                 "stopped_harness_runs": stopped_runs}
        trace_path = staging / "traces" / "semantic-trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, separators=(",", ":")) + "\n",
                              encoding="utf-8")
        os.chmod(trace_path, 0o600)
        network = spec.get("network") if isinstance(spec.get("network"), Mapping) else {}
        (staging / "traces" / "bridge.log").write_text(
            self._container_log(str(spec.get("container_name") or "")), encoding="utf-8")
        (staging / "traces" / "mcp.log").write_text(
            self._container_log(str(network.get("mcp_container_name") or "")), encoding="utf-8")
        harness_log = "Full model conversations and reasoning are intentionally excluded.\n"
        for run in self.control.list_harness_runs():
            if str(run.get("run_id")) in stopped_runs:
                harness_log += self._container_log(str(run.get("container_name") or ""), tail=200)
        (staging / "traces" / "harness.log").write_text(harness_log, encoding="utf-8")
        (staging / "traces" / "supervisor.log").write_text(
            f"incident_id={incident_id}\ngap_id={report['gap_id']}\n"
            f"harness_runs_stopped={','.join(stopped_runs) or 'none'}\n",
            encoding="utf-8")
        for log_path in (staging / "traces").glob("*.log"):
            os.chmod(log_path, 0o600)
        saves = self._collect_recent_saves(instance_id, staging / "saves")
        readme = f"""# SMACX capability-gap diagnostic

Gap ID: `{report['gap_id']}`

Incident ID: `{incident_id}`

Match: `{match.get('match_id')}`

Turn / year: `{report.get('turn')}` / `{match.get('last_year')}`

The AI reached a native game state that the semantic bridge could not safely observe or act on.
Its harness was stopped before automatic continuation, while the native worker was preserved for diagnosis.

Attach this ZIP to a new issue at https://github.com/magiccodingman/smacx-agent/issues.
Describe what the human players saw immediately before the alert. The bundle intentionally excludes
game binaries/assets, credentials, private provider addresses, account data, chat, and full model reasoning.

`saves/` contains up to three newest distinct managed saves when they were safely available. Compressed
`.sav.zst` files can be decompressed with `zstd -d` before reproduction.
"""
        (staging / "README.md").write_text(readme, encoding="utf-8")
        os.chmod(staging / "README.md", 0o600)
        files = []
        for path in sorted(item for item in staging.rglob("*") if item.is_file()):
            files.append({"path": path.relative_to(staging).as_posix(), "sha256": _sha256(path),
                          "size_bytes": path.stat().st_size})
        manifest = {
            "schema": "smacx.capability-gap-bundle.v1", "incident_id": incident_id,
            "gap_id": report["gap_id"], "created_unix": time.time(),
            "privacy": {"game_binaries_included": False, "credentials_included": False,
                        "private_chat_included": False, "full_model_history_included": False},
            "saves": saves, "files": files,
        }
        self._write_json(staging / "manifest.json", manifest)
        temporary_bundle = bundle.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary_bundle, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for path in sorted(item for item in staging.rglob("*") if item.is_file()):
                archive.write(path, path.relative_to(staging).as_posix())
        if temporary_bundle.stat().st_size > DIAGNOSTIC_BUNDLE_MAX_BYTES:
            temporary_bundle.unlink(missing_ok=True)
            raise StoreError("diagnostic_bundle_too_large")
        os.chmod(temporary_bundle, 0o600)
        os.replace(temporary_bundle, bundle)
        shutil.rmtree(staging)
        return {"relative_path": bundle.relative_to(self.data_root).as_posix(),
                "file_name": bundle.name, "size_bytes": bundle.stat().st_size,
                "sha256": _sha256(bundle)}

    def ingest_capability_gaps_once(self) -> dict[str, Any]:
        ingested = ignored = errors = 0
        for report in self._capability_gap_records():
            gap_id = str(report["gap_id"])
            existing = self._existing_gap_incident(gap_id)
            if existing is not None and isinstance(existing.get("details"), Mapping) \
                    and isinstance(existing["details"].get("diagnostic_bundle"), Mapping):
                ignored += 1
                continue
            instance_id = self._instance_for_gap(report)
            if instance_id is None:
                ignored += 1
                continue
            try:
                stopped_runs = self._stop_gap_harness(instance_id, gap_id)
                detail = {
                    "schema": "smacx.capability-gap-incident.v1", "gap_id": gap_id,
                    "summary": "AI play stopped at a semantic capability gap.",
                    "screen_or_state": self._redact_text(str(report.get("screen_or_state") or "Unknown state")),
                    "intended_decision": self._redact_text(str(report.get("intended_decision") or "")),
                    "required_observation": self._redact_text(str(report.get("required_observation") or "")),
                    "required_action": self._redact_text(str(report.get("required_action") or "")),
                    "why_blocked": self._redact_text(str(report.get("why_blocked") or "")),
                    "turn": report.get("turn"), "revision": report.get("revision"),
                    "reported_at_unix": report.get("reported_at_unix"),
                    "harness_runs_stopped": stopped_runs,
                    "native_worker_preserved": True,
                }
                if existing is not None and isinstance(existing.get("details"), Mapping):
                    # A harness-detected bridge outage is published immediately
                    # so the UI cannot stay silent. Enrich that same incident
                    # asynchronously with the normal redacted diagnostic ZIP.
                    detail = {**dict(existing["details"]), **detail}
                    incident = existing
                    existing_run = str(existing["details"].get("run_id") or "")
                    if existing_run and existing_run not in stopped_runs:
                        stopped_runs.append(existing_run)
                    detail["harness_runs_stopped"] = stopped_runs
                else:
                    incident = self._incident(
                        instance_id, f"capability_gap:{gap_id}", "operator_required", detail,
                    )
                bundle = self._create_gap_bundle(incident, report, instance_id, stopped_runs)
                detail["diagnostic_bundle"] = bundle
                self._incident(
                    instance_id, f"capability_gap:{gap_id}", "operator_required", detail,
                )
                ingested += 1
            except Exception as exc:
                errors += 1
                self._incident(instance_id, f"capability_gap:{gap_id}", "operator_required", {
                    "schema": "smacx.capability-gap-incident.v1", "gap_id": gap_id,
                    "summary": "AI play stopped at a semantic capability gap.",
                    "bundle_error": self._redact_text(str(exc)), "native_worker_preserved": True,
                })
        return {"checked": len(self._capability_gap_records()), "ingested": ingested,
                "ignored": ignored, "errors": errors}

    def reconcile_once(self) -> dict[str, Any]:
        with self._operation_lock:
            if self.worker_manager is not None:
                # Observe and decide under the same lifecycle guard as the
                # mutation. Otherwise a transient missing LAN peer can queue
                # a recovery that destroys a newly restarted healthy host.
                with self.worker_manager._lifecycle_lock:
                    return self._reconcile_once()
            return self._reconcile_once()

    def _reconcile_once(self) -> dict[str, Any]:
        gap_result = self.ingest_capability_gaps_once()
        if self.worker_manager is None:
            harness_result = self.harness_manager.reconcile_once() \
                if self.harness_manager is not None else {"checked": 0, "restarted": 0}
            return {"ok": True, "checked": 0, "recovered": 0, "operator_required": 0,
                    "capability_gaps": gap_result, "harness": harness_result}
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
                match = self.control.get_match(str(spec["match_id"]))
                metadata = match.get("metadata", {})
                if metadata.get("incident_quarantine", {}).get("native_and_collectors_frozen"):
                    # A preserved incident is an operator latch, not another
                    # lost-worker sample or a reason to restart its sidecar.
                    continue
                observed = self.worker_manager.worker_status(str(spec["instance_id"]))
                containment_pending = match.get("status") == "error" and metadata.get(
                    "recovery_reason") == "worker_lost_without_managed_checkpoint"
                if observed.get("running") and observed.get("health") == "healthy" \
                        and not containment_pending:
                    mcp = observed.get("mcp") if isinstance(observed.get("mcp"), Mapping) else {}
                    if self.worker_manager.control_data_volume and (
                            not mcp.get("running") or mcp.get("health") != "healthy"):
                        self.worker_manager.start_mcp_sidecar(str(spec["instance_id"]))
                        self._incident(str(spec["instance_id"]), "mcp_sidecar_lost", "recovered",
                                       {"action": "sidecar_restarted"})
                        recovered += 1
                    continue
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
                    quarantine = self.worker_manager.quarantine_match(str(spec["match_id"]))
                    self.control.update_match_lifecycle(
                        str(spec["match_id"]), "error", metadata={"incident_quarantine": quarantine},
                    )
                    self._incident(str(spec["instance_id"]), "worker_lost", "operator_required",
                                   {"checkpoint_available": False,
                                    "worker_was_running": bool(observed.get("running")),
                                    "worker_health": observed.get("health"),
                                    "quarantine": quarantine,
                                    "summary": "Autonomous play stopped; the game runtime is frozen for diagnosis.",
                                    "screen_or_state": "Game runtime frozen after bridge loss" if observed.get("running")
                                        else "Game worker unavailable",
                                    "why_blocked": "The bridge became unavailable before a complete, verified native and AI-memory checkpoint was published. Native execution and observation collectors are frozen; restoring an incomplete checkpoint would mismatch game state and AI memory."})
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
                "operator_required": operator_required, "capability_gaps": gap_result,
                "harness": harness_result}

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
    campaign_archive = source_root / "campaigns.tar.gz"
    campaigns_restored = False
    if campaign_archive.is_file():
        members = _validate_campaign_archive(campaign_archive)
        root = Path(data_root).expanduser().resolve()
        staging = Path(tempfile.mkdtemp(prefix=".campaign-restore-", dir=root))
        previous = root / f".campaigns-previous-{uuid.uuid4().hex}"
        target = root / "campaigns"
        moved_previous = False
        try:
            with tarfile.open(campaign_archive, "r:gz") as archive:
                archive.extractall(staging, members=members)
            candidate = staging / "campaigns"
            if not candidate.is_dir():
                raise StoreError("backup_campaign_archive_missing_root")
            if target.exists():
                os.replace(target, previous)
                moved_previous = True
            os.replace(candidate, target)
            campaigns_restored = True
            if moved_previous:
                shutil.rmtree(previous)
        except Exception:
            if moved_previous and not target.exists() and previous.exists():
                os.replace(previous, target)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    recovery_archive = source_root / "recovery-snapshots.tar.gz"
    recovery_snapshots_restored = False
    if recovery_archive.is_file():
        members = _validate_recovery_archive(recovery_archive)
        root = Path(data_root).expanduser().resolve()
        staging = Path(tempfile.mkdtemp(prefix=".recovery-snapshot-restore-", dir=root))
        previous = root / f".recovery-snapshots-previous-{uuid.uuid4().hex}"
        target = root / "recovery-snapshots"
        moved_previous = False
        try:
            with tarfile.open(recovery_archive, "r:gz") as archive:
                archive.extractall(staging, members=members)
            candidate = staging / "recovery-snapshots"
            if not candidate.is_dir():
                raise StoreError("backup_recovery_archive_missing_root")
            if target.exists():
                os.replace(target, previous)
                moved_previous = True
            os.replace(candidate, target)
            recovery_snapshots_restored = True
            if moved_previous:
                shutil.rmtree(previous)
        except Exception:
            if moved_previous and not target.exists() and previous.exists():
                os.replace(previous, target)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    specialist_trace_archive = source_root / "specialist-traces.tar.gz"
    specialist_traces_restored = False
    if specialist_trace_archive.is_file():
        members = _validate_specialist_trace_archive(specialist_trace_archive)
        root = Path(data_root).expanduser().resolve()
        staging = Path(tempfile.mkdtemp(prefix=".specialist-trace-restore-", dir=root))
        previous = root / f".specialist-traces-previous-{uuid.uuid4().hex}"
        target = root / "specialist-traces"
        moved_previous = False
        try:
            with tarfile.open(specialist_trace_archive, "r:gz") as archive:
                archive.extractall(staging, members=members)
            candidate = staging / "specialist-traces"
            if not candidate.is_dir():
                raise StoreError("backup_specialist_trace_archive_missing_root")
            if target.exists():
                os.replace(target, previous)
                moved_previous = True
            os.replace(candidate, target)
            specialist_traces_restored = True
            if moved_previous:
                shutil.rmtree(previous)
        except Exception:
            if moved_previous and not target.exists() and previous.exists():
                os.replace(previous, target)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    with restored_store.transaction() as connection:
        connection.execute(
            "UPDATE backup_sets SET status='restored', restored_unix=? WHERE backup_id=?",
            (time.time(), backup_id),
        )
    return {"ok": True, "backup_id": backup_id,
            "emergency_backup_id": emergency["backup_id"], "workers_restored": False,
            "campaigns_restored": campaigns_restored,
            "recovery_snapshots_restored": recovery_snapshots_restored,
            "specialist_traces_restored": specialist_traces_restored}
