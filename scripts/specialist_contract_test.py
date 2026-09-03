#!/usr/bin/env python3
"""Deterministic mission/attempt, dependency, isolation, and lifecycle gates."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_specialists import SpecialistError, SpecialistService, system_prompt
from smacx_store import MemoryScope, SmacxStore
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, material_hash


def projected(identity: WorldIdentity, *, home_population: int = 2,
              extra_tile: bool = False):
    return PerspectiveProjector(identity).project({
        "turn": 1, "map": {"width": 8, "height": 4, "horizontal_wrap": False},
        "tiles": [
            {"tile_id": 0, "x": 0, "y": 0, "visible_now": True, "terrain": "land"},
            *([{"tile_id": 1, "x": 2, "y": 0, "visible_now": True,
                "terrain": "land"}] if extra_tile else []),
        ],
        "bases": [{"id": 1, "base_ref": "base-home", "tile_id": 0,
                   "owned": True, "name": "Home", "population": home_population}],
        "units": [], "factions": [], "global": [],
    }, observation_sequence=home_population)


def result(mission_id: str, citation: str = "") -> dict:
    return {
        "mission_id": mission_id,
        "answer": "Bounded mechanical evidence only.",
        "claims": ([{"claim": "Home remains observed.", "citations": [citation],
                     "epistemic_status": "current"}] if citation else []),
        "limitations": [], "unresolved_questions": [],
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-specialist-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-specialist", "Specialist")
        store.create_match(match_id="match-specialist", display_name="Test", mode="solo")
        store.create_perspective("match-specialist", "agent-specialist",
                                 perspective_id="perspective-specialist")
        scope = MemoryScope("match-specialist", "agent-specialist", "perspective-specialist")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        identity = WorldIdentity(scope.match_id, scope.perspective_id,
                                 journal.timeline_id(scope), "world-specialist")
        worlds = WorldStore(store, root / "snapshots")
        initial = projected(identity)
        worlds.replace_projection(scope, identity, initial["objects"], observation_cursor=1,
                                  action_revision="a", continuity="complete",
                                  journal_head_hash="0" * 64)
        service = SpecialistService(store, worlds, scope, journal=journal,
                                    attention=AttentionService(store, journal, scope))
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO control_settings(setting_key,value_json,updated_unix) "
                "VALUES('specialist.policy',?,0)",
                (json.dumps({"installation_concurrency": 4, "seat_concurrency": 2}),),
            )

        first = service.commission(faculty="world", objective="Inspect Home",
                                   subject_refs=["base-home"])
        duplicate = service.commission(faculty="world", objective="Inspect Home",
                                       subject_refs=["base-home"])
        assert duplicate["deduplicated"] and duplicate["mission_id"] == first["mission_id"]
        second = service.commission(faculty="reference", objective="Research sensors")
        third = service.commission(faculty="world", objective="Third queued mission")
        assert third["status"] == "mission_pending"

        with store._connect() as connection:
            first_row = dict(connection.execute(
                "SELECT * FROM specialist_missions WHERE mission_id=?",
                (first["mission_id"],)).fetchone())
            assert connection.execute(
                "SELECT COUNT(*) FROM world_snapshot_pins WHERE snapshot_id=?",
                (first_row["world_snapshot_id"],)).fetchone()[0] == 1

        attempt = service.begin_attempt(first["mission_id"], "test-runtime")
        objects = {item["object_ref"]: item for item in
                   worlds.load(scope, identity.timeline_id)["objects"]}
        service.record_dependencies(attempt["attempt_id"], 1, [{
            "kind": "world_object", "ref": "base-home",
            "hash": material_hash(objects["base-home"]),
        }])
        unrelated = projected(identity, extra_tile=True)
        worlds.replace_projection(scope, identity, unrelated["objects"], observation_cursor=2,
                                  action_revision="b", continuity="complete",
                                  journal_head_hash="1" * 64)
        accepted = service.accept_attempt(
            first["mission_id"], attempt["attempt_id"],
            result(first["mission_id"], "base-home"),
            usage={"provider_calls": 2, "provider_tokens": 700,
                   "peak_context_tokens": 1200},
        )
        assert accepted["ok"] and accepted["status"] == "accepted"
        assert service.attention.pending_summary()["count"] == 1
        with store._connect() as connection:
            published = connection.execute(
                "SELECT completion_journal_sequence FROM specialist_missions WHERE mission_id=?",
                (first["mission_id"],),
            ).fetchone()
        journal_events = journal.events_after(scope, limit=500)
        published_event = next(event for event in journal_events
                               if event["event_id"] == accepted["journal_event_id"])
        assert int(published["completion_journal_sequence"]) == int(published_event["sequence"])
        result_events_before = sum(event["event_type"] == "specialist.result_accepted"
                                   for event in journal_events)
        # Emulate a crash after the idempotent journal append but before the
        # SQLite projection was finalized. Startup repair must neither lose nor
        # duplicate the canonical publication.
        with store.transaction() as connection:
            connection.execute(
                "UPDATE specialist_missions SET status='active',"
                "completion_journal_sequence=NULL WHERE mission_id=?",
                (first["mission_id"],),
            )
            connection.execute(
                "UPDATE specialist_attempts SET status='validating' WHERE attempt_id=?",
                (attempt["attempt_id"],),
            )
        assert service.reconcile_prepared_results() == 1
        assert service.get(first["mission_id"])["status"] == "accepted"
        assert sum(
            event["event_type"] == "specialist.result_accepted"
            for event in journal.events_after(scope, limit=500)
        ) == result_events_before

        reference_attempt = service.begin_attempt(second["mission_id"], "test-runtime")
        service.record_dependencies(reference_attempt["attempt_id"], 1, [{
            "kind": "reference_document", "ref": "doc-sensors", "hash": "d" * 64,
        }])
        os.environ["SMACX_CORPUS_REVISION"] = "new-corpus"
        stale_reference = service.accept_attempt(
            second["mission_id"], reference_attempt["attempt_id"],
            result(second["mission_id"], "doc-sensors"), usage={},
        )
        assert stale_reference["status"] == "stale"
        os.environ.pop("SMACX_CORPUS_REVISION", None)

        changed_mission = service.commission(
            faculty="world", objective="Track Home population",
            subject_refs=["base-home"],
        )
        changed_attempt = service.begin_attempt(changed_mission["mission_id"], "test-runtime")
        objects = {item["object_ref"]: item for item in
                   worlds.load(scope, identity.timeline_id)["objects"]}
        service.record_dependencies(changed_attempt["attempt_id"], 1, [{
            "kind": "world_object", "ref": "base-home",
            "hash": material_hash(objects["base-home"]),
        }])
        changed = projected(identity, home_population=3, extra_tile=True)
        worlds.replace_projection(scope, identity, changed["objects"], observation_cursor=3,
                                  action_revision="c", continuity="complete",
                                  journal_head_hash="2" * 64)
        stale = service.accept_attempt(
            changed_mission["mission_id"], changed_attempt["attempt_id"],
            result(changed_mission["mission_id"], "base-home"), usage={},
        )
        assert stale["status"] == "stale"
        try:
            service.accept_attempt(
                changed_mission["mission_id"], changed_attempt["attempt_id"],
                result(changed_mission["mission_id"], "base-home"), usage={},
            )
            raise AssertionError("late duplicate publication succeeded")
        except SpecialistError as exc:
            assert str(exc) == "specialist_late_result_rejected"

        operation = service.attention.upsert_operation(
            operation_id=None, kind="defense_review", objective="Review Home defense",
            referenced_world_objects=("base-home",),
            source_world_revision=3, source_world_epoch=identity.world_epoch,
            source_dependency_hash=material_hash({"base-home": 3}),
            current_turn=1,
        )
        linked = service.commission(
            faculty="world", objective="Operation-linked Home analysis",
            subject_refs=("base-home",), operation_id=operation["operation_id"],
        )
        cancelled_at_handoff = service.cancel_for_turn_handoff()
        assert cancelled_at_handoff == 1
        assert service.get(third["mission_id"])["status"] == "cancelled"
        assert service.get(linked["mission_id"])["status"] == "mission_pending"
        cancelled_for_operation = service.cancel_for_operation(operation["operation_id"])
        assert cancelled_for_operation == 1
        assert service.get(linked["mission_id"])["status"] == "cancelled"

        epoch_mission = service.commission(
            faculty="world", objective="Must not outlive its world epoch",
        )
        epoch_attempt = service.begin_attempt(epoch_mission["mission_id"], "test-runtime")
        new_identity = WorldIdentity(
            scope.match_id, scope.perspective_id, identity.timeline_id, "world-replaced",
        )
        replaced = projected(new_identity, home_population=3, extra_tile=True)
        worlds.replace_projection(
            scope, new_identity, replaced["objects"], observation_cursor=4,
            action_revision="d", continuity="complete", journal_head_hash="3" * 64,
        )
        try:
            service.accept_attempt(
                epoch_mission["mission_id"], epoch_attempt["attempt_id"],
                result(epoch_mission["mission_id"]), usage={},
            )
            raise AssertionError("world-epoch-invalidated result published")
        except SpecialistError as exc:
            assert str(exc) == "specialist_late_result_rejected"
        assert service.get(epoch_mission["mission_id"])["status"] == "cancelled"
        with store._connect() as connection:
            epoch_row = connection.execute(
                "SELECT cancellation_reason,result_json FROM specialist_missions "
                "WHERE mission_id=?", (epoch_mission["mission_id"],),
            ).fetchone()
        assert tuple(epoch_row) == ("cancelled_by_world_epoch", None)

        timeline_mission = service.commission(
            faculty="reference", objective="Must not outlive its timeline",
        )
        timeline_attempt = service.begin_attempt(
            timeline_mission["mission_id"], "test-runtime",
        )
        with store.transaction() as connection:
            connection.execute(
                "UPDATE matches SET metadata_json=? WHERE match_id=?",
                (json.dumps({"active_memory_timeline": "timeline-restored"}), scope.match_id),
            )
        try:
            service.accept_attempt(
                timeline_mission["mission_id"], timeline_attempt["attempt_id"],
                result(timeline_mission["mission_id"]), usage={},
            )
            raise AssertionError("timeline-invalidated result published")
        except SpecialistError as exc:
            assert str(exc) == "specialist_late_result_rejected"
        assert service.get(timeline_mission["mission_id"])["status"] == "cancelled"

        for faculty in ("reference", "world"):
            _, prompt = system_prompt(faculty)
            lowered = prompt.casefold()
            assert "disposable" in lowered and "sovereign" in lowered
            assert "terminal" in lowered or "files" in lowered
        with store._connect() as connection:
            columns = {row[1] for row in connection.execute(
                "PRAGMA table_info('specialist_attempts')")}
            assert "session_state" not in columns and "conversation_json" not in columns

    print(json.dumps({"event": "pass", "payload": {
        "mission_attempt_split": True, "bounded_durable_queue": True,
        "idempotent_commission": True, "immutable_snapshot_pin": True,
        "platform_dependencies": True, "unrelated_change_accepted": True,
        "relevant_change_stale": True, "corpus_change_stale": True,
        "late_result_cas_rejected": True, "completion_attention": True,
        "specialist_prompt_boundary": True, "journal_publication_recovery": True,
        "turn_handoff_cancels_unlinked_only": True,
        "operation_completion_cancels_linked": True,
        "world_epoch_late_publish_rejected": True,
        "timeline_late_publish_rejected": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
