#!/usr/bin/env python3
"""Validate a legal game source and import one managed Proton runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from smacx_control_server import build_control
from smacx_docker import DockerClient
from smacx_worker_manager import WorkerManager


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--game-source", required=True)
    parser.add_argument("--proton-source", required=True)
    parser.add_argument("--directx-redist")
    parser.add_argument("--worker-image", default="smacx-agent-worker:dev")
    arguments = parser.parse_args()
    control = build_control(arguments.data_root)
    manager = WorkerManager(
        control, DockerClient(), worker_image=arguments.worker_image,
        directx_redist_host_path=arguments.directx_redist,
    )
    game_source = next((
        item for item in control.list_game_sources()
        if item["host_path"] == str(Path(arguments.game_source).resolve())
    ), None)
    if game_source is None:
        game_source = manager.validate_game_source(arguments.game_source)
    runtime = next((
        item for item in control.list_runtimes()
        if item.get("source_path") == str(Path(arguments.proton_source).resolve())
        and item.get("status") == "ready"
    ), None)
    if runtime is None:
        runtime = manager.import_proton(arguments.proton_source)
    print(json.dumps({"ok": True, "game_source": game_source, "runtime": runtime}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
