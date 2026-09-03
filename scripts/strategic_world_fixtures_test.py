#!/usr/bin/env python3
"""Mechanically check the locked strategic-perception scenario fixtures."""

from __future__ import annotations

import json

from smacx_mechanics import (
    base_mechanics, connector_analysis, location_affordances, lost_contact_envelopes,
    rendezvous_matrix, response_matrix,
)
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

    # Perspective-known mobility honors infrastructure, terrain cost, ZOC,
    # airdrops, and special connections without routing through missing tiles.
    mobility_squares = [
        KnownSquare("m0", 0, 0, "land", features=frozenset({"road", "magtube"})),
        KnownSquare("m1", 2, 0, "land", features=frozenset({"road", "magtube"})),
        KnownSquare("m2", 4, 0, "land", features=frozenset({"fungus"})),
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
    clean = mobility_topology.route(
        "m0", "m3", MobilityProfile("clean", "land", ignores_zoc=True))
    assert road.movement_cost == 1 / 3
    assert tube.movement_cost == 0.0
    assert fungus.movement_cost == 2.0
    assert not blocked.reachable and clean.reachable
    drop = mobility_topology.route("m0", "m3", MobilityProfile(
        "drop", "land", can_airdrop=True,
        airdrop_destination_refs=frozenset({"m3"}), ignores_zoc=True))
    gate = mobility_topology.route("m0", "m3", MobilityProfile(
        "gate", "land", special_connections=(("m0", "m3", 1.0, "psi_gate"),),
        ignores_zoc=True))
    assert drop.reachable and drop.movement_cost == 1.0
    assert gate.reachable and gate.movement_cost == 1.0
    results["roads_magtubes_fungus_zoc_airdrop_connections"] = True

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
                         triad="land", movement_points=2),
        "threat-b": item("threat-b", "foreign_contact", "location-99", owner_ref="faction-2",
                         triad="land", movement_points=1),
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

    # Multi-front and global races remain simultaneously represented.
    busy = [*squares]
    world_objects = [
        item("front-a", "foreign_contact", "location-1", owner_ref="faction-2"),
        item("front-b", "foreign_contact", "location-239", owner_ref="faction-3"),
        item("project-race", "project", None, name="Weather Paradigm", state="building"),
        item("council", "council_state", None, state={"governor_vote_due": True}),
    ]
    anchor = SemanticLodProjector(context_tier="64k").build(
        projection(32, 16, busy, world_objects))
    assert anchor["planet"]["active_theater_count"] >= 1
    assert {row["kind"] for row in anchor["strategic_objects"]} >= {"project", "council_state"}
    results["multi_front_warfare"] = results["project_global_race"] = True

    # Expansion/base-site comparison supplies affordances without a universal ranking.
    site_objects = {**objects}
    for ref in ("location-4", "location-6", "location-8"):
        square = next(value for value in squares if value.location_ref == ref)
        site_objects[ref] = item(ref, "location", None, terrain=square.terrain,
                                 features=["river"] if ref == "location-6" else [])
    sites = location_affordances(topo, site_objects, ["location-4", "location-6", "location-8"])
    assert len(sites) == 3 and all("no site ranking" in row["strategy_boundary"] for row in sites)
    results["expansion_site_reasoning"] = True

    # Transport, air recovery, and special connection mechanics.
    ocean_topology = PerspectiveTopology(MapShape(16, 8, False), [
        KnownSquare("land-a", 0, 2, "land"), KnownSquare("sea-a", 2, 2, "ocean"),
        KnownSquare("sea-b", 4, 2, "ocean"), KnownSquare("land-b", 6, 2, "land"),
    ])
    assert not ocean_topology.route("land-a", "land-b", MobilityProfile("land", "land")).reachable
    assert ocean_topology.route("sea-a", "sea-b", MobilityProfile("sea", "sea")).reachable
    air = MobilityProfile("air", "air", movement_points=2, max_air_turns=1,
                          refuel_location_refs=frozenset({"land-b"}))
    assert ocean_topology.route("land-a", "land-b", air).reachable
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
                                      owner_ref=f"faction-{2 + index % 6}"))
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
