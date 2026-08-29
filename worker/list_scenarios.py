#!/usr/bin/env python3
"""List safe scenario identifiers from the operator's legal game copy."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re

from entrypoint import validate_source


SOURCE_ROOT = Path(os.environ.get("SMACX_GAME_SOURCE", "/game-source"))
SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.'()-]{0,95}$")


def main() -> int:
    identity = validate_source(SOURCE_ROOT)
    root = SOURCE_ROOT / "scenarios"
    scenarios: list[dict] = []
    if root.is_dir() and not root.is_symlink():
        for path in sorted(root.rglob("*.SC"), key=lambda item: item.as_posix().casefold()):
            relative = path.relative_to(root)
            if path.is_symlink() or not path.is_file() or len(relative.parts) > 6:
                continue
            if not all(SAFE_PART.fullmatch(part) for part in relative.parts):
                continue
            size = path.stat().st_size
            if not 1024 <= size <= 16 * 1024 * 1024:
                continue
            scenarios.append({
                "scenario_id": relative.as_posix(),
                "display_name": path.stem,
                "relative_path": (Path("scenarios") / relative).as_posix(),
                "size_bytes": size,
            })
            if len(scenarios) > 256:
                raise RuntimeError("scenario_limit_exceeded")
    print(json.dumps({
        "ok": True, "schema": "smacx.scenario-catalog.v1",
        "terranx_sha256": identity["terranx_sha256"], "scenarios": scenarios,
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
