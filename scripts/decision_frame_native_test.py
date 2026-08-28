#!/usr/bin/env python3
"""Live size/equivalence regression for compact MCP decision frames."""

from __future__ import annotations

import json
import time

import smacx_mcp
from smacx_controller import bridge_request, new_game
from semantic_playthrough import handle_interaction


def choice_signature(frame: dict) -> list[tuple]:
    keys = (
        "id", "kind", "command", "unit_id", "base_id", "target_tile_id",
        "target_unit_id", "response", "option", "priority", "tech_id",
    )
    return [tuple(choice.get(key) for key in keys) for choice in frame.get("choices", [])]


def main() -> int:
    started = new_game(
        wait_seconds=60, difficulty=0, world_size=0, faction_id=1,
        blind_research=True, initial_research_priority=1,
        narrative_ui=False, tutorial_ui=False,
    )
    if not started.get("ok"):
        raise AssertionError(f"new game failed: {started}")

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot", timeout=5).get("snapshot", {})
        if snapshot.get("interaction", {}).get("kind") == "turn":
            break
        handled, reason = handle_interaction(snapshot)
        if not handled:
            raise AssertionError(f"opening interaction failed: {reason}: {snapshot}")
        time.sleep(0.05)
    else:
        raise AssertionError("turn did not become actionable")

    compact = smacx_mcp.smac_decision()
    full = smacx_mcp.smac_decision(detail="full")
    finish = smacx_mcp.smac_decision(finish_ready_units=True)
    compact_json = json.dumps(compact, separators=(",", ":"))
    full_json = json.dumps(full, separators=(",", ":"))
    if not compact.get("ok") or not full.get("ok"):
        raise AssertionError(f"decision failed: compact={compact}, full={full}")
    if compact.get("identity") != full.get("identity") \
            or compact.get("required_next") != full.get("required_next") \
            or compact.get("focus") != full.get("focus") \
            or choice_signature(compact) != choice_signature(full):
        raise AssertionError("compact/full decision semantics differ")
    if "snapshot" in compact or "state" not in compact or "snapshot" not in full:
        raise AssertionError("detail contract is malformed")
    if len(compact_json) >= len(full_json) * 0.75:
        raise AssertionError(
            f"compact frame is not materially smaller: {len(compact_json)} vs {len(full_json)}",
        )
    skip_all = next(
        (choice for choice in finish.get("choices", [])
         if choice.get("command") == "skip_all_ready_units"), None,
    )
    if not finish.get("ok") or finish.get("identity") != compact.get("identity") \
            or finish.get("focus", {}).get("purpose") != "finish_ready_units" \
            or not skip_all \
            or int(skip_all.get("ready_unit_count", -1)) \
            != int(finish.get("focus", {}).get("ready_unit_count", -2)):
        raise AssertionError(f"finish-ready decision frame is malformed: {finish}")

    print(json.dumps({
        "event": "pass",
        "compact_bytes": len(compact_json),
        "full_bytes": len(full_json),
        "reduction_percent": round((1 - len(compact_json) / len(full_json)) * 100, 1),
        "choices": len(compact.get("choices", [])),
        "identity_and_guard_equal": True,
        "choice_signatures_equal": True,
        "finish_ready_skip_all_exposed": True,
        "pixels_or_input_used": False,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
