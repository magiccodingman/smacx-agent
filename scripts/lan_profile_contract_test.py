#!/usr/bin/env python3
"""Keep guarded lobby profiles identical across native, manager, and UI layers."""

from __future__ import annotations

import json
from pathlib import Path
import re

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
    print(json.dumps({"event": "pass", "payload": {
        "profiles": EXPECTED,
        "native_manager_ui_consistent": True,
        "arbitrary_settings_rejected": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
