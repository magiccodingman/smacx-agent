#!/usr/bin/env python3
"""Contained regression for guarded semantic production rushing."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from smacx_controller import BridgeUnavailable, bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], name: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        timeout=10,
        command=name,
        match_id=source["match_id"],
        session_id=source["session_id"],
        expected_revision=source["revision"],
        **arguments,
    )


def main() -> int:
    started = new_game(
        wait_seconds=60,
        difficulty=0,
        world_size=0,
        faction_id=1,
        blind_research=True,
        narrative_ui=False,
        tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 100
    production_selected = False
    while time.monotonic() < deadline:
        try:
            envelope = bridge_request("semantic_snapshot", timeout=5)
        except BridgeUnavailable:
            time.sleep(0.15)
            continue
        snapshot = envelope.get("snapshot", {})
        if not snapshot:
            return 3
        if snapshot["interaction"]["kind"] != "turn":
            handled, result = handle_interaction(snapshot)
            if not handled and snapshot["protocol"]["phase"] == "wait":
                time.sleep(0.1)
                continue
            emit("interaction", result)
            if not handled:
                return 4
            continue

        bases = bridge_request("list_bases").get("items", [])
        if not bases:
            return 5
        base_id = int(bases[0]["id"])
        production = bridge_request("semantic_choices", kind="production", base_id=base_id)
        hurry = next((item for item in production.get("choices", [])
                      if item.get("command") == "hurry_production"), None)
        if hurry:
            before_energy = int(snapshot["faction"]["energy_credits"])
            before_minerals = int(bases[0]["minerals"]["accumulated"])
            result = command(production, "hurry_production", base_id=base_id)
            after_snapshot = bridge_request("semantic_snapshot")["snapshot"]
            after_base = next(item for item in bridge_request("list_bases")["items"]
                              if int(item["id"]) == base_id)
            if (not result.get("ok")
            or int(after_snapshot["faction"]["energy_credits"]) != before_energy - int(hurry["energy_cost"])
            or int(after_base["minerals"]["accumulated"]) != before_minerals + int(hurry["minerals_added"])):
                emit("failure", {"result": result, "before": bases[0], "after": after_base})
                return 6
            emit("pass", {
                "base_id": base_id,
                "energy_cost": hurry["energy_cost"],
                "minerals_added": hurry["minerals_added"],
                "native_cost_verified": True,
            })
            return 0

        if not production_selected:
            unit_choices = [item for item in production.get("choices", [])
                            if item.get("command") == "set_production" and item.get("kind") == "unit"]
            if not unit_choices:
                return 7
            cheapest = min(unit_choices, key=lambda item: int(item["mineral_cost"]))
            result = command(
                production,
                "set_production",
                base_id=base_id,
                item_id=int(cheapest["item_id"]),
            )
            emit("production_selected", result)
            if not result.get("ok"):
                return 8
            production_selected = True
            continue

        units = bridge_request("list_units", scope="own").get("items", [])
        ready = next((unit for unit in units if unit.get("ready")), None)
        if ready:
            unit_choices = bridge_request(
                "semantic_choices", kind="unit_actions", unit_id=int(ready["id"])
            )
            result = command(unit_choices, "skip_unit", unit_id=int(ready["id"]))
            if not result.get("ok") and result.get("error", {}).get("code") not in {
                "stale_state", "wrong_choice_phase"
            }:
                emit("failure", result)
                return 9
            continue

        result = command(envelope["snapshot"], "end_turn")
        if not result.get("ok") and result.get("error", {}).get("code") not in {
            "stale_state", "wrong_choice_phase", "units_still_ready"
        }:
            emit("failure", result)
            return 10

    emit("failure", {"reason": "deadline"})
    return 11


if __name__ == "__main__":
    sys.exit(main())
