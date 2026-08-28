#!/usr/bin/env python3
"""Contained regression for semantic native Psi Gate transfers."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def guarded(source: dict[str, Any], command: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command",
        command=command,
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
        if snapshot.get("interaction", {}).get("kind") != "turn":
            handled, outcome = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "interaction", "outcome": outcome})
                return 3
            continue
        units = bridge_request("list_units", scope="own").get("items", [])
        for unit in units:
            if not unit.get("ready"):
                continue
            choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=int(unit["id"]))
            gate = next((item for item in choices.get("choices", [])
                         if item.get("command") == "use_psi_gate"), None)
            if gate is None:
                continue
            result = guarded(
                choices,
                "use_psi_gate",
                unit_id=int(unit["id"]),
                source_base_id=int(gate["source_base_id"]),
                destination_base_id=int(gate["destination_base_id"]),
            )
            emit("transfer", {"choice": gate, "result": result})
            expected = gate["destination"]
            if not result.get("ok"):
                return 4
            if result.get("observed") != expected:
                return 5
            if not result.get("source_gate_used") or not result.get("destination_gate_used"):
                return 6
            after = bridge_request("semantic_choices", kind="unit_actions", unit_id=int(unit["id"]))
            repeated = any(item.get("command") == "use_psi_gate"
                           for item in after.get("choices", []))
            emit("pass", {
                "native_transfer": True,
                "both_gate_endpoints_consumed": True,
                "repeat_transfer_not_offered": not repeated,
                "coordinates_or_pixels_used": False,
            })
            return 0 if not repeated else 7
        emit("failure", {"stage": "fixture", "units": units})
        return 8
    return 9


if __name__ == "__main__":
    sys.exit(main())
