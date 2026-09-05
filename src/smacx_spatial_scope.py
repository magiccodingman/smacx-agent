"""Bounded sovereign geometry over perspective-known squares only.

Membership is private and frozen at creation. A scope never acquires newly
discovered territory or follows a moved center without sovereign renewal.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from smacx_topology import PerspectiveTopology
from smacx_world_types import content_hash, provider_safe


# Native path.h TableOffsetX/Y[0:21], used by base.cpp BIT_BASE_RADIUS.
BASE_WORKING_OFFSETS = (
    (0, 0), (1, -1), (2, 0), (1, 1), (0, 2), (-1, 1), (-2, 0),
    (-1, -1), (0, -2), (2, -2), (2, 2), (-2, 2), (-2, -2),
    (1, -3), (3, -1), (3, 1), (1, 3), (-1, 3), (-3, 1), (-3, -1), (-1, -3),
)


def scope_geometry(
    definition: Mapping[str, Any], subjects: tuple[str, ...],
    topology: PerspectiveTopology, objects: Mapping[str, Mapping[str, Any]],
    registry: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Return compact evidence plus private membership and dependency receipt."""
    if set(definition) - {"type", "radius", "domain"}:
        raise ValueError("unsupported_scope_definition_field")
    kind = definition.get("type")
    domain = definition.get("domain", "both")
    radius = definition.get("radius", 0)
    if domain not in {"land", "sea", "both"}:
        raise ValueError("invalid_scope_domain")
    if type(radius) is not int or not 0 <= radius <= 16:
        raise ValueError("scope_radius_must_be_integer_0_to_16")
    if kind not in {"proximity", "geography", "route_corridor", "base_radius", "union"}:
        raise ValueError("invalid_scope_type")
    if not subjects or len(subjects) > (8 if kind == "union" else 1):
        raise ValueError("invalid_scope_sources")
    if kind not in {"proximity", "route_corridor"} and radius:
        raise ValueError("radius_not_applicable_to_scope_type")
    positions: set[tuple[int, int]] = set()
    dependencies: dict[str, Any] = {}
    incomplete_source = False
    for ref in subjects:
        item = objects.get(ref, {})
        source = registry.get(ref, {})
        if kind in {"proximity", "base_radius"}:
            if item.get("kind") not in ({"base"} if kind == "base_radius" else {"base", "location"}) \
                    or item.get("status", "active") != "active":
                raise ValueError("scope_center_requires_active_base_or_location")
            at = str(item.get("location_ref") or ref)
            square = topology.by_ref.get(at)
            if square is None:
                raise ValueError("scope_center_location_unknown")
            dependencies[ref] = {"kind": item.get("kind"), "location_ref": at}
            if kind == "base_radius":
                offsets = BASE_WORKING_OFFSETS
            else:
                # Logical square coordinates produce (2r+1)^2 candidates.
                offsets = tuple((a + b, a - b) for a in range(-radius, radius + 1)
                                for b in range(-radius, radius + 1))
            positions.update(position for dx, dy in offsets
                             if (position := topology.shape.normalize((square.x + dx, square.y + dy)))
                             is not None)
        else:
            allowed = {"scope"} if kind == "union" else {"route"} if kind == "route_corridor" \
                else {"region", "frontier", "theater"}
            if source.get("kind") not in allowed:
                raise ValueError("scope_source_not_current_or_wrong_kind")
            refs = tuple(source.get("location_refs") or ())
            dependencies[ref] = {"kind": source.get("kind"), "location_refs": sorted(refs)}
            incomplete_source |= bool(source.get("unknown_boundary", False))
            for location in refs:
                square = topology.by_ref.get(str(location))
                if square is None:
                    raise ValueError("scope_source_location_unknown")
                positions.add((square.x, square.y))
    if kind == "route_corridor":
        frontier = set(positions)
        for _ in range(radius):
            frontier = {neighbor for position in frontier
                        for neighbor in topology.shape.neighbors(position).values()} - positions
            positions.update(frontier)
            if not frontier:
                break
    members, stale = [], 0
    unknown = incomplete_source
    terrain_evidence = []
    for position in sorted(positions):
        square = topology.by_position.get(position)
        if square is None or square.terrain not in {"land", "ocean"}:
            unknown = True
            terrain_evidence.append((position, "unknown"))
            continue
        # Evidence freshness is a qualification, never silently promoted to
        # current by this deterministic membership calculation.
        terrain_evidence.append((position, square.terrain))
        if domain == "land" and square.ocean or domain == "sea" and not square.ocean:
            continue
        members.append(square.location_ref)
        stale += not square.current
        unknown |= any(neighbor not in topology.by_position or
                       topology.by_position[neighbor].terrain not in {"land", "ocean"}
                       for neighbor in topology.shape.neighbors(position).values())
    normalized = {"type": kind, "radius": radius, "domain": domain}
    return {
        "definition": normalized, "source_refs": list(subjects),
        "known_coverage_count": len(members), "stale_terrain_count": stale,
        "unknown_boundary": bool(unknown),
        "coverage_kind": "perspective_known_geometry",
        "dependency_hash": content_hash({"sources": dependencies,
                                         "terrain": terrain_evidence,
                                         "shape": vars(topology.shape)}),
        "_location_refs": sorted(members),
    }


def _build_spatial_registry(world_store, scope, projection):
    """Resolve current derived spatial handles independently of anchor omission."""
    timeline = projection["identity"]["timeline_id"]
    registry: dict[str, dict[str, Any]] = {}
    from smacx_world import WorldService
    service = WorldService(world_store, scope)
    from smacx_regions import PHYSICAL_LAND_PROFILE, PHYSICAL_OCEAN_PROFILE
    for profile in ("mobility-land-default", "mobility-sea-default", PHYSICAL_LAND_PROFILE, PHYSICAL_OCEAN_PROFILE):
        for region in world_store.load_regions(scope, timeline, profile):
            registry[region.region_ref] = {"kind": "region", "location_refs": sorted(region.location_refs)}
    geography = service._derived_geography(projection, persist_regions=False)
    for region in geography.get("_region_projection", ()):
        registry[region.region_ref] = {"kind": "region", "location_refs": sorted(region.location_refs)}
    for frontier in geography.get("frontiers", ()):
        registry[str(frontier["frontier_ref"])] = {
            "kind": "frontier", "location_refs": list(frontier.get("boundary_refs") or ())}
    for theater in geography.get("active_theaters", ()):
        registry[str(theater["theater_ref"])] = {
            "kind": "theater", "location_refs": list(theater.get("_location_refs") or ()),
            "subject_refs": list(theater.get("subject_refs") or ())}
    # Current issued anchors also declare footprints; complete geography above
    # ensures omitted discoveries are not lost to the compact-anchor boundary.
    for tier in ("64k", "256k"):
        anchor = world_store.current_anchor(scope, timeline, tier)
        if not anchor or anchor.get("payload", {}).get("identity", {}).get("world_epoch") != projection["identity"]["world_epoch"]:
            continue
        for frontier in anchor.get("payload", {}).get("frontiers", ()):
            registry.setdefault(str(frontier["frontier_ref"]), {
                "kind": "frontier", "location_refs": list(frontier.get("boundary_refs") or ())})
    for result in service.valid_derived_results(projection):
        route = result.get("route")
        if isinstance(route, Mapping) and route.get("route_ref"):
            registry[str(route["route_ref"])] = {"kind": "route", "location_refs": list(route.get("path") or ())}
        for item in result.get("items", ()):
            if isinstance(item, Mapping) and item.get("rendezvous_ref"):
                registry[str(item["rendezvous_ref"])] = {
                    "kind": "rendezvous", "location_refs": [str(item["candidate_ref"])] if item.get("candidate_ref") else [],
                    "subject_refs": [str(value.get("participant_ref")) for value in item.get("arrivals", ())]}
    # Scopes form a creation-ordered DAG: unions can reference only already
    # issued scopes. Recompute source receipts before exposing any handle.
    # A changed dependency withdraws the handle instead of retargeting it.
    with world_store.store._connect() as connection:
        scopes = connection.execute(
            "SELECT watch_id,typed_predicate_json,expires_turn FROM world_watches WHERE match_id=? "
            "AND agent_id=? AND perspective_id=? AND timeline_id=? AND world_epoch=? "
            "AND watch_kind='spatial_scope' AND status='active' ORDER BY created_unix,watch_id",
            (*(scope.match_id, scope.agent_id, scope.perspective_id, timeline), str(projection["identity"]["world_epoch"])),
        ).fetchall()
    if scopes:
        from smacx_spatial_scope import scope_geometry
        from smacx_world import WorldService
        topology = WorldService._topology(projection)
        objects = {str(item["object_ref"]): item for item in projection.get("objects", ())}
        turn_state = next((item for item in objects.values() if item.get("kind") == "turn_state"), {})
        current_turn = turn_state.get("fields", {}).get("turn", {}).get("value")
        for row in scopes:
            if current_turn is not None and row["expires_turn"] is not None and row["expires_turn"] < current_turn:
                continue
            saved = json.loads(row["typed_predicate_json"]).get("_scope", {})
            try:
                current = scope_geometry(saved["definition"], tuple(saved["source_refs"]),
                                         topology, objects, registry)
            except (KeyError, ValueError):
                continue
            if current["dependency_hash"] == saved.get("dependency_hash"):
                registry[str(row["watch_id"])] = {
                    "kind": "scope", "location_refs": current["_location_refs"],
                    "unknown_boundary": current["unknown_boundary"],
                    "descriptor": provider_safe(current),
                }
    return registry



def semantic_spatial_registry(world_store, scope, projection):
    """Reuse a complete registry only while every declared input stays equal."""
    identity = projection["identity"]
    key = (scope.match_id, scope.agent_id, scope.perspective_id, identity["timeline_id"], identity["world_epoch"])
    with world_store.store._connect() as connection:
        watches = connection.execute(
            "SELECT watch_id,typed_predicate_json,expires_turn FROM world_watches WHERE match_id=? AND agent_id=? "
            "AND perspective_id=? AND timeline_id=? AND world_epoch=? AND watch_kind='spatial_scope' "
            "AND status='active' ORDER BY created_unix,watch_id", key).fetchall()
        queries = connection.execute(
            "SELECT query_fingerprint,dependency_hash FROM world_query_cache WHERE match_id=? AND agent_id=? "
            "AND perspective_id=? AND timeline_id=? AND world_epoch=? ORDER BY query_fingerprint", key).fetchall()
        regions = connection.execute(
            "SELECT region_ref,location_refs_json FROM world_regions WHERE match_id=? AND agent_id=? AND perspective_id=? "
            "AND timeline_id=? ORDER BY region_ref", key[:4]).fetchall()
    anchors = [world_store.current_anchor(scope, identity["timeline_id"], tier) for tier in ("64k", "256k")]
    signature = content_hash({"scope":key,"revision":projection["world_revision"],"action_revision":projection.get("action_revision"),
        "watches":[list(row) for row in watches],"queries":[list(row) for row in queries],
        "regions":[list(row) for row in regions],
        "issued":[{"promotions":a.get("payload",{}).get("lod",{}).get("promotion_refs",[]),
                   "frontiers":a.get("payload",{}).get("frontiers",[])} if a else None for a in anchors]})
    with world_store.store._spatial_cache_lock:
        cached = world_store.store._spatial_registry_cache.get(signature)
    if cached is not None:
        return cached
    result = _build_spatial_registry(world_store, scope, projection)
    with world_store.store._spatial_cache_lock:
        cache = world_store.store._spatial_registry_cache
        cache[signature] = result
        while len(cache) > 2:
            cache.pop(next(iter(cache)))
    return result
