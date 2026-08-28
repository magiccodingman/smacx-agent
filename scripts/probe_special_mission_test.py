#!/usr/bin/env python3
"""Contained regressions for genetic plague and captive-leader rescue."""

from __future__ import annotations

import json
import os
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


def mission_arguments(probe: dict[str, Any], mission: dict[str, Any]) -> dict[str, int]:
    return {
        "unit_id": int(probe["id"]),
        "target_base_id": int(mission["target_base_id"]),
        "target_tile_id": int(mission["target_tile_id"]),
        "action_id": int(mission["action_id"]),
        "enhanced": int(mission["enhanced"]),
        "frame_faction_id": int(mission["frame_faction_id"]),
        "confirm_probe_incident": 1,
    }


def main() -> int:
    mode = os.environ.get("SMACX_TEST_PROBE_SPECIAL", "plague")
    if mode not in {"plague", "leader"}:
        raise SystemExit("SMACX_TEST_PROBE_SPECIAL must be plague or leader")
    mission_name = "genetic_plague" if mode == "plague" else "free_captured_leader"
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 180
    queued: dict[str, Any] | None = None
    chosen_captive: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        interaction = snapshot.get("interaction", {}).get("kind")
        label = snapshot.get("interaction", {}).get("popup_label", "")
        if label == "FREEWHO":
            choices = bridge_request("semantic_choices", kind="interaction")
            captive_choices = [item for item in choices.get("choices", [])
                               if item.get("command") == "choose_captive_leader"]
            if mode != "leader" or not captive_choices:
                emit("failure", {"stage": "captive_menu", "choices": choices})
                return 3
            chosen_captive = captive_choices[0]
            result = guarded(
                choices,
                "choose_captive_leader",
                captive_faction_id=int(chosen_captive["captive_faction_id"]),
            )
            emit("captive_selected", {"choice": chosen_captive, "result": result})
            if not result.get("ok"):
                return 4
            continue
        if interaction not in {"turn", "waiting_for_engine", "waiting_for_turn"}:
            handled, outcome = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "interaction", "snapshot": snapshot, "outcome": outcome})
                return 5
            continue
        if queued is None and interaction == "turn":
            units = bridge_request("list_units", scope="visible").get("items", [])
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
                    if not result.get("ok") and result.get("error", {}).get("code") != "stale_state":
                        emit("failure", {"stage": "fixture_skip", "result": result})
                        return 6
                fresh = bridge_request("semantic_snapshot").get("snapshot", {})
                if fresh.get("interaction", {}).get("kind") == "turn":
                    guarded(fresh, "end_turn")
                time.sleep(0.2)
                continue
            choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=int(probe["id"]))
            mission = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "execute_probe_mission"
                 and item.get("mission") == mission_name),
                None,
            )
            if mission is None:
                emit("failure", {"stage": "mission_choice", "mode": mode, "choices": choices})
                return 7
            if mode == "leader" and "captive_faction_id" in mission:
                emit("failure", {"stage": "pre_success_identity_leak", "choice": mission})
                return 8
            arguments = mission_arguments(probe, mission)
            if mode == "plague":
                refused = guarded(choices, "execute_probe_mission", **arguments)
                emit("atrocity_guard", refused)
                if refused.get("error", {}).get("code") != "atrocity_confirmation_required":
                    return 9
                arguments["confirm_atrocity"] = 1
            queued = guarded(choices, "execute_probe_mission", **arguments)
            emit("queued", {"choice": mission, "result": queued})
            if not queued.get("ok") or not queued.get("queued"):
                return 10
            continue
        if queued is not None:
            status = bridge_request("action_status", action_id=int(queued["action_id"]))
            action = status.get("action", {})
            if action.get("status") == "pending":
                time.sleep(0.1)
                continue
            emit("completed", status)
            if action.get("status") != "completed":
                return 11
            if mode == "leader":
                if chosen_captive is None:
                    return 12
                factions = bridge_request("list_factions").get("items", [])
                captive_id = int(chosen_captive["captive_faction_id"])
                if not any(int(item.get("id", -1)) == captive_id for item in factions):
                    emit("failure", {"stage": "liberated_faction_visibility", "factions": factions})
                    return 13
            emit("pass", {
                "mode": mode,
                "explicit_atrocity_confirmation": mode == "plague",
                "captive_identity_hidden_until_native_menu": mode == "leader",
                "chosen_captive": chosen_captive,
                "native_probe_resolution": True,
                "coordinates_or_pixels_used": False,
            })
            return 0
        time.sleep(0.1)
    return 14


if __name__ == "__main__":
    sys.exit(main())
