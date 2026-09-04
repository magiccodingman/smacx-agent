#!/usr/bin/env python3
"""Bounded native observation-ring and continuity-gap integration contract."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time

from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_observation import ObservationCollector
from smacx_store import MemoryScope, SmacxStore
from smacx_world_store import WorldStore


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    source = (ROOT / "bridge/src/agent_bridge.cpp").read_text(encoding="utf-8")
    base_source = (ROOT / "bridge/src/base.cpp").read_text(encoding="utf-8")
    vehicle_source = (ROOT / "bridge/src/veh.cpp").read_text(encoding="utf-8")
    patch_source = (ROOT / "bridge/src/patch.cpp").read_text(encoding="utf-8")
    collector_source = (ROOT / "src/smacx_observation.py").read_text(encoding="utf-8")
    manager_source = (ROOT / "src/smacx_worker_manager.py").read_text(encoding="utf-8")
    required = (
        "const size_t MaxObservationEvents = 1024;",
        "bool incomplete = after && after + 1 < oldest;",
        "reconciliation_required",
        'append_observation_event("perspective_changed"',
        'append_observation_event(outbound ? "chat_outbound" : "chat_inbound"',
        'append_observation_event("deferred_action_queued"',
        '"visible_unit_moved"',
        '"visible_unit_damaged"',
        '"visible_unit_destroyed"',
        "semantic_vehicle_handles.erase",
        '"visible_base_founded"',
        '"visible_base_captured"',
        '"visible_base_destroyed"',
        '"known_tile_changed"',
        '"project_race_started"',
        '"project_race_changed"',
        '"project_race_halted"',
        'if (op == "observation_feed") return observation_feed_response(request);',
    )
    if any(value not in source for value in required):
        raise AssertionError("native observation ring or overflow contract drifted")
    if any(value not in base_source for value in (
        "agent_observe_base_founded(base_id)",
        "agent_observe_base_destroyed(base_id)",
        "agent_observe_base_captured(base_id, faction_id, faction_id_atk)",
    )) or "agent_observe_unit_destroyed(veh_id)" not in vehicle_source:
        raise AssertionError("native semantic lifecycle hooks are detached")
    if any(value not in patch_source for value in (
        "write_jump(0x5C08C0, (int)veh_kill)",
        "write_call(0x4C9870, (int)mod_base_init)",
        "write_call(0x4CD629, (int)mod_base_kill)",
        "write_call(0x50AE77, (int)mod_base_kill)",
        "write_call(0x598778, (int)mod_capture_base)",
    )):
        raise AssertionError("patched native mutation routes no longer reach lifecycle hooks")
    if any(value not in collector_source for value in (
        'self._semantic_items("list_bases"',
        'self._semantic_items("list_units"',
        'self._bridge("list_factions")',
        'self._bridge("list_technologies")',
    )):
        raise AssertionError("world reconciliation no longer uses valid bridge operation names")
    if any(value not in manager_source for value in (
        'get("io.smacx.session")',
        'f"SMACX_AGENT_MATCH_ID={spec[\'match_id\']}"',
        'f"SMACX_AGENT_SESSION_ID={session_id}"',
    )):
        raise AssertionError("MCP sidecar identity wiring drifted")

    with tempfile.TemporaryDirectory(prefix="smacx-native-observation-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-observation", "Observation")
        store.create_match(match_id="match-observation", display_name="Observation", mode="solo")
        store.create_perspective("match-observation", "agent-observation",
                                 perspective_id="perspective-observation")
        scope = MemoryScope("match-observation", "agent-observation",
                            "perspective-observation")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / "world-snapshots")
        attention = AttentionService(store, journal, scope)
        collector = ObservationCollector(
            scope=scope, session_id="session-observation",
            bridge_call=lambda *_args, **_kwargs: {}, journal=journal,
            world_store=worlds, attention=attention,
        )
        collector._append_native_feed({
            "continuity": "incomplete", "next_sequence": 1500,
            "lost_after_observation_sequence": 475,
            "events": [{"sequence": 1499, "kind": "chat_inbound", "turn": 30,
                        "subject_a": 2, "subject_b": 1}],
        })
        replay = journal.events_after(scope)
        kinds = [event["event_type"] for event in replay]
        assert "observation.continuity_gap" in kinds
        assert collector.native_after_sequence == 1500
        with store._connect() as connection:
            projected = connection.execute(
                "SELECT payload_json FROM world_observation_projection ORDER BY "
                "observation_sequence",
            ).fetchall()
        assert len(projected) == 1
        assert json.loads(projected[0]["payload_json"])["reconciliation_required"] is True
        assert attention.pending_summary()["has_critical"] is True

        collector._append_native_feed({
            "continuity": "complete", "next_sequence": 1502,
            "events": [
                {"sequence": 1501, "kind": "visible_unit_moved", "turn": 30,
                 "subject_a": 91, "subject_b": 2, "from_tile_id": 17,
                 "to_tile_id": 18, "continuous_visibility": True},
                {"sequence": 1502, "kind": "visible_unit_moved", "turn": 30,
                 "subject_a": 91, "subject_b": 2, "from_tile_id": 18,
                 "to_tile_id": 34, "continuous_visibility": True},
            ],
        })
        assert collector._continuous_contact_moves["vehicle-handle-91"] == [
            {"from": "location-17", "to": "location-18", "native_sequence": 1501},
            {"from": "location-18", "to": "location-34", "native_sequence": 1502},
        ]
        collector._append_native_feed({
            "continuity": "complete", "next_sequence": 1503,
            "events": [{"sequence": 1503, "kind": "visible_unit_lost", "turn": 30,
                        "subject_a": 91, "subject_b": 2}],
        })
        assert "vehicle-handle-91" not in collector._continuous_contact_moves

        # More than one transport page is drained before reconciliation. A
        # continuously visible path that crosses the 256-event boundary keeps
        # one semantic contact identity and the visible destruction is not
        # degraded into a fog-loss event.
        stream = []
        for sequence in range(1, 301):
            stream.append({
                "sequence": sequence, "kind": "visible_unit_moved", "turn": 31,
                "subject_a": 77, "subject_b": 2,
                "from_tile_id": sequence, "to_tile_id": sequence + 1,
                "continuous_visibility": True,
            })
        stream.extend([
            {"sequence": 301, "kind": "visible_unit_damaged", "turn": 31,
             "subject_a": 77, "subject_b": 2, "from_tile_id": 301,
             "to_tile_id": 301, "value_before": 10, "value_after": 7,
             "continuous_visibility": True},
            {"sequence": 302, "kind": "visible_unit_destroyed", "turn": 31,
             "subject_a": 77, "subject_b": 2, "from_tile_id": 301,
             "to_tile_id": 301, "value_before": 7, "value_after": 0,
             "continuous_visibility": True},
            {"sequence": 303, "kind": "chat_inbound", "turn": 31,
             "subject_a": 2, "subject_b": 1},
            {"sequence": 304, "kind": "known_tile_changed", "turn": 31,
             "subject_a": 400, "from_tile_id": 400, "to_tile_id": 400,
             "continuous_visibility": True},
        ])
        collector.native_after_sequence = 0
        collector._pending_native_events.clear()

        def paged_feed(_operation: str, **kwargs):
            after = int(kwargs.get("after_sequence", 0))
            page = [row for row in stream if int(row["sequence"]) > after][:256]
            next_sequence = int(page[-1]["sequence"]) if page else after
            return {"ok": True, "continuity": "complete", "events": page,
                    "next_sequence": next_sequence,
                    "has_more": next_sequence < int(stream[-1]["sequence"]),
                    "action_revision": "revision-batched"}

        collector.bridge_call = paged_feed
        drained = collector._drain_native_feed()
        assert drained["drained_pages"] == 2 and drained["drained_event_count"] == 304
        prior_contact = {
            "object_ref": "contact-stable", "kind": "foreign_contact", "status": "active",
            "location_ref": "location-1",
            "metadata": {"native_observation_key": "vehicle-handle-77"}, "fields": {},
        }
        semantic = collector._coalesce_native_events(
            current_objects=[], prior_objects=[prior_contact], turn=31,
        )
        moved = next(row for row in semantic if row["event_kind"] == "contact_moved")
        assert moved["contact_ref"] == "contact-stable" and len(moved["path"]) == 300
        assert any(row["event_kind"] == "contact_damaged" for row in semantic)
        assert any(row["event_kind"] == "contact_destroyed" for row in semantic)
        assert not any(row["event_kind"] == "contact_lost" for row in semantic)
        assert any(row["event_kind"] == "terrain_or_improvement_changed" for row in semantic)

        # A base may change hands twice between reconciliations. Preserve both
        # transitions and identify the return to the initially observed owner.
        collector._pending_native_events.extend([
            {"native_sequence": 305, "native_kind": "visible_base_captured",
             "subject_a": 3, "subject_b": 2, "from_tile_id": 44,
             "to_tile_id": 44, "value_before": 1, "value_after": 2,
             "continuous_visibility": True, "turn": 31},
            {"native_sequence": 306, "native_kind": "visible_base_captured",
             "subject_a": 3, "subject_b": 1, "from_tile_id": 44,
             "to_tile_id": 44, "value_before": 2, "value_after": 1,
             "continuous_visibility": True, "turn": 31},
        ])
        recapture = collector._coalesce_native_events(
            current_objects=[], prior_objects=[], turn=31,
        )
        captures = [row for row in recapture if row["base_ref"] == "base-location-44"]
        assert [row["event_kind"] for row in captures] == [
            "base_captured", "base_recaptured",
        ]
        assert captures[1]["capture_sequence"] == 2

        collector._pending_native_events.extend([
            {"native_sequence": 307, "native_kind": "project_race_started",
             "subject_a": 39, "subject_b": 2, "value_before": -1,
             "value_after": 39, "turn": 31},
            {"native_sequence": 308, "native_kind": "project_race_changed",
             "subject_a": 40, "subject_b": 2, "value_before": 39,
             "value_after": 40, "turn": 31},
        ])
        project_reports = collector._coalesce_native_events(
            current_objects=[], prior_objects=[], turn=31,
        )
        assert project_reports == [{
            "event_kind": "project_race_started", "project_ref": "project-39",
            "turn": 31, "provenance": "native_public_report",
            "builder_ref": "faction-2",
        }, {
            "event_kind": "project_race_changed", "project_ref": "project-40",
            "turn": 31, "provenance": "native_public_report",
            "builder_ref": "faction-2", "prior_project_ref": "project-39",
        }]

        # A provider request may demand a fresh projection while the observer
        # thread is polling. Both paths must share one serialized collector.
        active = 0
        maximum_active = 0
        guard = threading.Lock()

        def simulated_collection() -> dict:
            nonlocal active, maximum_active
            with guard:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.03)
            with guard:
                active -= 1
            return {"ok": True}

        collector._collect_once_locked = simulated_collection  # type: ignore[method-assign]
        threads = [threading.Thread(target=collector.collect_once) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(1)
        assert all(not thread.is_alive() for thread in threads)
        assert maximum_active == 1

    print(json.dumps({"event": "pass", "payload": {
        "bounded_native_ring": True,
        "overflow_explicit": True,
        "continuity_gap_journaled": True,
        "projection_marks_reconciliation": True,
        "overflow_enters_critical_attention": True,
        "chat_and_deferred_actions_emit_events": True,
        "semantic_native_transition_events_present": True,
        "native_lifecycle_hooks_attached": True,
        "continuous_movement_path_collected": True,
        "over_256_pages_drained_and_coalesced": True,
        "visible_destruction_not_fog_loss": True,
        "capture_recapture_preserved_within_reconciliation": True,
        "collector_bridge_operations_valid": True,
        "managed_sidecar_identity_explicit": True,
        "collector_refresh_serialized": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
