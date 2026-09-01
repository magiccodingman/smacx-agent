#!/usr/bin/env python3
"""Contract for clean autonomous yields, error restarts, and stall protection."""

from __future__ import annotations

import json
import time

from smacx_harness_manager import HarnessManager


def marker(revision: str, turn: int) -> dict:
    return {
        "available": True, "match_id": "match-continuation",
        "session_id": "session-continuation", "revision": revision,
        "turn": turn, "year": 2100 + turn, "phase": "turn",
        "game_completed": False, "final_score_completed": False,
    }


class FakeControl:
    def __init__(self) -> None:
        self.run = {
            "run_id": "run-continuation", "match_id": "match-continuation",
            "instance_id": "instance-continuation", "harness_profile_id": "harness-test",
            "agent_id": "agent-test", "desired_status": "running", "status": "running",
            "container_name": "harness-container", "restart_count": 0,
            "updated_unix": time.time() - 10,
            "restart_policy": {
                "restart_on_clean_exit": True, "restart_on_error": True,
                "restart_limit": 5, "max_clean_yields_without_progress": 3,
            },
            "metadata": {"attempt_started_progress": marker("r1", 1)},
        }
        self.incidents: list[dict] = []

    def list_harness_runs(self) -> list[dict]:
        return [dict(self.run)]

    def get_match(self, _match_id: str) -> dict:
        return {"status": "running"}

    def update_harness_run(self, _run_id: str, **values) -> dict:
        metadata = values.pop("metadata_update", None)
        if metadata:
            self.run["metadata"] = {**self.run.get("metadata", {}), **metadata}
        if values.pop("increment_restart", False):
            self.run["restart_count"] += 1
        self.run.update(values)
        self.run["updated_unix"] = time.time() - 10
        return dict(self.run)

    def record_supervision_incident(self, _instance_id: str, kind: str,
                                    status: str, details: dict) -> dict:
        value = {"incident_id": "incident-test", "kind": kind,
                 "status": status, "details": details}
        self.incidents.append(value)
        return {"incident_id": value["incident_id"], "status": status}


class FakeWorkerManager:
    def __init__(self) -> None:
        self.progress = marker("r2", 2)

    def semantic_progress(self, _instance_id: str) -> dict:
        return dict(self.progress)


class ContractHarnessManager(HarnessManager):
    def __init__(self, control: FakeControl, worker: FakeWorkerManager) -> None:
        self.control = control  # type: ignore[assignment]
        self.worker_manager = worker  # type: ignore[assignment]
        self.start_count = 0
        self.exit_code = 0

    def status(self, _run_id: str) -> dict:
        return {"ok": True, "run": self.control.run, "observed": {
            "container_present": True, "running": False,
            "exit_code": self.exit_code, "status": "exited",
        }}

    def start_run(self, run_id: str, **_arguments) -> dict:
        self.start_count += 1
        return self.control.update_harness_run(
            run_id, status="running", metadata_update={
                "attempt_started_progress": self.worker_manager.semantic_progress(
                    "instance-continuation"
                ),
            },
        )


def main() -> int:
    control = FakeControl()
    worker = FakeWorkerManager()
    manager = ContractHarnessManager(control, worker)

    advanced = manager.reconcile_once()
    if advanced.get("continued") != 1 or control.run["restart_count"] != 0 \
            or control.run["metadata"].get("continuation_count") != 1:
        raise AssertionError(f"clean progress was misclassified as restart: {advanced}")

    for expected in (1, 2):
        stalled = manager.reconcile_once()
        if stalled.get("continued") != 1 \
                or control.run["metadata"].get(
                    "consecutive_clean_yields_without_progress"
                ) != expected:
            raise AssertionError(f"bounded clean continuation failed: {stalled}")
    stopped = manager.reconcile_once()
    if stopped.get("operator_required") != 1 or control.run["status"] != "error" \
            or control.run["desired_status"] != "stopped" or not control.incidents:
        raise AssertionError(f"no-progress circuit breaker failed: {stopped}")

    control.run.update({
        "desired_status": "running", "status": "running", "restart_count": 0,
        "updated_unix": time.time() - 10,
    })
    manager.exit_code = 7
    errored = manager.reconcile_once()
    if errored.get("restarted") != 1 or control.run["restart_count"] != 1:
        raise AssertionError(f"error restart budget was not independent: {errored}")

    print(json.dumps({
        "event": "pass",
        "payload": {
            "clean_yield_is_continuation": True,
            "clean_yield_does_not_consume_restart_budget": True,
            "no_progress_circuit_breaker": True,
            "operator_incident_recorded": True,
            "error_restart_budget_independent": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
