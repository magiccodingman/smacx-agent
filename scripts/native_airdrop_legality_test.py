#!/usr/bin/env python3
"""Contained production-native airdrop diplomacy and anti-drop contract."""

from __future__ import annotations

import json
import sys

from smacx_controller import bridge_request, new_game


def main() -> int:
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    if not started.get("ok"):
        print(json.dumps({"event": "failure", "payload": started}, separators=(",", ":")))
        return 2
    result = bridge_request("test_airdrop_legality_fixture")
    expected = {
        "hostile_combat": True,
        "hostile_noncombat": False,
        "pact_combat": True,
        "pact_noncombat": True,
        "treaty_combat": False,
        "treaty_noncombat": False,
        "unknown_combat": False,
        "unknown_noncombat": False,
        "aerospace_defended": True,
        "air_superiority_defended": True,
    }
    if not result.get("ok") or any(result.get(key) is not value
                                    for key, value in expected.items()):
        print(json.dumps({"event": "failure", "payload": {
            "expected": expected, "actual": result,
        }}, separators=(",", ":")))
        return 3
    print(json.dumps({"event": "pass", "payload": {
        "native_allow_airdrop_diplomacy_matrix": True,
        "native_aerospace_complex_suppression": True,
        "native_air_superiority_suppression": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
