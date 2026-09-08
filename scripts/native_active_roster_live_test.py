#!/usr/bin/env python3
"""Isolated native proof that open managed roster slots do not spawn factions."""
import json
import os
from pathlib import Path
import tempfile
import time

import semantic_playthrough as play
from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import MemoryScope, SmacxStore
from smacx_worker_manager import WorkerManager


ACTIVE_MASK = 0x3E  # native faction slots 1..5; slots 6 and 7 are inactive


def main():
    assert os.environ.get("SMACX_AGENT_TEST_MODE") == "1"
    assert os.environ.get("SMACX_ACCEPTANCE_ACTIVE_ROSTER") == "1"
    docker = DockerClient()
    with tempfile.TemporaryDirectory(prefix="smacx-active-roster-") as tmp:
        control = ControlPlane(SmacxStore(Path(tmp) / "state.sqlite3"), Path(tmp) / "secrets")
        manager = WorkerManager(
            control, docker, worker_image=os.environ["SMACX_TEST_WORKER_IMAGE"],
        )
        worker = None
        try:
            source = manager.validate_game_source(
                os.environ["SMACX_TEST_GAME_SOURCE"], display_name="Active roster native proof",
            )
            runtime = manager.ensure_bundled_runtime()
            control.store.ensure_agent("agent-active-roster", "Active roster proof")
            match = control.create_solo_match(
                "Active roster native proof", "agent-active-roster", faction_id=1,
            )
            scope = MemoryScope(
                match["match"]["match_id"], "agent-active-roster",
                match["perspective"]["perspective_id"],
            )
            worker = manager.provision_worker(
                scope, source["game_source_id"], runtime["runtime_id"],
                autostart={
                    "enabled": True, "difficulty": 2, "world_size": 0,
                    "faction_id": 1, "faction_roster": list(range(7)),
                    "active_faction_mask": ACTIVE_MASK,
                },
                view_enabled=False,
            )
            manager.start_worker(worker["instance_id"], timeout=300)

            def call(op, **args):
                timeout = args.pop("timeout", 20)
                return manager._native_request(worker["instance_id"], op, timeout=timeout, **args)

            play.bridge_request = call
            for _ in range(180):
                snapshot = call("semantic_snapshot").get("snapshot", {})
                if not snapshot:
                    time.sleep(0.25)
                    continue
                if snapshot["interaction"]["kind"] != "turn":
                    play.handle_interaction(snapshot)
                    time.sleep(0.25)
                    continue
                status = call("test_active_faction_roster")
                assert status.get("ok"), status
                assert status["configured_active_faction_mask"] == ACTIVE_MASK, status
                assert status["living_faction_mask"] == ACTIVE_MASK, status
                assert status["living_faction_count"] == 5, status
                print(json.dumps({
                    "passed": True,
                    "configured_active_faction_mask": ACTIVE_MASK,
                    "living_faction_mask": status["living_faction_mask"],
                    "living_faction_count": status["living_faction_count"],
                    "inactive_slots": [6, 7],
                    "pixels_or_ui_input_used": False,
                }), flush=True)
                return
            raise AssertionError("no actionable native state within bounded observation window")
        finally:
            if worker:
                manager.park_worker(worker["instance_id"])
                for name, purpose in (
                    (worker["network"]["secret_volume"], "worker-secret"),
                    (worker["data_volume"], "worker-data"),
                ):
                    docker.require_owned(
                        docker.inspect_volume(name), manager.installation_id, purpose=purpose,
                    )
                    docker.remove_volume(name)


if __name__ == "__main__":
    main()
