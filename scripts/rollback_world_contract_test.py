#!/usr/bin/env python3
"""Rollback coherence across journal-derived world, attention, scopes, and caches."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_specialists import SpecialistService
from smacx_store import MemoryScope, SmacxStore
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, content_hash


def bundle(turn: int, *, future: bool) -> dict:
    tiles = [{"tile_id": 0, "x": 0, "y": 0, "terrain": "land", "visible_now": True},
             {"tile_id": 1, "x": 2, "y": 0, "terrain": "land", "visible_now": True}]
    return {"turn": turn, "year": 2100 + turn, "action_revision": f"r{turn}",
            "map": {"width": 8, "height": 4, "horizontal_wrap": False},
            "tiles": tiles,
            "bases": [{"id": 0, "base_ref": "base-home", "tile_id": 0,
                       "owned": True, "name": "Home", "population": 2}],
            "units": ([{"id": 1, "own_unit_ref": "own-unit-1", "tile_id": 0,
                        "owned": True, "name": "Scout"}] +
                      ([{"id": 99, "native_observation_key": "future-hidden-99",
                         "tile_id": 1, "owned": False, "name": "Future Contact"}]
                       if future else [])),
            "factions": [{"id": 1, "faction_ref": "faction-1", "owned": True}]}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-rollback-world-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-rollback", "Rollback")
        store.create_match(match_id="match-rollback", display_name="Rollback", mode="solo")
        store.create_perspective("match-rollback", "agent-rollback",
                                 perspective_id="perspective-rollback")
        scope = MemoryScope("match-rollback", "agent-rollback", "perspective-rollback")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / "snapshots")
        identity = WorldIdentity(scope.match_id, scope.perspective_id,
                                 journal.timeline_id(scope), "world-rollback")
        projected = PerspectiveProjector(identity).project(bundle(5, future=False),
                                                            observation_sequence=5)
        worlds.replace_projection(scope, identity, projected["objects"], observation_cursor=5,
                                  action_revision="r5", continuity="complete",
                                  journal_head_hash="0" * 64)
        checkpoint = journal.append(scope, "checkpoint.native", {"turn": 5}, turn=5)
        snapshot = worlds.snapshot(scope, identity,
                                   journal_head_hash=checkpoint["event_hash"],
                                   journal_sequence=checkpoint["sequence"],
                                   calculator_versions={"world": "v1"})
        worlds.pin_snapshot(
            snapshot["snapshot_id"], "checkpoint", checkpoint["event_id"],
        )

        attention = AttentionService(store, journal, scope)
        attention.enqueue("chat", {"message": {"message_uid": "future-chat"}},
                          observation_cursor=6, priority=80, critical=True)
        attention.create_watch("base_threat", ["base-home"],
                               {"field": "threatened", "equals": True}, current_turn=6)
        future_scope = attention.create_watch("spatial_scope", ["base-home"],
                                             {"type": "proximity", "radius": 2}, current_turn=6)
        future_plan = store.put_plan(scope, "future-staging", "Future staging", "Future intent")
        journal.append(scope, "memory.plan", {"record": future_plan})
        future_milestone = attention.create_watch("milestone", ["base-home"],
                                                  {"requirements": [{"ref": "base-home", "kind": "exists"}]},
                                                  current_turn=6, linked_plan_id=future_plan["plan_id"])
        operation_refs = ["base-home"]
        dependencies = attention.semantic_dependency_hashes()
        attention.upsert_operation(
            operation_id=None, kind="future", objective="Future branch",
            referenced_world_objects=operation_refs, source_world_revision=1,
            source_world_epoch="world-rollback", source_dependency_hash=content_hash({
                ref: dependencies[ref] for ref in operation_refs
            }),
            current_turn=6)
        future_projection = PerspectiveProjector(identity, prior_projection=worlds.load(
            scope, identity.timeline_id)).project(bundle(6, future=True), observation_sequence=6)
        worlds.replace_projection(scope, identity, future_projection["objects"], observation_cursor=6,
                                  action_revision="r6", continuity="complete",
                                  journal_head_hash="f" * 64)
        service = WorldService(worlds, scope)
        service.query(mode="forces", context_length=65536)
        specialist = SpecialistService(store, worlds, scope)
        mission = specialist.commission(
            faculty="world", objective="future", subject_refs=["base-home"],
        )
        specialist.begin_attempt(mission["mission_id"], "future-runtime")

        def restore(target: str) -> str:
            journal.fork_timeline(scope, target, native_save_sha256="a" * 64,
                                  from_event_hash=checkpoint["event_hash"],
                                  parent_timeline_id="timeline-main")
            with store.transaction() as connection:
                connection.execute(
                    "UPDATE matches SET metadata_json=json_set(metadata_json," \
                    "'$.active_memory_timeline',?) WHERE match_id=?", (target, scope.match_id))
            payload = worlds.verify_snapshot(snapshot["snapshot_id"],
                                             journal_head_hash=checkpoint["event_hash"],
                                             journal_sequence=checkpoint["sequence"])
            restored = worlds.restore_projection_from_snapshot(
                scope, payload, target_timeline_id=target,
                journal_head_hash=checkpoint["event_hash"])
            SpecialistService(store, worlds, scope, journal=journal).cancel_for_rollback(target)
            worlds.discard_future(scope, target)
            return str(restored["projection_checksum"])

        first_checksum = restore("timeline-restore-one")
        restored = worlds.load(scope, "timeline-restore-one")
        assert restored and restored["observation_cursor"] == 5
        serialized = json.dumps(restored)
        assert "Future Contact" not in serialized and "future-hidden-99" not in serialized
        fresh_attention = AttentionService(store, journal, scope)
        assert fresh_attention.pending_summary()["count"] == 0
        assert fresh_attention.runtime_state(current_turn=5)["operations"] == []
        with store._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM world_watches").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM world_query_cache").fetchone()[0] == 0
            mission_row = connection.execute(
                "SELECT status,cancellation_reason FROM specialist_missions WHERE mission_id=?",
                (mission["mission_id"],),
            ).fetchone()
            assert tuple(mission_row) == ("cancelled", "cancelled_by_rollback")
            assert connection.execute(
                "SELECT COUNT(*) FROM specialist_attempts WHERE mission_id=? AND status='cancelled'",
                (mission["mission_id"],),
            ).fetchone()[0] == 1

        # A second restore from the same authoritative checkpoint is byte-stable.
        second_checksum = restore("timeline-restore-two")
        assert second_checksum == first_checksum
        assert journal.replay(scope)["manifest"]["timeline_id"] == "timeline-restore-two"
        print(json.dumps({"event": "pass", "payload": {
            "new_timeline_activated": True,
            "snapshot_journal_head_verified": True,
            "future_world_removed": True,
            "future_attention_watch_operation_removed": True,
            "future_cache_removed_specialist_diagnostics_retained": True,
            "repeat_restore_deterministic": True,
        }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
