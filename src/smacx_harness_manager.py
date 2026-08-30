"""Docker-managed, resumable Hermes runtimes for autonomous game seats."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import re
import secrets
import tarfile
import tempfile
import time
from typing import Any, Mapping

from smacx_control import ControlPlane
from smacx_docker import DockerClient, DockerError, DockerNotFound
from smacx_hermes import configure_profile
from smacx_store import InvalidRecord, ScopeViolation, StoreError
from smacx_worker_manager import WorkerManager


HERMES_IMAGE = (
    "docker.io/nousresearch/hermes-agent:v2026.8.27@"
    "sha256:5f23552e16589d291099cd8041233e6200197d225e4b28b22a0463e732d4b843"
)
RESOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")


class HarnessManagerError(StoreError):
    pass


RUNNER = r'''#!/usr/bin/env python3
import os
from pathlib import Path

secret = Path("/run/secrets/provider-api-key")
if secret.is_file():
    value = secret.read_text(encoding="utf-8")
    if not value or "\x00" in value:
        raise SystemExit("invalid managed provider secret")
    os.environ["SMACX_PROVIDER_API_KEY"] = value
os.execvpe("/opt/hermes/.venv/bin/hermes", ["hermes", *os.sys.argv[1:]], os.environ)
'''


class HarnessManager:
    def __init__(self, control: ControlPlane, docker: DockerClient,
                 worker_manager: WorkerManager, *,
                 image_ref: str = HERMES_IMAGE) -> None:
        self.control = control
        self.store = control.store
        self.docker = docker
        self.worker_manager = worker_manager
        if not isinstance(image_ref, str) or len(image_ref) > 512 or not image_ref:
            raise InvalidRecord("invalid_harness_image")
        self.image_ref = image_ref
        self.installation_id = self.store.installation_id()

    def _name(self, kind: str, identity: str) -> str:
        import hashlib
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        value = f"{self.worker_manager.resource_prefix}-{kind}-{digest}"
        if not RESOURCE.fullmatch(value):
            raise HarnessManagerError("invalid_harness_resource_name")
        return value

    def _labels(self, purpose: str, **extra: str) -> dict[str, str]:
        return self.docker.labels(self.installation_id, purpose, **extra)

    @staticmethod
    def _archive_file(name: str, value: str, *, mode: int = 0o400) -> bytes:
        output = BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            payload = value.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = mode
            info.uid = 10000
            info.gid = 10000
            info.mtime = int(time.time())
            archive.addfile(info, BytesIO(payload))
        return output.getvalue()

    @staticmethod
    def _archive_tree(root: Path) -> bytes:
        output = BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root).as_posix()
                info = archive.gettarinfo(str(path), arcname=relative)
                info.uid = 10000
                info.gid = 10000
                info.uname = "hermes"
                info.gname = "hermes"
                if path.is_dir():
                    info.mode = 0o700
                    archive.addfile(info)
                elif path.is_file():
                    info.mode = 0o700 if path.name == "smacx-runner.py" else 0o600
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
        return output.getvalue()

    def _seed_volume(self, volume: str, archive: bytes, *, purpose: str,
                     identity: str) -> None:
        helper_name = self._name("harness-seed", f"{identity}:{purpose}")
        identifier = self.docker.create_container(helper_name, {
            "Image": self.worker_manager.mcp_image,
            "Entrypoint": ["/bin/true"],
            "User": "0:0",
            "Labels": self._labels("harness-volume-writer", **{
                "io.smacx.harness": identity,
            }),
            "HostConfig": {
                "NetworkMode": "none", "ReadonlyRootfs": True,
                "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
                "Mounts": [{"Type": "volume", "Source": volume, "Target": "/target"}],
            },
        })
        try:
            self.docker.put_archive(identifier, "/target", archive)
        finally:
            try:
                self.docker.remove_container(identifier)
            except DockerNotFound:
                pass

    def _internal_descriptor(self, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        worker = self.control.get_worker_spec(str(descriptor["instance_id"]))
        mcp_name = worker.get("network", {}).get("mcp_container_name")
        if not isinstance(mcp_name, str) or not mcp_name:
            raise HarnessManagerError("managed_mcp_container_unavailable")
        result = dict(descriptor)
        result["mcp_url"] = f"http://{mcp_name}:47815/mcp"
        return result

    def provision_profile(self, descriptor: Mapping[str, Any]) -> dict[str, Any]:
        harness_profile_id = str(descriptor.get("harness_profile_id") or "")
        if not harness_profile_id:
            raise InvalidRecord("invalid_harness_descriptor")
        self.docker.inspect_image(self.image_ref)
        internal = self._internal_descriptor(descriptor)
        for run in self.control.list_harness_runs():
            if run["harness_profile_id"] == harness_profile_id and run["status"] in {
                    "queued", "starting", "running", "restarting"}:
                raise HarnessManagerError("harness_profile_already_running")
        try:
            existing = self.control.get_harness_runtime_spec(harness_profile_id)
        except ScopeViolation:
            existing = None
        data_volume = str(existing["data_volume"]) if existing else self._name(
            "hermes-data", harness_profile_id,
        )
        secret_volume = str(existing["secret_volume"]) if existing else self._name(
            "hermes-secret", harness_profile_id,
        )
        container_name = str(existing["container_name"]) if existing else self._name(
            "hermes", harness_profile_id,
        )
        if existing is None:
            self.docker.create_volume(
                data_volume, self._labels("harness-data", **{
                    "io.smacx.harness": harness_profile_id,
                }),
            )
            self.docker.create_volume(
                secret_volume, self._labels("harness-secret", **{
                    "io.smacx.harness": harness_profile_id,
                }),
            )
        else:
            # A profile can move from a keyed provider to an unkeyed provider.
            # Rotate its tiny purpose-specific secret volume on every stopped
            # reprovision so obsolete credentials can never remain mounted.
            try:
                old_container = self.docker.inspect_container(container_name)
                self.docker.require_owned(
                    old_container, self.installation_id, purpose="harness-run",
                )
                if old_container.get("State", {}).get("Running"):
                    raise HarnessManagerError("harness_profile_container_still_running")
                self.docker.remove_container(container_name)
            except DockerNotFound:
                pass
            old_secret = self.docker.inspect_volume(secret_volume)
            self.docker.require_owned(
                old_secret, self.installation_id, purpose="harness-secret",
            )
            self.docker.remove_volume(secret_volume)
            self.docker.create_volume(
                secret_volume, self._labels("harness-secret", **{
                    "io.smacx.harness": harness_profile_id,
                }),
            )
        for volume, purpose in ((data_volume, "harness-data"),
                                (secret_volume, "harness-secret")):
            resource = self.docker.inspect_volume(volume)
            self.docker.require_owned(resource, self.installation_id, purpose=purpose)
        with tempfile.TemporaryDirectory(prefix="smacx-managed-hermes-") as temporary:
            staging = Path(temporary)
            configure_profile(
                hermes_root=staging,
                runtime_hermes_root=Path("/opt/data"),
                agent_id=str(internal["agent_id"]), agent_name=str(internal["agent_name"]),
                match_id=str(internal["match_id"]), mcp_url=str(internal["mcp_url"]),
                provider_base_url=str(internal["provider_base_url"]),
                model_id=str(internal["model_id"]),
                reasoning_effort=str(internal["reasoning_effort"]),
                profile_id=str(internal["external_profile_id"]),
                context_length=internal.get("context_length"),
                provider_api_key_env=(
                    "SMACX_PROVIDER_API_KEY"
                    if internal.get("provider_requires_api_key") else None
                ),
            )
            (staging / "smacx-runner.py").write_text(RUNNER, encoding="utf-8")
            self._seed_volume(
                data_volume, self._archive_tree(staging), purpose="profile",
                identity=harness_profile_id,
            )
        api_key = self.control.provider_api_key(str(internal["provider_id"]))
        if internal.get("provider_requires_api_key") and not api_key:
            raise HarnessManagerError("managed_provider_secret_unavailable")
        if api_key:
            self._seed_volume(
                secret_volume, self._archive_file("provider-api-key", api_key),
                purpose="provider-key", identity=harness_profile_id,
            )
        return self.control.put_harness_runtime_spec(
            harness_profile_id, image_ref=self.image_ref, data_volume=data_volume,
            secret_volume=secret_volume, container_name=container_name,
            metadata={
                "agent_id": internal["agent_id"], "match_id": internal["match_id"],
                "profile_id": internal["external_profile_id"],
                "provider_id": internal["provider_id"],
                "provider_secret_injected": bool(api_key),
                "mcp_url": internal["mcp_url"],
                "image_pinned": "@sha256:" in self.image_ref,
            },
        )

    @staticmethod
    def default_initial_prompt() -> str:
        return (
            "Play this Alien Crossfire match autonomously as a genuine player until the operator "
            "stops the managed run or a semantic capability gap requires developer work. Use only "
            "SMACX semantic tools for the game. Observe chat every decision cycle, negotiate when "
            "useful, pursue victory, and maintain scoped facts, beliefs, relationships, commitments, "
            "and goals. Never use screenshots, mouse, keyboard, desktop, terminal, or raw UI input."
        )

    @staticmethod
    def default_continuation_prompt() -> str:
        return (
            "Continue the same durable match and Hermes conversation from current semantic state. "
            "Resume autonomous play until operator stop or a reported capability gap."
        )

    def create_run(self, descriptor: Mapping[str, Any], *,
                   initial_prompt: str | None = None,
                   run_budget_seconds: int = 3600,
                   max_turns: int = 5000,
                   restart_limit: int = 1000) -> dict[str, Any]:
        runtime = self.provision_profile(descriptor)
        budget = min(max(int(run_budget_seconds), 60), 86_400)
        turns = min(max(int(max_turns), 10), 5000)
        restarts = min(max(int(restart_limit), 0), 100_000)
        run = self.control.create_harness_run(
            str(descriptor["harness_profile_id"]), match_id=str(descriptor["match_id"]),
            initial_prompt=initial_prompt or self.default_initial_prompt(),
            continuation_prompt=self.default_continuation_prompt(),
            restart_policy={
                "restart_on_clean_exit": True, "restart_on_error": True,
                "restart_limit": restarts, "run_budget_seconds": budget,
                "max_turns": turns, "toolsets": "smacx,web",
            },
        )
        return self.start_run(str(run["run_id"]), runtime=runtime)

    def _container_config(self, run: Mapping[str, Any], runtime: Mapping[str, Any],
                          prompt: str) -> dict[str, Any]:
        policy = run["restart_policy"]
        profile = self.control.get_harness_profile(str(run["harness_profile_id"]))
        profile_id = str(profile["external_profile_id"])
        workspace = f"/opt/data/profiles/{profile_id}/workspace/matches/{run['match_id']}"
        command = [
            "-p", profile_id, "chat", "--continue", str(run["match_id"]),
            "--create-if-missing", "--in", workspace,
            "--reasoning", str(profile["reasoning_effort"]),
            "--toolsets", str(policy.get("toolsets", "smacx,web")),
            "--max-turns", str(policy.get("max_turns", 5000)),
            "--run-budget", str(policy.get("run_budget_seconds", 3600)),
            "--pass-session-id", "--query", prompt,
        ]
        return {
            "Image": runtime["image_ref"],
            "Entrypoint": ["python3", "/opt/data/smacx-runner.py"],
            "Cmd": command,
            "User": "10000:10000",
            "Env": [
                "HOME=/opt/data", "HERMES_HOME=/opt/data",
                "PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1",
            ],
            "Labels": self._labels("harness-run", **{
                "io.smacx.run": str(run["run_id"]),
                "io.smacx.match": str(run["match_id"]),
                "io.smacx.agent": str(run["agent_id"]),
            }),
            "HostConfig": {
                "NetworkMode": self.worker_manager.network_name or "bridge",
                "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Tmpfs": {
                    "/tmp": "rw,nosuid,nodev,size=512m,mode=1777",
                    "/run": "rw,nosuid,nodev,size=32m,mode=0755",
                },
                "Mounts": [
                    {"Type": "volume", "Source": runtime["data_volume"],
                     "Target": "/opt/data"},
                    {"Type": "volume", "Source": runtime["secret_volume"],
                     "Target": "/run/secrets", "ReadOnly": True},
                ],
            },
        }

    def start_run(self, run_id: str, *,
                  runtime: Mapping[str, Any] | None = None) -> dict[str, Any]:
        run = self.control.get_harness_run(run_id)
        if run["desired_status"] != "running":
            raise HarnessManagerError("harness_run_stop_requested")
        runtime = runtime or self.control.get_harness_runtime_spec(
            str(run["harness_profile_id"]),
        )
        for volume, purpose in ((runtime["data_volume"], "harness-data"),
                                (runtime["secret_volume"], "harness-secret")):
            resource = self.docker.inspect_volume(str(volume))
            self.docker.require_owned(resource, self.installation_id, purpose=purpose)
        prompt = run["initial_prompt"] if int(run["restart_count"]) == 0 \
            else run["continuation_prompt"]
        container_name = str(runtime["container_name"])
        try:
            old = self.docker.inspect_container(container_name)
            self.docker.require_owned(old, self.installation_id, purpose="harness-run")
            if old.get("State", {}).get("Running"):
                return self.status(run_id)
            self.docker.remove_container(container_name)
        except DockerNotFound:
            pass
        self.control.update_harness_run(
            run_id, status="starting", container_name=container_name,
        )
        identifier = self.docker.create_container(
            container_name, self._container_config(run, runtime, str(prompt)),
        )
        try:
            self.docker.start_container(identifier)
        except Exception as exc:
            self.control.update_harness_run(
                run_id, status="error", last_error=str(exc)[:2000],
            )
            raise
        return self.control.update_harness_run(
            run_id, status="running", container_name=container_name,
            heartbeat=True, last_error="",
        )

    def status(self, run_id: str) -> dict[str, Any]:
        run = self.control.get_harness_run(run_id)
        container_name = run.get("container_name")
        observed = {"container_present": False, "running": False}
        if container_name:
            try:
                container = self.docker.inspect_container(str(container_name))
                self.docker.require_owned(container, self.installation_id, purpose="harness-run")
                state = container.get("State", {})
                observed = {
                    "container_present": True, "running": bool(state.get("Running")),
                    "exit_code": state.get("ExitCode"), "status": state.get("Status"),
                }
            except DockerNotFound:
                pass
        return {"ok": True, "run": run, "observed": observed}

    def telemetry(self, run_id: str) -> dict[str, Any]:
        """Read aggregate Hermes usage from its private durable state.

        Hermes is the authority for provider token accounting.  A short-lived,
        no-network helper reads the profile SQLite database read-only; neither
        the portal nor the control process receives filesystem access to the
        harness home.
        """
        run = self.control.get_harness_run(run_id)
        runtime = self.control.get_harness_runtime_spec(
            str(run["harness_profile_id"]),
        )
        data_volume = str(runtime["data_volume"])
        resource = self.docker.inspect_volume(data_volume)
        self.docker.require_owned(
            resource, self.installation_id, purpose="harness-data",
        )
        helper_name = self._name("telemetry", run_id)
        try:
            old = self.docker.inspect_container(helper_name)
            self.docker.require_owned(old, self.installation_id, purpose="harness-telemetry")
            if old.get("State", {}).get("Running"):
                self.docker.stop_container(helper_name, timeout=5)
            self.docker.remove_container(helper_name)
        except DockerNotFound:
            pass
        query = r'''import glob,json,sqlite3
paths=glob.glob('/data/profiles/*/state.db')
result={'sessions':0,'api_calls':0,'input_tokens':0,'output_tokens':0,'cache_read_tokens':0,'cache_write_tokens':0,'reasoning_tokens':0}
for path in paths:
    try:
        db=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=3)
        row=db.execute('SELECT COUNT(*),COALESCE(SUM(api_call_count),0),COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(cache_read_tokens),0),COALESCE(SUM(cache_write_tokens),0),COALESCE(SUM(reasoning_tokens),0) FROM sessions').fetchone()
        db.close()
        for key,value in zip(result,row): result[key]+=int(value or 0)
    except (sqlite3.Error,OSError): pass
print(json.dumps(result,separators=(',',':')))
'''
        identifier = self.docker.create_container(helper_name, {
            "Image": self.worker_manager.mcp_image,
            "Entrypoint": ["python3", "-c"],
            "Cmd": [query],
            "User": "10000:10000",
            "Labels": self._labels("harness-telemetry", **{
                "io.smacx.run": run_id,
            }),
            "HostConfig": {
                "NetworkMode": "none", "ReadonlyRootfs": True,
                "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
                "Mounts": [{"Type": "volume", "Source": data_volume,
                            "Target": "/data", "ReadOnly": True}],
            },
        })
        try:
            self.docker.start_container(identifier)
            observed = self.docker.wait_container(identifier, timeout=15)
            if int(observed.get("State", {}).get("ExitCode") or 0) != 0:
                raise HarnessManagerError("harness_telemetry_failed")
            logs = self.docker.container_logs(identifier, tail=20)
            candidates = re.findall(r"\{[^\r\n]+\}", logs)
            if not candidates:
                raise HarnessManagerError("invalid_harness_telemetry")
            value = json.loads(candidates[-1])
            if not isinstance(value, dict):
                raise HarnessManagerError("invalid_harness_telemetry")
            return {"ok": True, "run_id": run_id, "telemetry": value}
        finally:
            try:
                self.docker.remove_container(identifier)
            except DockerNotFound:
                pass

    def stop_run(self, run_id: str) -> dict[str, Any]:
        run = self.control.update_harness_run(run_id, desired_status="stopped")
        if run.get("status") == "stopped":
            return run
        container_name = run.get("container_name")
        if container_name:
            try:
                container = self.docker.inspect_container(str(container_name))
                self.docker.require_owned(container, self.installation_id, purpose="harness-run")
                if container.get("State", {}).get("Running"):
                    self.docker.stop_container(str(container_name), timeout=20)
                self.docker.remove_container(str(container_name))
            except DockerNotFound:
                pass
            except DockerError:
                # A stop and the background reconciler may meet at the same
                # container.  Treat that race as success only after Docker
                # confirms the process is absent or no longer running.
                try:
                    observed = self.docker.inspect_container(str(container_name))
                except DockerNotFound:
                    observed = None
                if observed is not None and observed.get("State", {}).get("Running"):
                    raise
        return self.control.update_harness_run(run_id, status="stopped", exit_code=0)

    def reconcile_once(self) -> dict[str, Any]:
        checked = restarted = stopped = errors = 0
        for run in self.control.list_harness_runs():
            if run["status"] not in {"queued", "starting", "running", "restarting"}:
                continue
            checked += 1
            if run["desired_status"] == "stopped":
                self.stop_run(str(run["run_id"]))
                stopped += 1
                continue
            observed = self.status(str(run["run_id"]))["observed"]
            if observed.get("running"):
                self.control.update_harness_run(str(run["run_id"]), heartbeat=True)
                continue
            # Avoid a tight create/exit loop when a provider is unavailable or
            # a prompt ends immediately. The supervisor runs every ten seconds;
            # this guard also makes manual rapid reconciliation harmless.
            if time.time() - float(run.get("updated_unix") or 0) < 5.0:
                continue
            exit_code = int(observed.get("exit_code") or 0)
            policy = run["restart_policy"]
            restart_allowed = (
                (exit_code == 0 and policy.get("restart_on_clean_exit") is True)
                or (exit_code != 0 and policy.get("restart_on_error") is True)
            ) and int(run["restart_count"]) < int(policy.get("restart_limit", 0))
            if restart_allowed:
                self.control.update_harness_run(
                    str(run["run_id"]), status="restarting", exit_code=exit_code,
                    increment_restart=True,
                )
                self.start_run(str(run["run_id"]))
                restarted += 1
            else:
                final = "completed" if exit_code == 0 else "error"
                self.control.update_harness_run(
                    str(run["run_id"]), status=final, exit_code=exit_code,
                    last_error=("" if exit_code == 0 else "managed_harness_exited"),
                )
                errors += int(exit_code != 0)
        return {"ok": True, "checked": checked, "restarted": restarted,
                "stopped": stopped, "errors": errors}
