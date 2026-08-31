#!/usr/bin/env python3
"""Exercise save pruning, zstd storage, and final preservation in Docker."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-save-retention-") as temporary:
        root = Path(temporary)
        state = root / "state"
        control = root / "control"
        saves = state / "game" / "saves"
        saves.mkdir(parents=True)
        control.mkdir()
        # The worker image runs as uid 10001. These are disposable test-only
        # bind directories containing no credentials or user data.
        os.chmod(state, 0o777)
        os.chmod(saves, 0o777)
        os.chmod(control, 0o777)
        baseline = time.time() - 1000
        expected_final = b"checkpoint-40\n" * 256
        for number in range(1, 41):
            path = saves / f"checkpoint-{number:04d}.sav"
            path.write_bytes((f"checkpoint-{number}\n".encode()) * 256)
            os.utime(path, (baseline + number, baseline + number))
        recovery = saves / "control_recovery.sav"
        recovery.write_bytes(b"verified-recovery\n" * 256)
        os.utime(recovery, (baseline + 20.5, baseline + 20.5))

        completed = subprocess.run([
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--tmpfs", "/tmp:size=64m,mode=1777",
            "-e", "SMACX_MATCH_ID=match-retention-live",
            "-e", "SMACX_INSTANCE_ID=instance-retention-live",
            "-e", "SMACX_RECENT_SAVES=10",
            "-e", "SMACX_MILESTONE_INTERVAL=25",
            "-e", "SMACX_RETAIN_FULL_HISTORY=0",
            "-e", "SMACX_COMPLETED_MATCH=1",
            "--mount", f"type=bind,src={state},dst=/state",
            "--mount", f"type=bind,src={control},dst=/control",
            "--entrypoint", "python3", "smacx-agent-worker:dev",
            "/opt/smacx/compact_saves.py",
        ], check=True, capture_output=True, text=True)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        archives = sorted(saves.glob("*.sav.zst"))
        raw = sorted(saves.glob("*.sav"))
        final = control / "campaigns" / "match-retention-live" / "final" \
            / "instance-retention-live.sav.zst"
        metadata = final.with_suffix("").with_suffix(".json")
        restored = subprocess.run(
            ["zstd", "-q", "-d", "-c", str(final)], check=True,
            capture_output=True,
        ).stdout
        # Return container-created files to the invoking uid so Python's
        # TemporaryDirectory can remove this isolated fixture on every pass.
        subprocess.run([
            "docker", "run", "--rm", "--network", "none", "--user", "0:0",
            "--mount", f"type=bind,src={root},dst=/cleanup",
            "--entrypoint", "chown", "smacx-agent-worker:dev",
            "-R", f"{os.getuid()}:{os.getgid()}", "/cleanup",
        ], check=True, capture_output=True)
        if result.get("ok") is not True or len(archives) != 12 or raw \
                or not final.is_file() or not metadata.is_file() \
                or restored != expected_final:
            raise AssertionError({
                "result": result, "archives": len(archives),
                "raw": [item.name for item in raw], "final": final.is_file(),
                "metadata": metadata.is_file(), "latest_preserved": restored == expected_final,
            })
        print(json.dumps({
            "event": "pass", "payload": {
                "recent_retained": 10, "milestone_retained": 1,
                "verified_recovery_retained": True, "raw_saves": 0,
                "zstd_archives": len(archives), "one_final_save": True,
                "latest_checkpoint_is_final": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
