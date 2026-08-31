#!/usr/bin/env python3
"""Build and verify the installation-local shared worker image."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import SmacxStore
from smacx_worker_manager import WorkerManager

def main() -> int:
    source = Path(os.environ.get("SMACX_GAME_SOURCE", ""))
    if not (source / "terranx.exe").is_file():
        print(json.dumps({"event": "skip", "reason": "missing_game_source"}))
        return 0
    with tempfile.TemporaryDirectory(prefix="smacx-prepared-image-test-") as temporary:
        root = Path(temporary)
        store = SmacxStore(root / "control.sqlite3")
        control = ControlPlane(store, root / "secrets")
        manager = WorkerManager(control, DockerClient())
        registered = manager.validate_game_source(
            str(source), display_name="Prepared image test",
        )
        first = manager.ensure_prepared_worker_image(registered["game_source_id"])
        second = manager.ensure_prepared_worker_image(registered["game_source_id"])
        image = manager.docker.inspect_image(first)
        labels = image.get("Config", {}).get("Labels", {})
        if first != second or labels.get("io.smacx.purpose") != "prepared-worker-image":
            raise AssertionError("prepared worker image was not stable and installation-owned")
        print(json.dumps({
            "event": "pass", "payload": {
                "image_ref": first, "image_id": image.get("Id"),
                "source_fingerprint_bound": labels.get("io.smacx.source-sha256") ==
                    registered["metadata"]["source_tree_sha256"],
                "shared_cache_hit": first == second,
            },
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
