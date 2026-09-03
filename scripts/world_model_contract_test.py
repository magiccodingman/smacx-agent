#!/usr/bin/env python3
"""Deterministic acceptance contracts for the sovereign perspective world."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_attention import AttentionError, AttentionService
from smacx_journal import CampaignJournal
from smacx_regions import RegionBuilder
from smacx_store import MemoryScope, SmacxStore
from smacx_topology import KnownSquare, MapShape, MobilityProfile, PerspectiveTopology
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector, SemanticLodProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldContractError, WorldIdentity, content_hash


def initialized(root: Path) -> tuple[SmacxStore, CampaignJournal, MemoryScope, WorldStore]:
    store = SmacxStore(root / "smacx.sqlite3")
    store.ensure_agent("agent-world-test", "World Test")
    store.create_match(match_id="match-world-test", display_name="World Test", mode="solo")
    store.create_perspective("match-world-test", "agent-world-test",
                             perspective_id="perspective-world-test")
    scope = MemoryScope("match-world-test", "agent-world-test", "perspective-world-test")
    journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
    return store, journal, scope, WorldStore(store, root / "world-snapshots")


def bundle(width: int = 16, height: int = 8, *, contact: bool = True) -> dict:
    tiles = []
    for y in range(height):
        for x in range(y & 1, width, 2):
            tile_id = (x + width * y) // 2
            tiles.append({
                "tile_id": tile_id, "x": x, "y": y, "visible_now": y < 4,
                "features": ["road"] if y == 2 else [],
                "terrain": "ocean" if y == height - 1 else "land",
            })
    units = [{"id": 7, "tile_id": 1, "owned": True, "name": "Scout",
              "triad": "land", "hp": 10, "max_hp": 10}]
    if contact:
        units.append({"id": 91, "native_observation_key": "hidden-engine-91",
                      "tile_id": width + 1, "owned": False,
                      "name": "Rover", "owner_ref": "faction-2", "hp": 8, "max_hp": 10})
    return {
        "turn": 12, "year": 2212, "action_revision": "action-a",
        "map": {"width": width, "height": height, "horizontal_wrap": True},
        "tiles": tiles,
        "bases": [{"id": 0, "base_ref": "base-alpha", "tile_id": 0,
                   "owned": True, "name": "Alpha", "population": 3,
                   "mineral_surplus": 4}],
        "units": units,
        "factions": [{"id": 1, "faction_ref": "faction-1", "owned": True,
                      "faction_name": "Spartans"},
                     {"id": 2, "faction_ref": "faction-2", "owned": False,
                      "faction_name": "University", "relations": {"vendetta": True}}],
    }


def main() -> int:
    # Parity lattice, boundaries, wrapping, and known-world route constraints.
    wrapped = MapShape(16, 8, True)
    flat = MapShape(16, 8, False)
    assert wrapped.neighbor((0, 2), "W") == (14, 2)
    assert flat.neighbor((0, 2), "W") is None
    assert set(wrapped.neighbors((2, 2))) == {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
    assert wrapped.distance((0, 2), (14, 2)) == 1
    known = [KnownSquare("location-a", 0, 2, "land"),
             KnownSquare("location-b", 2, 2, "land"),
             KnownSquare("location-c", 4, 2, "land")]
    topology = PerspectiveTopology(flat, known)
    route = topology.route("location-a", "location-c", MobilityProfile("land-test", "land"))
    assert route.reachable and route.turns == 2
    hidden_gap = PerspectiveTopology(flat, (known[0], known[2])).route(
        "location-a", "location-c", MobilityProfile("land-test", "land"))
    assert not hidden_gap.reachable and "unknown geography" in hidden_gap.uncertainty[0]

    with tempfile.TemporaryDirectory(prefix="smacx-world-") as temporary:
        root = Path(temporary)
        store, journal, scope, world_store = initialized(root)
        identity = WorldIdentity(scope.match_id, scope.perspective_id,
                                 "timeline-main", "world-test")
        first = PerspectiveProjector(identity).project(bundle(), observation_sequence=1)
        saved = world_store.replace_projection(
            scope, identity, first["objects"], observation_cursor=1,
            action_revision="action-a", continuity="complete", journal_head_hash="0" * 64,
        )
        assert saved["world_revision"] == 1 and saved["changed"]
        stable = world_store.replace_projection(
            scope, identity, first["objects"], observation_cursor=2,
            action_revision="action-b", continuity="complete", journal_head_hash="0" * 64,
        )
        assert stable["world_revision"] == 1 and not stable["changed"]
        loaded = world_store.load(scope, "timeline-main")
        assert loaded and loaded["action_revision"] == "action-b"
        contact = next(item for item in loaded["objects"] if item["kind"] == "foreign_contact")
        assert "hidden-engine-91" not in json.dumps({
            key: value for key, value in contact.items() if key != "metadata"
        })
        continued = PerspectiveProjector(identity, prior_projection=loaded).project(
            bundle(), observation_sequence=3)
        assert {
            item.object_ref: item.as_dict(provider_safe=False) for item in continued["objects"]
        } == {item["object_ref"]: item for item in loaded["objects"]}
        assert next(item.object_ref for item in continued["objects"]
                    if item.kind == "foreign_contact" and item.status == "active") == contact["object_ref"]
        # Identity may cross tiles only when the native feed proves one
        # uninterrupted visible path.  This includes advance/retreat and ally
        # rendezvous trajectories; a bare reconciliation location change is
        # deliberately insufficient.
        moved_bundle = bundle()
        moved_bundle["units"][-1]["tile_id"] = 2 * 16 + 2
        moved_bundle["_continuous_visible_contact_moves"] = {
            "hidden-engine-91": [
                {"from": "location-17", "to": "location-18"},
                {"from": "location-18", "to": "location-34"},
            ],
        }
        moved = PerspectiveProjector(identity, prior_projection=loaded).project(
            moved_bundle, observation_sequence=31)
        assert next(item.object_ref for item in moved["objects"]
                    if item.kind == "foreign_contact" and item.status == "active") == contact["object_ref"]
        retreat_prior = {**loaded, "objects": [item.as_dict(provider_safe=False)
                                                for item in moved["objects"]]}
        retreat_bundle = bundle()
        retreat_bundle["_continuous_visible_contact_moves"] = {
            "hidden-engine-91": [
                {"from": "location-34", "to": "location-18"},
                {"from": "location-18", "to": "location-17"},
            ],
        }
        retreated = PerspectiveProjector(identity, prior_projection=retreat_prior).project(
            retreat_bundle, observation_sequence=32)
        assert next(item.object_ref for item in retreated["objects"]
                    if item.kind == "foreign_contact" and item.status == "active") == contact["object_ref"]
        unproven_bundle = bundle()
        unproven_bundle["units"][-1]["tile_id"] = 2 * 16 + 2
        unproven = PerspectiveProjector(identity, prior_projection=loaded).project(
            unproven_bundle, observation_sequence=33)
        assert next(item.object_ref for item in unproven["objects"]
                    if item.kind == "foreign_contact" and item.status == "active") != contact["object_ref"]
        compacted_bundle = bundle()
        compacted_bundle["_contact_identity_reset"] = True
        compacted = PerspectiveProjector(identity, prior_projection=loaded).project(
            compacted_bundle, observation_sequence=34)
        assert next(item.object_ref for item in compacted["objects"]
                    if item.kind == "foreign_contact" and item.status == "active") != contact["object_ref"]
        missing = PerspectiveProjector(identity, prior_projection=loaded).project(
            bundle(contact=False), observation_sequence=4)
        assert any(item.object_ref == contact["object_ref"] and item.status == "lost"
                   for item in missing["objects"])
        reappeared_prior = {**loaded, "objects": [item.as_dict(provider_safe=False)
                                                   for item in missing["objects"]]}
        reappeared = PerspectiveProjector(identity, prior_projection=reappeared_prior).project(
            bundle(), observation_sequence=5)
        assert next(item.object_ref for item in reappeared["objects"]
                    if item.kind == "foreign_contact" and item.status == "active") != contact["object_ref"]

        # Provider-facing world never serializes collector-private IDs.
        service = WorldService(world_store, scope)
        forces = service.query(mode="forces", context_length=65536)
        assert "native_observation_key" not in json.dumps(forces)
        relation = service.query(mode="relation", origin_ref="base-alpha",
                                 target_ref="own-unit-7", context_length=65536)
        assert relation["relation"]["geometric_distance"] >= 0
        # Every provider-facing semantic-zoom mode has one deterministic
        # contract. This protects the compact facade from growing modes that
        # exist in its schema but fail only when a sovereign needs them.
        mode_results = {
            "overview": service.query(mode="overview", context_length=65536),
            "area": service.query(mode="area", origin_ref="base-alpha", radius=2,
                                  context_length=65536),
            "route": service.query(mode="route", origin_ref="own-unit-7",
                                   target_ref="base-alpha", context_length=65536),
            "reachability": service.query(mode="reachability", origin_ref="own-unit-7",
                                          radius=2, context_length=65536),
            "compare": service.query(mode="compare", origin_ref="own-unit-7",
                                     subject_refs=["base-alpha"], context_length=65536),
            "base": service.query(mode="base", subject_refs=["base-alpha"],
                                  context_length=65536),
            "forces": forces,
            "logistics": service.query(mode="logistics", context_length=65536),
            "intel": service.query(mode="intel", context_length=65536),
            "changes": service.query(mode="changes", since_cursor=0,
                                     context_length=65536),
            "global": service.query(mode="global", context_length=65536),
            "render": service.query(mode="render", detail="compact",
                                    context_length=65536),
        }
        assert set(mode_results) == {
            "overview", "area", "route", "reachability", "compare", "base",
            "forces", "logistics", "intel", "changes", "global", "render",
        }
        for mode, result in mode_results.items():
            assert result["ok"] and result["mode"] == mode
            assert result["identity"]["perspective_id"] == scope.perspective_id
            assert result["result_token_estimate"] <= 2048
        assert mode_results["render"]["rendering"]["svg"].startswith("<svg")
        repeated = service.query(mode="relation", origin_ref="base-alpha",
                                 target_ref="own-unit-7", context_length=65536)
        assert repeated["cache"]["hit"] is True
        first_anchor = service.anchor(context_length=65536)
        promoted_anchor = service.anchor(context_length=65536, focus_ref="own-unit-7")
        assert promoted_anchor["world_anchor_id"] != first_anchor["world_anchor_id"]
        with store._connect() as connection:
            anchor_count = connection.execute(
                "SELECT COUNT(*) FROM world_anchors WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND context_tier='64k'",
                (scope.match_id, scope.agent_id, scope.perspective_id),
            ).fetchone()[0]
        assert anchor_count == 1

        # Anchor footprint follows strategic complexity rather than tile count.
        quiet_small = PerspectiveProjector(identity).project(bundle(16, 8, contact=False),
                                                               observation_sequence=10)
        quiet_huge = PerspectiveProjector(identity).project(bundle(160, 80, contact=False),
                                                              observation_sequence=11)
        small_anchor = SemanticLodProjector(context_tier="64k").build(quiet_small)
        huge_anchor = SemanticLodProjector(context_tier="64k").build(quiet_huge)
        assert huge_anchor["token_estimate"] <= small_anchor["token_estimate"] * 1.15
        assert huge_anchor["token_estimate"] <= 6000

        # At-least-once attention is not consumed by placement or a failed call.
        attention = AttentionService(store, journal, scope)
        queued = attention.enqueue("chat", {"text": "hello"}, observation_cursor=2,
                                   priority=80, critical=True)
        lease = attention.lease("episode-world-one")
        attention.placed(lease["attention_lease_id"])
        attention.abandon(lease["attention_lease_id"])
        redelivery = attention.lease("episode-world-two")
        assert redelivery["items"][0]["attention_id"] == queued["attention_id"]
        assert redelivery["items"][0]["redelivered"] is True
        attention.placed(redelivery["attention_lease_id"])
        try:
            attention.acknowledge(redelivery["attention_lease_id"], through_cursor=2)
            raise AssertionError("placement incorrectly counted as cognition")
        except AttentionError:
            pass
        attention.responded(redelivery["attention_lease_id"])
        acknowledged = attention.acknowledge(
            redelivery["attention_lease_id"], through_cursor=2,
        )
        assert acknowledged["acknowledged_ids"] == [queued["attention_id"]]

        # Watch/operation bounds and sovereign writer serialization.
        watch = attention.create_watch("base_threat", ["base-alpha"],
                                       {"field": "threatened", "equals": True},
                                       current_turn=12)
        assert watch["expires_turn"] == 22
        triggered = attention.evaluate_watches([{
            "object_ref": "base-alpha", "change": "changed", "current": {
                "object_ref": "base-alpha", "kind": "base",
                "fields": {"threatened": {"value": True}},
            },
        }], observation_cursor=3, turn=12)
        assert triggered[0]["watch_id"] == watch["watch_id"]
        operation = attention.upsert_operation(
            operation_id=None, kind="compare_bases", objective="Compare defense windows",
            referenced_world_objects=["base-alpha"], source_world_revision=1,
            source_world_epoch="world-test",
            source_dependency_hash=content_hash({"base-alpha": "test"}),
            current_turn=12,
        )
        assert operation["foreground"]
        token = attention.acquire_sovereign("episode-gameplay-one", "gameplay")
        try:
            attention.acquire_sovereign("episode-communication-one", "communication")
            raise AssertionError("concurrent sovereign writer admitted")
        except AttentionError:
            pass
        attention.release_sovereign(token, committed=True)
        invalidated = attention.runtime_state(
            current_world_epoch="world-replaced",
            object_dependency_hashes={"base-alpha": "different"}, current_turn=13,
        )
        assert invalidated["operations"] == []

    print(json.dumps({"event": "pass", "payload": {
        "correct_square_topology": True, "known_world_routing": True,
        "all_semantic_world_modes": True,
        "action_world_revision_separated": True, "foreign_fog_identity_safe": True,
        "perspective_provider_sanitized": True, "query_cache": True,
        "single_materialized_anchor_per_tier": True,
        "huge_quiet_anchor_bounded": True, "attention_redelivery": True,
        "watch_operation_bounds": True, "single_sovereign_writer": True,
        "watch_trigger_delivery": True, "world_invalid_operation_collected": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
