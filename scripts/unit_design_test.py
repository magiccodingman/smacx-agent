#!/usr/bin/env python3
"""Contained regression for semantic Unit Workshop design lifecycle."""

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


def choices() -> dict[str, Any]:
    return bridge_request("semantic_choices", kind="unit_design")


def create(source: dict[str, Any], name: str, chassis_id: int) -> dict[str, Any]:
    return command(
        source,
        "create_unit_design",
        chassis_id=chassis_id,
        weapon_id=2,
        armor_id=1,
        reactor_id=1,
        ability_id_1=-1,
        ability_id_2=-1,
        name=name,
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

        initial = choices()
        emit("initial_choices", initial)
        catalogs = initial.get("catalogs", {})
        required = (
            any(item.get("chassis_id") == 1 for item in catalogs.get("chassis", []))
            and any(item.get("weapon_id") == 2 for item in catalogs.get("weapons", []))
            and any(item.get("armor_id") == 1 for item in catalogs.get("armor", []))
            and any(item.get("reactor_id") == 1 for item in catalogs.get("reactors", []))
        )
        if not required:
            emit("failure", {"stage": "fixture_catalog", "catalogs": catalogs})
            return 4

        disposable = create(initial, "Harness Rover", 1)
        emit("created_disposable", disposable)
        if not disposable.get("ok"):
            return 5
        disposable_id = int(disposable["prototype"]["prototype_id"])

        stale = command(initial, "retire_unit_design", prototype_id=disposable_id, confirm_retire=1)
        fresh = choices()
        refused_retire = command(fresh, "retire_unit_design", prototype_id=disposable_id)
        retired = command(fresh, "retire_unit_design", prototype_id=disposable_id, confirm_retire=1)
        emit("retirement", {"stale": stale, "refused": refused_retire, "retired": retired})
        if stale.get("error", {}).get("code") != "stale_state" \
        or refused_retire.get("error", {}).get("code") != "retire_confirmation_required" \
        or not retired.get("ok") or retired.get("active") is not False:
            return 6

        before_target = choices()
        target = create(before_target, "Harness Sentinel", 0)
        emit("created_upgrade_target", target)
        if not target.get("ok"):
            return 7
        target_id = int(target["prototype"]["prototype_id"])

        owned_units = bridge_request("list_units", scope="own").get("items", [])
        source_ids = {int(item["prototype_id"]) for item in owned_units}
        after_target = choices()
        prototypes = {int(item["prototype_id"]): item
                      for item in after_target.get("available_prototypes", [])}
        source_id = next(
            (unit_id for unit_id in source_ids
             if unit_id in prototypes
             and prototypes[unit_id].get("chassis_id") == 0
             and prototypes[unit_id].get("weapon_id", 99) <= 2),
            -1,
        )
        if source_id < 0:
            emit("failure", {"stage": "upgrade_source", "units": owned_units})
            return 8

        refused_upgrade = command(
            after_target,
            "upgrade_prototype",
            source_prototype_id=source_id,
            target_prototype_id=target_id,
        )
        upgraded = command(
            after_target,
            "upgrade_prototype",
            source_prototype_id=source_id,
            target_prototype_id=target_id,
            confirm_upgrade=1,
        )
        emit("upgrade", {"refused": refused_upgrade, "upgraded": upgraded})
        if refused_upgrade.get("error", {}).get("code") != "upgrade_confirmation_required" \
        or not upgraded.get("ok") or upgraded.get("units_upgraded", 0) < 1:
            return 9

        after_upgrade = choices()
        in_use = command(
            after_upgrade,
            "retire_unit_design",
            prototype_id=target_id,
            confirm_retire=1,
        )
        emit("in_use_guard", in_use)
        if in_use.get("error", {}).get("code") != "prototype_in_use":
            return 10

        emit("pass", {
            "catalogs_structured": True,
            "created": True,
            "stale_guard": True,
            "retirement_confirmation": True,
            "bulk_upgrade_confirmation": True,
            "in_use_retirement_blocked": True,
            "coordinates_or_pixels_used": False,
        })
        return 0
    return 11


if __name__ == "__main__":
    sys.exit(main())
