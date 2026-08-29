#!/usr/bin/env python3
"""Contained contract tests for the Linux game-worker bootstrap."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entrypoint = load("smacx_worker_entrypoint", ROOT / "worker" / "entrypoint.py")
healthcheck = load("smacx_worker_healthcheck", ROOT / "worker" / "healthcheck.py")


def legal_source(root: Path, executable: bytes = b"MZtest executable") -> Path:
    source = root / "source"
    source.mkdir(parents=True)
    (source / "terranx.exe").write_bytes(executable)
    (source / "alphax.txt").write_text("test\n", encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested" / "asset.txt").write_text("asset\n", encoding="utf-8")
    return source


def test_source_import_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="smacx-worker-test-") as temp_name:
        root = Path(temp_name)
        source = legal_source(root)
        identity = entrypoint.validate_source(source)
        assert len(identity["terranx_sha256"]) == 64
        game = root / "state" / "game"
        entrypoint.import_game(source, game, identity)
        assert (game / "nested" / "asset.txt").read_text(encoding="utf-8") == "asset\n"
        entrypoint.import_game(source, game, identity)

        changed = legal_source(root / "changed", b"MZchanged executable")
        try:
            entrypoint.import_game(changed, game, entrypoint.validate_source(changed))
        except RuntimeError as exc:
            assert str(exc) == "game_volume_source_changed_use_a_new_worker_volume"
        else:
            raise AssertionError("changed source was silently accepted")


def test_source_rejections() -> None:
    with tempfile.TemporaryDirectory(prefix="smacx-worker-test-") as temp_name:
        root = Path(temp_name)
        bad = legal_source(root, b"not a PE")
        try:
            entrypoint.validate_source(bad)
        except RuntimeError as exc:
            assert str(exc) == "game_source_terranx_not_pe"
        else:
            raise AssertionError("non-PE executable was accepted")

    with tempfile.TemporaryDirectory(prefix="smacx-worker-test-") as temp_name:
        root = Path(temp_name)
        source = legal_source(root)
        (source / "escape").symlink_to("/etc/passwd")
        try:
            entrypoint.validate_source(source)
        except RuntimeError as exc:
            assert str(exc).startswith("game_source_symlinks_forbidden:")
        else:
            raise AssertionError("source symlink was accepted")


def test_identity_contract() -> None:
    old = os.environ.get("SMACX_AGENT_ID")
    try:
        os.environ["SMACX_AGENT_ID"] = "agent_123456"
        assert entrypoint.require_identity("SMACX_AGENT_ID") == "agent_123456"
        os.environ["SMACX_AGENT_ID"] = "bad/id"
        try:
            entrypoint.require_identity("SMACX_AGENT_ID")
        except RuntimeError as exc:
            assert str(exc) == "invalid_or_missing_smacx_agent_id"
        else:
            raise AssertionError("invalid identity was accepted")
    finally:
        if old is None:
            os.environ.pop("SMACX_AGENT_ID", None)
        else:
            os.environ["SMACX_AGENT_ID"] = old


def test_file_secret_precedence() -> None:
    with tempfile.TemporaryDirectory(prefix="smacx-worker-test-") as temp_name:
        secret = Path(temp_name) / "secret"
        secret.write_text("file-secret-value\n", encoding="utf-8")
        old_value = os.environ.get("SMACX_AGENT_TOKEN")
        old_file = os.environ.get("SMACX_AGENT_TOKEN_FILE")
        try:
            os.environ["SMACX_AGENT_TOKEN"] = "environment-secret"
            os.environ["SMACX_AGENT_TOKEN_FILE"] = str(secret)
            assert entrypoint.secret_value("SMACX_AGENT_TOKEN") == "file-secret-value"
            assert healthcheck.secret_value("SMACX_AGENT_TOKEN") == "file-secret-value"
        finally:
            if old_value is None:
                os.environ.pop("SMACX_AGENT_TOKEN", None)
            else:
                os.environ["SMACX_AGENT_TOKEN"] = old_value
            if old_file is None:
                os.environ.pop("SMACX_AGENT_TOKEN_FILE", None)
            else:
                os.environ["SMACX_AGENT_TOKEN_FILE"] = old_file


def test_runtime_binary_selection() -> None:
    with tempfile.TemporaryDirectory(prefix="smacx-worker-test-") as temp_name:
        runtime = Path(temp_name)
        for name in ("wine", "wineboot", "wineserver"):
            (runtime / name).write_text("test\n", encoding="utf-8")
        environment = {"SMACX_WINE_BIN": str(runtime / "wine")}
        assert entrypoint.runtime_binary(environment, "wine") == str(runtime / "wine")
        assert entrypoint.runtime_binary(environment, "wineserver") == str(runtime / "wineserver")

        proton_root = runtime / "proton-runtime"
        (proton_root / "files" / "bin").mkdir(parents=True)
        (proton_root / "proton").write_text("test\n", encoding="utf-8")
        (proton_root / "files" / "bin" / "wineserver").write_text("test\n", encoding="utf-8")
        proton_environment = {"SMACX_PROTON_BIN": str(proton_root / "proton")}
        assert entrypoint.runtime_command(proton_environment, "reg", "query") == [
            str(proton_root / "proton"), "runinprefix", "reg", "query",
        ]
        assert entrypoint.runtime_prefix_command(proton_environment, "reg", "query") == [
            str(proton_root / "proton"), "runinprefix", "reg", "query",
        ]
        assert entrypoint.runtime_server(proton_environment) == str(
            proton_root / "files" / "bin" / "wineserver"
        )


def test_prefix_architecture_is_immutable() -> None:
    with tempfile.TemporaryDirectory(prefix="smacx-worker-test-") as temp_name:
        root = Path(temp_name)
        (root / "wine-ready.json").write_text('{"winearch":"win64"}\n', encoding="utf-8")
        try:
            entrypoint.initialize_wine({"WINEARCH": "win32"}, root)
        except RuntimeError as exc:
            assert str(exc) == "wine_volume_architecture_changed_use_a_new_worker_volume"
        else:
            raise AssertionError("worker accepted an in-place prefix architecture change")


def test_authenticated_healthcheck() -> None:
    token = "worker-contract-token"
    ready = threading.Event()
    port_holder: list[int] = []

    def serve() -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port_holder.append(listener.getsockname()[1])
            ready.set()
            connection, _ = listener.accept()
            with connection:
                request = json.loads(connection.recv(4096))
                response = {"ok": request.get("token") == token and request.get("op") == "ping"}
                connection.sendall(json.dumps(response).encode() + b"\n")

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    assert ready.wait(2)
    old_token = os.environ.get("SMACX_AGENT_TOKEN")
    old_port = os.environ.get("SMACX_BRIDGE_PROXY_PORT")
    try:
        os.environ["SMACX_AGENT_TOKEN"] = token
        os.environ["SMACX_BRIDGE_PROXY_PORT"] = str(port_holder[0])
        assert healthcheck.main() == 0
    finally:
        if old_token is None:
            os.environ.pop("SMACX_AGENT_TOKEN", None)
        else:
            os.environ["SMACX_AGENT_TOKEN"] = old_token
        if old_port is None:
            os.environ.pop("SMACX_BRIDGE_PROXY_PORT", None)
        else:
            os.environ["SMACX_BRIDGE_PROXY_PORT"] = old_port
    server.join(2)


def test_distribution_contract() -> None:
    dockerfile = (ROOT / "worker" / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "USER smacx" in dockerfile
    assert "COPY runtime" not in dockerfile
    assert "COPY toolchain" not in dockerfile
    assert "alsa-null.conf" in dockerfile
    assert "inspect_source.py" in dockerfile
    assert "fluxbox-init" in dockerfile
    assert "fluxbox-overlay" in dockerfile
    entrypoint_source = (ROOT / "worker" / "entrypoint.py").read_text(encoding="utf-8")
    manager_source = (ROOT / "src" / "smacx_worker_manager.py").read_text(encoding="utf-8")
    assert '"fluxbox", "-rc", "/opt/smacx/fluxbox-init", "-no-toolbar"' in entrypoint_source
    assert 'if stopping:\n                emit("worker_stopped", reason="signal")' in entrypoint_source
    assert '"Target": "/proton",\n             "ReadOnly": True' in manager_source
    assert "SMACX_PROTON_DIST_LOCK" in manager_source
    assert "runtime" in dockerignore
    assert "toolchain" in dockerignore


if __name__ == "__main__":
    test_source_import_contract()
    test_source_rejections()
    test_identity_contract()
    test_file_secret_precedence()
    test_runtime_binary_selection()
    test_prefix_architecture_is_immutable()
    test_authenticated_healthcheck()
    test_distribution_contract()
    print("worker contract test passed")
