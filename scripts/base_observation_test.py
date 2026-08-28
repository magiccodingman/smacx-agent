#!/usr/bin/env python3
"""Contained regression for semantic owned-base state."""

from __future__ import annotations

import json
import sys
import time

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def main() -> int:
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 70
    required = {
        "citizens", "nutrients", "minerals", "energy", "eco_damage",
        "production_id", "production_name", "production_queue", "governor", "drone_riots",
    }
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
        response = bridge_request("list_bases", limit=10)
        items = response.get("items", [])
        if not items:
            time.sleep(0.1)
            continue
        base = items[0]
        missing = sorted(required.difference(base))
        valid_queue = bool(base["production_queue"]) and base["production_queue"][0]["item_id"] == base["production_id"]
        valid_citizens = isinstance(base["citizens"].get("specialists"), list)
        valid_governor = all(isinstance(base["governor"].get(key), bool)
                             for key in ("active", "manage_citizens", "manage_production"))
        emit("base", base)
        if missing or not valid_queue or not valid_citizens or not valid_governor:
            emit("failure", {
                "missing": missing,
                "valid_queue": valid_queue,
                "valid_citizens": valid_citizens,
                "valid_governor": valid_governor,
            })
            return 4
        emit("pass", {"base_id": base["id"], "fields": sorted(required)})
        return 0
    return 5


if __name__ == "__main__":
    sys.exit(main())
