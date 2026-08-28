#!/usr/bin/env python3
"""Contained live-game regression for the match knowledge observation guard."""

from __future__ import annotations

import json
import time

from semantic_playthrough import handle_interaction
from smacx_controller import (
    BridgeUnavailable,
    bridge_request,
    new_game,
    put_match_knowledge,
    read_match_knowledge,
)


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 30
    snapshot: dict = {}
    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if snapshot.get("interaction", {}).get("kind") == "turn":
            break
        handled, reason = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "opening", "reason": reason, "snapshot": snapshot})
            return 3
        time.sleep(0.05)
    if snapshot.get("interaction", {}).get("kind") != "turn":
        emit("failure", {"stage": "turn_not_reached", "snapshot": snapshot})
        return 4

    match_id = str(snapshot["match_id"])
    session_id = str(snapshot["session_id"])
    revision = str(snapshot["revision"])
    raw_ui = bridge_request("act", action="click", x1000=500, y1000=500)
    if raw_ui.get("error", {}).get("code") != "raw_ui_disabled":
        emit("failure", {"stage": "raw_ui_gate", "result": raw_ui})
        return 5
    recorded = put_match_knowledge(
        match_id, session_id, revision, "test.live-observation",
        "The player faction completed its native opening sequence.",
        category="test", subject="player-faction",
    )
    if not recorded.get("ok"):
        emit("failure", {"stage": "put", "result": recorded})
        return 6
    fetched = read_match_knowledge(match_id, key="test.live-observation")
    if not fetched.get("ok") \
            or fetched.get("entry", {}).get("observed_turn") != snapshot.get("turn") \
            or fetched.get("entry", {}).get("session_id") != session_id:
        emit("failure", {"stage": "get", "result": fetched, "snapshot": snapshot})
        return 7
    emit("passed", {
        "match_id": match_id,
        "session_id": session_id,
        "observed_revision": revision,
        "turn_provenance_verified": True,
        "fixed_ledger_path": recorded.get("ledger_path"),
        "raw_ui_bridge_operation_rejected": True,
        "pixels_or_input_used": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
