#!/usr/bin/env python3
"""Contained contract for durable, redacted capability-gap diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import zipfile

from smacx_control import ControlPlane
from smacx_operations import OperationsManager
from smacx_store import MemoryScope, SmacxStore


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-gap-incident-") as temporary:
        root = Path(temporary)
        store = SmacxStore(root / "smacx.sqlite3")
        control = ControlPlane(store, root / "secrets")
        agent = control.create_agent("Diagnostic agent", agent_id="agent-diagnostic")
        match = control.create_solo_match(
            "Diagnostic match", agent["agent_id"], match_id="match-diagnostic",
        )
        scope = MemoryScope(
            match["match"]["match_id"], agent["agent_id"],
            match["perspective"]["perspective_id"],
        )
        instance = store.register_instance(
            instance_id="instance-diagnostic", worker_kind="container-linux", scope=scope,
        )
        source = control.register_game_source(
            "Legal local game", "/private/game/location", "a" * 64,
            game_source_id="game-diagnostic",
        )
        runtime = control.register_runtime(
            "Managed Proton", "docker-volume", "runtime-volume-diagnostic",
            content_fingerprint="b" * 64, runtime_id="runtime-diagnostic",
        )
        secret = control.vault.put("worker.instance-diagnostic.bridge_token", "bridge-secret")
        control.put_worker_spec(
            instance["instance_id"], source["game_source_id"], runtime["runtime_id"],
            "smacx-agent-worker:dev", "worker-diagnostic", "worker-volume-diagnostic",
            secret["secret_id"], network={
                "secret_volume": "secret-volume-diagnostic",
                "mcp_status": "running", "mcp_url": "http://mcp-diagnostic:47814/mcp",
                "mcp_container_name": "mcp-diagnostic",
            },
        )
        control.assign_instance_to_seat(
            scope.match_id, scope.agent_id, scope.perspective_id, instance["instance_id"],
        )
        control.update_worker_observation(
            instance["instance_id"], desired_status="running", observed_status="running",
            instance_status="running",
        )
        session = store.start_session(
            scope, instance["instance_id"], session_id="session-diagnostic",
        )
        provider = control.configure_provider(
            "Diagnostic provider", "http://models-diagnostic:8000/v1",
            default_model_id="diagnostic-model", context_length_override=65_536,
        )
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO provider_models(provider_id, model_id, display_name, context_length, "
                "capabilities_json, raw_metadata_json, discovered_unix) "
                "VALUES (?, 'diagnostic-model', 'Diagnostic model', 65536, '{}', '{}', 1)",
                (provider["provider_id"],),
            )
        descriptor = control.prepare_hermes_profile(
            scope.match_id, provider["provider_id"], reasoning_effort="low",
        )
        run = control.create_harness_run(
            descriptor["harness_profile_id"], match_id=scope.match_id,
            initial_prompt="Play safely.", continuation_prompt="Continue safely.",
            restart_policy={"restart_limit": 10}, run_id="run-diagnostic",
        )
        control.update_harness_run(run["run_id"], status="running", desired_status="running")
        gap = {
            "gap_id": "gap-" + "c" * 32,
            "reported_at_unix": 1_800_000_000.0,
            "match_id": scope.match_id,
            "session_id": session["session_id"],
            "revision": "revision-diagnostic",
            "turn": 17,
            "screen_or_state": "Unexpected orbital insertion dialog",
            "intended_decision": "Choose a legal destination for the orbital unit",
            "required_observation": "Expose candidate destinations and their legality",
            "required_action": "Select one destination or cancel",
            "why_blocked": "provider=http://10.20.30.40:8000/v1 api_key=DO-NOT-LEAK",
            "snapshot": {
                "ok": True,
                "snapshot": {"phase": "capability_gap", "chat_messages": ["private words"]},
                "provider_url": "http://private-model.internal:8000/v1",
            },
        }
        (root / "capability-gaps.jsonl").write_text(
            json.dumps(gap, separators=(",", ":")) + "\n", encoding="utf-8",
        )
        operations = OperationsManager(control, data_root=root)
        result = operations.ingest_capability_gaps_once()
        if result["ingested"] != 1 or result["errors"]:
            raise AssertionError(f"gap was not ingested: {result}")
        incidents = control.list_supervision_incidents(match_id=scope.match_id, active_only=True)
        if len(incidents) != 1 or incidents[0]["status"] != "operator_required":
            raise AssertionError(f"durable operator incident missing: {incidents}")
        stopped_run = control.get_harness_run(run["run_id"])
        if stopped_run["desired_status"] != "stopped" or \
                stopped_run.get("metadata", {}).get("capability_gap_id") != gap["gap_id"]:
            raise AssertionError(f"autonomous harness was not stopped: {stopped_run}")
        bundle_info = incidents[0]["details"].get("diagnostic_bundle", {})
        bundle = root / str(bundle_info.get("relative_path", ""))
        if not bundle.is_file() or bundle.stat().st_size >= 25 * 1024 * 1024:
            raise AssertionError("bounded diagnostic ZIP was not published")
        with zipfile.ZipFile(bundle) as archive:
            names = set(archive.namelist())
            required = {
                "README.md", "manifest.json", "incident/capability-gap.json",
                "incident/environment.json", "incident/match-configuration.json",
                "incident/seat-map.json", "traces/semantic-trace.jsonl",
                "traces/bridge.log", "traces/mcp.log", "traces/harness.log",
                "traces/supervisor.log",
            }
            if not required.issubset(names):
                raise AssertionError(f"diagnostic files missing: {sorted(required - names)}")
            combined = b"\n".join(archive.read(name) for name in names).decode(
                "utf-8", errors="replace",
            )
        for forbidden in (
            "DO-NOT-LEAK", "10.20.30.40", "private-model.internal", "private words",
            "/private/game/location", "bridge-secret",
        ):
            if forbidden in combined:
                raise AssertionError(f"diagnostic bundle leaked forbidden value: {forbidden}")
        repeated = operations.ingest_capability_gaps_once()
        if repeated["ingested"] != 0 or repeated["ignored"] != 1:
            raise AssertionError(f"gap ingestion was not idempotent: {repeated}")

        # Harness supervision publishes the incident immediately, then the
        # operations pass must enrich that same incident with a diagnostic ZIP
        # instead of treating it as an already-finished duplicate.
        supervisor_gap = {
            **gap,
            "gap_id": "gap-" + "d" * 32,
            "why_blocked": "worker_not_healthy",
            "supervisor_generated": True,
        }
        control.record_supervision_incident(
            instance["instance_id"], f"capability_gap:{supervisor_gap['gap_id']}",
            "operator_required", {
                "schema": "smacx.capability-gap-incident.v1",
                "gap_id": supervisor_gap["gap_id"],
                "summary": "The AI stopped because its native game bridge became unavailable.",
                "run_id": run["run_id"],
                "native_worker_preserved": True,
            },
        )
        with (root / "capability-gaps.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(supervisor_gap, separators=(",", ":")) + "\n")
        enriched = operations.ingest_capability_gaps_once()
        if enriched["ingested"] != 1 or enriched["errors"]:
            raise AssertionError(f"supervisor incident was not enriched: {enriched}")
        supervisor_incident = next(
            item for item in control.list_supervision_incidents(
                match_id=scope.match_id, active_only=True,
            ) if item["incident_kind"].endswith(supervisor_gap["gap_id"])
        )
        supervisor_bundle = root / str(
            supervisor_incident["details"].get("diagnostic_bundle", {}).get(
                "relative_path", ""
            )
        )
        if not supervisor_bundle.is_file():
            raise AssertionError("supervisor incident diagnostic ZIP was not published")
        print(json.dumps({
            "event": "pass", "payload": {
                "durable_operator_incident": True, "idempotent_ingestion": True,
                "autonomous_restart_stopped": True,
                "redacted_bundle": True, "bounded_zip": True,
                "game_binaries_excluded": True, "private_conversations_excluded": True,
                "supervisor_incident_enriched": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
