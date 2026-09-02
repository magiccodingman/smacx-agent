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
    shutil.copy2("/opt/smacx/thinker.ini", game / "thinker.ini")
    shutil.copy2("/opt/smacx/modmenu.txt", game / "modmenu.txt")


def configured_view_dimensions() -> tuple[int, int]:
    width = min(max(int(os.environ.get("SMACX_VIEW_WIDTH", "1280")), 800), 5120)
    height = min(max(int(os.environ.get("SMACX_VIEW_HEIGHT", "800")), 600), 2160)
    if width * height > 3840 * 2160:
        raise RuntimeError("view_resolution_exceeds_validated_pixel_envelope")
    return width, height


def configure_worker_game(game: Path, width: int, height: int) -> None:
    """Set worker-local presentation defaults without editing the legal source."""
    path = game / "Alpha Centauri.Ini"
    lines = path.read_text(encoding="latin-1").splitlines()
    wanted = {
        "movieplayerpath": "MoviePlayerPath=",
        "movieplayerargs": "MoviePlayerArgs=",
        "disableopeningmovie": "DisableOpeningMovie=1",
    }
    output: list[str] = []
    in_section = False
    seen: set[str] = set()
    inserted = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not inserted:
                output.extend(value for key, value in wanted.items() if key not in seen)
                inserted = True
            in_section = stripped.casefold() == "[alpha centauri]"
            output.append(line)
            continue
        if in_section and "=" in line:
            key = line.split("=", 1)[0].strip().casefold()
            if key in wanted:
                output.append(wanted[key])
                seen.add(key)
                continue
        output.append(line)
    if in_section and not inserted:
        output.extend(value for key, value in wanted.items() if key not in seen)
    path.write_text("\n".join(output) + "\n", encoding="latin-1")

    # Thinker owns the native DirectDraw surface. Xvfb alone only changes the
    # outer framebuffer and otherwise leaves the game at its 1024x768 default.
    # A custom fullscreen surface avoids window-decoration clipping at the
    # 800x600 minimum and gives every managed profile a true native canvas.
    thinker_path = game / "thinker.ini"
    thinker_lines = thinker_path.read_text(encoding="utf-8").splitlines()
    replacements = {
        "video_mode": "video_mode=1",
        "window_width": f"window_width={width}",
        "window_height": f"window_height={height}",
    }
    thinker_output: list[str] = []
    in_thinker = False
    thinker_seen: set[str] = set()
    thinker_inserted = False
    for line in thinker_lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_thinker and not thinker_inserted:
                thinker_output.extend(
                    value for key, value in replacements.items() if key not in thinker_seen
                )
                thinker_inserted = True
            in_thinker = stripped.casefold() == "[thinker]"
            thinker_output.append(line)
            continue
        if in_thinker and "=" in line:
            key = line.split("=", 1)[0].strip().casefold()
            if key in replacements:
                thinker_output.append(replacements[key])
                thinker_seen.add(key)
                continue
        thinker_output.append(line)
    if in_thinker and not thinker_inserted:
        thinker_output.extend(
            value for key, value in replacements.items() if key not in thinker_seen
        )
    thinker_path.write_text("\n".join(thinker_output) + "\n", encoding="utf-8")


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
        "SMACX_DIRECTX_REDIST", "/opt/smacx/redist/directx_feb2010_redist.exe",
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


def selkies_command(
    *, port: str, password: str, view_only_password: str, subfolder: str,
    width: int, height: int, encoder: str, audio_enabled: bool,
) -> list[str]:
    """Build one authenticated stream endpoint for the shared game display."""
    return [
        "selkies", "--addr=0.0.0.0", f"--port={port}",
        "--enable-https=false", "--enable-basic-auth=true",
        "--basic-auth-user=smacx", f"--basic-auth-password={password}",
        f"--basic-auth-viewonly-password={view_only_password}",
        f"--subfolder={subfolder}", "--mode=websocket",
        f"--encoder={encoder}", "--framerate=30",
        f"--video-bitrate={os.environ.get('SMACX_STREAM_VIDEO_BITRATE', '3500')}",
        f"--audio-enabled={'true' if audio_enabled else 'false'}", "--enable-resize=false",
        "--microphone-enabled=false", "--webcam-enabled=false",
        "--gamepad-enabled=false", "--enable-clipboard=false",
        "--file-transfers=none", "--command-enabled=false",
        "--enable-sharing=false", "--enable-collab=false",
        "--ui-title=SMACX Agent", "--ui-show-logo=false",
        "--ui-show-core-buttons=false", "--ui-show-sidebar=false",
        "--is-manual-resolution-mode=true",
        f"--manual-width={width}", f"--manual-height={height}",
    ]


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


def windows_process_ids(image_name: str, proc_root: Path = Path("/proc")) -> set[int]:
    """Return Wine process ids whose argv/comm names one exact PE image."""
    expected = image_name.lower()
    process_ids: set[int] = set()
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().split(b"\0")
            arguments = [value.decode("utf-8", "replace") for value in command if value]
            comm = (entry / "comm").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        names = [comm]
        names.extend(
            argument.replace("\\", "/").rsplit("/", 1)[-1]
            for argument in arguments
        )
        if any(name.lower() == expected for name in names):
            process_ids.add(int(entry.name))
    return process_ids


def terminate_runtime(environment: dict[str, str]) -> None:
    """Stop this worker's isolated Wine/Proton prefix after the game exits."""
    subprocess.run(
        [runtime_server(environment), "-k"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=10,
    )


def stop_all(*_: Any) -> None:
    global stopping
    if stopping:
        return
    stopping = True
    for process in reversed(children):
        if process.poll() is None:
            process.terminate()


def prepare_immutable_base() -> int:
    """Prepare one installation-local Docker image layer.

    The manager commits this stopped container and never pushes the resulting
    image. Every seat then shares these bytes through Docker's ordinary image
    layer store, while its running container receives an isolated copy-on-write
    view and its native saves live in the small durable worker volume.
    """
    source = Path(os.environ.get("SMACX_GAME_SOURCE", "/game-source"))
    source_identity = validate_source(source)
    prepared = Path("/opt/smacx/prepared")
    game = prepared / "game"
    if prepared.exists():
        for child in prepared.iterdir():
            if child.name == ".keep":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
    prepared.mkdir(parents=True, exist_ok=True)
    import_game(source, game, source_identity)
    overlay_bridge(game)
    configure_worker_game(game, 1280, 800)

    # Personal saves from the operator's source tree are never baked into the
    # shared layer. Workers see this stable absolute link backed by their own
    # small managed state volume.
    saves = game / "saves"
    if saves.is_dir() and not saves.is_symlink():
        shutil.rmtree(saves)
    else:
        saves.unlink(missing_ok=True)
    saves.symlink_to("/var/lib/smacx/game/saves", target_is_directory=True)

    home = prepared / "home"
    compatdata = prepared / "compatdata"
    home.mkdir(parents=True, exist_ok=True)
    compatdata.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update({
        "HOME": str(home),
        "WINEARCH": "win64",
        "WINEPREFIX": str(compatdata / "pfx"),
        "WINEDLLOVERRIDES": "mscoree,mshtml=",
        "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(prepared / "steam-client"),
        "STEAM_COMPAT_DATA_PATH": str(compatdata),
        "STEAM_COMPAT_APP_ID": os.environ.get("SMACX_STEAM_APP_ID", "2204130"),
        "SteamAppId": os.environ.get("SMACX_STEAM_APP_ID", "2204130"),
        "SteamGameId": os.environ.get("SMACX_STEAM_APP_ID", "2204130"),
        "SMACX_PROTON_BIN": os.environ.get("SMACX_PROTON_BIN", "/opt/proton/proton"),
        "SMACX_PROTON_DIST_LOCK": "/tmp/smacx-proton-dist.lock",
        "SMACX_REQUIRE_DIRECTPLAY": "1",
    })
    (prepared / "steam-client").mkdir(parents=True, exist_ok=True)
    initialize_wine(environment, prepared)
    directplay = install_directplay(environment, prepared)
    manifest = {
        "schema": "smacx.prepared-worker.v1",
        "source": source_identity,
        "runtime": "proton",
        "directplay_ready": directplay,
        "created_unix": time.time(),
    }
    (prepared / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )
    emit("prepared_base_ready", source=source_identity, directplay_ready=directplay)
    return 0


def hydrate_compressed_saves(worker_root: Path) -> int:
    restored = 0
    saves = worker_root / "game" / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    for archived in saves.rglob("*.sav.zst"):
        target = archived.with_suffix("")
        completed = subprocess.run(
            ["zstd", "-q", "-f", "-d", str(archived), "-o", str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"save_archive_restore_failed:{archived.name}")
        archived.unlink()
        restored += 1
    return restored


def main() -> int:
    if os.environ.get("SMACX_PREPARE_BASE") == "1":
        return prepare_immutable_base()
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
    prepared = Path("/opt/smacx/prepared")
    prepared_manifest = prepared / "manifest.json"
    if prepared_manifest.is_file():
        manifest = json.loads(prepared_manifest.read_text(encoding="utf-8"))
        source_identity = manifest["source"]
        game = prepared / "game"
        runtime_state_root = prepared
        restored_saves = hydrate_compressed_saves(worker_root)
        emit("worker_state_hydrated", restored_saves=restored_saves)
    else:
        game = worker_root / "game"
        source_identity = validate_source(source)
        import_game(source, game, source_identity)
        runtime_state_root = worker_root
    overlay_bridge(game)
    width, height = configured_view_dimensions()
    configure_worker_game(game, width, height)

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
        compatdata = runtime_state_root / "compatdata"
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
    stream_backend = "disabled"
    if view_enabled:
        password = secret_value("SMACX_VIEW_PASSWORD")
        view_only_password = secret_value("SMACX_VIEW_ONLY_PASSWORD")
        if len(password) < 12:
            raise RuntimeError("view_password_must_be_at_least_12_characters")
        if len(view_only_password) < 12:
            raise RuntimeError("view_only_password_must_be_at_least_12_characters")
        stream_backend = os.environ.get("SMACX_STREAM_BACKEND", "selkies")
        stream_started = False
        if stream_backend == "selkies" and shutil.which("selkies"):
            pulse_runtime = worker_root / "pulse"
            pulse_runtime.mkdir(parents=True, exist_ok=True)
            pulse_socket = pulse_runtime / "native"
            environment.update({
                "PULSE_RUNTIME_PATH": str(pulse_runtime),
                "PULSE_SERVER": f"unix:{pulse_socket}",
            })
            pulse = start([
                "pulseaudio", "--daemonize=no", "--exit-idle-time=-1",
                "--log-target=stderr",
                f"--load=module-native-protocol-unix socket={pulse_socket} auth-anonymous=1",
                "--load=module-null-sink sink_name=smacx",
            ], environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            pulse_deadline = time.monotonic() + 10
            while time.monotonic() < pulse_deadline and not pulse_socket.exists():
                if pulse.poll() is not None:
                    break
                time.sleep(0.1)
            subfolder = os.environ.get("SMACX_STREAM_SUBFOLDER", "")
            stream = start(selkies_command(
                port=os.environ.get("SMACX_VIEW_PORT", "6080"),
                password=password, view_only_password=view_only_password,
                subfolder=subfolder, width=width, height=height,
                encoder="h264enc", audio_enabled=True,
            ), environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1)
            stream_started = stream.poll() is None
            if not stream_started:
                emit("selkies_unavailable", fallback="novnc")
            else:
                # Browsers do not expose WebCodecs on a non-loopback HTTP
                # origin.  A second server watches the same X display using
                # JPEG/WebSocket so LAN clients can still play or spectate.
                compatibility_stream = start(selkies_command(
                    port=os.environ.get("SMACX_COMPAT_VIEW_PORT", "6081"),
                    password=password, view_only_password=view_only_password,
                    subfolder=subfolder, width=width, height=height,
                    encoder="jpeg", audio_enabled=False,
                ), environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1)
                if compatibility_stream.poll() is not None:
                    emit("selkies_compatibility_unavailable")
        if not stream_started:
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
            stream_backend = "novnc"

    initialize_wine(environment, runtime_state_root)
    directplay = install_directplay(environment, runtime_state_root)
    proxy_port = int(os.environ.get("SMACX_BRIDGE_PROXY_PORT", "47814"))
    ready_marker = Path(os.environ.get(
        "SMACX_READY_MARKER", "/tmp/smacx/bridge-ready",
    ))
    ready_marker.parent.mkdir(parents=True, exist_ok=True)
    ready_marker.unlink(missing_ok=True)

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
        "stream_backend": stream_backend,
        "bridge_proxy_port": proxy_port,
    }
    (worker_root / "worker.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    game_command = runtime_command(environment, str(game / "thinker.exe"), "-windowed")
    startup_save = os.environ.get("SMACX_AGENT_STARTUP_SAVE")
    startup_scenario = os.environ.get("SMACX_AGENT_STARTUP_SCENARIO")
    if startup_save is not None and startup_scenario is not None:
        raise RuntimeError("conflicting_startup_modes")
    if startup_save is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", startup_save):
            raise RuntimeError("invalid_startup_save")
        game_command.append(f"saves\\agent\\{match_id}\\{startup_save}.sav")
    if startup_scenario is not None:
        parts = startup_scenario.split("/")
        if not parts or len(startup_scenario) > 512 \
                or not startup_scenario.upper().endswith(".SC") \
                or any(part in ("", ".", "..") or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9 _.'()-]{0,95}", part,
                ) for part in parts):
            raise RuntimeError("invalid_startup_scenario")
        scenario_path = game / "scenarios"
        for part in parts:
            scenario_path /= part
        resolved_scenario = scenario_path.resolve()
        if not resolved_scenario.is_file() or game.resolve() not in resolved_scenario.parents:
            raise RuntimeError("startup_scenario_unavailable")
        game_command.append("scenarios\\" + "\\".join(parts))
    game_process = start(
        game_command,
        environment,
        cwd=game,
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    deadline = time.monotonic() + min(max(int(os.environ.get("SMACX_BRIDGE_START_TIMEOUT", "90")), 10), 300)
    while time.monotonic() < deadline:
        if bridge_ready(47813, token):
            start([
                "socat", f"TCP-LISTEN:{proxy_port},bind=0.0.0.0,reuseaddr,fork",
                "TCP:127.0.0.1:47813",
            ], environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ready_marker.touch(mode=0o600)
            emit("worker_ready", **metadata)
            # Thinker is a native launcher and may exit successfully after it
            # hands terranx.exe to Wine. DirectPlay's dplaysvr.exe can outlive
            # the game indefinitely, so wineserver --wait alone is not an
            # honest game-liveness signal. Supervise the exact PE image while
            # retaining the prefix waiter as a secondary runtime signal.
            wine_waiter = start([runtime_server(environment), "--wait"], environment)
            game_seen = bool(windows_process_ids("terranx.exe"))
            absent_since: float | None = None
            exit_grace = min(max(float(os.environ.get(
                "SMACX_GAME_EXIT_GRACE", "5",
            )), 1), 30)
            while True:
                if stopping:
                    ready_marker.unlink(missing_ok=True)
                    emit("worker_stopped", reason="signal")
                    return 0
                process_ids = windows_process_ids("terranx.exe")
                if process_ids:
                    game_seen = True
                    absent_since = None
                elif game_seen:
                    if absent_since is None:
                        absent_since = time.monotonic()
                    elif time.monotonic() - absent_since >= exit_grace:
                        ready_marker.unlink(missing_ok=True)
                        launcher_return_code = game_process.poll()
                        emit(
                            "game_process_exited",
                            image="terranx.exe",
                            launcher_return_code=launcher_return_code,
                            grace_seconds=exit_grace,
                        )
                        terminate_runtime(environment)
                        return 70
                runtime_return_code = wine_waiter.poll()
                if runtime_return_code is not None:
                    ready_marker.unlink(missing_ok=True)
                    if stopping:
                        emit("worker_stopped", reason="signal")
                        return 0
                    emit(
                        "runtime_exited",
                        return_code=runtime_return_code,
                        game_process_seen=game_seen,
                    )
                    return runtime_return_code or 71
                time.sleep(1)
        launcher_return_code = game_process.poll()
        if launcher_return_code not in (None, 0):
            raise RuntimeError(f"game_launcher_failed:{launcher_return_code}")
        time.sleep(1)
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
