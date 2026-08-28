#!/usr/bin/env python3
"""Contained fair-play regression for staged native targeted sabotage."""

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


def main() -> int:
    abort_mode = os.environ.get("SMACX_TEST_SABOTAGE_ABORT") == "1"
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 150
    queued: dict[str, Any] | None = None
    saw_native_target_menu = False
    selected_target: dict[str, Any] | None = None
    saw_warning = False
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        interaction = snapshot.get("interaction", {}).get("kind")
        label = snapshot.get("interaction", {}).get("popup_label", "")
        if label == "VIRUS":
            choices = bridge_request("semantic_choices", kind="interaction")
            targets = [item for item in choices.get("choices", [])
                       if item.get("command") == "choose_probe_sabotage_target"]
            if not targets:
                emit("failure", {"stage": "native_target_menu", "choices": choices})
                return 3
            saw_native_target_menu = True
            selected_target = (next(
                (item for item in targets if item.get("target_kind") == "abort"), targets[-1],
            ) if abort_mode else next(
                (item for item in targets if item.get("target_kind") == "facility"),
                next((item for item in targets if item.get("target_kind") == "production"), targets[0]),
            ))
            result = guarded(
                choices,
                "choose_probe_sabotage_target",
                sabotage_target_id=int(selected_target["sabotage_target_id"]),
            )
            emit("target_selected", {"choice": selected_target, "result": result})
            if not result.get("ok"):
                return 4
            continue
        if label in {"MILVIRUS", "HQVIRUS"}:
            choices = bridge_request("semantic_choices", kind="interaction")
            proceed = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "respond_to_probe_sabotage_warning"
                 and item.get("response") == "proceed"),
                None,
            )
            if proceed is None:
                emit("failure", {"stage": "warning", "choices": choices})
                return 5
            saw_warning = True
            result = guarded(choices, "respond_to_probe_sabotage_warning", response="proceed")
            emit("warning_resolved", {"label": label, "result": result})
            if not result.get("ok"):
                return 6
            continue
        if interaction not in {"turn", "waiting_for_engine", "waiting_for_turn"}:
            handled, outcome = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "interaction", "snapshot": snapshot, "outcome": outcome})
                return 7
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
                        return 8
                fresh = bridge_request("semantic_snapshot").get("snapshot", {})
                if fresh.get("interaction", {}).get("kind") == "turn":
                    guarded(fresh, "end_turn")
                time.sleep(0.2)
                continue
            choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=int(probe["id"]))
            mission = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "execute_probe_mission"
                 and item.get("mission") == "targeted_sabotage"),
                None,
            )
            if mission is None:
                emit("failure", {"stage": "targeted_choice", "choices": choices})
                return 9
            if any(key in mission for key in ("sabotage_target_id", "facility_id", "facilities")):
                emit("failure", {"stage": "pre_entry_leak", "choice": mission})
                return 10
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
                return 11
            continue
        if queued is not None:
            status = bridge_request("action_status", action_id=int(queued["action_id"]))
            action = status.get("action", {})
            if action.get("status") == "pending":
                time.sleep(0.1)
                continue
            emit("completed", status)
            if (action.get("status") != "completed" or not saw_native_target_menu
                    or (abort_mode and action.get("resolution") != "aborted_by_agent")):
                return 12
            emit("pass", {
                "targets_hidden_until_native_post_entry_menu": True,
                "native_menu_ids_only": True,
                "semantic_target_selection": selected_target,
                "extra_security_warning_handled": saw_warning,
                "abort_mode": abort_mode,
                "resolution": action.get("resolution"),
                "native_probe_resolution": True,
                "coordinates_or_pixels_used": False,
            })
            return 0
        time.sleep(0.1)
    return 13


if __name__ == "__main__":
    sys.exit(main())
