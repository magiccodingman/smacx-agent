"""Versioned known-world regions, frontiers, and operational theaters."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterable, Mapping

from smacx_topology import MobilityProfile, PerspectiveTopology


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

    def as_dict(self) -> dict:
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

    def as_dict(self) -> dict:
        return {
            "frontier_ref": self.frontier_ref, "region_ref": self.region_ref,
            "boundary_refs": list(self.boundary_refs),
            "unknown_neighbor_count": self.unknown_neighbor_count,
            "may_connect_elsewhere": self.may_connect_elsewhere,
        }


def _lineage(profile_ref: str, anchor_ref: str, birth_revision: int) -> str:
    # A component detached by a later split is a new lineage even if the same
    # canonical location happened to anchor an older, subsequently merged
    # branch.  Birth revision makes that temporal identity explicit.
    digest = hashlib.sha256(
        f"{profile_ref}\x1f{anchor_ref}\x1f{birth_revision}".encode()
    ).hexdigest()[:16]
    return f"region-lineage-{digest}"


class RegionBuilder:
    """Build connected known components while retaining split/merge lineage."""

    def build(
        self, topology: PerspectiveTopology, profile: MobilityProfile,
        previous: Iterable[Region] = (), *, world_revision: int,
    ) -> tuple[list[Region], dict[str, str]]:
        old = list(previous)
        results: list[Region] = []
        for component in sorted(topology.connected_components(profile), key=lambda item: min(item)):
            overlaps = [region for region in old if region.location_refs & component]
            anchor_candidates = [region for region in overlaps
                                 if region.anchor_location_ref in component]
            anchor_owner = min(
                anchor_candidates,
                key=lambda item: (item.lineage_birth_revision, item.lineage_ref),
            ) if anchor_candidates else None
            anchor_ref = anchor_owner.anchor_location_ref if anchor_owner else min(component)
            lineage_ref = anchor_owner.lineage_ref if anchor_owner else _lineage(
                profile.profile_ref, anchor_ref, world_revision,
            )
            old_same = next((region for region in old
                             if region.lineage_ref == lineage_ref
                             and region.location_refs == frozenset(component)), None)
            version = old_same.version if old_same else max(
                (region.version for region in old if region.lineage_ref == lineage_ref), default=0,
            ) + 1
            region_ref = f"{lineage_ref}-v{version}"
            supersedes = tuple(sorted(region.region_ref for region in overlaps
                                      if region.region_ref != region_ref))
            current = Region(region_ref, lineage_ref, version, profile.profile_ref,
                             anchor_ref, frozenset(component), supersedes,
                             anchor_owner.lineage_birth_revision if anchor_owner
                             else world_revision)
            results.append(current)
        aliases: dict[str, str] = {}
        for region in old:
            successors = [item for item in results if item.location_refs & region.location_refs]
            # Only deterministic one-to-one supersession is aliasable. Splits
            # remain explicit and invalidate derived handles/watches.
            if len(successors) == 1 and successors[0].region_ref != region.region_ref:
                aliases[region.region_ref] = successors[0].region_ref
        return results, aliases

    def frontiers(self, topology: PerspectiveTopology, regions: Iterable[Region]) -> list[Frontier]:
        result: list[Frontier] = []
        for region in regions:
            boundary: set[str] = set()
            unknown = 0
            for ref in region.location_refs:
                square = topology.by_ref[ref]
                known_neighbors = topology.adjacent(ref)
                possible = topology.shape.neighbors((square.x, square.y))
                missing = len(possible) - len(known_neighbors)
                if missing:
                    boundary.add(ref)
                    unknown += missing
            if not boundary:
                continue
            digest = hashlib.sha256(
                (region.region_ref + "\x1f" + "\x1f".join(sorted(boundary))).encode()
            ).hexdigest()[:16]
            result.append(Frontier(
                f"frontier-{digest}", region.region_ref, tuple(sorted(boundary)), unknown,
                may_connect_elsewhere=unknown > 0,
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

    def as_dict(self) -> dict:
        return {
            "theater_ref": self.theater_ref, "region_refs": list(self.region_refs),
            "subject_refs": list(self.subject_refs), "reason": self.reason,
            "salience": self.salience, "source_world_revision": self.source_world_revision,
        }


def build_theaters(
    objects: Iterable[Mapping], location_to_region: Mapping[str, str], *, world_revision: int,
) -> list[Theater]:
    """Create neutral activity overlays; this does not choose strategy."""
    active: dict[str, list[str]] = {}
    reasons: dict[str, set[str]] = {}
    for item in objects:
        location = item.get("location_ref")
        region = location_to_region.get(str(location))
        if not region:
            continue
        kind = str(item.get("kind") or "")
        fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
        hostile = fields.get("hostile") if isinstance(fields, Mapping) else None
        relationship = fields.get("relationship") if isinstance(fields, Mapping) else None
        threatened = fields.get("threatened") if isinstance(fields, Mapping) else None
        is_active = kind in {"combat", "global_event"} \
            or (kind == "foreign_contact" and isinstance(relationship, Mapping)
                and relationship.get("value") == "hostile") \
            or (isinstance(hostile, Mapping) and hostile.get("value") is True) \
            or (isinstance(threatened, Mapping) and threatened.get("value") is True)
        if is_active:
            active.setdefault(region, []).append(str(item.get("object_ref")))
            reasons.setdefault(region, set()).add(kind)
    theaters = []
    for region, refs in sorted(active.items()):
        digest = hashlib.sha256((region + "\x1f" + "\x1f".join(sorted(refs))).encode()).hexdigest()[:16]
        theaters.append(Theater(
            f"theater-{digest}", (region,), tuple(sorted(refs)),
            ",".join(sorted(reasons[region])), min(100, 50 + len(refs) * 5), world_revision,
        ))
    return theaters
