#!/usr/bin/env python3
"""Huge-world disposable specialist snapshots remain storage bounded."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_journal import CampaignJournal
from smacx_specialists import SpecialistService
from smacx_store import MemoryScope, SmacxStore
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-specialist-snapshot-scale-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-snapshot-scale", "Snapshot Scale")
        store.create_match(match_id="match-snapshot-scale", display_name="Scale", mode="solo")
        store.create_perspective(
            "match-snapshot-scale", "agent-snapshot-scale",
            perspective_id="perspective-snapshot-scale",
        )
        scope = MemoryScope(
            "match-snapshot-scale", "agent-snapshot-scale", "perspective-snapshot-scale",
        )
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        identity = WorldIdentity(
            scope.match_id, scope.perspective_id, journal.timeline_id(scope), "world-huge-scale",
        )
        tiles = [
            {"tile_id": (x + 160 * y) // 2, "x": x, "y": y,
             "visible_now": y < 8, "terrain": "land", "features": []}
            for y in range(80) for x in range(y & 1, 160, 2)
        ]
        projection = PerspectiveProjector(identity).project({
            "turn": 50, "year": 2150, "action_revision": "scale",
            "map": {"width": 160, "height": 80, "horizontal_wrap": True},
            "tiles": tiles,
            "bases": [{"id": 0, "base_ref": "base-scale", "tile_id": 0,
                       "owned": True, "name": "Scale Base", "population": 7}],
            "units": [], "factions": [], "global": [],
        }, observation_sequence=1)
        worlds = WorldStore(store, root / "snapshots")
        worlds.replace_projection(
            scope, identity, projection["objects"], observation_cursor=1,
            action_revision="scale", continuity="complete", journal_head_hash="0" * 64,
        )
        service = SpecialistService(store, worlds, scope, journal=journal)

        peak_rows = 0
        peak_bytes = 0
        for index in range(24):
            mission = service.commission(
                faculty="world", objective=f"Inspect Huge world concern {index}",
                subject_refs=["base-scale"],
            )
            duplicate = service.commission(
                faculty="world", objective=f"Inspect Huge world concern {index}",
                subject_refs=["base-scale"],
            )
            assert duplicate["deduplicated"] and duplicate["mission_id"] == mission["mission_id"]
            with store._connect() as connection:
                rows = int(connection.execute("SELECT COUNT(*) FROM world_snapshots").fetchone()[0])
                pins = int(connection.execute("SELECT COUNT(*) FROM world_snapshot_pins").fetchone()[0])
            bytes_now = sum(path.stat().st_size for path in (root / "snapshots").rglob("*.json"))
            peak_rows = max(peak_rows, rows)
            peak_bytes = max(peak_bytes, bytes_now)
            assert rows == 1 and pins == 1
            service.cancel(mission["mission_id"], "cancelled_by_parent")
            with store._connect() as connection:
                assert connection.execute("SELECT COUNT(*) FROM world_snapshots").fetchone()[0] == 0
                assert connection.execute("SELECT COUNT(*) FROM world_snapshot_pins").fetchone()[0] == 0
            assert not list((root / "snapshots").rglob("*.json"))

        # Simulate a process death after atomic snapshot+pin publication but
        # before the mission row commits. Supervisor reconciliation must
        # release the orphan and collect its content.
        manifest = journal.replay(scope)["manifest"]
        orphan = worlds.snapshot(
            scope, identity, journal_head_hash=str(manifest["head_hash"]),
            journal_sequence=int(manifest["sequence"]),
            calculator_versions={"world": "scale"},
            pin_owner=("specialist_mission", "mission-never-committed"),
        )
        assert Path(orphan["path"]).exists()
        assert worlds.gc_orphaned_specialist_snapshot_pins() == 1
        assert not Path(orphan["path"]).exists()

        print(json.dumps({"event": "pass", "payload": {
            "huge_known_tiles": len(tiles), "missions": 24,
            "peak_snapshot_rows": peak_rows, "peak_snapshot_bytes": peak_bytes,
            "final_snapshot_rows": 0, "final_snapshot_bytes": 0,
            "idempotent_commission_does_not_leak_snapshot": True,
            "orphaned_precommit_pin_reconciled": True,
        }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
