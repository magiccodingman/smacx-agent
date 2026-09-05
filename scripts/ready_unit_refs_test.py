#!/usr/bin/env python3
"""Contained regression for snapshot-native ready-unit references."""

from __future__ import annotations

import json
import time
from typing import Any

from smacx_controller import bridge_request, new_game
from semantic_playthrough import handle_interaction


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

    deadline = time.monotonic() + 45
    snapshot: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        interaction = snapshot.get("interaction", {})
        if interaction.get("kind") == "turn":
            break
        handled, reason = handle_interaction(snapshot)
        if not handled:
            emit("failure", {"stage": "opening", "reason": reason,
                             "interaction": interaction})
            return 3
        time.sleep(0.05)
    if snapshot.get("interaction", {}).get("kind") != "turn":
        emit("failure", {"stage": "turn_not_reached", "snapshot": snapshot})
        return 4

    refs = snapshot.get("ready_unit_refs")
    ready_count = int(snapshot.get("faction", {}).get("ready_units", -1))
    if not isinstance(refs, list) or not refs or len(refs) != ready_count:
        emit("failure", {"stage": "reference_count", "refs": refs,
                         "ready_count": ready_count})
        return 5
    semantic_refs = [str(item.get("own_unit_ref") or "") for item in refs]
    if len(semantic_refs) != len(set(semantic_refs)) or any(
            not value.startswith("own-unit-") for value in semantic_refs):
        emit("failure", {"stage": "reference_ids", "refs": refs})
        return 6

    units = bridge_request("list_units", scope="own")
    unit_by_ref = {str(item["own_unit_ref"]): item for item in units.get("items", [])}
    for ref in refs:
        unit = unit_by_ref.get(str(ref["own_unit_ref"]))
        if not unit or not unit.get("ready") or unit.get("name") != ref.get("name") \
                or f"location-{unit.get('tile_id')}" != ref.get("location_ref"):
            emit("failure", {"stage": "reference_truth", "ref": ref, "unit": unit})
            return 7

    chosen = refs[0]
    native_unit = unit_by_ref[str(chosen["own_unit_ref"])]
    native_id = int(native_unit["id"])
    choices = bridge_request("semantic_choices", kind="unit_actions", unit_id=native_id)
    skip = next((item for item in choices.get("choices", [])
                 if item.get("command") == "skip_unit"), None)
    if choices.get("revision") != snapshot.get("revision") or not skip:
        emit("failure", {"stage": "fresh_choice", "snapshot_revision": snapshot.get("revision"),
                         "choices": choices})
        return 8
    result = bridge_request(
        "semantic_command", command="skip_unit",
        match_id=choices["match_id"], session_id=choices["session_id"],
        expected_revision=choices["revision"], unit_id=native_id,
    )
    if not result.get("ok"):
        emit("failure", {"stage": "guarded_skip", "result": result})
        return 9

    deadline = time.monotonic() + 5
    after: dict[str, Any] = {}
    while time.monotonic() < deadline:
        after = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if after.get("revision") != snapshot.get("revision"):
            break
        time.sleep(0.05)
    after_refs = after.get("ready_unit_refs", [])
    if len(after_refs) != ready_count - 1 \
            or any(item.get("own_unit_ref") == chosen["own_unit_ref"] for item in after_refs):
        emit("failure", {"stage": "post_mutation_refs", "before": refs, "after": after_refs})
        return 10

    emit("passed", {
        "ready_count": ready_count,
        "refs_match_owned_ready_units": True,
        "snapshot_revision_matches_choices": True,
        "guarded_action_resolved_semantic_ref": chosen["own_unit_ref"],
        "refs_refresh_after_mutation": True,
        "pixels_or_input_used": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
