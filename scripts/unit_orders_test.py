#!/usr/bin/env python3
"""Contained regression for persistent semantic hold and sentry orders."""

from __future__ import annotations

import json
import sys
import time

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def run_command(unit_id: int, command: str) -> dict:
    choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=unit_id)
    if not any(item.get("command") == command for item in choices.get("choices", [])):
        return {"ok": False, "error": "choice_missing", "choices": choices}
    return bridge_request(
        "semantic_command",
        command=command,
        unit_id=unit_id,
        match_id=choices["match_id"],
        session_id=choices["session_id"],
        expected_revision=choices["revision"],
    )


def main() -> int:
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 70
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
        ready = [item for item in bridge_request("list_units", scope="own", limit=50)["items"]
                 if item.get("ready")]
        if len(ready) < 2:
            time.sleep(0.1)
            continue
        hold_id, sentry_id = int(ready[0]["id"]), int(ready[1]["id"])
        held = run_command(hold_id, "hold_unit")
        sentried = run_command(sentry_id, "sentry_unit")
        units = {int(item["id"]): item for item in bridge_request("list_units", scope="own")["items"]}
        emit("orders", {"hold": held, "sentry": sentried, "units": units})
        if (not held.get("ok") or not sentried.get("ok")
        or units[hold_id]["order"] != 2 or units[hold_id]["ready"]
        or units[sentry_id]["order"] != 1 or units[sentry_id]["ready"]):
            return 4
        emit("pass", {"hold_order": 2, "sentry_order": 1, "both_not_ready": True})
        return 0
    return 5


if __name__ == "__main__":
    sys.exit(main())
