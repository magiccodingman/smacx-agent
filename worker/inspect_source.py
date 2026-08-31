#!/usr/bin/env python3
"""Read-only helper used by the Control Center to validate a host game path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from entrypoint import validate_source


def source_tree_sha256(root: Path) -> str:
    """Fingerprint the supplied installation without personal save data."""
    digest = hashlib.sha256()
    files = sorted(
        (item for item in root.rglob("*") if item.is_file() and
         not any(part.casefold() in {"save", "saves"} for part in item.relative_to(root).parts)),
        key=lambda item: (
            item.relative_to(root).as_posix().casefold(),
            item.relative_to(root).as_posix(),
        ),
    )
    for item in files:
        relative = item.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(item.stat().st_size.to_bytes(8, "big"))
        with item.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def main() -> int:
    try:
        root = Path("/game-source")
        result = validate_source(root)
        result["source_tree_sha256"] = source_tree_sha256(root)
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
