#!/usr/bin/env python3
"""Contained regression for the native Alien Artifact decision pipeline."""

from __future__ import annotations

import json
import time
from typing import Any

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def guarded(source: dict[str, Any], command: str, **arguments: object) -> dict[str, Any]:
    return bridge_request(
        "semantic_command", command=command,
        match_id=source["match_id"], session_id=source["session_id"],
        expected_revision=source["revision"], **arguments,
    )


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=0, world_size=0, faction_id=1,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2

    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") == "turn":
            break
        handled, reason = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "opening", "reason": reason, "snapshot": snapshot})
            return 3
        time.sleep(0.05)
    else:
        return 4

    units = bridge_request("list_units", scope="own", limit=300).get("items", [])
    artifact = next((item for item in units if item.get("name") == "Alien Artifact"), None)
    bases = bridge_request("list_bases", limit=100).get("items", [])
    if artifact is None or not bases:
        emit("failure", {"stage": "fixture", "artifact": artifact, "bases": bases})
        return 5
    base = bases[0]
    before_techs = bridge_request("list_technologies").get("items", [])

    actions = bridge_request(
        "semantic_choices", kind="unit_actions", unit_id=int(artifact["id"]),
    )
    move = next(
        (item for item in actions.get("choices", [])
         if item.get("command") == "move_unit"
         and int(item.get("target_tile_id", -1)) == int(base["tile_id"])),
        None,
    )
    if move is None or "x" in move or "y" in move:
        emit("failure", {"stage": "move_choice", "actions": actions, "base": base})
        return 6
    moved = guarded(
        actions, "move_unit", unit_id=int(artifact["id"]),
        target_tile_id=int(move["target_tile_id"]),
    )
    emit("move_to_base", moved)
    if not moved.get("ok") or not moved.get("queued"):
        return 7

    interaction: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if snapshot.get("interaction", {}).get("popup_label") == "ARTIFACT":
            interaction = bridge_request("semantic_choices", kind="interaction")
            break
        time.sleep(0.05)
    artifact_choices = interaction.get("choices", [])
    link = next((item for item in artifact_choices
                 if item.get("response") == "link_technology"), None)
    leave = next((item for item in artifact_choices
                  if item.get("response") == "no_action"), None)
    context = next((item for item in artifact_choices
                    if item.get("id") == "artifact:context"), None)
    if link is None or leave is None or context is None \
            or link.get("confirm_consume_artifact") != 1 \
            or link.get("effect") != "discover_random_available_technology" \
            or any("x" in item or "y" in item for item in artifact_choices):
        emit("failure", {"stage": "artifact_choices", "interaction": interaction})
        return 8

    unconfirmed = guarded(
        interaction, "respond_to_artifact", response="link_technology",
        confirm_consume_artifact=0,
    )
    if unconfirmed.get("error", {}).get("code") != "artifact_consumption_confirmation_required":
        emit("failure", {"stage": "confirmation_guard", "result": unconfirmed})
        return 9
    interaction = bridge_request("semantic_choices", kind="interaction")
    linked = guarded(
        interaction, "respond_to_artifact", response="link_technology",
        confirm_consume_artifact=1,
    )
    emit("artifact_link", linked)
    if not linked.get("ok") or not linked.get("consumes_artifact"):
        return 10

    final_action: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        kind = snapshot.get("interaction", {}).get("kind")
        final_action = bridge_request(
            "action_status", action_id=int(moved["action_id"]),
        ).get("action", {})
        if kind == "turn" and final_action.get("status") == "completed":
            break
        if kind == "popup":
            choices = bridge_request("semantic_choices", kind="interaction")
            acknowledgement = next(
                (item for item in choices.get("choices", [])
                 if item.get("command") == "acknowledge_popup"), None,
            )
            if acknowledgement:
                result = guarded(choices, "acknowledge_popup")
                if not result.get("ok"):
                    emit("failure", {"stage": "notice", "result": result})
                    return 11
        time.sleep(0.05)
    else:
        return 12

    after_units = bridge_request("list_units", scope="own", limit=300).get("items", [])
    after_techs = bridge_request("list_technologies").get("items", [])
    if any(item.get("name") == "Alien Artifact" for item in after_units) \
            or len(after_techs) != len(before_techs) + 1 \
            or final_action.get("status") != "completed":
        emit("failure", {
            "stage": "native_effect", "before_techs": len(before_techs),
            "after_techs": len(after_techs), "units": after_units,
            "action": final_action,
        })
        return 13
    emit("success", {
        "typed_choices": True,
        "consumption_guard": True,
        "artifact_consumed": True,
        "technology_discovered": after_techs[-1].get("name") if after_techs else None,
        "deferred_move_completed": True,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
