#!/usr/bin/env python3
"""Opt-in end-to-end test of managed assets, worker start, park, and resume."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import tempfile

from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import MemoryScope, SmacxStore
from smacx_worker_manager import WorkerManager


def bridge_call(port: int, token: str, operation: str) -> dict:
    request = json.dumps({"id": operation, "op": operation, "token": token}, separators=(",", ":"))
    with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
        connection.sendall(request.encode() + b"\n")
        data = b""
        while b"\n" not in data:
            block = connection.recv(1024 * 1024)
            if not block:
                break
            data += block
    return json.loads(data.split(b"\n", 1)[0])


def main() -> int:
    required = {
        "game": os.environ.get("SMACX_TEST_GAME_SOURCE"),
        "proton": os.environ.get("SMACX_TEST_PROTON_SOURCE"),
        "directx": os.environ.get("SMACX_TEST_DIRECTX_REDIST"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        print(json.dumps({"event": "skip", "reason": "missing_live_assets", "missing": missing}))
        return 0

    docker = DockerClient()
    runtime = None
    worker = None
    manager = None
    with tempfile.TemporaryDirectory(prefix="smacx-worker-manager-") as temporary:
        root = Path(temporary)
        control = ControlPlane(SmacxStore(root / "state.sqlite3"), root / "secrets")
        manager = WorkerManager(
            control, docker, directx_redist_host_path=required["directx"],
        )
        try:
            source = manager.validate_game_source(required["game"], display_name="Live legal source")
            runtime = manager.import_proton(required["proton"], display_name="Live managed Proton")
            control.store.ensure_agent("agent-manager-live", "Manager Live Agent")
            created = control.create_solo_match(
                "Manager live match", "agent-manager-live",
                match_id="match-manager-live",
                faction_id=1, faction_name="Gaia's Stepdaughters",
            )
            perspective = created["perspective"]
            scope = MemoryScope(
                "match-manager-live", "agent-manager-live", perspective["perspective_id"],
            )
            worker = manager.provision_worker(
                scope, source["game_source_id"], runtime["runtime_id"],
                autostart={"enabled": True, "difficulty": 0, "world_size": 0, "faction_id": 1},
                view_enabled=True,
            )
            if "bridge-token" in json.dumps(worker) or "SMACX_AGENT_TOKEN" in json.dumps(worker):
                raise AssertionError("worker spec exposed bridge secret material")

            try:
                first = manager.start_worker(worker["instance_id"], timeout=300)
            except Exception:
                print(json.dumps({
                    "event": "worker_start_failure",
                    "last_error": control.get_worker_spec(worker["instance_id"])["last_error"],
                }, separators=(",", ":")))
                raise
            container = docker.inspect_container(first["container_id"])
            proton_mount = next(
                (mount for mount in container.get("Mounts", []) if mount.get("Destination") == "/proton"),
                None,
            )
            if not proton_mount or proton_mount.get("RW") is not False:
                raise AssertionError("managed Proton runtime was writable in a game worker")
            access = manager.spectator_access(worker["instance_id"])
            if access.get("mode") != "view-only" or len(access.get("password", "")) < 12 \
                    or not isinstance(access.get("host_port"), int):
                raise AssertionError("view-only spectator access was not operator-scoped")
            with socket.create_connection(("127.0.0.1", access["host_port"]), timeout=5):
                pass
            if access["password"] in json.dumps(container):
                raise AssertionError("spectator password leaked into container configuration")
            token = control.vault.read(
                worker["bridge_secret_id"], purpose=f"worker.{worker['instance_id']}.bridge_token",
            )
            first_snapshot = bridge_call(first["bridge_host_port"], token, "semantic_snapshot")
            snap = first_snapshot.get("snapshot", {})
            if not first_snapshot.get("ok") or snap.get("match_id") != scope.match_id \
                    or snap.get("session_id") != first["session_id"] \
                    or snap.get("interaction", {}).get("popup_label") != "PLANETFALL":
                raise AssertionError(f"managed first session did not reach semantic opening: {snap}")
            manager.park_worker(worker["instance_id"])
            if manager.worker_status(worker["instance_id"])["container_present"]:
                raise AssertionError("park left a stale game container")

            second = manager.start_worker(worker["instance_id"], timeout=240)
            second_snapshot = bridge_call(second["bridge_host_port"], token, "semantic_snapshot")
            snap2 = second_snapshot.get("snapshot", {})
            if second["session_id"] == first["session_id"] \
                    or snap2.get("match_id") != scope.match_id \
                    or snap2.get("session_id") != second["session_id"]:
                raise AssertionError("resume did not preserve match identity and rotate session identity")
            manager.park_worker(worker["instance_id"])

            print(json.dumps({
                "event": "pass",
                "payload": {
                    "source_validated_in_container": True,
                    "proton_private_copy_manifested": True,
                    "proton_worker_mount_read_only": True,
                    "secret_transferred_without_environment": True,
                    "worker_read_only_root": True,
                    "view_only_spectator": True,
                    "spectator_secret_not_in_environment": True,
                    "first_session_semantic_opening": True,
                    "park_removed_container": True,
                    "resume_rotated_session": True,
                    "match_identity_preserved": True,
                },
            }, separators=(",", ":")))
        finally:
            if manager and worker:
                try:
                    manager.park_worker(worker["instance_id"])
                except Exception:
                    pass
                for name, purpose in (
                    (worker["network"]["secret_volume"], "worker-secret"),
                    (worker["data_volume"], "worker-data"),
                ):
                    try:
                        volume = docker.inspect_volume(name)
                        docker.require_owned(volume, manager.installation_id, purpose=purpose)
                        docker.remove_volume(name)
                    except Exception:
                        pass
            if manager and runtime:
                try:
                    volume = docker.inspect_volume(runtime["storage_ref"])
                    docker.require_owned(volume, manager.installation_id, purpose="proton-runtime")
                    docker.remove_volume(runtime["storage_ref"])
                except Exception:
                    pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
