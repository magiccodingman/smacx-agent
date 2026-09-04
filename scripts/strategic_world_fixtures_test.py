#!/usr/bin/env python3
"""Mechanically check the locked strategic-perception scenario fixtures."""

from __future__ import annotations

import json

from smacx_mechanics import (
    base_mechanics, connector_analysis, location_affordances, lost_contact_envelopes,
    mobility_profile, rendezvous_matrix, response_matrix, transport_route,
)
from smacx_regions import RegionBuilder
from smacx_topology import KnownSquare, MapShape, MobilityProfile, PerspectiveTopology
from smacx_world_model import SemanticLodProjector


def field(value, status="current", source="owned_state", turn=50):
    return {"value": value, "epistemic_status": status, "source": source,
            "last_verified_turn": turn, "provenance_ref": "fixture"}


def item(ref, kind, location=None, **fields):
    return {"object_ref": ref, "kind": kind, "location_ref": location,
            "status": "active", "fields": {k: field(v) for k, v in fields.items()}}


def grid(width=32, height=16, *, ocean_rows=()):
    return [KnownSquare(f"location-{(x + width*y)//2}", x, y,
                        "ocean" if y in ocean_rows else "land", True)
            for y in range(height) for x in range(y & 1, width, 2)]


def projection(width, height, squares, objects, *, revision=1):
    return {"identity": {"match_id": "match-fixture", "perspective_id": "perspective-fixture",
                         "timeline_id": "timeline-main", "world_epoch": "world-fixture"},
            "world_revision": revision, "observation_cursor": revision, "turn": 50,
            "year": 2250, "map_shape": {"width": width, "height": height,
                                         "horizontal_wrap": False},
            "known_squares": squares, "objects": objects}


def main() -> int:
    results = {}

    # Native square geometry: SMAC's parity grid has eight bearings, wraps
    # horizontally only when configured, and never invents off-map squares.
    wrapped = MapShape(8, 4, True)
    flat = MapShape(8, 4, False)
    assert wrapped.neighbor((0, 0), "W") == (6, 0)
    assert flat.neighbor((0, 0), "W") is None
    assert wrapped.distance((0, 0), (6, 0)) == 1
    assert flat.distance((0, 0), (6, 0)) == 3
    assert set(flat.neighbors((2, 2))) == {"N", "NE", "E", "SE", "SW", "W", "NW"}
    results["parity_wrap_flat_boundaries"] = True

    # Real region construction preserves exactly one old lineage through a
    # split, lets the genuinely oldest lineage win a merge, and assigns a new
    # lineage to every detached component.  Exercise the builder itself rather
    # than constructing Region rows by hand.
    region_builder = RegionBuilder()
    region_profile = MobilityProfile("lineage-land", "land")
    connected = PerspectiveTopology(MapShape(10, 2, False), [
        KnownSquare("a", 0, 0, "land"), KnownSquare("b", 2, 0, "land"),
        KnownSquare("c", 4, 0, "land"),
    ])
    initial, _ = region_builder.build(
        connected, region_profile, world_revision=1,
    )
    assert len(initial) == 1 and initial[0].anchor_location_ref == "a"
    split_topology = PerspectiveTopology(MapShape(10, 2, False), [
        KnownSquare("a", 0, 0, "land"), KnownSquare("c", 4, 0, "land"),
    ])
    first_split, aliases = region_builder.build(
        split_topology, region_profile, initial, world_revision=2,
    )
    by_anchor = {row.anchor_location_ref: row for row in first_split}
    assert by_anchor["a"].lineage_ref == initial[0].lineage_ref
    assert by_anchor["c"].lineage_ref != initial[0].lineage_ref
    assert not aliases  # one old region has two successors: ambiguous split
    merged, merge_aliases = region_builder.build(
        connected, region_profile, first_split, world_revision=3,
    )
    assert len(merged) == 1
    assert merged[0].lineage_ref == initial[0].lineage_ref
    assert merged[0].lineage_birth_revision == 1
    assert set(merge_aliases) == {row.region_ref for row in first_split
                                  if row.region_ref != merged[0].region_ref}
    second_split, _ = region_builder.build(
        split_topology, region_profile, merged, world_revision=4,
    )
    second_by_anchor = {row.anchor_location_ref: row for row in second_split}
    assert second_by_anchor["a"].lineage_ref == initial[0].lineage_ref
    assert second_by_anchor["c"].lineage_ref not in {
        initial[0].lineage_ref, by_anchor["c"].lineage_ref,
    }
    results["region_split_merge_split_lineage"] = True

    # Perspective-known mobility honors infrastructure, terrain cost, ZOC,
    # airdrops, and special connections without routing through missing tiles.
    mobility_squares = [
        KnownSquare("m0", 0, 0, "land", features=frozenset({"road", "magtube"})),
        KnownSquare("m1", 2, 0, "land", features=frozenset({"road", "magtube"})),
        KnownSquare("m2", 4, 0, "land", features=frozenset({"fungus"}), hostile_zoc=True),
        KnownSquare("m3", 6, 0, "rocky", hostile_zoc=True),
        KnownSquare("r0", 1, 1, "land", features=frozenset({"road"})),
        KnownSquare("r1", 3, 1, "land", features=frozenset({"road"})),
    ]
    mobility_topology = PerspectiveTopology(MapShape(10, 2, False), mobility_squares)
    road = mobility_topology.route("r0", "r1", MobilityProfile("road", "land"))
    tube = mobility_topology.route(
        "m0", "m1", MobilityProfile("tube", "land", magtube_cost=0.0))
    fungus = mobility_topology.route("m1", "m2", MobilityProfile("fungus", "land"))
    blocked = mobility_topology.route("m0", "m3", MobilityProfile("zoc", "land"))
    probe = mobility_topology.route(
        "m0", "m3", MobilityProfile("probe", "land", ignores_zoc=True))
    assert road.movement_cost == 1 / 3
    assert tube.movement_cost == 0.0
    # Conventional land units pay three full terrain movement units in fungus
    # absent Planet/Xeno/native modifiers.
    assert fungus.movement_cost == 3.0
    assert fungus.eta_kind == "stochastic_earliest"
    assert fungus.latest_turns is None
    # Native SMAC land movement permits a unit with at least one ordinary
    # movement point left to enter a more expensive non-fungus square now and
    # exhaust the turn.  It must not be delayed to a fictitious next turn.
    rough_boundary = PerspectiveTopology(MapShape(6, 2, False), [
        KnownSquare("rough-origin", 0, 0, "land"),
        KnownSquare("rough-target", 2, 0, "rocky"),
    ])
    rough_now = rough_boundary.route(
        "rough-origin", "rough-target",
        MobilityProfile("rough-now", "land", movement_points=3, movement_remaining=1),
    )
    rough_later = rough_boundary.route(
        "rough-origin", "rough-target",
        MobilityProfile("rough-later", "land", movement_points=3, movement_remaining=0),
    )
    assert rough_now.turns == 1 and rough_now.latest_turns == 1
    assert rough_later.turns == 2 and rough_later.latest_turns == 2
    assert not blocked.reachable and probe.reachable
    drop = mobility_topology.route("m0", "m3", MobilityProfile(
        "drop", "land", can_airdrop=True,
        airdrop_origin_ref="m0",
        airdrop_destination_refs=frozenset({"m3"}), ignores_zoc=True))
    gate = mobility_topology.route("m0", "m3", MobilityProfile(
        "gate", "land", special_connections=(("m0", "m3", 1.0, "psi_gate"),),
        ignores_zoc=True))
    assert drop.reachable and drop.movement_cost == 1.0
    assert gate.reachable and gate.movement_cost == 1.0
    results["roads_magtubes_fungus_zoc_airdrop_connections"] = True

    # Relationship and movement authority are deliberately distinct. Native
    # land ZOC applies to non-Pact combat units, while only Vendetta contacts
    # enter hostile threat summaries. Identical geometry therefore yields a
    # threat for Vendetta, no threat for Treaty/unknown/Pact, and movement ZOC
    # for every non-Pact relationship.
    def relationship_projection(relation: str) -> tuple[dict, dict]:
        relation_bundle = {
            "turn": 50, "year": 2250,
            "map": {"width": 10, "height": 2, "horizontal_wrap": False},
            "tiles": [
                {"tile_id": 0, "x": 0, "y": 0, "terrain": "land", "visible_now": True},
                {"tile_id": 1, "x": 2, "y": 0, "terrain": "land", "visible_now": True},
                {"tile_id": 2, "x": 4, "y": 0, "terrain": "land", "visible_now": True},
                {"tile_id": 3, "x": 6, "y": 0, "terrain": "land", "visible_now": True},
                {"tile_id": 6, "x": 3, "y": 1, "terrain": "land", "visible_now": True},
            ],
            "bases": [{"id": 0, "base_ref": "base-home", "tile_id": 3,
                       "owned": True, "owner_ref": "faction-1", "name": "Home"}],
            "units": [{"id": 9, "native_observation_key": "relation-contact",
                       "tile_id": 6, "owned": False, "owner_ref": "faction-2",
                       "triad": "land", "movement_points": 1,
                       "roles": {"combat": True, "probe": False}}],
            "factions": [
                {"id": 1, "faction_ref": "faction-1", "owned": True},
                {"id": 2, "faction_ref": "faction-2", "owned": False,
                 "relations": {
                     "vendetta": relation == "hostile", "pact": relation == "allied",
                     "treaty": relation == "neutral", "truce": False,
                 }},
            ],
        }
        from smacx_world_model import PerspectiveProjector
        from smacx_world_types import WorldIdentity
        projected = PerspectiveProjector(WorldIdentity(
            "match-relation", "perspective-relation", "timeline-main",
            f"world-{relation}",
        )).project(relation_bundle, observation_sequence=1)
        rows = {row.object_ref: row.as_dict(provider_safe=True)
                for row in projected["objects"]}
        relation_topology = PerspectiveTopology(
            MapShape(10, 2, False), projected["known_squares"],
        )
        return rows, {
            "route": relation_topology.route(
                "location-1", "location-2", MobilityProfile("land", "land"),
            ),
            "bases": base_mechanics(relation_topology, rows, ["base-home"]),
        }

    relation_results = {name: relationship_projection(name)
                        for name in ("hostile", "allied", "neutral", "unknown")}
    assert relation_results["hostile"][1]["bases"][0]["visible_hostile_response"]
    assert all(not relation_results[name][1]["bases"][0]["visible_hostile_response"]
               for name in ("allied", "neutral", "unknown"))
    assert not relation_results["hostile"][1]["route"].reachable
    assert not relation_results["neutral"][1]["route"].reachable
    assert not relation_results["unknown"][1]["route"].reachable
    assert relation_results["allied"][1]["route"].reachable
    results["relation_aware_threat_and_native_zoc"] = True

    # Peninsula defense: a single known connector is mechanical, not a verdict.
    shape = MapShape(16, 8, False)
    peninsula = [KnownSquare("west", 0, 2, "land"), KnownSquare("neck", 2, 2, "land"),
                 KnownSquare("east", 4, 2, "land"), KnownSquare("north", 2, 0, "ocean"),
                 KnownSquare("south", 2, 4, "ocean")]
    topo = PerspectiveTopology(shape, peninsula)
    connectors = connector_analysis(topo, MobilityProfile("land", "land"))
    assert any(row["location_ref"] == "neck" for row in connectors)
    assert all("best" not in canonical.lower() for canonical in map(json.dumps, connectors))
    results["peninsula_defense"] = True

    # Two bases and one reserve: expose arrival windows and production, leave priority sovereign.
    squares = grid(32, 16)
    topo = PerspectiveTopology(MapShape(32, 16, False), squares)
    objects = {
        "base-a": item("base-a", "base", "location-0", owner_ref="faction-1",
                       production_name="Defender", production_cost=20,
                       minerals_accumulated=10, mineral_surplus=5),
        "base-b": item("base-b", "base", "location-100", owner_ref="faction-1",
                       production_name="Defender", production_cost=40,
                       minerals_accumulated=5, mineral_surplus=5),
        "reserve": item("reserve", "own_unit", "location-34", owner_ref="faction-1",
                        triad="land", movement_points=2, roles={"combat": True}),
        "threat-a": item("threat-a", "foreign_contact", "location-1", owner_ref="faction-2",
                         triad="land", movement_points=2, relationship="hostile"),
        "threat-b": item("threat-b", "foreign_contact", "location-99", owner_ref="faction-2",
                         triad="land", movement_points=1, relationship="hostile"),
    }
    bases = base_mechanics(topo, objects, ["base-a", "base-b"])
    response = response_matrix(topo, objects, ["reserve"], ["base-a", "base-b"], "land")
    assert len(bases) == 2 and len(response[0]["responses"]) == 2
    assert all("recommend" not in json.dumps(row).lower() for row in bases)
    results["two_threatened_bases_one_reserve"] = True

    # Ally rendezvous: reported intent stays distinct from observed mechanics.
    objects["ally"] = item("ally", "foreign_contact", "location-68",
                           owner_ref="faction-3", triad="land", movement_points=2)
    rendezvous = rendezvous_matrix(topo, objects, ["reserve", "ally"],
                                   ["location-102"], "land")
    assert len(rendezvous[0]["arrivals"]) == 2
    claim = {"object_ref": "claim-ally", "kind": "claim",
             "fields": {"content": field("I will arrive", "reported", "player_report")}}
    assert claim["fields"]["content"]["epistemic_status"] == "reported"
    results["ally_rendezvous"] = True

    # Fog pursuit: a lost contact receives an explicit possibility envelope only.
    lost = item("retired-contact", "foreign_contact", "location-34",
                owner_ref="faction-2", triad="land", movement_points=2, last_seen_turn=48)
    lost["status"] = "lost"
    fog = lost_contact_envelopes(topo, {"retired-contact": lost}, current_turn=50)[0]
    assert fog["epistemic_status"] == "estimated" and "retired" in fog["identity_continuity"]
    results["fog_pursuit"] = True

    # Multi-front and global races remain simultaneously represented.  The
    # theater assertions are intentionally mechanical: a connected
    # land/ocean/land operation spans mobility regions, allied participation
    # is explicit, and a separate quiet plan-linked base remains promoted.
    busy = [
        KnownSquare("west-front", 0, 2, "land"),
        KnownSquare("front-sea-a", 1, 3, "ocean"),
        KnownSquare("front-sea-b", 3, 3, "ocean"),
        KnownSquare("east-front", 4, 2, "land"),
        KnownSquare("quiet-plan", 16, 6, "land"),
        KnownSquare("quiet-unrelated", 10, 0, "land"),
    ]
    world_objects = [
        *[item(square.location_ref, "location", None, terrain=square.terrain,
               features=[]) for square in busy],
        item("front-a", "foreign_contact", "west-front", owner_ref="faction-2",
             relationship="hostile", triad="land"),
        item("front-ally", "foreign_contact", "front-sea-a", owner_ref="faction-3",
             relationship="allied", triad="sea"),
        item("front-b", "foreign_contact", "east-front", owner_ref="faction-2",
             relationship="hostile", triad="land"),
        item("quiet-plan-base", "base", "quiet-plan", owner_ref="faction-1"),
        item("project-race", "project", None, name="Weather Paradigm", state="building"),
        item("council", "council_state", None, state={"governor_vote_due": True}),
    ]
    anchor = SemanticLodProjector(context_tier="64k").build(
        projection(32, 16, busy, world_objects),
        active_plan_refs=["quiet-plan-base"], recent_material_refs=["front-b"],
    )
    assert anchor["planet"]["active_theater_count"] >= 2
    assert any(len(row["region_refs"]) > 1 and row["allied_faction_refs"]
               for row in anchor["active_theaters"])
    assert any("quiet-plan-base" in row["promoted_by_refs"]
               for row in anchor["active_theaters"])
    assert any("front-b" in row["recent_material_refs"]
               for row in anchor["active_theaters"])
    assert any(row["lod_level"] == "geographic"
               for row in anchor["physical_masses"])
    assert {row["kind"] for row in anchor["strategic_objects"]} >= {"project", "council_state"}
    results["multi_front_warfare"] = results["project_global_race"] = True

    # Expansion/base-site comparison supplies affordances without a universal ranking.
    site_objects = {**objects}
    for ref in ("location-4", "location-6", "location-8"):
        square = next(value for value in squares if value.location_ref == ref)
        site_objects[ref] = item(ref, "location", None, terrain=square.terrain,
                                 features=["river"] if ref == "location-6" else [])
    site_objects["nearby-hostile"] = item(
        "nearby-hostile", "foreign_contact", "location-10",
        owner_ref="faction-2", relationship="hostile", triad="land",
    )
    receipts = {
        "location-4": {
            "legal_for_land_colony": True, "legal_for_sea_colony": False,
            "current_tile_yields": {"nutrients": 2, "minerals": 1, "energy": 1},
            "known_radius": [], "known_radius_location_count": 21,
            "radius_complete_currently_visible": True,
            "overlapping_known_bases": [],
        },
        "location-6": {
            "legal_for_land_colony": True, "legal_for_sea_colony": False,
            "current_tile_yields": {"nutrients": 3, "minerals": 1, "energy": 2},
            "known_radius": [], "known_radius_location_count": 21,
            "radius_complete_currently_visible": True,
            "overlapping_known_bases": [
                {"base_ref": "base-a", "overlapping_radius_location_count": 8},
            ],
        },
        "location-8": {
            "legal_for_land_colony": False, "legal_for_sea_colony": False,
            "current_tile_yields": {"nutrients": 1, "minerals": 2, "energy": 0},
            "known_radius": [], "known_radius_location_count": 10,
            "radius_complete_currently_visible": False,
            "overlapping_known_bases": [],
        },
    }
    sites = location_affordances(
        topo, site_objects, ["location-4", "location-6", "location-8"],
        native_receipts=receipts,
        physical_mass_by_location={
            "location-4": "landmass-home", "location-6": "landmass-home",
            "location-8": "landmass-other",
        },
        mobility_region_by_location={
            "location-4": ["region-home"], "location-6": ["region-home"],
            "location-8": ["region-other"],
        },
    )
    by_site = {row["location_ref"]: row for row in sites}
    assert by_site["location-4"]["founding_buildability"]["legal_for_land_colony"] is True
    assert by_site["location-6"]["overlapping_known_base_radii"]
    assert by_site["location-8"]["founding_buildability"]["legal_for_land_colony"] is False
    assert by_site["location-4"]["physical_mass_ref"] != \
        by_site["location-8"]["physical_mass_ref"]
    assert by_site["location-6"]["known_current_tile_yields"] != \
        by_site["location-8"]["known_current_tile_yields"]
    assert all("best" not in json.dumps(row).lower() for row in sites)
    results["expansion_site_reasoning"] = True

    # Transport, air recovery, and special connection mechanics.
    ocean_topology = PerspectiveTopology(MapShape(16, 8, False), [
        KnownSquare("land-a", 0, 2, "land", features=frozenset({"base"})),
        KnownSquare("sea-a", 2, 2, "ocean"),
        KnownSquare("sea-b", 4, 2, "ocean"), KnownSquare("land-b", 6, 2, "land"),
    ])
    assert not ocean_topology.route("land-a", "land-b", MobilityProfile("land", "land")).reachable
    assert ocean_topology.route("sea-a", "sea-b", MobilityProfile("sea", "sea")).reachable
    air = MobilityProfile("air", "air", movement_points=2, air_safe_range=4,
                          air_origin_refuels=True,
                          refuel_location_refs=frozenset({"land-b"}))
    assert ocean_topology.route("land-a", "land-b", air).reachable
    crossing_objects = {
        "port-base": item("port-base", "base", "land-a",
                          owner_ref="faction-1", coastal=True),
        "passenger": item("passenger", "own_unit", "land-a", owner_ref="faction-1",
                          triad="land", movement_points=1,
                          roles={"combat": True, "amphibious": False}),
        "transport": item("transport", "own_unit", "land-a", owner_ref="faction-1",
                          triad="sea", movement_points=2,
                          roles={"transport": True}, cargo={"capacity": 4, "loaded": 1}),
        "land-b": item("land-b", "location", None, terrain="land"),
    }
    crossing = transport_route(
        ocean_topology, crossing_objects, "passenger", "land-b",
    )
    assert crossing is not None and crossing["transport_ref"] == "transport"
    assert crossing["capacity"]["available"] == 3
    assert crossing["eta_kind"] == "exact_serialized_guarded_schedule"
    full_transport = {**crossing_objects, "transport": item(
        "transport", "own_unit", "land-a", owner_ref="faction-1", triad="sea",
        movement_points=2, roles={"transport": True}, cargo={"capacity": 4, "loaded": 4},
    )}
    assert transport_route(
        ocean_topology, full_transport, "passenger", "land-b",
    )["reachable"] is False

    pact_topology = PerspectiveTopology(MapShape(16, 8, False), [
        KnownSquare("owned-port", 0, 2, "land", features=frozenset({"base"})),
        KnownSquare("land-start", 2, 2, "land"),
        KnownSquare("pact-port", 4, 2, "land", features=frozenset({"base"})),
        KnownSquare("target-coast", 6, 2, "land"),
        KnownSquare("sea-start", 1, 3, "ocean"),
        KnownSquare("sea-mid", 3, 3, "ocean"),
        KnownSquare("sea-end", 5, 3, "ocean"),
    ])
    actors = {
        "passenger": item("passenger", "own_unit", "land-start",
                          owner_ref="faction-1", triad="land", movement_points=2,
                          roles={"combat": True, "amphibious": False}),
        "transport": item("transport", "own_unit", "sea-start",
                          owner_ref="faction-1", triad="sea", movement_points=2,
                          roles={"transport": True}, cargo={"capacity": 2, "loaded": 0}),
        "target-coast": item("target-coast", "location", None, terrain="land"),
        "faction-2": item("faction-2", "faction", None, relationship="allied"),
    }
    owned_only = {**actors, "owned-base": item(
        "owned-base", "base", "owned-port", owner_ref="faction-1", coastal=True,
    )}
    owned_route = transport_route(pact_topology, owned_only, "passenger", "target-coast")
    assert owned_route and owned_route["reachable"] \
        and owned_route["embark"]["base_access"] == "current_owned_coastal_base"
    pact_only = {**actors, "pact-base": item(
        "pact-base", "base", "pact-port", owner_ref="faction-2", coastal=True,
    )}
    pact_route = transport_route(pact_topology, pact_only, "passenger", "target-coast")
    assert pact_route and pact_route["reachable"] \
        and pact_route["embark"]["base_access"] == "current_pact_coastal_base" \
        and pact_route["embark"]["relationship_dependency_hash"]
    both_route = transport_route(
        pact_topology, {**owned_only, **pact_only}, "passenger", "target-coast",
    )
    assert both_route and both_route["reachable"]
    assert both_route["eta_turns"] == min(
        owned_route["eta_turns"], pact_route["eta_turns"],
    )
    assert both_route["embark"]["base_ref"] in {"owned-base", "pact-base"}
    broken = {**pact_only, "faction-2": item(
        "faction-2", "faction", None, relationship="neutral",
    )}
    assert transport_route(
        pact_topology, broken, "passenger", "target-coast",
    )["reachable"] is False
    stale_faction = item("faction-2", "faction", None, relationship="allied")
    stale_faction["fields"]["relationship"] = field(
        "allied", status="stale", source="stale_map",
    )
    stale_base = item(
        "pact-base", "base", "pact-port", owner_ref="faction-2", coastal=True,
    )
    stale_base["fields"]["coastal"] = field(
        True, status="stale", source="stale_map",
    )
    assert transport_route(
        pact_topology, {**actors, "faction-2": stale_faction,
                        "pact-base": stale_base},
        "passenger", "target-coast",
    )["reachable"] is False
    results["current_pact_coastal_port_routing"] = True

    drop_objects = {
        "dropper": item("dropper", "own_unit", "land-a", owner_ref="faction-1",
                        triad="land", movement_points=1,
                        roles={"combat": True, "airdrop_capable": True},
                        airdrop_ready=True, airdrop_range=3),
    }
    drop_profile = mobility_profile(
        drop_objects, "drop", subject_ref="dropper", topology=ocean_topology,
    )
    assert drop_profile.can_airdrop and "land-b" in drop_profile.airdrop_destination_refs
    results["transport_island_logistics"] = results["air_carrier_refueling"] = True

    # Ecology changes produce a different region version/topology projection.
    before = SemanticLodProjector(context_tier="64k").build(
        projection(16, 8, peninsula, [], revision=1))
    mutated = [*peninsula[:-1], KnownSquare("south", 2, 4, "land")]
    after = SemanticLodProjector(context_tier="64k").build(
        projection(16, 8, mutated, [], revision=2),
        previous_regions=before.pop("_region_projection"))
    assert after["world_revision"] == 2
    results["ecology_sea_level_change"] = True

    # Scenario/Alien/global objectives are first-class strategic objects.
    alien = [item("scenario", "scenario_rules", None,
                  state={"progenitor_objective": "resonance", "cooperative_victory": False}),
             item("victory", "victory_state", None, state={"transcendence": "available"})]
    alien_anchor = SemanticLodProjector(context_tier="64k").build(
        projection(16, 8, peninsula, alien))
    assert {row["kind"] for row in alien_anchor["strategic_objects"]} == {
        "scenario_rules", "victory_state"}
    results["alien_crossfire_objectives"] = True

    # Quiet and chaotic Huge worlds remain bounded and advertise omitted detail.
    huge = grid(320, 160)
    small = grid(32, 16)
    quiet_small = SemanticLodProjector(context_tier="64k").build(
        projection(32, 16, small, []))
    quiet_huge = SemanticLodProjector(context_tier="64k").build(
        projection(320, 160, huge, []))
    assert quiet_huge["token_estimate"] <= quiet_small["token_estimate"] * 1.15
    chaotic_objects = []
    for index in range(300):
        loc = huge[(index * 79) % len(huge)].location_ref
        chaotic_objects.append(item(f"contact-{index}", "foreign_contact", loc,
                                      owner_ref=f"faction-{2 + index % 6}", relationship="hostile"))
    for index in range(80):
        loc = huge[(index * 131) % len(huge)].location_ref
        chaotic_objects.append(item(f"base-{index}", "base", loc,
                                      owner_ref=f"faction-{1 + index % 7}", population=4))
    huge_chaotic = SemanticLodProjector(context_tier="64k").build(
        projection(320, 160, huge, chaotic_objects))
    assert huge_chaotic["token_estimate"] <= 6000
    assert huge_chaotic["lod"]["strategic_objects_truncated"]
    results["huge_quiet_world"] = results["huge_chaotic_world"] = True

    print(json.dumps({"event": "pass", "payload": results}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
