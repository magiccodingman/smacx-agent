#!/usr/bin/env python3
"""Contained regression for match-scoped semantic save/load lifecycle."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

from semantic_playthrough import handle_interaction
from smacx_controller import (
    GAME,
    bridge_request,
    list_saved_games,
    load_saved_game,
    new_game,
    stop_game,
)


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def main() -> int:
    slot = f"regression_{os.getpid()}"
    save_path: Path | None = None
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    match_id = started["identity"]["match_id"]
    original_session = started["identity"]["session_id"]
    save_path = GAME / "saves" / "agent" / match_id / f"{slot}.sav"
    try:
        deadline = time.monotonic() + 70
        saved_turn = -1
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
            choices = bridge_request("semantic_choices", kind="game_management")
            save_choice = next(
                (item for item in choices.get("choices", []) if item.get("command") == "save_game"),
                None,
            )
            if not save_choice:
                emit("failure", {"stage": "save_choice", "choices": choices})
                return 4
            result = bridge_request(
                "semantic_command",
                command="save_game",
                match_id=choices["match_id"],
                session_id=choices["session_id"],
                expected_revision=choices["revision"],
                slot=slot,
            )
            if not result.get("ok") or not save_path.is_file() or save_path.stat().st_size < 1024:
                emit("failure", {"stage": "save", "result": result})
                return 5
            saved_turn = int(snapshot["turn"])
            emit("saved", {"result": result, "turn": saved_turn, "bytes": save_path.stat().st_size})
            break
        if saved_turn < 0:
            return 6

        listed = list_saved_games(match_id)
        if slot not in {item["slot"] for item in listed.get("saves", [])}:
            emit("failure", {"stage": "list", "result": listed})
            return 7
        stopped = stop_game()
        if not stopped.get("ok"):
            emit("failure", {"stage": "stop", "result": stopped})
            return 8
        loaded = load_saved_game(match_id, slot, wait_seconds=70)
        emit("loaded", loaded)
        if not loaded.get("ok"):
            return 9
        identity = loaded.get("identity", {})
        loaded_snapshot = loaded.get("snapshot", {}).get("snapshot", {})
        if (identity.get("match_id") != match_id
        or identity.get("session_id") == original_session
        or int(loaded_snapshot.get("turn", -999)) != saved_turn):
            emit("failure", {
                "stage": "identity_or_turn",
                "saved_turn": saved_turn,
                "original_session": original_session,
                "loaded": loaded,
            })
            return 10
        manifest_path = Path(loaded["knowledge_directory"]) / "match.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sessions = manifest.get("sessions", [])
        if (len(sessions) < 2 or sessions[-2].get("status") != "stopped"
        or sessions[-1].get("session_id") != identity.get("session_id")
        or sessions[-1].get("status") != "running"
        or not sessions[-1].get("loaded_save", "").endswith(f"{slot}.sav")):
            emit("failure", {"stage": "session_ledger", "manifest": manifest})
            return 11
        stopped_loaded = stop_game()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not stopped_loaded.get("ok") or manifest["sessions"][-1].get("status") != "stopped":
            emit("failure", {
                "stage": "loaded_session_stop_ledger",
                "stop": stopped_loaded,
                "manifest": manifest,
            })
            return 12
        emit("pass", {
            "match_id_preserved": True,
            "new_session_id": True,
            "turn_restored": saved_turn,
            "session_ledger": True,
        })
        return 0
    finally:
        if save_path and save_path.is_file():
            save_path.unlink()


if __name__ == "__main__":
    sys.exit(main())
