#!/usr/bin/env python3
"""Contained native-effect regression for semantic unit ownership transfer."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from smacx_controller import BridgeUnavailable, bridge_request, new_game
from semantic_playthrough import handle_interaction


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(source: dict[str, Any], **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command", timeout=10,
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], **arguments,
    )


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=1, world_size=0, faction_id=4,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 90
    fixture_initialized = False
    while time.monotonic() < deadline:
        try:
            snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        except BridgeUnavailable:
            time.sleep(0.1)
            continue
        if not snapshot:
            continue
        interaction = snapshot.get("interaction", {})
        if interaction.get("kind") not in {"turn", "waiting_for_turn", "waiting_for_engine"}:
            handled, result = handle_interaction(snapshot)
            emit("interaction", result)
            if not handled:
                return 3
            continue
        if interaction.get("kind") in {"waiting_for_turn", "waiting_for_engine"}:
            time.sleep(0.05)
            continue
        if interaction.get("kind") != "turn":
            time.sleep(0.05)
            continue
        if not fixture_initialized:
            fixture = bridge_request("semantic_choices", kind="diplomacy")
            emit("fixture", fixture)
            if not fixture.get("ok"):
                return 4
            fixture_initialized = True

        units = bridge_request("list_units", scope="own", limit=100).get("items", [])
        for unit in units:
            choices = bridge_request(
                "semantic_choices", kind="unit_actions", unit_id=int(unit["id"]),
            )
            transfer = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "give_unit"), None,
            )
            if transfer is None:
                continue
            unit_id = int(transfer["unit_id"])
            target = int(transfer["faction_id"])
            refused = command(
                choices, command="give_unit", unit_id=unit_id, faction_id=target,
            )
            emit("confirmation_guard", refused)
            if refused.get("error", {}).get("code") != "unit_transfer_confirmation_required":
                return 5
            result = command(
                choices, command="give_unit", unit_id=unit_id, faction_id=target,
                confirm_transfer=1,
            )
            emit("transfer", result)
            if not result.get("ok") or not result.get("native_owner_change_verified"):
                return 6
            visible = bridge_request("list_units", scope="visible", limit=100).get("items", [])
            observed = next((item for item in visible if int(item["id"]) == unit_id), None)
            own_after = bridge_request("list_units", scope="own", limit=100).get("items", [])
            if (observed is not None and int(observed.get("owner", -1)) != target) \
                    or any(int(item["id"]) == unit_id for item in own_after):
                emit("failure", {"stage": "ownership", "observed": observed,
                                 "own_after": own_after})
                return 7
            emit("pass", {
                "unit_id": unit_id,
                "to_faction_id": target,
                "native_owner_change": True,
                "confirmation_guard": True,
                "pixels_or_ui_input_used": False,
            })
            return 0
        time.sleep(0.05)

    emit("failure", {"stage": "deadline", "fixture_initialized": fixture_initialized})
    return 8


if __name__ == "__main__":
    sys.exit(main())
