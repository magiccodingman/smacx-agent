#!/usr/bin/env python3
"""Acceptance contracts for physical geography and deterministic geopolitics."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_mechanics import location_affordances, logistics
from smacx_regions import (
    PHYSICAL_LAND_PROFILE, PHYSICAL_OCEAN_PROFILE, RegionBuilder,
)
from smacx_store import MemoryScope, SmacxStore
from smacx_topology import KnownSquare, MapShape, MobilityProfile, PerspectiveTopology
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector, SemanticLodProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity


def field(value, status="current", source="owned_state", turn=40):
    return {"value": value, "epistemic_status": status, "source": source,
            "last_verified_turn": turn, "provenance_ref": "native-shaped-fixture"}


def obj(ref, kind, location=None, status="active", **fields):
    return {"object_ref": ref, "kind": kind, "location_ref": location,
            "status": status, "fields": {key: field(value) for key, value in fields.items()}}


def projection(squares, objects, *, width=20, height=8, revision=1):
    return {
        "identity": {"match_id": "match-geo", "perspective_id": "perspective-geo",
                     "timeline_id": "timeline-main", "world_epoch": "world-geo"},
        "world_revision": revision, "observation_cursor": revision, "turn": 40,
        "year": 2240, "map_shape": {"width": width, "height": height,
                                      "horizontal_wrap": False},
        "known_squares": squares, "objects": objects,
    }


def initialized(root: Path):
    store = SmacxStore(root / "smacx.sqlite3")
    store.ensure_agent("agent-geo", "Geo")
    store.create_match(match_id="match-geo", display_name="Geo", mode="solo")
    store.create_perspective("match-geo", "agent-geo", perspective_id="perspective-geo")
    scope = MemoryScope("match-geo", "agent-geo", "perspective-geo")
    return store, scope, WorldStore(store, root / "snapshots")


def main() -> int:
    results = {}
    builder = RegionBuilder()
    shape = MapShape(20, 8, False)
    base_squares = [
        KnownSquare("l0", 0, 2, "land"), KnownSquare("l1", 2, 2, "land"),
        KnownSquare("l2", 4, 2, "land"),
        KnownSquare("o0", 1, 3, "ocean"), KnownSquare("o1", 3, 3, "ocean"),
    ]
    topology = PerspectiveTopology(shape, base_squares)
    land_before, _ = builder.build_physical(topology, "land", world_revision=1)
    ocean_before, _ = builder.build_physical(topology, "ocean", world_revision=1)
    assert len(land_before) == len(ocean_before) == 1
    land_identity = land_before[0].region_ref
    ocean_identity = ocean_before[0].region_ref
    # Ownership, diplomacy, ZOC, bases, and units are intentionally absent
    # from physical topology. Changing them cannot affect mass identity.
    changed_nonterrain = PerspectiveTopology(shape, [
        KnownSquare("l0", 0, 2, "land", owner_ref="faction-2", hostile_zoc=True),
        KnownSquare("l1", 2, 2, "land", owner_ref="faction-3"),
        KnownSquare("l2", 4, 2, "land", owner_ref="faction-2"),
        KnownSquare("o0", 1, 3, "ocean", owner_ref="faction-2"),
        KnownSquare("o1", 3, 3, "ocean", owner_ref="faction-3"),
    ])
    land_after, _ = builder.build_physical(
        changed_nonterrain, "land", land_before, world_revision=2,
    )
    ocean_after, _ = builder.build_physical(
        changed_nonterrain, "ocean", ocean_before, world_revision=2,
    )
    assert land_after[0].region_ref == land_identity
    assert ocean_after[0].region_ref == ocean_identity
    # A real terrain break changes physical lineage/versioning.
    split = PerspectiveTopology(
        shape, [base_squares[0], base_squares[2], *base_squares[3:]],
    )
    land_split, aliases = builder.build_physical(split, "land", land_after, world_revision=3)
    assert len(land_split) == 2
    assert {frozenset(row.location_refs) for row in land_split} == {
        frozenset({"l0"}), frozenset({"l2"}),
    }
    # A one-to-many split is intentionally not aliased to one arbitrary child.
    assert land_identity not in aliases
    merged_topology = PerspectiveTopology(shape, base_squares)
    land_merged, merge_aliases = builder.build_physical(
        merged_topology, "land", land_split, world_revision=4,
    )
    assert len(land_merged) == 1
    assert land_merged[0].location_refs == {"l0", "l1", "l2"}
    assert set(merge_aliases) == {row.region_ref for row in land_split}
    # A coastal land base joins sea mobility, never the physical ocean mass.
    coastal = PerspectiveTopology(shape, [
        KnownSquare("land-port", 0, 2, "land", features=frozenset({"base"})),
        KnownSquare("sea", 1, 3, "ocean"),
    ])
    sea_mobility, _ = builder.build(
        coastal, MobilityProfile("sea-mobility", "sea"), world_revision=1,
    )
    ocean_physical, _ = builder.build_physical(coastal, "ocean", world_revision=1)
    assert sea_mobility[0].location_refs == {"land-port", "sea"}
    assert ocean_physical[0].location_refs == {"sea"}
    results["physical_identity_and_mobility_separation"] = True

    # Native-shaped projection carries current ownership, resources, and a
    # visible named landmark. A later fogged receipt preserves them as stale.
    identity = WorldIdentity("match-geo", "perspective-geo", "timeline-main", "world-geo")
    native = {
        "turn": 40, "year": 2240, "action_revision": "a",
        "map": {"width": 20, "height": 8, "horizontal_wrap": False},
        "tiles": [
            {"tile_id": 20, "x": 0, "y": 2, "visible_now": True, "terrain": "land",
             "owner_ref": "faction-1", "features": ["nutrient_resource", "sensor"],
             "landmarks": [{"landmark_type_id": None,
                             "landmark_type": "named_natural_landmark",
                             "natural_name": "Mount Planet", "landmark_code": 0,
                             "named_center": True}]},
            {"tile_id": 21, "x": 2, "y": 2, "visible_now": True, "terrain": "land",
             "owner_ref": "faction-2", "features": ["mineral_resource"]},
            {"tile_id": 22, "x": 4, "y": 2, "visible_now": False, "terrain": "land",
             "features": []},
            {"tile_id": 30, "x": 1, "y": 3, "visible_now": True, "terrain": "ocean",
             "owner_ref": "faction-2", "features": ["energy_resource"]},
        ],
        "bases": [
            {"id": 0, "base_ref": "base-home", "tile_id": 20, "owned": True,
             "visible_now": True, "owner_ref": "faction-1", "name": "Home", "coastal": True},
            {"id": 1, "base_ref": "base-sea", "tile_id": 30, "owned": False,
             "visible_now": True, "owner_ref": "faction-2", "name": "Sea Base"},
        ],
        "units": [
            {"id": 7, "own_unit_ref": "own-unit-7", "tile_id": 30, "owned": True,
             "owner_ref": "faction-1", "name": "Foil", "triad": "sea", "hp": 8,
             "max_hp": 10, "roles": {"combat": True}},
            {"id": 8, "native_observation_key": "enemy-8", "tile_id": 30,
             "owned": False, "owner_ref": "faction-2", "name": "Enemy Foil",
             "triad": "sea", "hp": 10, "max_hp": 10},
        ],
        "factions": [
            {"id": 1, "faction_ref": "faction-1", "owned": True},
            {"id": 2, "faction_ref": "faction-2", "owned": False,
             "relations": {"vendetta": True}},
        ],
    }
    first = PerspectiveProjector(identity).project(native, observation_sequence=1)
    anchor = SemanticLodProjector(context_tier="64k").build(first)
    landmass = next(row for row in anchor["physical_masses"] if row.get("landmass_ref"))
    ocean = next(row for row in anchor["physical_masses"] if row.get("ocean_mass_ref"))
    assert {row["faction_ref"] for row in landmass["territorial_composition"]} \
        == {"faction-1", "faction-2"}
    assert landmass["known_feature_composition"] and landmass["known_landmarks"]
    assert landmass["known_landmark_composition"]
    assert ocean["known_sea_bases_by_faction"] == {"faction-2": 1}
    assert ocean["current_visible_naval_contact_counts_by_faction"] == {"faction-2": 1}
    assert ocean["owned_naval_force_count"] == 1
    assert ocean["relevant_coastal_base_refs"] == ["base-home"]
    assert landmass["unknown_ownership_location_count"] == 1
    assert anchor["ownership_interfaces"][0]["current_known_adjacency_count"] >= 1
    assert "controlled" not in json.dumps(ocean).lower()
    prior = {**first, "objects": [item.as_dict(provider_safe=False) for item in first["objects"]],
             "world_revision": 1}
    fogged = dict(native)
    fogged["tiles"] = [
        {"tile_id": row["tile_id"], "x": row["x"], "y": row["y"],
         "visible_now": False, "features": row["features"]}
        for row in native["tiles"]
    ]
    second = PerspectiveProjector(identity, prior_projection=prior).project(
        fogged, observation_sequence=2,
    )
    second_anchor = SemanticLodProjector(context_tier="64k").build(
        second, previous_regions=anchor["_region_projection"],
    )
    stale_land = next(row for row in second_anchor["physical_masses"] if row.get("landmass_ref"))
    assert sum(row["stale_known_owned_location_count"]
               for row in stale_land["territorial_composition"]) == 2
    results["territory_resources_landmarks_ocean_geopolitics"] = True

    # A missing corridor between known components remains explicitly possible,
    # never asserted as hidden terrain. Frontier depth is queried lazily.
    frontier_squares = [
        KnownSquare("location-20", 0, 2, "land"),
        KnownSquare("location-21", 2, 2, "land"),
        KnownSquare("location-23", 6, 2, "land"),
        KnownSquare("location-24", 8, 2, "land"),
        KnownSquare("location-30", 1, 3, "ocean"),
    ]
    frontier_objects = [
        obj(square.location_ref, "location", None, terrain=square.terrain,
            features=["nutrient_resource"] if square.location_ref == "location-21" else [])
        for square in frontier_squares
    ] + [obj("scout", "own_unit", "location-20", owner_ref="faction-1",
             triad="land", movement_points=2, roles={"scout": True, "combat": True})]
    frontier_anchor = SemanticLodProjector(context_tier="64k").build(
        projection(frontier_squares, frontier_objects + [
            obj("faction-1", "faction", None, is_self=True, relations={}),
        ]),
    )
    assert any(row["unknown_geography_may_connect_known_components"]
               for row in frontier_anchor["frontiers"])
    assert all("faction-1" not in row["nearby_foreign_faction_refs"]
               for row in frontier_anchor["frontiers"])
    assert any(row["nearby_resource_composition"]
               for row in frontier_anchor["frontiers"])
    with tempfile.TemporaryDirectory(prefix="smacx-geo-") as temporary:
        store, scope, world_store = initialized(Path(temporary))
        frontier_native = {
            "turn": 40, "year": 2240, "action_revision": "frontier-a",
            "map": {"width": 20, "height": 8, "horizontal_wrap": False},
            "tiles": [
                {"tile_id": 20, "x": 0, "y": 2, "visible_now": True,
                 "terrain": "land", "features": []},
                {"tile_id": 21, "x": 2, "y": 2, "visible_now": True,
                 "terrain": "land", "features": ["nutrient_resource"]},
                {"tile_id": 23, "x": 6, "y": 2, "visible_now": True,
                 "terrain": "land", "features": []},
                {"tile_id": 24, "x": 8, "y": 2, "visible_now": True,
                 "terrain": "land", "features": []},
                {"tile_id": 30, "x": 1, "y": 3, "visible_now": True,
                 "terrain": "ocean", "features": []},
            ],
            "bases": [],
            "units": [{
                "id": 7, "own_unit_ref": "own-unit-7", "tile_id": 20,
                "owned": True, "owner_ref": "faction-1", "name": "Scout Patrol",
                "triad": "land", "movement_points": 2,
                "roles": {"scout": True, "combat": True},
            }],
            "factions": [{"id": 1, "faction_ref": "faction-1", "owned": True}],
        }
        world_store.replace_projection(
            scope, identity,
            PerspectiveProjector(identity).project(
                frontier_native, observation_sequence=1,
            )["objects"],
            observation_cursor=1, action_revision="frontier-a", continuity="complete",
            journal_head_hash="0" * 64,
        )
        service = WorldService(world_store, scope)
        issued = service.anchor(context_length=65536)["payload"]["frontiers"]
        assert issued
        detail = service.query(mode="area", origin_ref=issued[0]["frontier_ref"],
                               context_length=65536)
        assert detail["geographic_object"]["frontier_ref"] == issued[0]["frontier_ref"]
        assert detail["frontier_access"]["calculation_scope"] == "lazy_query_only"
        assert detail["frontier_access"]["reachable_scouts"]
        assert detail["frontier_access"]["nearest_scout_arrival_turns"] is not None
        legal = service.query(
            mode="compare", subject_refs=["location-20"], context_length=65536,
            runtime_base_site_receipts={
                "location-20": {"legal_for_land_colony": True},
            },
        )
        illegal = service.query(
            mode="compare", subject_refs=["location-20"], context_length=65536,
            runtime_base_site_receipts={
                "location-20": {"legal_for_land_colony": False},
            },
        )
        assert legal["items"][0].get("founding_buildability", {}).get(
            "legal_for_land_colony"
        ) is True, legal
        assert illegal["items"][0]["founding_buildability"]["legal_for_land_colony"] is False
    results["frontier_uncertainty_and_lazy_access"] = True

    # Cross-region activity may form one mechanically connected theater while
    # a quiet plan-linked island remains independently promoted.
    theater_squares = [
        KnownSquare("west", 0, 2, "land"), KnownSquare("sea-a", 1, 3, "ocean"),
        KnownSquare("sea-b", 3, 3, "ocean"), KnownSquare("east", 4, 2, "land"),
        KnownSquare("quiet", 16, 6, "land"), KnownSquare("irrelevant", 10, 0, "land"),
    ]
    theater_objects = [
        obj(ref, "location", None, terrain=square.terrain, features=[])
        for ref, square in ((row.location_ref, row) for row in theater_squares)
    ] + [
        obj("hostile-west", "foreign_contact", "west", owner_ref="faction-2",
            relationship="hostile", triad="land"),
        obj("ally-sea", "foreign_contact", "sea-a", owner_ref="faction-3",
            relationship="allied", triad="sea"),
        obj("hostile-east", "foreign_contact", "east", owner_ref="faction-2",
            relationship="hostile", triad="land"),
        obj("base-quiet", "base", "quiet", owner_ref="faction-1", name="Plan Base"),
    ]
    theater_anchor = SemanticLodProjector(context_tier="64k").build(
        projection(theater_squares, theater_objects),
        active_plan_refs=["base-quiet"], recent_material_refs=["hostile-east"],
    )
    theaters = theater_anchor["active_theaters"]
    assert len(theaters) >= 2
    assert any(len(row["region_refs"]) > 1 and row["allied_faction_refs"] for row in theaters)
    assert any("base-quiet" in row["promoted_by_refs"] for row in theaters)
    assert any("hostile-east" in row["recent_material_refs"] for row in theaters)
    quiet_mass = next(row for row in theater_anchor["physical_masses"]
                      if "base-quiet" in row["promoted_by_refs"])
    assert quiet_mass["lod_level"] == "operational"
    assert any(row["lod_level"] == "geographic" for row in theater_anchor["physical_masses"])
    with tempfile.TemporaryDirectory(prefix="smacx-theater-refs-") as temporary:
        store, scope, world_store = initialized(Path(temporary))
        theater_native = {
            "turn": 40, "year": 2240, "action_revision": "theater-a",
            "map": {"width": 20, "height": 8, "horizontal_wrap": False},
            "tiles": [
                {"tile_id": 20, "x": 0, "y": 2, "visible_now": True,
                 "terrain": "land", "features": []},
                {"tile_id": 30, "x": 1, "y": 3, "visible_now": True,
                 "terrain": "ocean", "features": []},
                {"tile_id": 31, "x": 3, "y": 3, "visible_now": True,
                 "terrain": "ocean", "features": []},
                {"tile_id": 22, "x": 4, "y": 2, "visible_now": True,
                 "terrain": "land", "features": []},
                {"tile_id": 68, "x": 16, "y": 6, "visible_now": True,
                 "terrain": "land", "features": []},
            ],
            "bases": [{
                "id": 0, "base_ref": "base-quiet", "tile_id": 68,
                "owned": True, "visible_now": True, "owner_ref": "faction-1",
                "name": "Quiet Plan Base",
            }],
            "units": [
                {"id": 2, "native_observation_key": "hostile-west", "tile_id": 20,
                 "owned": False, "owner_ref": "faction-2", "triad": "land"},
                {"id": 3, "native_observation_key": "ally-sea", "tile_id": 30,
                 "owned": False, "owner_ref": "faction-3", "triad": "sea"},
                {"id": 4, "native_observation_key": "hostile-east", "tile_id": 22,
                 "owned": False, "owner_ref": "faction-2", "triad": "land"},
            ],
            "factions": [
                {"id": 1, "faction_ref": "faction-1", "owned": True},
                {"id": 2, "faction_ref": "faction-2", "owned": False,
                 "relations": {"vendetta": True}},
                {"id": 3, "faction_ref": "faction-3", "owned": False,
                 "relations": {"pact": True}},
            ],
        }
        projected = PerspectiveProjector(identity).project(
            theater_native, observation_sequence=1,
        )
        world_store.replace_projection(
            scope, identity, projected["objects"], observation_cursor=1,
            action_revision="theater-a", continuity="complete",
            journal_head_hash="0" * 64,
        )
        world_store.record_observation_projection(
            scope, identity.timeline_id,
            {"sequence": 1, "kind": "world_batch", "turn": 40,
             "continuity": "complete", "payload": {"deltas": [{
                 "object_ref": "base-quiet", "change": "changed",
                 "current": {"object_ref": "base-quiet", "kind": "base",
                             "location_ref": "location-68"},
             }]}},
            "journal-event-recent-base",
        )
        service = WorldService(world_store, scope)
        recent_anchor = service.anchor(context_length=262144)
        assert any("base-quiet" in row["recent_material_refs"]
                   for row in recent_anchor["payload"]["active_theaters"])
        issued_anchor = service.anchor(
            context_length=65536, active_plan_refs=["base-quiet"],
        )
        quiet_theater = next(
            row for row in issued_anchor["payload"]["active_theaters"]
            if "base-quiet" in row["promoted_by_refs"]
        )
        queried = service.query(
            mode="area", origin_ref=quiet_theater["theater_ref"],
            context_length=65536,
        )
        assert queried["ok"] is True
        assert queried["geographic_object"]["theater_ref"] == quiet_theater["theater_ref"]
    results["cross_region_theaters_and_complete_promotion"] = True

    # Expansion candidates differ mechanically without acquiring a ranking.
    site_topology = PerspectiveTopology(MapShape(20, 8, False), [
        KnownSquare("site-rich", 0, 2, "land", features=frozenset({"river", "nutrient_resource"})),
        KnownSquare("site-overlap", 2, 2, "land", features=frozenset({"mineral_resource"})),
        KnownSquare("site-risk", 16, 6, "rocky"),
        KnownSquare("home", 4, 2, "land", features=frozenset({"base"})),
        KnownSquare("enemy", 14, 6, "land"),
    ])
    site_objects = {
        row.location_ref: obj(row.location_ref, "location", None, terrain=row.terrain,
                              features=list(row.features)) for row in site_topology.by_ref.values()
    }
    site_objects.update({
        "faction-1": obj("faction-1", "faction", None, is_self=True),
        "base-home": obj("base-home", "base", "home", owner_ref="faction-1"),
        "enemy-unit": obj("enemy-unit", "foreign_contact", "enemy",
                          owner_ref="faction-2", relationship="hostile"),
    })
    receipts = {
        "site-rich": {"legal_for_land_colony": True, "legal_for_sea_colony": False,
                      "current_tile_yields": {"nutrients": 3, "minerals": 1, "energy": 1},
                      "known_radius_location_count": 5, "radius_complete_currently_visible": False,
                      "known_radius": [], "overlapping_known_bases": []},
        "site-overlap": {"legal_for_land_colony": True, "legal_for_sea_colony": False,
                         "current_tile_yields": {"nutrients": 1, "minerals": 3, "energy": 0},
                         "known_radius_location_count": 5, "radius_complete_currently_visible": False,
                         "known_radius": [], "overlapping_known_bases": [
                             {"base_ref": "base-home", "overlapping_radius_location_count": 9}]},
        "site-risk": {"legal_for_land_colony": False, "legal_for_sea_colony": False,
                      "current_tile_yields": {"nutrients": 0, "minerals": 2, "energy": 0},
                      "known_radius_location_count": 2, "radius_complete_currently_visible": False,
                      "known_radius": [], "overlapping_known_bases": []},
    }
    sites = location_affordances(
        site_topology, site_objects, receipts,
        native_receipts=receipts,
        physical_mass_by_location={"site-rich": "mass-a", "site-overlap": "mass-a",
                                   "site-risk": "mass-b"},
        mobility_region_by_location={"site-rich": ["region-a"],
                                     "site-overlap": ["region-a"], "site-risk": ["region-b"]},
    )
    by_ref = {row["location_ref"]: row for row in sites}
    assert by_ref["site-rich"]["known_current_tile_yields"]["nutrients"] == 3
    assert by_ref["site-overlap"]["overlapping_known_base_radii"]
    assert by_ref["site-risk"]["founding_buildability"]["legal_for_land_colony"] is False
    assert by_ref["site-risk"]["nearest_visible_contact_distance"] < \
        by_ref["site-rich"]["nearest_visible_contact_distance"]
    assert all("best" not in json.dumps(row).lower() for row in sites)
    results["expansion_mechanical_affordances"] = True

    # Repair/staging remains subject-relative and rules-aware, without ranking.
    repair_objects = {
        **site_objects,
        "base-home": obj(
            "base-home", "base", "home", owner_ref="faction-1",
            drone_riots=False,
            facilities=[{"facility_id": 27, "name": "Command Center"}],
        ),
        "faction-3": obj(
            "faction-3", "faction", None, is_self=False,
            relations={"pact": True},
        ),
        "base-pact": obj(
            "base-pact", "base", "site-overlap", owner_ref="faction-3",
        ),
        "damaged": obj("damaged", "own_unit", "site-rich", owner_ref="faction-1",
                       triad="land", movement_points=2, hp=4, max_hp=10,
                       roles={"combat": True, "planet_life": False}),
        "global-repair-rules": obj("global-repair-rules", "repair_rules", None,
                                   state={"minimal": 1, "base_bonus": 1,
                                          "bunker_bonus": 1,
                                          "base_facility_bonus": 10}),
    }
    repair = logistics(repair_objects, site_topology, ["damaged", "base-home"])
    assert repair["staging_bases"] and repair["damaged_unit_repair_options"]
    assert {row["base_ref"] for row in repair["staging_bases"]} >= {
        "base-home", "base-pact",
    }
    home_repair = next(
        row for row in repair["damaged_unit_repair_options"]
        if row.get("base_ref") == "base-home"
    )
    assert home_repair["known_repair_rule_modifiers"]["base_bonus"] == 1
    assert home_repair["known_repair_rule_modifiers"]["base_facility_bonus"] == 10
    pact_location = next(
        row for row in repair["repair_locations"] if row.get("base_ref") == "base-pact"
    )
    assert pact_location["base_repair_status"] == "unknown_drone_riot_state"
    assert all("best" not in json.dumps(row).lower()
               for row in repair["damaged_unit_repair_options"])
    results["repair_and_staging_logistics"] = True

    # Hundreds of irrelevant islands/frontiers remain aggregate-only; active
    # plan/recent/hostile geography survives individual promotion.
    huge_shape = MapShape(200, 100, False)
    isolated_positions = [(x, y) for y in range(0, 100, 4)
                          for x in range(0, 200, 4)]
    huge_squares = [KnownSquare(f"h-{index}", x, y, "land")
                    for index, (x, y) in enumerate(isolated_positions[:800])]
    huge_objects = [obj(row.location_ref, "location", None, terrain="land", features=[])
                    for row in huge_squares]
    promoted_locations = [row.location_ref for row in huge_squares[:4]]
    huge_objects.extend([
        obj("owned-base", "base", promoted_locations[0], owner_ref="faction-1"),
        obj("plan-base", "base", promoted_locations[1], owner_ref="faction-1"),
        obj("ally", "foreign_contact", promoted_locations[2], owner_ref="faction-3",
            relationship="allied"),
        obj("hostile", "foreign_contact", promoted_locations[3], owner_ref="faction-2",
            relationship="hostile"),
    ])
    huge = SemanticLodProjector(context_tier="64k").build(
        projection(huge_squares, huge_objects, width=200, height=100),
        focus_ref="owned-base", active_plan_refs=["plan-base"],
        recent_material_refs=["ally"], triggered_watch_refs=["hostile"],
    )
    assert huge["physical_mass_overflow"]["omitted_count"] > 100
    represented = set(huge["lod"]["promotion_refs"])
    assert {"owned-base", "plan-base", "ally", "hostile"} <= represented
    assert huge["token_estimate"] <= 6000
    results["huge_fragmented_geographic_lod"] = True

    print(json.dumps({"event": "pass", "payload": results}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
