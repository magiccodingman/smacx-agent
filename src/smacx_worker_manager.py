"""Docker-backed lifecycle manager for isolated Linux SMACX game workers."""

from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import Path
import re
import secrets
import tarfile
import time
from typing import Any, Mapping
import uuid

from smacx_control import ControlPlane
from smacx_docker import DockerClient, DockerError, DockerNotFound
from smacx_store import InvalidRecord, MemoryScope, ScopeViolation, StoreError


RESOURCE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,254}$")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
UNSAFE_LOG_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SAFE_HOST_PATH_LIMIT = 4096


class WorkerManagerError(StoreError):
    pass


def _new_id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex}"


def _host_path(value: str, field: str) -> str:
    if not isinstance(value, str) or len(value) > SAFE_HOST_PATH_LIMIT or "\x00" in value:
        raise InvalidRecord(f"invalid_{field}")
    path = Path(value)
    if not path.is_absolute():
        raise InvalidRecord(f"invalid_{field}")
    if str(path) in ("/", "/home", "/usr", "/etc", "/var", "/tmp"):
        raise InvalidRecord(f"unsafe_{field}")
    return str(path)


def _clean_log(value: str) -> str:
    return UNSAFE_LOG_CONTROL.sub("", ANSI_ESCAPE.sub("", value))


class WorkerManager:
    def __init__(self, control: ControlPlane, docker: DockerClient, *,
                 worker_image: str = "smacx-agent-worker:dev",
                 network_name: str | None = None,
                 directx_redist_host_path: str | None = None) -> None:
        self.control = control
        self.store = control.store
        self.docker = docker
        if not RESOURCE_NAME.fullmatch(worker_image.replace(":", "-", 1)):
            # Image references may contain one registry slash and colon. Keep
            # the check intentionally conservative for the initial local build.
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,400}(?::[A-Za-z0-9_.-]{1,100})?", worker_image):
                raise InvalidRecord("invalid_worker_image")
        self.worker_image = worker_image
        self.network_name = network_name
        self.directx_redist_host_path = (
            _host_path(directx_redist_host_path, "directx_redist_path")
            if directx_redist_host_path else None
        )
        installation_hash = hashlib.sha256(
            self.store.installation_id().encode("utf-8")
        ).hexdigest()[:12]
        self.resource_prefix = f"smacx-{installation_hash}"

    @property
    def installation_id(self) -> str:
        return self.store.installation_id()

    def health(self) -> dict[str, Any]:
        try:
            ok = self.docker.ping()
            version = self.docker.version() if ok else {}
        except DockerError:
            return {"ok": False, "error": "docker_engine_unavailable"}
        return {
            "ok": ok,
            "server_version": version.get("Version"),
            "api_version": version.get("ApiVersion"),
            "worker_image": self.worker_image,
            "network_name": self.network_name,
            "directplay_source_configured": bool(self.directx_redist_host_path),
        }

    def _name(self, kind: str, identity: str | None = None) -> str:
        suffix = hashlib.sha256((identity or uuid.uuid4().hex).encode()).hexdigest()[:16]
        name = f"{self.resource_prefix}-{kind}-{suffix}"
        if not RESOURCE_NAME.fullmatch(name):
            raise WorkerManagerError("invalid_managed_resource_name")
        return name

    def _labels(self, purpose: str, **extra: str) -> dict[str, str]:
        return self.docker.labels(self.installation_id, purpose, **extra)

    def _cleanup_container(self, identifier: str, purpose: str) -> None:
        try:
            inspected = self.docker.inspect_container(identifier)
            self.docker.require_owned(inspected, self.installation_id, purpose=purpose)
            if inspected.get("State", {}).get("Running"):
                self.docker.stop_container(identifier, timeout=10)
            self.docker.remove_container(identifier)
        except DockerNotFound:
            return

    def validate_game_source(self, host_path: str, *, display_name: str = "Alien Crossfire") -> dict[str, Any]:
        host_path = _host_path(host_path, "game_source_host_path")
        self.docker.inspect_image(self.worker_image)
        name = self._name("inspect")
        labels = self._labels("game-source-inspector")
        identifier = self.docker.create_container(name, {
            "Image": self.worker_image,
            "Entrypoint": ["python3", "/opt/smacx/inspect_source.py"],
            "Cmd": [],
            "Tty": True,
            "Labels": labels,
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Tmpfs": {"/tmp": "rw,nosuid,nodev,noexec,size=64m"},
                "Mounts": [{
                    "Type": "bind", "Source": host_path, "Target": "/game-source",
                    "ReadOnly": True, "BindOptions": {"Propagation": "rprivate"},
                }],
            },
        })
        try:
            self.docker.start_container(identifier)
            finished = self.docker.wait_container(identifier, timeout=120)
            logs = self.docker.container_logs(identifier, tail=20)
            payload = self._last_json(logs)
            if finished.get("State", {}).get("ExitCode") != 0 or not payload.get("ok"):
                raise WorkerManagerError("game_source_validation_failed")
            source = payload.get("source")
            if not isinstance(source, Mapping) or not re.fullmatch(
                r"[a-f0-9]{64}", str(source.get("terranx_sha256", "")),
            ):
                raise WorkerManagerError("invalid_game_source_probe_response")
            return self.control.register_game_source(
                display_name, host_path, str(source["terranx_sha256"]),
                metadata={"validated_by": "container", "worker_image": self.worker_image},
            )
        finally:
            self._cleanup_container(identifier, "game-source-inspector")

    def import_proton(self, source_host_path: str, *, display_name: str = "Managed Proton") -> dict[str, Any]:
        source_host_path = _host_path(source_host_path, "proton_source_path")
        self.docker.inspect_image(self.worker_image)
        runtime_id = _new_id("runtime")
        volume = self._name("proton", runtime_id)
        labels = self._labels("proton-runtime", **{"io.smacx.runtime": runtime_id})
        self.docker.create_volume(volume, labels)
        helper = self._name("import", runtime_id)
        identifier: str | None = None
        script = """
set -eu
test -f /source/proton
test -f /source/files/bin/wine
test -f /source/files/bin/wineserver
cp -a /source/. /target/
cd /target
# Proton takes an exclusive lock beside its immutable distribution on every
# launch. Redirect only that lock into the worker's private tmpfs so the
# imported, checksummed runtime can remain read-only for every seat.
sed -i 's|self.dist_lock = FileLock(self.path("dist.lock"), timeout=-1)|self.dist_lock = FileLock(os.environ.get("SMACX_PROTON_DIST_LOCK", self.path("dist.lock")), timeout=-1)|' /target/proton
grep -Fq 'SMACX_PROTON_DIST_LOCK' /target/proton
find . -type f ! -name .smacx-manifest.sha256 -print0 | LC_ALL=C sort -z | xargs -0 sha256sum > /tmp/manifest
fingerprint=$(sha256sum /tmp/manifest | cut -d' ' -f1)
install -m 0444 /tmp/manifest /target/.smacx-manifest.sha256
chown -R 10001:10001 /target
printf '{"ok":true,"fingerprint":"%s"}\n' "$fingerprint"
""".strip()
        try:
            identifier = self.docker.create_container(helper, {
                "Image": self.worker_image,
                "Entrypoint": ["/bin/sh", "-c"],
                "Cmd": [script],
                "User": "0:0",
                "Tty": True,
                "Labels": self._labels("proton-importer", **{"io.smacx.runtime": runtime_id}),
                "HostConfig": {
                    "NetworkMode": "none",
                    "ReadonlyRootfs": True,
                    "CapDrop": ["ALL"],
                    "CapAdd": ["CHOWN", "FOWNER", "DAC_OVERRIDE"],
                    "SecurityOpt": ["no-new-privileges"],
                    "Tmpfs": {"/tmp": "rw,nosuid,nodev,size=256m"},
                    "Mounts": [
                        {"Type": "bind", "Source": source_host_path, "Target": "/source",
                         "ReadOnly": True, "BindOptions": {"Propagation": "rprivate"}},
                        {"Type": "volume", "Source": volume, "Target": "/target"},
                    ],
                },
            })
            self.docker.start_container(identifier)
            finished = self.docker.wait_container(identifier, timeout=1800, interval=1)
            payload = self._last_json(self.docker.container_logs(identifier, tail=20))
            fingerprint = str(payload.get("fingerprint", ""))
            if finished.get("State", {}).get("ExitCode") != 0 or not payload.get("ok") \
                    or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
                raise WorkerManagerError("proton_import_failed")
            return self.control.register_runtime(
                display_name, "docker-volume", volume, runtime_id=runtime_id,
                runtime_kind="proton", source_path=source_host_path,
                content_fingerprint=fingerprint, status="ready",
                metadata={
                    "manifest_path": "/proton/.smacx-manifest.sha256",
                    "managed": True,
                    "proton_dist_lock_redirected": True,
                },
            )
        except Exception:
            if identifier:
                try:
                    self._cleanup_container(identifier, "proton-importer")
                finally:
                    identifier = None
            try:
                owned = self.docker.inspect_volume(volume)
                self.docker.require_owned(owned, self.installation_id, purpose="proton-runtime")
                self.docker.remove_volume(volume)
            except Exception:
                pass
            raise
        finally:
            if identifier:
                self._cleanup_container(identifier, "proton-importer")

    @staticmethod
    def _last_json(logs: str) -> dict[str, Any]:
        for line in reversed(logs.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise WorkerManagerError("managed_helper_returned_no_json")

    @staticmethod
    def _secret_archive(name: str, value: str) -> bytes:
        output = BytesIO()
        with tarfile.open(fileobj=output, mode="w") as stream:
            data = value.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o400
            info.uid = 10001
            info.gid = 10001
            info.mtime = int(time.time())
            stream.addfile(info, BytesIO(data))
        return output.getvalue()

    def _seed_secret_volume(self, volume: str, instance_id: str, token: str) -> None:
        helper = self._name("secret", instance_id)
        identifier = self.docker.create_container(helper, {
            "Image": self.worker_image,
            "Entrypoint": ["/bin/true"],
            "Cmd": [],
            "User": "0:0",
            "Tty": True,
            "Labels": self._labels("worker-secret-writer", **{"io.smacx.instance": instance_id}),
            "HostConfig": {
                "NetworkMode": "none", "ReadonlyRootfs": True,
                "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
                "Mounts": [{"Type": "volume", "Source": volume, "Target": "/secrets"}],
            },
        })
        try:
            self.docker.put_archive(identifier, "/secrets", self._secret_archive("bridge-token", token))
        finally:
            self._cleanup_container(identifier, "worker-secret-writer")

    def provision_worker(self, scope: MemoryScope, game_source_id: str, runtime_id: str, *,
                         autostart: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.store.require_scope(scope)
        source = self.control.get_game_source(game_source_id)
        runtime = self.control.get_runtime(runtime_id)
        if source["status"] != "validated" or runtime["status"] != "ready" \
                or runtime["storage_kind"] != "docker-volume":
            raise WorkerManagerError("worker_assets_not_ready")
        runtime_volume = self.docker.inspect_volume(runtime["storage_ref"])
        self.docker.require_owned(runtime_volume, self.installation_id, purpose="proton-runtime")
        self.docker.inspect_image(self.worker_image)
        instance_id = _new_id("instance")
        container_name = self._name("worker", instance_id)
        data_volume = self._name("data", instance_id)
        secret_volume = self._name("secret", instance_id)
        data_created = False
        secret_created = False
        bridge_secret_id: str | None = None
        try:
            self.docker.create_volume(
                data_volume, self._labels("worker-data", **{"io.smacx.instance": instance_id}),
            )
            data_created = True
            self.docker.create_volume(
                secret_volume, self._labels("worker-secret", **{"io.smacx.instance": instance_id}),
            )
            secret_created = True
            token = secrets.token_urlsafe(48)
            secret = self.control.vault.put(f"worker.{instance_id}.bridge_token", token)
            bridge_secret_id = str(secret["secret_id"])
            self._seed_secret_volume(secret_volume, instance_id, token)
            self.store.register_instance(
                instance_id=instance_id, worker_kind="container-linux", scope=scope,
                runtime_root=data_volume,
                metadata={"container_name": container_name, "managed": True},
            )
            return self.control.put_worker_spec(
                instance_id, game_source_id, runtime_id, self.worker_image,
                container_name, data_volume, bridge_secret_id,
                autostart=self._autostart(autostart),
                network={"name": self.network_name, "secret_volume": secret_volume},
            )
        except Exception:
            with self.store.transaction() as connection:
                connection.execute(
                    "DELETE FROM instances WHERE instance_id=? AND status='available' "
                    "AND NOT EXISTS (SELECT 1 FROM sessions WHERE instance_id=?)",
                    (instance_id, instance_id),
                )
            if bridge_secret_id:
                self.control.vault.revoke(bridge_secret_id)
            for volume_name, purpose, created in (
                (secret_volume, "worker-secret", secret_created),
                (data_volume, "worker-data", data_created),
            ):
                if not created:
                    continue
                try:
                    resource = self.docker.inspect_volume(volume_name)
                    self.docker.require_owned(resource, self.installation_id, purpose=purpose)
                    self.docker.remove_volume(volume_name)
                except Exception:
                    pass
            raise

    @staticmethod
    def _autostart(value: Mapping[str, Any] | None) -> dict[str, Any]:
        supplied = dict(value or {})
        result = {
            "enabled": bool(supplied.get("enabled", True)),
            "difficulty": int(supplied.get("difficulty", 0)),
            "world_size": int(supplied.get("world_size", 0)),
            "faction_id": int(supplied.get("faction_id", 1)),
            "blind_research": bool(supplied.get("blind_research", True)),
            "initial_research_priority": int(supplied.get("initial_research_priority", 1)),
            "narrative_ui": bool(supplied.get("narrative_ui", False)),
            "tutorial_ui": bool(supplied.get("tutorial_ui", False)),
        }
        if not 0 <= result["difficulty"] <= 5 or not 0 <= result["world_size"] <= 4 \
                or not 1 <= result["faction_id"] <= 7 \
                or not 0 <= result["initial_research_priority"] <= 3:
            raise InvalidRecord("invalid_worker_autostart")
        return result

    def _worker_environment(self, spec: Mapping[str, Any], session_id: str) -> list[str]:
        autostart = spec["autostart"]
        values = {
            "SMACX_AGENT_TOKEN_FILE": "/run/secrets/bridge-token",
            "SMACX_AGENT_MATCH_ID": spec["match_id"],
            "SMACX_AGENT_SESSION_ID": session_id,
            "SMACX_AGENT_ID": spec["agent_id"],
            "SMACX_PERSPECTIVE_ID": spec["perspective_id"],
            "SMACX_INSTANCE_ID": spec["instance_id"],
            "SMACX_PROTON_BIN": "/proton/proton",
            "SMACX_PROTON_DIST_LOCK": "/tmp/smacx-proton-dist.lock",
            "SMACX_WINEARCH": "win64",
            "SMACX_REQUIRE_DIRECTPLAY": "1" if self.directx_redist_host_path else "0",
            "SMACX_VIEW_ENABLE": "0",
            "SMACX_AGENT_AUTOSTART": "1" if autostart["enabled"] else "0",
            "SMACX_AGENT_DIFFICULTY": str(autostart["difficulty"]),
            "SMACX_AGENT_WORLD_SIZE": str(autostart["world_size"]),
            "SMACX_AGENT_FACTION_ID": str(autostart["faction_id"]),
            "SMACX_AGENT_BLIND_RESEARCH": "1" if autostart["blind_research"] else "0",
            "SMACX_AGENT_INITIAL_RESEARCH_PRIORITY": str(autostart["initial_research_priority"]),
            "SMACX_AGENT_NARRATIVE_UI": "1" if autostart["narrative_ui"] else "0",
            "SMACX_AGENT_TUTORIAL_UI": "1" if autostart["tutorial_ui"] else "0",
            "SMACX_BRIDGE_START_TIMEOUT": "180",
        }
        return [f"{key}={value}" for key, value in values.items()]

    def start_worker(self, instance_id: str, *, timeout: float = 240.0) -> dict[str, Any]:
        spec = self.control.get_worker_spec(instance_id)
        source = self.control.get_game_source(spec["game_source_id"])
        runtime = self.control.get_runtime(spec["runtime_id"])
        if spec["observed_status"] == "running":
            return self.worker_status(instance_id)
        for volume_name, purpose in (
            (spec["data_volume"], "worker-data"),
            (spec["network"]["secret_volume"], "worker-secret"),
            (runtime["storage_ref"], "proton-runtime"),
        ):
            resource = self.docker.inspect_volume(volume_name)
            self.docker.require_owned(resource, self.installation_id, purpose=purpose)
        session_id = _new_id("session")
        scope = MemoryScope(spec["match_id"], spec["agent_id"], spec["perspective_id"])
        self.store.start_session(scope, instance_id, session_id=session_id,
                                 metadata={"container_name": spec["container_name"]})
        mounts = [
            {"Type": "bind", "Source": source["host_path"], "Target": "/game-source",
             "ReadOnly": True, "BindOptions": {"Propagation": "rprivate"}},
            {"Type": "volume", "Source": runtime["storage_ref"], "Target": "/proton",
             "ReadOnly": True},
            {"Type": "volume", "Source": spec["data_volume"], "Target": "/var/lib/smacx"},
            {"Type": "volume", "Source": spec["network"]["secret_volume"],
             "Target": "/run/secrets", "ReadOnly": True},
        ]
        if self.directx_redist_host_path:
            mounts.append({
                "Type": "bind", "Source": self.directx_redist_host_path,
                "Target": "/redist/directx_feb2010_redist.exe", "ReadOnly": True,
                "BindOptions": {"Propagation": "rprivate"},
            })
        labels = self._labels(
            "game-worker", **{
                "io.smacx.instance": instance_id,
                "io.smacx.match": spec["match_id"],
                "io.smacx.session": session_id,
            },
        )
        config = {
            "Image": spec["image_ref"],
            "Env": self._worker_environment(spec, session_id),
            "Tty": True,
            "Labels": labels,
            "ExposedPorts": {"47814/tcp": {}},
            "HostConfig": {
                "NetworkMode": self.network_name or "bridge",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Tmpfs": {
                    "/tmp": "rw,nosuid,nodev,size=512m,mode=1777",
                    "/run": "rw,nosuid,nodev,size=32m,mode=0755",
                },
                "PortBindings": {"47814/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]},
                "Mounts": mounts,
            },
        }
        container_id: str | None = None
        try:
            try:
                old = self.docker.inspect_container(spec["container_name"])
                self.docker.require_owned(old, self.installation_id, purpose="game-worker")
                if old.get("State", {}).get("Running"):
                    raise WorkerManagerError("worker_container_already_running")
                self.docker.remove_container(spec["container_name"])
            except DockerNotFound:
                pass
            container_id = self.docker.create_container(spec["container_name"], config)
            self.docker.start_container(container_id)
            deadline = time.monotonic() + min(max(float(timeout), 30.0), 600.0)
            inspected: dict[str, Any] = {}
            while time.monotonic() < deadline:
                inspected = self.docker.inspect_container(container_id)
                state = inspected.get("State", {})
                health = state.get("Health", {}).get("Status")
                if health == "healthy":
                    break
                if not state.get("Running") or health == "unhealthy":
                    raise WorkerManagerError("worker_failed_healthcheck")
                time.sleep(1)
            else:
                raise WorkerManagerError("worker_health_timeout")
            binding = inspected.get("NetworkSettings", {}).get("Ports", {}).get("47814/tcp")
            host_port = int(binding[0]["HostPort"]) if isinstance(binding, list) and binding else None
            self.control.update_worker_observation(
                instance_id, desired_status="running", observed_status="running", last_error="",
                bridge_host="127.0.0.1" if host_port else spec["container_name"],
                bridge_port=host_port or 47814, instance_status="running",
            )
            return {
                "ok": True, "instance_id": instance_id, "session_id": session_id,
                "container_name": spec["container_name"], "container_id": container_id,
                "health": "healthy", "bridge_host_port": host_port,
            }
        except Exception as exc:
            failure_detail = str(exc)
            if container_id:
                try:
                    logs = self.docker.container_logs(container_id, tail=80)
                    if logs.strip():
                        failure_detail = f"{failure_detail}\n{_clean_log(logs)[-1800:]}"
                except Exception:
                    pass
                try:
                    self._cleanup_container(container_id, "game-worker")
                except Exception:
                    pass
            try:
                self.store.close_session(session_id, status="failed")
            except Exception:
                pass
            self.control.update_worker_observation(
                instance_id, desired_status="stopped", observed_status="error",
                last_error=failure_detail[:2000], bridge_host=None, bridge_port=None,
                instance_status="error",
            )
            raise

    def worker_status(self, instance_id: str) -> dict[str, Any]:
        spec = self.control.get_worker_spec(instance_id)
        try:
            container = self.docker.inspect_container(spec["container_name"])
            self.docker.require_owned(container, self.installation_id, purpose="game-worker")
        except DockerNotFound:
            return {"ok": True, "instance_id": instance_id, "container_present": False,
                    "observed_status": spec["observed_status"]}
        state = container.get("State", {})
        return {
            "ok": True, "instance_id": instance_id, "container_present": True,
            "running": bool(state.get("Running")),
            "health": state.get("Health", {}).get("Status"),
            "exit_code": state.get("ExitCode"),
            "session_id": container.get("Config", {}).get("Labels", {}).get("io.smacx.session"),
        }

    def park_worker(self, instance_id: str) -> dict[str, Any]:
        spec = self.control.get_worker_spec(instance_id)
        try:
            container = self.docker.inspect_container(spec["container_name"])
            self.docker.require_owned(container, self.installation_id, purpose="game-worker")
            if container.get("State", {}).get("Running"):
                self.docker.stop_container(spec["container_name"], timeout=20)
            self.docker.remove_container(spec["container_name"])
        except DockerNotFound:
            pass
        with self.store.transaction() as connection:
            session = connection.execute(
                "SELECT session_id FROM sessions WHERE instance_id=? AND status='running' "
                "ORDER BY started_unix DESC LIMIT 1", (instance_id,),
            ).fetchone()
        if session:
            self.store.close_session(str(session["session_id"]), status="parked")
        updated = self.control.update_worker_observation(
            instance_id, desired_status="parked", observed_status="parked", last_error="",
            bridge_host=None, bridge_port=None, instance_status="available",
        )
        return {"ok": True, "instance_id": instance_id, "status": updated["observed_status"]}
