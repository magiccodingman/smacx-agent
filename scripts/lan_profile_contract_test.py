#!/usr/bin/env python3
"""Keep guarded lobby profiles identical across native, manager, and UI layers."""

from __future__ import annotations

import json
from pathlib import Path
import re

from smacx_game_settings import LAN_RULE_FIELDS, normalize_lan_game_settings
from smacx_store import InvalidRecord
from smacx_worker_manager import LAN_PROFILES


EXPECTED = {
    "tiny_citizen": (0, 0),
    "small_easy": (0, 1),
    "standard_librarian": (3, 2),
    "large_thinker": (4, 3),
    "huge_transcend": (5, 4),
}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    native = (root / "bridge/src/agent_bridge.cpp").read_text(encoding="utf-8")
    ui = (root / "control_center/static/index.html").read_text(encoding="utf-8")
    native_profiles = {
        name: (int(difficulty), int(size))
        for name, difficulty, size in re.findall(
            r'\{"([a-z_]+)",\s*([0-6]),\s*(-?[0-4])\}', native
        ) if name in EXPECTED
    }
    ui_profiles = set(re.findall(r'<option value="([a-z_]+)"', ui)) & set(EXPECTED)
    if native_profiles != EXPECTED or LAN_PROFILES != set(EXPECTED) \
            or ui_profiles != set(EXPECTED):
        raise AssertionError({
            "native": native_profiles, "manager": sorted(LAN_PROFILES),
            "ui": sorted(ui_profiles),
        })
    custom = normalize_lan_game_settings({
        "difficulty": 5, "time_control": 4, "world_size": 4,
        "ocean_coverage": 2, "erosive_forces": 0,
        "native_life": 2, "cloud_cover": 0,
        **{field: index % 2 == 0 for index, field in enumerate(LAN_RULE_FIELDS)},
    })
    if set(custom) != {
            "difficulty", "time_control", "world_size", "ocean_coverage",
            "erosive_forces", "native_life", "cloud_cover", *LAN_RULE_FIELDS,
    }:
        raise AssertionError("typed LAN setting family was not preserved")
    for invalid in (
        {"difficulty": 6, "time_control": 0, "world_size": 0,
         "ocean_coverage": 0, "erosive_forces": 0, "native_life": 0,
         "cloud_cover": 0},
        {"difficulty": 0, "time_control": 5, "world_size": 0,
         "ocean_coverage": 0, "erosive_forces": 0, "native_life": 0,
         "cloud_cover": 0},
        {"difficulty": 0, "time_control": 0, "world_size": 0,
         "ocean_coverage": 0, "erosive_forces": 0, "native_life": 0,
         "cloud_cover": 0, "random_leader_agendas": True},
    ):
        try:
            normalize_lan_game_settings(invalid)
        except InvalidRecord:
            pass
        else:
            raise AssertionError(f"invalid custom LAN settings accepted: {invalid}")
    print(json.dumps({"event": "pass", "payload": {
        "profiles": EXPECTED,
        "native_manager_ui_consistent": True,
        "typed_custom_settings": True,
        "arbitrary_settings_rejected": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
