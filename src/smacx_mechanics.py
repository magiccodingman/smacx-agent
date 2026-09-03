"""Deterministic fair-play strategic mechanics over a perspective projection.

This module computes geometry, timing, and constraints. It deliberately never
ranks strategic value or recommends an action.
"""

from __future__ import annotations

from collections import Counter
from math import ceil
from typing import Any, Iterable, Mapping

from smacx_topology import MobilityProfile, PerspectiveTopology


def field_value(item: Mapping[str, Any], name: str, default: Any = None) -> Any:
    field = item.get("fields", {}).get(name) if isinstance(item.get("fields"), Mapping) else None
    return field.get("value", default) if isinstance(field, Mapping) else default


def object_location(item: Mapping[str, Any] | None, fallback: str = "") -> str:
    if not item:
        return fallback
    return str(item.get("location_ref") or item.get("object_ref") or fallback)


def mobility_profile(objects: Mapping[str, Mapping[str, Any]], profile_ref: str,
                     *, subject_ref: str = "") -> MobilityProfile:
    """Resolve a bounded profile from owned, perspective-known unit facts."""
    subject = objects.get(subject_ref, {})
    triad = str(field_value(subject, "triad", ""))
    if not triad:
        triad = "sea" if "sea" in profile_ref else "air" if "air" in profile_ref else "land"
    moves = field_value(subject, "movement_points", field_value(subject, "speed", 1))
    try:
        moves = max(1, int(moves))
    except (TypeError, ValueError):
        moves = 1
    roles = field_value(subject, "roles", {})
    abilities = field_value(subject, "abilities", [])
    if not isinstance(abilities, list):
        abilities = []
    if isinstance(roles, Mapping) and roles.get("boarded"):
        triad = "sea"
    refuel = {
        object_location(item) for item in objects.values()
        if item.get("kind") == "base" and field_value(item, "owner_ref") == field_value(subject, "owner_ref")
    }
    for item in objects.values():
        item_roles = field_value(item, "roles", {})
        if item.get("kind") == "own_unit" and isinstance(item_roles, Mapping) \
                and item_roles.get("carrier"):
            refuel.add(object_location(item))
    own_bases = [item for item in objects.values() if item.get("kind") == "base"
                 and field_value(item, "owner_ref") == field_value(subject, "owner_ref")]
    psi_gates = [object_location(item) for item in own_bases
                 if "psi_gate" in set(map(str, field_value(item, "facilities", []) or []))]
    special_connections = tuple(
        (origin, target, 1.0, "psi_gate")
        for origin in psi_gates for target in psi_gates if origin != target
    )
    airdrop_destinations = frozenset(
        object_location(item) for item in own_bases
        if object_location(item)
    ) if isinstance(roles, Mapping) and roles.get("airdrop_capable") else frozenset()
    return MobilityProfile(
        profile_ref=profile_ref, triad=triad, movement_points=moves,
        ignores_zoc="clean_reactor" in abilities or "hover" in abilities,
        can_embark=False,
        can_airdrop=bool(isinstance(roles, Mapping) and roles.get("airdrop_capable")),
        max_air_turns=(int(field_value(subject, "air_range", 0)) or None)
        if triad == "air" else None,
        refuel_location_refs=frozenset(refuel), abilities=frozenset(map(str, abilities)),
        special_connections=special_connections,
        airdrop_destination_refs=airdrop_destinations,
    )


def response_matrix(topology: PerspectiveTopology,
                    objects: Mapping[str, Mapping[str, Any]],
                    origins: Iterable[str], targets: Iterable[str],
                    profile_ref: str) -> list[dict[str, Any]]:
    rows = []
    for origin_ref in dict.fromkeys(map(str, origins)):
        origin = object_location(objects.get(origin_ref), origin_ref)
        profile = mobility_profile(objects, profile_ref, subject_ref=origin_ref)
        cells = []
        for target_ref in dict.fromkeys(map(str, targets)):
            target = object_location(objects.get(target_ref), target_ref)
            route = topology.route(origin, target, profile)
            cells.append({
                "target_ref": target_ref, "reachable": route.reachable,
                "eta_turns": route.turns, "movement_cost": route.movement_cost,
                "uncertainty": list(route.uncertainty),
            })
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
                mobility_profile(objects, profile_ref, subject_ref=participant_ref),
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
                if item.get("kind") == "foreign_contact" and item.get("status") == "active"]
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
                                       mobility_profile(objects, "owned-response", subject_ref=str(unit["object_ref"])))
            except Exception:
                continue
            reinforcements.append({"unit_ref": unit["object_ref"], "eta_turns": route.turns,
                                   "reachable": route.reachable,
                                   "uncertainty": list(route.uncertainty)})
        for contact in contacts:
            try:
                route = topology.route(object_location(contact), location,
                                       mobility_profile(objects, "observed-hostile-response",
                                                        subject_ref=str(contact["object_ref"])))
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
            "support_burden": sum(1 for unit in own_units
                                  if field_value(unit, "home_base_ref") == ref),
        })
    return rows


def logistics(objects: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    own = [item for item in objects.values() if item.get("kind") == "own_unit"]
    home_counts = Counter(str(field_value(item, "home_base_ref")) for item in own
                          if field_value(item, "home_base_ref"))
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
            convoys.append({**compact, "order": field_value(item, "order_name")})
    return {"support_by_home_base": dict(home_counts), "transports": transports,
            "aircraft": aircraft, "convoys": convoys}


def lost_contact_envelopes(topology: PerspectiveTopology,
                           objects: Mapping[str, Mapping[str, Any]],
                           *, current_turn: int | None) -> list[dict[str, Any]]:
    """Bound possible locations using only last-seen, known terrain, and observed mobility.

    This never preserves identity across fog and never asserts that a unit still
    exists. The result is an explicit possibility envelope, not hidden tracking.
    """
    rows: list[dict[str, Any]] = []
    for item in objects.values():
        if item.get("kind") != "foreign_contact" or item.get("status") != "lost":
            continue
        last_seen = field_value(item, "last_seen_turn")
        elapsed = None
        if isinstance(current_turn, int) and isinstance(last_seen, int):
            elapsed = max(0, current_turn - last_seen)
        profile = mobility_profile(objects, "observed-contact-envelope",
                                   subject_ref=str(item["object_ref"]))
        start = object_location(item)
        reached: dict[str, float] = {}
        if elapsed is not None and start in topology.by_ref:
            reached = topology.reachable_costs(
                start, profile, max_cost=float(max(1, elapsed) * profile.movement_points),
            )
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
                ref for ref, _ in sorted(reached.items(), key=lambda pair: (pair[1], pair[0]))[:32]
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
                if item.get("kind") == "foreign_contact" and item.get("status") == "active"]
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
