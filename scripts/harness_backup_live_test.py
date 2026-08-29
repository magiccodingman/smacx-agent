#!/usr/bin/env python3
"""Opt-in Docker regression for private Hermes conversation-volume backups."""

from __future__ import annotations

import json
from io import BytesIO
import os
from pathlib import Path
import tarfile
import time
import uuid

from smacx_control import ControlPlane
from smacx_docker import DockerClient, DockerNotFound
from smacx_operations import OperationsManager
from smacx_store import SmacxStore


class MinimalWorkerManager:
    def __init__(self, control, docker: DockerClient, control_volume: str) -> None:
        self.control = control
        self.docker = docker
        self.control_data_volume = control_volume
        self.installation_id = control.store.installation_id()
        self.mcp_image = "smacx-agent-control:dev"
        self.resource_prefix = "smacx-harness-backup-test"

    def _name(self, kind: str, identity: str) -> str:
        import hashlib
        return f"{self.resource_prefix}-{kind}-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"

    def _labels(self, purpose: str, **extra: str) -> dict[str, str]:
        return self.docker.labels(self.installation_id, purpose, **extra)

    def _cleanup_container(self, identifier: str, purpose: str) -> None:
        container = self.docker.inspect_container(identifier)
        self.docker.require_owned(container, self.installation_id, purpose=purpose)
        self.docker.remove_container(identifier)


def main() -> int:
    control_volume = os.environ.get("SMACX_TEST_CONTROL_VOLUME", "")
    if not control_volume:
        print(json.dumps({"event": "skip", "reason": "missing_control_volume"}))
        return 0
    root = Path("/var/lib/smacx")
    store = SmacxStore(root / "state.sqlite3")
    control = ControlPlane(store, root / "secrets")
    docker = DockerClient()
    agent = control.create_agent("Harness backup player")
    provider = control.configure_provider(
        "Contract provider", "http://models.invalid/v1", default_model_id="contract-model",
    )
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO provider_models(provider_id, model_id, display_name, context_length, "
            "discovered_unix) VALUES (?, 'contract-model', 'Contract model', 65536, ?)",
            (provider["provider_id"], time.time()),
        )
    profile = control.configure_harness_profile(
        agent["agent_id"], provider["provider_id"], display_name="Backup profile",
        external_profile_id="smacx-harness-backup", workspace_path="/opt/data/workspace",
    )
    suffix = uuid.uuid4().hex[:16]
    volume = f"smacx-harness-backup-source-{suffix}"
    container = f"smacx-harness-backup-seed-{suffix}"
    labels = docker.labels(
        store.installation_id(), "harness-data",
        **{"io.smacx.harness": profile["harness_profile_id"]},
    )
    docker.create_volume(volume, labels)
    try:
        identifier = docker.create_container(container, {
            "Image": "smacx-agent-control:dev", "Entrypoint": ["/bin/true"],
            "User": "0:0", "Labels": docker.labels(store.installation_id(), "test-seed"),
            "HostConfig": {"NetworkMode": "none", "CapDrop": ["ALL"],
                           "Mounts": [{"Type": "volume", "Source": volume,
                                       "Target": "/source"}]},
        })
        payload = BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as archive:
            for name in ("profiles", "profiles/private", "profiles/private/sessions"):
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                info.mode = 0o700
                info.uid = 10000
                info.gid = 10000
                archive.addfile(info)
            value = b"durable-session"
            info = tarfile.TarInfo("profiles/private/sessions/conversation.json")
            info.size = len(value)
            info.mode = 0o600
            info.uid = 10000
            info.gid = 10000
            archive.addfile(info, BytesIO(value))
        docker.put_archive(identifier, "/source", payload.getvalue())
        docker.start_container(identifier)
        seeded = docker.wait_container(identifier)
        if int(seeded.get("State", {}).get("ExitCode", -1)) != 0:
            raise AssertionError(docker.container_logs(identifier))
        docker.remove_container(identifier)
        control.put_harness_runtime_spec(
            profile["harness_profile_id"], image_ref="official-hermes@sha256:" + "a" * 64,
            data_volume=volume, secret_volume=f"unused-secret-{suffix}",
            container_name=f"unused-harness-{suffix}", metadata={},
        )
        manager = MinimalWorkerManager(control, docker, control_volume)
        operations = OperationsManager(
            control, data_root=root, worker_manager=manager, harness_manager=object(),
        )
        backup = operations.create_backup(include_secrets=True, include_workers=True)
        verified = operations.verify_backup(backup["backup_id"])
        if verified.get("harness_count") != 1 or verified.get("worker_count") != 0:
            raise AssertionError(f"private harness archive not verified: {verified}")
        print(json.dumps({
            "event": "pass", "payload": {
                "private_uid_10000_profile_archived": True,
                "archive_integrity_verified": True,
                "conversation_volume_count": verified["harness_count"],
            },
        }, separators=(",", ":")))
    finally:
        try:
            docker.remove_container(container)
        except DockerNotFound:
            pass
        try:
            owned = docker.inspect_volume(volume)
            docker.require_owned(owned, store.installation_id(), purpose="harness-data")
            docker.remove_volume(volume)
        except DockerNotFound:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
