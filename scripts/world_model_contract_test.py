#!/usr/bin/env python3
"""Deterministic acceptance contracts for the sovereign perspective world."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_attention import AttentionError, AttentionService
from smacx_journal import CampaignJournal
from smacx_regions import Region, RegionBuilder
from smacx_store import MemoryScope, SmacxStore
from smacx_topology import KnownSquare, MapShape, MobilityProfile, PerspectiveTopology
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector, SemanticLodProjector, net_deltas
from smacx_world_store import WorldStore
from smacx_world_types import (
    WorldContractError, WorldIdentity, WorldObject, content_hash, material_hash,
)


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
              "triad": "land", "hp": 10, "max_hp": 10,
              "airdrop_ready": True, "airdrop_range": 8,
              "roles": {"combat": True}}]
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

    # VEH is a compact native array. Changing only its private row index must
    # preserve owned/contact semantic identity and produce no player-visible
    # change when the bridge's monotonic handle remains stable.
    compaction_identity = WorldIdentity(
        "match-compaction", "perspective-compaction", "timeline-main", "world-compaction",
    )
    before_compaction = bundle()
    before_compaction["units"][0]["own_unit_ref"] = "own-unit-501"
    before_compaction["units"][1]["native_observation_key"] = "vehicle-handle-901"
    first_compaction = PerspectiveProjector(compaction_identity).project(
        before_compaction, observation_sequence=1,
    )
    prior_compaction = {
        "identity": compaction_identity.as_dict(), "world_revision": 1,
        "objects": [item.as_dict(provider_safe=False) for item in first_compaction["objects"]],
    }
    after_compaction = bundle()
    after_compaction["units"][0].update({"id": 2, "own_unit_ref": "own-unit-501"})
    after_compaction["units"][1].update({
        "id": 3, "native_observation_key": "vehicle-handle-901",
    })
    second_compaction = PerspectiveProjector(
        compaction_identity, prior_projection=prior_compaction,
    ).project(after_compaction, observation_sequence=2)
    first_semantic = {
        item.object_ref: item.as_dict(provider_safe=True)
        for item in first_compaction["objects"]
    }
    second_semantic = {
        item.object_ref: item.as_dict(provider_safe=True)
        for item in second_compaction["objects"]
    }
    assert first_semantic == second_semantic
    assert "own-unit-501" in second_semantic
    assert next(ref for ref, item in second_semantic.items()
                if item["kind"] == "foreign_contact") == next(
                    ref for ref, item in first_semantic.items()
                    if item["kind"] == "foreign_contact"
                )
    assert {
        ref: material_hash(item) for ref, item in first_semantic.items()
    } == {
        ref: material_hash(item) for ref, item in second_semantic.items()
    }
    assert second_compaction["temporal_events"] == []

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
        service = WorldService(world_store, scope)
        receipt_query = service.query(
            mode="route", origin_ref="own-unit-7", target_ref="base-alpha",
            context_length=65536,
            runtime_airdrop_receipt={
                "action_revision": "action-b",
                "targets": [{"target_tile_id": 0}],
                "target_count": 1, "targets_truncated": False,
            },
        )
        assert receipt_query["ok"] and receipt_query["cache"]["hit"] is False
        receipt_hit = service.query(
            mode="route", origin_ref="own-unit-7", target_ref="base-alpha",
            context_length=65536,
            runtime_airdrop_receipt={
                "action_revision": "action-b",
                "targets": [{"target_tile_id": 0}],
                "target_count": 1, "targets_truncated": False,
            },
        )
        assert receipt_hit["cache"]["hit"] is True
        persisted_after_receipt = world_store.load(scope, "timeline-main")
        persisted_dropper = next(item for item in persisted_after_receipt["objects"]
                                 if item["object_ref"] == "own-unit-7")
        assert "airdrop_target_tile_ids" not in persisted_dropper.get("fields", {})
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
        later_bundle = bundle()
        later_bundle.update({"turn": 13, "year": 2213})
        reverified = PerspectiveProjector(identity, prior_projection=loaded).project(
            later_bundle, observation_sequence=30,
        )
        reverified_contact = next(item for item in reverified["objects"]
                                  if item.object_ref == contact["object_ref"])
        assert reverified_contact.fields["hp"].first_known_turn == 12
        assert reverified_contact.fields["hp"].last_verified_turn == 13
        contact_deltas = [delta for delta in net_deltas(
            loaded["objects"],
            [item.as_dict(provider_safe=False) for item in reverified["objects"]],
        ) if delta.get("object_ref") == contact["object_ref"]]
        assert contact_deltas == []
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
        loop_bundle = bundle()
        loop_bundle["_continuous_visible_contact_moves"] = {
            "hidden-engine-91": [
                {"from": "location-17", "to": "location-18"},
                {"from": "location-18", "to": "location-34"},
                {"from": "location-34", "to": "location-17"},
            ],
        }
        looped = PerspectiveProjector(identity, prior_projection=loaded).project(
            loop_bundle, observation_sequence=321)
        loop_event = next(item for item in looped["temporal_events"]
                          if item["event_kind"] == "contact_moved")
        assert loop_event["from_location_ref"] == loop_event["to_location_ref"] \
            == "location-17" and len(loop_event["path"]) == 3
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

        # A loss event breaks lineage even when the same native row is visible
        # again at the identical square before this collector drain closes.
        two_contacts = bundle()
        two_contacts["units"].append({
            "id": 92, "native_observation_key": "hidden-engine-92",
            "tile_id": 18, "owned": False, "name": "Second Rover",
            "owner_ref": "faction-2", "hp": 10, "max_hp": 10,
        })
        first_two = PerspectiveProjector(identity).project(two_contacts, observation_sequence=40)
        two_prior = {**loaded, "objects": [item.as_dict(provider_safe=False)
                                             for item in first_two["objects"]]}
        before_by_key = {
            item.metadata.get("native_observation_key"): item.object_ref
            for item in first_two["objects"] if item.kind == "foreign_contact"
        }
        same_drain = dict(two_contacts)
        same_drain["_broken_contact_handles"] = ["hidden-engine-91"]
        after_gap = PerspectiveProjector(identity, prior_projection=two_prior).project(
            same_drain, observation_sequence=41,
        )
        after_by_key = {
            item.metadata.get("native_observation_key"): item.object_ref
            for item in after_gap["objects"]
            if item.kind == "foreign_contact" and item.status == "active"
        }
        assert after_by_key["hidden-engine-91"] != before_by_key["hidden-engine-91"]
        assert after_by_key["hidden-engine-92"] == before_by_key["hidden-engine-92"]

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
        # Cache metadata belongs to the same whole-result ceiling, for every
        # detail tier and on both miss and hit paths.
        for detail, ceiling in (("compact", 512), ("standard", 2048), ("deep", 3276)):
            first_budgeted = service.query(
                mode="forces", detail=detail, context_length=65536,
                continuation=f"cursor-{10 + ceiling}",
            )
            second_budgeted = service.query(
                mode="forces", detail=detail, context_length=65536,
                continuation=f"cursor-{10 + ceiling}",
            )
            assert first_budgeted["result_token_estimate"] <= ceiling
            assert second_budgeted["result_token_estimate"] <= ceiling
            assert second_budgeted.get("cache", {}).get("hit") is True

        # Collector-private bookkeeping is not a strategic dependency. It may
        # change without invalidating a provider result; material topology may
        # not. This specifically guards subject-filtered route/relation caches.
        internal_only = json.loads(json.dumps(loaded["objects"]))
        internal_only[0].setdefault("metadata", {})["native_debug_counter"] = 999
        world_store.replace_projection(
            scope, identity, [WorldObject.from_dict(item) for item in internal_only], observation_cursor=3,
            action_revision="action-private", continuity="complete",
            journal_head_hash="0" * 64,
        )
        private_stable = service.query(
            mode="relation", origin_ref="base-alpha", target_ref="own-unit-7",
            context_length=65536,
        )
        assert private_stable["cache"]["hit"] is True
        topology_changed = json.loads(json.dumps(internal_only))
        location = next(item for item in topology_changed
                        if item.get("object_ref") == "location-1")
        location["fields"]["features"]["value"] = ["fungus"]
        world_store.replace_projection(
            scope, identity, [WorldObject.from_dict(item) for item in topology_changed], observation_cursor=4,
            action_revision="action-material", continuity="complete",
            journal_head_hash="0" * 64,
        )
        material_invalidated = service.query(
            mode="relation", origin_ref="base-alpha", target_ref="own-unit-7",
            context_length=65536,
        )
        assert material_invalidated["cache"]["hit"] is False
        mode_results["route"] = service.query(
            mode="route", origin_ref="own-unit-7", target_ref="base-alpha",
            context_length=65536,
        )
        first_anchor = service.anchor(context_length=65536)
        frontiers = first_anchor["payload"]["frontiers"]
        if not frontiers:
            # This fixture starts with a completely remembered map. Publish a
            # deterministic provider-visible frontier registry entry so the
            # watch validation contract is exercised independently from map
            # exploration coverage.
            anchor_payload = dict(first_anchor["payload"])
            frontier_ref = "frontier-" + content_hash({
                "region_ref": "region-issued", "boundary_refs": ["location-18"],
            })[:16]
            anchor_payload["frontiers"] = [{
                "frontier_ref": frontier_ref, "region_ref": "region-issued",
                "boundary_refs": ["location-18"], "unknown_neighbor_count": 1,
                "may_connect_elsewhere": True,
            }]
            world_store.save_anchor(
                scope, identity, world_revision=1, observation_cursor=2,
                context_tier="64k", payload=anchor_payload,
                token_estimate=int(first_anchor["token_estimate"]),
            )
            frontiers = anchor_payload["frontiers"]
        frontier = frontiers[0]
        frontier_watch = AttentionService(store, journal, scope).create_watch(
            "frontier_contact", [frontier["frontier_ref"]], {}, current_turn=12,
        )
        frontier_trigger = AttentionService(store, journal, scope).evaluate_watches(
            [], temporal_events=[{
                "event_kind": "contact_moved", "contact_ref": "contact-visible",
                "path": [{"from_location_ref": "unknown-outside",
                          "to_location_ref": frontier["boundary_refs"][0]}],
            }], observation_cursor=35, turn=12,
        )
        assert [item["watch_id"] for item in frontier_trigger] == [
            frontier_watch["watch_id"]]
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
        assert queued["attention_id"] in acknowledged["acknowledged_ids"]

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
        # A transient crossing is meaningful even if the contact starts and
        # ends outside the watched region before the next reconciliation.
        watched_region = Region(
            "region-test-v1", "region-test", 1, "mobility-land-default",
            "location-18", frozenset({"location-18"}), (),
        )
        world_store.save_regions(scope, identity.timeline_id, [watched_region], 1)
        entry_watch = attention.create_watch(
            "region_entry", [watched_region.region_ref], {}, current_turn=12,
        )
        exit_watch = attention.create_watch(
            "region_exit", [watched_region.region_ref], {}, current_turn=12,
        )
        transient = attention.evaluate_watches([], temporal_events=[loop_event],
                                               observation_cursor=4, turn=12)
        assert [item["watch_id"] for item in transient].count(entry_watch["watch_id"]) == 1
        assert [item["watch_id"] for item in transient].count(exit_watch["watch_id"]) == 1

        # Derived refs must have actually been issued to this perspective.
        try:
            attention.create_watch("route_disruption", ["route-invented"], {}, current_turn=12)
            raise AssertionError("invented semantic handle admitted")
        except AttentionError:
            pass
        route_ref = mode_results["route"]["route"]["route_ref"]
        route_watch = attention.create_watch(
            "route_disruption", [route_ref], {}, current_turn=12,
        )
        # The same current issued derived handle is valid operation evidence.
        current_projection = world_store.load(scope, identity.timeline_id)
        assert current_projection is not None
        route_dependencies = attention.semantic_dependency_hashes(current_projection)
        route_operation = attention.upsert_operation(
            operation_id=None, kind="route_review", objective="Review issued route",
            referenced_world_objects=[route_ref],
            source_world_revision=int(current_projection["world_revision"]),
            source_world_epoch=str(current_projection["identity"]["world_epoch"]),
            source_dependency_hash=content_hash({route_ref: route_dependencies[route_ref]}),
            current_turn=12,
        )
        assert route_operation["status"] == "active"
        route_path = mode_results["route"]["route"]["path"]
        disrupted = attention.evaluate_watches([], temporal_events=[{
            "event_kind": "terrain_or_improvement_changed",
            "location_ref": route_path[0],
            "affected_location_refs": [route_path[0]],
        }], observation_cursor=5, turn=12)
        assert [item["watch_id"] for item in disrupted] == [route_watch["watch_id"]]

        # Query-cache rows are merely issued-handle receipts, not permanent
        # authority. Once the route receipt expires, both watch and operation
        # lifecycle checks must reject/collect the derived reference.
        with store.transaction() as connection:
            connection.execute(
                "DELETE FROM world_query_cache WHERE match_id=? AND agent_id=? "
                "AND perspective_id=? AND timeline_id=?",
                (scope.match_id, scope.agent_id, scope.perspective_id,
                 identity.timeline_id),
            )
        route_state = attention.runtime_state(
            current_world_revision=int(current_projection["world_revision"]),
            current_world_epoch=str(current_projection["identity"]["world_epoch"]),
            object_dependency_hashes=attention.semantic_dependency_hashes(current_projection),
            current_turn=12,
        )
        assert route_operation["operation_id"] not in {
            row["operation_id"] for row in route_state["operations"]
        }

        rendezvous = service.query(
            mode="compare", subject_refs=["own-unit-7"], target_ref="base-alpha",
            context_length=65536,
        )["items"][0]
        rendezvous_watch = attention.create_watch(
            "rendezvous_progress", [rendezvous["rendezvous_ref"]], {}, current_turn=12,
        )
        rendezvous_progress = attention.evaluate_watches([], temporal_events=[{
            "event_kind": "contact_moved", "rendezvous_ref": rendezvous["rendezvous_ref"],
            "location_ref": rendezvous["candidate_ref"],
        }], observation_cursor=6, turn=12)
        assert [item["watch_id"] for item in rendezvous_progress] == [
            rendezvous_watch["watch_id"]]

        # Exercise the real builder through persistence, merge, split, and
        # empty-profile replacement. The prior-anchor component retains the
        # oldest lineage; every detached component receives a fresh lineage.
        persisted_profile = MobilityProfile("persisted-lineage", "land")
        persisted_connected = PerspectiveTopology(MapShape(10, 2, False), [
            KnownSquare("persist-a", 0, 0, "land"),
            KnownSquare("persist-b", 2, 0, "land"),
            KnownSquare("persist-c", 4, 0, "land"),
        ])
        persisted_split = PerspectiveTopology(MapShape(10, 2, False), [
            KnownSquare("persist-a", 0, 0, "land"),
            KnownSquare("persist-c", 4, 0, "land"),
        ])
        built_initial, _ = RegionBuilder().build(
            persisted_connected, persisted_profile, world_revision=10,
        )
        world_store.save_regions(scope, identity.timeline_id, built_initial, 10)
        loaded_initial = world_store.load_regions(
            scope, identity.timeline_id, persisted_profile.profile_ref,
        )
        built_split, split_aliases = RegionBuilder().build(
            persisted_split, persisted_profile, loaded_initial, world_revision=11,
        )
        assert len(built_split) == 2 and not split_aliases
        initial_lineage = loaded_initial[0].lineage_ref
        assert next(row for row in built_split if row.anchor_location_ref == "persist-a").lineage_ref \
            == initial_lineage
        detached_lineage = next(
            row for row in built_split if row.anchor_location_ref == "persist-c"
        ).lineage_ref
        assert detached_lineage != initial_lineage
        world_store.save_regions(scope, identity.timeline_id, built_split, 11)
        loaded_split = world_store.load_regions(
            scope, identity.timeline_id, persisted_profile.profile_ref,
        )
        built_merge, _ = RegionBuilder().build(
            persisted_connected, persisted_profile, loaded_split, world_revision=12,
        )
        assert len(built_merge) == 1
        assert built_merge[0].lineage_ref == initial_lineage
        assert built_merge[0].lineage_birth_revision == 10
        world_store.save_regions(scope, identity.timeline_id, built_merge, 12)
        loaded_merge = world_store.load_regions(
            scope, identity.timeline_id, persisted_profile.profile_ref,
        )
        built_second_split, _ = RegionBuilder().build(
            persisted_split, persisted_profile, loaded_merge, world_revision=13,
        )
        second_detached = next(
            row for row in built_second_split if row.anchor_location_ref == "persist-c"
        )
        assert second_detached.lineage_ref not in {initial_lineage, detached_lineage}
        world_store.save_regions(scope, identity.timeline_id, built_second_split, 13)
        world_store.save_regions(
            scope, identity.timeline_id, [], 14,
            mobility_profiles=[persisted_profile.profile_ref],
        )
        assert world_store.load_regions(
            scope, identity.timeline_id, persisted_profile.profile_ref,
        ) == []

        # One-to-one region supersession migrates; a split is ambiguous and
        # invalidates instead of silently choosing a new region.
        migrated_region = Region(
            "region-test-v2", "region-test", 2, "mobility-land-default",
            "location-18", frozenset({"location-18", "location-34"}),
            (watched_region.region_ref,),
        )
        world_store.save_regions(scope, identity.timeline_id, [migrated_region], 2)
        attention.gc_watches(13)
        with store._connect() as connection:
            migrated_subjects = json.loads(connection.execute(
                "SELECT subject_refs_json FROM world_watches WHERE watch_id=?",
                (entry_watch["watch_id"],),
            ).fetchone()[0])
        assert migrated_subjects == [migrated_region.region_ref]
        split_a = Region(
            "region-test-a-v3", "region-test", 3, "mobility-land-default",
            "location-18", frozenset({"location-18"}), (migrated_region.region_ref,),
        )
        split_b = Region(
            "region-test-b-v1", "region-test-b", 1, "mobility-land-default",
            "location-34", frozenset({"location-34"}), (migrated_region.region_ref,),
        )
        world_store.save_regions(scope, identity.timeline_id, [split_a, split_b], 3)
        attention.gc_watches(14)
        with store._connect() as connection:
            assert connection.execute(
                "SELECT status FROM world_watches WHERE watch_id=?",
                (entry_watch["watch_id"],),
            ).fetchone()[0] == "invalid"

        merge_a = Region(
            "region-merge-a-v1", "region-merge-a", 1, "mobility-land-default",
            "location-18", frozenset({"location-18"}), (),
        )
        merge_b = Region(
            "region-merge-b-v1", "region-merge-b", 1, "mobility-land-default",
            "location-34", frozenset({"location-34"}), (),
        )
        world_store.save_regions(scope, identity.timeline_id, [merge_a, merge_b], 4)
        merge_watch = attention.create_watch(
            "region_entry", [merge_a.region_ref, merge_b.region_ref], {}, current_turn=14,
        )
        merged = Region(
            "region-merge-a-v2", "region-merge-a", 2, "mobility-land-default",
            "location-18", frozenset({"location-18", "location-34"}),
            (merge_a.region_ref, merge_b.region_ref),
        )
        world_store.save_regions(scope, identity.timeline_id, [merged], 5)
        attention.gc_watches(15)
        with store._connect() as connection:
            merge_row = connection.execute(
                "SELECT status,subject_refs_json FROM world_watches WHERE watch_id=?",
                (merge_watch["watch_id"],),
            ).fetchone()
        assert merge_row["status"] == "active"
        assert json.loads(merge_row["subject_refs_json"]) == [merged.region_ref]

        # TTL and linked-plan lifecycle are enforced independently of model behavior.
        ttl_watch = attention.create_watch(
            "base_status", ["base-alpha"], {}, current_turn=20, expires_turn=21,
        )
        plan = store.put_plan(scope, "watch-plan", "Hold Alpha", "Keep Alpha secure")
        linked_watch = attention.create_watch(
            "base_threat", ["base-alpha"], {"field": "threatened"},
            current_turn=20, linked_plan_id=plan["plan_id"],
        )
        store.put_plan(scope, "watch-plan", "Hold Alpha", "No longer active",
                       status="completed")
        attention.gc_watches(22)
        with store._connect() as connection:
            statuses = {row["watch_id"]: row["status"] for row in connection.execute(
                "SELECT watch_id,status FROM world_watches WHERE watch_id IN (?,?)",
                (ttl_watch["watch_id"], linked_watch["watch_id"]),
            )}
        assert statuses == {ttl_watch["watch_id"]: "expired",
                            linked_watch["watch_id"]: "expired"}
        operation_refs = ["base-alpha"]
        current_projection = world_store.load(scope, identity.timeline_id)
        assert current_projection is not None
        dependencies = attention.semantic_dependency_hashes()
        operation = attention.upsert_operation(
            operation_id=None, kind="compare_bases", objective="Compare defense windows",
            referenced_world_objects=operation_refs,
            source_world_revision=int(current_projection["world_revision"]),
            source_world_epoch=str(current_projection["identity"]["world_epoch"]),
            source_dependency_hash=content_hash({
                ref: dependencies[ref] for ref in operation_refs
            }),
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
        "advance_retreat_path_preserved": True,
        "transient_region_entry_watch": True,
        "region_exit_watch": True, "semantic_handle_registry": True,
        "frontier_temporal_watch": True,
        "route_disruption_watch": True, "rendezvous_watch": True,
        "region_watch_migration_split_and_merge": True,
        "region_builder_persistence_split_merge_split": True,
        "watch_ttl_and_plan_cleanup": True,
        "native_row_compaction_semantically_inert": True,
        "first_known_preserved_on_reverification": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
