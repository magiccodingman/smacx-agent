#!/usr/bin/env python3
"""Initialize and supervise one isolated SMACX game worker."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any


IDENTITY = re.compile(r"^[A-Za-z0-9_-]{8,96}$")
DIRECTX_SHA256 = "f6d191e89a963d7cca34f169d30f49eab99c1ed3bb92da73ec43617caaa1e93f"
children: list[subprocess.Popen[Any]] = []
stopping = False


def emit(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def require_identity(name: str) -> str:
    value = os.environ.get(name, "")
    if not IDENTITY.fullmatch(value):
        raise RuntimeError(f"invalid_or_missing_{name.lower()}")
    return value


def secret_value(name: str) -> str:
    file_name = os.environ.get(f"{name}_FILE", "")
    if file_name:
        path = Path(file_name)
        if not path.is_file():
            raise RuntimeError(f"missing_secret_file_{name.lower()}")
        return path.read_text(encoding="utf-8").strip()
    return os.environ.get(name, "")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_source(source: Path) -> dict[str, Any]:
    if not source.is_dir():
        raise RuntimeError("game_source_not_mounted")
    required = ("terranx.exe", "alphax.txt")
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise RuntimeError("invalid_game_source_missing:" + ",".join(missing))
    symlinks = [str(path.relative_to(source)) for path in source.rglob("*") if path.is_symlink()]
    if symlinks:
        raise RuntimeError("game_source_symlinks_forbidden:" + ",".join(symlinks[:10]))
    executable = source / "terranx.exe"
    header = executable.read_bytes()[:2]
    if header != b"MZ":
        raise RuntimeError("game_source_terranx_not_pe")
    return {
        "terranx_sha256": sha256(executable),
        "source_path": str(source),
    }


def import_game(source: Path, game: Path, source_identity: dict[str, Any]) -> None:
    marker = game.parent / "game-import.json"
    if game.is_dir() and marker.is_file():
        existing = json.loads(marker.read_text(encoding="utf-8"))
        if existing.get("terranx_sha256") != source_identity["terranx_sha256"]:
            raise RuntimeError("game_volume_source_changed_use_a_new_worker_volume")
        return
    if game.exists() or marker.exists():
        raise RuntimeError("incomplete_game_import_use_a_new_worker_volume")
    game.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="game-import-", dir=game.parent))
    try:
        shutil.copytree(source, temporary / "game", copy_function=shutil.copy2)
        os.replace(temporary / "game", game)
        marker.write_text(json.dumps(source_identity, indent=2) + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def overlay_bridge(game: Path) -> None:
    bridge = Path("/opt/smacx/bridge")
    for name in ("thinker.exe", "thinker.dll"):
        shutil.copy2(bridge / name, game / name)


def run_checked(command: list[str], environment: dict[str, str]) -> None:
    completed = subprocess.run(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout,
        stderr=sys.stderr,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"command_failed:{Path(command[0]).name}:{completed.returncode}")


def runtime_binary(environment: dict[str, str], name: str) -> str:
    explicit = environment.get(f"SMACX_{name.upper()}_BIN", "")
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise RuntimeError(f"missing_runtime_binary_{name}")
        return str(path)
    wine = environment.get("SMACX_WINE_BIN", "")
    if wine and name != "wine":
        sibling = Path(wine).with_name(name)
        if sibling.is_file():
            return str(sibling)
    return name


def proton_binary(environment: dict[str, str]) -> str | None:
    configured = environment.get("SMACX_PROTON_BIN", "")
    if not configured:
        return None
    path = Path(configured)
    if not path.is_file():
        raise RuntimeError("missing_runtime_binary_proton")
    return str(path)


def runtime_command(environment: dict[str, str], *arguments: str) -> list[str]:
    proton = proton_binary(environment)
    if proton:
        # A headless worker has no Steam client process. runinprefix retains
        # Proton's prepared environment without routing through steam.exe.
        return [proton, "runinprefix", *arguments]
    return [runtime_binary(environment, "wine"), *arguments]


def runtime_prefix_command(environment: dict[str, str], *arguments: str) -> list[str]:
    proton = proton_binary(environment)
    if proton:
        return [proton, "runinprefix", *arguments]
    return [runtime_binary(environment, "wine"), *arguments]


def runtime_server(environment: dict[str, str]) -> str:
    proton = proton_binary(environment)
    if proton:
        server = Path(proton).parent / "files/bin/wineserver"
        if not server.is_file():
            raise RuntimeError("missing_runtime_binary_wineserver")
        return str(server)
    return runtime_binary(environment, "wineserver")


def initialize_wine(environment: dict[str, str], worker_root: Path) -> None:
    marker = worker_root / "wine-ready.json"
    if marker.is_file():
        existing = json.loads(marker.read_text(encoding="utf-8"))
        if existing.get("winearch") != environment["WINEARCH"]:
            raise RuntimeError("wine_volume_architecture_changed_use_a_new_worker_volume")
        expected_runtime = "proton" if proton_binary(environment) else "wine"
        if existing.get("runtime") != expected_runtime:
            raise RuntimeError("wine_volume_runtime_changed_use_a_new_worker_volume")
        return
    run_checked(runtime_prefix_command(environment, "wineboot", "--init"), environment)
    run_checked([runtime_server(environment), "--wait"], environment)
    marker.write_text(json.dumps({
        "winearch": environment["WINEARCH"],
        "runtime": "proton" if proton_binary(environment) else "wine",
    }) + "\n", encoding="utf-8")


def install_directplay(environment: dict[str, str], worker_root: Path) -> bool:
    marker = worker_root / "directplay-ready.json"
    if marker.is_file():
        return True
    redist = Path(os.environ.get(
        "SMACX_DIRECTX_REDIST", "/redist/directx_feb2010_redist.exe",
    ))
    if not redist.is_file():
        if os.environ.get("SMACX_REQUIRE_DIRECTPLAY", "0") == "1":
            raise RuntimeError("directx_redist_not_mounted")
        emit("directplay_unavailable", reason="redist_not_mounted", singleplayer_available=True)
        return False
    if sha256(redist) != DIRECTX_SHA256:
        raise RuntimeError("directx_redist_checksum_mismatch")
    with tempfile.TemporaryDirectory(prefix="directplay-") as temporary_name:
        temporary = Path(temporary_name)
        run_checked(["cabextract", "-q", "-d", str(temporary), "-L", "-F", "dxnt.cab", str(redist)], environment)
        prefix = Path(environment["WINEPREFIX"])
        system_name = "syswow64" if environment["WINEARCH"] == "win64" else "system32"
        system = prefix / "drive_c/windows" / system_name
        system.mkdir(parents=True, exist_ok=True)
        names = (
            "dplaysvr.exe", "dplayx.dll", "dpmodemx.dll", "dpnet.dll",
            "dpnhpast.dll", "dpnhupnp.dll", "dpnsvr.exe", "dpwsockx.dll",
        )
        for name in names:
            target = system / name
            if target.exists() or target.is_symlink():
                target.unlink()
            run_checked([
                "cabextract", "-q", "-d", str(system), "-L", "-F", name,
                str(temporary / "dxnt.cab"),
            ], environment)
    for name in ("dplaysvr.exe", "dplayx", "dpmodemx", "dpnet", "dpnhpast", "dpnhupnp", "dpnsvr.exe", "dpwsockx"):
        run_checked([
            *runtime_prefix_command(environment, "reg"), "add", r"HKCU\Software\Wine\DllOverrides",
            "/v", name, "/d", "native", "/f",
        ], environment)
    for name in ("dplayx.dll", "dpnet.dll", "dpnhpast.dll", "dpnhupnp.dll"):
        windows_directory = "syswow64" if environment["WINEARCH"] == "win64" else "system32"
        run_checked(runtime_prefix_command(
            environment, rf"C:\windows\{windows_directory}\regsvr32.exe",
            "/s", rf"C:\windows\{windows_directory}\{name}",
        ), environment)
    run_checked([runtime_server(environment), "--wait"], environment)
    marker.write_text(json.dumps({"sha256": DIRECTX_SHA256}) + "\n", encoding="utf-8")
    return True


def start(command: list[str], environment: dict[str, str], **kwargs: Any) -> subprocess.Popen[Any]:
    process = subprocess.Popen(command, env=environment, **kwargs)
    children.append(process)
    return process


def wait_for_x(display: str, environment: dict[str, str]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        checked = subprocess.run(
            ["xdpyinfo", "-display", display], env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if checked.returncode == 0:
            return
        time.sleep(0.25)
    raise RuntimeError("xvfb_start_timeout")


def bridge_ready(port: int, token: str) -> bool:
    request = json.dumps({"op": "ping", "token": token}, separators=(",", ":")).encode() + b"\n"
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.sendall(request)
            response = connection.recv(4096)
        return bool(json.loads(response).get("ok"))
    except (OSError, json.JSONDecodeError):
        return False


def stop_all(*_: Any) -> None:
    global stopping
    if stopping:
        return
    stopping = True
    for process in reversed(children):
        if process.poll() is None:
            process.terminate()


def main() -> int:
    match_id = require_identity("SMACX_AGENT_MATCH_ID")
    session_id = require_identity("SMACX_AGENT_SESSION_ID")
    agent_id = require_identity("SMACX_AGENT_ID")
    perspective_id = require_identity("SMACX_PERSPECTIVE_ID")
    instance_id = require_identity("SMACX_INSTANCE_ID")
    token = secret_value("SMACX_AGENT_TOKEN")
    if len(token) < 16:
        raise RuntimeError("invalid_or_missing_smacx_agent_token")
    worker_root = Path(os.environ.get("SMACX_WORKER_ROOT", "/var/lib/smacx"))
    worker_home = worker_root / "home"
    worker_home.mkdir(parents=True, exist_ok=True)
    source = Path(os.environ.get("SMACX_GAME_SOURCE", "/game-source"))
    game = worker_root / "game"
    source_identity = validate_source(source)
    import_game(source, game, source_identity)
    overlay_bridge(game)

    environment = os.environ.copy()
    winearch = os.environ.get("SMACX_WINEARCH", "win64")
    if winearch not in ("win32", "win64"):
        raise RuntimeError("invalid_smacx_winearch")
    environment.update({
        "DISPLAY": os.environ.get("DISPLAY", ":99"),
        "HOME": str(worker_home),
        "WINEARCH": winearch,
        "WINEPREFIX": str(worker_root / "prefix"),
        "WINEDLLOVERRIDES": "mscoree,mshtml=",
        "SMACX_AGENT_ENABLE": "1",
        "SMACX_AGENT_PORT": "47813",
        "SMACX_AGENT_TOKEN": token,
    })
    if proton_binary(environment):
        compatdata = worker_root / "compatdata"
        compatdata.mkdir(parents=True, exist_ok=True)
        (worker_root / "steam-client").mkdir(parents=True, exist_ok=True)
        environment.update({
            "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(worker_root / "steam-client"),
            "STEAM_COMPAT_DATA_PATH": str(compatdata),
            "STEAM_COMPAT_APP_ID": os.environ.get("SMACX_STEAM_APP_ID", "2204130"),
            "SteamAppId": os.environ.get("SMACX_STEAM_APP_ID", "2204130"),
            "SteamGameId": os.environ.get("SMACX_STEAM_APP_ID", "2204130"),
            "WINEPREFIX": str(compatdata / "pfx"),
        })
    display = environment["DISPLAY"]
    width = min(max(int(os.environ.get("SMACX_VIEW_WIDTH", "1280")), 800), 2560)
    height = min(max(int(os.environ.get("SMACX_VIEW_HEIGHT", "800")), 600), 1600)
    depth = 24
    x_socket_root = Path("/tmp/.X11-unix")
    x_socket_root.mkdir(mode=0o1777, exist_ok=True)
    x_socket_root.chmod(0o1777)
    start(["Xvfb", display, "-screen", "0", f"{width}x{height}x{depth}", "-nolisten", "tcp"], environment)
    wait_for_x(display, environment)
    # Use a deterministic, image-owned configuration.  Debian's default style
    # asks fbsetbg for a desktop wallpaper and opens an xmessage when no
    # wallpaper helper is installed.  That window is irrelevant to the game
    # but would pollute an optional spectator display.
    start([
        "fluxbox", "-rc", "/opt/smacx/fluxbox-init", "-no-toolbar",
    ], environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    view_enabled = os.environ.get("SMACX_VIEW_ENABLE", "0") == "1"
    if view_enabled:
        password = secret_value("SMACX_VIEW_PASSWORD")
        if len(password) < 12:
            raise RuntimeError("view_password_must_be_at_least_12_characters")
        password_file = worker_root / "view-password"
        run_checked(["x11vnc", "-storepasswd", password, str(password_file)], environment)
        vnc_command = [
            "x11vnc", "-display", display, "-rfbauth", str(password_file),
            "-rfbport", "5900", "-forever", "-shared", "-noxdamage",
        ]
        if os.environ.get("SMACX_VIEW_MODE", "view-only") != "interactive":
            vnc_command.append("-viewonly")
        start(vnc_command, environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        start([
            "websockify", "--web", "/usr/share/novnc/",
            os.environ.get("SMACX_VIEW_PORT", "6080"), "127.0.0.1:5900",
        ], environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    initialize_wine(environment, worker_root)
    directplay = install_directplay(environment, worker_root)
    proxy_port = int(os.environ.get("SMACX_BRIDGE_PROXY_PORT", "47814"))
    start([
        "socat", f"TCP-LISTEN:{proxy_port},bind=0.0.0.0,reuseaddr,fork",
        "TCP:127.0.0.1:47813",
    ], environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    metadata = {
        "schema": "smacx.worker.v1",
        "match_id": match_id,
        "session_id": session_id,
        "agent_id": agent_id,
        "perspective_id": perspective_id,
        "instance_id": instance_id,
        "source": source_identity,
        "directplay_ready": directplay,
        "display": display,
        "view_enabled": view_enabled,
        "view_mode": os.environ.get("SMACX_VIEW_MODE", "view-only"),
        "bridge_proxy_port": proxy_port,
    }
    (worker_root / "worker.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    game_process = start(
        runtime_command(environment, str(game / "thinker.exe"), "-windowed"),
        environment,
        cwd=game,
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    deadline = time.monotonic() + min(max(int(os.environ.get("SMACX_BRIDGE_START_TIMEOUT", "90")), 10), 300)
    while time.monotonic() < deadline:
        if bridge_ready(proxy_port, token):
            emit("worker_ready", **metadata)
            # Thinker is a native launcher and may exit successfully after it
            # hands terranx.exe to Wine.  Supervise the prefix, not that shim.
            wine_waiter = start([runtime_server(environment), "--wait"], environment)
            return_code = wine_waiter.wait()
            if stopping:
                emit("worker_stopped", reason="signal")
                return 0
            emit("game_exited", return_code=return_code)
            return return_code
        launcher_return_code = game_process.poll()
        if launcher_return_code not in (None, 0):
            raise RuntimeError(f"game_launcher_failed:{launcher_return_code}")
        time.sleep(0.5)
    raise RuntimeError("bridge_start_timeout")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop_all)
    signal.signal(signal.SIGINT, stop_all)
    try:
        raise SystemExit(main())
    except Exception as exc:
        emit("worker_failed", error=f"{type(exc).__name__}: {exc}")
        stop_all()
        raise
