#!/usr/bin/env python3
"""Contained regression for semantic native probe-team missions."""

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
        timeout=12,
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
    deadline = time.monotonic() + 120
    queued: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        interaction = snapshot.get("interaction", {}).get("kind")
        if interaction != "turn":
            handled, outcome = handle_interaction(snapshot)
            if interaction == "waiting_for_engine":
                time.sleep(0.1)
                continue
            if not handled:
                emit("failure", {"stage": "interaction", "snapshot": snapshot, "outcome": outcome})
                return 3
            continue
        if queued is None:
            unit_response = bridge_request("list_units", scope="visible")
            units = unit_response.get("items", [])
            probe = next(
                (item for item in units
                 if item.get("owner") == snapshot["faction"]["id"]
                 and item.get("roles", {}).get("probe")),
                None,
            )
            if probe is None:
                for item in units:
                    if item.get("owner") != snapshot["faction"]["id"] or not item.get("ready"):
                        continue
                    choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=int(item["id"]))
                    result = guarded(choices, "skip_unit", unit_id=int(item["id"]))
                    if not result.get("ok"):
                        if result.get("error", {}).get("code") == "stale_state":
                            break
                        emit("failure", {"stage": "fixture_skip", "result": result})
                        return 4
                fresh = bridge_request("semantic_snapshot").get("snapshot", {})
                if fresh.get("interaction", {}).get("kind") == "turn":
                    ended = guarded(fresh, "end_turn")
                    emit("fixture_wait", {"turn": fresh.get("turn"), "units": len(units), "end_turn": ended})
                time.sleep(0.2)
                continue
            choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=int(probe["id"]))
            mission = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "execute_probe_mission"
                 and item.get("mission") == "drain_energy_reserves"
                 and int(item.get("frame_faction_id", 0)) > 0),
                None,
            )
            if mission is None:
                emit("failure", {"stage": "choice", "choices": choices})
                return 5
            refused = guarded(
                choices,
                "execute_probe_mission",
                unit_id=int(probe["id"]),
                target_base_id=int(mission["target_base_id"]),
                target_tile_id=int(mission["target_tile_id"]),
                action_id=int(mission["action_id"]),
                enhanced=int(mission["enhanced"]),
                frame_faction_id=int(mission["frame_faction_id"]),
            )
            emit("confirmation_guard", refused)
            if refused.get("error", {}).get("code") != "probe_confirmation_required":
                return 6
            queued = guarded(
                choices,
                "execute_probe_mission",
                unit_id=int(probe["id"]),
                target_base_id=int(mission["target_base_id"]),
                target_tile_id=int(mission["target_tile_id"]),
                action_id=int(mission["action_id"]),
                enhanced=int(mission["enhanced"]),
                frame_faction_id=int(mission["frame_faction_id"]),
                confirm_probe_incident=1,
            )
            emit("queued", {"choice": mission, "result": queued})
            if not queued.get("ok") or not queued.get("queued"):
                emit("debug", {"unit": probe, "choices": choices})
                return 7
            continue
        status = bridge_request("action_status", action_id=int(queued["action_id"]))
        action = status.get("action", {})
        if action.get("status") == "pending":
            time.sleep(0.1)
            continue
        emit("completed", status)
        if action.get("status") != "completed" or not action.get("native_result"):
            return 8
        emit("pass", {
            "visible_adjacent_target_only": True,
            "explicit_mission_and_incident_confirmation": True,
            "native_probe_resolution": True,
            "deferred_completion": True,
            "coordinates_or_pixels_used": False,
            "object_ids_only": True,
        })
        return 0
    return 9


if __name__ == "__main__":
    sys.exit(main())
