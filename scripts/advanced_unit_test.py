#!/usr/bin/env python3
"""Contained regression for advanced semantic unit actions."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], command_name: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        command=command_name,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def main() -> int:
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        if snapshot["interaction"]["kind"] != "turn":
            handled, outcome = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "interaction", "outcome": outcome})
                return 3
            continue

        design = bridge_request("semantic_choices", kind="unit_design")
        drop_ability = next(
            (item for item in design.get("catalogs", {}).get("abilities", [])
             if item.get("ability_id") == 4),
            None,
        )
        if drop_ability is None:
            emit("failure", {"stage": "drop_ability_fixture", "design": design})
            return 4
        created = command(
            design,
            "create_unit_design",
            chassis_id=0,
            weapon_id=2,
            armor_id=1,
            reactor_id=1,
            ability_id_1=4,
            ability_id_2=-1,
            name="Harness Drop Sentinel",
        )
        emit("created", created)
        if not created.get("ok"):
            return 5
        target_prototype_id = int(created["prototype"]["prototype_id"])

        fresh_design = bridge_request("semantic_choices", kind="unit_design")
        source_id = next(
            (int(item["prototype_id"])
             for item in fresh_design.get("available_prototypes", [])
             if item.get("name") == "Scout Patrol" and item.get("active_unit_count", 0) > 0),
            -1,
        )
        if source_id < 0:
            emit("failure", {"stage": "source", "design": fresh_design})
            return 6
        upgraded = command(
            fresh_design,
            "upgrade_prototype",
            source_prototype_id=source_id,
            target_prototype_id=target_prototype_id,
            confirm_upgrade=1,
        )
        emit("upgraded", upgraded)
        if not upgraded.get("ok"):
            return 7

        own_units = bridge_request("list_units", scope="own").get("items", [])
        unit = next(
            (item for item in own_units
             if int(item.get("prototype_id", -1)) == target_prototype_id),
            None,
        )
        if unit is None:
            emit("failure", {"stage": "upgraded_unit", "units": own_units})
            return 8
        unit_id = int(unit["id"])
        action_choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=unit_id)
        airdrop = next(
            (item for item in action_choices.get("choices", [])
             if item.get("command") == "airdrop_unit"),
            None,
        )
        if airdrop is None:
            emit("failure", {"stage": "airdrop_choice", "choices": action_choices})
            return 9
        target = next(
            (item for item in airdrop.get("targets", []) if item.get("range", 0) > 0),
            None,
        )
        if target is None:
            emit("failure", {"stage": "airdrop_target", "choice": airdrop})
            return 10
        dropped = command(
            action_choices,
            "airdrop_unit",
            unit_id=unit_id,
            target_tile_id=int(target["target_tile_id"]),
        )
        emit("dropped", dropped)
        if not dropped.get("ok") or not dropped.get("accepted"):
            return 11
        if dropped.get("observed_tile_id") != target["target_tile_id"]:
            return 12
        emit("pass", {
            "native_airdrop": True,
            "visible_rule_validated_target": True,
            "unit_design_integration": True,
            "coordinates_or_pixels_used": False,
            "target_tile_id_only": True,
        })
        return 0
    return 13


if __name__ == "__main__":
    sys.exit(main())
