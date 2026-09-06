#!/usr/bin/env python3
"""Contained regression for explicit, fail-closed capability recovery."""

from __future__ import annotations

import json
import gzip
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace

from smacx_control import ControlPlane
from smacx_journal import CampaignJournal
from smacx_operations import OperationsManager
from smacx_store import MemoryScope, SmacxStore
from smacx_worker_manager import WorkerManager, WorkerManagerError


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-incident-recovery-") as temporary:
        root = Path(temporary)
        os.environ["SMACX_DIAGNOSTICS_ROOT"] = str(root / "diagnostics")
        store = SmacxStore(root / "smacx.sqlite3")
        control = ControlPlane(store, root / "secrets")
        agent = control.create_agent("Recovery agent", agent_id="agent-recovery")
        created = control.create_solo_match(
            "Recovery match", agent["agent_id"], match_id="match-recovery",
        )
        scope = MemoryScope(
            created["match"]["match_id"], agent["agent_id"],
            created["perspective"]["perspective_id"],
        )
        instance = store.register_instance(
            instance_id="instance-recovery", worker_kind="container-linux", scope=scope,
        )
        source = control.register_game_source(
            "Local game", "/fixture/game", "a" * 64, game_source_id="game-recovery",
        )
        runtime = control.register_runtime(
            "Managed runtime", "image", "smacx-agent-worker:dev",
            content_fingerprint="b" * 64, runtime_id="runtime-recovery",
        )
        secret = control.vault.put("worker.instance-recovery.bridge_token", "secret")
        control.put_worker_spec(
            instance["instance_id"], source["game_source_id"], runtime["runtime_id"],
            "smacx-agent-prepared:old", "worker-recovery", "data-recovery",
            secret["secret_id"], network={"secret_volume": "secret-recovery"},
        )
        control.assign_instance_to_seat(
            scope.match_id, scope.agent_id, scope.perspective_id, instance["instance_id"],
        )
        capability = control.record_supervision_incident(
            instance["instance_id"], "capability_gap:gap-" + "c" * 32,
            "operator_required", {"turn": 12},
        )
        derivative = control.record_supervision_incident(
            instance["instance_id"], "harness_clean_yield_no_progress",
            "operator_required", {"run_id": "run-old"},
        )
        unrelated = control.record_supervision_incident(
            instance["instance_id"], "worker_lost", "open", {"reason": "fixture"},
        )
        other_gap = control.record_supervision_incident(
            instance["instance_id"], "capability_gap:gap-" + "e" * 32,
            "operator_required", {"turn": 12, "reason": "different gap"},
        )
        journal = CampaignJournal(root / "campaigns")
        before_list = control.list_supervision_incidents(match_id=scope.match_id)
        before_head = journal.verify(scope)["head_hash"]
        after_list = control.list_supervision_incidents(match_id=scope.match_id)
        after_head = journal.verify(scope)["head_hash"]
        if before_list != after_list or before_head != after_head:
            raise AssertionError("listing incidents unexpectedly mutated the campaign journal")
        control.record_supervision_incident(
            instance["instance_id"], "worker_lost", "open", {"reason": "fixture"},
        )
        if journal.verify(scope)["head_hash"] != before_head:
            raise AssertionError("unchanged supervision sample appended another journal event")

        manager = object.__new__(WorkerManager)
        manager.control = control
        manager.store = store
        manager.journal = journal
        manager._lifecycle_lock = threading.RLock()
        manager._incident_recovery_lock = manager._lifecycle_lock
        control.update_worker_network(instance["instance_id"], {
            **control.get_worker_spec(instance["instance_id"])["network"],
            "mcp_container_name": "mcp-recovery",
        })
        containers = {
            name: {"State": {"Running": True, "Paused": False}, "purpose": purpose}
            for name, purpose in (("worker-recovery", "game-worker"),
                                  ("mcp-recovery", "mcp-sidecar"))
        }
        paused = []

        def pause(name):
            assert not containers[name]["State"]["Paused"]
            containers[name]["State"]["Paused"] = True
            paused.append(name)

        def require_owned(container, installation_id, *, purpose):
            assert installation_id == store.installation_id()
            assert container["purpose"] == purpose

        manager.docker = SimpleNamespace(inspect_container=lambda name: containers[name],
                                        require_owned=require_owned, pause_container=pause)
        frozen = manager.quarantine_match(scope.match_id)
        assert frozen["native_and_collectors_frozen"]
        assert paused == ["worker-recovery", "mcp-recovery"]
        manager.quarantine_match(scope.match_id)
        assert len(paused) == 2, "quarantine is not idempotent"
        manager.ensure_prepared_worker_image = lambda game_source_id: (
            "smacx-agent-prepared:current"
        )
        refreshed = manager._refresh_match_worker_images(scope.match_id)
        if not refreshed[0]["changed"] or control.get_worker_spec(
                instance["instance_id"])["image_ref"] != "smacx-agent-prepared:current":
            raise AssertionError(f"worker runtime did not refresh: {refreshed}")

        recovery_calls: list[tuple[str, bool]] = []

        def recover(match_id: str, *, refresh_runtime: bool = False) -> dict:
            active = control.list_supervision_incidents(match_id=match_id, active_only=True)
            if capability["incident_id"] not in {item["incident_id"] for item in active}:
                raise AssertionError("capability latch cleared before native recovery")
            recovery_calls.append((match_id, refresh_runtime))
            control.update_match_lifecycle(match_id, "running")
            return {"ok": True, "match": control.get_match(match_id), "runtime_refresh": refreshed}

        manager.recover_match = recover
        result = manager.retry_match_after_update(
            scope.match_id, capability["incident_id"],
        )
        if recovery_calls != [(scope.match_id, True)] or not result["operator_attention_cleared"]:
            raise AssertionError(f"explicit recovery was not executed once: {result}")
        status = {
            item["incident_id"]: item["status"]
            for item in control.list_supervision_incidents(match_id=scope.match_id)
        }
        if status[capability["incident_id"]] != "recovered" or \
                status[derivative["incident_id"]] != "recovered" or \
                status[unrelated["incident_id"]] != "open" or \
                status[other_gap["incident_id"]] != "operator_required":
            raise AssertionError(f"incident recovery scope was unsafe: {status}")
        repeated = manager.retry_match_after_update(
            scope.match_id, capability["incident_id"],
        )
        if not repeated.get("already_recovered") or len(recovery_calls) != 1:
            raise AssertionError(f"repeated recovery was not idempotent: {repeated}")

        failed = control.record_supervision_incident(
            instance["instance_id"], "capability_gap:gap-" + "d" * 32,
            "operator_required", {"turn": 13},
        )

        def fail_recovery(match_id: str, *, refresh_runtime: bool = False) -> dict:
            raise WorkerManagerError("contained_recovery_failure")

        manager.recover_match = fail_recovery
        try:
            manager.retry_match_after_update(scope.match_id, failed["incident_id"])
        except WorkerManagerError as exception:
            if str(exception) != "contained_recovery_failure":
                raise
        else:
            raise AssertionError("failed recovery unexpectedly succeeded")
        still_active = {
            item["incident_id"]
            for item in control.list_supervision_incidents(
                match_id=scope.match_id, active_only=True,
            )
        }
        if failed["incident_id"] not in still_active:
            raise AssertionError("failed recovery cleared the capability latch")

        # A bridge outage with a live container must freeze once and remain
        # latched on the next supervision pass, including sidecar restarts.
        manager.control_data_volume = None
        manager.worker_status = lambda _: {"running": True, "health": "unhealthy"}
        with store.transaction() as connection:
            connection.execute("UPDATE worker_specs SET desired_status='running',updated_unix=0")
        operations = OperationsManager(control, data_root=root, worker_manager=manager)
        stopped = operations.reconcile_once()
        assert stopped["operator_required"] == 1
        match = control.get_match(scope.match_id)
        assert match["status"] == "error"
        assert match["metadata"]["incident_quarantine"]["native_and_collectors_frozen"]
        stopped_head = journal.verify(scope)["head_hash"]
        repeated_stop = operations.reconcile_once()
        assert repeated_stop["operator_required"] == 0 and repeated_stop["recovered"] == 0
        assert journal.verify(scope)["head_hash"] == stopped_head

        # The original unhealthy process evidence must exist before recovery
        # removes/replaces the container, and must survive a failed recovery.
        control.update_match_lifecycle(scope.match_id, "running", metadata={
            "incident_quarantine": {}, "recovery_required": False,
            "recovery_checkpoint": {"verified": True, "slot": "fixture"}})
        with store.transaction() as connection:
            connection.execute("UPDATE worker_specs SET desired_status='running',updated_unix=0")
        containers["worker-recovery"]["Config"] = {"Labels": {
            "io.smacx.managed": "true", "io.smacx.installation": store.installation_id()}}
        containers["worker-recovery"]["State"].update({"OOMKilled": False,
            "Health": {"Status": "unhealthy", "FailingStreak": 6,
                       "Log": [{"ExitCode": 1, "Output": "bridge timeout token=private-fixture"}]}})
        manager.docker.container_logs = lambda name, tail: "bridge stalled password=private-fixture"
        evidence_verified = []
        def evidence_before_recovery(match_id):
            rows = [json.loads(line) for path in (root / "diagnostics").rglob("*.gz")
                    for line in gzip.open(path, "rt")]
            loss = [row for row in rows if row["kind"] == "worker_liveness_lost"][-1]
            assert loss["payload"]["state"]["OOMKilled"] is False
            assert loss["payload"]["health"]["failing_streak"] == 6
            assert "bridge stalled" in loss["payload"]["worker_log"]
            assert "private-fixture" not in json.dumps(loss)
            assert loss["payload"]["cause"] == "undetermined"
            evidence_verified.append(True)
            raise WorkerManagerError("fixture_recovery_failed")
        manager.recover_match = evidence_before_recovery
        assert operations.reconcile_once()["operator_required"] == 1
        assert evidence_verified == [True]

        print(json.dumps({
            "event": "pass",
            "payload": {
                "current_runtime_image_selected": True,
                "checkpoint_recovery_precedes_acknowledgement": True,
                "capability_and_derivative_recovered": True,
                "unrelated_incident_preserved": True,
                "interrupted_response_retry_is_idempotent": True,
                "failed_recovery_stays_latched": True,
                "incident_listing_is_read_only": True,
                "unchanged_incident_journal_deduplicated": True,
                "live_worker_and_collector_quarantined_once": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
