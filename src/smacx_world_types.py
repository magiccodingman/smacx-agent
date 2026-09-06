"""Shared contracts for the perspective-scoped SMACX world model.

These types intentionally contain no bridge, provider, or persistence code.
They are the validation boundary between fair-play observations and every
derived calculator/projection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping


REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")

# Collector/private implementation details are permitted inside the durable
# projection, but never across a provider-facing boundary.  Keep the policy in
# one recursive serializer: filtering only WorldObject.metadata is insufficient
# because cached dictionaries, deltas, auxiliary envelopes, and promoted
# focus/watch objects can otherwise bypass it.
_PRIVATE_EXACT_KEYS = frozenset({
    "engine_id", "hidden_id", "native_id", "native_observation_key",
    "native_x", "native_y", "subject_a", "subject_b", "from_tile_id",
    "to_tile_id", "tile_id", "row_index", "vehicle_id", "base_id",
    "owner", "prototype_id", "home_base_id", "transport_unit_id",
    "order", "order_auto_type",
    "_scope", "_location_refs", "_subject_location_refs", "_completed", "_state",
})


def provider_safe(value: Any) -> Any:
    """Recursively serialize only provider-authorized strategic information.

    This is deliberately idempotent and accepts dataclasses used by the world
    model as well as dictionaries loaded from SQLite.  Private coordinates and
    native row handles remain available internally for topology and identity
    reconciliation, but are impossible to emit accidentally via a nested
    auxiliary result or promoted object.
    """
    if isinstance(value, WorldObject):
        return provider_safe(value.as_dict(provider_safe=False))
    if isinstance(value, EpistemicValue):
        return provider_safe(value.as_dict())
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            lower = key.lower()
            if lower.startswith(("native_", "hidden_", "collector_", "engine_")) \
                    or lower in _PRIVATE_EXACT_KEYS:
                continue
            result[key] = provider_safe(item)
        if result.get("event_kind") in {"contact_moved", "unit_moved"}:
            endpoints = [result.get("from_location_ref"), result.get("to_location_ref")]
            for segment in result.get("path") or ():
                if isinstance(segment, Mapping):
                    endpoints.extend((segment.get("from_location_ref"), segment.get("to_location_ref")))
            if any(isinstance(ref, str) and re.fullmatch(r"location--[0-9]+", ref) for ref in endpoints):
                # Old journal/cache records remain immutable diagnostic evidence.
                # Their provider projection must not revive a sentinel endpoint
                # as an exact route, including after checkpoint restoration.
                result["reported_event_kind"] = result["event_kind"]
                result["event_kind"] = "movement_observation_incomplete"
                result.pop("path", None)
                for key in ("from_location_ref", "to_location_ref"):
                    ref = result.get(key)
                    if isinstance(ref, str) and re.fullmatch(r"location--[0-9]+", ref):
                        result[key] = None
                result.update(reason="invalid_recorded_movement_endpoint", outcome="not_established",
                              continuous_visibility=False, current_whereabouts="unknown")
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [provider_safe(item) for item in value]
    return value


class WorldContractError(ValueError):
    """Raised when perspective-world input violates the public contract."""


class EpistemicStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    REPORTED = "reported"
    DERIVED = "derived"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class EvidenceSource(str, Enum):
    OWNED_STATE = "owned_state"
    DIRECT_SIGHT = "direct_sight"
    STALE_MAP = "stale_map"
    SURVEY = "survey"
    PACT = "pact"
    INFILTRATION = "infiltration"
    GOVERNOR = "governor"
    PROJECT = "project"
    SATELLITE = "satellite"
    PUBLIC_REPORT = "public_report"
    SCENARIO = "scenario"
    PLAYER_ASSERTION = "player_assertion"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def material_object(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the strategic value of an object without observation bookkeeping.

    Re-observing an unchanged current fact advances its verification turn and
    provenance record, but it is not a new world fact.  Full objects remain in
    the projection/checkpoint integrity hash; this normalized shape is used
    only for material revisions, anchor deltas, and attention classification.
    """
    fields = value.get("fields") if isinstance(value.get("fields"), Mapping) else {}
    normalized_fields: dict[str, Any] = {}
    for name, raw in sorted(fields.items()):
        # For a currently observed object, ``last_seen_turn`` is observation
        # freshness bookkeeping: it advances merely because the collector saw
        # the same object again.  If the object later leaves visibility its
        # status transition (and the retained stale value) is material, but
        # repeated current sightings must not manufacture world deltas.
        if name == "last_seen_turn" and value.get("status", "active") == "active":
            continue
        if not isinstance(raw, Mapping):
            normalized_fields[str(name)] = raw
            continue
        normalized_fields[str(name)] = {
            key: raw.get(key) for key in (
                "value", "epistemic_status", "source", "known_bounds",
            ) if key in raw
        }
    metadata = value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}
    return provider_safe({
        "object_ref": value.get("object_ref"), "kind": value.get("kind"),
        "location_ref": value.get("location_ref"), "parent_ref": value.get("parent_ref"),
        "status": value.get("status", "active"), "fields": normalized_fields,
        "metadata": metadata,
    })


def material_hash(value: Mapping[str, Any]) -> str:
    return content_hash(material_object(value))


def require_ref(value: str, field_name: str = "reference") -> str:
    if not isinstance(value, str) or not REF.fullmatch(value):
        raise WorldContractError(f"invalid_{field_name}")
    return value


@dataclass(frozen=True)
class WorldIdentity:
    match_id: str
    perspective_id: str
    timeline_id: str
    world_epoch: str

    def __post_init__(self) -> None:
        for name, value in self.as_dict().items():
            require_ref(value, name)

    def as_dict(self) -> dict[str, str]:
        return {
            "match_id": self.match_id,
            "perspective_id": self.perspective_id,
            "timeline_id": self.timeline_id,
            "world_epoch": self.world_epoch,
        }


@dataclass(frozen=True)
class EpistemicValue:
    value: Any
    status: EpistemicStatus
    source: EvidenceSource
    first_known_turn: int | None
    last_verified_turn: int | None
    world_revision: int
    provenance_ref: str
    known_bounds: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        require_ref(self.provenance_ref, "provenance_ref")
        if self.world_revision < 0:
            raise WorldContractError("invalid_world_revision")
        if self.status is EpistemicStatus.UNKNOWN and self.value is not None:
            raise WorldContractError("unknown_value_must_be_null")
        if self.status is EpistemicStatus.REPORTED \
                and self.source is not EvidenceSource.PLAYER_ASSERTION:
            raise WorldContractError("reported_value_requires_player_assertion")
        if self.source is EvidenceSource.PLAYER_ASSERTION \
                and self.status is not EpistemicStatus.REPORTED:
            raise WorldContractError("player_assertion_requires_reported_status")

    def as_dict(self) -> dict[str, Any]:
        result = {
            "value": self.value,
            "epistemic_status": self.status.value,
            "source": self.source.value,
            "first_known_turn": self.first_known_turn,
            "last_verified_turn": self.last_verified_turn,
            "world_revision": self.world_revision,
            "provenance_ref": self.provenance_ref,
        }
        if self.known_bounds is not None:
            result["known_bounds"] = dict(self.known_bounds)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EpistemicValue":
        return cls(
            value=value.get("value"),
            status=EpistemicStatus(str(value.get("epistemic_status"))),
            source=EvidenceSource(str(value.get("source"))),
            first_known_turn=value.get("first_known_turn"),
            last_verified_turn=value.get("last_verified_turn"),
            world_revision=int(value.get("world_revision", 0)),
            provenance_ref=str(value.get("provenance_ref")),
            known_bounds=value.get("known_bounds"),
        )


@dataclass
class WorldObject:
    object_ref: str
    kind: str
    fields: dict[str, EpistemicValue] = field(default_factory=dict)
    location_ref: str | None = None
    parent_ref: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_ref(self.object_ref, "object_ref")
        require_ref(self.kind, "object_kind")
        if self.location_ref is not None:
            require_ref(self.location_ref, "location_ref")
        if self.parent_ref is not None:
            require_ref(self.parent_ref, "parent_ref")

    def as_dict(self, *, provider_safe: bool = True) -> dict[str, Any]:
        result = {
            "object_ref": self.object_ref,
            "kind": self.kind,
            "location_ref": self.location_ref,
            "parent_ref": self.parent_ref,
            "status": self.status,
            "fields": {key: value.as_dict() for key, value in sorted(self.fields.items())},
            "metadata": dict(self.metadata),
        }
        return globals()["provider_safe"](result) if provider_safe else result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "WorldObject":
        return cls(
            object_ref=str(value["object_ref"]),
            kind=str(value["kind"]),
            location_ref=value.get("location_ref"),
            parent_ref=value.get("parent_ref"),
            status=str(value.get("status", "active")),
            fields={str(key): EpistemicValue.from_dict(item)
                    for key, item in dict(value.get("fields", {})).items()},
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(frozen=True)
class Observation:
    observation_id: str
    sequence: int
    kind: str
    turn: int | None
    source: EvidenceSource
    payload: Mapping[str, Any]
    observed_unix: float
    continuity: str = "complete"

    def __post_init__(self) -> None:
        require_ref(self.observation_id, "observation_id")
        require_ref(self.kind, "observation_kind")
        if self.sequence < 1:
            raise WorldContractError("invalid_observation_sequence")
        if self.continuity not in {"complete", "incomplete"}:
            raise WorldContractError("invalid_observation_continuity")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "smacx.observation.v1",
            "observation_id": self.observation_id,
            "sequence": self.sequence,
            "kind": self.kind,
            "turn": self.turn,
            "source": self.source.value,
            "payload": dict(self.payload),
            "observed_unix": self.observed_unix,
            "continuity": self.continuity,
        }


@dataclass(frozen=True)
class DependencyStamp:
    timeline_id: str
    world_epoch: str
    world_revision: int
    observation_cursor: int
    dependency_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "timeline_id": self.timeline_id,
            "world_epoch": self.world_epoch,
            "world_revision": self.world_revision,
            "observation_cursor": self.observation_cursor,
            "dependency_hash": self.dependency_hash,
        }
