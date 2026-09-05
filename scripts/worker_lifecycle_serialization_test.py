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
from smacx_operations import OperationsManager


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
    # A supervisor must not observe the intermediate absent peer of a LAN
    # transition, then queue recovery after that transition has completed.
    started = threading.Event()
    release = threading.Event()
    observed = threading.Event()
    errors = []
    def lan_start(match_id, **kwargs):
        started.set()
        if not release.wait(3):
            errors.append("LAN transition was not released")
        return {"ok": True}
    manager._start_lan_match_locked = lan_start
    operations = object.__new__(OperationsManager)
    operations.worker_manager = manager
    operations._operation_lock = threading.RLock()
    def observe():
        observed.set()
        return {"ok": True}
    operations._reconcile_once = observe
    transition = threading.Thread(target=lambda: manager.start_lan_match("match-lan"))
    transition.start()
    assert started.wait(1)
    attempting = threading.Event()
    def supervise():
        attempting.set()
        operations.reconcile_once()
    supervisor = threading.Thread(target=supervise)
    supervisor.start()
    assert attempting.wait(1)
    try:
        assert not observed.wait(0.1), "supervisor observed a partial LAN transition"
    finally:
        release.set()
        transition.join(3)
        supervisor.join(3)
    assert not errors and observed.is_set() and not supervisor.is_alive(), errors
    print(json.dumps({"event": "pass", "maximum_concurrency": maximum,
                      "lan_transition_and_supervision_serialized": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
