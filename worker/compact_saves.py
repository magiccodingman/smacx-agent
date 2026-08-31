#!/usr/bin/env python3
"""Bound and zstd-compress one stopped worker's native save state."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


IDENTITY = re.compile(r"^[A-Za-z0-9_-]{8,96}$")


def required_identity(name: str) -> str:
    value = os.environ.get(name, "")
    if not IDENTITY.fullmatch(value):
        raise RuntimeError(f"invalid_{name.lower()}")
    return value


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def compress(source: Path) -> Path:
    if source.name.endswith(".zst"):
        return source
    target = source.with_name(source.name + ".zst")
    completed = subprocess.run(
        ["zstd", "-q", "-9", "-f", str(source), "-o", str(target)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"zstd_failed:{source.name}")
    source.unlink()
    return target


def logical_name(path: Path) -> str:
    value = path.as_posix()
    return value[:-4] if value.endswith(".zst") else value


def main() -> int:
    match_id = required_identity("SMACX_MATCH_ID")
    instance_id = required_identity("SMACX_INSTANCE_ID")
    state = Path("/state")
    saves = state / "game" / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    recent = min(max(int(os.environ.get("SMACX_RECENT_SAVES", "10")), 1), 250)
    milestone = min(max(int(os.environ.get("SMACX_MILESTONE_INTERVAL", "25")), 0), 10_000)
    full = os.environ.get("SMACX_RETAIN_FULL_HISTORY") == "1"
    completed_match = os.environ.get("SMACX_COMPLETED_MATCH") == "1"
    archive_root: Path | None = None
    if completed_match:
        # Validate the durable destination before pruning or transforming the
        # worker volume. A permission/configuration error must leave the
        # source state untouched and immediately recoverable.
        archive_root = Path("/control/campaigns") / match_id / "final"
        archive_root.mkdir(parents=True, exist_ok=True)
        probe = archive_root / f".{instance_id}.write-test"
        probe.write_bytes(b"")
        probe.unlink()

    files = [item for item in saves.rglob("*.sav") if item.is_file()]
    files.extend(item for item in saves.rglob("*.sav.zst") if item.is_file())
    # A hydrated and compressed representation of the same logical save must
    # count once if an interrupted maintenance operation left both behind.
    unique: dict[str, Path] = {}
    for item in files:
        relative = item.relative_to(saves)
        key = logical_name(relative)
        current = unique.get(key)
        if current is None or item.stat().st_mtime >= current.stat().st_mtime:
            unique[key] = item
    ordered = sorted(unique.values(), key=lambda item: (item.stat().st_mtime, item.as_posix()))
    protected = {
        item for item in ordered
        if item.name in {"control_recovery.sav", "control_recovery.sav.zst",
                         "final.sav", "final.sav.zst"}
    }
    keep = set(ordered if full else ordered[-recent:]) | protected
    if not full and milestone:
        keep.update(item for index, item in enumerate(ordered, 1) if index % milestone == 0)
    final_logical = logical_name(max(
        keep, key=lambda item: item.stat().st_mtime,
    ).relative_to(state)) if completed_match and keep else None

    removed = 0
    compressed: list[dict[str, object]] = []
    archived_by_logical: dict[str, Path] = {}
    for item in ordered:
        if item not in keep:
            item.unlink(missing_ok=True)
            removed += 1
            continue
        original_bytes = item.stat().st_size
        original_sha = digest(item) if not item.name.endswith(".zst") else None
        archived = compress(item)
        archived_by_logical[logical_name(archived.relative_to(state))] = archived
        compressed.append({
            "path": archived.relative_to(state).as_posix(),
            "logical_path": logical_name(archived.relative_to(state)),
            "archive_sha256": digest(archived),
            "original_sha256": original_sha,
            "original_bytes": original_bytes,
            "archive_bytes": archived.stat().st_size,
        })

    final: dict[str, object] | None = None
    if completed_match and final_logical is not None:
        candidate = archived_by_logical[final_logical]
        assert archive_root is not None
        target = archive_root / f"{instance_id}.sav.zst"
        shutil.copy2(candidate, target)
        final = {
            "path": target.relative_to("/control").as_posix(),
            "sha256": digest(target), "bytes": target.stat().st_size,
        }
        (archive_root / f"{instance_id}.json").write_text(
            json.dumps({
                "schema": "smacx.final-save.v1", "match_id": match_id,
                "instance_id": instance_id, "save": final,
                "created_unix": time.time(),
            }, indent=2) + "\n", encoding="utf-8",
        )

    manifest = {
        "schema": "smacx.worker-save-archive.v1", "match_id": match_id,
        "instance_id": instance_id, "compression": "zstd-9",
        "recent_limit": recent, "milestone_interval": milestone,
        "retain_full_turn_history": full, "removed": removed,
        "saves": compressed, "final": final, "created_unix": time.time(),
    }
    (state / "save-archive.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({"ok": True, **manifest}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
