#!/usr/bin/env python3
"""Read-only Linux/WSL2 deployment preflight with optional Docker bind probes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess


def command(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, text=True, capture_output=True, check=False, timeout=30)


def docker_mount_probe(path: Path, expected: str) -> bool:
    result = command(
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--mount", f"type=bind,src={path},dst=/probe,readonly",
        "alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40",
        "test", "-f", f"/probe/{expected}",
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-path", type=Path)
    parser.add_argument("--directx-redist", type=Path)
    parser.add_argument("--require-wsl2", action="store_true")
    arguments = parser.parse_args()

    release = platform.release().lower()
    proc_version = Path("/proc/version").read_text(encoding="utf-8", errors="replace").lower()
    wsl2 = "microsoft" in release or "microsoft" in proc_version
    docker = command("docker", "info", "--format", "{{json .}}") \
        if shutil.which("docker") else None
    compose = command("docker", "compose", "version", "--short") \
        if docker and docker.returncode == 0 else None
    docker_info = json.loads(docker.stdout) if docker and docker.returncode == 0 else {}

    checks = {
        "linux_kernel": platform.system() == "Linux",
        "wsl2": wsl2,
        "docker_reachable": bool(docker and docker.returncode == 0),
        "docker_linux_engine": docker_info.get("OSType") == "linux",
        "docker_compose_v2": bool(compose and compose.returncode == 0),
        "tun_available": Path("/dev/net/tun").exists(),
        "x86_64": platform.machine() in {"x86_64", "amd64"},
    }
    paths: dict[str, object] = {}
    if arguments.game_path:
        game = arguments.game_path.expanduser().resolve()
        paths["game"] = {
            "path": str(game),
            "terranx": (game / "terranx.exe").is_file(),
            "docker_read_only_bind": docker_mount_probe(game, "terranx.exe")
            if checks["docker_reachable"] and game.is_dir() else False,
        }
    if arguments.directx_redist:
        redist = arguments.directx_redist.expanduser().resolve()
        paths["directx"] = {
            "path": str(redist),
            "regular_file": redist.is_file(),
            "size": redist.stat().st_size if redist.is_file() else 0,
        }

    required = ["linux_kernel", "docker_reachable", "docker_linux_engine", "docker_compose_v2", "x86_64"]
    if arguments.require_wsl2:
        required.append("wsl2")
    okay = all(checks[name] for name in required)
    if arguments.game_path:
        okay = okay and all(paths["game"][name] for name in ("terranx", "docker_read_only_bind"))
    if arguments.directx_redist:
        okay = okay and bool(paths["directx"]["regular_file"])

    print(json.dumps({
        "event": "platform_preflight", "ok": okay,
        "platform": "wsl2" if wsl2 else "linux",
        "checks": checks, "paths": paths,
        "notes": [
            "AI-only and routed-tailnet workers use Linux containers and Proton on both hosts.",
            "Test the physical Windows/WSL2 DirectPlay route from the intended client before play.",
        ],
    }, separators=(",", ":")))
    return 0 if okay else 1


if __name__ == "__main__":
    raise SystemExit(main())
