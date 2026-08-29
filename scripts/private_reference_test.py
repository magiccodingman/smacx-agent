#!/usr/bin/env python3
"""Verify private mechanics extraction and optional Docker-backed import."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import SmacxStore
from smacx_worker_manager import WorkerManager


BANNED = (
    "notes and tips",
    "general tips",
    "advanced customization",
    "dancing naked through the trees",
    "walkthrough",
    "strategy guide",
    "scenario editor",
    "cheat mode",
)


def extract(source: Path) -> list[dict]:
    environment = dict(os.environ)
    environment["SMACX_GAME_SOURCE"] = str(source)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "worker")
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "worker" / "extract_reference.py")],
        env=environment, check=True, text=True, capture_output=True,
    )
    return [json.loads(line) for line in completed.stdout.splitlines()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-source", type=Path)
    parser.add_argument("--live-docker", action="store_true")
    parser.add_argument("--worker-image", default="smacx-agent-worker:dev")
    arguments = parser.parse_args()
    if arguments.game_source is None:
        print(json.dumps({"event": "skip", "reason": "game_source_not_supplied"}))
        return 0
    source = arguments.game_source.expanduser().resolve()
    items = extract(source)
    documents = [item for item in items if item.get("type") == "document"]
    assert items[0].get("schema") == "smacx.private-reference.v1"
    assert items[0].get("policy") == "mechanics_only_no_guides"
    assert items[-1].get("documents") == len(documents)
    assert 1 <= len(documents) <= 900
    combined = "\n".join(str(item.get("body", "")) for item in documents).casefold()
    assert not any(value in combined for value in BANNED)
    approved_sources = {name.casefold() for name in (
        "Manual.pdf", "alpha.txt", "alphax.txt", "help.txt", "helpx.txt",
        "concepts.txt", "conceptsx.txt", "TECHSHORTS.txt", "GAIANS.TXT",
        "MORGAN.TXT", "PEACE.TXT", "angels.txt", "believe.txt", "caretake.txt",
        "cyborg.txt", "drone.txt", "fungboy.txt", "hive.txt", "pirates.txt",
        "spartans.txt", "univ.txt", "usurper.txt",
    )}
    assert all(str(item.get("source_name", "")).casefold() in approved_sources
               for item in documents)
    assert all("scenario" not in str(item.get("source_name", "")).casefold()
               for item in documents)
    payload: dict = {
        "event": "pass", "documents": len(documents),
        "sources": items[-1].get("sources"), "guides_excluded": True,
    }
    if arguments.live_docker:
        with tempfile.TemporaryDirectory(prefix="smacx-reference-test-") as directory:
            root = Path(directory)
            store = SmacxStore(root / "smacx.sqlite3")
            control = ControlPlane(store, root / "secrets")
            manager = WorkerManager(control, DockerClient(), worker_image=arguments.worker_image)
            registration = manager.validate_game_source(str(source), display_name="Private reference test")
            imported = registration.get("private_reference", {})
            assert imported.get("documents") == len(documents)
            assert imported.get("distributed") is False
            search = store.search_reference(
                "terraforming nutrient mineral energy", limit=5,
                private_prefix=f"private.{registration['game_source_id']}.",
            )
            assert search and any(str(item["document_id"]).startswith("private.") for item in search)
            payload["docker_import"] = imported
            payload["search_result_count"] = len(search)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
