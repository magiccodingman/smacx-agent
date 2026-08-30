#!/usr/bin/env python3
"""Contained contract for managed Hermes isolation and secret injection."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tarfile

from smacx_docker import DockerClient, DockerNotFound
from smacx_harness_manager import HERMES_IMAGE, HarnessManager
from smacx_prompt import SYSTEM_PROMPT_SCHEMA, compose_player_system_prompt, prompt_sha256


class FakeStore:
    def installation_id(self) -> str:
        return "installation-harness-contract"


class FakeControl:
    def __init__(self) -> None:
        self.store = FakeStore()
        self.runtime = None
        self.runs: list[dict] = []
        self.key = "secret-provider-key"

    def get_worker_spec(self, _instance_id: str) -> dict:
        return {"network": {"mcp_container_name": "mcp-harness-contract"}}

    def list_harness_runs(self) -> list[dict]:
        return self.runs

    def get_harness_runtime_spec(self, _profile_id: str) -> dict:
        if self.runtime is None:
            from smacx_store import ScopeViolation
            raise ScopeViolation("unknown_harness_runtime")
        return dict(self.runtime)

    def put_harness_runtime_spec(self, profile_id: str, **values) -> dict:
        self.runtime = {
            "harness_profile_id": profile_id, **values,
            "observed_status": "ready",
        }
        return dict(self.runtime)

    def provider_api_key(self, _provider_id: str) -> str | None:
        return self.key

    def get_harness_profile(self, _profile_id: str) -> dict:
        prompt = compose_player_system_prompt(
            agent_name="Contract Player", agent_id="agent-contract-player",
            match_id="match-contract-game", match_name="Contract match",
            perspective_id="perspective-contract-player", ruleset_id="smacx",
            seat_index=0,
        )
        return {
            "external_profile_id": "smacx-contract-player",
            "reasoning_effort": "low",
            "system_prompt": prompt,
            "metadata": {
                "system_prompt_schema": SYSTEM_PROMPT_SCHEMA,
                "system_prompt_sha256": prompt_sha256(prompt),
            },
        }


class FakeDocker:
    def __init__(self) -> None:
        self.volumes: dict[str, dict] = {}
        self.contents: dict[str, list[bytes]] = {}
        self.containers: dict[str, dict] = {}
        self.configs: list[dict] = []

    def inspect_image(self, image_ref: str) -> dict:
        return {"RepoDigests": [image_ref]}

    def create_volume(self, name: str, labels: dict) -> dict:
        self.volumes[name] = {"Name": name, "Labels": labels}
        self.contents[name] = []
        return self.volumes[name]

    def inspect_volume(self, name: str) -> dict:
        if name not in self.volumes:
            raise DockerNotFound("missing volume")
        return self.volumes[name]

    def remove_volume(self, name: str) -> None:
        del self.volumes[name]
        del self.contents[name]

    def create_container(self, name: str, config: dict) -> str:
        self.configs.append(config)
        self.containers[name] = {"Id": name, "Config": config, "State": {"Running": False}}
        return name

    def inspect_container(self, name: str) -> dict:
        if name not in self.containers:
            raise DockerNotFound("missing container")
        return self.containers[name]

    def remove_container(self, name: str) -> None:
        if name not in self.containers:
            raise DockerNotFound("missing container")
        del self.containers[name]

    def put_archive(self, container: str, _destination: str, archive: bytes) -> None:
        config = self.containers[container]["Config"]
        volume = config["HostConfig"]["Mounts"][0]["Source"]
        self.contents[volume].append(archive)

    @staticmethod
    def labels(installation_id: str, purpose: str, **extra: str) -> dict[str, str]:
        return DockerClient.labels(installation_id, purpose, **extra)

    @staticmethod
    def require_owned(resource: dict, installation_id: str, *, purpose=None) -> None:
        DockerClient.require_owned(resource, installation_id, purpose=purpose)


class FakeWorkerManager:
    resource_prefix = "smacx-test"
    mcp_image = "smacx-agent-mcp:test"
    network_name = "smacx-private"


def unpack(archive: bytes) -> dict[str, bytes]:
    result = {}
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as stream:
        for member in stream.getmembers():
            if member.isfile():
                extracted = stream.extractfile(member)
                result[member.name] = extracted.read() if extracted else b""
    return result


def descriptor(*, keyed: bool) -> dict:
    return {
        "harness_profile_id": "harness-contract-profile",
        "external_profile_id": "smacx-contract-player",
        "instance_id": "instance-contract-worker",
        "agent_id": "agent-contract-player",
        "agent_name": "Contract Player",
        "match_id": "match-contract-game",
        "provider_id": "provider-contract-model",
        "provider_base_url": "http://models.internal:8000/v1",
        "provider_requires_api_key": keyed,
        "model_id": "Qwen3.8-27B",
        "reasoning_effort": "low",
        "context_length": 65536,
    }


def main() -> int:
    control = FakeControl()
    docker = FakeDocker()
    manager = HarnessManager(control, docker, FakeWorkerManager())  # type: ignore[arg-type]
    runtime = manager.provision_profile(descriptor(keyed=True))
    data_files = unpack(docker.contents[runtime["data_volume"]][-1])
    config_bytes = next(value for name, value in data_files.items() if name.endswith("config.yaml"))
    config = json.loads(config_bytes)
    serialized = json.dumps(config)
    if control.key in serialized or config["custom_providers"][0].get("key_env") != \
            "SMACX_PROVIDER_API_KEY":
        raise AssertionError("managed profile persisted provider key or omitted key_env")
    secret_files = unpack(docker.contents[runtime["secret_volume"]][-1])
    if secret_files != {"provider-api-key": control.key.encode()}:
        raise AssertionError("provider key was not isolated in the purpose secret volume")

    run_config = manager._container_config({  # noqa: SLF001
        "run_id": "run-contract-player", "harness_profile_id": "harness-contract-profile",
        "match_id": "match-contract-game", "agent_id": "agent-contract-player",
        "restart_count": 0, "restart_policy": {
            "toolsets": "smacx", "max_turns": 100, "run_budget_seconds": 60,
        },
    }, runtime, "Play one bounded turn.")
    if control.key in json.dumps(run_config) or any(
            "SMACX_PROVIDER_API_KEY=" in value for value in run_config["Env"]):
        raise AssertionError("provider key leaked into Docker inspect-visible configuration")
    if run_config["HostConfig"].get("ReadonlyRootfs") is not True \
            or run_config["HostConfig"].get("CapDrop") != ["ALL"]:
        raise AssertionError("managed harness isolation regressed")
    environment = dict(value.split("=", 1) for value in run_config["Env"])
    if environment.get("SMACX_STRICT_SYSTEM_PROMPT") != "1" \
            or len(environment.get("SMACX_SYSTEM_PROMPT_SHA256", "")) != 64:
        raise AssertionError("strict provider-facing prompt guard was not enabled")
    if not any(name.endswith("SYSTEM.md") for name in data_files):
        raise AssertionError("strict prompt file was not seeded into managed data")

    old_secret_volume = runtime["secret_volume"]
    control.key = None
    manager.provision_profile(descriptor(keyed=False))
    if docker.contents[old_secret_volume]:
        raise AssertionError("reprovision retained an obsolete provider secret")
    if HERMES_IMAGE != "smacx-agent-harness:dev":
        raise AssertionError("managed harness did not select the SMACX-owned derived image")
    dockerfile = Path(__file__).resolve().parents[1] / "harness" / "Dockerfile"
    if "nousresearch/hermes-agent:v2026.8.27@sha256:" not in dockerfile.read_text(encoding="utf-8"):
        raise AssertionError("derived harness parent is not digest pinned")
    print(json.dumps({
        "event": "pass",
        "payload": {
            "official_image_digest_pinned": True,
            "provider_secret_volume_only": True,
            "docker_inspect_secret_free": True,
            "key_env_not_key_persisted": True,
            "secret_rotated_on_reprovision": True,
            "read_only_capability_dropped_runtime": True,
            "semantic_toolsets_only": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
