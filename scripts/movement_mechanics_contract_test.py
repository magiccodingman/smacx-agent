#!/usr/bin/env python3
"""Production-shaped deterministic movement, fuel, gate, and logistics contracts."""

from __future__ import annotations

import json

from smacx_mechanics import (
    base_mechanics, logistics, lost_contact_envelopes, mobility_profile, transport_route,
)
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
    for origin, profile in (
        ("plain", land), ("plain", native), ("plain", hover),
        ("shelf-fungus", sea),
    ):
        arrivals = terrain.arrival_map(origin, profile, max_turns=16)
        for target in terrain.by_ref:
            route = terrain.route(origin, target, profile)
            assert route.reachable == (target in arrivals)
            if route.reachable:
                assert route.turns == arrivals[target]["turns"]
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
    for profile in (
        MobilityProfile("flat-parity", "land", movement_points=2),
        MobilityProfile("remaining-parity", "land", movement_points=3,
                        movement_remaining=1),
    ):
        arrivals = boundary.arrival_map("b0", profile, max_turns=8)
        for target in boundary.by_ref:
            route = boundary.route("b0", target, profile)
            assert route.reachable == (target in arrivals)
            if route.reachable:
                assert route.turns == arrivals[target]["turns"]
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

    # Foreign movement is evaluated in the moving faction's access frame, not
    # the sovereign's Pact frame. Even a Pact aircraft cannot borrow our base
    # or carrier merely because that faction is allied to us.
    access_objects = {
        "faction-1": obj("faction-1", "faction", relationship="allied"),
        "faction-2": obj("faction-2", "faction", relationship="allied"),
        "faction-3": obj("faction-3", "faction", relationship="neutral"),
        "faction-4": obj("faction-4", "faction", relationship="hostile"),
    }
    for faction_no, location in enumerate(("a0", "a1", "a2", "a3"), start=1):
        access_objects[f"base-{faction_no}"] = obj(
            f"base-{faction_no}", "base", location,
            owner_ref=f"faction-{faction_no}",
            facilities=[{"facility_id": 33, "name": "Psi Gate"}],
            psi_gate_ready=True, is_ocean=False, coastal=True,
        )
    access_objects.update({
        "our-carrier": obj("our-carrier", "own_unit", "a4", owner_ref="faction-1",
                           triad="sea", roles={"carrier": True},
                           cargo={"capacity": 4, "loaded": 0}),
        "hostile-carrier": obj("hostile-carrier", "foreign_contact", "a5",
                               owner_ref="faction-4", triad="sea",
                               roles={"carrier": True}, cargo={"capacity": 4, "loaded": 1}),
    })
    for faction_no in range(1, 5):
        kind = "own_unit" if faction_no == 1 else "foreign_contact"
        ref = f"aircraft-{faction_no}"
        access_objects[ref] = obj(
            ref, kind, f"a{faction_no - 1}", owner_ref=f"faction-{faction_no}",
            triad="air", movement_points=2, moves_remaining=2,
            air_safe_range=3, air_full_safe_range=3, roles={"combat": True},
        )
    own_air = mobility_profile(access_objects, "own-air", subject_ref="aircraft-1",
                               topology=air_topology)
    pact_air = mobility_profile(access_objects, "pact-air", subject_ref="aircraft-2",
                                topology=air_topology)
    treaty_air = mobility_profile(access_objects, "treaty-air", subject_ref="aircraft-3",
                                  topology=air_topology)
    hostile_air = mobility_profile(access_objects, "hostile-air", subject_ref="aircraft-4",
                                   topology=air_topology)
    assert {"a0", "a1", "a4"}.issubset(own_air.refuel_location_refs)
    assert pact_air.refuel_location_refs == frozenset({"a1"})
    assert treaty_air.refuel_location_refs == frozenset({"a2"})
    assert hostile_air.refuel_location_refs == frozenset({"a3", "a5"})
    assert pact_air.special_connections == ()
    foreign_route = air_topology.route("a3", "a6", hostile_air)
    assert foreign_route.reachable and foreign_route.eta_kind == "conditional_minimum"
    assert foreign_route.latest_turns is None
    assert any("refuelling" in value for value in foreign_route.uncertainty)
    results["subject_relative_foreign_air_access"] = True

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
            {"tile_id": 5, "x": 1, "y": 1, "visible_now": True,
             "is_ocean": True, "altitude": 2, "features": []},
            {"tile_id": 6, "x": 3, "y": 1, "visible_now": True,
             "is_ocean": True, "altitude": 2, "features": []},
        ],
        "bases": [{"id": 0, "base_ref": "base-home", "tile_id": 0,
                   "owned": True, "owner_ref": "faction-1", "name": "Home",
                   "coastal": True,
                   "minerals": {"unit_support_cost": 1}}],
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
             "tile_id": 0, "owned": True, "owner_ref": "faction-1", "triad": "sea",
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
    hostile = obj("hostile", "foreign_contact", "location-0",
                  owner_ref="faction-2", triad="land", movement_points=3,
                  movement_scale=3, roles={})
    assert transport_route(topology, {**objects, "hostile": hostile},
                           "hostile", "location-4") is None
    opposed_objects = json.loads(json.dumps(objects))
    opposed_objects["landing-defender"] = obj(
        "landing-defender", "foreign_contact", "location-4",
        owner_ref="faction-2", relationship="hostile", triad="land",
        movement_points=3, movement_scale=3, roles={"combat": True},
    )
    assert transport_route(
        topology, opposed_objects, "supported", "location-4",
    )["reachable"] is False
    opposed_objects["supported"]["fields"]["roles"]["value"]["amphibious"] = True
    assault = transport_route(
        topology, opposed_objects, "supported", "location-4",
    )
    assert assault is not None
    assert "eta_kind" in assault, assault
    assert assault["eta_kind"] == "conditional_guarded_amphibious_assault"
    assert assault["latest_turns"] is None and assault["disembark"]["opposed"] is True
    results["production_projection_support_convoy_transport"] = True

    # The planner mirrors native board/disembark actions.  Boarding requires
    # exact co-location (a normal port is shared land/sea topology), skips only
    # the passenger, and leaves the transport's real residual movement intact.
    # A newly boarded passenger cannot disembark before its next native turn.
    # Disembarkation is one charged adjacent move and any residual may be used
    # on land immediately.
    phase_topology = PerspectiveTopology(MapShape(14, 4, False), [
        KnownSquare("phase-before-port", 0, 0, "land"),
        KnownSquare("phase-port", 2, 0, "land", features=frozenset({"base"})),
        KnownSquare("phase-sea-a", 3, 1, "ocean"),
        KnownSquare("phase-sea-b", 5, 1, "ocean"),
        KnownSquare("phase-sea-c", 7, 1, "ocean"),
        KnownSquare("phase-short-land", 4, 2, "land"),
        KnownSquare("phase-land-b", 8, 2, "land"),
        KnownSquare("phase-target", 10, 2, "land"),
    ])
    newly_boarded: dict[int, dict] = {}
    for transport_remaining in (0, 1, 2):
        phase_objects = {
            "phase-base": obj("phase-base", "base", "phase-port",
                              owner_ref="faction-1", coastal=True),
            "phase-passenger": obj(
                "phase-passenger", "own_unit", "phase-port",
                owner_ref="faction-1", triad="land", movement_points=2,
                moves_remaining=2, roles={"combat": True},
            ),
            "phase-transport": obj(
                "phase-transport", "own_unit", "phase-port",
                owner_ref="faction-1", triad="sea", movement_points=2,
                moves_remaining=transport_remaining, roles={"transport": True},
                cargo={"capacity": 4, "loaded": 0},
            ),
            "phase-target": obj("phase-target", "location", "phase-target"),
        }
        schedule = transport_route(
            phase_topology, phase_objects, "phase-passenger", "phase-target",
        )
        assert schedule and schedule["reachable"]
        assert schedule["embark"]["co_located"] is True
        assert schedule["embark"]["boarding_action"] == {
            "passenger_skipped": True,
            "transport_skipped": False,
            "transport_movement_remaining_after": float(transport_remaining),
        }
        assert schedule["disembark"]["turn_offset"] >= 1
        newly_boarded[transport_remaining] = schedule
    assert newly_boarded[0]["eta_turns"] > newly_boarded[2]["eta_turns"]
    short_objects = {
        "phase-base": obj("phase-base", "base", "phase-port",
                          owner_ref="faction-1", coastal=True),
        "phase-passenger": obj(
            "phase-passenger", "own_unit", "phase-port", owner_ref="faction-1",
            triad="land", movement_points=2, moves_remaining=2,
            roles={"combat": True},
        ),
        "phase-transport": obj(
            "phase-transport", "own_unit", "phase-port", owner_ref="faction-1",
            triad="sea", movement_points=2, moves_remaining=2,
            roles={"transport": True}, cargo={"capacity": 4, "loaded": 0},
        ),
        "phase-short-land": obj(
            "phase-short-land", "location", "phase-short-land",
        ),
    }
    same_turn_crossing = transport_route(
        phase_topology, short_objects, "phase-passenger", "phase-short-land",
    )
    assert same_turn_crossing and same_turn_crossing["reachable"]
    assert same_turn_crossing["crossing"]["arrival_turn_offset"] == 0
    assert same_turn_crossing["disembark"]["turn_offset"] == 1

    # Arriving at a port with residual movement permits same-turn boarding;
    # exhausting the passenger on arrival defers boarding by one turn.
    arrival_schedules = {}
    for passenger_remaining in (1, 2):
        phase_objects = {
            "phase-base": obj("phase-base", "base", "phase-port",
                              owner_ref="faction-1", coastal=True),
            "phase-passenger": obj(
                "phase-passenger", "own_unit", "phase-before-port",
                owner_ref="faction-1", triad="land", movement_points=2,
                moves_remaining=passenger_remaining, roles={"combat": True},
            ),
            "phase-transport": obj(
                "phase-transport", "own_unit", "phase-port",
                owner_ref="faction-1", triad="sea", movement_points=2,
                moves_remaining=2, roles={"transport": True},
                cargo={"capacity": 4, "loaded": 0},
            ),
            "phase-target": obj("phase-target", "location", "phase-target"),
        }
        arrival_schedules[passenger_remaining] = transport_route(
            phase_topology, phase_objects, "phase-passenger", "phase-target",
        )
    assert arrival_schedules[2]["embark"]["board_turn_offset"] == 0
    assert arrival_schedules[1]["embark"]["board_turn_offset"] == 1

    # The transport can also arrive at the shared port, board there, and keep
    # precisely the movement left by that rendezvous move.
    arriving_transport_objects = {
        "phase-base": obj("phase-base", "base", "phase-port",
                          owner_ref="faction-1", coastal=True),
        "phase-passenger": obj(
            "phase-passenger", "own_unit", "phase-port",
            owner_ref="faction-1", triad="land", movement_points=2,
            moves_remaining=2, roles={"combat": True},
        ),
        "phase-transport": obj(
            "phase-transport", "own_unit", "phase-sea-a",
            owner_ref="faction-1", triad="sea", movement_points=2,
            moves_remaining=2, roles={"transport": True},
            cargo={"capacity": 4, "loaded": 0},
        ),
        "phase-target": obj("phase-target", "location", "phase-target"),
    }
    arriving_transport = transport_route(
        phase_topology, arriving_transport_objects,
        "phase-passenger", "phase-target",
    )
    assert arriving_transport and arriving_transport["reachable"]
    assert arriving_transport["embark"]["board_turn_offset"] == 0
    assert arriving_transport["embark"]["transport_arrival"]["arrival_state"] == {
        "turn": 1, "movement_spent": 1.0, "movement_remaining": 1.0,
    }
    assert arriving_transport["embark"]["boarding_action"][
        "transport_movement_remaining_after"
    ] == 1.0

    # Merely being adjacent across a coast is not a legal embark state.
    no_port_objects = {
        "phase-passenger": obj(
            "phase-passenger", "own_unit", "phase-before-port",
            owner_ref="faction-1", triad="land", movement_points=2,
            moves_remaining=2, roles={"combat": True},
        ),
        "phase-transport": obj(
            "phase-transport", "own_unit", "phase-sea-a",
            owner_ref="faction-1", triad="sea", movement_points=2,
            moves_remaining=2, roles={"transport": True},
            cargo={"capacity": 4, "loaded": 0},
        ),
        "phase-target": obj("phase-target", "location", "phase-target"),
    }
    without_port = PerspectiveTopology(MapShape(14, 4, False), [
        KnownSquare("phase-before-port", 0, 0, "land"),
        KnownSquare("phase-sea-a", 1, 1, "ocean"),
        KnownSquare("phase-sea-b", 3, 1, "ocean"),
        KnownSquare("phase-land-b", 4, 2, "land"),
        KnownSquare("phase-target", 6, 2, "land"),
    ])
    illegal_adjacent = transport_route(
        without_port, no_port_objects, "phase-passenger", "phase-target",
    )
    assert illegal_adjacent and illegal_adjacent["reachable"] is False
    assert illegal_adjacent["status"] == "mechanically_unreachable_in_known_world"

    boarded_schedules = {}
    for passenger_remaining in (0, 1, 2):
        boarded_objects = {
            "phase-passenger": obj(
                "phase-passenger", "own_unit", "phase-sea-a",
                owner_ref="faction-1", triad="land", movement_points=2,
                moves_remaining=passenger_remaining, roles={"combat": True},
                transport_unit_ref="phase-transport",
            ),
            "phase-transport": obj(
                "phase-transport", "own_unit", "phase-sea-a",
                owner_ref="faction-1", triad="sea", movement_points=2,
                moves_remaining=2, roles={"transport": True},
                cargo={"capacity": 4, "loaded": 1},
            ),
            "phase-target": obj("phase-target", "location", "phase-target"),
        }
        schedule = transport_route(
            phase_topology, boarded_objects, "phase-passenger", "phase-target",
        )
        assert schedule and schedule["reachable"]
        boarded_schedules[passenger_remaining] = schedule
    # With full residual the unit pays one point to disembark and one to reach
    # the target in the same native turn. Partial/zero residual need a boundary.
    assert boarded_schedules[2]["eta_turns"] == 1
    assert boarded_schedules[2]["disembark"]["movement_cost"] == 1.0
    assert boarded_schedules[2]["disembark"]["passenger_movement_after"] == 1
    assert boarded_schedules[1]["eta_turns"] == 2
    assert boarded_schedules[1]["disembark"]["passenger_movement_after"] == 0
    assert boarded_schedules[0]["eta_turns"] == 2
    assert boarded_schedules[0]["disembark"]["turn_offset"] == 1
    results["transport_phase_correct_movement"] = True

    # Exact embark schedules require a current owned or Pact coastal base
    # object, not a remembered tile feature. Treaty/neutral/enemy access is
    # never promoted to mechanical co-location permission.
    access_core = {
        "phase-passenger": obj(
            "phase-passenger", "own_unit", "phase-port",
            owner_ref="faction-1", triad="land", movement_points=2,
            moves_remaining=2, roles={"combat": True},
        ),
        "phase-transport": obj(
            "phase-transport", "own_unit", "phase-port",
            owner_ref="faction-1", triad="sea", movement_points=2,
            moves_remaining=2, roles={"transport": True},
            cargo={"capacity": 4, "loaded": 0},
        ),
        "phase-target": obj("phase-target", "location", "phase-target"),
        "faction-2": obj("faction-2", "faction", relationship="hostile"),
        "faction-3": obj("faction-3", "faction", relationship="neutral"),
        "faction-4": obj("faction-4", "faction", relationship="allied"),
    }
    own_port = obj("port-own", "base", "phase-port",
                   owner_ref="faction-1", coastal=True)
    own_schedule = transport_route(
        phase_topology, {**access_core, "port-own": own_port},
        "phase-passenger", "phase-target",
    )
    assert own_schedule and own_schedule["reachable"]
    assert own_schedule["embark"]["base_ref"] == "port-own"
    assert own_schedule["embark"]["base_owner_ref"] == "faction-1"
    assert own_schedule["embark"]["base_access"] == "current_owned_coastal_base"
    assert own_schedule["embark"]["base_dependency_hash"]
    assert own_schedule["search"]["search_turn_horizon"] is None
    assert own_schedule["search"]["search_horizon_complete"] is True
    mixed_port_schedule = transport_route(
        phase_topology,
        {**access_core, "port-own": own_port,
         "port-pact": obj("port-pact", "base", "phase-port",
                          owner_ref="faction-4", coastal=True)},
        "phase-passenger", "phase-target",
    )
    assert mixed_port_schedule and mixed_port_schedule["reachable"]
    assert mixed_port_schedule["search"]["search_complete"] is True
    assert mixed_port_schedule["embark"]["base_ref"] == "port-own"
    pact_schedule = transport_route(
        phase_topology,
        {**access_core,
         "port-pact": obj("port-pact", "base", "phase-port",
                          owner_ref="faction-4", coastal=True)},
        "phase-passenger", "phase-target",
    )
    assert pact_schedule and pact_schedule["reachable"]
    assert pact_schedule["embark"]["base_access"] == "current_pact_coastal_base"
    assert pact_schedule["embark"]["relationship_dependency_hash"]
    disallowed_ports = {
        "missing": None,
        "enemy": obj("port-enemy", "base", "phase-port",
                     owner_ref="faction-2", coastal=True),
        "treaty": obj("port-treaty", "base", "phase-port",
                      owner_ref="faction-3", coastal=True),
    }
    stale_port = json.loads(json.dumps(own_port))
    stale_port["status"] = "stale"
    for value in stale_port["fields"].values():
        value["epistemic_status"] = "stale"
    disallowed_ports["stale"] = stale_port
    destroyed_port = json.loads(json.dumps(own_port))
    destroyed_port["status"] = "destroyed"
    disallowed_ports["destroyed"] = destroyed_port
    for name, port in disallowed_ports.items():
        candidate_objects = dict(access_core)
        if port is not None:
            candidate_objects[f"port-{name}"] = port
        rejected = transport_route(
            phase_topology, candidate_objects, "phase-passenger", "phase-target",
        )
        assert rejected and rejected["reachable"] is False, (name, rejected)
    broken_pact = json.loads(json.dumps(access_core))
    broken_pact["faction-4"]["fields"]["relationship"] = field("neutral")
    broken_pact["port-pact"] = obj(
        "port-pact", "base", "phase-port", owner_ref="faction-4", coastal=True,
    )
    rejected = transport_route(
        phase_topology, broken_pact, "phase-passenger", "phase-target",
    )
    assert rejected and rejected["reachable"] is False
    results["current_owned_or_pact_embark_port_authority"] = True

    # A winding finite known graph can require more arrival turns than
    # width+height. Preparatory rendezvous search is exhaustive over that
    # graph, while only the documented candidate frontiers remain bounded.
    snake_squares = []
    sequence = []
    for row_index in range(10):
        y = row_index * 4
        xs = list(range(0, 12, 2))
        if row_index % 2:
            xs.reverse()
        sequence.extend((x, y) for x in xs)
        if row_index < 9:
            sequence.append((xs[-1], y + 2))
    for index, (x, y) in enumerate(sequence):
        snake_squares.append(KnownSquare(
            f"snake-{index}", x, y, "land",
            features=frozenset({"base"}) if (x, y) == sequence[-1] else frozenset(),
        ))
    sea_refs = []
    for x in range(1, 12, 2):
        ref = f"snake-sea-{x}"
        sea_refs.append(ref)
        snake_squares.append(KnownSquare(ref, x, 37, "ocean"))
    snake_topology = PerspectiveTopology(MapShape(12, 38, False), snake_squares)
    port_ref = f"snake-{len(sequence) - 1}"
    origin_ref = sequence and "snake-0"
    target_snake_ref = next(square.location_ref for square in snake_squares
                            if square.x == 10 and square.y == 36)
    snake_objects = {
        "snake-port": obj("snake-port", "base", port_ref,
                          owner_ref="faction-1", coastal=True),
        "snake-passenger": obj(
            "snake-passenger", "own_unit", origin_ref,
            owner_ref="faction-1", triad="land", movement_points=1,
            moves_remaining=1, roles={"combat": True},
        ),
        "snake-transport": obj(
            "snake-transport", "own_unit", port_ref,
            owner_ref="faction-1", triad="sea", movement_points=1,
            moves_remaining=1, roles={"transport": True},
            cargo={"capacity": 4, "loaded": 0},
        ),
        "snake-target": obj("snake-target", "location", target_snake_ref),
    }
    snake_schedule = transport_route(
        snake_topology, snake_objects, "snake-passenger", "snake-target",
    )
    assert snake_schedule and snake_schedule["reachable"], snake_schedule
    assert snake_schedule["embark"]["passenger_arrival"]["turns"] \
        > snake_topology.shape.width + snake_topology.shape.height
    assert snake_schedule["search"]["search_horizon_complete"] is True
    results["amphibious_exhaustive_known_graph_search"] = True

    # Candidate caps are explicit coverage, never a false proof of
    # unreachability. Block the first eight geometrically ranked landings while
    # retaining feasible candidates beyond the frontier.
    frontier_squares = [
        *(KnownSquare(f"upper-{x}", x, 0, "land",
                      features=frozenset({"base"}) if x == 0 else frozenset())
          for x in range(0, 20, 2)),
        *(KnownSquare(f"water-{x}", x, 1, "ocean") for x in range(1, 20, 2)),
        *(KnownSquare(f"lower-{x}", x, 2, "land") for x in range(0, 20, 2)),
    ]
    frontier_topology = PerspectiveTopology(MapShape(20, 3, False), frontier_squares)
    target_ref = "lower-18"
    coast_pairs = sorted({
        (land_ref, neighbor.location_ref)
        for land_ref, square in frontier_topology.by_ref.items() if not square.ocean
        for neighbor in frontier_topology.adjacent(land_ref).values() if neighbor.ocean
    })
    ranked_landings = sorted(coast_pairs, key=lambda pair: (
        frontier_topology.shape.distance(
            (frontier_topology.by_ref[pair[0]].x, frontier_topology.by_ref[pair[0]].y),
            (frontier_topology.by_ref[target_ref].x,
             frontier_topology.by_ref[target_ref].y),
        ), pair,
    ))
    assert len(ranked_landings) > 8
    frontier_objects = {
        "frontier-base": obj("frontier-base", "base", "upper-0",
                             owner_ref="faction-1", coastal=True),
        "frontier-passenger": obj(
            "frontier-passenger", "own_unit", "upper-0", owner_ref="faction-1",
            triad="land", movement_points=3, moves_remaining=3,
            roles={"combat": True, "amphibious": False},
        ),
        "frontier-transport": obj(
            "frontier-transport", "own_unit", "upper-0", owner_ref="faction-1",
            triad="sea", movement_points=3, moves_remaining=3,
            roles={"transport": True}, cargo={"capacity": 4, "loaded": 0},
        ),
        target_ref: obj(target_ref, "location", target_ref),
        "faction-hostile": obj("faction-hostile", "faction", relationship="hostile"),
    }
    for index, (landing_ref, _sea_ref) in enumerate(ranked_landings[:8]):
        frontier_objects[f"frontier-blocker-{index}"] = obj(
            f"frontier-blocker-{index}", "foreign_contact", landing_ref,
            owner_ref="faction-hostile", triad="land", roles={"combat": True},
        )
    bounded = transport_route(
        frontier_topology, frontier_objects, "frontier-passenger", target_ref,
    )
    assert bounded and bounded["reachable"] is False
    assert bounded["status"] == "no_route_found_within_bounded_candidate_search"
    assert bounded["search"]["search_complete"] is False
    assert bounded["search"]["landing_candidates_available"] > 8
    results["amphibious_candidate_coverage_explicit"] = True

    # Occupancy/ZOC constraints belong to the moving subject. Own movement is
    # exact; a foreign subject receives an explicit conditional minimum and is
    # not falsely blocked by our perspective's enemy map.
    zoc_topology = PerspectiveTopology(MapShape(8, 2, False), [
        KnownSquare("z0", 0, 0, "land", hostile_zoc=True),
        KnownSquare("z1", 2, 0, "land", hostile_zoc=True,
                    blocking_contact_occupied=True),
        KnownSquare("z2", 4, 0, "land"),
    ])
    own_route = zoc_topology.route(
        "z0", "z2", MobilityProfile("own-zoc", "land", constraint_mode="sovereign_exact"),
    )
    foreign_route = zoc_topology.route(
        "z0", "z2", MobilityProfile("foreign-zoc", "land", constraint_mode="subject_unknown"),
    )
    assert not own_route.reachable
    assert foreign_route.reachable and foreign_route.eta_kind == "conditional_minimum"
    assert any("Subject-relative" in item for item in foreign_route.uncertainty)
    relation_objects = {
        "faction-1": obj("faction-1", "faction", is_self=True),
        "faction-pact": obj("faction-pact", "faction", relationship="allied"),
        "faction-treaty": obj("faction-treaty", "faction", relationship="neutral"),
        "faction-truce": obj("faction-truce", "faction", relationship="neutral"),
        "faction-hostile": obj("faction-hostile", "faction", relationship="hostile"),
        "faction-unknown": obj("faction-unknown", "faction", relationship="unknown"),
        "own-subject": obj("own-subject", "own_unit", "z0", owner_ref="faction-1",
                           triad="land", movement_points=3, movement_scale=3, roles={}),
    }
    for suffix in ("pact", "treaty", "truce", "hostile", "unknown"):
        relation_objects[f"subject-{suffix}"] = obj(
            f"subject-{suffix}", "foreign_contact", "z0",
            owner_ref=f"faction-{suffix}", triad="land", movement_points=3,
            movement_scale=3, roles={},
        )
    own_profile = mobility_profile(
        relation_objects, "own", subject_ref="own-subject", topology=zoc_topology,
    )
    assert own_profile.constraint_mode == "sovereign_exact"
    assert not zoc_topology.route("z0", "z2", own_profile).reachable
    for suffix in ("pact", "treaty", "truce", "hostile", "unknown"):
        profile = mobility_profile(
            relation_objects, suffix, subject_ref=f"subject-{suffix}",
            topology=zoc_topology,
        )
        routed = zoc_topology.route("z0", "z2", profile)
        assert profile.constraint_mode == "subject_unknown"
        assert routed.reachable and routed.eta_kind == "conditional_minimum"
    results["subject_relative_zoc_and_occupancy"] = True

    # Foreign airdrop candidates must never inherit sovereign aggregate ZOC
    # or occupancy flags.  Prune only relations mechanically known from the
    # moving faction's frame; preserve unknown third-party cases as
    # conditional possibilities. Combat may attack a known-war base or stack
    # but cannot silently break a treaty; unknown relations remain possible,
    # never exact.
    drop_refs = (
        "fd-origin", "fd-sovereign-zoc", "fd-same-unit", "fd-hostile-unit", "fd-pact-unit",
        "fd-neutral-unit", "fd-unknown-unit", "fd-own-base", "fd-pact-base",
        "fd-hostile-base", "fd-neutral-base", "fd-unknown-base",
    )
    foreign_drop_topology = PerspectiveTopology(MapShape(24, 2, False), [
        KnownSquare(
            ref, index * 2, 0, "land",
            hostile_zoc=ref == "fd-sovereign-zoc",
            blocking_contact_occupied=ref in {
                "fd-sovereign-zoc", "fd-unknown-unit",
            },
            features=frozenset({"base"}) if ref.endswith("-base") else frozenset(),
        )
        for index, ref in enumerate(drop_refs)
    ])
    foreign_drop_objects = {
        "faction-2": obj("faction-2", "faction", relationship="hostile"),
        "foreign-dropper": obj(
            "foreign-dropper", "foreign_contact", "fd-origin",
            owner_ref="faction-2", triad="land", movement_points=1,
            moves_remaining=1, roles={"combat": False}, airdrop_ready=True,
            airdrop_range=20,
            relationships_by_faction={
                "faction-1": "war", "faction-3": "pact", "faction-4": "treaty",
            },
        ),
        "same-unit": obj("same-unit", "foreign_contact", "fd-same-unit",
                         owner_ref="faction-2", triad="land", roles={}),
        "hostile-unit": obj("hostile-unit", "own_unit", "fd-hostile-unit",
                            owner_ref="faction-1", triad="land", roles={}),
        "pact-unit": obj("pact-unit", "foreign_contact", "fd-pact-unit",
                         owner_ref="faction-3", triad="land", roles={}),
        "neutral-unit": obj("neutral-unit", "foreign_contact", "fd-neutral-unit",
                            owner_ref="faction-4", triad="land", roles={}),
        "unknown-unit": obj("unknown-unit", "foreign_contact", "fd-unknown-unit",
                            owner_ref="faction-5", triad="land", roles={}),
    }
    for suffix, owner in (
        ("own", "faction-2"), ("pact", "faction-3"),
        ("hostile", "faction-1"), ("neutral", "faction-4"),
        ("unknown", "faction-5"),
    ):
        foreign_drop_objects[f"{suffix}-base"] = obj(
            f"{suffix}-base", "base", f"fd-{suffix}-base", owner_ref=owner,
        )
    noncombat_drop = mobility_profile(
        foreign_drop_objects, "foreign-drop", subject_ref="foreign-dropper",
        topology=foreign_drop_topology,
    )
    assert "fd-sovereign-zoc" in noncombat_drop.airdrop_destination_refs
    assert "fd-same-unit" in noncombat_drop.airdrop_destination_refs
    assert "fd-pact-unit" in noncombat_drop.airdrop_destination_refs
    assert "fd-unknown-unit" in noncombat_drop.airdrop_destination_refs
    assert "fd-own-base" in noncombat_drop.airdrop_destination_refs
    assert "fd-pact-base" in noncombat_drop.airdrop_destination_refs
    assert "fd-unknown-base" in noncombat_drop.airdrop_destination_refs
    assert "fd-hostile-unit" not in noncombat_drop.airdrop_destination_refs
    assert "fd-neutral-unit" not in noncombat_drop.airdrop_destination_refs
    assert "fd-hostile-base" not in noncombat_drop.airdrop_destination_refs
    assert "fd-neutral-base" not in noncombat_drop.airdrop_destination_refs
    combat_objects = json.loads(json.dumps(foreign_drop_objects))
    combat_objects["foreign-dropper"]["fields"]["roles"]["value"]["combat"] = True
    combat_drop = mobility_profile(
        combat_objects, "foreign-combat-drop", subject_ref="foreign-dropper",
        topology=foreign_drop_topology,
    )
    assert "fd-hostile-base" in combat_drop.airdrop_destination_refs
    assert "fd-neutral-base" not in combat_drop.airdrop_destination_refs
    assert "fd-hostile-unit" not in combat_drop.airdrop_destination_refs
    assert "fd-pact-unit" in combat_drop.airdrop_destination_refs
    assert "fd-neutral-unit" not in combat_drop.airdrop_destination_refs
    assert "fd-unknown-unit" in combat_drop.airdrop_destination_refs
    assert not combat_drop.airdrop_targets_native_guarded
    assert not combat_drop.airdrop_targets_complete
    clean_foreign_drop_topology = PerspectiveTopology(MapShape(24, 2, False), [
        KnownSquare(
            ref, index * 2, 0, "land",
            features=frozenset({"base"}) if ref.endswith("-base") else frozenset(),
        )
        for index, ref in enumerate(drop_refs)
    ])
    clean_noncombat_drop = mobility_profile(
        foreign_drop_objects, "foreign-drop-clean", subject_ref="foreign-dropper",
        topology=clean_foreign_drop_topology,
    )
    assert clean_noncombat_drop.airdrop_destination_refs \
        == noncombat_drop.airdrop_destination_refs
    results["subject_relative_foreign_airdrop_candidates"] = True

    # Owned exact destinations come only from the current native legal-target
    # receipt. This binds anti-drop coverage without projecting hidden enemy
    # facilities; a locally inferred target remains explicitly conditional.
    receipt_topology = PerspectiveTopology(MapShape(8, 2, False), [
        KnownSquare("location-0", 0, 0, "land", features=frozenset({"base"})),
        KnownSquare("location-1", 2, 0, "land"),
        KnownSquare("location-2", 4, 0, "land", blocking_contact_occupied=True),
        KnownSquare("location-3", 6, 0, "land"),
    ])
    receipt_dropper = obj(
        "receipt-dropper", "own_unit", "location-0", owner_ref="faction-1",
        triad="land", movement_points=1, moves_remaining=1,
        roles={"combat": True}, airdrop_ready=True, airdrop_range=4,
        airdrop_target_tile_ids=[2], airdrop_target_count=1,
        airdrop_targets_truncated=False,
    )
    receipt_profile = mobility_profile(
        {"receipt-dropper": receipt_dropper}, "receipt",
        subject_ref="receipt-dropper", topology=receipt_topology,
    )
    assert receipt_profile.airdrop_destination_refs == frozenset({"location-2"})
    assert receipt_profile.airdrop_targets_native_guarded
    assert receipt_profile.airdrop_targets_complete
    exact_drop = receipt_topology.route("location-0", "location-2", receipt_profile)
    assert exact_drop.reachable and exact_drop.eta_kind == "exact_known_state"
    assert not receipt_topology.route(
        "location-0", "location-3", receipt_profile,
    ).reachable
    fallback_dropper = obj(
        "fallback-dropper", "own_unit", "location-0", owner_ref="faction-1",
        triad="land", movement_points=1, moves_remaining=1,
        roles={"combat": True}, airdrop_ready=True, airdrop_range=4,
    )
    fallback_profile = mobility_profile(
        {"fallback-dropper": fallback_dropper}, "fallback",
        subject_ref="fallback-dropper", topology=receipt_topology,
    )
    conditional_drop = receipt_topology.route(
        "location-0", "location-3", fallback_profile,
    )
    assert conditional_drop.reachable
    assert conditional_drop.eta_kind == "conditional_known_state"
    assert conditional_drop.latest_turns is None
    results["owned_airdrop_native_target_receipt"] = True

    # Lost-contact envelopes include the residual disappearance phase plus a
    # fresh phase for every crossed turn boundary. Exercise 0/partial/full
    # residual movement over the movement cost families that can otherwise
    # expose an under-approximation.
    movement_worlds = {
        "road": PerspectiveTopology(MapShape(10, 2, False), [
            KnownSquare(f"road-{i}", i * 2, 0, "land", features=frozenset({"road"}))
            for i in range(5)
        ]),
        "tube": PerspectiveTopology(MapShape(10, 2, False), [
            KnownSquare(f"tube-{i}", i * 2, 0, "land",
                        features=frozenset({"road", "magtube"})) for i in range(5)
        ]),
        "rough": PerspectiveTopology(MapShape(10, 2, False), [
            KnownSquare(f"rough-{i}", i * 2, 0, "rocky") for i in range(5)
        ]),
        "fungus": PerspectiveTopology(MapShape(10, 2, False), [
            KnownSquare(f"fungus-{i}", i * 2, 0, "land",
                        features=frozenset({"fungus"})) for i in range(5)
        ]),
        "sea": PerspectiveTopology(MapShape(10, 2, False), [
            KnownSquare(f"sea-{i}", i * 2, 0, "ocean") for i in range(5)
        ]),
        "air": PerspectiveTopology(MapShape(10, 2, False), [
            KnownSquare(f"air-{i}", i * 2, 0, "land") for i in range(5)
        ]),
    }
    for family, envelope_topology in movement_worlds.items():
        triad = "sea" if family == "sea" else "air" if family == "air" else "land"
        for remaining in (0, 1, 3):
            for elapsed in (0, 1, 3):
                ref = f"lost-{family}-{remaining}-{elapsed}"
                start = f"{family}-0"
                contact = obj(
                    ref, "foreign_contact", start, owner_ref="faction-hostile",
                    triad=triad, movement_points=3, moves_remaining=remaining,
                    last_seen_turn=20,
                )
                contact["status"] = "lost"
                objects_for_envelope = {ref: contact}
                profile = mobility_profile(
                    objects_for_envelope, "envelope", subject_ref=ref,
                    topology=envelope_topology,
                )
                mechanically_possible = set(envelope_topology.arrival_map(
                    start, profile, max_turns=elapsed + 1,
                ))
                row = lost_contact_envelopes(
                    envelope_topology, objects_for_envelope,
                    current_turn=20 + elapsed,
                )[0]
                exposed = set(row["known_world_possible_location_refs"])
                assert mechanically_possible.issubset(exposed)
                assert row["unseen_movement_phases"] == elapsed + 1
    zero_next = obj(
        "zero-next", "foreign_contact", "road-0", owner_ref="faction-hostile",
        triad="land", movement_points=3, moves_remaining=0, last_seen_turn=20,
    )
    zero_next["status"] = "lost"
    assert "road-1" in lost_contact_envelopes(
        movement_worlds["road"], {"zero-next": zero_next}, current_turn=21,
    )[0]["known_world_possible_location_refs"]
    results["lost_contact_phase_superset"] = True

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
