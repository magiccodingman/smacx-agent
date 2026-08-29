#!/usr/bin/env python3
"""Verify one fully typed native LAN setup on two isolated game processes."""

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


SETTINGS = {
    "difficulty": 4,
    "time_control": 3,
    "world_size": 3,
    "ocean_coverage": 2,
    "erosive_forces": 0,
    "native_life": 2,
    "cloud_cover": 0,
    "victory_transcendence": False,
    "victory_conquest": True,
    "victory_diplomatic": False,
    "victory_economic": True,
    "victory_cooperative": True,
    "do_or_die": True,
    "look_first": False,
    "tech_stagnation": True,
    "spoils_of_war": False,
    "blind_research": False,
    "intense_rivalry": True,
    "unity_survey": True,
    "unity_scattering": False,
    "random_events": False,
    "time_warp": True,
    "ironman": True,
}


def assert_settings(actual: dict[str, object]) -> None:
    difficulty = actual.get("difficulty")
    if not isinstance(difficulty, dict) or difficulty.get("id") != SETTINGS["difficulty"]:
        raise AssertionError(f"difficulty_mismatch:{actual}")
    world = actual.get("map")
    if not isinstance(world, dict):
        raise AssertionError(f"world_settings_missing:{actual}")
    expected_world = {
        "size_id": SETTINGS["world_size"],
        "ocean_coverage": SETTINGS["ocean_coverage"],
        "erosive_forces": SETTINGS["erosive_forces"],
        "native_life": SETTINGS["native_life"],
        "cloud_cover": SETTINGS["cloud_cover"],
    }
    for key, expected in expected_world.items():
        if world.get(key) != expected:
            raise AssertionError(f"world_mismatch:{key}:{world.get(key)}:{expected}")
    rules = actual.get("rules")
    if not isinstance(rules, dict):
        raise AssertionError(f"rule_settings_missing:{actual}")
    for key, expected in SETTINGS.items():
        if key.startswith("victory_") or key in {
            "do_or_die", "look_first", "tech_stagnation", "spoils_of_war",
            "blind_research", "intense_rivalry", "unity_survey",
            "unity_scattering", "random_events", "time_warp", "ironman",
        }:
            if rules.get(key) is not expected:
                raise AssertionError(f"rule_mismatch:{key}:{rules.get(key)}:{expected}")


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
    suffix = secrets.token_hex(4)
    agent_ids = [f"agent-custom-lan-{suffix}-{index}" for index in range(2)]
    for index, agent_id in enumerate(agent_ids):
        control.store.ensure_agent(agent_id, f"Custom LAN Seat {index + 1}")
    match_id = f"match-custom-lan-{suffix}"
    created = control.create_lan_match(
        "Typed custom LAN live test", agent_ids, match_id=match_id,
    )
    instance_ids: list[str] = []
    try:
        for seat in created["seats"]:
            worker = manager.provision_worker(
                MemoryScope(match_id, seat["agent_id"], seat["perspective_id"]),
                sources[0]["game_source_id"], runtimes[0]["runtime_id"],
                autostart={"enabled": False},
            )
            instance_ids.append(worker["instance_id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(
                lambda instance_id: manager.start_worker(instance_id, timeout=600),
                instance_ids,
            ))
        result = manager.start_lan_match(
            match_id, session_name="SMACX Typed LAN", game_settings=SETTINGS,
            timeout=600,
        )
        if not result.get("ok") or result.get("profile") != "custom":
            raise AssertionError(f"custom_lan_failed:{result}")
        for instance_id in instance_ids:
            snapshot = manager._native_request(instance_id, "semantic_snapshot")
            if not snapshot.get("ok"):
                raise AssertionError(f"snapshot_failed:{snapshot}")
            game_settings = snapshot.get("snapshot", {}).get("game_settings")
            if not isinstance(game_settings, dict):
                raise AssertionError(f"game_settings_missing:{snapshot}")
            assert_settings(game_settings)
        print(json.dumps({
            "ok": True, "match_id": match_id, "workers": len(instance_ids),
            "settings": SETTINGS, "pixels_or_ui_input_used": False,
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
