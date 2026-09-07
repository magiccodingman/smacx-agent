"""Minimal Docker Engine client with strict SMACX ownership checks.

The Control Center talks directly to the local Unix socket so its runtime image
does not need a shell-facing Docker CLI or a broad third-party SDK.  Callers
must label every created object with the installation identity and may mutate
only objects carrying those labels.
"""

from __future__ import annotations

import http.client
import io
import json
from pathlib import Path
import socket
import time
import tarfile
from typing import Any, Mapping
from urllib.parse import quote, urlencode


MAX_DOCKER_RESPONSE = 16 * 1024 * 1024
MANAGED_LABEL = "io.smacx.managed"
INSTALLATION_LABEL = "io.smacx.installation"
PURPOSE_LABEL = "io.smacx.purpose"


class DockerError(RuntimeError):
    pass


class DockerUnavailable(DockerError):
    pass


class DockerNotFound(DockerError):
    pass


class DockerOwnershipError(DockerError):
    pass


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class DockerClient:
    def __init__(self, socket_path: str = "/var/run/docker.sock", *, timeout: float = 15.0) -> None:
        path = Path(socket_path)
        if not path.is_absolute() or len(socket_path) > 4096:
            raise DockerUnavailable("invalid_docker_socket_path")
        self.socket_path = str(path)
        self.timeout = min(max(float(timeout), 1.0), 120.0)

    def _request(self, method: str, path: str, *, payload: Any | None = None,
                 raw_body: bytes | None = None, content_type: str = "application/json",
                 expected: tuple[int, ...] = (200, 201, 204),
                 timeout: float | None = None) -> tuple[int, bytes]:
        if raw_body is not None and payload is not None:
            raise DockerError("conflicting_docker_request_body")
        body = raw_body
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Host": "docker"}
        if body is not None:
            headers.update({"Content-Type": content_type, "Content-Length": str(len(body))})
        request_timeout = self.timeout if timeout is None else min(max(float(timeout), 1.0), 900.0)
        connection = UnixHTTPConnection(self.socket_path, request_timeout)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(MAX_DOCKER_RESPONSE + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise DockerUnavailable("docker_engine_unavailable") from exc
        finally:
            connection.close()
        if len(data) > MAX_DOCKER_RESPONSE:
            raise DockerError("docker_response_too_large")
        if response.status not in expected:
            message = "docker_request_failed"
            try:
                decoded = json.loads(data)
                candidate = decoded.get("message") if isinstance(decoded, Mapping) else None
                if isinstance(candidate, str) and candidate:
                    # Docker messages can include host paths. Keep the detailed
                    # value internal to the exception but bound it tightly.
                    message = candidate[:1000]
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            if response.status == 404:
                raise DockerNotFound(message)
            raise DockerError(f"docker_http_{response.status}:{message}")
        return response.status, data

    def _json(self, method: str, path: str, *, payload: Any | None = None,
              expected: tuple[int, ...] = (200, 201),
              timeout: float | None = None) -> Any:
        _, data = self._request(method, path, payload=payload, expected=expected, timeout=timeout)
        if not data:
            return None
        try:
            return json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DockerError("invalid_docker_response") from exc

    def ping(self) -> bool:
        _, body = self._request("GET", "/_ping", expected=(200,))
        return body.strip() == b"OK"

    def version(self) -> dict[str, Any]:
        value = self._json("GET", "/version", expected=(200,))
        if not isinstance(value, dict):
            raise DockerError("invalid_docker_version_response")
        return value

    def inspect_image(self, image_ref: str) -> dict[str, Any]:
        value = self._json("GET", f"/images/{quote(image_ref, safe='')}/json", expected=(200,))
        if not isinstance(value, dict):
            raise DockerError("invalid_docker_image_response")
        return value

    def commit_container(self, identifier: str, repository: str, tag: str,
                         *, labels: Mapping[str, str] | None = None) -> str:
        """Commit one stopped preparation container as an installation-local image.

        The image is never pushed. Docker's content-addressed layers allow all
        game seats to share the prepared game and Wine prefix while retaining
        an isolated copy-on-write container layer.
        """
        query = urlencode({
            "container": identifier, "repo": repository, "tag": tag,
            "pause": "false",
        })
        payload: dict[str, Any] = {}
        if labels:
            payload["Labels"] = dict(labels)
        # A prepared image includes a complete game tree and initialized Proton
        # prefix.  Docker may need materially longer than an ordinary control
        # request to persist that layer on slower disks.
        value = self._json(
            "POST", f"/commit?{query}", payload=payload, expected=(201,), timeout=600.0,
        )
        if not isinstance(value, dict) or not isinstance(value.get("Id"), str):
            raise DockerError("invalid_docker_commit_response")
        return value["Id"]

    def create_volume(self, name: str, labels: Mapping[str, str]) -> dict[str, Any]:
        value = self._json("POST", "/volumes/create", payload={"Name": name, "Labels": dict(labels)})
        if not isinstance(value, dict):
            raise DockerError("invalid_docker_volume_response")
        return value

    def inspect_volume(self, name: str) -> dict[str, Any]:
        value = self._json("GET", f"/volumes/{quote(name, safe='')}", expected=(200,))
        if not isinstance(value, dict):
            raise DockerError("invalid_docker_volume_response")
        return value

    def inspect_network(self, name: str) -> dict[str, Any]:
        value = self._json("GET", f"/networks/{quote(name, safe='')}", expected=(200,))
        if not isinstance(value, dict):
            raise DockerError("invalid_docker_network_response")
        return value

    def remove_volume(self, name: str) -> None:
        self._request("DELETE", f"/volumes/{quote(name, safe='')}", expected=(204,))

    def create_container(self, name: str, config: Mapping[str, Any]) -> str:
        path = "/containers/create?" + urlencode({"name": name})
        value = self._json("POST", path, payload=dict(config), expected=(201,))
        if not isinstance(value, dict) or not isinstance(value.get("Id"), str):
            raise DockerError("invalid_docker_create_response")
        return value["Id"]

    def inspect_container(self, identifier: str) -> dict[str, Any]:
        value = self._json("GET", f"/containers/{quote(identifier, safe='')}/json", expected=(200,))
        if not isinstance(value, dict):
            raise DockerError("invalid_docker_container_response")
        return value

    def start_container(self, identifier: str) -> None:
        self._request("POST", f"/containers/{quote(identifier, safe='')}/start", expected=(204, 304))

    def stop_container(self, identifier: str, *, timeout: int = 20) -> None:
        query = urlencode({"t": min(max(int(timeout), 1), 60)})
        self._request(
            "POST", f"/containers/{quote(identifier, safe='')}/stop?{query}",
            expected=(204, 304),
        )

    def pause_container(self, identifier: str) -> None:
        self._request(
            "POST", f"/containers/{quote(identifier, safe='')}/pause", expected=(204,),
        )

    def unpause_container(self, identifier: str) -> None:
        self._request(
            "POST", f"/containers/{quote(identifier, safe='')}/unpause", expected=(204,),
        )

    def remove_container(self, identifier: str, *, volumes: bool = False) -> None:
        query = urlencode({"v": "true" if volumes else "false", "force": "false"})
        self._request(
            "DELETE", f"/containers/{quote(identifier, safe='')}?{query}", expected=(204,),
        )

    def container_logs(self, identifier: str, *, tail: int = 100) -> str:
        query = urlencode({"stdout": "true", "stderr": "true", "tail": min(max(int(tail), 1), 1000)})
        _, data = self._request(
            "GET", f"/containers/{quote(identifier, safe='')}/logs?{query}", expected=(200,),
        )
        # Non-TTY Docker log streams are multiplexed. Each payload has an
        # eight-byte header: stream byte, three reserved bytes, and a
        # big-endian payload length. Decoding that framing as text can leave a
        # printable length byte in front of valid helper JSON. TTY/raw output
        # has no framing, so retain it when the complete response is not a
        # valid frame sequence.
        cursor = 0
        frames: list[bytes] = []
        while cursor + 8 <= len(data):
            header = data[cursor:cursor + 8]
            if header[0] not in (0, 1, 2) or header[1:4] != b"\x00\x00\x00":
                frames = []
                break
            length = int.from_bytes(header[4:8], "big")
            end = cursor + 8 + length
            if end > len(data):
                frames = []
                break
            frames.append(data[cursor + 8:end])
            cursor = end
        if frames and cursor == len(data):
            data = b"".join(frames)
        return data.decode("utf-8", errors="replace")

    def put_archive(self, identifier: str, destination: str, archive: bytes) -> None:
        query = urlencode({"path": destination})
        self._request(
            "PUT", f"/containers/{quote(identifier, safe='')}/archive?{query}",
            raw_body=archive, content_type="application/x-tar", expected=(200,),
        )

    def read_container_file(self, identifier: str, path: str, *, max_bytes: int = 65536) -> bytes:
        """Read one bounded regular file without extracting a container archive."""
        if not path.startswith("/") or ".." in Path(path).parts:
            raise DockerError("invalid_container_file_path")
        limit = min(max(int(max_bytes), 1), 1024 * 1024)
        _, data = self._request(
            "GET", f"/containers/{quote(identifier, safe='')}/archive?" + urlencode({"path": path}),
            expected=(200,), timeout=5.0,
        )
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            member = archive.next()
            if member is None or not member.isfile() or member.size > limit:
                raise DockerError("container_file_not_regular_or_too_large")
            stream = archive.extractfile(member)
            if stream is None:
                raise DockerError("container_file_missing")
            content = stream.read(limit + 1)
            if len(content) > limit or archive.next() is not None:
                raise DockerError("container_file_archive_invalid")
            return content

    def wait_container(self, identifier: str, *, timeout: float = 120.0,
                       interval: float = 0.25) -> dict[str, Any]:
        deadline = time.monotonic() + min(max(float(timeout), 1.0), 1800.0)
        while time.monotonic() < deadline:
            state = self.inspect_container(identifier)
            if not state.get("State", {}).get("Running", False):
                return state
            time.sleep(min(max(float(interval), 0.05), 2.0))
        raise DockerError("docker_container_wait_timeout")

    @staticmethod
    def labels(installation_id: str, purpose: str, **extra: str) -> dict[str, str]:
        result = {
            MANAGED_LABEL: "true",
            INSTALLATION_LABEL: installation_id,
            PURPOSE_LABEL: purpose,
        }
        result.update(extra)
        return result

    @staticmethod
    def require_owned(resource: Mapping[str, Any], installation_id: str,
                      *, purpose: str | None = None) -> None:
        labels = resource.get("Labels")
        if not isinstance(labels, Mapping):
            labels = resource.get("Config", {}).get("Labels") if isinstance(resource.get("Config"), Mapping) else None
        if not isinstance(labels, Mapping) or labels.get(MANAGED_LABEL) != "true" \
                or labels.get(INSTALLATION_LABEL) != installation_id:
            raise DockerOwnershipError("docker_resource_not_owned_by_installation")
        if purpose is not None and labels.get(PURPOSE_LABEL) != purpose:
            raise DockerOwnershipError("docker_resource_purpose_mismatch")
