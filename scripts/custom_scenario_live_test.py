#!/usr/bin/env python3
"""Live legal-copy verification for typed custom worlds and solo scenarios."""

from __future__ import annotations

import argparse
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
    catalog = manager.list_scenarios(sources[0]["game_source_id"])
    scenarios = catalog.get("scenarios", [])
    if not scenarios:
        raise RuntimeError("legal_copy_scenario_catalog_empty")

    suffix = secrets.token_hex(4)
    workers: list[str] = []
    results: dict[str, object] = {"scenario_count": len(scenarios)}
    try:
        agent_id = f"agent-live-{suffix}"
        control.store.ensure_agent(agent_id, "Typed Setup Live Test")
        custom_match = f"match-custom-{suffix}"
        created = control.create_solo_match(
            "Typed custom world live test", agent_id, match_id=custom_match,
            faction_id=1, faction_name="Gaia's Stepdaughters",
        )
        custom = manager.provision_worker(
            MemoryScope(custom_match, agent_id, created["perspective"]["perspective_id"]),
            sources[0]["game_source_id"], runtimes[0]["runtime_id"],
            autostart={
                "enabled": True, "difficulty": 2, "faction_id": 1,
                "game_settings": {
                    "map_generation": "custom", "world_size": 99,
                    "custom_width": 64, "custom_height": 48,
                    "ocean_coverage": 0, "erosive_forces": 2,
                    "native_life": 0, "cloud_cover": 2,
                    "victory_transcendence": False,
                    "victory_conquest": True, "blind_research": False,
                    "unity_survey": True, "unity_scattering": False,
                    "random_events": False, "ironman": True,
                },
            },
        )
        workers.append(custom["instance_id"])
        try:
            manager.start_worker(custom["instance_id"], timeout=600)
        except Exception:
            print(json.dumps({"custom_worker_failure": control.get_worker_spec(
                custom["instance_id"]
            ).get("last_error")}, sort_keys=True), flush=True)
            raise
        custom_state = manager._wait_native(  # live contract probe
            custom["instance_id"], "semantic_snapshot",
            lambda value: isinstance(value.get("snapshot", {}).get("game_settings"), dict),
            timeout=120, context="typed_custom_world",
        )["snapshot"]
        settings = custom_state["game_settings"]
        expected_map = {
            # SMACX's native custom-map dialog stores the horizontal input as
            # half the staggered tile-grid width. A requested 64 is therefore
            # reported by the engine as MapAreaX=128.
            "size_id": 99, "width": 128, "height": 48,
            "ocean_coverage": 0, "erosive_forces": 2,
            "native_life": 0, "cloud_cover": 2,
        }
        for key, value in expected_map.items():
            if settings["map"].get(key) != value:
                raise AssertionError(f"custom_map_mismatch:{key}:{settings['map'].get(key)}")
        expected_rules = {
            "victory_transcendence": False, "victory_conquest": True,
            "blind_research": False, "unity_survey": True,
            "unity_scattering": False, "random_events": False, "ironman": True,
        }
        for key, value in expected_rules.items():
            if settings["rules"].get(key) is not value:
                raise AssertionError(f"custom_rule_mismatch:{key}:{settings['rules'].get(key)}")
        results["typed_custom_world"] = True
        manager.park_worker(custom["instance_id"])

        scenario_id = str(scenarios[0]["scenario_id"])
        scenario_agent = f"agent-scenario-{suffix}"
        control.store.ensure_agent(scenario_agent, "Scenario Live Test")
        scenario_match = f"match-scenario-{suffix}"
        scenario_created = control.create_solo_match(
            "Typed scenario live test", scenario_agent, match_id=scenario_match,
            faction_id=1, faction_name="Scenario faction",
        )
        scenario_worker = manager.provision_worker(
            MemoryScope(
                scenario_match, scenario_agent,
                scenario_created["perspective"]["perspective_id"],
            ), sources[0]["game_source_id"], runtimes[0]["runtime_id"],
            autostart={"enabled": False, "scenario_id": scenario_id,
                       "difficulty": 0, "faction_id": 1},
        )
        workers.append(scenario_worker["instance_id"])
        try:
            manager.start_worker(scenario_worker["instance_id"], timeout=600)
        except Exception:
            print(json.dumps({"scenario_worker_failure": control.get_worker_spec(
                scenario_worker["instance_id"]
            ).get("last_error")}, sort_keys=True), flush=True)
            raise
        scenario_state = manager._wait_native(
            scenario_worker["instance_id"], "semantic_snapshot",
            lambda value: value.get("snapshot", {}).get("scenario", {}).get("active") is True,
            timeout=120, context="typed_solo_scenario",
        )["snapshot"]
        if scenario_state["scenario"].get("scenario_id") != scenario_id:
            raise AssertionError("scenario_identity_mismatch")
        results["typed_solo_scenario"] = True
        results["scenario_id"] = scenario_id
        manager.park_worker(scenario_worker["instance_id"])
    finally:
        for instance_id in reversed(workers):
            try:
                manager.park_worker(instance_id)
            except Exception:
                pass
    print(json.dumps({"ok": True, "results": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
