#!/usr/bin/env python3
"""Production-shaped observation collector latency and write-amplification gate."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_observation import ObservationCollector
from smacx_store import MemoryScope, SmacxStore
from smacx_world_store import WorldStore


class NativeFixture:
    def __init__(self, width: int, height: int, *, contacts: int = 0,
                 bases: int = 1, events: int = 0,
                 continuity_incomplete: bool = False) -> None:
        self.width, self.height = width, height
        self.revision = 1
        self.calls = 0
        self.continuity_incomplete = continuity_incomplete
        self.tiles = [
            {"tile_id": (x + width * y) // 2, "x": x, "y": y,
             "visible_now": y < max(2, height // 8),
             "terrain": "ocean" if y == height - 1 else "land",
             "features": ["road"] if y == 2 else []}
            for y in range(height) for x in range(y & 1, width, 2)
        ]
        self.bases = [
            {"id": index, "base_ref": f"base-{index}",
             "tile_id": self.tiles[(index * 97) % len(self.tiles)]["tile_id"],
             "owned": index % 2 == 0, "visible_now": True,
             "owner_ref": f"faction-{1 if index % 2 == 0 else 2}",
             "name": f"Base {index}", "population": 3 + index % 8,
             "mineral_surplus": 4}
            for index in range(bases)
        ]
        self.units = [
            {"id": index, "own_unit_ref": f"own-unit-{1000 + index}",
             "native_observation_key": f"vehicle-handle-{1000 + index}",
             "tile_id": self.tiles[(index * 79 + 1) % len(self.tiles)]["tile_id"],
             "owned": False, "owner_ref": f"faction-{2 + index % 5}",
             "name": "Observed Unit", "hp": 10, "max_hp": 10,
             "triad": "land", "movement_points": 3, "movement_scale": 3,
             "roles": {"combat": True}}
            for index in range(contacts)
        ]
        self.factions = [
            {"id": index, "faction_ref": f"faction-{index}",
             "owned": index == 1, "faction_name": f"Faction {index}",
             "relations": {"vendetta": index == 2}}
            for index in range(1, 8)
        ]
        self.events = [
            {"sequence": index + 1, "kind": "known_tile_changed", "turn": 50,
             "subject_a": index % len(self.tiles),
             "from_tile_id": index % len(self.tiles),
             "to_tile_id": index % len(self.tiles), "continuous_visibility": True}
            for index in range(events)
        ]

    def __call__(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        revision = f"benchmark-{self.revision}"
        if operation == "observation_feed":
            after = int(kwargs.get("after_sequence", 0))
            limit = int(kwargs.get("limit", 256))
            page = [row for row in self.events if int(row["sequence"]) > after][:limit]
            cursor = int(page[-1]["sequence"]) if page else after
            return {"ok": True, "events": page, "next_sequence": cursor,
                    "has_more": cursor < len(self.events),
                    "continuity": "incomplete" if self.continuity_incomplete and after == 0
                    else "complete",
                    "lost_after_observation_sequence": 0 if self.continuity_incomplete else None,
                    "action_revision": revision}
        if operation == "perspective_world_page":
            domain = str(kwargs["domain"])
            cursor, limit = int(kwargs.get("cursor", 0)), int(kwargs.get("limit", 128))
            if domain == "summary":
                return {"ok": True, "items": [], "next_cursor": None,
                        "action_revision": revision, "turn": 50, "year": 2150,
                        "faction_id": 1,
                        "map": {"width": self.width, "height": self.height,
                                "horizontal_wrap": True}}
            values = {"tiles": self.tiles, "bases": self.bases,
                      "units": self.units, "factions": self.factions}[domain]
            page = values[cursor:cursor + limit]
            next_cursor = cursor + limit if cursor + limit < len(values) else None
            return {"ok": True, "items": page, "next_cursor": next_cursor,
                    "action_revision": revision}
        if operation in {"list_bases", "list_units"}:
            values = self.bases if operation == "list_bases" else []
            offset, limit = int(kwargs.get("offset", 0)), int(kwargs.get("limit", 200))
            page = values[offset:offset + limit]
            next_offset = offset + limit if offset + limit < len(values) else -1
            return {"ok": True, "items": page, "next_offset": next_offset}
        if operation == "list_factions":
            return {"ok": True, "items": self.factions}
        if operation == "list_technologies":
            return {"ok": True, "items": [{"technology_ref": "technology-1",
                                              "name": "Doctrine: Mobility"}]}
        if operation == "semantic_snapshot":
            return {"ok": True, "snapshot": {
                "revision": revision,
                "game_settings": {"turn_clock": "none", "map_size": "huge"},
                "scenario": {"name": "benchmark"},
                "economy": {"energy": 100}, "research": {"labs": 20},
                "social_engineering": {}, "public_projects": [],
                "known_project_races": [], "own_orbitals": {},
                "governor_faction_id": -1, "movement_rules": {"road_rate": 3},
                "ecology": {}, "victory_posture": {},
            }}
        raise AssertionError(f"unexpected native operation: {operation}")


def run_case(name: str, width: int, height: int, *, contacts: int = 0,
             bases: int = 1, events: int = 0,
             continuity_incomplete: bool = False) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"smacx-collector-{name}-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-benchmark", "Collector Benchmark")
        store.create_match(match_id="match-benchmark", display_name=name, mode="solo")
        store.create_perspective("match-benchmark", "agent-benchmark",
                                 perspective_id="perspective-benchmark")
        scope = MemoryScope("match-benchmark", "agent-benchmark", "perspective-benchmark")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / "snapshots")
        fixture = NativeFixture(
            width, height, contacts=contacts, bases=bases, events=events,
            continuity_incomplete=continuity_incomplete,
        )
        collector = ObservationCollector(
            scope=scope, session_id="session-benchmark", bridge_call=fixture,
            journal=journal, world_store=worlds,
            attention=AttentionService(store, journal, scope),
        )
        ticks: list[float] = []
        stop = threading.Event()

        def responsiveness_probe() -> None:
            while not stop.wait(0.01):
                ticks.append(time.perf_counter())

        probe = threading.Thread(target=responsiveness_probe, daemon=True)
        probe.start()
        initial = collector.collect_once()
        replayed = journal.replay(scope)
        assert len(replayed["world_objects"]) == initial["collector_metrics"]["world_objects"]
        assert len(worlds.changes_since(scope, collector.timeline_id, 0, limit=512)) == min(
            512, int(initial["collector_metrics"]["material_deltas"]),
        )
        assert len(worlds.temporal_events_since(
            scope, collector.timeline_id, 0, limit=256,
        )) == min(256, int(initial["collector_metrics"]["semantic_events"]))
        fixture.events = []
        fixture.revision += 1
        unchanged = collector.collect_once()
        stop.set()
        probe.join(1)
        gaps = [(right - left) * 1000 for left, right in zip(ticks, ticks[1:])]
        metrics = initial["collector_metrics"]
        stable_metrics = unchanged["collector_metrics"]
        assert metrics["bridge_calls"] == fixture.calls - stable_metrics["bridge_calls"]
        assert stable_metrics["projection_object_rows_written"] == 0
        assert stable_metrics["material_deltas"] == 0
        assert metrics["native_events_drained"] == events
        assert metrics["native_backlog_after"] == 0
        return {
            "case": name, "known_tiles": len(fixture.tiles), "contacts": contacts,
            "bases": bases, "initial": metrics, "unchanged": stable_metrics,
            "ui_probe_max_gap_ms": round(max(gaps, default=0.0), 3),
            "sqlite_bytes": (root / "state.sqlite3").stat().st_size,
            "journal_replay_object_count": len(replayed["world_objects"]),
        }


def main() -> int:
    cases = [
        run_case("small_quiet", 32, 16),
        run_case("stock_huge_quiet", 160, 80),
        run_case("stock_huge_active", 160, 80, contacts=300, bases=80),
        run_case("large_custom_quiet", 320, 160),
        run_case("action_dense_overflow", 64, 32, contacts=80, bases=24,
                 events=768, continuity_incomplete=True),
    ]
    by_name = {row["case"]: row for row in cases}
    assert by_name["stock_huge_quiet"]["initial"]["wall_ms"] < 30_000
    assert by_name["stock_huge_active"]["initial"]["wall_ms"] < 30_000
    assert by_name["large_custom_quiet"]["initial"]["wall_ms"] < 30_000
    assert by_name["action_dense_overflow"]["initial"]["native_feed_pages"] == 3
    assert by_name["action_dense_overflow"]["initial"]["native_continuity_incomplete"]
    assert all(row["ui_probe_max_gap_ms"] < 500 for row in cases)
    print(json.dumps({"event": "pass", "payload": {
        "production_collector_pipeline": True,
        "incremental_projection_writes": True,
        "bounded_native_backlog": True,
        "independent_ui_responsiveness_probe": True,
        "cases": cases,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
