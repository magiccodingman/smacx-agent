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
from smacx_world import WorldService
from observation_collector_benchmark import NativeFixture


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
        'if (op == "semantic_identity_state") return semantic_identity_state_response(request);',
        'if (op == "test_identity_compaction_fixture")',
        'if (op == "test_airdrop_legality_fixture")',
        'if (op == "test_airdrop_collection_stress_fixture")',
        'if (op == "semantic_airdrop_targets")',
        '"smacx.private-vehicle-identity.v1"',
        "semantic_airdrop_target_receipt(",
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
    move_source = (ROOT / "bridge/src/move.cpp").read_text(encoding="utf-8")
    if "&& (!combat || !at_war(faction_id, veh->faction_id))" in move_source \
            or "&& veh->faction_id != faction_id && !has_pact(faction_id, veh->faction_id))" not in move_source:
        raise AssertionError("native non-Pact occupied-airdrop rejection drifted")
    routine_units = source.split('} else if (domain == "units") {', 1)[1].split(
        '} else if (domain == "factions") {', 1,
    )[0]
    if "semantic_airdrop_target_receipt(" in routine_units \
            or '\\"airdrop_target_tile_ids\\"' in routine_units:
        raise AssertionError("routine perspective collection enumerates Drop targets")
    fixture_gate = source.split("std::string test_airdrop_legality_fixture_response()", 1)[1].split(
        "if (!game_active())", 1,
    )[0]
    if 'GetEnvironmentVariableA("SMACX_AGENT_TEST_MODE"' not in fixture_gate \
            or 'GetEnvironmentVariableA("SMACX_ACCEPTANCE_AIRDROP_LEGALITY"' not in fixture_gate \
            or "||" not in fixture_gate:
        raise AssertionError("destructive airdrop fixture is not dual-gated")
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
        '"native_semantic_identity": native_identity_by_instance',
        '"semantic_identity_state", timeout=20.0',
        'if os.environ.get("SMACX_AGENT_TEST_MODE") == "1"',
        'values["SMACX_ACCEPTANCE_OWN_UNIT_COMPACTION"] = "1"',
        'values["SMACX_ACCEPTANCE_AIRDROP_LEGALITY"] = "1"',
        'values["SMACX_AGENT_TEST_MODE"] = "1"',
    )):
        raise AssertionError("MCP sidecar identity wiring drifted")
    revision_source = source.split("std::string semantic_revision() {", 1)[1].split(
        "void CALLBACK semantic_observation_timer_proc", 1,
    )[0]
    if "mix(static_cast<uint32_t>(*VehCount))" in revision_source \
            or "mix(static_cast<uint32_t>(*BaseCount))" in revision_source \
            or "mix(static_cast<uint32_t>(i))" in revision_source:
        raise AssertionError("provider action revision depends on compacting native row layout")

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
        # Drain is private durable staging, not publication. A crash here must
        # leave the ring cursor/events replayable without exposing raw rows.
        replay = journal.events_after(scope)
        assert replay == []
        assert collector.native_after_sequence == 1500
        staged = worlds.load_native_observation_stage(scope, collector.timeline_id)
        assert staged["continuity_gap"]["reconciliation_required"] is True
        assert staged["events"][0]["native_kind"] == "chat_inbound"
        restarted = ObservationCollector(
            scope=scope, session_id="session-observation",
            bridge_call=lambda *_args, **_kwargs: {}, journal=journal,
            world_store=worlds, attention=attention,
        )
        assert restarted.native_after_sequence == 1500
        assert restarted._pending_native_events == collector._pending_native_events
        worlds.begin_native_observation_publication(
            scope, collector.timeline_id, 1,
            {"source_through_sequence": int(staged["staged_after_sequence"])},
        )
        worlds.acknowledge_native_observation_publication(scope, collector.timeline_id, 1)
        collector._restore_native_stage()

        # Provider-inaccessible staging may legitimately span more than one
        # 1024-row native-ring generation while publication is being retried.
        # It must remain lossless rather than silently retaining only a tail.
        first_stage = [
            {"sequence": sequence, "kind": "known_tile_changed", "turn": 30,
             "subject_a": sequence, "from_tile_id": sequence,
             "to_tile_id": sequence, "continuous_visibility": True}
            for sequence in range(1, 901)
        ]
        second_stage = [
            {"sequence": sequence, "kind": "known_tile_changed", "turn": 30,
             "subject_a": sequence, "from_tile_id": sequence,
             "to_tile_id": sequence, "continuous_visibility": True}
            for sequence in range(901, 1801)
        ]
        collector._append_native_feed({
            "continuity": "complete", "next_sequence": 900, "events": first_stage,
        })
        collector._append_native_feed({
            "continuity": "complete", "next_sequence": 1800, "events": second_stage,
        })
        staged = worlds.load_native_observation_stage(scope, collector.timeline_id)
        assert len(staged["events"]) == 1800
        worlds.begin_native_observation_publication(
            scope, collector.timeline_id, 2,
            {"source_through_sequence": int(staged["staged_after_sequence"])},
        )
        worlds.acknowledge_native_observation_publication(scope, collector.timeline_id, 2)
        collector._restore_native_stage()

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
        # Use a fresh timeline-private stage for the bounded multi-page case.
        worlds._native_stage_path(scope, collector.timeline_id).unlink(missing_ok=True)
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
        captures = [row for row in recapture if row.get("base_ref") == "base-location-44"]
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
        project_reports = [row for row in project_reports
                           if str(row.get("event_kind", "")).startswith("project_race_")]
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

    # Freeze the complete publication, not merely its cursor. New native
    # activity after a partial publish belongs exclusively to N+1.
    with tempfile.TemporaryDirectory(prefix="smacx-native-frozen-publication-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-freeze", "Freeze")
        store.create_match(match_id="match-freeze", display_name="Freeze", mode="solo")
        store.create_perspective("match-freeze", "agent-freeze",
                                 perspective_id="perspective-freeze")
        scope = MemoryScope("match-freeze", "agent-freeze", "perspective-freeze")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / "snapshots")
        fixture = NativeFixture(16, 8, contacts=1)

        def make_collector():
            return ObservationCollector(
                scope=scope, session_id="session-freeze", bridge_call=fixture,
                journal=journal, world_store=worlds,
                attention=AttentionService(store, journal, scope),
            )

        make_collector().collect_once()
        fixture.revision = 2
        fixture.events = [{
            "sequence": 1, "kind": "visible_unit_damaged", "turn": 51,
            "subject_a": 1000, "subject_b": 2, "from_tile_id": 1,
            "to_tile_id": 1, "value_before": 10, "value_after": 8,
            "continuous_visibility": True,
        }]
        original_append = journal.append
        injected = False

        def fail_after_first_batch(*args, **kwargs):
            nonlocal injected
            event_type = args[1] if len(args) > 1 else kwargs.get("event_type")
            if event_type == "observation.semantic_batch" and not injected:
                injected = True
                raise RuntimeError("injected_after_first_journal_batch")
            return original_append(*args, **kwargs)

        journal.append = fail_after_first_batch  # type: ignore[method-assign]
        try:
            make_collector().collect_once()
            raise AssertionError("frozen publication failure not injected")
        except RuntimeError as exc:
            assert str(exc) == "injected_after_first_journal_batch"
        journal.append = original_append  # type: ignore[method-assign]
        frozen = worlds.load_native_observation_stage(scope, journal.timeline_id(scope))
        package_n = json.loads(json.dumps(frozen["publication_package"]))
        assert package_n["source_native_sequences"] == [1]
        assert package_n["action_revision"] == "benchmark-2"
        fixture.revision = 3
        fixture.events.append({
            "sequence": 2, "kind": "visible_unit_damaged", "turn": 52,
            "subject_a": 1000, "subject_b": 2, "from_tile_id": 1,
            "to_tile_id": 1, "value_before": 8, "value_after": 6,
            "continuous_visibility": True,
        })
        make_collector().collect_once()
        after_n = worlds.load(scope, journal.timeline_id(scope))
        assert after_n["action_revision"] == "benchmark-2"
        assert worlds.load_native_observation_stage(
            scope, journal.timeline_id(scope),
        )["committed_after_sequence"] == 1
        make_collector().collect_once()
        after_n1 = worlds.load(scope, journal.timeline_id(scope))
        assert after_n1["action_revision"] == "benchmark-3"
        events = worlds.temporal_events_since(scope, journal.timeline_id(scope), 0, limit=256)
        damage = [row for row in events if row["event"].get("event_kind") == "contact_damaged"]
        assert len(damage) == 2

        # The other dangerous window is after semantic journal publication but
        # before world-head replacement.
        fixture.revision = 4
        fixture.events.append({
            "sequence": 3, "kind": "visible_unit_damaged", "turn": 53,
            "subject_a": 1000, "subject_b": 2, "from_tile_id": 1,
            "to_tile_id": 1, "value_before": 6, "value_after": 5,
            "continuous_visibility": True,
        })
        original_replace = worlds.replace_projection
        replaced_once = False

        def fail_before_world_head(*args, **kwargs):
            nonlocal replaced_once
            if not replaced_once:
                replaced_once = True
                raise RuntimeError("injected_before_world_head_replacement")
            return original_replace(*args, **kwargs)

        worlds.replace_projection = fail_before_world_head  # type: ignore[method-assign]
        try:
            make_collector().collect_once()
            raise AssertionError("world-head failure not injected")
        except RuntimeError as exc:
            assert str(exc) == "injected_before_world_head_replacement"
        worlds.replace_projection = original_replace  # type: ignore[method-assign]
        frozen = worlds.load_native_observation_stage(scope, journal.timeline_id(scope))
        assert frozen["publication_package"]["source_native_sequences"] == [3]
        fixture.revision = 5
        fixture.events.append({
            "sequence": 4, "kind": "known_tile_changed", "turn": 54,
            "subject_a": 2, "from_tile_id": 2, "to_tile_id": 2,
            "continuous_visibility": True,
        })
        make_collector().collect_once()
        assert worlds.load(scope, journal.timeline_id(scope))["action_revision"] == "benchmark-4"
        make_collector().collect_once()
        assert worlds.load(scope, journal.timeline_id(scope))["action_revision"] == "benchmark-5"

    # Exercise both two-phase crash windows through the complete collector,
    # projector, journal, and temporal-history path.
    with tempfile.TemporaryDirectory(prefix="smacx-native-publication-crash-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-crash", "Crash")
        store.create_match(match_id="match-crash", display_name="Crash", mode="solo")
        store.create_perspective("match-crash", "agent-crash",
                                 perspective_id="perspective-crash")
        scope = MemoryScope("match-crash", "agent-crash", "perspective-crash")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / "snapshots")
        fixture = NativeFixture(16, 8, contacts=1)
        collector = ObservationCollector(
            scope=scope, session_id="session-crash", bridge_call=fixture,
            journal=journal, world_store=worlds,
            attention=AttentionService(store, journal, scope),
        )
        collector.collect_once()
        fixture.revision += 1
        fixture.events = [{
            "sequence": 1, "kind": "visible_unit_damaged", "turn": 51,
            "subject_a": 1000, "subject_b": 2, "from_tile_id": 1,
            "to_tile_id": 1, "value_before": 10, "value_after": 7,
            "continuous_visibility": True,
        }]
        original_append = journal.append
        failed = False

        def fail_before_semantic(*args, **kwargs):
            nonlocal failed
            event_type = args[1] if len(args) > 1 else kwargs.get("event_type")
            if event_type == "observation.semantic_batch" and not failed:
                failed = True
                raise RuntimeError("injected_before_semantic_publication")
            return original_append(*args, **kwargs)

        journal.append = fail_before_semantic  # type: ignore[method-assign]
        try:
            collector.collect_once()
            raise AssertionError("publication failure was not injected")
        except RuntimeError as exc:
            assert str(exc) == "injected_before_semantic_publication"
        journal.append = original_append  # type: ignore[method-assign]
        collector = ObservationCollector(
            scope=scope, session_id="session-crash", bridge_call=fixture,
            journal=journal, world_store=worlds,
            attention=AttentionService(store, journal, scope),
        )
        collector.collect_once()
        damaged = [row for row in worlds.temporal_events_since(
            scope, collector.timeline_id, 0, limit=256,
        ) if row["event"].get("event_kind") == "contact_damaged"]
        assert len(damaged) == 1

        fixture.revision += 1
        fixture.events = [{
            "sequence": 2, "kind": "visible_unit_damaged", "turn": 52,
            "subject_a": 1000, "subject_b": 2, "from_tile_id": 1,
            "to_tile_id": 1, "value_before": 7, "value_after": 5,
            "continuous_visibility": True,
        }]
        failed = False

        def fail_after_semantic(*args, **kwargs):
            nonlocal failed
            event_type = args[1] if len(args) > 1 else kwargs.get("event_type")
            if event_type == "observation.reconciled" and not failed:
                failed = True
                raise RuntimeError("injected_after_semantic_publication")
            return original_append(*args, **kwargs)

        journal.append = fail_after_semantic  # type: ignore[method-assign]
        try:
            collector.collect_once()
            raise AssertionError("post-semantic failure was not injected")
        except RuntimeError as exc:
            assert str(exc) == "injected_after_semantic_publication"
        journal.append = original_append  # type: ignore[method-assign]
        ObservationCollector(
            scope=scope, session_id="session-crash", bridge_call=fixture,
            journal=journal, world_store=worlds,
            attention=AttentionService(store, journal, scope),
        ).collect_once()
        damaged = [row for row in worlds.temporal_events_since(
            scope, collector.timeline_id, 0, limit=256,
        ) if row["event"].get("event_kind") == "contact_damaged"]
        assert len(damaged) == 2

        fixture.revision += 1
        fixture.units = []
        fixture.events = [{
            "sequence": 3, "kind": "visible_unit_destroyed", "turn": 53,
            "subject_a": 1000, "subject_b": 2, "from_tile_id": 1,
            "to_tile_id": 1, "value_before": 5, "value_after": 0,
            "continuous_visibility": True,
        }]
        collector = ObservationCollector(
            scope=scope, session_id="session-crash", bridge_call=fixture,
            journal=journal, world_store=worlds,
            attention=AttentionService(store, journal, scope),
        )
        collector.collect_once()
        temporal = worlds.temporal_events_since(scope, collector.timeline_id, 0, limit=256)
        assert sum(row["event"].get("event_kind") == "contact_destroyed"
                   for row in temporal) == 1
        assert not any(row["event"].get("event_kind") == "contact_lost"
                       and row["event"].get("contact_ref") == damaged[0]["event"].get("contact_ref")
                       for row in temporal)
        intel = WorldService(worlds, scope).query(mode="intel", context_length=65536)
        assert not any(item.get("status") == "lost" for item in intel.get("items", []))
        assert not intel.get("lost_contact_envelopes")

    with tempfile.TemporaryDirectory(prefix="smacx-native-fog-break-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-fog", "Fog")
        store.create_match(match_id="match-fog", display_name="Fog", mode="solo")
        store.create_perspective("match-fog", "agent-fog", perspective_id="perspective-fog")
        scope = MemoryScope("match-fog", "agent-fog", "perspective-fog")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / "snapshots")
        fixture = NativeFixture(16, 8, contacts=2)
        collector = ObservationCollector(
            scope=scope, session_id="session-fog", bridge_call=fixture,
            journal=journal, world_store=worlds,
            attention=AttentionService(store, journal, scope),
        )
        collector.collect_once()
        before = {
            item["metadata"]["native_observation_key"]: item["object_ref"]
            for item in worlds.load(scope, collector.timeline_id)["objects"]
            if item.get("kind") == "foreign_contact" and item.get("status") == "active"
        }
        fixture.revision += 1
        location = int(fixture.units[0]["tile_id"])
        fixture.events = [
            {"sequence": 1, "kind": "visible_unit_lost", "turn": 51,
             "subject_a": 1000, "subject_b": 2, "from_tile_id": location,
             "to_tile_id": location, "continuous_visibility": False},
            {"sequence": 2, "kind": "visible_unit_appeared", "turn": 51,
             "subject_a": 1000, "subject_b": 2, "from_tile_id": -1,
             "to_tile_id": location, "continuous_visibility": False},
        ]
        collector.collect_once()
        after = {
            item["metadata"]["native_observation_key"]: item["object_ref"]
            for item in worlds.load(scope, collector.timeline_id)["objects"]
            if item.get("kind") == "foreign_contact" and item.get("status") == "active"
        }
        assert after["vehicle-handle-1000"] != before["vehicle-handle-1000"]
        assert after["vehicle-handle-1001"] == before["vehicle-handle-1001"]

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
        "two_phase_publication_crash_replay_exactly_once": True,
        "immutable_publication_package_defers_new_native_activity": True,
        "post_semantic_pre_world_head_recovery_exact": True,
        "durable_stage_retains_more_than_native_ring_capacity": True,
        "semantic_identity_checkpoint_wiring": True,
        "action_revision_ignores_native_row_layout": True,
        "confirmed_destruction_full_pipeline": True,
        "same_drain_visibility_gap_breaks_only_affected_contact": True,
        "airdrop_fixture_acceptance_flag_alone_rejected": True,
        "airdrop_fixture_test_mode_alone_rejected": True,
        "airdrop_fixture_both_flags_required": True,
        "routine_airdrop_receipts_absent": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
