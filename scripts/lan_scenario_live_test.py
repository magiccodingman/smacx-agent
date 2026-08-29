#!/usr/bin/env python3
"""Run one two-seat native multiplayer scenario without pixels or UI input."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets

from smacx_control_server import build_control
from smacx_docker import DockerClient
from smacx_store import MemoryScope
from smacx_worker_manager import WorkerManager


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--directx-redist", required=True)
    parser.add_argument("--worker-image", default="smacx-agent-worker:dev")
    arguments = parser.parse_args()
    control = build_control(arguments.data_root)
    manager = WorkerManager(
        control, DockerClient(), worker_image=arguments.worker_image,
        directx_redist_host_path=arguments.directx_redist,
    )
    sources = [item for item in control.list_game_sources() if item["status"] == "validated"]
    runtimes = [item for item in control.list_runtimes() if item["status"] == "ready"]
    if not sources or not runtimes:
        raise RuntimeError("validated_game_source_and_runtime_required")
    scenarios = manager.list_scenarios(sources[0]["game_source_id"])["scenarios"]
    # Use a scenario explicitly authored for multiple players when available.
    selected = next(
        (item for item in scenarios if "MP--" in item["scenario_id"]), scenarios[0],
    )
    suffix = secrets.token_hex(4)
    agent_ids = [f"agent-lan-scenario-{suffix}-{index}" for index in range(2)]
    for index, agent_id in enumerate(agent_ids):
        control.store.ensure_agent(agent_id, f"LAN Scenario Seat {index + 1}")
    match_id = f"match-lan-scenario-{suffix}"
    created = control.create_lan_match(
        "Native multiplayer scenario live test", agent_ids, match_id=match_id,
        metadata={"scenario_id": selected["scenario_id"]},
    )
    instance_ids: list[str] = []
    try:
        for seat in created["seats"]:
            worker = manager.provision_worker(
                MemoryScope(match_id, seat["agent_id"], seat["perspective_id"]),
                sources[0]["game_source_id"], runtimes[0]["runtime_id"],
                autostart={"enabled": False, "lan_scenario_id": selected["scenario_id"]},
            )
            instance_ids.append(worker["instance_id"])
        # Prefix and DirectPlay initialization are independent per worker.
        with ThreadPoolExecutor(max_workers=2) as pool:
            started_workers = list(pool.map(
                lambda instance_id: manager.start_worker(instance_id, timeout=600),
                instance_ids,
            ))
        result = manager.start_lan_match(
            match_id, session_name="SMACX Scenario Live",
            scenario_id=selected["scenario_id"], timeout=600,
        )
        if not result.get("ok") or len(result.get("seats", [])) != 2:
            raise AssertionError(f"multiplayer_scenario_failed:{result}")
        if result.get("scenario_id") != selected["scenario_id"]:
            raise AssertionError("multiplayer_scenario_identity_missing")
        snapshots = [manager._native_request(
            instance_id, "semantic_snapshot",
        ) for instance_id in instance_ids]
        if any(not item.get("ok") or not item.get("snapshot", {}).get(
                "scenario", {}).get("active") for item in snapshots):
            raise AssertionError(f"scenario_state_missing:{snapshots}")
        print(json.dumps({
            "ok": True, "scenario_id": selected["scenario_id"],
            "workers_initialized": len(started_workers),
            "seats": result["seats"], "pixels_or_ui_input_used": False,
        }, sort_keys=True))
    finally:
        for instance_id in reversed(instance_ids):
            try:
                manager.park_worker(instance_id)
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
