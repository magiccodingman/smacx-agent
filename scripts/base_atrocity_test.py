#!/usr/bin/env python3
"""Contained semantic regression for confirmation-gated native nerve stapling."""

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
    deadline = time.monotonic() + 150
    queued: dict[str, Any] | None = None
    base_id = -1
    confirmation_checked = False
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        interaction = snapshot.get("interaction", {}).get("kind")
        if interaction not in {"turn", "waiting_for_engine", "waiting_for_turn"}:
            handled, outcome = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "interaction", "snapshot": snapshot, "outcome": outcome})
                return 3
            continue
        if queued is None and interaction == "turn":
            bases = bridge_request("list_bases").get("items", [])
            if not bases:
                emit("failure", {"stage": "base_fixture"})
                return 4
            base_id = int(bases[0]["id"])
            choices = bridge_request("semantic_choices", kind="base_management", base_id=base_id)
            action = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "nerve_staple"),
                None,
            )
            if action is None or action.get("confirm_atrocity") != 1 or not action.get("atrocity"):
                emit("failure", {"stage": "choice", "choices": choices})
                return 5
            refused = guarded(choices, "nerve_staple", base_id=base_id)
            emit("atrocity_guard", refused)
            if refused.get("error", {}).get("code") != "atrocity_confirmation_required":
                return 6
            confirmation_checked = True
            queued = guarded(choices, "nerve_staple", base_id=base_id, confirm_atrocity=1)
            emit("queued", {"choice": action, "result": queued})
            if not queued.get("ok") or not queued.get("queued"):
                return 7
            continue
        if queued is not None:
            status = bridge_request("action_status", action_id=int(queued["action_id"]))
            action = status.get("action", {})
            if action.get("status") == "pending":
                time.sleep(0.1)
                continue
            emit("completed", status)
            if action.get("status") != "completed" or action.get("resolution") != "stapled":
                return 8
            bases = bridge_request("list_bases").get("items", [])
            base = next((item for item in bases if int(item.get("id", -1)) == base_id), None)
            if base is None or int(base.get("nerve_stapling", {}).get("turns_left", 0)) <= 0:
                emit("failure", {"stage": "effect", "base": base})
                return 9
            fresh = bridge_request("semantic_choices", kind="base_management", base_id=base_id)
            if any(item.get("command") == "nerve_staple" for item in fresh.get("choices", [])):
                emit("failure", {"stage": "repeat_suppression", "choices": fresh})
                return 10
            emit("pass", {
                "explicit_atrocity_confirmation": confirmation_checked,
                "native_atrocity_resolution": True,
                "turns_left": base["nerve_stapling"]["turns_left"],
                "repeat_suppressed_while_active": True,
                "coordinates_or_pixels_used": False,
            })
            return 0
        time.sleep(0.1)
    return 11


if __name__ == "__main__":
    sys.exit(main())
