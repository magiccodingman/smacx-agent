#!/usr/bin/env python3
"""Replay a pre-sunspots LAN save and verify guarded local continuation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import MemoryScope, SmacxStore
from smacx_worker_manager import WorkerManager


def main() -> int:
    saved_path = Path(os.environ["SMACX_SUNSPOTS_TEST_SAVE"])
    saved = saved_path.read_bytes()
    os.environ["SMACX_AGENT_TEST_MODE"] = "1"
    os.environ["SMACX_AGENT_TEST_LAN_HOST"] = "1"
    docker = DockerClient()
    with tempfile.TemporaryDirectory(prefix="smacx-sunspots-") as temporary:
        root = Path(temporary)
        store = SmacxStore(root / "state.sqlite3")
        installation_id = os.environ.get("SMACX_SUNSPOTS_TEST_INSTALLATION_ID")
        if installation_id:
            assert re.fullmatch(r"installation-[a-f0-9]{32}", installation_id)
            with store.transaction() as connection:
                connection.execute(
                    "INSERT INTO installations(singleton, installation_id, created_unix) "
                    "VALUES (1, ?, ?)", (installation_id, time.time()),
                )
        control = ControlPlane(store, root / "secrets")
        manager = WorkerManager(
            control, docker, worker_image=os.environ["SMACX_TEST_WORKER_IMAGE"],
        )
        source = manager.validate_game_source(
            os.environ["SMACX_TEST_GAME_SOURCE"], display_name="Sunspots replay",
        )
        runtime = manager.ensure_bundled_runtime()
        agents = ["agent-sunspots-host", "agent-sunspots-peer"]
        for agent in agents:
            control.store.ensure_agent(agent, agent)
        created = control.create_lan_match("Sunspots replay", agents)
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
                match_id, session_name="Sunspots regression",
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

            observed = {}
            def complete():
                return all(set(observed.get(label, {}).get("acknowledging_clients", []))
                           == set(ids) for label in {"SUNSPOTS", "NOMORESPOTS"})

            deadline = time.monotonic() + 600
            while time.monotonic() < deadline and not complete():
                progressed = False
                for instance_id in ids:
                    snapshot = call(instance_id, "semantic_snapshot")["snapshot"]
                    interaction = snapshot["interaction"]
                    label = interaction.get("popup_label") or ""
                    if label in {"SUNSPOTS", "NOMORESPOTS"}:
                        frame = call(instance_id, "semantic_choices", kind="interaction")
                        acknowledgement = next(
                            (row for row in frame.get("choices", [])
                             if row.get("command") == "acknowledge_popup"), None,
                        )
                        context = next(
                            (row for row in frame.get("choices", [])
                             if row.get("id") == "ecology:sunspots_context"), None,
                        )
                        assert acknowledgement and context, frame
                        expected_event = "sunspot_activity_started" \
                            if label == "SUNSPOTS" else "sunspot_activity_ended"
                        assert context["event"] == expected_event, context
                        assert context["sunspots_active"] is (label == "SUNSPOTS"), context
                        assert (context["sunspot_duration"] > 0) is (label == "SUNSPOTS"), context
                        result = command(instance_id, frame, "acknowledge_popup")
                        assert result.get("ok") and result["popup_label"] == label, result
                        for _ in range(100):
                            current = call(instance_id, "semantic_snapshot")["snapshot"]
                            if current["interaction"].get("popup_label") != label:
                                break
                            time.sleep(.1)
                        else:
                            raise AssertionError(f"{label} popup did not leave the native stack")
                        durations = [call(peer, "semantic_snapshot")["snapshot"]
                                     ["ecology"]["sunspot_duration"] for peer in ids]
                        assert all((value > 0) is (label == "SUNSPOTS")
                                   for value in durations), durations
                        evidence = observed.setdefault(label, {
                            "acknowledging_clients": [],
                            "dismissal_left_popup_stack": True,
                            "replica_sunspot_durations": durations,
                        })
                        if instance_id not in evidence["acknowledging_clients"]:
                            evidence["acknowledging_clients"].append(instance_id)
                        progressed = True
                        continue
                    if interaction["kind"] not in {
                        "turn", "waiting_for_turn", "waiting_for_engine",
                    }:
                        frame = call(instance_id, "semantic_choices", kind="interaction")
                        continuation = next((row for row in frame.get("choices", [])
                            if row.get("command") in {
                                "acknowledge_popup", "advance_technology_presentation",
                                "advance_project_information", "choose_research_priority",
                            }), None)
                        if continuation:
                            arguments = ({"priority": continuation["priority"]}
                                if continuation["command"] == "choose_research_priority"
                                else {})
                            result = command(
                                instance_id, frame, continuation["command"],
                                **arguments,
                            )
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
                            assert result.get("ok") or result.get("error", {}).get("code") in {
                                "not_actionable", "stale_state",
                                "multiplayer_command_not_validated",
                            }, result
                            progressed = True
                            break
                    else:
                        frame = call(instance_id, "semantic_choices", kind="game_management")
                        if any(row.get("command") == "end_turn"
                               for row in frame.get("choices", [])):
                            result = command(instance_id, frame, "end_turn")
                            assert result.get("ok") or result.get("error", {}).get("code") in {
                                "not_actionable", "stale_state",
                                "multiplayer_command_not_validated",
                            }, result
                            progressed = True
                if not progressed and not complete():
                    time.sleep(.2)
            assert complete(), observed
            print(json.dumps({
                "passed": True,
                "save_sha256": hashlib.sha256(saved).hexdigest(),
                "native_clients": 2,
                "sunspots_started": observed["SUNSPOTS"],
                "sunspots_ended": observed["NOMORESPOTS"],
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
