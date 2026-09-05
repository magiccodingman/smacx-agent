#!/usr/bin/env python3
"""Perspective geometry, crossing attention and durable scope acceptance."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import tempfile
from unittest.mock import patch

import smacx_mcp as mcp

from smacx_attention import AttentionError, AttentionService
from smacx_journal import CampaignJournal
from smacx_spatial_scope import BASE_WORKING_OFFSETS, scope_geometry
from smacx_store import MemoryScope, SmacxStore
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, WorldObject


def main() -> int:
    # Live multi-base enumeration exposed an auxiliary-envelope collision:
    # individually small primary rows must remain pageable even when the full
    # base objects cannot accompany them in the same response.
    page = WorldService._trim({"ok": True, "mode": "base", "items": [
        {"base_ref": "base-a", "location_ref": "location-a"},
        {"base_ref": "base-b", "location_ref": "location-b"}],
        "objects": [{"object_ref": "base-a", "fields": {"description": "x" * 4000}},
                    {"object_ref": "base-b", "fields": {"description": "y" * 4000}}]}, 512)
    assert page["ok"] and page["items"][0]["base_ref"] == "base-a" and page["result_token_estimate"] <= 512
    crowded = WorldService._trim({"ok": True, "mode": "base", "items": [{
        "base_ref": "base-crowded", "garrison_refs": [f"own-unit-{i}" for i in range(200)],
        "observed_defender_count": 200,
        "friendly_response": [{"unit_ref": f"own-unit-{i}", "uncertainty": ["conditional"]} for i in range(12)]}]}, 512)
    assert crowded["ok"] and crowded["items"][0]["base_ref"] == "base-crowded"
    assert crowded["items"][0]["observed_defender_count"] == 200
    assert crowded["items"][0]["mechanics_detail_truncated"] and crowded["result_token_estimate"] <= 512
    # Compare the maintained adapter offsets to the actual native radius
    # table. This is source/adapter evidence, not a running-game yield claim.
    native = (Path(__file__).resolve().parents[1] / "bridge/src/path.h").read_text()
    axes = [list(map(int, re.findall(r"-?\d+", re.search(
        rf"const int TableOffset{axis}\[\] = \{{(.*?)\}}", native, re.S)[1])))[:21]
        for axis in ("X", "Y")]
    assert tuple(zip(*axes)) == BASE_WORKING_OFFSETS
    with tempfile.TemporaryDirectory(prefix="smacx-spatial-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-scope", "Scope")
        store.create_match(match_id="match-scope", display_name="Scope", mode="lan")
        store.create_perspective("match-scope", "agent-scope", perspective_id="perspective-scope")
        scope = MemoryScope("match-scope", "agent-scope", "perspective-scope")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store)
        identity = WorldIdentity(scope.match_id, scope.perspective_id,
                                 store.active_timeline_id(scope), "epoch-scope")
        # Continent at x<=8, narrow peninsula pointing east, coastal ocean.
        tiles = [{"tile_id": y * 24 + x, "x": x, "y": y, "visible_now": True,
                  "terrain": "land" if x <= 8 or (y == 6 and x <= 16) else "ocean"}
                 for y in range(14) for x in range(48) if (x + y) % 2 == 0]
        # Native IDs here need only be unique; use the actual half-width row stride.
        for tile in tiles:
            tile["tile_id"] = tile["y"] * 24 + tile["x"] // 2
        bundle = {"turn": 4, "map": {"width": 48, "height": 14, "horizontal_wrap": False},
                  "tiles": tiles, "bases": [], "units": [], "factions": [], "global": []}
        projected = PerspectiveProjector(identity).project(bundle, observation_sequence=1)
        objects = [item.as_dict(provider_safe=False) for item in projected["objects"]]
        center = "location-150"  # (12,6), on the peninsula.
        def contact(ref: str, relationship: str) -> dict:
            return {"object_ref": ref, "kind": "foreign_contact", "status": "active",
                    "location_ref": center, "fields": {
                        "last_seen_turn": {"value": 4, "source": "direct_sight", "epistemic_status": "current"},
                        "relationship": {"value": relationship, "source": "direct_sight", "epistemic_status": "current"}}}
        objects += [contact("contact-land", "hostile"), contact("contact-sea", "hostile"),
                    contact("contact-ally", "allied"), contact("contact-distant", "hostile")]
        def save(rows: list[dict], cursor: int) -> None:
            worlds.replace_projection(scope, identity, [WorldObject.from_dict(row) for row in rows], observation_cursor=cursor,
                                      action_revision=f"r{cursor}", continuity="complete",
                                      journal_head_hash="0" * 64)
        save(objects, 1)
        attention = AttentionService(store, journal, scope)
        service = WorldService(worlds, scope)
        with patch.object(mcp, "_managed_scope_identity", return_value=(scope.match_id, "session-scope", scope.agent_id, scope.perspective_id)), \
             patch.object(mcp, "controller_world_service", return_value=(scope, service, attention)):
            land = mcp.smac_cognition(action="scope_create", subject_refs=[center],
                                       predicate_json=json.dumps({"type": "proximity", "radius": 2, "domain": "land"}))
            assert land["ok"], land
            public = mcp.smac_cognition(action="scope_inspect", subject_refs=[land["watch_id"]])
            assert public["ok"] and public["scope"]["known_coverage_count"] > 0
        sea = attention.create_watch("spatial_scope", [center],
                                     {"type": "proximity", "radius": 2, "domain": "sea"}, current_turn=4)
        combined = attention.create_watch("spatial_scope", [land["watch_id"], sea["watch_id"]],
                                          {"type": "union"}, current_turn=4)
        descriptor = attention.inspect_scope(combined["watch_id"])
        assert descriptor["known_coverage_count"] == 25
        assert "_location_refs" not in json.dumps(combined)
        entry = attention.create_watch("region_entry", [combined["watch_id"]],
                                       {"relationship": "hostile"}, current_turn=4)
        exit_watch = attention.create_watch("region_exit", [combined["watch_id"]],
                                            {"relationship": "hostile"}, current_turn=4)
        def move(ref: str, destination: str, origin: str = "location-156") -> dict:
            return {"event_kind": "contact_moved", "contact_ref": ref,
                    "path": [{"from_location_ref": origin, "to_location_ref": destination}]}
        events = [move("contact-land", center), move("contact-sea", "location-126"),
                  move("contact-ally", center), move("contact-distant", "location-157"),
                  {"event_kind": "contact_appeared", "contact_ref": "contact-land", "location_ref": center},
                  {"event_kind": "contact_moved", "contact_ref": "contact-land",
                   "from_location_ref": "location-156", "to_location_ref": center}]
        triggered = attention.evaluate_watches([], temporal_events=events, observation_cursor=2, turn=4)
        assert len(triggered) == 1 and triggered[0]["watch_id"] == entry["watch_id"]
        assert [match["temporal_event"]["contact_ref"] for match in triggered[0]["matches"]] \
            == ["contact-land", "contact-sea"]
        # Restart preserves membership, and repeated observations do not duplicate attention.
        reopened = AttentionService(SmacxStore(root / "state.sqlite3"), journal, scope)
        assert reopened.inspect_scope(combined["watch_id"]) == descriptor
        assert not reopened.evaluate_watches([], temporal_events=events, observation_cursor=2, turn=4)
        leaving = reopened.evaluate_watches([], temporal_events=[move("contact-land", "location-156", center)],
                                            observation_cursor=3, turn=4)
        assert len(leaving) == 1 and leaving[0]["watch_id"] == exit_watch["watch_id"]
        stale = deepcopy(objects)
        next(row for row in stale if row["object_ref"] == "contact-land")["fields"]["last_seen_turn"]["epistemic_status"] = "stale"
        save(stale, 4)
        assert not reopened.evaluate_watches([], temporal_events=[events[0]], observation_cursor=4, turn=4)
        changed = deepcopy(objects)
        next(row for row in changed if row["object_ref"] == center)["fields"]["terrain"]["value"] = "ocean"
        save(changed, 5)
        try:
            reopened.inspect_scope(combined["watch_id"])
            raise AssertionError("changed geography silently retargeted composite")
        except AttentionError:
            pass
        assert not reopened.evaluate_watches([], temporal_events=events, observation_cursor=5, turn=4)
        reopened.gc_watches(4)
        with store._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM world_watches WHERE status='invalid'").fetchone()[0] == 5
        topology = WorldService._topology(worlds.load(scope, identity.timeline_id))
        by_ref = {row["object_ref"]: row for row in objects}
        geographic = {"region-issued": {"kind": "region", "location_refs": [center, "location-151"]},
                      "route-issued": {"kind": "route", "location_refs": [center, "location-151"]},
                      "frontier-issued": {"kind": "frontier", "location_refs": [center]},
                      "theater-issued": {"kind": "theater", "location_refs": [center]}}
        for source in ("region-issued", "frontier-issued", "theater-issued"):
            geometry = scope_geometry({"type": "geography"}, (source,), topology, by_ref, geographic)
            assert set(geometry["_location_refs"]) == set(geographic[source]["location_refs"])
        corridor = scope_geometry({"type": "route_corridor", "radius": 2},
                                  ("route-issued",), topology, by_ref, geographic)
        expected = {square.location_ref for square in topology.by_ref.values()
                    if min(topology.shape.distance((square.x, square.y),
                                                   (topology.by_ref[ref].x, topology.by_ref[ref].y))
                           for ref in geographic["route-issued"]["location_refs"]) <= 2}
        assert set(corridor["_location_refs"]) == expected
        by_ref["base-radius"] = {"object_ref": "base-radius", "kind": "base", "location_ref": center}
        radius = scope_geometry({"type": "base_radius"}, ("base-radius",), topology, by_ref, geographic)
        assert radius["known_coverage_count"] == 21
        for invalid in ({"type": "proximity", "radius": True}, {"type": "proximity", "radius": 17},
                        {"type": "union", "members": [center]}):
            try:
                scope_geometry(invalid, (center,), topology, by_ref, {})
                raise AssertionError("unbounded or arbitrary geometry accepted")
            except ValueError:
                pass
        expiring = reopened.create_watch("spatial_scope", [center],
                                         {"type": "proximity", "radius": 1},
                                         current_turn=4, expires_turn=4)
        with store.transaction() as connection:
            connection.execute("UPDATE world_watches SET expires_turn=3 WHERE watch_id=?",
                               (expiring["watch_id"],))
        try:
            reopened.inspect_scope(expiring["watch_id"])
            raise AssertionError("expired scope remained queryable before periodic GC")
        except AttentionError:
            pass
    print(json.dumps({"ok": True, "native_radius_adapter": True, "private_composite_geometry": True,
                      "observed_hostile_crossings_only": True, "stale_evidence_withheld": True,
                      "restart_and_dedupe": True, "dependency_invalidation": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
