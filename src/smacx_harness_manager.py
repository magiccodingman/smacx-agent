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
import uuid
from typing import Any, Mapping

from smacx_context_policy import HERMES_COMPRESSION_THRESHOLD_RATIO

from smacx_control import ControlPlane
from smacx_docker import DockerClient, DockerError, DockerNotFound
from smacx_hermes import configure_profile
from smacx_journal import CampaignJournal
from smacx_store import InvalidRecord, ScopeViolation, StoreError
from smacx_worker_manager import WorkerManager
from smacx_attention import AttentionService


HERMES_IMAGE = "smacx-agent-harness:dev"
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

    def _journal_run_event(
        self, run: Mapping[str, Any], event_type: str, payload: Mapping[str, Any],
        *, commit_reason: str = "",
    ) -> list[dict[str, str]]:
        """Record bounded Hermes lifecycle metadata in the campaign authority.

        Raw prompts, model output, and reasoning remain in Hermes's private
        transcript.  The campaign timeline stores only stable references and
        aggregate counters required to explain/rebuild an autonomous turn.
        """
        journal = CampaignJournal(
            self.store.path.parent / "campaigns",
            timeline_resolver=self.store.active_timeline_id,
        )
        results: list[dict[str, str]] = []
        for scope in self.store.scopes_for_match(str(run["match_id"])):
            if scope.agent_id != str(run.get("agent_id") or ""):
                continue
            event = journal.append(
                scope, event_type, dict(payload),
                session_id=(str(run.get("native_session_id"))
                            if run.get("native_session_id") else None),
                commit_reason=commit_reason,
            )
            results.append({
                "perspective_id": scope.perspective_id,
                "event_id": str(event["event_id"]),
                "head_hash": str(event["event_hash"]),
            })
        return results

    def _append_capability_gap_report(self, report: Mapping[str, Any]) -> None:
        """Queue a redacted diagnostic bundle for an observed bridge outage."""
        path = self.store.path.parent / "capability-gaps.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(report), ensure_ascii=False,
                                    separators=(",", ":")) + "\n")

    def _cancel_sovereign_after_process_stop(self, run: Mapping[str, Any], reason: str) -> None:
        journal = CampaignJournal(
            self.store.path.parent / "campaigns",
            timeline_resolver=self.store.active_timeline_id,
        )
        for scope in self.store.scopes_for_match(str(run["match_id"])):
            if scope.agent_id == str(run.get("agent_id") or ""):
                AttentionService(self.store, journal, scope).cancel_active_sovereign(reason)

    def _episode_mode(self, run: Mapping[str, Any], progress: Mapping[str, Any]) -> str:
        phase = str(progress.get("phase") or "")
        if phase in {"turn", "interaction", "handoff"}:
            return "gameplay"
        journal = CampaignJournal(
            self.store.path.parent / "campaigns",
            timeline_resolver=self.store.active_timeline_id,
        )
        for scope in self.store.scopes_for_match(str(run["match_id"])):
            if scope.agent_id == str(run.get("agent_id") or "") \
                    and scope.perspective_id == str(run.get("perspective_id") or ""):
                if AttentionService(self.store, journal, scope).pending_summary()["has_chat"]:
                    return "communication"
        return "gameplay"

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

    def _claim_data_volume(self, volume: str, *, identity: str) -> None:
        """Make a named Hermes volume writable by its unprivileged runtime.

        Docker creates a fresh named-volume root as root:root/0755 and archive
        extraction cannot change the mountpoint owner.  Use a short-lived,
        networkless helper with only CAP_CHOWN; the long-running harness still
        receives no capabilities and never runs as root.
        """
        helper_name = self._name("harness-owner", identity)
        identifier = self.docker.create_container(helper_name, {
            "Image": self.worker_manager.mcp_image,
            "Entrypoint": ["/bin/chown"],
            "Cmd": ["10000:10000", "/target"],
            "User": "0:0",
            "Labels": self._labels("harness-volume-owner", **{
                "io.smacx.harness": identity,
            }),
            "HostConfig": {
                "NetworkMode": "none", "ReadonlyRootfs": True,
                "CapDrop": ["ALL"], "CapAdd": ["CHOWN"],
                "SecurityOpt": ["no-new-privileges"],
                "Mounts": [{"Type": "volume", "Source": volume, "Target": "/target"}],
            },
        })
        try:
            self.docker.start_container(identifier)
            stopped = self.docker.wait_container(identifier, timeout=15.0)
            if int(stopped.get("State", {}).get("ExitCode", -1)) != 0:
                raise HarnessManagerError("harness_data_volume_ownership_failed")
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
            harness_profile = self.control.get_harness_profile(harness_profile_id)
            configure_profile(
                hermes_root=staging,
                runtime_hermes_root=Path("/opt/data"),
                agent_id=str(internal["agent_id"]), agent_name=str(internal["agent_name"]),
                match_id=str(internal["match_id"]), mcp_url=str(internal["mcp_url"]),
                provider_base_url=str(internal["provider_base_url"]),
                model_id=str(internal["model_id"]),
                reasoning_effort=str(internal["reasoning_effort"]),
                generation_settings=internal.get("generation_settings"),
                profile_id=str(internal["external_profile_id"]),
                context_length=internal.get("context_length"),
                provider_api_key_env=(
                    "SMACX_PROVIDER_API_KEY"
                    if internal.get("provider_requires_api_key") else None
                ),
                system_prompt=str(harness_profile["system_prompt"]),
            )
            (staging / "smacx-runner.py").write_text(RUNNER, encoding="utf-8")
            self._claim_data_volume(data_volume, identity=harness_profile_id)
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
        worker = self.control.get_worker_spec(str(internal["instance_id"]))
        runtime_context_token = self.control.vault.read(
            str(worker["bridge_secret_id"]),
            purpose=f"worker.{internal['instance_id']}.bridge_token",
        )
        self._seed_volume(
            secret_volume, self._archive_file("runtime-context-token", runtime_context_token),
            purpose="runtime-context-token", identity=harness_profile_id,
        )
        return self.control.put_harness_runtime_spec(
            harness_profile_id, image_ref=self.image_ref, data_volume=data_volume,
            secret_volume=secret_volume, container_name=container_name,
            metadata={
                "agent_id": internal["agent_id"], "match_id": internal["match_id"],
                "perspective_id": internal["perspective_id"],
                "instance_id": internal["instance_id"],
                "profile_id": internal["external_profile_id"],
                "provider_id": internal["provider_id"],
                "generation_settings": internal.get("generation_settings"),
                "provider_secret_injected": bool(api_key),
                "mcp_url": internal["mcp_url"],
                "context_length": internal.get("context_length") or 65_536,
                "strict_system_prompt": True,
                "system_prompt_schema": harness_profile.get("metadata", {}).get(
                    "system_prompt_schema"
                ),
                "system_prompt_sha256": harness_profile.get("metadata", {}).get(
                    "system_prompt_sha256"
                ),
                "base_image_digest_pinned": True,
            },
        )

    @staticmethod
    def default_initial_prompt() -> str:
        return (
            "[SMACX_EPISODE_BOUNDARY kind=start] Re-anchor with the authoritative "
            "smac_decision state, then continue autonomous play."
        )

    @staticmethod
    def default_continuation_prompt() -> str:
        return (
            "[SMACX_EPISODE_BOUNDARY kind=resume] Re-anchor with the newest authoritative "
            "smac_decision state. Continue autonomous play until the next real boundary; "
            "produce a TURN HANDOFF only when a semantic result requires it."
        )

    def create_run(self, descriptor: Mapping[str, Any], *,
                   initial_prompt: str | None = None,
                   run_budget_seconds: int = 3600,
                   max_turns: int = 256,
                   restart_limit: int = 1000) -> dict[str, Any]:
        runtime = self.provision_profile(descriptor)
        budget = min(max(int(run_budget_seconds), 60), 86_400)
        turns = min(max(int(max_turns), 10), 512)
        restarts = min(max(int(restart_limit), 0), 100_000)
        run = self.control.create_harness_run(
            str(descriptor["harness_profile_id"]), match_id=str(descriptor["match_id"]),
            initial_prompt=initial_prompt or self.default_initial_prompt(),
            continuation_prompt=self.default_continuation_prompt(),
            restart_policy={
                "restart_on_clean_exit": True, "restart_on_error": True,
                "restart_limit": restarts, "run_budget_seconds": budget,
                "max_turns": turns, "toolsets": "smacx",
                "max_clean_yields_without_progress": 3,
                "semantic_stall_seconds": 360,
                "semantic_stall_recovery_limit": 2,
            },
        )
        return self.start_run(str(run["run_id"]), runtime=runtime)

    def _container_config(self, run: Mapping[str, Any], runtime: Mapping[str, Any],
                          prompt: str, *, episode_mode: str = "gameplay") -> dict[str, Any]:
        policy = run["restart_policy"]
        profile = self.control.get_harness_profile(str(run["harness_profile_id"]))
        runtime_metadata = runtime.get("metadata") if isinstance(runtime.get("metadata"), Mapping) else {}
        runtime_context_url = re.sub(
            r":47815/mcp$", ":47816/runtime-context",
            str(runtime_metadata.get("mcp_url") or ""),
        )
        profile_id = str(profile["external_profile_id"])
        workspace = f"/opt/data/profiles/{profile_id}/workspace/matches/{run['match_id']}"
        prompt_path = f"/opt/data/profiles/{profile_id}/SYSTEM.md"
        prompt_hash = str(profile.get("metadata", {}).get("system_prompt_sha256") or "")
        if len(prompt_hash) != 64:
            raise HarnessManagerError("managed_system_prompt_hash_missing")
        toolset = ("smacx-communication" if episode_mode == "communication"
                   else str(policy.get("toolsets", "smacx")))
        command = [
            "-p", profile_id, "chat", "--continue", str(run["match_id"]),
            "--create-if-missing", "--in", workspace,
            "--reasoning", str(profile["reasoning_effort"]),
            "--toolsets", toolset,
            "--max-turns", str(policy.get("max_turns", 256)),
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
                "SMACX_STRICT_SYSTEM_PROMPT=1",
                f"SMACX_SYSTEM_PROMPT_FILE={prompt_path}",
                f"SMACX_SYSTEM_PROMPT_SHA256={prompt_hash}",
                f"SMACX_AGENT_MATCH_ID={run['match_id']}",
                f"SMACX_AGENT_ID={run['agent_id']}",
                f"SMACX_HARNESS_PROFILE_ID={run['harness_profile_id']}",
                f"SMACX_AGENT_SESSION_ID={run.get('native_session_id') or ''}",
                f"SMACX_PERSPECTIVE_ID={runtime_metadata.get('perspective_id') or ''}",
                f"SMACX_CONTEXT_LENGTH={runtime_metadata.get('context_length') or 65536}",
                f"SMACX_HERMES_COMPRESSION_THRESHOLD_RATIO={HERMES_COMPRESSION_THRESHOLD_RATIO}",
                f"SMACX_EPISODE_MODE={episode_mode}",
                f"SMACX_RUNTIME_CONTEXT_URL={runtime_context_url}",
                "SMACX_RUNTIME_CONTEXT_TOKEN_FILE=/run/secrets/runtime-context-token",
                "SMACX_REFERENCE_URL=http://knowledge-service:8090",
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
        # Runtime specs and their named volumes outlive control-container
        # upgrades.  Reassert the data-root ownership on every invocation so
        # old or externally restored volumes cannot strand Hermes before it
        # can write its session database and logs.
        self._claim_data_volume(
            str(runtime["data_volume"]), identity=str(run["harness_profile_id"]),
        )
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        invocation_count = int(metadata.get("invocation_count") or 0)
        first_invocation = invocation_count == 0 and int(run["restart_count"]) == 0
        container_name = str(runtime["container_name"])
        try:
            old = self.docker.inspect_container(container_name)
            self.docker.require_owned(old, self.installation_id, purpose="harness-run")
            if old.get("State", {}).get("Running"):
                return self.status(run_id)
            self.docker.remove_container(container_name)
        except DockerNotFound:
            pass
        self._cancel_sovereign_after_process_stop(run, "provider_episode_restart")
        progress = self.worker_manager.semantic_progress(str(run["instance_id"]))
        episode_mode = self._episode_mode(run, progress)
        if first_invocation:
            prompt = run["initial_prompt"]
        elif episode_mode == "communication":
            prompt = (
                f"[SMACX_EPISODE_BOUNDARY kind=communication sequence={invocation_count + 1}] "
                "You are the same sovereign player. Process the delivered conversation, "
                "reply or negotiate as you judge appropriate, update durable cognition when "
                "material, acknowledge what you actually processed, then yield. Native gameplay "
                "mutation is unavailable in this serialized communication episode."
            )
        else:
            prompt = (
                f"[SMACX_EPISODE_BOUNDARY kind=resume sequence={invocation_count + 1}] "
                "Re-anchor with the newest authoritative smac_decision state. Continue "
                "autonomous play until the next real boundary; produce a TURN HANDOFF only "
                "when a semantic result requires it."
            )
        self.control.update_harness_run(
            run_id, status="starting", container_name=container_name,
            metadata_update={
                "invocation_count": invocation_count + 1,
                "attempt_started_progress": progress,
                "attempt_started_unix": time.time(),
                "episode_mode": episode_mode,
            },
        )
        identifier = self.docker.create_container(
            container_name, self._container_config(
                run, runtime, str(prompt), episode_mode=episode_mode,
            ),
        )
        try:
            self.docker.start_container(identifier)
            episode_journal = self._journal_run_event(
                run, "agent.episode_started", {
                    "run_id": run["run_id"],
                    "harness_profile_id": run["harness_profile_id"],
                    "external_session_id": run.get("external_session_id"),
                    "native_session_id": run.get("native_session_id"),
                    "invocation": invocation_count + 1,
                    "episode_mode": episode_mode,
                    "reasoning_effort": self.control.get_harness_profile(
                        str(run["harness_profile_id"])
                    ).get("reasoning_effort"),
                    "starting_progress": progress,
                },
                commit_reason=("Start autonomous episode" if first_invocation
                               else "Resume autonomous episode"),
            )
        except Exception as exc:
            try:
                self.docker.stop_container(identifier, timeout=5)
                self.docker.remove_container(identifier)
            except (DockerError, DockerNotFound):
                pass
            self.control.update_harness_run(
                run_id, status="error", last_error=str(exc)[:2000],
            )
            raise
        return self.control.update_harness_run(
            run_id, status="running", container_name=container_name,
            heartbeat=True, last_error="", metadata_update={
                "episode_journal": episode_journal,
            },
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
        self._cancel_sovereign_after_process_stop(run, "provider_episode_stopped")
        return self.control.update_harness_run(run_id, status="stopped", exit_code=0)

    def _observe_bridge_unavailable(self, run: dict, metadata: dict,
                                    progress: dict, now: float) -> bool:
        unavailable_since = float(
            metadata.get("semantic_unavailable_since_unix") or now
        )
        unavailable_samples = int(
            metadata.get("semantic_unavailable_samples") or 0
        ) + 1
        unavailable_update = {
            "semantic_sample_unix": now,
            "semantic_progress": progress,
            "semantic_unavailable_since_unix": unavailable_since,
            "semantic_unavailable_samples": unavailable_samples,
            "semantic_unavailable_reason": str(
                progress.get("reason") or "semantic_progress_unavailable"
            )[:240],
        }
        # A single failed probe is expected during brief native
        # transitions. Three consecutive samples spanning at least
        # one minute means the agent is alive but has lost its
        # semantic game connection. Stop it before it can loop and
        # expose a recoverable operator incident in the portal.
        if unavailable_samples >= 3 and now - unavailable_since >= 60:
            gap_id = "gap-" + uuid.uuid4().hex
            reason = unavailable_update["semantic_unavailable_reason"]
            detail = {
                "schema": "smacx.capability-gap-incident.v1",
                "gap_id": gap_id,
                "summary": "The AI stopped because its native game bridge became unavailable.",
                "screen_or_state": "Managed game worker stopped answering semantic observations.",
                "intended_decision": "Continue the current autonomous turn safely.",
                "required_observation": "A healthy semantic snapshot from the managed game worker.",
                "required_action": "Restore the worker from its latest verified checkpoint.",
                "why_blocked": reason,
                "turn": (metadata.get("semantic_progress") or {}).get("turn")
                    if isinstance(metadata.get("semantic_progress"), dict) else None,
                "reported_at_unix": now,
                "native_worker_preserved": True,
                "run_id": run["run_id"],
                "unavailable_seconds": now - unavailable_since,
                "unavailable_samples": unavailable_samples,
            }
            prior_progress = metadata.get("semantic_progress") \
                if isinstance(metadata.get("semantic_progress"), dict) else {}
            self._append_capability_gap_report({
                "schema": "smacx.capability-gap.v1",
                "gap_id": gap_id,
                "match_id": run["match_id"],
                "session_id": prior_progress.get("session_id") or "",
                "revision": prior_progress.get("revision") or "",
                "turn": detail["turn"],
                "screen_or_state": detail["screen_or_state"],
                "intended_decision": detail["intended_decision"],
                "required_observation": detail["required_observation"],
                "required_action": detail["required_action"],
                "why_blocked": detail["why_blocked"],
                "reported_at_unix": now,
                "supervisor_generated": True,
            })
            incident = self.control.record_supervision_incident(
                str(run["instance_id"]), f"capability_gap:{gap_id}",
                "operator_required", detail,
            )
            container_name = str(run.get("container_name") or "")
            if container_name:
                try:
                    self.docker.stop_container(container_name, timeout=10)
                except DockerNotFound:
                    pass
            self._journal_run_event(
                run, "agent.episode_ended", {
                    "run_id": run["run_id"],
                    "outcome": "semantic_bridge_unavailable",
                    "progress": progress,
                    "unavailable_seconds": now - unavailable_since,
                }, commit_reason="Pause autonomous episode",
            )
            self.control.update_harness_run(
                str(run["run_id"]), status="error", desired_status="stopped",
                last_error=f"capability_gap:{gap_id}",
                metadata_update={
                    **unavailable_update,
                    "operator_attention_required": True,
                    "supervision_incident": incident,
                },
            )
            return True
        self.control.update_harness_run(
            str(run["run_id"]), heartbeat=True,
            metadata_update=unavailable_update,
        )
        return False

    def reconcile_once(self) -> dict[str, Any]:
        checked = restarted = continued = stopped = errors = operator_required = 0
        for run in self.control.list_harness_runs():
            if run["status"] not in {"queued", "starting", "running", "restarting"}:
                continue
            checked += 1
            # A process restart must never resurrect autonomous callers for a
            # dormant campaign. Match lifecycle outranks a stale run's
            # desired_status bit; the portal can explicitly create a fresh run
            # only after recovery returns the native match to running.
            try:
                match_status = self.control.get_match(str(run["match_id"]))["status"]
            except ScopeViolation:
                match_status = "missing"
            if match_status != "running":
                self.control.update_harness_run(
                    str(run["run_id"]), desired_status="stopped",
                )
                self.stop_run(str(run["run_id"]))
                stopped += 1
                continue
            if run["desired_status"] == "stopped":
                self.stop_run(str(run["run_id"]))
                stopped += 1
                continue
            observed = self.status(str(run["run_id"]))["observed"]
            if observed.get("running"):
                now = time.time()
                metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
                last_sample = float(metadata.get("semantic_sample_unix") or 0)
                if now - last_sample < 30:
                    self.control.update_harness_run(str(run["run_id"]), heartbeat=True)
                    continue
                progress = self.worker_manager.semantic_progress(str(run["instance_id"]))
                if not progress.get("available"):
                    if self._observe_bridge_unavailable(run, metadata, progress, now):
                        errors += 1
                        operator_required += 1
                    continue
                fingerprint = progress.get("meaningful_fingerprint")
                previous_fingerprint = metadata.get("semantic_fingerprint")
                progress_changed = bool(
                    progress.get("available") and fingerprint
                    and previous_fingerprint and fingerprint != previous_fingerprint
                )
                progress_since = float(metadata.get("semantic_progress_unix") or now)
                if progress_changed or not previous_fingerprint:
                    progress_since = now
                telemetry = metadata.get("semantic_telemetry") \
                    if isinstance(metadata.get("semantic_telemetry"), dict) else {}
                last_telemetry = float(metadata.get("semantic_telemetry_unix") or 0)
                if now - last_telemetry >= 60:
                    try:
                        sample = self.telemetry(str(run["run_id"]))
                        if isinstance(sample.get("telemetry"), dict):
                            telemetry = sample["telemetry"]
                            last_telemetry = now
                    except (DockerError, StoreError, ValueError, json.JSONDecodeError):
                        pass
                baseline = metadata.get("semantic_baseline_telemetry") \
                    if isinstance(metadata.get("semantic_baseline_telemetry"), dict) else {}
                if progress_changed or not baseline:
                    baseline = dict(telemetry)
                generated = (
                    int(telemetry.get("output_tokens") or 0)
                    + int(telemetry.get("reasoning_tokens") or 0)
                    - int(baseline.get("output_tokens") or 0)
                    - int(baseline.get("reasoning_tokens") or 0)
                )
                calls = int(telemetry.get("api_calls") or 0) - int(baseline.get("api_calls") or 0)
                metadata_update = {
                    "semantic_sample_unix": now,
                    "semantic_fingerprint": fingerprint or previous_fingerprint,
                    "semantic_progress_unix": progress_since,
                    "semantic_progress": progress,
                    "semantic_telemetry": telemetry,
                    "semantic_telemetry_unix": last_telemetry,
                    "semantic_baseline_telemetry": baseline,
                    "semantic_unavailable_since_unix": None,
                    "semantic_unavailable_samples": 0,
                    "semantic_unavailable_reason": None,
                }
                stall_seconds = min(max(int(
                    run["restart_policy"].get("semantic_stall_seconds", 360)
                ), 120), 1800)
                stalled = bool(
                    progress.get("available") and previous_fingerprint
                    and fingerprint == previous_fingerprint
                    and now - progress_since >= stall_seconds
                    and (generated >= 4096 or calls >= 2)
                )
                if stalled:
                    recovery_count = int(metadata.get("semantic_stall_recoveries") or 0) + 1
                    recovery_limit = min(max(int(
                        run["restart_policy"].get("semantic_stall_recovery_limit", 2)
                    ), 0), 10)
                    incident = self.control.record_supervision_incident(
                        str(run["instance_id"]), "harness_semantic_stall",
                        "open" if recovery_count <= recovery_limit else "operator_required",
                        {
                            "run_id": run["run_id"], "stall_seconds": now - progress_since,
                            "generated_tokens_without_progress": generated,
                            "api_calls_without_progress": calls, "progress": progress,
                            "recovery_count": recovery_count,
                        },
                    )
                    if recovery_count > recovery_limit:
                        container_name = str(run.get("container_name") or "")
                        if container_name:
                            try:
                                self.docker.stop_container(container_name, timeout=10)
                                self.docker.remove_container(container_name)
                            except DockerNotFound:
                                pass
                        self.control.update_harness_run(
                            str(run["run_id"]), status="error", desired_status="stopped",
                            last_error="harness_semantic_stall",
                            metadata_update={**metadata_update,
                                             "semantic_stall_recoveries": recovery_count,
                                             "supervision_incident": incident},
                        )
                        errors += 1
                        operator_required += 1
                        continue
                    container_name = str(run.get("container_name") or "")
                    if container_name:
                        try:
                            self.docker.stop_container(container_name, timeout=10)
                            self.docker.remove_container(container_name)
                        except DockerNotFound:
                            pass
                    self.control.update_harness_run(
                        str(run["run_id"]), status="restarting", increment_restart=True,
                        last_error="harness_semantic_stall_auto_recovery",
                        metadata_update={**metadata_update,
                                         "semantic_stall_recoveries": recovery_count,
                                         "supervision_incident": incident,
                                         "semantic_progress_unix": now,
                                         "semantic_baseline_telemetry": dict(telemetry)},
                    )
                    self.start_run(str(run["run_id"]))
                    restarted += 1
                    continue
                self.control.update_harness_run(
                    str(run["run_id"]), heartbeat=True,
                    metadata_update=metadata_update,
                )
                continue
            # Avoid a tight create/exit loop when a provider is unavailable or
            # a prompt ends immediately. The supervisor runs every ten seconds;
            # this guard also makes manual rapid reconciliation harmless.
            if time.time() - float(run.get("updated_unix") or 0) < 5.0:
                continue
            exit_code = int(observed.get("exit_code") or 0)
            policy = run["restart_policy"]
            if exit_code == 0:
                progress = self.worker_manager.semantic_progress(str(run["instance_id"]))
                metadata = run.get("metadata") \
                    if isinstance(run.get("metadata"), dict) else {}
                # An exited CLI may report zero after a runtime-context error.
                # It is not a playable yield until a fresh native observation
                # succeeds. Reuse the live-outage deadline without spawning
                # another provider episode or resetting the outage counters.
                if not progress.get("available"):
                    if self._observe_bridge_unavailable(run, metadata, progress, time.time()):
                        errors += 1
                        operator_required += 1
                    continue
                started = metadata.get("attempt_started_progress") \
                    if isinstance(metadata.get("attempt_started_progress"), dict) else {}
                comparable = bool(started.get("available") and progress.get("available"))
                marker_fields = (
                    "match_id", "session_id", "turn", "year", "phase",
                    "meaningful_fingerprint",
                    "game_completed", "final_score_completed",
                )
                advanced = comparable and any(
                    started.get(field) != progress.get(field) for field in marker_fields
                )
                no_progress = int(metadata.get("consecutive_clean_yields_without_progress") or 0)
                no_progress = 0 if advanced or not comparable else no_progress + 1
                continuation_count = int(metadata.get("continuation_count") or 0) + 1
                yield_metadata = {
                    "continuation_count": continuation_count,
                    "consecutive_clean_yields_without_progress": no_progress,
                    "last_clean_yield_progress": progress,
                    "last_clean_yield_advanced": advanced,
                    "last_clean_yield_unix": time.time(),
                    "semantic_unavailable_since_unix": None,
                    "semantic_unavailable_samples": 0,
                }
                if progress.get("final_score_completed") is True:
                    self._journal_run_event(
                        run, "agent.episode_ended", {
                            "run_id": run["run_id"], "outcome": "match_completed",
                            "exit_code": 0, "progress": progress,
                        }, commit_reason="Complete autonomous campaign",
                    )
                    self.control.update_harness_run(
                        str(run["run_id"]), status="completed", exit_code=0,
                        metadata_update=yield_metadata,
                    )
                    continue
                threshold = min(max(int(
                    policy.get("max_clean_yields_without_progress", 3)
                ), 1), 20)
                if comparable and no_progress >= threshold:
                    detail = {
                        "run_id": run["run_id"], "clean_yield_count": continuation_count,
                        "consecutive_without_progress": no_progress,
                        "threshold": threshold, "progress": progress,
                    }
                    incident = self.control.record_supervision_incident(
                        str(run["instance_id"]), "harness_clean_yield_no_progress",
                        "operator_required", detail,
                    )
                    self._journal_run_event(
                        run, "agent.episode_ended", {
                            "run_id": run["run_id"],
                            "outcome": "clean_yield_without_progress",
                            "exit_code": 0, "progress": progress,
                            "consecutive_without_progress": no_progress,
                        }, commit_reason="Pause autonomous episode",
                    )
                    self.control.update_harness_run(
                        str(run["run_id"]), status="error", desired_status="stopped",
                        exit_code=0, last_error="harness_clean_yield_no_progress",
                        metadata_update={**yield_metadata, "supervision_incident": incident},
                    )
                    errors += 1
                    operator_required += 1
                    continue
                if policy.get("restart_on_clean_exit") is True:
                    self._journal_run_event(
                        run, "agent.episode_ended", {
                            "run_id": run["run_id"], "outcome": "clean_yield",
                            "exit_code": 0, "progress": progress,
                            "advanced": advanced,
                        }, commit_reason="Yield autonomous episode",
                    )
                    self.control.update_harness_run(
                        str(run["run_id"]), status="restarting", exit_code=0,
                        metadata_update=yield_metadata,
                    )
                    self.start_run(str(run["run_id"]))
                    continued += 1
                    continue
                self.control.update_harness_run(
                    str(run["run_id"]), status="error", exit_code=0,
                    last_error="managed_harness_clean_exit_while_match_active",
                    metadata_update=yield_metadata,
                )
                errors += 1
                continue
            restart_allowed = (
                exit_code != 0 and policy.get("restart_on_error") is True
            ) and int(run["restart_count"]) < int(policy.get("restart_limit", 0))
            self._journal_run_event(
                run, "agent.episode_ended", {
                    "run_id": run["run_id"], "outcome": "process_error",
                    "exit_code": exit_code,
                    "restart_allowed": restart_allowed,
                }, commit_reason=("Recover autonomous episode" if restart_allowed
                                  else "Pause autonomous episode"),
            )
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
                "continued": continued, "stopped": stopped, "errors": errors,
                "operator_required": operator_required}
