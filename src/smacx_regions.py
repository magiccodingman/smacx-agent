"""Versioned physical geography, mobility regions, frontiers, and theaters."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Iterable, Mapping

from smacx_topology import MobilityProfile, PerspectiveTopology


PHYSICAL_LAND_PROFILE = "physical-landmass"
PHYSICAL_OCEAN_PROFILE = "physical-ocean-mass"


@dataclass(frozen=True)
class Region:
    region_ref: str
    lineage_ref: str
    version: int
    mobility_profile_ref: str
    anchor_location_ref: str
    location_refs: frozenset[str]
    supersedes: tuple[str, ...] = ()
    lineage_birth_revision: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "region_ref": self.region_ref, "lineage_ref": self.lineage_ref,
            "version": self.version, "mobility_profile_ref": self.mobility_profile_ref,
            "anchor_location_ref": self.anchor_location_ref,
            "location_count": len(self.location_refs),
            "supersedes": list(self.supersedes),
            "lineage_birth_revision": self.lineage_birth_revision,
        }


@dataclass(frozen=True)
class Frontier:
    frontier_ref: str
    region_ref: str
    boundary_refs: tuple[str, ...]
    unknown_neighbor_count: int
    may_connect_elsewhere: bool
    landmass_ref: str | None = None
    contiguous_unknown_boundary_ref: str | None = None
    mapped_location_count: int = 0
    current_boundary_count: int = 0
    stale_boundary_count: int = 0
    oldest_last_verified_turn: int | None = None
    newest_last_verified_turn: int | None = None
    possible_known_component_refs: tuple[str, ...] = ()
    nearby_current_foreign_faction_refs: tuple[str, ...] = ()
    nearby_stale_foreign_faction_refs: tuple[str, ...] = ()
    nearby_resource_counts: Mapping[str, int] = field(default_factory=dict)
    nearby_resource_composition: tuple[Mapping[str, Any], ...] = ()
    nearby_landmark_refs: tuple[str, ...] = ()
    nearby_landmarks: tuple[Mapping[str, Any], ...] = ()
    adjacent_ocean_mass_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "frontier_ref": self.frontier_ref, "region_ref": self.region_ref,
            "landmass_ref": self.landmass_ref,
            "contiguous_unknown_boundary_ref": self.contiguous_unknown_boundary_ref,
            "boundary_refs": list(self.boundary_refs),
            "unknown_boundary_size": self.unknown_neighbor_count,
            "unknown_neighbor_count": self.unknown_neighbor_count,
            "mapped_location_count": self.mapped_location_count,
            "mapped_coverage_status": "partial_known_component",
            "may_connect_elsewhere": self.may_connect_elsewhere,
            "unknown_geography_may_connect_known_components": bool(
                self.possible_known_component_refs
            ),
            "possible_known_component_refs": list(self.possible_known_component_refs),
            "map_information": {
                "current_boundary_locations": self.current_boundary_count,
                "stale_boundary_locations": self.stale_boundary_count,
                "oldest_last_verified_turn": self.oldest_last_verified_turn,
                "newest_last_verified_turn": self.newest_last_verified_turn,
                "provenance": "perspective_known_map",
            },
            "nearby_foreign_faction_refs": sorted(set(
                self.nearby_current_foreign_faction_refs
                + self.nearby_stale_foreign_faction_refs
            )),
            "nearby_current_foreign_faction_refs": list(
                self.nearby_current_foreign_faction_refs
            ),
            "nearby_stale_foreign_faction_refs": list(
                self.nearby_stale_foreign_faction_refs
            ),
            "nearby_resource_counts": dict(self.nearby_resource_counts),
            "nearby_resource_composition": [dict(value)
                                               for value in self.nearby_resource_composition],
            "nearby_landmark_refs": list(self.nearby_landmark_refs),
            "nearby_landmarks": [dict(value) for value in self.nearby_landmarks],
            "adjacent_ocean_mass_refs": list(self.adjacent_ocean_mass_refs),
            "detail": "bounded_anchor_summary; scout ETA is query-scoped",
        }


def _lineage(profile_ref: str, anchor_ref: str, birth_revision: int) -> str:
    digest = hashlib.sha256(
        f"{profile_ref}\x1f{anchor_ref}\x1f{birth_revision}".encode()
    ).hexdigest()[:16]
    return f"region-lineage-{digest}"


class RegionBuilder:
    """Build connected known components while retaining split/merge lineage."""

    @staticmethod
    def _physical_components(
        topology: PerspectiveTopology, *, ocean: bool,
    ) -> list[set[str]]:
        remaining = {
            ref for ref, square in topology.by_ref.items()
            if square.terrain != "unknown" and square.ocean is ocean
        }
        components: list[set[str]] = []
        while remaining:
            seed = min(remaining)
            component = {seed}
            stack = [seed]
            remaining.remove(seed)
            while stack:
                ref = stack.pop()
                for square in topology.adjacent(ref).values():
                    if square.location_ref in remaining and square.ocean is ocean:
                        remaining.remove(square.location_ref)
                        component.add(square.location_ref)
                        stack.append(square.location_ref)
            components.append(component)
        return components

    def _build_components(
        self, components: Iterable[set[str]], profile_ref: str,
        previous: Iterable[Region], *, world_revision: int,
    ) -> tuple[list[Region], dict[str, str]]:
        old = list(previous)
        results: list[Region] = []
        for component in sorted(components, key=lambda item: min(item)):
            overlaps = [region for region in old if region.location_refs & component]
            candidates = [region for region in overlaps
                          if region.anchor_location_ref in component]
            owner = min(candidates, key=lambda item: (
                item.lineage_birth_revision, item.lineage_ref,
            )) if candidates else None
            anchor_ref = owner.anchor_location_ref if owner else min(component)
            lineage_ref = owner.lineage_ref if owner else _lineage(
                profile_ref, anchor_ref, world_revision,
            )
            unchanged = next((region for region in old
                              if region.lineage_ref == lineage_ref
                              and region.location_refs == frozenset(component)), None)
            version = unchanged.version if unchanged else max(
                (region.version for region in old if region.lineage_ref == lineage_ref),
                default=0,
            ) + 1
            region_ref = f"{lineage_ref}-v{version}"
            supersedes = tuple(sorted(region.region_ref for region in overlaps
                                      if region.region_ref != region_ref))
            results.append(Region(
                region_ref, lineage_ref, version, profile_ref, anchor_ref,
                frozenset(component), supersedes,
                owner.lineage_birth_revision if owner else world_revision,
            ))
        aliases: dict[str, str] = {}
        for region in old:
            successors = [item for item in results if item.location_refs & region.location_refs]
            if len(successors) == 1 and successors[0].region_ref != region.region_ref:
                aliases[region.region_ref] = successors[0].region_ref
        return results, aliases

    def build(
        self, topology: PerspectiveTopology, profile: MobilityProfile,
        previous: Iterable[Region] = (), *, world_revision: int,
    ) -> tuple[list[Region], dict[str, str]]:
        return self._build_components(
            topology.connected_components(profile), profile.profile_ref, previous,
            world_revision=world_revision,
        )

    def build_physical(
        self, topology: PerspectiveTopology, kind: str,
        previous: Iterable[Region] = (), *, world_revision: int,
    ) -> tuple[list[Region], dict[str, str]]:
        if kind not in {"land", "ocean"}:
            raise ValueError("physical_mass_kind_must_be_land_or_ocean")
        profile = PHYSICAL_LAND_PROFILE if kind == "land" else PHYSICAL_OCEAN_PROFILE
        return self._build_components(
            self._physical_components(topology, ocean=kind == "ocean"), profile, previous,
            world_revision=world_revision,
        )

    def frontiers(
        self, topology: PerspectiveTopology, regions: Iterable[Region], *,
        landmass_by_location: Mapping[str, str] | None = None,
        ocean_mass_by_location: Mapping[str, str] | None = None,
        objects_by_location: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
        relationship_by_faction: Mapping[str, str] | None = None,
    ) -> list[Frontier]:
        """Return one bounded object per contiguous known boundary segment."""
        landmass_by_location = landmass_by_location or {}
        ocean_mass_by_location = ocean_mass_by_location or {}
        objects_by_location = objects_by_location or {}
        relationship_by_faction = relationship_by_faction or {}
        location_to_component = {
            ref: region.region_ref for region in regions for ref in region.location_refs
        }
        result: list[Frontier] = []
        for region in regions:
            boundary: set[str] = set()
            missing_by_ref: dict[str, set[tuple[int, int]]] = {}
            for ref in region.location_refs:
                square = topology.by_ref[ref]
                possible = set(topology.shape.neighbors((square.x, square.y)).values())
                known = {(item.x, item.y) for item in topology.adjacent(ref).values()
                         if item.terrain != "unknown"}
                missing = possible - known
                if missing:
                    boundary.add(ref)
                    missing_by_ref[ref] = missing
            while boundary:
                seed = min(boundary)
                segment = {seed}
                stack = [seed]
                boundary.remove(seed)
                while stack:
                    ref = stack.pop()
                    neighbors = {item.location_ref for item in topology.adjacent(ref).values()}
                    for neighbor in sorted(boundary & neighbors):
                        boundary.remove(neighbor)
                        segment.add(neighbor)
                        stack.append(neighbor)
                missing_positions = set().union(*(missing_by_ref[ref] for ref in segment))
                digest = hashlib.sha256(
                    (region.lineage_ref + "\x1f" + "\x1f".join(sorted(segment))).encode()
                ).hexdigest()[:16]
                nearby_resources: dict[str, dict[str, Any]] = {}
                landmarks: set[str] = set()
                landmark_rows: dict[str, dict[str, Any]] = {}
                current_foreign: set[str] = set()
                stale_foreign: set[str] = set()
                ocean_refs: set[str] = set()
                current_count = stale_count = 0
                turns: list[int] = []
                for ref in segment:
                    current_count += int(topology.by_ref[ref].current)
                    stale_count += int(not topology.by_ref[ref].current)
                    for obj in objects_by_location.get(ref, ()):
                        fields = obj.get("fields") if isinstance(obj.get("fields"), Mapping) else {}
                        if obj.get("kind") == "landmark":
                            landmark_ref = str(obj.get("object_ref"))
                            landmarks.add(landmark_ref)
                            name = fields.get("name") if isinstance(fields, Mapping) else None
                            landmark_rows[landmark_ref] = {
                                "landmark_ref": landmark_ref,
                                "name": name.get("value") if isinstance(name, Mapping) else None,
                                "epistemic_status": name.get("epistemic_status")
                                if isinstance(name, Mapping) else "unknown",
                            }
                        location_landmarks = fields.get("landmarks") \
                            if isinstance(fields, Mapping) else None
                        for landmark in (
                            location_landmarks.get("value", ())
                            if isinstance(location_landmarks, Mapping) else ()
                        ) or ():
                            if not isinstance(landmark, Mapping):
                                continue
                            landmark_type = str(
                                landmark.get("natural_name") or landmark.get("landmark_type")
                                or "known_natural_landmark"
                            )
                            key = f"type:{landmark_type}:{ref}"
                            landmark_rows[key] = {
                                "landmark_type": landmark_type,
                                "location_ref": ref,
                                "epistemic_status": location_landmarks.get(
                                    "epistemic_status", "unknown"
                                ),
                            }
                        owner = fields.get("owner_ref") if isinstance(fields, Mapping) else None
                        owner_value = owner.get("value") if isinstance(owner, Mapping) else None
                        if owner_value and relationship_by_faction.get(
                            str(owner_value), "unknown"
                        ) != "self":
                            target = current_foreign if owner.get(
                                "epistemic_status"
                            ) == "current" else stale_foreign
                            target.add(str(owner_value))
                        features = fields.get("features") if isinstance(fields, Mapping) else None
                        values = features.get("value", []) if isinstance(features, Mapping) else []
                        for feature in values or ():
                            if feature in {"nutrient_resource", "mineral_resource", "energy_resource",
                                           "resource_bonus", "monolith", "supply_pod"}:
                                row = nearby_resources.setdefault(str(feature), {
                                    "feature": str(feature), "current_count": 0,
                                    "stale_count": 0, "representative_refs": [],
                                })
                                freshness = "current" if features.get(
                                    "epistemic_status"
                                ) == "current" else "stale"
                                row[f"{freshness}_count"] += 1
                                if len(row["representative_refs"]) < 3:
                                    row["representative_refs"].append(ref)
                        if obj.get("kind") == "location":
                            for name in ("terrain", "altitude", "features", "owner_ref"):
                                value = fields.get(name)
                                if isinstance(value, Mapping) and isinstance(value.get("last_verified_turn"), int):
                                    turns.append(int(value["last_verified_turn"]))
                    for neighbor in topology.adjacent(ref).values():
                        if ocean_mass_by_location.get(neighbor.location_ref):
                            ocean_refs.add(ocean_mass_by_location[neighbor.location_ref])
                nearby = {neighbor.location_ref for ref in segment
                          for neighbor in topology.adjacent(ref).values()} - segment
                for ref in nearby:
                    for obj in objects_by_location.get(ref, ()):
                        if obj.get("kind") != "foreign_contact":
                            continue
                        owner = obj.get("fields", {}).get("owner_ref", {})
                        if not isinstance(owner, Mapping) or not owner.get("value"):
                            continue
                        target = current_foreign if obj.get("status") == "active" and owner.get("epistemic_status") == "current" else stale_foreign
                        target.add(str(owner["value"]))
                possible_components: set[str] = set()
                for position in missing_positions:
                    for adjacent in topology.shape.neighbors(position).values():
                        known = topology.by_position.get(adjacent)
                        if known:
                            other = location_to_component.get(known.location_ref)
                            if other and other != region.region_ref:
                                possible_components.add(other)
                result.append(Frontier(
                    f"frontier-{digest}", region.region_ref, tuple(sorted(segment)),
                    len(missing_positions), True, landmass_by_location.get(seed),
                    f"unknown-boundary-{digest}", len(region.location_refs),
                    current_count, stale_count, min(turns) if turns else None,
                    max(turns) if turns else None, tuple(sorted(possible_components)[:8]),
                    tuple(sorted(current_foreign)[:8]), tuple(sorted(stale_foreign)[:8]),
                    {key: value["current_count"] + value["stale_count"]
                     for key, value in sorted(nearby_resources.items())},
                    tuple(dict(value) for _, value in sorted(nearby_resources.items())),
                    tuple(sorted(landmarks)[:8]),
                    tuple(landmark_rows[key] for key in sorted(landmark_rows)[:8]),
                    tuple(sorted(ocean_refs)[:8]),
                ))
        return result


@dataclass(frozen=True)
class Theater:
    theater_ref: str
    region_refs: tuple[str, ...]
    subject_refs: tuple[str, ...]
    reason: str
    salience: int
    source_world_revision: int
    landmass_refs: tuple[str, ...] = ()
    participant_faction_refs: tuple[str, ...] = ()
    allied_faction_refs: tuple[str, ...] = ()
    hostile_faction_refs: tuple[str, ...] = ()
    threatened_base_refs: tuple[str, ...] = ()
    transport_dependency_refs: tuple[str, ...] = ()
    recent_material_refs: tuple[str, ...] = ()
    promoted_by_refs: tuple[str, ...] = ()
    location_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "theater_ref": self.theater_ref, "region_refs": list(self.region_refs),
            "_location_refs": list(self.location_refs),
            "landmass_refs": list(self.landmass_refs),
            "subject_refs": list(self.subject_refs), "reason": self.reason,
            "salience": self.salience, "source_world_revision": self.source_world_revision,
            "participant_faction_refs": list(self.participant_faction_refs),
            "allied_faction_refs": list(self.allied_faction_refs),
            "hostile_faction_refs": list(self.hostile_faction_refs),
            "threatened_base_refs": list(self.threatened_base_refs),
            "transport_dependency_refs": list(self.transport_dependency_refs),
            "recent_material_refs": list(self.recent_material_refs),
            "promoted_by_refs": list(self.promoted_by_refs),
            "interpretation_boundary": "mechanical operational overlay; sovereign owns strategy",
        }


def _field_value(item: Mapping[str, Any], name: str) -> Any:
    fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
    field = fields.get(name)
    return field.get("value") if isinstance(field, Mapping) else None


def build_theaters(
    objects: Iterable[Mapping[str, Any]], location_to_region: Mapping[str, Any], *,
    world_revision: int, location_to_mass: Mapping[str, str] | None = None,
    region_adjacency: Mapping[str, Iterable[str]] | None = None,
    promoted_refs: Iterable[str] = (), recent_material_refs: Iterable[str] = (),
    topology: PerspectiveTopology | None = None,
) -> list[Theater]:
    """Build neutral, mechanically connected operational overlays."""
    rows = list(objects)
    location_to_mass = location_to_mass or {}
    region_adjacency = region_adjacency or {}
    promoted = set(map(str, promoted_refs))
    recent = set(map(str, recent_material_refs))
    active: dict[str, Mapping[str, Any]] = {}
    for item in rows:
        ref = str(item.get("object_ref") or "")
        location = str(item.get("location_ref") or "")
        if item.get("status", "active") != "active" or not location:
            continue
        if (item.get("kind") in {"combat", "global_event"}
            or item.get("kind") == "foreign_contact" and _field_value(item, "relationship") in {"hostile", "allied"}
            or _field_value(item, "hostile") is True or _field_value(item, "threatened") is True
            or ref in promoted or ref in recent or location in promoted or location in recent):
            active[ref] = item
    graph = {ref: set() for ref in active}
    # Regions describe mobility, not operational coupling. Join locally interacting
    # participants only; distinct distant crises on one continent remain separate.
    for left, a in active.items():
        for right, b in active.items():
            if right <= left:
                continue
            la, lb = str(a.get("location_ref")), str(b.get("location_ref"))
            local = la == lb
            if topology and la in topology.by_ref and lb in topology.by_ref:
                sa, sb = topology.by_ref[la], topology.by_ref[lb]
                local = topology.shape.distance((sa.x, sa.y), (sb.x, sb.y)) <= 3
            owner_a, owner_b = _field_value(a, "owner_ref"), _field_value(b, "owner_ref")
            related = (owner_a is not None and owner_a == owner_b
                       or "allied" in {_field_value(a, "relationship"), _field_value(b, "relationship")}
                       or _field_value(a, "threatened") is True or _field_value(b, "threatened") is True)
            if local and related:
                graph[left].add(right); graph[right].add(left)
    for item in rows:
        if item.get("kind") not in {"route", "convoy", "operation"}:
            continue
        linked = set(map(str, _field_value(item, "subject_refs") or ()))
        linked.update(str(_field_value(item, name) or "") for name in ("origin_ref", "target_ref"))
        participants = {ref for ref, row in active.items()
                        if ref in linked or str(row.get("location_ref")) in linked}
        for ref in participants:
            graph[ref].update(participants - {ref})
    theaters: list[Theater] = []
    unseen = set(graph)
    while unseen:
        seed = min(unseen)
        component = {seed}
        stack = [seed]
        unseen.remove(seed)
        while stack:
            region = stack.pop()
            for neighbor in sorted(graph[region] & unseen):
                unseen.remove(neighbor); component.add(neighbor); stack.append(neighbor)
        involved = [active[ref] for ref in component]
        involved_regions: set[str] = set()
        for item in involved:
            mapped = location_to_region.get(str(item.get("location_ref")), ())
            involved_regions.update((mapped,) if isinstance(mapped, str) else mapped)
        subjects = sorted({str(item.get("object_ref")) for item in involved if item.get("object_ref")})
        locations = {str(item.get("location_ref")) for item in involved if item.get("location_ref")}
        # Spatial footprint is actual activity plus explicit linked route
        # evidence, never all cells of a containing mobility region.
        for route in rows:
            if route.get("kind") not in {"route", "convoy", "operation"}:
                continue
            linked = set(map(str, _field_value(route, "subject_refs") or ()))
            linked.update(str(_field_value(route, name) or "") for name in ("origin_ref", "target_ref"))
            if linked & (set(subjects) | locations):
                locations.update(str(ref) for ref in (_field_value(route, "path") or ())
                                 if topology and str(ref) in topology.by_ref)
        factions = {str(_field_value(item, "owner_ref")) for item in involved
                    if _field_value(item, "owner_ref")}
        allied = {str(_field_value(item, "owner_ref")) for item in involved
                  if _field_value(item, "relationship") == "allied"}
        hostile = {str(_field_value(item, "owner_ref")) for item in involved
                   if _field_value(item, "relationship") == "hostile"}
        threatened = {str(item.get("object_ref")) for item in involved
                      if item.get("kind") == "base" and _field_value(item, "threatened") is True}
        transports: set[str] = set()
        for item in involved:
            roles = _field_value(item, "roles")
            if item.get("kind") in {"route", "convoy"} or (
                isinstance(roles, Mapping) and roles.get("transport")
            ):
                transports.add(str(item.get("object_ref")))
        promoted_here = (set(subjects) | locations) & promoted
        recent_here = (set(subjects) | locations) & recent
        digest = hashlib.sha256(
            ("\x1f".join(sorted(component)) + "\x1e" + "\x1f".join(subjects)).encode()
        ).hexdigest()[:16]
        theaters.append(Theater(
            f"theater-{digest}", tuple(sorted(involved_regions)), tuple(subjects[:32]),
            ",".join(sorted({str(item.get("kind")) for item in involved})),
            min(100, 35 + len(subjects) * 5 + len(promoted_here) * 15 + len(recent_here) * 10),
            world_revision, tuple(sorted({location_to_mass.get(ref, "") for ref in locations}
                                         - {""})[:12]), tuple(sorted(factions)[:8]),
            tuple(sorted(allied)[:8]), tuple(sorted(hostile)[:8]),
            tuple(sorted(threatened)[:12]), tuple(sorted(transports)[:12]),
            tuple(sorted(recent_here)[:12]), tuple(sorted(promoted_here)[:12]), tuple(sorted(locations)),
        ))
    return theaters
