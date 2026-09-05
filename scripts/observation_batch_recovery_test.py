#!/usr/bin/env python3
"""Frozen journal publication survives either side of a batched cache commit."""
import json
import tempfile
from pathlib import Path

from observation_collector_benchmark import (
    NativeFixture, SmacxStore, MemoryScope, CampaignJournal, WorldStore,
    ObservationCollector, AttentionService,
)


def main():
    results = []
    for window in ("before_commit", "during_commit", "after_commit"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = SmacxStore(root / "state.sqlite3")
            store.ensure_agent("agent-batch", "Batch")
            store.create_match(match_id="match-batch", display_name="Batch", mode="solo")
            store.create_perspective("match-batch", "agent-batch", perspective_id="perspective-batch")
            scope = MemoryScope("match-batch", "agent-batch", "perspective-batch")
            journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
            worlds = WorldStore(store, root / "snapshots")
            fixture = NativeFixture(32, 16)

            def collector():
                return ObservationCollector(scope=scope, session_id="session-batch",
                    bridge_call=fixture, journal=journal, world_store=worlds,
                    attention=AttentionService(store, journal, scope))

            original = worlds.record_observation_projections
            batches = []

            def crash(scope, timeline, rows):
                rows = list(rows)
                assert len(rows) >= 2, "fixture must exercise a multi-row cache commit"
                batches.extend(rows)
                if window == "after_commit":
                    original(scope, timeline, rows)
                elif window == "during_commit":
                    def partial_rows():
                        yield rows[0]
                        raise RuntimeError("injected_cache_commit_failure")
                    original(scope, timeline, partial_rows())
                raise RuntimeError("injected_cache_commit_failure")

            worlds.record_observation_projections = crash
            try:
                collector().collect_once()
                raise AssertionError("cache failure was not injected")
            except RuntimeError as error:
                assert str(error) == "injected_cache_commit_failure"
            assert batches and journal.verify(scope)["ok"]
            timeline = journal.timeline_id(scope)
            cached = worlds.changes_since(scope, timeline, 0, limit=512)
            assert not cached, "uninstalled publication became provider-visible"
            with store._connect() as connection:
                raw_count = connection.execute("SELECT COUNT(*) FROM world_observation_projection").fetchone()[0]
            assert bool(raw_count) == (window == "after_commit")
            worlds.record_observation_projections = original
            recovered = collector().collect_once()
            count = recovered["collector_metrics"]["world_objects"]
            assert len(journal.replay(scope)["world_objects"]) == count
            assert len(worlds.changes_since(scope, timeline, 0, limit=512)) == count
            events = [json.loads(path.read_text()) for path in
                      (journal.perspective_root(scope, timeline) / "events").glob("*.json")]
            keys = [event["idempotency_key"] for event in events if event.get("idempotency_key")]
            assert len(keys) == len(set(keys)), "recovery duplicated canonical events"
            assert journal.verify(scope)["ok"]
            results.append({"crash_window": window, "journal_and_cache_objects": count,
                            "canonical_events_not_duplicated": True})
    print(json.dumps({"passed": True, "windows": results}))


if __name__ == "__main__":
    main()
