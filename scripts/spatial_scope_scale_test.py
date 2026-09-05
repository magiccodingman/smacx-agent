#!/usr/bin/env python3
"""End-to-end scope creation/inspection cost over quiet perspective projections."""

import json
from pathlib import Path
import tempfile
import time

from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_regions import Region, PHYSICAL_LAND_PROFILE
from smacx_store import MemoryScope, SmacxStore
from smacx_world_model import estimate_tokens
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, WorldObject, EpistemicValue, EpistemicStatus, EvidenceSource


def measure(width, height):
    with tempfile.TemporaryDirectory(prefix="smacx-scope-scale-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-scale", "Scale")
        store.create_match(match_id="match-scale", display_name="Scale", mode="solo")
        store.create_perspective("match-scale", "agent-scale", perspective_id="perspective-scale")
        scope = MemoryScope("match-scale", "agent-scale", "perspective-scale")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        world = WorldStore(store)
        identity = WorldIdentity(scope.match_id, scope.perspective_id, store.active_timeline_id(scope), "world-scale")
        def field(value):
            return EpistemicValue(value, EpistemicStatus.CURRENT, EvidenceSource.DIRECT_SIGHT, 1, 1, 1, "fixture-scale")
        terrain = field("land")
        rows = [WorldObject(f"location-{(y * width + x)//2}", "location", {"terrain": terrain},
                            metadata={"native_x": x, "native_y": y})
                for y in range(height) for x in range(y % 2, width, 2)]
        refs = frozenset(row.object_ref for row in rows)
        rows.append(WorldObject("world-map", "map_state", {"width": field(width), "height": field(height),
                                                              "horizontal_wrap": field(False)}))
        world.replace_projection(scope, identity, rows, observation_cursor=1, action_revision="r1",
                                 continuity="complete", journal_head_hash="0" * 64)
        world.save_regions(scope, identity.timeline_id, [Region("region-whole-land", "region-land", 1,
                           PHYSICAL_LAND_PROFILE, "location-0", refs)], 1)
        attention = AttentionService(store, journal, scope)
        started = time.perf_counter()
        created = attention.create_watch("spatial_scope", ["region-whole-land"], {"type": "geography"}, current_turn=1)
        created_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        descriptor = attention.inspect_scope(created["watch_id"])
        inspected_ms = (time.perf_counter() - started) * 1000
        assert descriptor["known_coverage_count"] == len(refs)
        assert "_location_refs" not in json.dumps(descriptor)
        assert estimate_tokens(descriptor) < 250
        with store._connect() as connection:
            stored_bytes = len(connection.execute("SELECT typed_predicate_json FROM world_watches WHERE watch_id=?",
                                                  (created["watch_id"],)).fetchone()[0].encode())
        assert stored_bytes < 1600
        return {"tiles": len(refs), "create_ms": round(created_ms, 3),
                "inspect_ms": round(inspected_ms, 3), "descriptor_tokens": estimate_tokens(descriptor),
                "private_watch_definition_bytes": stored_bytes}


if __name__ == "__main__":
    results = [measure(40, 20), measure(400, 200), measure(512, 256)]
    assert results[1]["tiles"] == 100 * results[0]["tiles"]
    assert results[1]["descriptor_tokens"] - results[0]["descriptor_tokens"] <= 4
    print(json.dumps({"ok": True, "scope_service_scale": results}))
