#!/usr/bin/env python3
"""Unit regression for ordered and revision-stable MCP decision frames."""

from __future__ import annotations

import smacx_mcp


def snapshot(phase: str, revision: str = "r1", ready: list[dict] | None = None) -> dict:
    return {
        "ok": True,
        "snapshot": {
            "match_id": "match-test", "session_id": "session-test",
            "revision": revision, "turn": 4, "year": 2104,
            "interaction": {"popup_label": "PLANETFALL" if phase == "interaction" else ""},
            "protocol": {"phase": phase, "required_action": f"required_{phase}"},
            "ready_unit_refs": ready or [],
        },
    }


def main() -> int:
    original = smacx_mcp._call
    calls: list[tuple[str, dict]] = []
    try:
        def turn_call(operation: str, **arguments: object) -> dict:
            calls.append((operation, dict(arguments)))
            if operation == "semantic_snapshot":
                return snapshot("turn", ready=[{"id": 7, "name": "Scout Patrol", "roles": {"combat": True}}])
            return {
                "ok": True, "match_id": "match-test", "session_id": "session-test",
                "revision": "r1", "choices": [{"command": "skip_unit", "unit_id": 7}],
            }

        smacx_mcp._call = turn_call
        frame = smacx_mcp.smac_decision()
        if not frame.get("ok") or frame.get("focus", {}).get("unit", {}).get("id") != 7 \
                or frame.get("required_next", {}).get("guard", {}).get("expected_revision") != "r1" \
                or "snapshot" in frame \
                or calls[-1] != ("semantic_choices", {
                    "kind": "unit_actions", "unit_id": 7,
                    "target_tile_id": -1, "target_unit_id": -1,
                }):
            raise AssertionError(f"bad ready-unit frame: {frame}, calls={calls}")

        full_frame = smacx_mcp.smac_decision(detail="full")
        if full_frame.get("snapshot", {}).get("revision") != "r1":
            raise AssertionError(f"full detail omitted snapshot: {full_frame}")

        calls.clear()
        finish_frame = smacx_mcp.smac_decision(finish_ready_units=True)
        if finish_frame.get("focus", {}).get("purpose") != "finish_ready_units" \
                or finish_frame.get("focus", {}).get("ready_unit_count") != 1 \
                or calls[-1] != ("semantic_choices", {"kind": "game_management"}):
            raise AssertionError(f"bad finish-ready frame: {finish_frame}, calls={calls}")
        conflict = smacx_mcp.smac_decision(unit_id=7, finish_ready_units=True)
        if conflict.get("error", {}).get("code") != "conflicting_decision_focus":
            raise AssertionError(f"finish/unit focus conflict was not rejected: {conflict}")

        compact_moves = smacx_mcp._compact_decision_choices([{
            "id": "move:12", "command": "move_unit", "unit_id": 7,
            "target_tile_id": 12, "direction_id": 3, "known": True,
            "visible_now": True, "is_ocean": False, "features": [],
            "may_initiate_combat_or_contact": False, "boards_transport": False,
        }, {
            "id": "move:13", "command": "move_unit", "unit_id": 7,
            "target_tile_id": 13, "direction_id": 4, "known": True,
            "visible_now": True, "is_ocean": False, "features": ["vehicle"],
            "may_initiate_combat_or_contact": True, "boards_transport": False,
        }, {
            "id": "move:14", "command": "move_unit", "unit_id": 7,
            "target_tile_id": 14, "direction_id": 5, "known": False,
            "visible_now": False, "is_ocean": True, "features": [],
            "may_initiate_combat_or_contact": False, "boards_transport": False,
        }])
        if set(compact_moves[0]) != {"id", "command", "unit_id", "target_tile_id"} \
                or compact_moves[1].get("may_initiate_combat_or_contact") is not True \
                or compact_moves[1].get("features") != ["vehicle"] \
                or compact_moves[2].get("known") is not False \
                or compact_moves[2].get("visible_now") is not False \
                or compact_moves[2].get("is_ocean") is not True:
            raise AssertionError(f"unsafe move compaction: {compact_moves}")

        calls.clear()
        smacx_mcp._call = lambda operation, **arguments: (
            snapshot("interaction") if operation == "semantic_snapshot" else {
                "ok": True, "match_id": "match-test", "session_id": "session-test",
                "revision": "r1", "choices": [{"command": "acknowledge_popup"}],
            }
        )
        frame = smacx_mcp.smac_decision()
        if frame.get("focus", {}).get("kind") != "interaction" \
                or frame.get("choices", [{}])[0].get("command") != "acknowledge_popup":
            raise AssertionError(f"bad interaction frame: {frame}")

        smacx_mcp._call = lambda operation, **arguments: snapshot("wait")
        waiting = smacx_mcp.smac_decision()
        if waiting.get("required_next", {}).get("tool") != "smac_wait" \
                or waiting.get("choices") != []:
            raise AssertionError(f"bad wait frame: {waiting}")

        smacx_mcp._call = lambda operation, **arguments: snapshot("capability_gap")
        gap = smacx_mcp.smac_decision()
        if gap.get("required_next", {}).get("tool") != "smac_report_capability_gap" \
                or gap.get("required_next", {}).get("stop_after") is not True:
            raise AssertionError(f"bad gap frame: {gap}")

        unstable_calls = 0
        def unstable(operation: str, **arguments: object) -> dict:
            nonlocal unstable_calls
            if operation == "semantic_snapshot":
                unstable_calls += 1
                return snapshot("turn", revision=f"r{unstable_calls}")
            return {
                "ok": True, "match_id": "match-test", "session_id": "session-test",
                "revision": "different", "choices": [],
            }
        smacx_mcp._call = unstable
        unstable_frame = smacx_mcp.smac_decision()
        if unstable_frame.get("error", {}).get("code") != "decision_frame_unstable" \
                or unstable_calls != 3:
            raise AssertionError(f"bad unstable guard: {unstable_frame}")
    finally:
        smacx_mcp._call = original
    print("decision frame tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
