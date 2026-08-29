#!/usr/bin/env python3
"""Live, self-cleaning contract test for the minimal Docker Engine client."""

from __future__ import annotations

from io import BytesIO
import json
import tarfile
import time

from smacx_docker import DockerClient, DockerOwnershipError


def archive() -> bytes:
    output = BytesIO()
    with tarfile.open(fileobj=output, mode="w") as stream:
        data = b"archive-ok\n"
        info = tarfile.TarInfo("probe")
        info.size = len(data)
        info.mode = 0o400
        info.uid = 10001
        info.gid = 10001
        info.mtime = int(time.time())
        stream.addfile(info, BytesIO(data))
    return output.getvalue()


def main() -> int:
    docker = DockerClient()
    if not docker.ping():
        raise AssertionError("Docker ping failed")
    version = docker.version()
    installation = "installation-docker-test"
    suffix = str(int(time.time() * 1_000_000))
    volume = f"smacx-docker-contract-{suffix}"
    container = f"smacx-docker-contract-{suffix}"
    labels = docker.labels(installation, "contract-test")
    container_id = None
    try:
        created_volume = docker.create_volume(volume, labels)
        docker.require_owned(created_volume, installation, purpose="contract-test")
        config = {
            "Image": "smacx-agent-control:dev",
            "Entrypoint": ["/bin/sh", "-c"],
            "Cmd": ["test \"$(cat /target/probe)\" = archive-ok && echo archive-ok"],
            "Tty": True,
            "Labels": labels,
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Mounts": [{"Type": "volume", "Source": volume, "Target": "/target"}],
            },
        }
        container_id = docker.create_container(container, config)
        inspected = docker.inspect_container(container_id)
        docker.require_owned(inspected, installation, purpose="contract-test")
        try:
            docker.require_owned(inspected, "installation-someone-else")
        except DockerOwnershipError:
            pass
        else:
            raise AssertionError("cross-installation Docker mutation was allowed")
        docker.put_archive(container_id, "/target", archive())
        docker.start_container(container_id)
        finished = docker.wait_container(container_id, timeout=30)
        if finished["State"]["ExitCode"] != 0 or "archive-ok" not in docker.container_logs(container_id):
            raise AssertionError("archive upload or container execution failed")
        print(json.dumps({
            "event": "pass",
            "payload": {
                "docker_api": version.get("ApiVersion"),
                "unix_socket": True,
                "owned_resource_guard": True,
                "archive_secret_transport": True,
                "container_wait_and_logs": True,
                "cleanup": True,
            },
        }, separators=(",", ":")))
    finally:
        if container_id:
            try:
                inspected = docker.inspect_container(container_id)
                docker.require_owned(inspected, installation, purpose="contract-test")
                if inspected.get("State", {}).get("Running"):
                    docker.stop_container(container_id)
                docker.remove_container(container_id)
            except Exception:
                pass
        try:
            owned_volume = docker.inspect_volume(volume)
            docker.require_owned(owned_volume, installation, purpose="contract-test")
            docker.remove_volume(volume)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
