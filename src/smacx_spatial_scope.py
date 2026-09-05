"""Bounded sovereign geometry over perspective-known squares only.

Membership is private and frozen at creation. A scope never acquires newly
discovered territory or follows a moved center without sovereign renewal.
"""

from __future__ import annotations

from typing import Any, Mapping

from smacx_topology import PerspectiveTopology
from smacx_world_types import content_hash


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
