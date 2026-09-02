"""Docker-backed lifecycle manager for isolated Linux SMACX game workers."""

from __future__ import annotations

from io import BytesIO
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import tarfile
import time
from typing import Any, Mapping
import uuid

from smacx_control import ControlPlane
from smacx_controller import BridgeUnavailable, bridge_request_to
from smacx_docker import DockerClient, DockerError, DockerNotFound
from smacx_game_settings import (
    LAN_RULE_FIELDS, game_settings_environment, normalize_game_settings,
    normalize_lan_game_settings,
)
from smacx_store import InvalidRecord, MemoryScope, ScopeViolation, StoreError


RESOURCE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,254}$")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
UNSAFE_LOG_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SAFE_HOST_PATH_LIMIT = 4096
LAN_PROFILES = {
    "tiny_citizen", "small_easy", "standard_librarian",
    "large_thinker", "huge_transcend",
}
NATIVE_RESOLUTION_PROFILES: dict[str, tuple[int, int]] = {
    "800x600": (800, 600),
    "1024x768": (1024, 768),
    "1280x720": (1280, 720),
    "1280x800": (1280, 800),
    "1440x900": (1440, 900),
    "1600x900": (1600, 900),
    "1600x1200": (1600, 1200),
    "1920x1080": (1920, 1080),
    "1920x1200": (1920, 1200),
    "2560x1080": (2560, 1080),
    "2560x1440": (2560, 1440),
    "2560x1600": (2560, 1600),
    "3440x1440": (3440, 1440),
    "3840x1600": (3840, 1600),
    "3840x2160": (3840, 2160),
    "5120x1440": (5120, 1440),
}


def stream_bitrate_kbps(width: int, height: int) -> int:
    """A bounded H.264 target for an old, mostly-static strategy UI."""
    pixels = width * height
    if pixels <= 800 * 600:
        return 2200
    if pixels <= 1280 * 800:
        return 3500
    if pixels <= 1920 * 1200:
        return 5500
    if pixels <= 2560 * 1600:
        return 8000
    if pixels <= 3840 * 2160:
        return 12000
    return 14000


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
                 mcp_image: str = "smacx-agent-control:dev",
                 network_name: str | None = None,
                 control_data_volume: str | None = None,
                 directx_redist_host_path: str | None = None,
                 view_publish_ip: str = "127.0.0.1") -> None:
        self.control = control
        self.store = control.store
        self.docker = docker
        if not RESOURCE_NAME.fullmatch(worker_image.replace(":", "-", 1)):
            # Image references may contain one registry slash and colon. Keep
            # the check intentionally conservative for the initial local build.
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,400}(?::[A-Za-z0-9_.-]{1,100})?", worker_image):
                raise InvalidRecord("invalid_worker_image")
        self.worker_image = worker_image
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,400}(?::[A-Za-z0-9_.-]{1,100})?", mcp_image):
            raise InvalidRecord("invalid_mcp_image")
        self.mcp_image = mcp_image
        self.network_name = network_name
        if control_data_volume is not None and not RESOURCE_NAME.fullmatch(control_data_volume):
            raise InvalidRecord("invalid_control_data_volume")
        self.control_data_volume = control_data_volume
        self.directx_redist_host_path = (
            _host_path(directx_redist_host_path, "directx_redist_path")
            if directx_redist_host_path else None
        )
        if view_publish_ip not in {"127.0.0.1", "0.0.0.0"}:
            raise InvalidRecord("invalid_view_publish_ip")
        self.view_publish_ip = view_publish_ip
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
            "mcp_image": self.mcp_image,
            "network_name": self.network_name,
            "managed_mcp_configured": bool(self.control_data_volume),
            "directplay_source_configured": True,
        }

    def ensure_bundled_runtime(self) -> dict[str, Any]:
        """Register the compatibility stack already sealed in the worker image.

        Runtime selection is an implementation detail of this installation,
        not a lobby setting.  The stable record preserves fingerprints in
        match history while allowing an image rebuild to refresh the digest.
        """
        image = self.docker.inspect_image(self.worker_image)
        image_id = str(image.get("Id") or "")
        fingerprint = image_id.removeprefix("sha256:")
        if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            raise WorkerManagerError("worker_image_fingerprint_unavailable")
        return self.control.register_runtime(
            "Docker-managed GE-Proton + DirectPlay", "image", self.worker_image,
            runtime_id="runtime-bundled-proton", runtime_kind="proton",
            content_fingerprint=fingerprint, status="ready",
            metadata={
                "managed": True,
                "bundled_in_worker_image": True,
                "directplay_bundled": True,
                "proton_source": "digest-pinned-upstream-release",
                "proton_distribution": "GE-Proton10-34",
                "operator_selectable": False,
            },
        )

    def ensure_prepared_worker_image(self, game_source_id: str) -> str:
        """Return the installation-local image shared by every seat.

        Preparation imports the operator's game once and initializes one
        DirectPlay-ready Proton prefix once. The resulting image stays local
        to this Docker Engine and is content-addressed by source and worker
        image fingerprints; no game files are placed in project artifacts.
        """
        source = self.control.get_game_source(game_source_id)
        base = self.docker.inspect_image(self.worker_image)
        base_id = str(base.get("Id") or "").removeprefix("sha256:")
        source_hash = str(
            source.get("metadata", {}).get("source_tree_sha256")
            or source.get("executable_sha256") or ""
        )
        if not re.fullmatch(r"[a-f0-9]{64}", base_id) or \
                not re.fullmatch(r"[a-f0-9]{64}", source_hash):
            raise WorkerManagerError("prepared_image_fingerprint_unavailable")
        repository = "smacx-agent-prepared"
        tag = f"{self.resource_prefix.removeprefix('smacx-')}-{source_hash[:12]}-{base_id[:12]}"
        image_ref = f"{repository}:{tag}"
        # Minimal contract-test doubles intentionally model only the original
        # Docker surface. Production always uses DockerClient.
        if not hasattr(self.docker, "commit_container"):
            return self.worker_image
        try:
            existing = self.docker.inspect_image(image_ref)
            self.docker.require_owned(
                existing, self.installation_id, purpose="prepared-worker-image",
            )
            return image_ref
        except DockerNotFound:
            pass
        name = self._name("prepare", f"{source_hash}-{base_id}")
        identifier: str | None = None
        try:
            identifier = self.docker.create_container(name, {
                "Image": self.worker_image,
                "Env": [
                    "SMACX_PREPARE_BASE=1",
                    "SMACX_GAME_SOURCE=/game-source",
                    "SMACX_PROTON_BIN=/opt/proton/proton",
                    "SMACX_REQUIRE_DIRECTPLAY=1",
                ],
                "Tty": True,
                "Labels": self._labels("prepared-image-builder", **{
                    "io.smacx.game-source": game_source_id,
                    "io.smacx.source-sha256": source_hash,
                    "io.smacx.base-image": base_id,
                }),
                "HostConfig": {
                    "NetworkMode": "none", "ReadonlyRootfs": False,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges"],
                    "Tmpfs": {
                        "/tmp": "rw,nosuid,nodev,size=512m,mode=1777",
                        "/run": "rw,nosuid,nodev,size=32m,mode=0755",
                    },
                    "Mounts": [{
                        "Type": "bind", "Source": str(source["host_path"]),
                        "Target": "/game-source", "ReadOnly": True,
                        "BindOptions": {"Propagation": "rprivate"},
                    }],
                },
            })
            self.docker.start_container(identifier)
            stopped = self.docker.wait_container(identifier, timeout=600.0)
            exit_code = int(stopped.get("State", {}).get("ExitCode", -1))
            if exit_code:
                logs = _clean_log(self.docker.container_logs(identifier, tail=200))[-4000:]
                raise WorkerManagerError(f"prepared_image_build_failed:{exit_code}:{logs}")
            self.docker.commit_container(
                identifier, repository, tag,
                labels=self._labels("prepared-worker-image", **{
                    "io.smacx.game-source": game_source_id,
                    "io.smacx.source-sha256": source_hash,
                    "io.smacx.base-image": base_id,
                }),
            )
            committed = self.docker.inspect_image(image_ref)
            self.docker.require_owned(
                committed, self.installation_id, purpose="prepared-worker-image",
            )
            return image_ref
        finally:
            if identifier:
                self._cleanup_container(identifier, "prepared-image-builder")

    def _native_request(self, instance_id: str, operation: str, *,
                        timeout: float = 8.0, **arguments: Any) -> dict[str, Any]:
        spec = self.control.get_worker_spec(instance_id)
        container = self.docker.inspect_container(spec["container_name"])
        self.docker.require_owned(container, self.installation_id, purpose="game-worker")
        if not container.get("State", {}).get("Running"):
            raise WorkerManagerError("game_worker_not_running")
        networks = container.get("NetworkSettings", {}).get("Networks", {})
        network = networks.get(self.network_name) if self.network_name and isinstance(networks, Mapping) else None
        if not isinstance(network, Mapping):
            candidates = [item for item in networks.values() if isinstance(item, Mapping)] \
                if isinstance(networks, Mapping) else []
            network = candidates[0] if len(candidates) == 1 else None
        host = network.get("IPAddress") if isinstance(network, Mapping) else None
        if not isinstance(host, str) or not host:
            raise WorkerManagerError("game_worker_network_address_unavailable")
        token = self.control.vault.read(
            str(spec["bridge_secret_id"]), purpose=f"worker.{instance_id}.bridge_token",
        )
        try:
            return bridge_request_to(
                host, 47814, token, operation, timeout=timeout, **arguments,
            )
        except BridgeUnavailable as exc:
            raise WorkerManagerError("game_worker_bridge_unavailable") from exc

    def _wait_native(self, instance_id: str, operation: str, predicate, *,
                     timeout: float, poll_seconds: float = 0.25,
                     context: str | None = None,
                     **arguments: Any) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        latest: dict[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                latest = self._native_request(instance_id, operation, **arguments)
            except WorkerManagerError as exc:
                # A stock multiplayer checkpoint is deserialized on the game
                # thread when its setup packet arrives. During that bounded
                # interval the authenticated bridge can time out even though
                # the worker and DirectPlay session remain healthy. Retry only
                # this transport condition; ownership/lifecycle errors remain
                # immediate failures.
                if str(exc) != "game_worker_bridge_unavailable":
                    raise
                latest = {"ok": False, "error": str(exc)}
                time.sleep(poll_seconds)
                continue
            if predicate(latest):
                return latest
            time.sleep(poll_seconds)
        detail = json.dumps(latest, separators=(",", ":"), default=str)[:1800]
        stage = f"_{context}" if context else ""
        raise WorkerManagerError(f"native_{operation}{stage}_timeout:{detail}")

    def portal_chat(self, instance_id: str, *, action: str = "list",
                    text: str | None = None, recipient_faction_id: int = 0,
                    client_message_id: str | None = None,
                    after_sequence: int = 0) -> dict[str, Any]:
        """Read or send native chat for one exact managed human seat."""
        spec = self.control.get_worker_spec(instance_id)
        if spec.get("network", {}).get("controller_kind") != "human":
            raise WorkerManagerError("portal_chat_requires_managed_human_seat")
        listed = self._native_request(
            instance_id, "semantic_chat", action="list",
            after_sequence=max(int(after_sequence), 0), timeout=20.0,
        )
        if action == "list":
            return listed
        if action != "send" or not isinstance(text, str):
            raise InvalidRecord("invalid_portal_chat_action")
        identity = listed.get("identity")
        if not isinstance(identity, Mapping):
            raise WorkerManagerError("native_chat_identity_unavailable")
        message_id = client_message_id or _new_id("portal-chat")
        return self._native_request(
            instance_id, "semantic_chat", action="send", text=text,
            recipient_faction_id=int(recipient_faction_id),
            client_message_id=message_id,
            match_id=identity.get("match_id"), session_id=identity.get("session_id"),
            timeout=20.0,
        )

    def human_ui_state(self, instance_id: str) -> dict[str, Any]:
        """Read the exact native root-MENU state for a managed human seat.

        This operation deliberately remains outside every MCP capability.  It
        exists only so the portal can decorate an interactive human stream.
        """
        spec = self.control.get_worker_spec(instance_id)
        network = spec.get("network", {})
        if network.get("controller_kind") != "human" \
                or network.get("view_mode") != "interactive":
            raise WorkerManagerError("human_ui_state_requires_interactive_human")
        state = self._native_request(instance_id, "human_ui_state", timeout=5.0)
        quit_intercepted = False
        if state.get("popup_label") == "REALLYQUIT":
            prevented = self._native_request(
                instance_id, "human_ui_control", action="cancel_native_quit", timeout=5.0,
            )
            quit_intercepted = prevented.get("prevented") is True
            state = self._native_request(instance_id, "human_ui_state", timeout=5.0)
        profile_id = str(network.get("resolution_profile") or "1280x800")
        width, height = NATIVE_RESOLUTION_PROFILES.get(profile_id, (1280, 800))
        return {**state, "instance_id": instance_id,
                "resolution_profile_id": profile_id,
                "native_width": width, "native_height": height,
                "stream_bitrate_kbps": int(network.get("stream_bitrate_kbps")
                                           or stream_bitrate_kbps(width, height)),
                "stream_encoder": "h264enc",
                "native_quit_intercepted": quit_intercepted}

    def portal_group_chat(
        self, instance_id: str, *, action: str, group_id: str = "",
        display_name: str = "", member_faction_ids: list[int] | None = None,
        response: str = "", text: str = "",
    ) -> dict[str, Any]:
        """Human control-center access to the shared logical group-chat store."""
        spec = self.control.get_worker_spec(instance_id)
        if spec.get("network", {}).get("controller_kind") != "human":
            raise WorkerManagerError("portal_group_chat_requires_managed_human_seat")
        scope = MemoryScope(str(spec["match_id"]), str(spec["agent_id"]),
                            str(spec["perspective_id"]))
        native = self._native_request(instance_id, "semantic_chat", action="list",
                                      after_sequence=0, timeout=20.0)
        identity = native.get("identity")
        participants = [item for item in native.get("participants", [])
                        if isinstance(item, Mapping)]
        local = next((item for item in participants if item.get("local") is True), None)
        if not isinstance(identity, Mapping) or local is None:
            raise WorkerManagerError("native_chat_identity_unavailable")
        local_faction_id = int(local["faction_id"])
        match_id = str(identity.get("match_id") or spec["match_id"])
        session_id = str(identity.get("session_id") or "")
        if action == "list":
            return {"ok": True, "groups": self.store.list_chat_groups(
                scope, local_faction_id), "participants": participants,
                "logical_delivery": True}
        if action == "create":
            requested = {int(item) for item in (member_faction_ids or [])}
            requested.add(local_faction_id)
            selected = [item for item in participants
                        if int(item.get("faction_id", -1)) in requested]
            if {int(item["faction_id"]) for item in selected} != requested:
                raise InvalidRecord("unknown_chat_group_member")
            if any(item.get("local") is not True and
                   item.get("private_eligible") is not True for item in selected):
                raise InvalidRecord("chat_group_requires_mutual_commlink")
            group = self.store.create_chat_group(
                scope, display_name, local_faction_id,
                [{"faction_id": int(item["faction_id"]),
                  "display_name": item.get("player_name") or item.get("faction_name")
                    or f"Faction {item['faction_id']}",
                  "faction_name": item.get("faction_name")}
                 for item in selected],
            )
            deliveries = []
            for faction_id in sorted(requested - {local_faction_id}):
                sent = self.portal_chat(
                    instance_id, action="send", recipient_faction_id=faction_id,
                    client_message_id=f"{group['group_id']}-invite-{faction_id}",
                    text=(f"[SMACX group invitation: {group['display_name']}; "
                          f"id {group['group_id']}. Accept or reject in group chat.]"),
                )
                deliveries.append({"recipient_faction_id": faction_id,
                                   "delivered": bool(sent.get("ok"))})
            return {"ok": True, "group": group, "deliveries": deliveries}
        if action in {"respond", "leave"}:
            desired = "left" if action == "leave" else response
            group = self.store.respond_chat_group(
                scope, group_id, local_faction_id, desired,
            )
            creator = int(group["created_by_faction_id"])
            if creator != local_faction_id:
                self.portal_chat(
                    instance_id, action="send", recipient_faction_id=creator,
                    client_message_id=(
                        f"{group_id}-response-{local_faction_id}-{group['version']}"
                    ),
                    text=f"[SMACX group {group_id}: faction {local_faction_id} {desired}.]",
                )
            return {"ok": True, "group": group}
        if action != "send":
            raise InvalidRecord("invalid_group_chat_action")
        message = self.store.begin_group_message(
            scope, group_id, local_faction_id, text,
        )
        prefix = f"[Group: {message['group']['display_name']}] "
        deliveries = []
        for faction_id in message["recipients"]:
            sent = self.portal_chat(
                instance_id, action="send", recipient_faction_id=faction_id,
                client_message_id=f"{message['logical_message_id']}-f{faction_id}",
                text=prefix + message["content"],
            )
            delivered = bool(sent.get("ok") and sent.get("sent"))
            event = sent.get("event") if isinstance(sent.get("event"), Mapping) else {}
            self.store.complete_group_delivery(
                message["logical_message_id"], faction_id, delivered=delivered,
                native_message_uid=(str(event.get("client_message_id"))
                                    if event.get("client_message_id") else None),
            )
            deliveries.append({"recipient_faction_id": faction_id,
                               "status": "delivered" if delivered else "failed"})
        return {"ok": all(item["status"] == "delivered" for item in deliveries),
                "logical_message_id": message["logical_message_id"],
                "group_id": group_id, "content": message["content"],
                "deliveries": deliveries, "logical_delivery": True,
                "native_echoes_collapsed": True, "match_id": match_id,
                "session_id": session_id}

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

    def validate_game_source(self, host_path: str, *, display_name: str = "Alien Crossfire",
                             game_source_id: str | None = None) -> dict[str, Any]:
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
        registered: dict[str, Any] | None = None
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
            registered = self.control.register_game_source(
                display_name, host_path, str(source["terranx_sha256"]),
                game_source_id=game_source_id,
                metadata={
                    "validated_by": "container", "worker_image": self.worker_image,
                    "source_tree_sha256": str(source["source_tree_sha256"]),
                },
            )
        finally:
            self._cleanup_container(identifier, "game-source-inspector")
        if registered is None:
            raise WorkerManagerError("game_source_registration_failed")
        scenario_catalog = self.list_scenarios(registered["game_source_id"])
        updated = self.control.register_game_source(
            display_name, host_path, str(registered["executable_sha256"]),
            game_source_id=str(registered["game_source_id"]),
            metadata={
                "validated_by": "container", "worker_image": self.worker_image,
                "source_tree_sha256": registered["metadata"]["source_tree_sha256"],
                "reference_knowledge": {
                    "status": "managed-by-knowledge-service",
                    "distributed": False,
                },
                "scenario_count": len(scenario_catalog["scenarios"]),
            },
        )
        updated["reference_knowledge"] = {
            "status": "managed-by-knowledge-service", "distributed": False,
        }
        updated["scenario_catalog"] = scenario_catalog
        return updated

    def list_scenarios(self, game_source_id: str) -> dict[str, Any]:
        """Return only catalogued relative .SC identifiers from a legal source."""
        source = self.control.get_game_source(game_source_id)
        host_path = _host_path(str(source["host_path"]), "game_source_host_path")
        self.docker.inspect_image(self.worker_image)
        # Dashboard clients may request the same catalog for solo and LAN
        # selectors concurrently. Inspector names must therefore be unique;
        # ownership labels, not names, define the cleanup boundary.
        name = self._name("scenarios", f"{game_source_id}-{uuid.uuid4().hex}")
        identifier = self.docker.create_container(name, {
            "Image": self.worker_image,
            "Entrypoint": ["python3", "/opt/smacx/list_scenarios.py"],
            "Cmd": [], "Tty": True,
            "Labels": self._labels("scenario-catalog", **{"io.smacx.game-source": game_source_id}),
            "HostConfig": {
                "NetworkMode": "none", "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Tmpfs": {"/tmp": "rw,nosuid,nodev,noexec,size=32m"},
                "Mounts": [{
                    "Type": "bind", "Source": host_path, "Target": "/game-source",
                    "ReadOnly": True, "BindOptions": {"Propagation": "rprivate"},
                }],
            },
        })
        try:
            self.docker.start_container(identifier)
            finished = self.docker.wait_container(identifier, timeout=120)
            payload = self._last_json(self.docker.container_logs(identifier, tail=20))
            if finished.get("State", {}).get("ExitCode") != 0 \
                    or payload.get("schema") != "smacx.scenario-catalog.v1" \
                    or payload.get("terranx_sha256") != source["executable_sha256"] \
                    or not isinstance(payload.get("scenarios"), list):
                raise WorkerManagerError("scenario_catalog_failed")
            return payload
        finally:
            self._cleanup_container(identifier, "scenario-catalog")

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

    def _seed_secret_volume(self, volume: str, instance_id: str,
                            file_name: str, value: str) -> None:
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
            self.docker.put_archive(
                identifier, "/secrets", self._secret_archive(file_name, value),
            )
        finally:
            self._cleanup_container(identifier, "worker-secret-writer")

    def provision_worker(self, scope: MemoryScope, game_source_id: str, runtime_id: str, *,
                         autostart: Mapping[str, Any] | None = None,
                         view_enabled: bool = False,
                         view_mode: str = "view-only",
                         controller_kind: str = "agent",
                         resolution_profile: str = "1280x800") -> dict[str, Any]:
        self.store.require_scope(scope)
        if view_mode not in {"view-only", "interactive"}:
            raise InvalidRecord("invalid_worker_view_mode")
        if controller_kind not in {"agent", "human"}:
            raise InvalidRecord("invalid_worker_controller_kind")
        if controller_kind == "human" and not view_enabled:
            raise InvalidRecord("managed_human_requires_stream")
        if resolution_profile not in NATIVE_RESOLUTION_PROFILES:
            raise InvalidRecord("invalid_native_resolution_profile")
        source = self.control.get_game_source(game_source_id)
        runtime = self.control.get_runtime(runtime_id)
        if source["status"] != "validated" or runtime["status"] != "ready" \
                or runtime["storage_kind"] not in {"docker-volume", "image"}:
            raise WorkerManagerError("worker_assets_not_ready")
        normalized_autostart = self._autostart(autostart)
        scenario_id = normalized_autostart.get("scenario_id")
        if isinstance(scenario_id, str):
            catalog = self.list_scenarios(game_source_id)
            if scenario_id not in {
                    item.get("scenario_id") for item in catalog.get("scenarios", [])
                    if isinstance(item, Mapping)}:
                raise InvalidRecord("unknown_worker_scenario_id")
        if runtime["storage_kind"] == "docker-volume":
            runtime_volume = self.docker.inspect_volume(runtime["storage_ref"])
            self.docker.require_owned(runtime_volume, self.installation_id, purpose="proton-runtime")
        elif runtime["storage_ref"] != self.worker_image:
            raise WorkerManagerError("worker_runtime_image_mismatch")
        self.docker.inspect_image(self.worker_image)
        prepared_image = self.ensure_prepared_worker_image(game_source_id)
        instance_id = _new_id("instance")
        container_name = self._name("worker", instance_id)
        data_volume = self._name("data", instance_id)
        secret_volume = self._name("secret", instance_id)
        data_created = False
        secret_created = False
        bridge_secret_id: str | None = None
        view_secret_id: str | None = None
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
            self._seed_secret_volume(secret_volume, instance_id, "bridge-token", token)
            if view_enabled:
                view_password = secrets.token_urlsafe(24)
                view_only_password = secrets.token_urlsafe(24)
                view_secret = self.control.vault.put(
                    f"worker.{instance_id}.view_passwords", json.dumps({
                        "control": view_password,
                        "viewer": view_only_password,
                    }, separators=(",", ":")),
                )
                view_secret_id = str(view_secret["secret_id"])
                self._seed_secret_volume(
                    secret_volume, instance_id, "view-password", view_password,
                )
                self._seed_secret_volume(
                    secret_volume, instance_id, "view-only-password", view_only_password,
                )
            self.store.register_instance(
                instance_id=instance_id, worker_kind="container-linux", scope=scope,
                runtime_root=data_volume,
                metadata={"container_name": container_name, "managed": True},
            )
            worker = self.control.put_worker_spec(
                instance_id, game_source_id, runtime_id, prepared_image,
                container_name, data_volume, bridge_secret_id,
                autostart=normalized_autostart,
                network={
                    "name": self.network_name,
                    "secret_volume": secret_volume,
                    "view_enabled": bool(view_enabled),
                    "view_mode": view_mode,
                    "controller_kind": controller_kind,
                    "resolution_profile": resolution_profile,
                    "stream_bitrate_kbps": stream_bitrate_kbps(
                        *NATIVE_RESOLUTION_PROFILES[resolution_profile]
                    ),
                },
                view_secret_id=view_secret_id,
            )
            self.control.assign_instance_to_seat(
                scope.match_id, scope.agent_id, scope.perspective_id, instance_id,
            )
            return worker
        except Exception:
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE seat_assignments SET instance_id=NULL, updated_unix=? WHERE instance_id=?",
                    (time.time(), instance_id),
                )
                connection.execute("DELETE FROM worker_specs WHERE instance_id=?", (instance_id,))
                connection.execute(
                    "DELETE FROM instances WHERE instance_id=? AND status='available' "
                    "AND NOT EXISTS (SELECT 1 FROM sessions WHERE instance_id=?)",
                    (instance_id, instance_id),
                )
            if bridge_secret_id:
                self.control.vault.revoke(bridge_secret_id)
            if view_secret_id:
                self.control.vault.revoke(view_secret_id)
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
        nested_settings = supplied.get("game_settings")
        if nested_settings is not None and not isinstance(nested_settings, Mapping):
            raise InvalidRecord("invalid_worker_game_settings")
        settings_input = dict(nested_settings or {})
        settings_input.setdefault("world_size", int(supplied.get("world_size", 0)))
        settings_input.setdefault("blind_research", bool(supplied.get("blind_research", True)))
        game_settings = normalize_game_settings(
            settings_input,
            default_blind_research=bool(supplied.get("blind_research", True)),
        )
        result = {
            "enabled": bool(supplied.get("enabled", True)),
            "difficulty": int(supplied.get("difficulty", 0)),
            "world_size": int(game_settings["world_size"]),
            "faction_id": int(supplied.get("faction_id", 1)),
            "blind_research": bool(game_settings.get("blind_research", True)),
            "initial_research_priority": int(supplied.get("initial_research_priority", 1)),
            "narrative_ui": bool(supplied.get("narrative_ui", False)),
            "tutorial_ui": bool(supplied.get("tutorial_ui", False)),
            "game_settings": game_settings,
        }
        faction_roster = supplied.get("faction_roster")
        if faction_roster is not None:
            if not isinstance(faction_roster, list) or len(faction_roster) != 7 \
                    or len(set(faction_roster)) != 7 \
                    or any(not isinstance(choice, int) or not 0 <= choice <= 13
                           for choice in faction_roster):
                raise InvalidRecord("invalid_worker_faction_roster")
            result["faction_roster"] = list(faction_roster)
        startup_save = supplied.get("startup_save")
        scenario_id = supplied.get("scenario_id")
        lan_scenario_id = supplied.get("lan_scenario_id")
        if startup_save is not None and scenario_id is not None:
            raise InvalidRecord("conflicting_worker_startup_modes")
        if startup_save is not None:
            if not isinstance(startup_save, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{1,32}", startup_save):
                raise InvalidRecord("invalid_worker_startup_save")
            result["startup_save"] = startup_save
            result["enabled"] = False
        if scenario_id is not None:
            if not isinstance(scenario_id, str) or len(scenario_id) > 512 \
                    or not scenario_id.upper().endswith(".SC"):
                raise InvalidRecord("invalid_worker_scenario_id")
            parts = scenario_id.split("/")
            if not parts or any(
                    part in ("", ".", "..")
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.'()-]{0,95}", part)
                    for part in parts):
                raise InvalidRecord("invalid_worker_scenario_id")
            result["scenario_id"] = scenario_id
            result["enabled"] = False
        if lan_scenario_id is not None:
            if not isinstance(lan_scenario_id, str) or len(lan_scenario_id) > 512 \
                    or not lan_scenario_id.upper().endswith(".SC"):
                raise InvalidRecord("invalid_worker_lan_scenario_id")
            parts = lan_scenario_id.split("/")
            if not parts or any(
                    part in ("", ".", "..")
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.'()-]{0,95}", part)
                    for part in parts):
                raise InvalidRecord("invalid_worker_lan_scenario_id")
            result["lan_scenario_id"] = lan_scenario_id
        if not 0 <= result["difficulty"] <= 5 or result["world_size"] not in (*range(5), 99) \
                or not 1 <= result["faction_id"] <= 7 \
                or not 0 <= result["initial_research_priority"] <= 3:
            raise InvalidRecord("invalid_worker_autostart")
        return result

    @staticmethod
    def _lan_settings_match(observed: object, expected: Mapping[str, Any] | None,
                            profile: str) -> bool:
        if not isinstance(observed, Mapping):
            return False
        if expected is None:
            return observed.get("profile") == profile
        checks = (
            (observed.get("difficulty", {}).get("id"), expected["difficulty"]),
            (observed.get("time_control", {}).get("id"), expected["time_control"]),
            (observed.get("map_size", {}).get("id"), expected["world_size"]),
            (observed.get("world", {}).get("ocean_coverage", {}).get("id"),
             expected["ocean_coverage"]),
            (observed.get("world", {}).get("erosive_forces", {}).get("id"),
             expected["erosive_forces"]),
            (observed.get("world", {}).get("native_life", {}).get("id"),
             expected["native_life"]),
            (observed.get("world", {}).get("cloud_cover", {}).get("id"),
             expected["cloud_cover"]),
        )
        if any(actual != wanted for actual, wanted in checks):
            return False
        rules = observed.get("rules")
        return isinstance(rules, Mapping) and all(
            rules.get(key) is value for key, value in expected.items()
            if key in LAN_RULE_FIELDS
        )

    def _worker_environment(self, spec: Mapping[str, Any], session_id: str) -> list[str]:
        autostart = spec["autostart"]
        resolution_profile = str(
            spec["network"].get("resolution_profile") or "1280x800"
        )
        if resolution_profile not in NATIVE_RESOLUTION_PROFILES:
            raise WorkerManagerError("invalid_native_resolution_profile")
        view_width, view_height = NATIVE_RESOLUTION_PROFILES[resolution_profile]
        view_bitrate = stream_bitrate_kbps(view_width, view_height)
        runtime = self.control.get_runtime(spec["runtime_id"])
        values = {
            # Prepared images inherit their builder environment from Docker's
            # commit operation. Every gameplay container must explicitly leave
            # preparation mode even when its image was produced that way.
            "SMACX_PREPARE_BASE": "0",
            "SMACX_AGENT_TOKEN_FILE": "/run/secrets/bridge-token",
            "SMACX_AGENT_MATCH_ID": spec["match_id"],
            "SMACX_AGENT_SESSION_ID": session_id,
            "SMACX_AGENT_ID": spec["agent_id"],
            "SMACX_PERSPECTIVE_ID": spec["perspective_id"],
            "SMACX_INSTANCE_ID": spec["instance_id"],
            "SMACX_WINEARCH": "win64",
            "SMACX_REQUIRE_DIRECTPLAY": "1",
            "SMACX_VIEW_ENABLE": "1" if spec["network"].get("view_enabled") else "0",
            "SMACX_VIEW_PASSWORD_FILE": "/run/secrets/view-password",
            "SMACX_VIEW_ONLY_PASSWORD_FILE": "/run/secrets/view-only-password",
            "SMACX_VIEW_MODE": str(spec["network"].get("view_mode") or "view-only"),
            "SMACX_CONTROLLER_KIND": str(
                spec["network"].get("controller_kind") or "agent"
            ),
            "SMACX_STREAM_SUBFOLDER": f"/stream/{spec['instance_id']}",
            "SMACX_VIEW_WIDTH": str(view_width),
            "SMACX_VIEW_HEIGHT": str(view_height),
            "SMACX_STREAM_VIDEO_BITRATE": str(view_bitrate),
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
        if runtime["runtime_kind"] == "proton":
            values["SMACX_PROTON_BIN"] = (
                "/proton/proton"
                if runtime["storage_kind"] == "docker-volume"
                else "/opt/proton/proton"
            )
            values["SMACX_PROTON_DIST_LOCK"] = "/tmp/smacx-proton-dist.lock"
        if isinstance(autostart.get("startup_save"), str):
            values["SMACX_AGENT_STARTUP_SAVE"] = autostart["startup_save"]
        if isinstance(autostart.get("scenario_id"), str):
            values["SMACX_AGENT_STARTUP_SCENARIO"] = autostart["scenario_id"]
        if isinstance(autostart.get("lan_scenario_id"), str):
            values["SMACX_AGENT_LAN_SCENARIO"] = autostart["lan_scenario_id"]
        faction_roster = autostart.get("faction_roster")
        if isinstance(faction_roster, list) and len(faction_roster) == 7:
            values["SMACX_AGENT_FACTION_ROSTER"] = ",".join(
                str(int(choice)) for choice in faction_roster
            )
            values["SMACX_AGENT_ALLOWED_FACTION_MASK"] = str(sum(
                1 << int(choice) for choice in faction_roster
            ))
        values.update(game_settings_environment(autostart["game_settings"]))
        return [f"{key}={value}" for key, value in values.items()]

    def set_match_resolution(self, match_id: str, profile_id: str) -> dict[str, Any]:
        """Set the next worker lifetime's native framebuffer profile.

        Callers must checkpoint and stop the match before invoking this.  The
        live stream is never resized out from under a native process.
        """
        dimensions = NATIVE_RESOLUTION_PROFILES.get(profile_id)
        if dimensions is None:
            raise InvalidRecord("invalid_native_resolution_profile")
        match = self.control.get_match(match_id)
        if match["status"] not in {"parked", "recovering", "starting"}:
            raise WorkerManagerError("resolution_change_requires_parked_match")
        workers: list[dict[str, Any]] = []
        for seat in self.control.list_seats(match_id):
            instance_id = seat.get("instance_id")
            if not instance_id:
                continue
            spec = self.control.get_worker_spec(str(instance_id))
            network = dict(spec["network"])
            network["resolution_profile"] = profile_id
            network["stream_bitrate_kbps"] = stream_bitrate_kbps(*dimensions)
            workers.append(self.control.update_worker_network(str(instance_id), network))
        updated = self.control.update_match_lifecycle(
            match_id, match["status"], metadata={
                "native_resolution_profile": profile_id,
                "native_resolution": {"width": dimensions[0], "height": dimensions[1]},
            },
        )
        return {"ok": True, "match": updated, "profile_id": profile_id,
                "width": dimensions[0], "height": dimensions[1],
                "workers": len(workers)}

    def set_match_seat_delegation(
        self, match_id: str, seat_index: int, *, delegated: bool,
    ) -> dict[str, Any]:
        """Change who occupies a saved faction on the next safe rehost.

        The running process is never mutated. An active delegation omits the
        seat's player worker when the verified save is loaded, allowing the
        stock game to retain that saved faction under native computer control.
        Reclaim includes the original worker again and binds it to the same
        saved faction through the ordinary lobby selector.
        """
        match = self.control.get_match(match_id)
        if match["status"] != "parked":
            raise WorkerManagerError("seat_delegation_requires_parked_match")
        seat = self.control.get_seat(match_id, int(seat_index))
        if seat["controller_kind"] != "human" or not seat.get("instance_id"):
            raise WorkerManagerError("delegation_requires_managed_human_seat")
        host_index = int(match.get("metadata", {}).get("managed_host_seat_index", 0))
        if delegated and int(seat_index) == host_index:
            raise WorkerManagerError("transfer_host_before_delegating_host")
        updated = self.control.update_lan_seat(
            match_id, int(seat_index), metadata={
                "delegation_status": "active" if delegated else "none",
                "temporary_controller_kind": "native_ai" if delegated else "none",
                "delegation_changed_unix": time.time(),
            },
        )
        return {"ok": True, "match_id": match_id, "seat": updated,
                "delegated": delegated}

    def set_match_host(self, match_id: str, seat_index: int) -> dict[str, Any]:
        """Select the managed worker that hosts the next checkpoint rehost."""
        match = self.control.get_match(match_id)
        if match["status"] != "parked":
            raise WorkerManagerError("host_transfer_requires_parked_match")
        seat = self.control.get_seat(match_id, int(seat_index))
        if not seat.get("instance_id") or \
                seat.get("metadata", {}).get("delegation_status") == "active":
            raise WorkerManagerError("new_host_requires_active_managed_seat")
        updated = self.control.update_match_lifecycle(
            match_id, "parked", host_instance_id=str(seat["instance_id"]),
            metadata={"managed_host_seat_index": int(seat_index),
                      "host_transferred_unix": time.time()},
        )
        for candidate in self.control.list_seats(match_id):
            self.control.update_lan_seat(
                match_id, int(candidate["seat_index"]), metadata={
                    "role": "host" if int(candidate["seat_index"]) == int(seat_index)
                    else "client",
                },
            )
        return {"ok": True, "match": updated, "seat_index": int(seat_index),
                "instance_id": seat["instance_id"]}

    def start_worker(self, instance_id: str, *, timeout: float = 240.0) -> dict[str, Any]:
        spec = self.control.get_worker_spec(instance_id)
        source = self.control.get_game_source(spec["game_source_id"])
        runtime = self.control.get_runtime(spec["runtime_id"])
        if spec["observed_status"] == "running":
            return self.worker_status(instance_id)
        for volume_name, purpose in (
            (spec["data_volume"], "worker-data"),
            (spec["network"]["secret_volume"], "worker-secret"),
        ):
            resource = self.docker.inspect_volume(volume_name)
            self.docker.require_owned(resource, self.installation_id, purpose=purpose)
        session_id = _new_id("session")
        scope = MemoryScope(spec["match_id"], spec["agent_id"], spec["perspective_id"])
        self.store.start_session(scope, instance_id, session_id=session_id,
                                 metadata={"container_name": spec["container_name"]})
        prepared_image = spec["image_ref"] != self.worker_image
        mounts = [
            {"Type": "volume", "Source": spec["data_volume"], "Target": "/var/lib/smacx"},
            {"Type": "volume", "Source": spec["network"]["secret_volume"],
             "Target": "/run/secrets", "ReadOnly": True},
        ]
        if not prepared_image:
            mounts.insert(0, {
                "Type": "bind", "Source": source["host_path"], "Target": "/game-source",
                "ReadOnly": True, "BindOptions": {"Propagation": "rprivate"},
            })
        if runtime["storage_kind"] == "docker-volume":
            resource = self.docker.inspect_volume(runtime["storage_ref"])
            self.docker.require_owned(resource, self.installation_id, purpose="proton-runtime")
            mounts.insert(1, {
                "Type": "volume", "Source": runtime["storage_ref"], "Target": "/proton",
                "ReadOnly": True,
            })
        elif runtime["storage_kind"] != "image" or runtime["storage_ref"] != self.worker_image:
            raise WorkerManagerError("worker_runtime_image_mismatch")
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
        exposed_ports = {"47814/tcp": {}}
        port_bindings: dict[str, list[dict[str, str]]] = {
            "47814/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}],
        }
        if spec["network"].get("view_enabled"):
            exposed_ports["6080/tcp"] = {}
            port_bindings["6080/tcp"] = [{
                "HostIp": self.view_publish_ip, "HostPort": "",
            }]
        config = {
            "Image": spec["image_ref"],
            "Env": self._worker_environment(spec, session_id),
            "Tty": True,
            "Labels": labels,
            "ExposedPorts": exposed_ports,
            "HostConfig": {
                "NetworkMode": self.network_name or "bridge",
                # Prepared workers use Docker's private copy-on-write layer
                # for disposable Wine/registry changes. Campaign saves remain
                # in the separately mounted managed volume.
                "ReadonlyRootfs": not prepared_image,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Tmpfs": {
                    "/tmp": "rw,nosuid,nodev,size=512m,mode=1777",
                    "/run": "rw,nosuid,nodev,size=32m,mode=0755",
                },
                "PortBindings": port_bindings,
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
                # Docker can report unhealthy after its image-level startup
                # grace expires while a first-run Proton prefix is still
                # registering DirectPlay. Health checks continue afterward
                # and can recover, so the manager's explicit bounded deadline
                # is authoritative as long as the worker process is alive.
                if not state.get("Running"):
                    raise WorkerManagerError("worker_failed_healthcheck")
                time.sleep(1)
            else:
                raise WorkerManagerError("worker_health_timeout")
            binding = inspected.get("NetworkSettings", {}).get("Ports", {}).get("47814/tcp")
            host_port = int(binding[0]["HostPort"]) if isinstance(binding, list) and binding else None
            view_binding = inspected.get("NetworkSettings", {}).get("Ports", {}).get("6080/tcp")
            view_port = (
                int(view_binding[0]["HostPort"])
                if isinstance(view_binding, list) and view_binding else None
            )
            if spec["network"].get("view_enabled") and not view_port:
                raise WorkerManagerError("spectator_port_unavailable")
            network = dict(spec["network"])
            network.update({
                "view_status": "running" if view_port else "disabled",
                "view_host_port": view_port,
                "view_path": f"/stream/{instance_id}/",
                "view_publish_ip": self.view_publish_ip if view_port else None,
                "stream_backend": "selkies",
            })
            self.control.update_worker_network(instance_id, network)
            self.control.update_worker_observation(
                instance_id, desired_status="running", observed_status="running", last_error="",
                bridge_host="127.0.0.1" if host_port else spec["container_name"],
                bridge_port=host_port or 47814, instance_status="running",
            )
            mcp_endpoint = (
                self.start_mcp_sidecar(instance_id)
                if self.control_data_volume
                and spec["network"].get("controller_kind", "agent") == "agent"
                else None
            )
            return {
                "ok": True, "instance_id": instance_id, "session_id": session_id,
                "container_name": spec["container_name"], "container_id": container_id,
                "health": "healthy", "bridge_host_port": host_port,
                "spectator": ({
                    "enabled": True, "host_port": view_port,
                    "path": network["view_path"],
                    "mode": str(network.get("view_mode") or "view-only"),
                } if view_port else {"enabled": False}),
                "mcp": mcp_endpoint,
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

    @staticmethod
    def _lan_operation_id(match_id: str, stage: str, seat_index: int = 0) -> str:
        digest = hashlib.sha256(f"{match_id}:{stage}:{seat_index}".encode("utf-8")).hexdigest()[:32]
        return f"managed-{stage}-{digest}"

    def _container_address(self, instance_id: str) -> str:
        spec = self.control.get_worker_spec(instance_id)
        container = self.docker.inspect_container(spec["container_name"])
        self.docker.require_owned(container, self.installation_id, purpose="game-worker")
        networks = container.get("NetworkSettings", {}).get("Networks", {})
        network = networks.get(self.network_name) if self.network_name and isinstance(networks, Mapping) else None
        if not isinstance(network, Mapping):
            candidates = [item for item in networks.values() if isinstance(item, Mapping)] \
                if isinstance(networks, Mapping) else []
            network = candidates[0] if len(candidates) == 1 else None
        address = network.get("IPAddress") if isinstance(network, Mapping) else None
        if not isinstance(address, str) or not address:
            raise WorkerManagerError("game_worker_network_address_unavailable")
        return address

    def _external_lan_network(self) -> dict[str, Any]:
        if not self.network_name:
            raise WorkerManagerError("external_lan_network_not_configured")
        network = self.docker.inspect_network(self.network_name)
        driver = str(network.get("Driver") or "")
        labels = network.get("Labels") if isinstance(network.get("Labels"), Mapping) else {}
        routed_bridge = driver == "bridge" \
            and labels.get("io.smacx.player-lan") == "true" \
            and labels.get("io.smacx.transport") == "tailscale-routed"
        if (driver not in {"macvlan", "ipvlan"} and not routed_bridge) \
                or network.get("Internal") is True:
            raise WorkerManagerError(
                "external_lan_requires_player_lan_transport"
            )
        return {
            "name": self.network_name,
            "driver": driver,
            "transport": "tailscale-routed" if routed_bridge else "physical-lan",
            "scope": str(network.get("Scope") or "local"),
        }

    @staticmethod
    def _managed_lan_player_name(seat_index: int,
                                 seat: Mapping[str, Any] | None = None) -> str:
        metadata = seat.get("metadata", {}) if isinstance(seat, Mapping) else {}
        configured = metadata.get("player_name") if isinstance(metadata, Mapping) else None
        if isinstance(configured, str) and configured:
            return configured
        return "Semantic Host" if seat_index == 0 else f"Semantic Agent {seat_index + 1}"

    @staticmethod
    def _external_host_address(value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise InvalidRecord("invalid_external_lan_host_address") from exc
        if address.version != 4 or address.is_unspecified or address.is_multicast:
            raise InvalidRecord("invalid_external_lan_host_address")
        return str(address)

    @staticmethod
    def _lan_name_key(value: str) -> str:
        return value.strip().casefold()

    def _remove_unassigned_native_participants(
            self, match_id: str, host_instance: str,
            host_lobby: Mapping[str, Any], expected_names: set[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Remove late native joins that cannot map to one exact portal seat.

        Managed participants join before the lobby is advertised.  For a
        case-insensitive duplicate, the earliest DirectPlay index therefore
        keeps the seat and every later conflicting participant is removed.
        Unknown names are also removed because external seats are explicitly
        reserved by public display name.
        """
        expected_keys = {self._lan_name_key(name) for name in expected_names}
        rejected: list[dict[str, Any]] = []
        current = dict(host_lobby)
        while True:
            participants = [
                dict(item) for item in current.get("lobby", {}).get("participants", [])
                if isinstance(item, Mapping) and isinstance(item.get("name"), str)
                and isinstance(item.get("player_index"), int)
            ]
            seen: set[str] = set()
            target: dict[str, Any] | None = None
            reason = ""
            for participant in sorted(participants, key=lambda item: int(item["player_index"])):
                key = self._lan_name_key(str(participant["name"]))
                if key not in expected_keys:
                    target, reason = participant, "unreserved_display_name"
                    break
                if key in seen:
                    target, reason = participant, "display_name_already_present"
                    break
                seen.add(key)
            if target is None:
                return current, rejected
            identity = current["identity"]
            removed = self._native_request(
                host_instance, "semantic_lan", action="drop_player",
                player_index=int(target["player_index"]),
                expected_player_name=str(target["name"]),
                match_id=identity["match_id"], session_id=identity["session_id"],
                expected_lobby_revision=current["lobby"]["revision"],
                client_operation_id=self._lan_operation_id(
                    match_id,
                    f"reject-native-name-{target.get('player_id', target['player_index'])}",
                    int(target["player_index"]),
                ),
            )
            if not removed.get("ok"):
                detail = json.dumps(removed, separators=(",", ":"))[:1200]
                raise WorkerManagerError(f"native_lan_participant_removal_failed:{detail}")
            rejected.append({
                "player_name": str(target["name"]),
                "reason": reason,
                "message": (
                    "That public display name is already in use in this lobby."
                    if reason == "display_name_already_present" else
                    "That public display name does not have a reserved native seat in this lobby."
                ),
            })
            current = self._wait_native(
                host_instance, "semantic_lan",
                lambda value, prior=len(participants): (
                    value.get("lifecycle") == "lobby" and
                    value.get("lobby", {}).get("participant_count") == prior - 1
                ), timeout=20, context="native_name_conflict_removed", action="status",
            )

    def _human_hosted_lan_context(self, match_id: str) -> tuple[dict[str, Any],
                                                                  list[dict[str, Any]],
                                                                  list[dict[str, Any]],
                                                                  list[dict[str, Any]]]:
        match = self.control.get_match(match_id)
        seats = self.control.list_seats(match_id)
        agent_seats = [seat for seat in seats if seat["controller_kind"] == "agent"]
        human_seats = [seat for seat in seats if seat["controller_kind"] == "human"]
        if not seats or seats[0]["controller_kind"] != "human" \
                or seats[0].get("metadata", {}).get("role") != "host" \
                or not agent_seats or int(agent_seats[0]["seat_index"]) != 1:
            raise WorkerManagerError("human_hosted_lan_required")
        if any(not seat.get("instance_id") for seat in agent_seats):
            raise WorkerManagerError("managed_lan_seat_not_provisioned")
        if any(seat.get("instance_id") for seat in human_seats):
            raise WorkerManagerError("external_human_seat_must_not_have_worker")
        return match, seats, agent_seats, human_seats

    def prepare_human_hosted_lan_match(self, match_id: str, *,
                                       profile: str = "external_host",
                                       resume_ref: str | None = None,
                                       timeout: float = 420.0) -> dict[str, Any]:
        """Start managed clients but leave native lobby ownership to the human."""
        match, seats, agent_seats, human_seats = self._human_hosted_lan_context(match_id)
        network = self._external_lan_network()
        deadline = time.monotonic() + min(max(float(timeout), 120.0), 900.0)
        self.control.update_match_lifecycle(
            match_id, "starting", metadata={
                "lan_profile": profile,
                "resume_ref": resume_ref,
                "external_lan": {
                    "mode": "human_hosted",
                    "phase": "preparing_clients",
                    "network": network,
                    "host_player_name": seats[0]["metadata"]["external_player_name"],
                },
            },
        )
        try:
            for seat in agent_seats:
                remaining = max(30.0, deadline - time.monotonic())
                self.start_worker(str(seat["instance_id"]), timeout=min(remaining, 300.0))
        except Exception as exc:
            self.control.update_match_lifecycle(
                match_id, "error", metadata={"last_lan_error": str(exc)[:1000]},
            )
            raise
        staged = self.control.update_match_lifecycle(
            match_id, "lobby", metadata={
                "external_lan": {
                    "mode": "human_hosted",
                    "phase": "awaiting_discovery",
                    "network": network,
                    "host_player_name": seats[0]["metadata"]["external_player_name"],
                    "human_players": [
                        {
                            "seat_index": int(seat["seat_index"]),
                            "player_name": seat["metadata"]["external_player_name"],
                            "role": seat["metadata"].get("role"),
                            "expected_faction_id": seat.get("faction_id"),
                        }
                        for seat in human_seats
                    ],
                    "resume_ref": resume_ref,
                },
            },
        )
        return {
            "ok": True,
            "match": staged,
            "awaiting_external_host": True,
            "external_host": {
                "player_name": seats[0]["metadata"]["external_player_name"],
                "network": network,
                "instructions": (
                    "Create or load the native TCP/IP multiplayer lobby on the human game. "
                    "Then provide its reachable IPv4 address to discover and select the exact session."
                ),
            },
            "managed_agent_count": len(agent_seats),
            "pixels_or_ui_input_used": False,
        }

    def discover_human_hosted_lan_match(self, match_id: str, *,
                                        host_address: str,
                                        timeout: float = 140.0) -> dict[str, Any]:
        """Discover joinable native sessions at an explicitly supplied human host."""
        match, _, agent_seats, _ = self._human_hosted_lan_context(match_id)
        if match["status"] != "lobby":
            raise WorkerManagerError("human_hosted_lan_not_prepared")
        self._external_lan_network()
        address = self._external_host_address(host_address)
        discovered = self._native_request(
            str(agent_seats[0]["instance_id"]), "semantic_lan",
            action="discover", host_address=address, timeout=min(max(timeout, 30.0), 180.0),
        )
        sessions = [
            dict(item) for item in discovered.get("sessions", [])
            if isinstance(item, Mapping) and item.get("joinable") is True
            and isinstance(item.get("network_session_id"), str)
        ]
        external = dict(match.get("metadata", {}).get("external_lan") or {})
        external.update({
            "phase": "session_discovered" if sessions else "awaiting_discovery",
            "host_address": address,
        })
        updated = self.control.update_match_lifecycle(
            match_id, "lobby", metadata={"external_lan": external},
        )
        return {
            "ok": True, "match": updated, "host_address": address,
            "sessions": sessions, "session_count": len(sessions),
            "pixels_or_ui_input_used": False,
        }

    def join_human_hosted_lan_match(self, match_id: str, *,
                                    host_address: str,
                                    network_session_id: str,
                                    timeout: float = 420.0) -> dict[str, Any]:
        """Join and ready every managed agent in one exact human-owned lobby."""
        match, seats, agent_seats, human_seats = self._human_hosted_lan_context(match_id)
        if match["status"] != "lobby":
            raise WorkerManagerError("human_hosted_lan_not_prepared")
        self._external_lan_network()
        address = self._external_host_address(host_address)
        if not isinstance(network_session_id, str) or not re.fullmatch(
            r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
            r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", network_session_id,
        ):
            raise InvalidRecord("invalid_lan_network_session_id")
        deadline = time.monotonic() + min(max(float(timeout), 120.0), 900.0)
        try:
            for seat in agent_seats:
                instance_id = str(seat["instance_id"])
                discovered = self._native_request(
                    instance_id, "semantic_lan", action="discover",
                    host_address=address,
                    timeout=min(140.0, max(30.0, deadline - time.monotonic())),
                )
                exact = [
                    item for item in discovered.get("sessions", [])
                    if isinstance(item, Mapping)
                    and item.get("network_session_id") == network_session_id
                    and item.get("joinable") is True
                ]
                if len(exact) != 1:
                    raise WorkerManagerError("native_lan_exact_session_not_discovered")
                seat_index = int(seat["seat_index"])
                joined = self._native_request(
                    instance_id, "semantic_lan", action="join",
                    network_session_id=network_session_id,
                    player_name=self._managed_lan_player_name(seat_index, seat),
                    host_address=address,
                    client_operation_id=self._lan_operation_id(
                        match_id, "external_host_join", seat_index,
                    ),
                    timeout=min(140.0, max(30.0, deadline - time.monotonic())),
                )
                if not joined.get("ok") or not joined.get("joined"):
                    raise WorkerManagerError("native_lan_join_failed")
                self._wait_native(
                    instance_id, "semantic_lan",
                    lambda value, expected=network_session_id: (
                        value.get("lifecycle") == "lobby"
                        and value.get("identity", {}).get("network_session_id") == expected
                    ), timeout=60, context=f"human_host_client_{seat_index}_joined",
                    action="status",
                )

            expected_names = {
                str(seat["metadata"]["external_player_name"])
                for seat in human_seats
            } | {
                self._managed_lan_player_name(int(seat["seat_index"]), seat)
                for seat in agent_seats
            }
            first_instance = str(agent_seats[0]["instance_id"])
            lobby = self._wait_native(
                first_instance, "semantic_lan",
                lambda value: value.get("lifecycle") == "lobby"
                and value.get("lobby", {}).get("participant_count", 0) >= len(agent_seats) + 1,
                timeout=75, context="human_host_all_agents_joined", action="status",
            )
            participants = [
                dict(item) for item in lobby.get("lobby", {}).get("participants", [])
                if isinstance(item, Mapping) and isinstance(item.get("name"), str)
            ]
            observed_names = [str(item["name"]) for item in participants]
            observed_keys = [self._lan_name_key(name) for name in observed_names]
            expected_keys = {self._lan_name_key(name) for name in expected_names}
            unexpected = sorted(
                name for name in observed_names
                if self._lan_name_key(name) not in expected_keys
            )
            if len(observed_keys) != len(set(observed_keys)) or unexpected:
                detail = json.dumps({
                    "duplicate_names": sorted(
                        name for name in set(observed_names)
                        if observed_keys.count(self._lan_name_key(name)) > 1
                    ),
                    "unexpected_players": unexpected,
                }, separators=(",", ":"))[:1200]
                raise WorkerManagerError(
                    f"external_lan_participant_identity_mismatch:{detail}"
                )
            host_name = str(seats[0]["metadata"]["external_player_name"])
            host_participant = next(
                (item for item in participants
                 if self._lan_name_key(str(item["name"])) == self._lan_name_key(host_name)),
                None,
            )
            if not host_participant or host_participant.get("host") is not True:
                raise WorkerManagerError("human_lan_host_identity_mismatch")

            game_type = str(lobby.get("lobby", {}).get("game_type") or "new")
            for seat in agent_seats:
                instance_id = str(seat["instance_id"])
                seat_index = int(seat["seat_index"])
                local_lobby = self._native_request(
                    instance_id, "semantic_lan", action="status",
                )
                local = next(
                    (dict(item) for item in local_lobby.get("lobby", {}).get(
                        "participants", []
                    ) if isinstance(item, Mapping) and item.get("local") is True),
                    {},
                )
                expected_faction = seat.get("faction_id")
                if game_type == "load" and isinstance(expected_faction, int):
                    required_choice = local.get("required_faction_choice_id")
                    if local.get("faction_id") != expected_faction \
                            or not isinstance(required_choice, int):
                        raise WorkerManagerError("native_lan_restored_faction_binding_mismatch")
                    if local.get("faction_choice_id") != required_choice:
                        selected = self._native_request(
                            instance_id, "semantic_lan", action="select_faction",
                            faction_choice_id=required_choice,
                            match_id=local_lobby["identity"]["match_id"],
                            session_id=local_lobby["identity"]["session_id"],
                            expected_lobby_revision=local_lobby["lobby"]["revision"],
                            client_operation_id=self._lan_operation_id(
                                match_id, "external_host_faction", seat_index,
                            ),
                        )
                        if not selected.get("ok"):
                            raise WorkerManagerError("native_lan_faction_selection_failed")
                        local_lobby = self._native_request(
                            instance_id, "semantic_lan", action="status",
                        )
                identity = local_lobby["identity"]
                ready = self._native_request(
                    instance_id, "semantic_lan", action="set_ready", ready=True,
                    match_id=identity["match_id"], session_id=identity["session_id"],
                    expected_lobby_revision=local_lobby["lobby"]["revision"],
                    client_operation_id=self._lan_operation_id(
                        match_id, "external_host_ready", seat_index,
                    ),
                )
                if not ready.get("ok"):
                    raise WorkerManagerError("native_lan_ready_failed")
                self.control.update_lan_seat(
                    match_id, seat_index,
                    faction_id=(int(local["faction_id"])
                                if isinstance(local.get("faction_id"), int) else None),
                    metadata={
                        "network_session_id": network_session_id,
                        "native_role": "client",
                    },
                )
            by_name = {self._lan_name_key(str(item["name"])): item for item in participants}
            for seat in human_seats:
                player_name = str(seat["metadata"]["external_player_name"])
                participant = by_name.get(self._lan_name_key(player_name))
                if participant and isinstance(participant.get("faction_id"), int):
                    self.control.update_lan_seat(
                        match_id, int(seat["seat_index"]),
                        faction_id=int(participant["faction_id"]),
                        metadata={
                            "network_session_id": network_session_id,
                            "network_player_index": participant.get("player_index"),
                            "native_role": "external_host" if int(seat["seat_index"]) == 0
                            else "external_client",
                        },
                    )
            external = dict(match.get("metadata", {}).get("external_lan") or {})
            external.update({
                "phase": "awaiting_human_start",
                "host_address": address,
                "network_session_id": network_session_id,
                "session_name": lobby.get("lobby", {}).get("session_name"),
                "game_type": game_type,
            })
            updated = self.control.update_match_lifecycle(
                match_id, "lobby", metadata={
                    "network_session_id": network_session_id,
                    "external_lan": external,
                },
            )
            return {
                "ok": True, "match": updated,
                "network_session_id": network_session_id,
                "awaiting_human_start": True,
                "external_host": {
                    "host_address": address,
                    "player_name": host_name,
                    "session_name": external.get("session_name"),
                    "instructions": (
                        "All managed agents are joined and Ready. The human host may now "
                        "verify seats/settings and press the native Start button."
                    ),
                },
                "pixels_or_ui_input_used": False,
            }
        except Exception as exc:
            self.control.update_match_lifecycle(
                match_id, "error", metadata={"last_lan_error": str(exc)[:1000]},
            )
            raise

    def finalize_human_hosted_lan_match(self, match_id: str, *,
                                        timeout: float = 90.0) -> dict[str, Any]:
        """Observe a human-owned Start and bind every visible faction durably."""
        match, seats, agent_seats, human_seats = self._human_hosted_lan_context(match_id)
        external = match.get("metadata", {}).get("external_lan")
        if match["status"] != "lobby" or not isinstance(external, Mapping) \
                or external.get("phase") != "awaiting_human_start":
            raise WorkerManagerError("human_hosted_lan_agents_not_joined")
        expected_session = external.get("network_session_id")
        first_instance = str(agent_seats[0]["instance_id"])
        first = self._native_request(first_instance, "semantic_lan", action="status")
        if first.get("lifecycle") == "lobby":
            participants = [
                dict(item) for item in first.get("lobby", {}).get("participants", [])
                if isinstance(item, Mapping) and isinstance(item.get("name"), str)
            ]
            by_name = {
                self._lan_name_key(str(item["name"])): item for item in participants
            }
            blockers: list[dict[str, Any]] = []
            for seat in human_seats[1:]:
                name = str(seat["metadata"]["external_player_name"])
                participant = by_name.get(self._lan_name_key(name))
                if participant is None:
                    blockers.append({"player_name": name, "reason": "not_joined"})
                elif participant.get("ready") is not True:
                    blockers.append({"player_name": name, "reason": "not_ready"})
            waiting = {
                "ok": True, "match": match,
                "awaiting_human_start": True,
                "blockers": blockers,
                "external_host": {
                    "host_address": external.get("host_address"),
                    "player_name": external.get("host_player_name"),
                    "session_name": external.get("session_name"),
                    "instructions": "Press Start in the native human-hosted lobby when all seats are ready.",
                },
                "pixels_or_ui_input_used": False,
            }
            if blockers:
                return waiting
            try:
                first = self._wait_native(
                    first_instance, "semantic_lan",
                    lambda value, expected=expected_session: (
                        value.get("lifecycle") == "game"
                        and value.get("identity", {}).get("network_session_id") == expected
                    ),
                    timeout=min(max(float(timeout), 5.0), 30.0),
                    poll_seconds=0.5, context="human_host_start_transition",
                    action="status",
                )
            except WorkerManagerError as exc:
                if not str(exc).startswith(
                    "native_semantic_lan_human_host_start_transition_timeout:"
                ):
                    raise
                return waiting
        if first.get("lifecycle") != "game" \
                or first.get("identity", {}).get("network_session_id") != expected_session:
            raise WorkerManagerError("human_hosted_lan_session_changed")

        deadline = time.monotonic() + min(max(float(timeout), 30.0), 300.0)
        native: list[dict[str, Any]] = []
        for seat in agent_seats:
            instance_id = str(seat["instance_id"])
            game = self._wait_native(
                instance_id, "semantic_lan",
                lambda value, expected=expected_session: value.get("lifecycle") == "game"
                and value.get("identity", {}).get("network_session_id") == expected,
                timeout=max(15.0, deadline - time.monotonic()),
                context=f"human_host_seat_{seat['seat_index']}_game", action="status",
            )
            snapshot = self._wait_native(
                instance_id, "semantic_snapshot",
                lambda value: isinstance(value.get("snapshot", {}).get("faction"), Mapping),
                timeout=45, context=f"human_host_seat_{seat['seat_index']}_snapshot",
            )
            faction = snapshot["snapshot"]["faction"]
            updated = self.control.update_lan_seat(
                match_id, int(seat["seat_index"]),
                faction_id=int(faction["id"]),
                faction_name=str(faction.get("name") or "") or None,
                metadata={
                    "network_session_id": expected_session,
                    "native_role": "client",
                },
            )
            native.append({
                "seat_index": int(seat["seat_index"]),
                "controller_kind": "agent",
                "instance_id": instance_id,
                "faction_id": updated.get("faction_id"),
                "faction_name": updated.get("faction_name"),
                "lifecycle": game["lifecycle"],
            })

        chat = self._native_request(first_instance, "semantic_chat", action="list")
        participants = [
            dict(item) for item in chat.get("participants", [])
            if isinstance(item, Mapping) and isinstance(item.get("player_name"), str)
        ]
        by_name = {
            self._lan_name_key(str(item["player_name"])): item for item in participants
        }
        expected_names = {
            str(seat["metadata"]["external_player_name"]) for seat in human_seats
        } | {
            self._managed_lan_player_name(int(seat["seat_index"]), seat) for seat in agent_seats
        }
        expected_keys = {self._lan_name_key(name) for name in expected_names}
        if set(by_name) != expected_keys:
            detail = json.dumps({
                "expected": sorted(expected_names), "observed": sorted(by_name),
            }, separators=(",", ":"))[:1200]
            raise WorkerManagerError(f"external_lan_participant_identity_mismatch:{detail}")
        for seat in human_seats:
            name = str(seat["metadata"]["external_player_name"])
            participant = by_name[self._lan_name_key(name)]
            faction_id = participant.get("faction_id")
            if not isinstance(faction_id, int):
                raise WorkerManagerError("human_lan_faction_identity_missing")
            updated = self.control.update_lan_seat(
                match_id, int(seat["seat_index"]), faction_id=faction_id,
                faction_name=str(participant.get("faction_name") or "") or None,
                metadata={
                    "network_session_id": expected_session,
                    "network_player_id": participant.get("player_id"),
                    "native_role": "external_host" if int(seat["seat_index"]) == 0
                    else "external_client",
                },
            )
            native.append({
                "seat_index": int(seat["seat_index"]),
                "controller_kind": "human", "player_name": name,
                "faction_id": updated.get("faction_id"),
                "faction_name": updated.get("faction_name"),
                "lifecycle": "external_game",
            })
        running_external = dict(external)
        running_external["phase"] = "running"
        running = self.control.update_match_lifecycle(
            match_id, "running", metadata={
                "network_session_id": expected_session,
                "participant_count": len(seats),
                "external_lan": running_external,
            },
        )
        return {
            "ok": True, "match": running,
            "network_session_id": expected_session,
            "seats": sorted(native, key=lambda item: int(item["seat_index"])),
            "human_hosted": True,
            "pixels_or_ui_input_used": False,
        }

    def start_lan_match(self, match_id: str, *, session_name: str | None = None,
                        profile: str = "small_easy", resume_slot: str | None = None,
                        scenario_id: str | None = None,
                        game_settings: Mapping[str, Any] | None = None,
                        timeout: float = 420.0) -> dict[str, Any]:
        match = self.control.get_match(match_id)
        if session_name is None:
            session_name = str(
                match.get("metadata", {}).get("lan_session_name") or "SMACX Managed LAN"
            )
        if not isinstance(session_name, str) or not 1 <= len(session_name) <= 31 \
                or any(ord(character) < 32 or ord(character) > 126 for character in session_name):
            raise InvalidRecord("invalid_lan_session_name")
        if resume_slot is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", resume_slot):
            raise InvalidRecord("invalid_save_slot")
        if resume_slot is not None and scenario_id is not None:
            raise InvalidRecord("conflicting_lan_startup_modes")
        if game_settings is not None and (resume_slot is not None or scenario_id is not None):
            raise InvalidRecord("lan_settings_require_fresh_game")
        normalized_lan_settings = (
            normalize_lan_game_settings(game_settings) if game_settings is not None else None
        )
        seats = self.control.list_seats(match_id)
        agent_seats = [seat for seat in seats if seat["controller_kind"] == "agent"]
        human_seats = [seat for seat in seats if seat["controller_kind"] == "human"]
        delegated_seats = [
            seat for seat in seats
            if seat.get("metadata", {}).get("delegation_status") == "active"
        ]
        managed_seats = [
            seat for seat in seats
            if seat.get("instance_id") and seat not in delegated_seats
        ]
        external_human_seats = [seat for seat in human_seats if not seat.get("instance_id")]
        if not 2 <= len(seats) <= 7 or not managed_seats \
                or len(agent_seats) + len(human_seats) != len(seats):
            raise WorkerManagerError("managed_lan_requires_valid_seats")
        if any(not seat.get("instance_id") for seat in agent_seats):
            raise WorkerManagerError("managed_lan_seat_not_provisioned")
        if any(
                seat.get("metadata", {}).get("managed") is True
                and not seat.get("instance_id") for seat in human_seats):
            raise WorkerManagerError("managed_human_seat_not_provisioned")
        if normalized_lan_settings is None and profile not in LAN_PROFILES:
            raise InvalidRecord("unsupported_lan_profile")
        if normalized_lan_settings is not None:
            profile = "custom"
        external_human_hosted = (
            seats[0]["controller_kind"] == "human" and not seats[0].get("instance_id")
        )
        if external_human_hosted:
            if seats[0].get("metadata", {}).get("role") != "host" \
                    or int(managed_seats[0]["seat_index"]) != 1:
                raise WorkerManagerError("human_hosted_lan_seat_order_invalid")
            if match["status"] == "lobby":
                return self.finalize_human_hosted_lan_match(match_id, timeout=timeout)
            return self.prepare_human_hosted_lan_match(
                match_id, profile=profile, resume_ref=resume_slot, timeout=timeout,
            )
        configured_host_index = int(match.get("metadata", {}).get(
            "managed_host_seat_index", 0
        ))
        host = next(
            (seat for seat in managed_seats
             if int(seat["seat_index"]) == configured_host_index), None,
        )
        if host is None:
            raise WorkerManagerError("managed_host_seat_unavailable")
        # DirectPlay assigns participant indexes by join order. Keep the
        # selected host first while preserving the durable portal seat and
        # faction identities separately.
        managed_seats = [host] + [seat for seat in managed_seats if seat is not host]
        external_network = self._external_lan_network() if external_human_seats else None
        if external_human_seats and match["status"] == "lobby":
            return self.finalize_external_lan_match(match_id, timeout=timeout)
        host_instance = str(host["instance_id"])
        if scenario_id is not None:
            host_spec = self.control.get_worker_spec(host_instance)
            catalog = self.list_scenarios(str(host_spec["game_source_id"]))
            if scenario_id not in {
                    item.get("scenario_id") for item in catalog.get("scenarios", [])
            }:
                raise InvalidRecord("unknown_lan_scenario_id")
        scenario_context_id = scenario_id
        if scenario_context_id is None and resume_slot is not None:
            stored_scenario = match.get("metadata", {}).get("scenario_id")
            if isinstance(stored_scenario, str):
                scenario_context_id = stored_scenario
        deadline = time.monotonic() + min(max(float(timeout), 120.0), 900.0)
        self.control.update_match_lifecycle(
            match_id, "starting", host_instance_id=host_instance,
            metadata={
                "lan_profile": profile, "lan_session_name": session_name,
                "resume_slot": resume_slot, "scenario_id": scenario_context_id,
                "game_settings": normalized_lan_settings,
            },
        )
        try:
            for seat in managed_seats:
                instance_id = str(seat["instance_id"])
                spec = self.control.get_worker_spec(instance_id)
                if spec["observed_status"] != "running":
                    autostart = dict(spec["autostart"])
                    if scenario_context_id is None:
                        autostart.pop("lan_scenario_id", None)
                    else:
                        autostart["lan_scenario_id"] = scenario_context_id
                    self.control.update_worker_autostart(instance_id, autostart)
                remaining = max(30.0, deadline - time.monotonic())
                self.start_worker(instance_id, timeout=min(remaining, 300.0))
            host_player_name = str(
                host.get("metadata", {}).get("external_player_name")
                or self._managed_lan_player_name(int(host["seat_index"]), host)
            )
            hosted = self._native_request(
                host_instance, "semantic_lan", timeout=min(140.0, max(30.0, deadline - time.monotonic())),
                action="host", session_name=session_name, player_name=host_player_name,
                client_operation_id=self._lan_operation_id(match_id, "host"),
            )
            if not hosted.get("ok") or not hosted.get("lobby_launch_queued"):
                raise WorkerManagerError("native_lan_host_failed")
            host_lobby = self._wait_native(
                host_instance, "semantic_lan",
                lambda value: value.get("lifecycle") == "lobby", timeout=45,
                context="host_lobby", action="status",
            )
            network_session_id = host_lobby.get("identity", {}).get("network_session_id")
            if not isinstance(network_session_id, str) or not network_session_id:
                raise WorkerManagerError("native_lan_network_identity_missing")
            loaded_checkpoint = None
            if resume_slot is not None:
                identity = host_lobby["identity"]
                loaded_checkpoint = self._native_request(
                    host_instance, "semantic_lan", action="load_save", slot=resume_slot,
                    match_id=identity["match_id"], session_id=identity["session_id"],
                    expected_lobby_revision=host_lobby["lobby"]["revision"],
                    client_operation_id=self._lan_operation_id(match_id, "load"),
                )
                if not loaded_checkpoint.get("ok"):
                    detail = json.dumps(loaded_checkpoint, separators=(",", ":"))[:1200]
                    raise WorkerManagerError(f"native_lan_load_failed:{detail}")
                host_lobby = self._wait_native(
                    host_instance, "semantic_lan",
                    lambda value: (
                        value.get("lifecycle") == "lobby"
                        and value.get("lobby", {}).get("game_type") == "load"
                    ), timeout=45, context="host_loaded_lobby", action="status",
                )
            elif scenario_id is not None:
                identity = host_lobby["identity"]
                loaded_checkpoint = self._native_request(
                    host_instance, "semantic_lan", action="load_scenario",
                    scenario_id=scenario_id,
                    match_id=identity["match_id"], session_id=identity["session_id"],
                    expected_lobby_revision=host_lobby["lobby"]["revision"],
                    client_operation_id=self._lan_operation_id(match_id, "scenario"),
                )
                if not loaded_checkpoint.get("ok"):
                    detail = json.dumps(loaded_checkpoint, separators=(",", ":"))[:1200]
                    raise WorkerManagerError(f"native_lan_scenario_load_failed:{detail}")
                host_lobby = self._wait_native(
                    host_instance, "semantic_lan",
                    lambda value: (
                        value.get("lifecycle") == "lobby"
                        and value.get("lobby", {}).get("game_type")
                        == "multiplayer_scenario"
                    ), timeout=45, context="host_scenario_lobby", action="status",
                )
            host_address = self._container_address(host_instance)
            for seat in managed_seats[1:]:
                seat_index = int(seat["seat_index"])
                instance_id = str(seat["instance_id"])
                discovered = self._native_request(
                    instance_id, "semantic_lan", timeout=140, action="discover",
                    host_address=host_address,
                )
                matches = [
                    item for item in discovered.get("sessions", [])
                    if isinstance(item, Mapping)
                    and item.get("network_session_id") == network_session_id
                ]
                if not discovered.get("ok") or len(matches) != 1 or not matches[0].get("joinable"):
                    raise WorkerManagerError("native_lan_exact_session_not_discovered")
                player_name = str(
                    seat.get("metadata", {}).get("external_player_name")
                    or self._managed_lan_player_name(seat_index, seat)
                )
                joined = self._native_request(
                    instance_id, "semantic_lan", timeout=140, action="join",
                    network_session_id=network_session_id,
                    player_name=player_name,
                    host_address=host_address,
                    client_operation_id=self._lan_operation_id(match_id, "join", seat_index),
                )
                if not joined.get("ok") or not joined.get("joined"):
                    raise WorkerManagerError("native_lan_join_failed")
                self._wait_native(
                    instance_id, "semantic_lan",
                    lambda value, expected=network_session_id: (
                        value.get("lifecycle") == "lobby"
                        and value.get("identity", {}).get("network_session_id") == expected
                    ), timeout=45, context=f"client_{seat_index}_joined_lobby", action="status",
                )
            participant_count = len(managed_seats)
            host_lobby = self._wait_native(
                host_instance, "semantic_lan",
                lambda value: (
                    value.get("lifecycle") == "lobby"
                    and value.get("lobby", {}).get("participant_count") == participant_count
                ), timeout=45, context="host_participant_count", action="status",
            )
            resume_choices_by_seat: dict[int, int] = {}
            scenario_choices_by_seat: dict[int, int] = {}
            new_game_choices_by_seat: dict[int, int] = {}
            if resume_slot is None and scenario_id is None:
                identity = host_lobby["identity"]
                configured = self._native_request(
                    host_instance, "semantic_lan", action="configure", profile=profile,
                    **(normalized_lan_settings or {}),
                    match_id=identity["match_id"], session_id=identity["session_id"],
                    expected_lobby_revision=host_lobby["lobby"]["revision"],
                    client_operation_id=self._lan_operation_id(match_id, "configure"),
                )
                if not configured.get("ok"):
                    raise WorkerManagerError("native_lan_configure_failed")
                host_lobby = self._wait_native(
                    host_instance, "semantic_lan",
                    lambda value: (
                        value.get("lifecycle") == "lobby"
                        and self._lan_settings_match(
                            value.get("lobby", {}).get("settings"),
                            normalized_lan_settings, profile,
                        )
                    ), timeout=45, context="host_configured_lobby",
                    action="status",
                )
                for seat in managed_seats:
                    seat_index = int(seat["seat_index"])
                    choice = seat.get("metadata", {}).get("requested_faction_choice_id")
                    if not isinstance(choice, int):
                        raise WorkerManagerError("managed_seat_faction_choice_missing")
                    instance_id = str(seat["instance_id"])
                    lobby = self._wait_native(
                        instance_id, "semantic_lan",
                        lambda value, expected=network_session_id: (
                            value.get("lifecycle") == "lobby"
                            and value.get("identity", {}).get("network_session_id") == expected
                            and value.get("lobby", {}).get("game_type") == "new_game"
                        ), timeout=45, context=f"seat_{seat_index}_new_game_faction_lobby",
                        action="status",
                    )
                    local = next(
                        (item for item in lobby.get("lobby", {}).get("participants", [])
                         if isinstance(item, Mapping) and item.get("local") is True), {},
                    )
                    if local.get("faction_choice_id") != choice:
                        selected = self._native_request(
                            instance_id, "semantic_lan", action="select_faction",
                            faction_choice_id=choice,
                            match_id=lobby["identity"]["match_id"],
                            session_id=lobby["identity"]["session_id"],
                            expected_lobby_revision=lobby["lobby"]["revision"],
                            client_operation_id=self._lan_operation_id(
                                match_id, "new-game-faction", seat_index,
                            ),
                        )
                        if not selected.get("ok"):
                            detail = json.dumps(selected, separators=(",", ":"))[:1200]
                            raise WorkerManagerError(
                                f"native_lan_new_game_faction_selection_failed:{detail}"
                            )
                    new_game_choices_by_seat[seat_index] = choice
                    host_lobby = self._wait_native(
                        host_instance, "semantic_lan",
                        lambda value, choice=choice: (
                            value.get("lifecycle") == "lobby"
                            and any(
                                isinstance(item, Mapping)
                                and item.get("faction_choice_id") == choice
                                for item in value.get("lobby", {}).get("participants", [])
                            )
                        ), timeout=45,
                        context=f"host_observed_new_game_faction_{seat_index}",
                        action="status",
                    )
            elif resume_slot is not None:
                restored_by_player: dict[int, int] = {}
                for seat in managed_seats:
                    seat_index = int(seat["seat_index"])
                    instance_id = str(seat["instance_id"])
                    expected_faction = seat.get("faction_id")
                    if not isinstance(expected_faction, int):
                        raise WorkerManagerError("native_lan_saved_faction_missing")
                    lobby = self._wait_native(
                        instance_id, "semantic_lan",
                        lambda value, expected=network_session_id: (
                            value.get("lifecycle") == "lobby"
                            and value.get("identity", {}).get("network_session_id") == expected
                            and value.get("lobby", {}).get("game_type") == "load"
                            and value.get("lobby", {}).get("participant_count") == participant_count
                        ), timeout=75, context=f"seat_{seat_index}_loaded_faction_lobby",
                        action="status",
                    )
                    if restored_by_player:
                        lobby = self._wait_native(
                            instance_id, "semantic_lan",
                            lambda value, prior=dict(restored_by_player): (
                                value.get("lifecycle") == "lobby"
                                and all(
                                    next(
                                        (item for item in value.get("lobby", {}).get(
                                            "participants", []
                                        ) if isinstance(item, Mapping)
                                         and item.get("player_index") == player_index),
                                        {},
                                    ).get("faction_choice_id") == faction_choice_id
                                    for player_index, faction_choice_id in prior.items()
                                )
                            ), timeout=75,
                            context=f"seat_{seat_index}_prior_faction_choices",
                            action="status",
                        )
                    local = next(
                        (item for item in lobby["lobby"].get("participants", [])
                         if isinstance(item, Mapping) and item.get("local") is True),
                        {},
                    )
                    if local.get("faction_id") != expected_faction:
                        detail = json.dumps(local, separators=(",", ":"))[:1000]
                        raise WorkerManagerError(
                            f"native_lan_restored_faction_binding_mismatch:{detail}"
                        )
                    expected_choice = local.get("required_faction_choice_id")
                    if not isinstance(expected_choice, int):
                        detail = json.dumps(local, separators=(",", ":"))[:1000]
                        raise WorkerManagerError(
                            f"native_lan_loaded_faction_choice_unavailable:{detail}"
                        )
                    if local.get("faction_choice_id") != expected_choice:
                        selected = self._native_request(
                            instance_id, "semantic_lan", action="select_faction",
                            faction_choice_id=expected_choice,
                            match_id=lobby["identity"]["match_id"],
                            session_id=lobby["identity"]["session_id"],
                            expected_lobby_revision=lobby["lobby"]["revision"],
                            client_operation_id=self._lan_operation_id(
                                match_id, "faction", seat_index,
                            ),
                        )
                        if not selected.get("ok"):
                            detail = json.dumps({
                                "seat_index": seat_index,
                                "expected_faction_choice_id": expected_choice,
                                "native_selector_record_ids": lobby.get(
                                    "lobby", {}
                                ).get("native_selector_record_ids", []),
                                "participants": lobby.get("lobby", {}).get(
                                    "participants", []
                                ),
                                "result": selected,
                            }, separators=(",", ":"))[:1800]
                            raise WorkerManagerError(
                                f"native_lan_faction_selection_failed:{detail}"
                            )
                    local_player_index = local.get("player_index")
                    if not isinstance(local_player_index, int):
                        raise WorkerManagerError(
                            "native_lan_loaded_player_index_unavailable"
                        )
                    resume_choices_by_seat[seat_index] = expected_choice
                    restored_by_player[local_player_index] = expected_choice
                    host_lobby = self._wait_native(
                        host_instance, "semantic_lan",
                        lambda value, player_index=local_player_index,
                        faction_choice_id=expected_choice: (
                            value.get("lifecycle") == "lobby"
                            and next(
                                (item for item in value.get("lobby", {}).get(
                                    "participants", []
                                ) if isinstance(item, Mapping)
                                 and item.get("player_index") == player_index),
                                {},
                            ).get("faction_choice_id") == faction_choice_id
                        ), timeout=75,
                        context=f"host_observed_seat_{seat_index}_faction_choice",
                        action="status",
                    )
                expected_by_player = dict(restored_by_player)
                host_lobby = self._wait_native(
                    host_instance, "semantic_lan",
                    lambda value: (
                        value.get("lifecycle") == "lobby"
                        and all(
                            next(
                                (item for item in value.get("lobby", {}).get("participants", [])
                                 if isinstance(item, Mapping)
                                 and item.get("player_index") == player_index),
                                {},
                            ).get("faction_choice_id") == faction_id
                            for player_index, faction_id in expected_by_player.items()
                        )
                    ), timeout=75, context="host_faction_choices_converged",
                    action="status",
                )
                participants = {
                    int(item["player_index"]): item
                    for item in host_lobby.get("lobby", {}).get("participants", [])
                    if isinstance(item, Mapping) and isinstance(item.get("player_index"), int)
                }
                mismatches: list[dict[str, Any]] = []
                player_index_by_seat = {
                    int(seat["seat_index"]): player_index
                    for player_index, seat in enumerate(managed_seats, start=1)
                }
                for seat in managed_seats:
                    expected_faction = seat.get("faction_id")
                    expected_choice = resume_choices_by_seat.get(
                        int(seat["seat_index"])
                    )
                    participant = participants.get(
                        player_index_by_seat[int(seat["seat_index"])], {}
                    )
                    if not isinstance(expected_faction, int) \
                            or not isinstance(expected_choice, int) \
                            or participant.get("faction_choice_id") != expected_choice \
                            or participant.get("faction_id") != expected_faction:
                        mismatches.append({
                            "seat_index": seat["seat_index"],
                            "expected_faction_id": expected_faction,
                            "expected_faction_choice_id": expected_choice,
                            "participant": participant,
                        })
                if mismatches:
                    detail = json.dumps(mismatches, separators=(",", ":"))[:1600]
                    raise WorkerManagerError(
                        f"native_lan_loaded_faction_binding_mismatch:{detail}"
                    )
            else:
                for seat in managed_seats:
                    seat_index = int(seat["seat_index"])
                    instance_id = str(seat["instance_id"])
                    lobby = self._wait_native(
                        instance_id, "semantic_lan",
                        lambda value, expected=network_session_id: (
                            value.get("lifecycle") == "lobby"
                            and value.get("identity", {}).get("network_session_id") == expected
                            and value.get("lobby", {}).get("game_type")
                            == "multiplayer_scenario"
                            and value.get("lobby", {}).get("participant_count")
                            == participant_count
                        ), timeout=75,
                        context=f"seat_{seat_index}_scenario_faction_lobby",
                        action="status",
                    )
                    available = lobby.get("lobby", {}).get(
                        "native_selector_record_ids", []
                    )
                    preferred = seat.get("metadata", {}).get(
                        "scenario_faction_choice_id"
                    )
                    choice = preferred if isinstance(preferred, int) else (
                        available[0] if available else None
                    )
                    if not isinstance(choice, int) or choice not in available:
                        detail = json.dumps({
                            "seat_index": seat_index, "preferred": preferred,
                            "available": available,
                        }, separators=(",", ":"))[:1000]
                        raise WorkerManagerError(
                            f"native_lan_scenario_faction_unavailable:{detail}"
                        )
                    selected = self._native_request(
                        instance_id, "semantic_lan", action="select_faction",
                        faction_choice_id=choice,
                        match_id=lobby["identity"]["match_id"],
                        session_id=lobby["identity"]["session_id"],
                        expected_lobby_revision=lobby["lobby"]["revision"],
                        client_operation_id=self._lan_operation_id(
                            match_id, "scenario-faction", seat_index,
                        ),
                    )
                    if not selected.get("ok"):
                        detail = json.dumps(selected, separators=(",", ":"))[:1200]
                        raise WorkerManagerError(
                            f"native_lan_scenario_faction_selection_failed:{detail}"
                        )
                    scenario_choices_by_seat[seat_index] = choice
                    player_index = managed_seats.index(seat) + 1
                    host_lobby = self._wait_native(
                        host_instance, "semantic_lan",
                        lambda value, player_index=player_index,
                        faction_choice_id=choice: (
                            value.get("lifecycle") == "lobby"
                            and next(
                                (item for item in value.get("lobby", {}).get(
                                    "participants", []
                                ) if isinstance(item, Mapping)
                                 and item.get("player_index") == player_index),
                                {},
                            ).get("faction_choice_id") == faction_choice_id
                        ), timeout=75,
                        context=f"host_observed_scenario_faction_{seat_index}",
                        action="status",
                    )
            for seat in managed_seats[1:]:
                seat_index = int(seat["seat_index"])
                instance_id = str(seat["instance_id"])
                lobby = self._wait_native(
                    instance_id, "semantic_lan",
                    lambda value: (
                        value.get("lifecycle") == "lobby"
                        and (
                            value.get("lobby", {}).get("game_type") == "load"
                            if resume_slot is not None else
                            value.get("lobby", {}).get("game_type")
                            == "multiplayer_scenario"
                            if scenario_id is not None else
                            self._lan_settings_match(
                                value.get("lobby", {}).get("settings"),
                                normalized_lan_settings, profile,
                            )
                        )
                    ), timeout=45, context=f"client_{seat_index}_configured_lobby", action="status",
                )
                identity = lobby["identity"]
                ready = self._native_request(
                    instance_id, "semantic_lan", action="set_ready", ready=True,
                    match_id=identity["match_id"], session_id=identity["session_id"],
                    expected_lobby_revision=lobby["lobby"]["revision"],
                    client_operation_id=self._lan_operation_id(match_id, "ready", seat_index),
                )
                if not ready.get("ok"):
                    raise WorkerManagerError("native_lan_ready_failed")
            if external_human_seats:
                human_players = [
                    {
                        "seat_index": int(seat["seat_index"]),
                        "player_name": str(seat["metadata"]["external_player_name"]),
                        "expected_faction_id": seat.get("faction_id"),
                        "expected_faction_key": seat.get("metadata", {}).get(
                            "requested_faction_key"
                        ),
                        "expected_faction_name": seat.get("metadata", {}).get(
                            "requested_faction_name"
                        ),
                        "expected_faction_choice_id": seat.get("metadata", {}).get(
                            "requested_faction_choice_id"
                        ),
                    }
                    for seat in external_human_seats
                ]
                match = self.control.update_match_lifecycle(
                    match_id, "lobby", host_instance_id=host_instance,
                    metadata={
                        "network_session_id": network_session_id,
                        "external_lan": {
                            "network": external_network,
                            "host_address": host_address,
                            "session_name": session_name,
                            "human_players": human_players,
                            "resume_slot": resume_slot,
                            "scenario_id": scenario_id,
                            "game_settings": normalized_lan_settings,
                        },
                    },
                )
                return {
                    "ok": True,
                    "match": match,
                    "network_session_id": network_session_id,
                    "profile": profile,
                    "resume_slot": resume_slot,
                    "scenario_id": scenario_id,
                    "game_settings": normalized_lan_settings,
                    "loaded_checkpoint": loaded_checkpoint,
                    "lobby_open": True,
                    "awaiting_external_humans": True,
                    "external_join": {
                        "host_address": host_address,
                        "session_name": session_name,
                        "network": external_network,
                        "human_players": human_players,
                        "instructions": (
                            "Join Multiplayer > TCP/IP using host_address, enter the exact "
                            "assigned player_name, select the faction reserved for that seat, "
                            "and mark Ready. Then ask the Control Center to start again."
                        ),
                    },
                    "pixels_or_ui_input_used": False,
                }
            host_lobby = self._wait_native(
                host_instance, "semantic_lan",
                lambda value: (
                    value.get("lifecycle") == "lobby"
                    and value.get("lobby", {}).get("all_clients_ready") is True
                ), timeout=45, context="host_all_clients_ready", action="status",
            )
            self.control.update_match_lifecycle(
                match_id, "lobby", host_instance_id=host_instance,
                metadata={"network_session_id": network_session_id},
            )
            identity = host_lobby["identity"]
            started = self._native_request(
                host_instance, "semantic_lan", action="start",
                match_id=identity["match_id"], session_id=identity["session_id"],
                expected_lobby_revision=host_lobby["lobby"]["revision"],
                client_operation_id=self._lan_operation_id(match_id, "start"),
            )
            if not started.get("ok"):
                raise WorkerManagerError("native_lan_start_failed")
            native: list[dict[str, Any]] = []
            for seat in managed_seats:
                instance_id = str(seat["instance_id"])
                game = self._wait_native(
                    instance_id, "semantic_lan",
                    lambda value: value.get("lifecycle") == "game",
                    timeout=max(60.0, deadline - time.monotonic()), poll_seconds=0.5,
                    context=f"seat_{seat['seat_index']}_game", action="status",
                )
                snapshot = self._wait_native(
                    instance_id, "semantic_snapshot",
                    lambda value: isinstance(value.get("snapshot", {}).get("faction"), Mapping),
                    timeout=45, context=f"seat_{seat['seat_index']}_snapshot",
                )
                faction = snapshot["snapshot"]["faction"]
                seat_index = int(seat["seat_index"])
                faction_choice_id = resume_choices_by_seat.get(
                    seat_index, scenario_choices_by_seat.get(
                        seat_index, new_game_choices_by_seat.get(seat_index))
                )
                seat_metadata = {
                    "network_session_id": network_session_id,
                    "native_role": "host" if seat is host else "client",
                }
                if isinstance(faction_choice_id, int):
                    seat_metadata[
                        "native_loaded_faction_choice_id" if resume_slot is not None
                        else "native_scenario_faction_choice_id" if scenario_id is not None
                        else "native_new_game_faction_choice_id"
                    ] = faction_choice_id
                self.control.update_lan_seat(
                    match_id, int(seat["seat_index"]),
                    faction_id=int(faction["id"]), faction_name=str(faction.get("name") or "") or None,
                    metadata=seat_metadata,
                )
                native.append({
                    "seat_index": int(seat["seat_index"]), "instance_id": instance_id,
                    "faction_id": int(faction["id"]), "faction_name": faction.get("name"),
                    "faction_choice_id": faction_choice_id,
                    "lifecycle": game["lifecycle"],
                })
            match = self.control.update_match_lifecycle(
                match_id, "running", host_instance_id=host_instance,
                metadata={"network_session_id": network_session_id,
                          "participant_count": len(managed_seats) + len(external_human_seats),
                          "delegated_native_ai_seats": [
                              int(item["seat_index"]) for item in delegated_seats
                          ]},
            )
            return {
                "ok": True, "match": match, "network_session_id": network_session_id,
                "profile": profile, "resume_slot": resume_slot,
                "scenario_id": scenario_id,
                "game_settings": normalized_lan_settings,
                "loaded_checkpoint": loaded_checkpoint, "seats": native,
                "pixels_or_ui_input_used": False,
            }
        except Exception as exc:
            self.control.update_match_lifecycle(
                match_id, "error", host_instance_id=host_instance,
                metadata={"last_lan_error": str(exc)[:1000]},
            )
            raise

    def finalize_external_lan_match(self, match_id: str, *,
                                    timeout: float = 420.0) -> dict[str, Any]:
        """Start an already-open mixed human/agent lobby after exact validation."""
        match = self.control.get_match(match_id)
        seats = self.control.list_seats(match_id)
        managed_seats = [seat for seat in seats if seat.get("instance_id")]
        human_seats = [
            seat for seat in seats
            if seat["controller_kind"] == "human" and not seat.get("instance_id")
        ]
        external = match.get("metadata", {}).get("external_lan")
        if match["status"] != "lobby" or not managed_seats or not human_seats \
                or not isinstance(external, Mapping):
            raise WorkerManagerError("external_lan_lobby_not_open")
        self._external_lan_network()
        host_instance = str(managed_seats[0]["instance_id"])
        host_lobby = self._native_request(
            host_instance, "semantic_lan", action="status",
        )
        if host_lobby.get("lifecycle") != "lobby":
            raise WorkerManagerError("external_lan_native_lobby_not_active")
        expected_session = match["metadata"].get("network_session_id")
        if host_lobby.get("identity", {}).get("network_session_id") != expected_session:
            raise WorkerManagerError("external_lan_network_session_changed")
        expected_managed_names = {
            str(seat.get("metadata", {}).get("external_player_name") or (
                self._managed_lan_player_name(int(seat["seat_index"]), seat)
            ))
            for seat in managed_seats
        }
        expected_human_names = {
            str(seat["metadata"].get("external_player_name") or "")
            for seat in human_seats
        }
        expected_names = expected_managed_names | expected_human_names
        host_lobby, rejected_players = self._remove_unassigned_native_participants(
            match_id, host_instance, host_lobby, expected_names,
        )
        raw_participants = host_lobby.get("lobby", {}).get("participants", [])
        participants = [
            dict(item) for item in raw_participants
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        ]
        by_name: dict[str, dict[str, Any]] = {}
        duplicate_names: set[str] = set()
        for participant in participants:
            name = str(participant["name"])
            key = self._lan_name_key(name)
            if key in by_name:
                duplicate_names.add(name)
            by_name[key] = participant
        expected_keys = {self._lan_name_key(name) for name in expected_names}
        unexpected = sorted(
            str(item["name"]) for item in participants
            if self._lan_name_key(str(item["name"])) not in expected_keys
        )
        if duplicate_names or unexpected:
            detail = json.dumps({
                "duplicate_names": sorted(duplicate_names),
                "unexpected_players": unexpected,
            }, separators=(",", ":"))[:1200]
            raise WorkerManagerError(f"external_lan_participant_identity_mismatch:{detail}")

        resume_slot = external.get("resume_slot")
        human_state: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        for seat in human_seats:
            player_name = str(seat["metadata"].get("external_player_name") or "")
            participant = by_name.get(self._lan_name_key(player_name))
            state = {
                "seat_index": int(seat["seat_index"]),
                "player_name": player_name,
                "joined": participant is not None,
                "ready": bool(participant and participant.get("ready") is True),
                "faction_id": participant.get("faction_id") if participant else None,
                "faction_choice_id": (
                    participant.get("faction_choice_id") if participant else None
                ),
            }
            human_state.append(state)
            if participant is None:
                blockers.append({"player_name": player_name, "reason": "not_joined"})
                continue
            faction_id = participant.get("faction_id")
            if not isinstance(faction_id, int):
                blockers.append({"player_name": player_name, "reason": "faction_not_selected"})
                continue
            if resume_slot is None:
                expected_choice = seat.get("metadata", {}).get(
                    "requested_faction_choice_id"
                )
                if isinstance(expected_choice, int) and \
                        participant.get("faction_choice_id") != expected_choice:
                    blockers.append({
                        "player_name": player_name,
                        "reason": "reserved_faction_not_selected",
                        "expected_faction_choice_id": expected_choice,
                        "observed_faction_choice_id": participant.get("faction_choice_id"),
                    })
                    continue
            if resume_slot is not None:
                expected_faction = seat.get("faction_id")
                required_choice = participant.get("required_faction_choice_id")
                if faction_id != expected_faction \
                        or not isinstance(required_choice, int) \
                        or participant.get("faction_choice_id") != required_choice:
                    blockers.append({
                        "player_name": player_name,
                        "reason": "saved_faction_not_restored",
                        "expected_faction_id": expected_faction,
                        "observed_faction_id": faction_id,
                        "required_faction_choice_id": required_choice,
                    })
                    continue
            if participant.get("ready") is not True:
                blockers.append({"player_name": player_name, "reason": "not_ready"})

        if len(participants) != len(seats):
            blockers.append({
                "reason": "participant_count",
                "expected": len(seats),
                "observed": len(participants),
            })
        if blockers or host_lobby.get("lobby", {}).get("all_clients_ready") is not True:
            return {
                "ok": True,
                "match": match,
                "lobby_open": True,
                "awaiting_external_humans": True,
                "external_join": {
                    "host_address": external.get("host_address"),
                    "session_name": external.get("session_name"),
                    "human_players": human_state,
                    "blockers": blockers,
                    "rejected_players": rejected_players,
                },
                "pixels_or_ui_input_used": False,
            }

        participants_by_name = by_name
        for seat in human_seats:
            player_name = str(seat["metadata"]["external_player_name"])
            participant = participants_by_name[self._lan_name_key(player_name)]
            self.control.update_lan_seat(
                match_id, int(seat["seat_index"]),
                faction_id=int(participant["faction_id"]),
                metadata={
                    "network_join_pending": False,
                    "network_player_index": participant.get("player_index"),
                    "network_session_id": expected_session,
                    "native_role": "external_client",
                },
            )
        identity = host_lobby["identity"]
        started = self._native_request(
            host_instance, "semantic_lan", action="start",
            match_id=identity["match_id"], session_id=identity["session_id"],
            expected_lobby_revision=host_lobby["lobby"]["revision"],
            client_operation_id=self._lan_operation_id(match_id, "start"),
        )
        if not started.get("ok"):
            raise WorkerManagerError("native_lan_start_failed")

        deadline = time.monotonic() + min(max(float(timeout), 60.0), 900.0)
        native: list[dict[str, Any]] = []
        for seat in managed_seats:
            instance_id = str(seat["instance_id"])
            game = self._wait_native(
                instance_id, "semantic_lan",
                lambda value: value.get("lifecycle") == "game",
                timeout=max(30.0, deadline - time.monotonic()), poll_seconds=0.5,
                context=f"seat_{seat['seat_index']}_external_game", action="status",
            )
            snapshot = self._wait_native(
                instance_id, "semantic_snapshot",
                lambda value: isinstance(value.get("snapshot", {}).get("faction"), Mapping),
                timeout=45, context=f"seat_{seat['seat_index']}_external_snapshot",
            )
            faction = snapshot["snapshot"]["faction"]
            self.control.update_lan_seat(
                match_id, int(seat["seat_index"]),
                faction_id=int(faction["id"]),
                faction_name=str(faction.get("name") or "") or None,
                metadata={
                    "network_session_id": expected_session,
                    "native_role": "host" if int(seat["seat_index"]) == 0 else "client",
                },
            )
            native.append({
                "seat_index": int(seat["seat_index"]),
                "controller_kind": "agent",
                "instance_id": instance_id,
                "faction_id": int(faction["id"]),
                "faction_name": faction.get("name"),
                "lifecycle": game["lifecycle"],
            })
        for seat in self.control.list_seats(match_id):
            if seat["controller_kind"] != "human":
                continue
            native.append({
                "seat_index": int(seat["seat_index"]),
                "controller_kind": "human",
                "player_name": seat["metadata"].get("external_player_name"),
                "faction_id": seat.get("faction_id"),
                "faction_name": seat.get("faction_name"),
                "lifecycle": "external_game",
            })
        running = self.control.update_match_lifecycle(
            match_id, "running", host_instance_id=host_instance,
            metadata={
                "network_session_id": expected_session,
                "participant_count": len(seats),
            },
        )
        return {
            "ok": True,
            "match": running,
            "network_session_id": expected_session,
            "resume_slot": resume_slot,
            "seats": sorted(native, key=lambda item: int(item["seat_index"])),
            "external_humans_connected": True,
            "pixels_or_ui_input_used": False,
        }

    def lan_match_status(self, match_id: str) -> dict[str, Any]:
        seats = self.control.list_seats(match_id)
        observed_agents: dict[int, dict[str, Any]] = {}
        host_native: dict[str, Any] | None = None
        host_snapshot: dict[str, Any] | None = None
        for seat in seats:
            instance_id = seat.get("instance_id")
            if not instance_id:
                continue
            worker = self.worker_status(str(instance_id))
            native = None
            snapshot = None
            if worker.get("running") and worker.get("health") == "healthy":
                if seat.get("controller_kind") == "human" and \
                        seat.get("metadata", {}).get("managed") is True:
                    try:
                        self.human_ui_state(str(instance_id))
                    except WorkerManagerError:
                        pass
                try:
                    native = self._native_request(str(instance_id), "semantic_lan", action="status")
                except WorkerManagerError:
                    native = {"ok": False, "error": "bridge_unavailable"}
                try:
                    snapshot_response = self._native_request(
                        str(instance_id), "semantic_snapshot",
                    )
                    candidate = snapshot_response.get("snapshot")
                    if isinstance(candidate, Mapping):
                        snapshot = dict(candidate)
                except WorkerManagerError:
                    snapshot = None
            if isinstance(native, dict) and (
                int(seat["seat_index"]) == 0 or host_native is None
            ):
                host_native = native
            if snapshot is not None:
                faction = snapshot.get("faction")
                outcome = snapshot.get("outcome")
                seat_metadata: dict[str, Any] = {}
                if isinstance(outcome, Mapping):
                    seat_metadata["outcome"] = dict(outcome)
                if isinstance(faction, Mapping) and isinstance(faction.get("id"), int):
                    self.control.update_lan_seat(
                        match_id, int(seat["seat_index"]),
                        faction_id=int(faction["id"]),
                        faction_name=(str(faction["name"]) if faction.get("name") else None),
                        metadata=seat_metadata,
                    )
                elif seat_metadata:
                    self.control.update_lan_seat(
                        match_id, int(seat["seat_index"]), metadata=seat_metadata,
                    )
                if int(seat["seat_index"]) == 0 or host_snapshot is None:
                    host_snapshot = snapshot
            observed_agents[int(seat["seat_index"])] = {
                "seat_index": seat["seat_index"], "controller_kind": seat["controller_kind"],
                "agent_id": seat["agent_id"],
                "instance_id": instance_id, "faction_id": seat.get("faction_id"),
                "worker": worker, "native": native,
                "progress": ({
                    "turn": snapshot.get("turn"), "year": snapshot.get("year"),
                    "faction": snapshot.get("faction"),
                } if snapshot is not None else None),
                "outcome": (snapshot.get("outcome") if snapshot is not None else None),
            }
        results: list[dict[str, Any]] = []
        for seat in seats:
            seat_index = int(seat["seat_index"])
            if seat_index in observed_agents:
                results.append(observed_agents[seat_index])
                continue
            participant = None
            player_name = seat.get("metadata", {}).get("external_player_name")
            if host_native and host_native.get("lifecycle") == "lobby":
                participant = next(
                    (dict(item) for item in host_native.get("lobby", {}).get(
                        "participants", []
                    ) if isinstance(item, Mapping) and item.get("name") == player_name),
                    None,
                )
            results.append({
                "seat_index": seat_index,
                "controller_kind": seat["controller_kind"],
                "provisioned": False,
                "player_name": player_name,
                "faction_id": seat.get("faction_id"),
                "external_participant": participant,
            })
        if host_snapshot is not None and isinstance(host_snapshot.get("turn"), int) \
                and isinstance(host_snapshot.get("year"), int):
            self.control.record_match_progress(
                match_id, int(host_snapshot["turn"]), int(host_snapshot["year"]),
            )
        host_outcome = host_snapshot.get("outcome") if host_snapshot is not None else None
        if isinstance(host_outcome, Mapping) and host_outcome.get("final_score_completed") is True:
            current_match = self.control.get_match(match_id)
            if current_match.get("status") != "completed":
                self.control.update_match_lifecycle(
                    match_id, "completed", metadata={"outcome": dict(host_outcome)},
                )
        return {
            "ok": True, "match_id": match_id, "seats": results,
            "progress": ({
            "turn": host_snapshot.get("turn"), "year": host_snapshot.get("year"),
            } if host_snapshot is not None else None),
            "outcome": (dict(host_outcome) if isinstance(host_outcome, Mapping) else None),
        }

    def park_match(self, match_id: str) -> dict[str, Any]:
        seats = self.control.list_seats(match_id)
        parked = []
        for seat in reversed(seats):
            if seat.get("instance_id"):
                parked.append(self.park_worker(str(seat["instance_id"])))
        match = self.control.update_match_lifecycle(match_id, "parked")
        return {"ok": True, "match": match, "workers": parked}

    def complete_match(self, match_id: str) -> dict[str, Any]:
        """Seal an already parked match as intentionally completed."""
        match = self.control.get_match(match_id)
        already_completed = match["status"] == "completed"
        if match["status"] not in {"parked", "completed"}:
            raise WorkerManagerError("match_completion_requires_parked_match")
        completed = match if already_completed else self.control.update_match_lifecycle(
            match_id, "completed", metadata={"ended_by_managed_vote": True},
        )
        released = []
        release_errors = []
        seats = self.control.list_seats(match_id)
        managed = [seat for seat in seats if isinstance(seat.get("instance_id"), str)]
        configured_host = int(match.get("metadata", {}).get("managed_host_seat_index", 0))
        archive_seat = next(
            (int(seat["seat_index"]) for seat in managed
             if int(seat["seat_index"]) == configured_host),
            int(managed[0]["seat_index"]) if managed else -1,
        )
        for seat in seats:
            instance_id = seat.get("instance_id")
            if not isinstance(instance_id, str):
                continue
            try:
                released.append(self.retire_completed_worker(
                    instance_id,
                    preserve_final=int(seat["seat_index"]) == archive_seat,
                ))
            except Exception as exc:
                release_errors.append({"instance_id": instance_id, "error": str(exc)[:300]})
        return {
            "ok": True, "match": completed, "already_completed": already_completed,
            "released_workers": released, "release_errors": release_errors,
        }

    def retire_completed_worker(self, instance_id: str, *, preserve_final: bool = False) -> dict[str, Any]:
        """Release a completed seat's bulky Wine prefix and ephemeral secrets.

        Match metadata, semantic memory, events, metrics, and Hermes telemetry
        stay in their durable stores. This operation is intentionally only
        reachable after the campaign is non-resumable.
        """
        spec = self.control.get_worker_spec(instance_id)
        if spec.get("observed_status") == "retired":
            return {
                "instance_id": instance_id, "status": "retired",
                "removed_volumes": [], "final_archive": None,
                "already_retired": True,
            }
        self.park_worker(instance_id)
        spec = self.control.get_worker_spec(instance_id)
        final_archive = self.compact_worker_state(instance_id, completed=preserve_final)
        removed: list[str] = []
        for volume, purpose in (
            (spec["data_volume"], "worker-data"),
            (spec["network"].get("secret_volume"), "worker-secret"),
        ):
            if not isinstance(volume, str) or not volume:
                continue
            try:
                resource = self.docker.inspect_volume(volume)
                self.docker.require_owned(resource, self.installation_id, purpose=purpose)
                self.docker.remove_volume(volume)
                removed.append(volume)
            except DockerNotFound:
                pass
        self.control.vault.revoke(str(spec["bridge_secret_id"]))
        if spec.get("view_secret_id"):
            self.control.vault.revoke(str(spec["view_secret_id"]))
        network = dict(spec["network"])
        network.update({
            "storage_released": True, "secret_volume": None,
            "mcp_status": "retired", "view_status": "retired",
        })
        self.control.update_worker_network(instance_id, network)
        updated = self.control.update_worker_observation(
            instance_id, desired_status="retired", observed_status="retired",
            last_error="", bridge_host=None, bridge_port=None, instance_status="retired",
        )
        return {
            "instance_id": instance_id, "status": updated["observed_status"],
            "removed_volumes": removed, "final_archive": final_archive,
        }

    def checkpoint_match(self, match_id: str, *,
                         slot: str = "control_recovery") -> dict[str, Any]:
        """Create and record one verified native checkpoint for managed recovery."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", slot):
            raise InvalidRecord("invalid_save_slot")
        match = self.control.get_match(match_id)
        if match["status"] != "running":
            raise WorkerManagerError("checkpoint_requires_running_match")
        seats = self.control.list_seats(match_id)
        # A browser-managed human host is just as recoverable as an agent:
        # both have an isolated worker and authenticated native bridge. Only a
        # truly external host lacks a worker we can checkpoint safely.
        host_instance = match.get("host_instance_id")
        if not isinstance(host_instance, str) or not host_instance:
            host_index = int(match.get("metadata", {}).get("managed_host_seat_index", 0))
            host_seat = next(
                (item for item in seats if int(item["seat_index"]) == host_index), None,
            )
            host_instance = host_seat.get("instance_id") if host_seat else None
        if not seats or not isinstance(host_instance, str) or not host_instance:
            raise WorkerManagerError("external_human_host_owns_checkpoint")
        host_instance_id = host_instance
        managed_instances = [
            str(seat["instance_id"]) for seat in seats
            if seat.get("instance_id") and
            seat.get("metadata", {}).get("delegation_status") != "active"
        ]
        controller_by_instance = {
            str(seat["instance_id"]): str(seat.get("controller_kind", "agent"))
            for seat in seats if seat.get("instance_id")
        }
        stable_samples: list[dict[str, Any]] = []
        previous_signature: tuple[tuple[str, int, int, str, str], ...] | None = None
        for sample_index in range(3):
            observed: list[tuple[str, int, int, str, str]] = []
            for instance_id in managed_instances:
                envelope = self._native_request(
                    instance_id, "semantic_snapshot", timeout=20.0,
                )
                snapshot = envelope.get("snapshot")
                if not isinstance(snapshot, Mapping):
                    raise WorkerManagerError("checkpoint_snapshot_unavailable")
                protocol = snapshot.get("protocol")
                phase = protocol.get("phase") if isinstance(protocol, Mapping) else None
                if controller_by_instance.get(instance_id) == "human":
                    # Human workers deliberately do not expose an AI decision
                    # protocol, so their semantic phase can be capability_gap
                    # during perfectly ordinary play.  Gate them on the
                    # private human UI state instead: a stable map with no
                    # native menu, submenu, modal, or page open is safe to
                    # serialize without interrupting an in-progress dialog.
                    human_ui = self._native_request(
                        instance_id, "human_ui_state", timeout=8.0,
                    )
                    if human_ui.get("lifecycle") != "game" or any(
                        human_ui.get(field) is True for field in (
                            "native_menu_visible", "modal_open", "native_page_open",
                        )
                    ):
                        raise WorkerManagerError(
                            "checkpoint_waiting_for_human_interaction"
                        )
                    phase = "human_idle"
                elif phase not in {"turn", "wait"}:
                    raise WorkerManagerError(
                        f"checkpoint_waiting_for_quiescence:{phase or 'unknown'}"
                    )
                observed.append((
                    instance_id, int(snapshot.get("turn", -1)),
                    int(snapshot.get("year", -1)), str(phase),
                    str(snapshot.get("revision", "")),
                ))
            signature = tuple(observed)
            # Every managed peer must agree on the serialized turn/year.  A
            # revision may differ by perspective, so compare each exact peer
            # with itself across three packet-pump intervals.
            if len({(row[1], row[2]) for row in observed}) != 1:
                raise WorkerManagerError("checkpoint_peers_not_synchronized")
            stable_samples.append({
                "index": sample_index + 1,
                "turn": observed[0][1], "year": observed[0][2],
                "peers": len(observed),
            })
            if previous_signature is not None and signature != previous_signature:
                raise WorkerManagerError("checkpoint_state_changed_during_quiescence")
            previous_signature = signature
            if sample_index < 2:
                time.sleep(0.35)
        choices = self._native_request(
            host_instance_id, "semantic_choices", kind="game_management",
            timeout=30.0,
        )
        choice = next(
            (item for item in choices.get("choices", [])
             if isinstance(item, Mapping) and item.get("command") == "save_game"),
            None,
        )
        if not choice:
            raise WorkerManagerError("native_checkpoint_not_currently_legal")
        saved = self._native_request(
            host_instance_id, "semantic_command", command="save_game", slot=slot,
            match_id=choices.get("match_id"), session_id=choices.get("session_id"),
            expected_revision=choices.get("revision"), timeout=30.0,
        )
        if not saved.get("ok"):
            raise WorkerManagerError("native_checkpoint_failed")
        checkpoint = {
            "slot": slot,
            "verified": True,
            "created_unix": time.time(),
            "host_instance_id": host_instance_id,
            "turn": saved.get("turn"),
            "year": saved.get("year"),
            "path": saved.get("path"),
            "quiescence_samples": stable_samples,
            "managed_peer_count": len(managed_instances),
        }
        updated = self.control.update_match_lifecycle(
            match_id, "running", metadata={"recovery_checkpoint": checkpoint,
                                           "recovery_required": False},
        )
        return {"ok": True, "match": updated, "checkpoint": checkpoint}

    def recover_match(self, match_id: str) -> dict[str, Any]:
        """Resume a managed match only from its last bridge-verified checkpoint."""
        match = self.control.get_match(match_id)
        checkpoint = match.get("metadata", {}).get("recovery_checkpoint")
        if not isinstance(checkpoint, Mapping) or checkpoint.get("verified") is not True:
            raise WorkerManagerError("verified_recovery_checkpoint_required")
        slot = str(checkpoint.get("slot") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", slot):
            raise WorkerManagerError("invalid_recovery_checkpoint")
        seats = self.control.list_seats(match_id)
        host_index = int(match.get("metadata", {}).get("managed_host_seat_index", 0))
        host_seat = next(
            (item for item in seats if int(item["seat_index"]) == host_index), None,
        )
        if not seats or host_seat is None or not host_seat.get("instance_id"):
            raise WorkerManagerError("external_human_host_recovery_required")
        self.park_match(match_id)
        if match["mode"] == "lan":
            # A fresh typed/custom lobby records the descriptive profile as
            # `custom`, but a recovery loads an already-serialized native save
            # and therefore must not reapply map settings. start_lan_match
            # still requires one supported bootstrap profile for its menu-free
            # recovery path; use the neutral small profile when the recorded
            # fresh-game label is not one of those bootstrap profiles.
            recorded_profile = str(match.get("metadata", {}).get(
                "lan_profile", "small_easy"
            ))
            recovery_profile = (
                recorded_profile if recorded_profile in LAN_PROFILES else "small_easy"
            )
            result = self.start_lan_match(
                match_id,
                session_name=str(match.get("metadata", {}).get(
                    "lan_session_name", "SMACX Managed LAN"
                )),
                profile=recovery_profile,
                resume_slot=slot,
            )
        elif match["mode"] == "singleplayer":
            instance_id = str(host_seat.get("instance_id") or "")
            if not instance_id:
                raise WorkerManagerError("managed_solo_worker_missing")
            spec = self.control.get_worker_spec(instance_id)
            autostart = dict(spec["autostart"])
            autostart["enabled"] = False
            autostart["startup_save"] = slot
            self.control.update_worker_autostart(instance_id, autostart)
            started = self.start_worker(instance_id)
            loaded = self._wait_native(
                instance_id, "semantic_snapshot",
                lambda value: value.get("ok") is True
                and isinstance(value.get("snapshot"), Mapping)
                and value["snapshot"].get("turn") is not None,
                timeout=120.0, context="solo_recovery",
            )
            running = self.control.update_match_lifecycle(
                match_id, "running", host_instance_id=instance_id,
                metadata={"recovery_required": False, "last_recovered_unix": time.time(),
                          "last_recovered_slot": slot},
            )
            result = {"ok": True, "match": running, "worker": started,
                      "loaded_checkpoint": loaded}
        else:
            raise WorkerManagerError("unsupported_match_recovery_mode")
        self.control.update_match_lifecycle(
            match_id, "running", metadata={"recovery_required": False,
                                           "last_recovered_unix": time.time(),
                                           "last_recovered_slot": slot},
        )
        return result

    def start_mcp_sidecar(self, instance_id: str, *, timeout: float = 90.0) -> dict[str, Any]:
        if not self.control_data_volume:
            raise WorkerManagerError("managed_mcp_not_configured")
        spec = self.control.get_worker_spec(instance_id)
        if spec["observed_status"] != "running":
            raise WorkerManagerError("game_worker_not_running")
        game = self.docker.inspect_container(spec["container_name"])
        self.docker.require_owned(game, self.installation_id, purpose="game-worker")
        if not game.get("State", {}).get("Running"):
            raise WorkerManagerError("game_worker_not_running")
        self.docker.inspect_image(self.mcp_image)
        self.docker.inspect_volume(self.control_data_volume)
        for volume_name, purpose in (
            (spec["data_volume"], "worker-data"),
            (spec["network"]["secret_volume"], "worker-secret"),
        ):
            resource = self.docker.inspect_volume(volume_name)
            self.docker.require_owned(resource, self.installation_id, purpose=purpose)

        container_name = self._name("mcp", instance_id)
        try:
            old = self.docker.inspect_container(container_name)
            self.docker.require_owned(old, self.installation_id, purpose="mcp-sidecar")
            if old.get("State", {}).get("Running"):
                binding = old.get("NetworkSettings", {}).get("Ports", {}).get("47815/tcp")
                host_port = int(binding[0]["HostPort"]) if isinstance(binding, list) and binding else None
                health = old.get("State", {}).get("Health", {}).get("Status")
                if host_port and health == "healthy":
                    network = dict(spec["network"])
                    network.update({
                        "mcp_container_name": container_name,
                        "mcp_status": "running",
                        "mcp_host_port": host_port,
                        "mcp_url": f"http://127.0.0.1:{host_port}/mcp",
                    })
                    self.control.update_worker_network(instance_id, network)
                    return {
                        "ok": True, "status": "running", "container_name": container_name,
                        "host_port": host_port, "url": network["mcp_url"],
                    }
                self.docker.stop_container(container_name, timeout=10)
            self.docker.remove_container(container_name)
        except DockerNotFound:
            pass

        bridge_host = spec["container_name"]
        if not self.network_name:
            networks = game.get("NetworkSettings", {}).get("Networks", {})
            addresses = [
                value.get("IPAddress") for value in networks.values()
                if isinstance(value, Mapping) and value.get("IPAddress")
            ] if isinstance(networks, Mapping) else []
            if not addresses:
                raise WorkerManagerError("game_worker_network_address_unavailable")
            bridge_host = str(addresses[0])
        labels = self._labels(
            "mcp-sidecar", **{
                "io.smacx.instance": instance_id,
                "io.smacx.match": spec["match_id"],
            },
        )
        config = {
            "Image": self.mcp_image,
            "Entrypoint": ["/usr/bin/tini", "--", "/usr/local/bin/smacx-mcp"],
            "Cmd": [],
            "Env": [
                "HOME=/tmp",
                "SMACX_MANAGED_ATTACHED=1",
                f"SMACX_BRIDGE_HOST={bridge_host}",
                "SMACX_BRIDGE_PORT=47814",
                "SMACX_AGENT_TOKEN_FILE=/run/secrets/bridge-token",
                "SMACX_MCP_HOST=0.0.0.0",
                "SMACX_MCP_PORT=47815",
                "SMACX_DB_PATH=/var/lib/smacx/smacx.sqlite3",
                "SMACX_RUNTIME_ROOT=/worker-state",
                "SMACX_GAME_PATH=/worker-state/game",
                "SMACX_LEGACY_KNOWLEDGE_ROOT=/worker-state/legacy-knowledge",
                f"SMACX_REFERENCE_URL={os.environ.get('SMACX_REFERENCE_URL', 'http://knowledge-service:8090')}",
                f"SMACX_GRAPHITI_RECALL_URL={os.environ.get('SMACX_GRAPHITI_RECALL_URL', 'http://graphiti-projector:8091')}",
                "SMACX_CAPABILITY_GAP_LOG=/var/lib/smacx/capability-gaps.jsonl",
                f"SMACX_AGENT_ID={spec['agent_id']}",
                f"SMACX_PERSPECTIVE_ID={spec['perspective_id']}",
                f"SMACX_GAME_SOURCE_ID={spec['game_source_id']}",
            ],
            "Tty": True,
            "Labels": labels,
            "ExposedPorts": {"47815/tcp": {}},
            "Healthcheck": {
                "Test": [
                    "CMD", "python3", "-c",
                    "import socket;s=socket.create_connection(('127.0.0.1',47815),2);s.close()",
                ],
                "Interval": 2_000_000_000,
                "Timeout": 3_000_000_000,
                "StartPeriod": 5_000_000_000,
                "Retries": 10,
            },
            "HostConfig": {
                "NetworkMode": self.network_name or "bridge",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Tmpfs": {
                    "/tmp": "rw,nosuid,nodev,size=128m,mode=1777",
                    "/run": "rw,nosuid,nodev,size=16m,mode=0755",
                },
                "PortBindings": {"47815/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]},
                "Mounts": [
                    {"Type": "volume", "Source": self.control_data_volume,
                     "Target": "/var/lib/smacx"},
                    {"Type": "volume", "Source": spec["data_volume"],
                     "Target": "/worker-state"},
                    {"Type": "volume", "Source": spec["network"]["secret_volume"],
                     "Target": "/run/secrets", "ReadOnly": True},
                ],
            },
        }
        identifier: str | None = None
        try:
            identifier = self.docker.create_container(container_name, config)
            self.docker.start_container(identifier)
            deadline = time.monotonic() + min(max(float(timeout), 15.0), 180.0)
            inspected: dict[str, Any] = {}
            while time.monotonic() < deadline:
                inspected = self.docker.inspect_container(identifier)
                state = inspected.get("State", {})
                health = state.get("Health", {}).get("Status")
                if health == "healthy":
                    break
                if not state.get("Running") or health == "unhealthy":
                    raise WorkerManagerError("mcp_sidecar_failed_healthcheck")
                time.sleep(0.5)
            else:
                raise WorkerManagerError("mcp_sidecar_health_timeout")
            binding = inspected.get("NetworkSettings", {}).get("Ports", {}).get("47815/tcp")
            host_port = int(binding[0]["HostPort"]) if isinstance(binding, list) and binding else None
            if not host_port:
                raise WorkerManagerError("mcp_sidecar_port_unavailable")
            network = dict(spec["network"])
            network.update({
                "mcp_container_name": container_name,
                "mcp_status": "running",
                "mcp_host_port": host_port,
                "mcp_url": f"http://127.0.0.1:{host_port}/mcp",
            })
            self.control.update_worker_network(instance_id, network)
            return {
                "ok": True, "status": "running", "container_name": container_name,
                "host_port": host_port, "url": network["mcp_url"],
            }
        except Exception:
            if identifier:
                try:
                    self._cleanup_container(identifier, "mcp-sidecar")
                except Exception:
                    pass
            network = dict(spec["network"])
            network.update({
                "mcp_container_name": container_name,
                "mcp_status": "error",
                "mcp_host_port": None,
                "mcp_url": None,
            })
            self.control.update_worker_network(instance_id, network)
            raise

    def stop_mcp_sidecar(self, instance_id: str) -> dict[str, Any]:
        spec = self.control.get_worker_spec(instance_id)
        container_name = str(spec["network"].get("mcp_container_name") or self._name("mcp", instance_id))
        try:
            self._cleanup_container(container_name, "mcp-sidecar")
        except DockerNotFound:
            pass
        network = dict(spec["network"])
        network.update({
            "mcp_container_name": container_name,
            "mcp_status": "stopped",
            "mcp_host_port": None,
            "mcp_url": None,
        })
        self.control.update_worker_network(instance_id, network)
        return {"ok": True, "instance_id": instance_id, "status": "stopped"}

    def worker_status(self, instance_id: str) -> dict[str, Any]:
        spec = self.control.get_worker_spec(instance_id)
        try:
            container = self.docker.inspect_container(spec["container_name"])
            self.docker.require_owned(container, self.installation_id, purpose="game-worker")
        except DockerNotFound:
            return {"ok": True, "instance_id": instance_id, "container_present": False,
                    "observed_status": spec["observed_status"]}
        state = container.get("State", {})
        result = {
            "ok": True, "instance_id": instance_id, "container_present": True,
            "running": bool(state.get("Running")),
            "health": state.get("Health", {}).get("Status"),
            "exit_code": state.get("ExitCode"),
            "session_id": container.get("Config", {}).get("Labels", {}).get("io.smacx.session"),
            "spectator": {
                "enabled": bool(spec["network"].get("view_enabled")),
                "status": spec["network"].get("view_status") or "disabled",
                "host_port": spec["network"].get("view_host_port"),
                "path": spec["network"].get("view_path"),
                "mode": spec["network"].get("view_mode") or "view-only",
                "resolution_profile": spec["network"].get("resolution_profile") or "1280x800",
                "bitrate_kbps": spec["network"].get("stream_bitrate_kbps") or 3500,
                "encoder": "h264enc",
            },
        }
        mcp_name = spec["network"].get("mcp_container_name")
        if mcp_name:
            try:
                sidecar = self.docker.inspect_container(str(mcp_name))
                self.docker.require_owned(sidecar, self.installation_id, purpose="mcp-sidecar")
                result["mcp"] = {
                    "container_present": True,
                    "running": bool(sidecar.get("State", {}).get("Running")),
                    "health": sidecar.get("State", {}).get("Health", {}).get("Status"),
                    "url": spec["network"].get("mcp_url"),
                }
            except DockerNotFound:
                result["mcp"] = {"container_present": False, "running": False}
        return result

    def semantic_progress(self, instance_id: str) -> dict[str, Any]:
        """Return a compact supervisor-only marker for autonomous progress."""
        worker = self.worker_status(instance_id)
        if not worker.get("running") or worker.get("health") != "healthy":
            return {
                "available": False,
                "reason": "worker_not_healthy",
                "session_id": worker.get("session_id"),
            }
        try:
            result = self._native_request(instance_id, "semantic_snapshot")
        except WorkerManagerError as exc:
            return {"available": False, "reason": str(exc)[:240]}
        snapshot = result.get("snapshot")
        if not result.get("ok") or not isinstance(snapshot, Mapping):
            return {"available": False, "reason": "semantic_snapshot_unavailable"}
        protocol = snapshot.get("protocol") \
            if isinstance(snapshot.get("protocol"), Mapping) else {}
        outcome = snapshot.get("outcome") \
            if isinstance(snapshot.get("outcome"), Mapping) else {}
        return {
            "available": True,
            "match_id": snapshot.get("match_id"),
            "session_id": snapshot.get("session_id"),
            "revision": snapshot.get("revision"),
            "turn": snapshot.get("turn"),
            "year": snapshot.get("year"),
            "phase": protocol.get("phase"),
            "game_completed": outcome.get("game_completed") is True,
            "final_score_completed": outcome.get("final_score_completed") is True,
        }

    def spectator_access(self, instance_id: str, *, interactive: bool = False) -> dict[str, Any]:
        """Return one short-lived caller's upstream stream credential."""
        spec = self.control.get_worker_spec(instance_id)
        if not spec["network"].get("view_enabled") or not spec.get("view_secret_id"):
            raise WorkerManagerError("spectator_not_enabled")
        status = self.worker_status(instance_id)
        port = spec["network"].get("view_host_port")
        if not status.get("running") or status.get("health") != "healthy" \
                or not isinstance(port, int):
            raise WorkerManagerError("spectator_not_running")
        credentials = json.loads(self.control.vault.read(
            str(spec["view_secret_id"]),
            purpose=f"worker.{instance_id}.view_passwords",
        ))
        requested_mode = str(spec["network"].get("view_mode") or "view-only")
        if interactive and requested_mode != "interactive":
            raise WorkerManagerError("interactive_stream_not_available")
        return {
            "ok": True,
            "instance_id": instance_id,
            "mode": requested_mode,
            "host_port": port,
            "path": str(spec["network"].get("view_path") or "/vnc.html"),
            "access_mode": "interactive" if interactive else "view-only",
            "password": credentials["control"] if interactive else credentials["viewer"],
            "container_name": spec["container_name"],
            "internal_port": 6080,
            "internal_base_url": f"http://{spec['container_name']}:6080",
        }

    def compact_worker_state(self, instance_id: str, *, completed: bool = False) -> dict[str, Any]:
        """Prune and zstd-compress a stopped seat's durable native saves."""
        if not hasattr(self.docker, "commit_container"):
            return {"ok": True, "skipped": "legacy_docker_test_double",
                    "final_preserved": False}
        spec = self.control.get_worker_spec(instance_id)
        volume = self.docker.inspect_volume(spec["data_volume"])
        self.docker.require_owned(volume, self.installation_id, purpose="worker-data")
        policy = self.control.storage_policy()
        name = self._name("compact", f"{instance_id}-{uuid.uuid4().hex}")
        mounts = [{"Type": "volume", "Source": spec["data_volume"], "Target": "/state"}]
        preserve_final = completed and bool(self.control_data_volume)
        if preserve_final:
            self.docker.inspect_volume(str(self.control_data_volume))
            mounts.append({"Type": "volume", "Source": self.control_data_volume,
                           "Target": "/control"})
        identifier = self.docker.create_container(name, {
            "Image": self.worker_image,
            "Entrypoint": ["python3", "/opt/smacx/compact_saves.py"],
            "Cmd": [], "Tty": True,
            "Env": [
                f"SMACX_MATCH_ID={spec['match_id']}", f"SMACX_INSTANCE_ID={instance_id}",
                f"SMACX_RECENT_SAVES={policy['recent_checkpoints']}",
                f"SMACX_MILESTONE_INTERVAL={policy['milestone_interval']}",
                "SMACX_RETAIN_FULL_HISTORY=" + ("1" if policy["retain_full_turn_history"] else "0"),
                "SMACX_COMPLETED_MATCH=" + ("1" if preserve_final else "0"),
            ],
            "Labels": self._labels("worker-state-compactor", **{
                "io.smacx.instance": instance_id, "io.smacx.match": str(spec["match_id"]),
            }),
            "HostConfig": {
                "NetworkMode": "none", "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Tmpfs": {"/tmp": "rw,nosuid,nodev,size=64m,mode=1777"}, "Mounts": mounts,
            },
        })
        try:
            self.docker.start_container(identifier)
            stopped = self.docker.wait_container(identifier, timeout=180.0)
            logs = _clean_log(self.docker.container_logs(identifier, tail=40))
            if int(stopped.get("State", {}).get("ExitCode", -1)):
                raise WorkerManagerError(f"worker_state_compaction_failed:{logs[-1800:]}")
            result = self._last_json(logs)
            if result.get("ok") is not True:
                raise WorkerManagerError("worker_state_compaction_invalid_result")
            result["final_preserved"] = preserve_final
            return result
        finally:
            self._cleanup_container(identifier, "worker-state-compactor")

    def park_worker(self, instance_id: str) -> dict[str, Any]:
        spec = self.control.get_worker_spec(instance_id)
        self.stop_mcp_sidecar(instance_id)
        spec = self.control.get_worker_spec(instance_id)
        try:
            container = self.docker.inspect_container(spec["container_name"])
            self.docker.require_owned(container, self.installation_id, purpose="game-worker")
            if container.get("State", {}).get("Running"):
                self.docker.stop_container(spec["container_name"], timeout=20)
            self.docker.remove_container(spec["container_name"])
        except DockerNotFound:
            pass
        archive = self.compact_worker_state(instance_id)
        with self.store.transaction() as connection:
            session = connection.execute(
                "SELECT session_id FROM sessions WHERE instance_id=? AND status='running' "
                "ORDER BY started_unix DESC LIMIT 1", (instance_id,),
            ).fetchone()
        if session:
            self.store.close_session(str(session["session_id"]), status="parked")
        network = dict(spec["network"])
        if network.get("view_enabled"):
            network.update({
                "view_status": "parked",
                "view_host_port": None,
            })
            self.control.update_worker_network(instance_id, network)
        updated = self.control.update_worker_observation(
            instance_id, desired_status="parked", observed_status="parked", last_error="",
            bridge_host=None, bridge_port=None, instance_status="available",
        )
        return {"ok": True, "instance_id": instance_id, "status": updated["observed_status"],
                "save_archive": archive}
