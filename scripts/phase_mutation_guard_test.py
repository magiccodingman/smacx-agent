#!/usr/bin/env python3
"""Prove fabricated turn mutations cannot cross an active native interaction."""

from __future__ import annotations

import json

from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")),
          flush=True)


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=0, world_size=0, faction_id=1,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    snapshot = started.get("snapshot", {}).get("snapshot", {})
    refs = snapshot.get("ready_unit_refs", [])
    if snapshot.get("interaction", {}).get("popup_label") != "PLANETFALL" or not refs:
        emit("failure", {"stage": "opening_fixture", "snapshot": snapshot})
        return 3
    before = bridge_request("list_units", scope="own", limit=200)
    unit = next(item for item in before.get("items", [])
                if item.get("own_unit_ref") == refs[0].get("own_unit_ref"))
    unit_id = int(unit["id"])
    fabricated = bridge_request(
        "semantic_command", command="disband_unit", unit_id=unit_id,
        confirm_disband=1, match_id=snapshot["match_id"],
        session_id=snapshot["session_id"],
        expected_revision=snapshot["revision"],
    )
    after = bridge_request("list_units", scope="own", limit=200)
    final_snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
    before_ids = {int(item["id"]) for item in before.get("items", [])}
    after_ids = {int(item["id"]) for item in after.get("items", [])}
    if fabricated.get("ok") \
            or fabricated.get("error", {}).get("code") != "not_actionable" \
            or unit_id not in before_ids or unit_id not in after_ids \
            or len(before_ids) != len(after_ids) \
            or final_snapshot.get("interaction", {}).get("popup_label") != "PLANETFALL" \
            or final_snapshot.get("revision") != snapshot.get("revision"):
        emit("failure", {
            "stage": "phase_guard", "result": fabricated,
            "before": before, "after": after, "snapshot": final_snapshot,
        })
        return 4
    emit("pass", {
        "fabricated_command": "disband_unit",
        "active_interaction": "PLANETFALL",
        "rejection": "not_actionable",
        "unit_preserved": True,
        "popup_preserved": True,
        "revision_preserved": True,
        "pixels_or_ui_input_used": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
