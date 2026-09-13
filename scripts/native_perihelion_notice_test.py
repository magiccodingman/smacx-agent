#!/usr/bin/env python3
"""Replay a pre-perihelion LAN save and verify guarded local continuation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import MemoryScope, SmacxStore
from smacx_worker_manager import WorkerManager


def main() -> int:
    saved_path = Path(os.environ["SMACX_PERIHELION_TEST_SAVE"])
    saved = saved_path.read_bytes()
    os.environ["SMACX_AGENT_TEST_MODE"] = "1"
    os.environ["SMACX_AGENT_TEST_LAN_HOST"] = "1"
    docker = DockerClient()
    with tempfile.TemporaryDirectory(prefix="smacx-perihelion-") as temporary:
        root = Path(temporary)
        control = ControlPlane(SmacxStore(root / "state.sqlite3"), root / "secrets")
        manager = WorkerManager(
            control, docker, worker_image=os.environ["SMACX_TEST_WORKER_IMAGE"],
        )
        source = manager.validate_game_source(
            os.environ["SMACX_TEST_GAME_SOURCE"], display_name="Perihelion replay",
        )
        runtime = manager.ensure_bundled_runtime()
        agents = ["agent-perihelion-host", "agent-perihelion-peer"]
        for agent in agents:
            control.store.ensure_agent(agent, agent)
        created = control.create_lan_match("Perihelion replay", agents)
        match_id = created["match"]["match_id"]
        workers = []
        try:
            for index, seat in enumerate(created["seats"]):
                control.update_lan_seat(match_id, index, faction_id=index + 1)
                worker = manager.provision_worker(
                    MemoryScope(match_id, seat["agent_id"], seat["perspective_id"]),
                    source["game_source_id"], runtime["runtime_id"],
                    autostart={"enabled": False}, view_enabled=False,
                )
                workers.append(worker)
                seed = (
                    "from pathlib import Path;import sys,os;"
                    "p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);"
                    "p.write_bytes(sys.stdin.buffer.read());"
                    "[os.chown(x,10001,10001) for x in [p,*list(p.parents)[:5]]]"
                )
                subprocess.run(
                    ["docker", "run", "--rm", "-i", "--user", "0",
                     "--entrypoint", "python3", "-v", worker["data_volume"] + ":/data",
                     os.environ["SMACX_TEST_CONTROL_IMAGE"], "-c", seed,
                     "/data/game/saves/agent/" + match_id + "/replay.sav"],
                    input=saved, check=True,
                )
            manager.start_lan_match(
                match_id, session_name="Perihelion regression",
                resume_slot="replay", _defer_ready=True,
            )
            ids = [worker["instance_id"] for worker in workers]

            def call(instance_id, operation, **arguments):
                return manager._native_request(
                    instance_id, operation, timeout=30, **arguments,
                )

            def command(instance_id, frame, name, **arguments):
                return call(
                    instance_id, "semantic_command", command=name,
                    match_id=frame["match_id"], session_id=frame["session_id"],
                    expected_revision=frame["revision"], **arguments,
                )

            observed = None
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline and observed is None:
                progressed = False
                for instance_id in ids:
                    snapshot = call(instance_id, "semantic_snapshot")["snapshot"]
                    interaction = snapshot["interaction"]
                    label = interaction.get("popup_label") or ""
                    if label == "PERIHELION":
                        frame = call(instance_id, "semantic_choices", kind="interaction")
                        acknowledgement = next(
                            (row for row in frame.get("choices", [])
                             if row.get("command") == "acknowledge_popup"), None,
                        )
                        context = next(
                            (row for row in frame.get("choices", [])
                             if row.get("id") == "ecology:perihelion_context"), None,
                        )
                        assert acknowledgement and context, frame
                        assert context["event"] == "perihelion_started" and \
                            context["perihelion_active"] is True, context
                        before = [call(peer, "test_network_sync_status") for peer in ids]
                        result = command(instance_id, frame, "acknowledge_popup")
                        assert result.get("ok") and result["popup_label"] == "PERIHELION", result
                        for _ in range(100):
                            current = call(instance_id, "semantic_snapshot")["snapshot"]
                            if current["interaction"].get("popup_label") != "PERIHELION":
                                break
                            time.sleep(.1)
                        else:
                            raise AssertionError("PERIHELION popup did not leave the native stack")
                        after = [call(peer, "test_network_sync_status") for peer in ids]
                        assert after[0]["vehicles"] == after[1]["vehicles"]
                        assert after[0]["bases"] == after[1]["bases"]
                        assert before[0]["vehicles"] == after[0]["vehicles"]
                        assert before[0]["bases"] == after[0]["bases"]
                        assert all(call(peer, "semantic_snapshot")["snapshot"]
                                   ["ecology"]["perihelion_active"] for peer in ids)
                        observed = {
                            "acknowledging_client": instance_id,
                            "dismissal_left_popup_stack": True,
                            "shared_units_unchanged": True,
                            "shared_bases_unchanged": True,
                            "both_replicas_perihelion_active": True,
                        }
                        break
                    if interaction["kind"] not in {
                        "turn", "waiting_for_turn", "waiting_for_engine",
                    }:
                        frame = call(instance_id, "semantic_choices", kind="interaction")
                        if any(row.get("command") == "acknowledge_popup"
                               for row in frame.get("choices", [])):
                            result = command(instance_id, frame, "acknowledge_popup")
                            assert result.get("ok") or result.get("error", {}).get("code") == "stale_state", result
                            progressed = True
                        continue
                    if interaction["kind"] != "turn":
                        continue
                    faction = snapshot["faction"]["id"]
                    for vehicle in call(instance_id, "test_network_sync_status")["vehicles"]:
                        if vehicle["faction_id"] != faction:
                            continue
                        frame = call(instance_id, "semantic_choices", kind="unit_actions",
                                     unit_id=vehicle["id"])
                        if any(row.get("command") == "skip_unit"
                               for row in frame.get("choices", [])):
                            result = command(instance_id, frame, "skip_unit",
                                             unit_id=vehicle["id"])
                            assert result.get("ok"), result
                            progressed = True
                            break
                    else:
                        frame = call(instance_id, "semantic_choices", kind="game_management")
                        if any(row.get("command") == "end_turn"
                               for row in frame.get("choices", [])):
                            result = command(instance_id, frame, "end_turn")
                            assert result.get("ok") or result.get("error", {}).get("code") in {
                                "not_actionable", "stale_state",
                            }, result
                            progressed = True
                if not progressed and observed is None:
                    time.sleep(.2)
            assert observed is not None, "replay did not reach PERIHELION"
            print(json.dumps({
                "passed": True,
                "save_sha256": hashlib.sha256(saved).hexdigest(),
                "native_clients": 2,
                **observed,
            }))
            return 0
        finally:
            for worker in reversed(workers):
                try:
                    manager.park_worker(worker["instance_id"])
                except Exception:
                    pass
                for name, purpose in (
                    (worker["data_volume"], "worker-data"),
                    (worker["network"]["secret_volume"], "worker-secret"),
                ):
                    try:
                        docker.require_owned(
                            docker.inspect_volume(name), manager.installation_id,
                            purpose=purpose,
                        )
                        docker.remove_volume(name)
                    except Exception:
                        pass


if __name__ == "__main__":
    raise SystemExit(main())
