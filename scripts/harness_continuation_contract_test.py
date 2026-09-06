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
        "meaningful_fingerprint": f"turn-{turn}",
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

    def update_match_lifecycle(self, _match_id: str, status: str, *, metadata: dict) -> dict:
        self.match_state = {"status": status, "metadata": metadata}
        return self.match_state

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
        self.quarantines = []

    def quarantine_match(self, match_id: str) -> dict:
        self.quarantines.append(match_id)
        return {"native_and_collectors_frozen": True}

    def semantic_progress(self, _instance_id: str) -> dict:
        return dict(self.progress)


class ContractHarnessManager(HarnessManager):
    def __init__(self, control: FakeControl, worker: FakeWorkerManager) -> None:
        self.control = control  # type: ignore[assignment]
        self.worker_manager = worker  # type: ignore[assignment]
        self.start_count = 0
        self.exit_code = 0
        self.observed_running = False
        self.docker = type("FakeDocker", (), {
            "stop_container": lambda _self, _name, timeout=10: None,
            "remove_container": lambda _self, _name: None,
        })()

    def status(self, _run_id: str) -> dict:
        return {"ok": True, "run": self.control.run, "observed": {
            "container_present": True, "running": self.observed_running,
            "exit_code": self.exit_code, "status": "exited",
        }}

    def _journal_run_event(self, *_arguments, **_keywords) -> list[dict[str, str]]:
        return []

    def _append_capability_gap_report(self, report: dict) -> None:
        self.capability_report = dict(report)

    def telemetry(self, _run_id: str) -> dict:
        return {"ok": True, "telemetry": {
            "api_calls": 3, "output_tokens": 5000, "reasoning_tokens": 1000,
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


def reasoning_detail_is_not_extra_output() -> None:
    for total, should_stop in ((2500, False), (4096, True)):
        control = FakeControl()
        worker = FakeWorkerManager()
        manager = ContractHarnessManager(control, worker)
        manager.observed_running = True
        control.run["metadata"] = {
            "semantic_sample_unix": time.time() - 61,
            "semantic_telemetry_unix": time.time() - 61,
            "semantic_fingerprint": "turn-2",
            "semantic_progress_unix": time.time() - 400,
            "semantic_baseline_telemetry": {
                "api_calls": 10, "output_tokens": 10000, "reasoning_tokens": 8000,
            },
        }
        manager.telemetry = lambda _: {"ok": True, "telemetry": {
            "api_calls": 11, "output_tokens": 10000 + total,
            "reasoning_tokens": 10000,
        }}
        result = manager.reconcile_once()
        assert bool(result["operator_required"]) is should_stop, result
        if should_stop:
            assert manager.capability_report["generated_tokens_without_progress"] == total
        else:
            assert not control.incidents and not worker.quarantines


def main() -> int:
    reasoning_detail_is_not_extra_output()
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

    # A process that stays alive while spending calls/tokens against an
    # unchanged native state is quarantined, never given a fresh retry episode.
    manager.observed_running = True
    manager.exit_code = 0
    control.run.update({
        "desired_status": "running", "status": "running", "restart_count": 0,
        "restart_policy": {
            **control.run["restart_policy"], "semantic_stall_seconds": 120,
            "semantic_stall_recovery_limit": 2,
        },
        "metadata": {
            "semantic_sample_unix": time.time() - 61,
            "semantic_telemetry_unix": time.time() - 61,
            "semantic_fingerprint": "turn-2",
            "semantic_progress_unix": time.time() - 180,
            "semantic_baseline_telemetry": {
                "api_calls": 0, "output_tokens": 0, "reasoning_tokens": 0,
            },
        },
    })
    live_stall = manager.reconcile_once()
    if live_stall.get("restarted") != 0 or live_stall.get("operator_required") != 1 \
            or control.run["desired_status"] != "stopped" \
            or worker.quarantines != ["match-continuation"]:
        raise AssertionError(f"live semantic stall was not quarantined: {live_stall}")
    assert manager.capability_report["supervisor_generated"] is True
    assert control.match_state["metadata"]["incident_quarantine"]["native_and_collectors_frozen"]

    # A live Hermes process whose native bridge remains unavailable must stop
    # with a visible capability incident. AI-2 previously remained alive and
    # silent forever in precisely this state.
    control.incidents.clear()
    control.run.update({
        "desired_status": "running", "status": "running", "restart_count": 0,
        "metadata": {
            "semantic_sample_unix": time.time() - 61,
            "semantic_unavailable_since_unix": time.time() - 90,
            "semantic_unavailable_samples": 2,
            "semantic_progress": marker("r2", 2),
        },
    })
    manager.worker_manager.progress = {
        "available": False, "reason": "worker_not_healthy",
    }
    unavailable = manager.reconcile_once()
    if unavailable.get("operator_required") != 1 \
            or control.run["status"] != "error" \
            or control.run["desired_status"] != "stopped" \
            or not str(control.run.get("last_error", "")).startswith("capability_gap:") \
            or not control.incidents \
            or not str(control.incidents[-1]["kind"]).startswith("capability_gap:"):
        raise AssertionError(f"bridge outage was not surfaced safely: {unavailable}")
    if manager.capability_report.get("supervisor_generated") is not True \
            or manager.capability_report.get("match_id") != "match-continuation":
        raise AssertionError("bridge outage did not queue a diagnostic report")

    # Hermes can exit zero after a runtime-context exception. Preserve the
    # same outage deadline across exited episodes; never spend another model
    # invocation while the native state is unavailable.
    control = FakeControl()
    worker = FakeWorkerManager()
    worker.progress = {"available": False, "reason": "game_worker_bridge_unavailable"}
    manager = ContractHarnessManager(control, worker)
    pending = manager.reconcile_once()
    assert manager.start_count == 0 and pending["continued"] == 0
    since = control.run["metadata"]["semantic_unavailable_since_unix"]
    manager.reconcile_once()
    assert control.run["metadata"]["semantic_unavailable_since_unix"] == since
    assert manager.start_count == 0
    control.run["metadata"]["semantic_unavailable_since_unix"] = time.time() - 61
    failed = manager.reconcile_once()
    assert failed["operator_required"] == 1 and manager.start_count == 0
    assert control.run["desired_status"] == "stopped"

    control = FakeControl()
    manager = ContractHarnessManager(control, worker)
    manager.reconcile_once()
    worker.progress = marker("recovered", 2)
    recovered = manager.reconcile_once()
    assert recovered["continued"] == 1 and manager.start_count == 1
    assert control.run["metadata"]["semantic_unavailable_samples"] == 0

    print(json.dumps({
        "event": "pass",
        "payload": {
            "clean_yield_is_continuation": True,
            "clean_yield_does_not_consume_restart_budget": True,
            "no_progress_circuit_breaker": True,
            "operator_incident_recorded": True,
            "error_restart_budget_independent": True,
            "live_token_spending_stall_quarantined": True,
            "persistent_bridge_outage_requires_operator": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
