#!/usr/bin/env python3
"""Read-only helper used by the Control Center to validate a host game path."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from entrypoint import validate_source


def main() -> int:
    try:
        result = validate_source(Path("/game-source"))
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }, separators=(",", ":")))
        return 1
    print(json.dumps({"ok": True, "source": result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
