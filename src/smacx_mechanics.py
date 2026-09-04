"""Deterministic fair-play strategic mechanics over a perspective projection.

This module computes geometry, timing, and constraints. It deliberately never
ranks strategic value or recommends an action.
"""

from __future__ import annotations

from collections import Counter
from math import ceil
from typing import Any, Iterable, Mapping

from smacx_topology import MobilityProfile, PerspectiveTopology
from smacx_world_types import content_hash


def field_value(item: Mapping[str, Any], name: str, default: Any = None) -> Any:
    field = item.get("fields", {}).get(name) if isinstance(item.get("fields"), Mapping) else None
    return field.get("value", default) if isinstance(field, Mapping) else default


def base_support_cost(item: Mapping[str, Any]) -> int | float | None:
    minerals = field_value(item, "minerals", {})
    if isinstance(minerals, Mapping) and isinstance(
            minerals.get("unit_support_cost"), (int, float)):
        return minerals["unit_support_cost"]
    # Compatibility with imported pre-rebuild fixtures only.
    value = field_value(item, "unit_support_cost")
    return value if isinstance(value, (int, float)) else None


def object_location(item: Mapping[str, Any] | None, fallback: str = "") -> str:
    if not item:
        return fallback
    return str(item.get("location_ref") or item.get("object_ref") or fallback)


def relationship_class(item: Mapping[str, Any]) -> str:
    value = str(field_value(item, "relationship", "unknown"))
    return value if value in {"hostile", "allied", "neutral", "unknown"} else "unknown"


def mobility_profile(objects: Mapping[str, Mapping[str, Any]], profile_ref: str,
                     *, subject_ref: str = "",
                     topology: PerspectiveTopology | None = None) -> MobilityProfile:
    """Resolve a bounded profile from owned, perspective-known unit facts."""
    subject = objects.get(subject_ref, {})
    triad = str(field_value(subject, "triad", ""))
    subject_is_foreign = subject.get("kind") == "foreign_contact"
    mobility_known = not subject_is_foreign or bool(triad)
    if not triad:
        triad = "sea" if "sea" in profile_ref else "air" if "air" in profile_ref else "land"
    moves = field_value(subject, "movement_points", field_value(subject, "speed", 1))
    remaining = field_value(subject, "moves_remaining", None)
    scale = field_value(subject, "movement_scale", 1)
    try:
        scale = max(1.0, float(scale))
        moves = max(1.0 / scale, float(moves) / scale)
        remaining = None if remaining is None else max(0.0, float(remaining) / scale)
    except (TypeError, ValueError):
        moves, remaining, scale = 1.0, None, 1.0
    roles = field_value(subject, "roles", {})
    abilities = field_value(subject, "abilities", [])
    if not isinstance(abilities, list):
        abilities = []
    if isinstance(roles, Mapping) and roles.get("boarded"):
        triad = "sea"
    subject_owner = field_value(subject, "owner_ref")

    def friendly_owner(owner_ref: Any) -> bool:
        return owner_ref in {None, "", subject_owner, "faction-0"} \
            or relationship_class(objects.get(str(owner_ref), {})) == "allied"

    stationary_refuel = {
        object_location(item) for item in objects.values()
        if item.get("kind") == "base" and friendly_owner(field_value(item, "owner_ref"))
    }
    stationary_refuel.update(
        str(item["object_ref"]) for item in objects.values()
        if item.get("kind") == "location"
        and "airbase" in set(map(str, field_value(item, "features", []) or []))
        and friendly_owner(field_value(item, "owner_ref"))
    )
    mobile_refuel: set[str] = set()
    for item in objects.values():
        item_roles = field_value(item, "roles", {})
        cargo = field_value(item, "cargo", {})
        if item.get("kind") == "own_unit" and isinstance(item_roles, Mapping) \
                and item_roles.get("carrier") \
                and isinstance(cargo, Mapping) \
                and int(cargo.get("loaded", 0)) < int(cargo.get("capacity", 0)):
            mobile_refuel.add(object_location(item))
    own_bases = [item for item in objects.values() if item.get("kind") == "base"
                 and field_value(item, "owner_ref") == field_value(subject, "owner_ref")]
    def has_psi_gate(item: Mapping[str, Any]) -> bool:
        facilities = field_value(item, "facilities", []) or []
        return any(
            isinstance(value, Mapping) and (
                int(value.get("facility_id", -1)) == 33
                or "psi gate" in str(value.get("name") or "").lower()
            ) or not isinstance(value, Mapping)
            and str(value).lower().replace("_", " ") == "psi gate"
            for value in facilities
        )
    def compatible_gate_destination(item: Mapping[str, Any]) -> bool:
        if triad == "air":
            return True
        if triad == "land":
            return not bool(field_value(item, "is_ocean", False))
        return bool(field_value(item, "coastal", False))

    psi_gates = [object_location(item) for item in own_bases
                 if has_psi_gate(item) and compatible_gate_destination(item)]
    ready_psi_gates = [object_location(item) for item in own_bases
                       if has_psi_gate(item)
                       and bool(field_value(item, "psi_gate_ready", False))]
    special_connections = tuple(
        (origin, target, 1.0, "psi_gate")
        for origin in ready_psi_gates for target in psi_gates if origin != target
    )
    airdrop_destinations: frozenset[str] = frozenset()
    airdrop_ready = bool(field_value(subject, "airdrop_ready", False))
    airdrop_range = int(field_value(subject, "airdrop_range", 0) or 0)
    if airdrop_ready and topology is not None and object_location(subject) in topology.by_ref:
        origin_square = topology.by_ref[object_location(subject)]
        combat = bool(isinstance(roles, Mapping) and roles.get("combat"))
        candidates = set()
        for location_ref, square in topology.by_ref.items():
            if not square.current or square.ocean:
                continue
            if topology.shape.distance(
                (origin_square.x, origin_square.y), (square.x, square.y),
            ) > airdrop_range:
                continue
            occupying_base = next((item for item in objects.values()
                if item.get("kind") == "base" and object_location(item) == location_ref), None)
            if square.blocking_contact_occupied:
                continue
            base_owner = field_value(occupying_base, "owner_ref") \
                if occupying_base is not None else None
            base_owner_object = objects.get(str(base_owner), {})
            base_is_non_pact = base_owner not in {None, "", field_value(subject, "owner_ref")} \
                and relationship_class(base_owner_object) != "allied"
            if not combat and (square.hostile_zoc or (
                occupying_base is not None
                and base_is_non_pact
            )):
                continue
            candidates.add(location_ref)
        airdrop_destinations = frozenset(candidates)
    return MobilityProfile(
        profile_ref=profile_ref, triad=triad, movement_points=moves,
        movement_remaining=remaining,
        fungus_cost=float(field_value(subject, "fungus_movement_cost", 3 * scale)) / scale,
        fungus_connects_to_road=bool(field_value(subject, "fungus_connects_to_road", False)),
        ignores_rough_movement=bool(field_value(subject, "ignores_rough_movement", False)),
        road_cost=float(field_value(subject, "road_movement_cost", 1)) / scale,
        magtube_cost=float(field_value(subject, "magtube_movement_cost", 0)) / scale,
        ignores_zoc=triad in {"sea", "air"} or bool(
            isinstance(roles, Mapping) and (roles.get("probe") or roles.get("cloaked"))),
        # Embarkation is represented by explicit transport dependency results;
        # a land profile never gains an ocean graph merely because some
        # transport exists elsewhere in the perspective.
        can_embark=triad == "land" and any(
            item.get("kind") == "own_unit"
            and isinstance(field_value(item, "roles", {}), Mapping)
            and field_value(item, "roles", {}).get("transport")
            and int((field_value(item, "cargo", {}) or {}).get("loaded", 0))
            < int((field_value(item, "cargo", {}) or {}).get("capacity", 0))
            for item in objects.values()
        ),
        can_airdrop=airdrop_ready,
        airdrop_origin_ref=object_location(subject) if airdrop_ready else None,
        air_safe_range=(int(field_value(subject, "air_safe_range", -1))
                        if int(field_value(subject, "air_safe_range", -1)) >= 0 else None)
        if triad == "air" else None,
        air_full_safe_range=(int(field_value(subject, "air_full_safe_range", -1))
                             if int(field_value(subject, "air_full_safe_range", -1)) >= 0 else None)
        if triad == "air" else None,
        air_origin_refuels=bool(field_value(subject, "air_origin_refuels", False)),
        refuel_location_refs=frozenset(stationary_refuel | mobile_refuel),
        mobile_refuel_location_refs=frozenset(mobile_refuel),
        abilities=frozenset(map(str, abilities)),
        special_connections=special_connections,
        airdrop_destination_refs=airdrop_destinations,
        known=mobility_known,
        constraint_mode=("subject_unknown" if subject_is_foreign
                         else "sovereign_exact"),
    )


def response_matrix(topology: PerspectiveTopology,
                    objects: Mapping[str, Mapping[str, Any]],
                    origins: Iterable[str], targets: Iterable[str],
                    profile_ref: str) -> list[dict[str, Any]]:
    rows = []
    for origin_ref in dict.fromkeys(map(str, origins)):
        origin = object_location(objects.get(origin_ref), origin_ref)
        profile = mobility_profile(objects, profile_ref, subject_ref=origin_ref,
                                   topology=topology)
        cells = []
        for target_ref in dict.fromkeys(map(str, targets)):
            target = object_location(objects.get(target_ref), target_ref)
            route = topology.route(origin, target, profile)
            cell = {
                "target_ref": target_ref, "reachable": route.reachable,
                "eta_turns": route.turns, "movement_cost": route.movement_cost,
                "uncertainty": list(route.uncertainty),
            }
            if not route.reachable and profile.triad == "land":
                transported = transport_route(
                    topology, objects, origin_ref, target_ref,
                )
                if transported is not None:
                    cell["transport_route"] = transported
                    cell["reachable_with_transport"] = True
                    cell["eta_turns_with_transport"] = transported["eta_turns"]
            cells.append(cell)
        rows.append({"origin_ref": origin_ref, "responses": cells})
    return rows


def rendezvous_matrix(topology: PerspectiveTopology,
                      objects: Mapping[str, Mapping[str, Any]],
                      participant_refs: Iterable[str],
                      candidate_refs: Iterable[str],
                      profile_ref: str) -> list[dict[str, Any]]:
    """Mechanical arrival windows for several participants at candidate sites."""
    rows: list[dict[str, Any]] = []
    participants = tuple(dict.fromkeys(map(str, participant_refs)))[:16]
    for candidate_ref in tuple(dict.fromkeys(map(str, candidate_refs)))[:32]:
        target = object_location(objects.get(candidate_ref), candidate_ref)
        arrivals = []
        for participant_ref in participants:
            origin = object_location(objects.get(participant_ref), participant_ref)
            route = topology.route(
                origin, target,
                mobility_profile(objects, profile_ref, subject_ref=participant_ref,
                                 topology=topology),
            )
            arrivals.append({
                "participant_ref": participant_ref, "reachable": route.reachable,
                "eta_turns": route.turns, "movement_cost": route.movement_cost,
                "uncertainty": list(route.uncertainty),
            })
        known = [item["eta_turns"] for item in arrivals
                 if item["reachable"] and item["eta_turns"] is not None]
        rows.append({
            "candidate_ref": candidate_ref, "arrivals": arrivals,
            "all_reachable": len(known) == len(arrivals),
            "earliest_common_arrival_turns": max(known) if len(known) == len(arrivals) else None,
            "strategy_boundary": "arrival mechanics only; no rendezvous recommendation",
        })
    return rows


def base_mechanics(topology: PerspectiveTopology,
                   objects: Mapping[str, Mapping[str, Any]],
                   base_refs: Iterable[str] = ()) -> list[dict[str, Any]]:
    selected = set(map(str, base_refs))
    bases = [item for item in objects.values() if item.get("kind") == "base"
             and (not selected or item.get("object_ref") in selected)]
    own_units = [item for item in objects.values() if item.get("kind") == "own_unit"]
    contacts = [item for item in objects.values()
                if item.get("kind") == "foreign_contact" and item.get("status") == "active"
                and relationship_class(item) == "hostile"]
    rows = []
    for base in sorted(bases, key=lambda item: str(item.get("object_ref"))):
        ref = str(base["object_ref"])
        location = object_location(base)
        garrison = [str(item["object_ref"]) for item in own_units
                    if object_location(item) == location]
        reinforcements = []
        threats = []
        for unit in own_units:
            roles = field_value(unit, "roles", {})
            if isinstance(roles, Mapping) and not roles.get("combat", False):
                continue
            try:
                route = topology.route(object_location(unit), location,
                                       mobility_profile(objects, "owned-response",
                                                        subject_ref=str(unit["object_ref"]),
                                                        topology=topology))
            except Exception:
                continue
            reinforcements.append({"unit_ref": unit["object_ref"], "eta_turns": route.turns,
                                   "reachable": route.reachable,
                                   "uncertainty": list(route.uncertainty)})
        for contact in contacts:
            try:
                route = topology.route(object_location(contact), location,
                                       mobility_profile(objects, "observed-hostile-response",
                                                        subject_ref=str(contact["object_ref"]),
                                                        topology=topology))
            except Exception:
                continue
            threats.append({"contact_ref": contact["object_ref"],
                            "minimum_observed_eta_turns": route.turns,
                            "reachable_on_known_world": route.reachable,
                            "uncertainty": list(route.uncertainty)})
        progress = field_value(base, "minerals_accumulated", 0)
        cost = field_value(base, "production_cost", None)
        surplus = field_value(base, "mineral_surplus", 0)
        completion = None
        if isinstance(cost, (int, float)) and isinstance(progress, (int, float)) \
                and isinstance(surplus, (int, float)) and surplus > 0:
            completion = max(0, ceil((cost - progress) / surplus))
        supported_refs = [str(unit["object_ref"]) for unit in own_units
                          if field_value(unit, "home_base_ref") == ref
                          and field_value(unit, "requires_support", False) is True]
        rows.append({
            "base_ref": ref, "location_ref": location,
            "garrison_refs": garrison, "observed_defender_count": len(garrison),
            "production": {"name": field_value(base, "production_name"),
                           "turns_remaining": completion},
            "friendly_response": sorted(reinforcements,
                                         key=lambda row: (row["eta_turns"] is None,
                                                          row["eta_turns"] or 10**9))[:12],
            "visible_hostile_response": sorted(threats,
                                               key=lambda row: (row["minimum_observed_eta_turns"] is None,
                                                                row["minimum_observed_eta_turns"] or 10**9))[:12],
            "support_burden": len(supported_refs),
            "supported_unit_refs": supported_refs[:32],
            "support_mineral_cost": base_support_cost(base),
        })
    return rows


def transport_route(
    topology: PerspectiveTopology,
    objects: Mapping[str, Mapping[str, Any]],
    passenger_ref: str,
    target_ref: str,
) -> dict[str, Any] | None:
    """Return the best known, capacity-valid land/sea/land schedule.

    This is an exact schedule for SMACX Agent's serialized guarded phase
    contract, not an implicit multi-unit command. Boarding, transport movement,
    and disembarkation remain separate actions. A rendezvous/boarding boundary
    and a disembark boundary end the participating unit's phase; fresh native
    choices can still invalidate the schedule before execution.
    """
    passenger = objects.get(passenger_ref)
    target_location = object_location(objects.get(target_ref), target_ref)
    # The sovereign can coordinate only its own passenger with its own
    # transports.  Foreign threat estimates must never borrow our fleet.
    if not passenger or passenger.get("kind") != "own_unit" \
            or target_location not in topology.by_ref \
            or str(field_value(passenger, "triad", "")) != "land":
        return None
    passenger_location = object_location(passenger)
    if passenger_location not in topology.by_ref or topology.by_ref[target_location].ocean:
        return None
    roles = field_value(passenger, "roles", {})
    amphibious = bool(isinstance(roles, Mapping) and roles.get("amphibious"))
    boarded_transport_ref = field_value(passenger, "transport_unit_ref")
    land_profile = mobility_profile(
        objects, "transport-passenger-land", subject_ref=passenger_ref,
        topology=topology,
    )
    transports = []
    for item in objects.values():
        item_roles = field_value(item, "roles", {})
        if item.get("kind") != "own_unit" or not isinstance(item_roles, Mapping) \
                or not item_roles.get("transport") \
                or str(field_value(item, "triad", "")) != "sea":
            continue
        cargo = field_value(item, "cargo", {})
        if not isinstance(cargo, Mapping):
            continue
        capacity = int(cargo.get("capacity", 0) or 0)
        loaded = int(cargo.get("loaded", 0) or 0)
        is_boarded_here = str(item.get("object_ref")) == str(boarded_transport_ref)
        if capacity <= 0 or (not is_boarded_here and loaded >= capacity):
            continue
        if object_location(item) not in topology.by_ref:
            continue
        transports.append((item, is_boarded_here, capacity, loaded))
    # Known coast pairs are computed once.  Candidate frontiers are capped so
    # this query scales as O(V) with small constants rather than repeated
    # all-land x all-land routing.
    coast_pairs = sorted({
        (land_ref, neighbor.location_ref)
        for land_ref, land_square in topology.by_ref.items() if not land_square.ocean
        for neighbor in topology.adjacent(land_ref).values()
        if neighbor.ocean or "base" in neighbor.features
    })
    search_turns = max(1, topology.shape.width + topology.shape.height)
    passenger_arrivals = topology.arrival_map(
        passenger_location, land_profile, max_turns=search_turns,
    )
    landing_candidates = sorted(
        coast_pairs,
        key=lambda pair: (
            topology.shape.distance(
                (topology.by_ref[pair[0]].x, topology.by_ref[pair[0]].y),
                (topology.by_ref[target_location].x, topology.by_ref[target_location].y),
            ), pair,
        ),
    )[:8]
    post_legs = {
        land_ref: topology.route(land_ref, target_location, land_profile)
        for land_ref, _sea_ref in landing_candidates
    }
    hostile_at = {
        object_location(item)
        for item in objects.values()
        if item.get("kind") == "foreign_contact" and item.get("status", "active") == "active"
        and relationship_class(objects.get(str(field_value(item, "owner_ref")), {})) != "allied"
    }
    best: tuple[tuple[int, float, str], dict[str, Any]] | None = None
    for transport, already_boarded, capacity, loaded in transports:
        transport_ref = str(transport["object_ref"])
        transport_location = object_location(transport)
        sea_profile = mobility_profile(
            objects, "transport-sea", subject_ref=transport_ref,
            topology=topology,
        )
        transport_arrivals = topology.arrival_map(
            transport_location, sea_profile, max_turns=search_turns,
        )
        embark_pairs: list[tuple[str, str, Any, Any]] = []
        if already_boarded:
            embark_pairs.append((passenger_location, transport_location, None, None))
        else:
            ranked = []
            for land_ref, sea_ref in coast_pairs:
                passenger_arrival = passenger_arrivals.get(land_ref)
                transport_arrival = transport_arrivals.get(sea_ref)
                if passenger_arrival is None or transport_arrival is None:
                    continue
                ranked.append((
                    max(int(passenger_arrival["turns"]), int(transport_arrival["turns"])),
                    float(passenger_arrival["movement_cost"])
                    + float(transport_arrival["movement_cost"]),
                    land_ref, sea_ref,
                ))
            for _turns, _cost, land_ref, sea_ref in sorted(ranked)[:4]:
                embark_pairs.append((
                    land_ref, sea_ref,
                    topology.route(passenger_location, land_ref, land_profile),
                    topology.route(transport_location, sea_ref, sea_profile),
                ))
        for embark_land, embark_sea, passenger_leg, transport_leg in embark_pairs:
            crossing_arrivals = topology.arrival_map(
                embark_sea, sea_profile, max_turns=search_turns,
            )
            for landing_ref, landing_sea_ref in landing_candidates:
                post_leg = post_legs[landing_ref]
                if not post_leg.reachable:
                    continue
                if landing_sea_ref not in crossing_arrivals:
                    continue
                opposed = landing_ref in hostile_at
                if opposed and not amphibious:
                    continue
                sea_leg = topology.route(embark_sea, landing_sea_ref, sea_profile)
                if not sea_leg.reachable:
                    continue
                try:
                    rendezvous_turn = 0 if already_boarded else max(
                        int(passenger_leg.turns or 0), int(transport_leg.turns or 0),
                    )
                    board_boundary = 0 if already_boarded else 1
                    eta = rendezvous_turn + board_boundary + int(sea_leg.turns or 0) \
                        + 1 + int(post_leg.turns or 0)
                    cost_legs = [sea_leg, post_leg]
                    if not already_boarded:
                        cost_legs.extend((passenger_leg, transport_leg))
                    raw_cost = sum(float(value.movement_cost or 0)
                                   for value in cost_legs if value is not None)
                    conditional = opposed or any(
                        value.eta_kind != "exact_known_state"
                        for value in (sea_leg, post_leg)
                    )
                    if not already_boarded:
                        conditional = conditional or passenger_leg.eta_kind != "exact_known_state" \
                            or transport_leg.eta_kind != "exact_known_state"
                    schedule = {
                        "transport_ref": transport_ref,
                        "passenger_ref": passenger_ref,
                        "target_ref": target_ref,
                        "eta_turns": eta,
                        "eta_kind": "conditional_guarded_amphibious_assault"
                        if opposed else (
                            "conditional_serialized_guarded_schedule" if conditional
                            else "exact_serialized_guarded_schedule"
                        ),
                        "latest_turns": None if conditional else eta,
                        "embark": {
                            "land_location_ref": embark_land,
                            "sea_location_ref": embark_sea,
                            "already_boarded": already_boarded,
                            "passenger_arrival_turns": 0 if already_boarded
                            else passenger_leg.turns,
                            "transport_arrival_turns": 0 if already_boarded
                            else transport_leg.turns,
                        },
                        "crossing": {
                            "path": list(sea_leg.path), "movement_cost": sea_leg.movement_cost,
                            "eta_turns": sea_leg.turns,
                        },
                        "disembark": {
                            "sea_location_ref": landing_sea_ref,
                            "land_location_ref": landing_ref,
                            "post_disembark_path": list(post_leg.path),
                            "amphibious": amphibious, "opposed": opposed,
                        },
                        "capacity": {"total": capacity, "loaded": loaded,
                                     "available": capacity - loaded},
                        "requirements": [
                            "Execute each phase through fresh guarded unit choices.",
                            "A non-amphibious passenger cannot make an opposed amphibious attack.",
                            "Re-query if coast, transport readiness, cargo, diplomacy, or ZOC changes.",
                        ],
                        "dependency_hash": content_hash({
                            "passenger": passenger_ref, "transport": transport_ref,
                            "target": target_ref, "embark": (embark_land, embark_sea),
                            "landing": (landing_sea_ref, landing_ref),
                            "passenger_leg": passenger_leg.as_dict() if passenger_leg else None,
                            "transport_leg": transport_leg.as_dict() if transport_leg else None,
                            "sea_leg": sea_leg.as_dict(), "post_leg": post_leg.as_dict(),
                            "cargo": (capacity, loaded), "amphibious": amphibious,
                        }),
                    }
                    score = (eta, raw_cost, transport_ref)
                    if best is None or score < best[0]:
                        best = (score, schedule)
                except (AttributeError, TypeError, ValueError):
                    continue
    return best[1] if best else None


def logistics(objects: Mapping[str, Mapping[str, Any]],
              topology: PerspectiveTopology | None = None,
              subject_refs: Iterable[str] = ()) -> dict[str, Any]:
    own = [item for item in objects.values() if item.get("kind") == "own_unit"]
    home_counts = Counter(str(field_value(item, "home_base_ref")) for item in own
                          if field_value(item, "home_base_ref")
                          and field_value(item, "requires_support", False) is True)
    transports, aircraft, convoys = [], [], []
    for item in own:
        roles = field_value(item, "roles", {})
        if not isinstance(roles, Mapping):
            roles = {}
        compact = {"unit_ref": item["object_ref"], "location_ref": item.get("location_ref")}
        cargo = field_value(item, "cargo", {})
        if roles.get("transport") or roles.get("carrier"):
            transports.append({**compact, "cargo": cargo, "carrier": bool(roles.get("carrier"))})
        if str(field_value(item, "triad", "")) == "air":
            aircraft.append({**compact, "boarded": bool(roles.get("boarded")),
                             "transport_unit_ref": field_value(item, "transport_unit_ref")})
        if roles.get("supply"):
            convoys.append({
                **compact, "order": field_value(item, "order_name"),
                "resource": field_value(item, "convoy_resource"),
                "source_location_ref": field_value(item, "convoy_source_location_ref"),
                "destination_base_ref": field_value(item, "convoy_destination_base_ref"),
                "base_effect": field_value(item, "convoy_base_effect"),
            })
    support_details = {
        str(item["object_ref"]): {
            "supported_unit_count": int(home_counts.get(str(item["object_ref"]), 0)),
            "mineral_cost": base_support_cost(item),
        }
        for item in objects.values() if item.get("kind") == "base"
        and str(item["object_ref"]) in home_counts
    }
    result = {"support_by_home_base": dict(home_counts),
              "support_details_by_home_base": support_details,
              "transports": transports,
              "aircraft": aircraft, "convoys": convoys}
    if topology is not None:
        requested = [ref for ref in dict.fromkeys(map(str, subject_refs))
                     if objects.get(ref, {}).get("kind") == "own_unit"]
        targets = [ref for ref in dict.fromkeys(map(str, subject_refs))
                   if objects.get(ref, {}).get("kind") in {"base", "location"}]
        options = []
        for passenger_ref in requested[:8]:
            for target_ref in targets[:8]:
                option = transport_route(topology, objects, passenger_ref, target_ref)
                if option is not None:
                    options.append(option)
        result["transport_route_options"] = options
        result["transport_query_hint"] = (
            "Supply one land unit and one destination in subject_refs, or use compare, "
            "to calculate a bounded guarded transport schedule."
        )
    return result


def lost_contact_envelopes(topology: PerspectiveTopology,
                           objects: Mapping[str, Mapping[str, Any]],
                           *, current_turn: int | None,
                           subject_refs: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Bound possible locations using only last-seen, known terrain, and observed mobility.

    This never preserves identity across fog and never asserts that a unit still
    exists. The result is an explicit possibility envelope, not hidden tracking.
    """
    rows: list[dict[str, Any]] = []
    selected = set(map(str, subject_refs))
    for item in objects.values():
        if item.get("kind") != "foreign_contact" or item.get("status") != "lost":
            continue
        if selected and str(item.get("object_ref")) not in selected:
            continue
        last_seen = field_value(item, "last_seen_turn")
        elapsed = None
        if isinstance(current_turn, int) and isinstance(last_seen, int):
            elapsed = max(0, current_turn - last_seen)
        profile = mobility_profile(objects, "observed-contact-envelope",
                                   subject_ref=str(item["object_ref"]), topology=topology)
        start = object_location(item)
        reached: dict[str, dict[str, Any]] = {}
        if elapsed is not None and start in topology.by_ref:
            reached = topology.arrival_map(start, profile, max_turns=elapsed)
        rows.append({
            "retired_contact_ref": item["object_ref"],
            "last_known_location_ref": start,
            "last_seen_turn": last_seen,
            "turns_since_last_seen": elapsed,
            "observed_mobility_profile": {
                "triad": profile.triad, "movement_points": profile.movement_points,
            },
            "known_world_possible_location_count": len(reached),
            "known_world_possible_location_refs": [
                ref for ref, value in sorted(
                    reached.items(), key=lambda pair: (
                        int(pair[1].get("turns") or 0), pair[0]
                    )
                )[:32]
            ],
            "epistemic_status": "estimated",
            "identity_continuity": "retired; a later similar unit is a new contact",
            "limitations": [
                "Unknown geography may enlarge or alter this envelope.",
                "The contact may have changed orders, embarked, been destroyed, or ceased to exist.",
            ],
        })
    return rows


def location_affordances(topology: PerspectiveTopology,
                         objects: Mapping[str, Mapping[str, Any]],
                         subject_refs: Iterable[str]) -> list[dict[str, Any]]:
    """Expose comparable known mechanics without assigning strategic value."""
    bases = [item for item in objects.values() if item.get("kind") == "base"]
    contacts = [item for item in objects.values()
                if item.get("kind") == "foreign_contact" and item.get("status") == "active"
                and relationship_class(item) == "hostile"]
    rows: list[dict[str, Any]] = []
    for subject_ref in dict.fromkeys(map(str, subject_refs)):
        item = objects.get(subject_ref)
        location_ref = object_location(item, subject_ref)
        square = objects.get(location_ref)
        if location_ref not in topology.by_ref or not square:
            continue
        source_fields = square.get("fields") if isinstance(square.get("fields"), Mapping) else {}
        def distance(other: Mapping[str, Any]) -> int | None:
            other_ref = object_location(other)
            if other_ref not in topology.by_ref:
                return None
            a, b = topology.by_ref[location_ref], topology.by_ref[other_ref]
            return topology.shape.distance((a.x, a.y), (b.x, b.y))
        base_distances = [value for value in (distance(base) for base in bases)
                          if value is not None]
        threat_distances = [value for value in (distance(contact) for contact in contacts)
                            if value is not None]
        rows.append({
            "subject_ref": subject_ref, "location_ref": location_ref,
            "terrain": source_fields.get("terrain"),
            "features": source_fields.get("features"),
            "rainfall": source_fields.get("rainfall"),
            "rockiness": source_fields.get("rockiness"),
            "altitude": source_fields.get("altitude"),
            "owner_ref": source_fields.get("owner_ref"),
            "nearest_known_base_distance": min(base_distances) if base_distances else None,
            "nearest_visible_contact_distance": min(threat_distances) if threat_distances else None,
            "unknown_neighbors": len(topology.shape.neighbors(
                (topology.by_ref[location_ref].x, topology.by_ref[location_ref].y)))
                - len(topology.adjacent(location_ref)),
            "strategy_boundary": "mechanical affordances only; no site ranking",
        })
    return rows


def connector_analysis(topology: PerspectiveTopology, profile: MobilityProfile) -> list[dict[str, Any]]:
    """Find articulation locations in the currently known mobility graph."""
    graph = {ref: {item.location_ref for item in topology.adjacent(ref).values()
                   if topology._passable(item, profile)}
             for ref, square in topology.by_ref.items() if topology._passable(square, profile)}
    timer = 0
    seen: set[str] = set()
    low: dict[str, int] = {}
    disc: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    articulation: set[str] = set()

    def visit(node: str) -> None:
        nonlocal timer
        seen.add(node); timer += 1; disc[node] = low[node] = timer
        children = 0
        for neighbor in sorted(graph.get(node, ())):
            if neighbor not in seen:
                parent[neighbor] = node; children += 1; visit(neighbor)
                low[node] = min(low[node], low[neighbor])
                if parent.get(node) is None and children > 1:
                    articulation.add(node)
                if parent.get(node) is not None and low[neighbor] >= disc[node]:
                    articulation.add(node)
            elif neighbor != parent.get(node):
                low[node] = min(low[node], disc[neighbor])
    for node in sorted(graph):
        if node not in seen:
            parent[node] = None; visit(node)
    return [{"location_ref": ref, "kind": "narrow_connector",
             "passage_width": 1, "mobility_profile_ref": profile.profile_ref,
             "unknown_geography_may_provide_alternates": any(
                 len(topology.adjacent(neighbor)) < len(topology.shape.neighbors(
                     (topology.by_ref[neighbor].x, topology.by_ref[neighbor].y)))
                 for neighbor in graph.get(ref, ())) }
            for ref in sorted(articulation)]
