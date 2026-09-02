#!/usr/bin/env python3
"""Contract test: concurrent lifecycle callers serialize Docker mutations."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from smacx_worker_manager import WorkerManager


def main() -> int:
    manager = object.__new__(WorkerManager)
    manager._lifecycle_lock = threading.RLock()
    active = 0
    maximum = 0
    gate = threading.Lock()

    def simulated_start(instance_id: str, *, timeout: float = 90.0) -> dict:
        nonlocal active, maximum
        with gate:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.04)
        with gate:
            active -= 1
        return {"instance_id": instance_id, "timeout": timeout}

    manager._start_mcp_sidecar_locked = simulated_start
    results: list[dict] = []
    threads = [
        threading.Thread(
            target=lambda instance_id=instance_id: results.append(
                manager.start_mcp_sidecar(instance_id)
            )
        )
        for instance_id in ("instance-one", "instance-two")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if maximum != 1 or len(results) != 2:
        raise AssertionError({"maximum_concurrency": maximum, "results": results})
    print(json.dumps({"event": "pass", "maximum_concurrency": maximum}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
