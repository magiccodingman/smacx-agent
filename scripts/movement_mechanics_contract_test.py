#!/usr/bin/env python3
"""Production-shaped deterministic movement, fuel, gate, and logistics contracts."""

from __future__ import annotations

import json

from smacx_mechanics import base_mechanics, logistics, mobility_profile, transport_route
from smacx_topology import KnownSquare, MapShape, MobilityProfile, PerspectiveTopology
from smacx_world_model import PerspectiveProjector
from smacx_world_types import WorldIdentity


def field(value):
    return {"value": value, "epistemic_status": "current", "source": "owned_state"}


def obj(ref: str, kind: str, location: str | None = None, **values):
    return {"object_ref": ref, "kind": kind, "location_ref": location,
            "status": "active", "fields": {key: field(value) for key, value in values.items()}}


def main() -> int:
    results: dict[str, bool] = {}

    # Exact native movement-cost mapping: infrastructure applies to land only;
    # river roads, forest/rock, conventional/native fungus and sea-shelf fungus
    # retain their distinct costs.
    terrain = PerspectiveTopology(MapShape(12, 4, False), [
        KnownSquare("plain", 0, 0, "land"),
        KnownSquare("road-a", 2, 0, "land", features=frozenset({"road"})),
        KnownSquare("road-b", 4, 0, "land", features=frozenset({"road"})),
        KnownSquare("tube", 6, 0, "land", features=frozenset({"road", "magtube"})),
        KnownSquare("forest", 1, 1, "land", features=frozenset({"forest"})),
        KnownSquare("rocky", 3, 1, "rocky", features=frozenset({"rocky"})),
        KnownSquare("fungus", 5, 1, "land", features=frozenset({"fungus"})),
        KnownSquare("river-a", 7, 1, "land", features=frozenset({"river"})),
        KnownSquare("river-b", 8, 2, "land", features=frozenset({"river"})),
        KnownSquare("shelf-fungus", 9, 1, "ocean", features=frozenset({"fungus"}), altitude=2),
        KnownSquare("deep-fungus", 10, 2, "ocean", features=frozenset({"fungus"}), altitude=1),
    ])
    land = MobilityProfile("land", "land", movement_points=3, road_cost=1 / 3,
                           fungus_cost=3)
    assert terrain._cost(terrain.by_ref["road-a"], terrain.by_ref["road-b"], land) == 1 / 3
    assert terrain._cost(terrain.by_ref["forest"], terrain.by_ref["rocky"], land) == 2
    assert terrain._cost(terrain.by_ref["plain"], terrain.by_ref["forest"], land) == 2
    assert terrain._cost(terrain.by_ref["rocky"], terrain.by_ref["fungus"], land) == 3
    native = MobilityProfile("native", "land", movement_points=1, fungus_cost=1)
    assert terrain._cost(terrain.by_ref["rocky"], terrain.by_ref["fungus"], native) == 1
    hover = MobilityProfile("hover", "land", movement_points=3, ignores_rough_movement=True)
    assert terrain._cost(terrain.by_ref["forest"], terrain.by_ref["rocky"], hover) == 1
    assert terrain._cost(terrain.by_ref["river-a"], terrain.by_ref["river-b"], land) == 1 / 3
    sea = MobilityProfile("sea", "sea", movement_points=3, fungus_cost=3,
                          road_cost=1 / 3, magtube_cost=0)
    assert terrain._cost(terrain.by_ref["shelf-fungus"], terrain.by_ref["deep-fungus"], sea) == 1
    assert terrain._cost(terrain.by_ref["deep-fungus"], terrain.by_ref["shelf-fungus"], sea) == 3
    assert terrain._cost(terrain.by_ref["road-a"], terrain.by_ref["road-b"], sea) == 1
    results["native_terrain_and_triad_costs"] = True

    # Stateful turn boundaries preserve native over-cost/stochastic semantics.
    boundary = PerspectiveTopology(MapShape(10, 2, False), [
        KnownSquare("b0", 0, 0, "land"), KnownSquare("b1", 2, 0, "rocky"),
        KnownSquare("b2", 4, 0, "land"), KnownSquare("b3", 6, 0, "land"),
    ])
    over = boundary.route("b0", "b1", MobilityProfile(
        "over", "land", movement_points=3, movement_remaining=1,
    ))
    exhausted = boundary.route("b0", "b1", MobilityProfile(
        "exhausted", "land", movement_points=3, movement_remaining=0,
    ))
    two_turn = boundary.route("b0", "b3", MobilityProfile(
        "two-turn", "land", movement_points=2,
    ))
    assert over.turns == 1 and exhausted.turns == 2 and two_turn.turns == 2
    results["stateful_turn_boundary_eta"] = True

    # Airdrops are origin-only, consume the action turn, avoid occupied
    # destinations, and cannot magically originate after ordinary movement.
    drop_topology = PerspectiveTopology(MapShape(12, 2, False), [
        KnownSquare("drop-origin", 0, 0, "land", features=frozenset({"base"})),
        KnownSquare("drop-mid", 2, 0, "land"),
        KnownSquare("drop-target", 8, 0, "land"),
        KnownSquare("drop-blocked", 10, 0, "land", blocking_contact_occupied=True),
    ])
    drop_profile = MobilityProfile(
        "drop", "land", movement_points=1, can_airdrop=True,
        airdrop_origin_ref="drop-origin",
        airdrop_destination_refs=frozenset({"drop-target"}),
    )
    assert drop_topology.route("drop-origin", "drop-target", drop_profile).turns == 1
    assert drop_topology.route("drop-mid", "drop-target", drop_profile).turns == 2
    results["airdrop_origin_and_guard_boundary"] = True

    # Psi Gates are emitted only from an unused source and only to a
    # triad-compatible owned destination. Arriving at a gate after moving
    # defers teleportation to the following turn.
    gate_objects = {
        "faction-1": obj("faction-1", "faction", is_self=True),
        "gate-source": obj("gate-source", "base", "g0", owner_ref="faction-1",
                           facilities=[{"facility_id": 33, "name": "Psi Gate"}],
                           psi_gate_ready=True, is_ocean=False, coastal=True),
        "gate-target": obj("gate-target", "base", "g3", owner_ref="faction-1",
                           facilities=[{"facility_id": 33, "name": "Psi Gate"}],
                           psi_gate_ready=False, is_ocean=False, coastal=True),
        "walker": obj("walker", "own_unit", "g0", owner_ref="faction-1", triad="land",
                      movement_points=3, movement_scale=3, moves_remaining=3, roles={}),
    }
    gate_topology = PerspectiveTopology(MapShape(10, 2, False), [
        KnownSquare("g-before", 0, 0, "land"),
        KnownSquare("g0", 2, 0, "land", features=frozenset({"base"})),
        KnownSquare("g3", 8, 0, "land", features=frozenset({"base"})),
    ])
    gate_profile = mobility_profile(gate_objects, "walker", subject_ref="walker",
                                    topology=gate_topology)
    assert gate_topology.route("g0", "g3", gate_profile).turns == 1
    assert gate_topology.route("g-before", "g3", gate_profile).turns == 2
    results["psi_gate_readiness_and_fresh_turn"] = True

    # Fuel is stateful across turns and may be restored only by ending a turn
    # at a legitimate stationary refuel point. Non-refuelling targets retain a
    # safe recovery path; a carrier dependency is explicit and conditional.
    air_topology = PerspectiveTopology(MapShape(14, 2, False), [
        KnownSquare(f"a{index}", index * 2, 0, "land") for index in range(7)
    ])
    hop = MobilityProfile(
        "needlejet", "air", movement_points=2, movement_remaining=2,
        air_safe_range=4, air_full_safe_range=4, air_origin_refuels=True,
        refuel_location_refs=frozenset({"a0", "a2"}),
    )
    safe_hop = air_topology.route("a0", "a4", hop)
    unsafe_sortie = air_topology.route("a0", "a3", MobilityProfile(
        "needlejet-no-hop", "air", movement_points=2, air_safe_range=4,
        air_full_safe_range=4, air_origin_refuels=True,
        refuel_location_refs=frozenset({"a0"}),
    ))
    carrier_hop = air_topology.route("a0", "a4", MobilityProfile(
        "carrier-hop", "air", movement_points=2, air_safe_range=4,
        air_full_safe_range=4, air_origin_refuels=True,
        refuel_location_refs=frozenset({"a0", "a2"}),
        mobile_refuel_location_refs=frozenset({"a2"}),
    ))
    assert safe_hop.reachable and safe_hop.turns == 2 and "a2" in safe_hop.path
    assert not unsafe_sortie.reachable
    assert carrier_hop.eta_kind == "conditional_known_state" and carrier_hop.uncertainty
    results["fuel_refuel_and_carrier_dependencies"] = True

    # A production-native-shaped bundle carries movement traits, actual
    # support flags/cost, convoy flow and transport capacity through the real
    # projector into calculators. Clean Reactor is support-only, not ZOC
    # immunity; probe/cloak are the actual projected land-ZOC exceptions.
    identity = WorldIdentity("match-move", "perspective-move", "timeline-main", "world-move")
    bundle = {
        "turn": 7, "year": 2107,
        "map": {"width": 12, "height": 4, "horizontal_wrap": False},
        "tiles": [
            {"tile_id": 0, "x": 0, "y": 0, "visible_now": True,
             "is_ocean": False, "altitude": 3, "features": ["base"]},
            {"tile_id": 1, "x": 2, "y": 0, "visible_now": True,
             "is_ocean": False, "altitude": 3, "features": ["road"]},
            {"tile_id": 2, "x": 4, "y": 0, "visible_now": True,
             "is_ocean": True, "altitude": 2, "features": ["fungus"]},
            {"tile_id": 3, "x": 6, "y": 0, "visible_now": True,
             "is_ocean": True, "altitude": 2, "features": []},
            {"tile_id": 4, "x": 8, "y": 0, "visible_now": True,
             "is_ocean": False, "altitude": 3, "features": []},
        ],
        "bases": [{"id": 0, "base_ref": "base-home", "tile_id": 0,
                   "owned": True, "owner_ref": "faction-1", "name": "Home",
                   "unit_support_cost": 1}],
        "units": [
            {"id": 1, "own_unit_ref": "clean", "native_observation_key": "vehicle-handle-1",
             "tile_id": 0, "owned": True, "owner_ref": "faction-1", "triad": "land",
             "movement_points": 3, "movement_scale": 3, "moves_remaining": 3,
             "abilities": ["clean_reactor"], "roles": {"combat": True},
             "home_base_ref": "base-home", "requires_support": False},
            {"id": 2, "own_unit_ref": "supported", "native_observation_key": "vehicle-handle-2",
             "tile_id": 0, "owned": True, "owner_ref": "faction-1", "triad": "land",
             "movement_points": 3, "movement_scale": 3, "moves_remaining": 3,
             "roles": {"combat": True}, "home_base_ref": "base-home",
             "requires_support": True},
            {"id": 3, "own_unit_ref": "crawler", "native_observation_key": "vehicle-handle-3",
             "tile_id": 1, "owned": True, "owner_ref": "faction-1", "triad": "land",
             "movement_points": 3, "movement_scale": 3, "moves_remaining": 3,
             "roles": {"supply": True}, "home_base_ref": "base-home",
             "convoy_resource": "minerals", "convoy_amount": 2,
             "convoy_source_location_ref": "location-1",
             "convoy_destination_base_ref": "base-home",
             "convoy_base_effect": {"resource": "minerals", "amount": 2}},
            {"id": 4, "own_unit_ref": "transport", "native_observation_key": "vehicle-handle-4",
             "tile_id": 2, "owned": True, "owner_ref": "faction-1", "triad": "sea",
             "movement_points": 6, "movement_scale": 3, "moves_remaining": 6,
             "roles": {"transport": True}, "cargo": {"capacity": 4, "loaded": 0}},
        ],
        "factions": [{"id": 1, "faction_ref": "faction-1", "owned": True}],
        "global": [],
    }
    projected = PerspectiveProjector(identity).project(bundle, observation_sequence=1)
    objects = {row.object_ref: row.as_dict(provider_safe=False) for row in projected["objects"]}
    topology = PerspectiveTopology(MapShape(12, 4, False), projected["known_squares"])
    base = base_mechanics(topology, objects, ["base-home"])[0]
    logistics_row = logistics(objects, topology, ["supported", "location-4"])
    clean_profile = mobility_profile(objects, "clean", subject_ref="clean", topology=topology)
    assert not clean_profile.ignores_zoc
    assert base["support_burden"] == 1 and base["support_mineral_cost"] == 1
    assert logistics_row["convoys"][0]["base_effect"] == {"resource": "minerals", "amount": 2}
    assert mobility_profile(objects, "supported", subject_ref="supported",
                            topology=topology).can_embark
    assert transport_route(topology, objects, "supported", "location-4") is not None
    results["production_projection_support_convoy_transport"] = True

    # Profile-aware connectivity and scenario map shape remain deterministic.
    coast = PerspectiveTopology(MapShape(8, 2, True), [
        KnownSquare("land", 0, 0, "land"),
        KnownSquare("sea", 2, 0, "ocean"),
        KnownSquare("coastal-base", 4, 0, "land", features=frozenset({"base"})),
        KnownSquare("sea-wrap", 6, 0, "ocean"),
    ])
    land_regions = coast.connected_components(MobilityProfile("land", "land"))
    sea_regions = coast.connected_components(MobilityProfile("sea", "sea"))
    assert {frozenset(row) for row in land_regions} != {frozenset(row) for row in sea_regions}
    assert coast.shape.distance((0, 0), (6, 0)) == 1
    results["mobility_profile_regions_and_wrap"] = True

    print(json.dumps({"event": "pass", "payload": results}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
