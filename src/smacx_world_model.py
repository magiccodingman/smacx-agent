"""Perspective projection, fair-play contact identity, semantic LOD, and anchors."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import time
from typing import Any, Iterable, Mapping

from smacx_regions import Region, RegionBuilder, build_theaters
from smacx_topology import KnownSquare, MapShape, MobilityProfile, PerspectiveTopology
from smacx_world_types import (
    EpistemicStatus, EpistemicValue, EvidenceSource, Observation, WorldContractError,
    WorldIdentity, WorldObject, canonical_json, content_hash, material_hash,
    provider_safe,
)


WORLD_MODEL_VERSION = "smacx.world-model.v1"
CALCULATOR_VERSION = "smacx.calculators.v1"

ENTITLEMENT_EVIDENCE_SOURCES = {
    "unity_survey": EvidenceSource.SURVEY,
    "pact_shared": EvidenceSource.PACT,
    "infiltration": EvidenceSource.INFILTRATION,
    "governor": EvidenceSource.GOVERNOR,
    "project_intelligence": EvidenceSource.PROJECT,
    "satellite_report": EvidenceSource.SATELLITE,
    "scenario": EvidenceSource.SCENARIO,
    "player_report": EvidenceSource.PLAYER_ASSERTION,
    "public_report": EvidenceSource.PUBLIC_REPORT,
}


def estimate_tokens(value: Any) -> int:
    return max(1, (len(canonical_json(value).encode("utf-8")) + 3) // 4)


def location_ref(tile_id: int) -> str:
    if not isinstance(tile_id, int) or tile_id < 0:
        raise WorldContractError("invalid_tile_id")
    return f"location-{tile_id}"


@dataclass
class ForeignContactState:
    contact_ref: str
    native_observation_key: str
    last_seen_turn: int | None
    last_location_ref: str
    active: bool = True


class ForeignContactRegistry:
    """Keep identity only through continuously observable contact."""

    def __init__(self, prior: Iterable[ForeignContactState] = (), *, namespace: str = "") -> None:
        self.states = {item.native_observation_key: item for item in prior if item.active}
        self.retired: list[ForeignContactState] = []
        self.namespace = namespace

    def begin_frame(self) -> None:
        self._seen: set[str] = set()

    def observe(self, native_observation_key: str, location: str,
                turn: int | None, *, creation_revision: int = 0,
                continuous_path: Iterable[Mapping[str, Any]] = ()) -> str:
        # This key is collector-private and is never serialized provider-side.
        state = self.states.get(native_observation_key)
        # A reconciliation frame proves presence, not an unseen movement path.
        # Without a native coalesced continuously-visible move event, changing
        # locations cannot safely preserve a foreign mobile identity.
        if state is not None and state.last_location_ref != location:
            cursor = state.last_location_ref
            proven = False
            for step in continuous_path:
                if not isinstance(step, Mapping) or str(step.get("from") or "") != cursor:
                    break
                cursor = str(step.get("to") or "")
                if cursor == location:
                    proven = True
                    break
            if not proven:
                self.states.pop(native_observation_key, None)
                state.active = False
                self.retired.append(state)
                state = None
        if state is None:
            digest = hashlib.sha256(
                f"{self.namespace}\x1f{native_observation_key}\x1f{creation_revision}\x1f{location}"
                .encode("utf-8")
            ).hexdigest()[:32]
            state = ForeignContactState(
                "contact-" + digest, native_observation_key, turn, location,
            )
            self.states[native_observation_key] = state
        state.last_seen_turn = turn
        state.last_location_ref = location
        self._seen.add(native_observation_key)
        return state.contact_ref

    def end_frame(self) -> list[ForeignContactState]:
        disappeared = list(self.retired)
        self.retired.clear()
        for key in tuple(self.states):
            if key in self._seen:
                continue
            state = self.states.pop(key)
            state.active = False
            self.retired.append(state)
            disappeared.append(state)
        return disappeared


def _evidence(
    value: Any, *, current: bool, owned: bool, turn: int | None,
    world_revision: int, provenance_ref: str,
) -> EpistemicValue:
    if owned:
        status, source = EpistemicStatus.CURRENT, EvidenceSource.OWNED_STATE
    elif current:
        status, source = EpistemicStatus.CURRENT, EvidenceSource.DIRECT_SIGHT
    else:
        status, source = EpistemicStatus.STALE, EvidenceSource.STALE_MAP
    return EpistemicValue(value, status, source, turn, turn, world_revision, provenance_ref)


class PerspectiveProjector:
    """Normalize a perspective-safe native bundle into a complete known world."""

    def __init__(self, identity: WorldIdentity, *, prior_projection: Mapping[str, Any] | None = None) -> None:
        self.identity = identity
        self.prior_projection = prior_projection or {}
        self._prior_objects = {
            str(item.get("object_ref")): item
            for item in self.prior_projection.get("objects", ())
            if isinstance(item, Mapping) and item.get("object_ref")
        }
        prior_contacts = []
        for item in self.prior_projection.get("objects", ()):
            if not isinstance(item, Mapping) or item.get("kind") != "foreign_contact":
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
            native_key = metadata.get("native_observation_key")
            if not native_key or item.get("status") != "active" or not item.get("location_ref"):
                continue
            last_seen = item.get("fields", {}).get("last_seen_turn", {})
            prior_contacts.append(ForeignContactState(
                str(item["object_ref"]), str(native_key),
                last_seen.get("value") if isinstance(last_seen, Mapping) else None,
                str(item["location_ref"]), True,
            ))
        self.contacts = ForeignContactRegistry(
            prior_contacts,
            namespace=canonical_json({
                "match_id": identity.match_id,
                "perspective_id": identity.perspective_id,
                "world_epoch": identity.world_epoch,
            }),
        )

    def _stabilize_evidence(self, objects: Iterable[WorldObject]) -> list[WorldObject]:
        """Preserve evidence identity when the observed fact did not change.

        Native reconciliation may run repeatedly at one action revision.  A new
        observation/provenance identifier is not itself a world change and must
        not invalidate anchors, operations, or query caches.  A turn boundary,
        epistemic transition, source change, bound change, or value change is a
        real dependency change and receives the proposed new evidence stamp.
        """
        stabilized: list[WorldObject] = []
        for item in objects:
            prior = self._prior_objects.get(item.object_ref)
            prior_fields = prior.get("fields", {}) if isinstance(prior, Mapping) else {}
            fields: dict[str, EpistemicValue] = {}
            for name, value in item.fields.items():
                old = prior_fields.get(name) if isinstance(prior_fields, Mapping) else None
                if isinstance(old, Mapping) and (
                    old.get("value") == value.value
                    and old.get("epistemic_status") == value.status.value
                    and old.get("source") == value.source.value
                    and old.get("known_bounds") == value.known_bounds
                ):
                    # Reverification advances freshness while retaining when the
                    # fact was first learned.  Bookkeeping alone is not a
                    # material world change.
                    fields[name] = EpistemicValue.from_dict(old) \
                        if old.get("last_verified_turn") == value.last_verified_turn \
                        else EpistemicValue(
                            value.value, value.status, value.source,
                            old.get("first_known_turn"), value.last_verified_turn,
                            value.world_revision, value.provenance_ref,
                            value.known_bounds,
                        )
                else:
                    fields[name] = value
            stabilized.append(WorldObject(
                item.object_ref, item.kind, fields, item.location_ref, item.parent_ref,
                item.status, dict(item.metadata),
            ))
        return stabilized

    def project(self, bundle: Mapping[str, Any], *, observation_sequence: int) -> dict[str, Any]:
        if bundle.get("hidden_state") is not None or bundle.get("spectator_state") is not None:
            raise WorldContractError("forbidden_world_input")
        turn = bundle.get("turn")
        revision_hint = int(self.prior_projection.get("world_revision", 0)) + 1
        provenance = f"observation-{observation_sequence}"
        objects: list[WorldObject] = []
        temporal_events: list[dict[str, Any]] = []
        squares: list[KnownSquare] = []
        relationship_by_faction: dict[str, str] = {}
        for faction in bundle.get("factions", ()):
            if not isinstance(faction, Mapping):
                continue
            faction_ref = str(faction.get("faction_ref") or f"faction-{faction.get('id')}")
            relations = faction.get("relations") if isinstance(faction.get("relations"), Mapping) else {}
            relationship_by_faction[faction_ref] = (
                "allied" if relations.get("pact") is True else
                "hostile" if relations.get("vendetta") is True else
                "neutral" if relations.get("treaty") is True or relations.get("truce") is True else
                "unknown"
            )
        map_data = bundle.get("map") if isinstance(bundle.get("map"), Mapping) else {}
        shape = MapShape(int(map_data.get("width", 2)), int(map_data.get("height", 1)),
                         bool(map_data.get("horizontal_wrap", True)))
        objects.append(WorldObject("world-map", "map_state", {
            "width": _evidence(shape.width, current=True, owned=True, turn=turn,
                               world_revision=revision_hint, provenance_ref=provenance),
            "height": _evidence(shape.height, current=True, owned=True, turn=turn,
                                world_revision=revision_hint, provenance_ref=provenance),
            "horizontal_wrap": _evidence(shape.horizontal_wrap, current=True, owned=True,
                                         turn=turn, world_revision=revision_hint,
                                         provenance_ref=provenance),
        }))
        objects.append(WorldObject("world-turn", "turn_state", {
            "turn": _evidence(turn, current=True, owned=True, turn=turn,
                              world_revision=revision_hint, provenance_ref=provenance),
            "year": _evidence(bundle.get("year"), current=True, owned=True, turn=turn,
                              world_revision=revision_hint, provenance_ref=provenance),
        }))
        for tile in bundle.get("tiles", ()):
            if not isinstance(tile, Mapping):
                continue
            ref = location_ref(int(tile["tile_id"]))
            current = bool(tile.get("visible_now"))
            feature_values = {str(value).lower().replace(" ", "_")
                              for value in tile.get("features", ())}
            rockiness = tile.get("rockiness")
            if rockiness == 2:
                feature_values.add("rocky")
            elif rockiness == 1:
                feature_values.add("rolling")
            features = frozenset(feature_values)
            prior_location = self._prior_objects.get(ref, {})
            prior_terrain = prior_location.get("fields", {}).get("terrain", {}) \
                if isinstance(prior_location, Mapping) else {}
            observed_terrain = tile.get("terrain")
            if observed_terrain is None and current and "is_ocean" in tile:
                observed_terrain = "ocean" if tile.get("is_ocean") else "land"
            if observed_terrain is None and isinstance(prior_terrain, Mapping):
                observed_terrain = prior_terrain.get("value")
            terrain = str(observed_terrain or "unknown")
            square = KnownSquare(ref, int(tile["x"]), int(tile["y"]), terrain,
                                 current, features, str(tile.get("owner_ref"))
                                 if tile.get("owner_ref") else None,
                                 bool(tile.get("hostile_zoc", False)), False,
                                 int(tile["altitude"]) if tile.get("altitude") is not None else None)
            squares.append(square)
            fields = {
                "features": _evidence(sorted(features), current=current, owned=False,
                                      turn=turn, world_revision=revision_hint,
                                      provenance_ref=provenance),
            }
            if observed_terrain is None:
                fields["terrain"] = EpistemicValue(
                    None, EpistemicStatus.UNKNOWN, EvidenceSource.STALE_MAP,
                    None, None, revision_hint, provenance,
                )
            else:
                fields["terrain"] = _evidence(
                    terrain, current=current, owned=False, turn=turn,
                    world_revision=revision_hint, provenance_ref=provenance,
                )
            for name in ("altitude", "rainfall", "temperature", "rockiness", "owner_ref"):
                if name in tile:
                    fields[name] = _evidence(tile[name], current=current, owned=False, turn=turn,
                                             world_revision=revision_hint,
                                             provenance_ref=provenance)
            if not current and isinstance(prior_location, Mapping):
                prior_fields = prior_location.get("fields") \
                    if isinstance(prior_location.get("fields"), Mapping) else {}
                for name, old in prior_fields.items():
                    if name in fields or not isinstance(old, Mapping):
                        continue
                    fields[str(name)] = EpistemicValue(
                        old.get("value"), EpistemicStatus.STALE, EvidenceSource.STALE_MAP,
                        old.get("first_known_turn"), old.get("last_verified_turn"),
                        revision_hint, provenance, old.get("known_bounds"),
                    )
            objects.append(WorldObject(ref, "location", fields, metadata={
                "native_x": square.x, "native_y": square.y,
            }))
        for base in bundle.get("bases", ()):
            if not isinstance(base, Mapping) or "tile_id" not in base:
                continue
            at = location_ref(int(base["tile_id"]))
            owner = str(base.get("owner_ref") or bundle.get("own_faction_ref") or "faction-own")
            ref = str(base.get("base_ref") or f"base-{at}")
            owned = bool(base.get("owned", True))
            fields = {name: _evidence(value, current=bool(base.get("visible_now", owned)),
                                      owned=owned, turn=turn, world_revision=revision_hint,
                                      provenance_ref=provenance)
                      for name, value in base.items()
                      if name not in {"id", "base_ref", "tile_id", "owned", "visible_now"}}
            fields["owner_ref"] = _evidence(owner, current=True, owned=owned, turn=turn,
                                             world_revision=revision_hint,
                                             provenance_ref=provenance)
            objects.append(WorldObject(ref, "base", fields, at,
                                       metadata={"native_id": base.get("id")} if owned else {}))
        # Native vehicle rows compact after destruction.  The feed marks that
        # boundary explicitly so an opaque row key can never be reassigned to
        # a different foreign unit at the same location.
        if bundle.get("_contact_identity_reset"):
            self.contacts = ForeignContactRegistry(namespace=canonical_json({
                "match_id": self.identity.match_id,
                "perspective_id": self.identity.perspective_id,
                "world_epoch": self.identity.world_epoch,
            }))
        self.contacts.begin_frame()
        movement_proofs = bundle.get("_continuous_visible_contact_moves") \
            if isinstance(bundle.get("_continuous_visible_contact_moves"), Mapping) else {}
        for unit in bundle.get("units", ()):
            if not isinstance(unit, Mapping) or "tile_id" not in unit:
                continue
            at = location_ref(int(unit["tile_id"]))
            owned = bool(unit.get("owned"))
            if owned:
                ref = str(unit.get("own_unit_ref") or f"own-unit-{unit.get('id')}")
                kind = "own_unit"
                handle = ref.removeprefix("own-unit-") if ref.startswith("own-unit-") else None
                metadata = {"native_id": unit.get("id"), "native_handle": handle}
            else:
                native_key = str(unit.get("native_observation_key") or unit.get("id"))
                path = movement_proofs.get(native_key, ())
                prior_contact = self.contacts.states.get(native_key)
                ref = self.contacts.observe(
                    native_key, at, turn, creation_revision=revision_hint,
                    continuous_path=path if isinstance(path, Iterable)
                    and not isinstance(path, (str, bytes, Mapping)) else (),
                )
                kind = "foreign_contact"
                metadata = {"native_observation_key": native_key}
                safe_path = [
                    {"from_location_ref": str(step.get("from")),
                     "to_location_ref": str(step.get("to"))}
                    for step in path
                    if isinstance(step, Mapping) and step.get("from") and step.get("to")
                ] if isinstance(path, Iterable) and not isinstance(path, (str, bytes, Mapping)) else []
                if prior_contact is None:
                    temporal_events.append({
                        "event_kind": "contact_appeared", "contact_ref": ref,
                        "location_ref": at, "turn": turn,
                    })
                if safe_path:
                    temporal_events.append({
                        "event_kind": "contact_moved", "contact_ref": ref,
                        "path": safe_path,
                        "from_location_ref": safe_path[0]["from_location_ref"],
                        "to_location_ref": safe_path[-1]["to_location_ref"],
                        "turn": turn,
                    })
            fields = {name: _evidence(value, current=True, owned=owned, turn=turn,
                                      world_revision=revision_hint, provenance_ref=provenance)
                      for name, value in unit.items()
                      if name not in {"id", "native_observation_key", "own_unit_ref",
                                      "tile_id", "owned"}}
            if not owned:
                owner_ref = str(unit.get("owner_ref") or "")
                fields["relationship"] = _evidence(
                    "hostile" if owner_ref == "faction-0" else
                    relationship_by_faction.get(owner_ref, "unknown"),
                    current=True, owned=False, turn=turn,
                    world_revision=revision_hint, provenance_ref=provenance,
                )
            fields["last_seen_turn"] = _evidence(
                turn, current=True, owned=owned, turn=turn,
                world_revision=revision_hint, provenance_ref=provenance,
            )
            objects.append(WorldObject(ref, kind, fields, at, metadata=metadata))
        disappeared = self.contacts.end_frame()
        for contact in disappeared:
            prior = self._prior_objects.get(contact.contact_ref, {})
            prior_fields = prior.get("fields") if isinstance(prior, Mapping) else {}
            stale_fields: dict[str, EpistemicValue] = {}
            if isinstance(prior_fields, Mapping):
                for name, old in prior_fields.items():
                    if not isinstance(old, Mapping):
                        continue
                    stale_fields[str(name)] = EpistemicValue(
                        old.get("value"), EpistemicStatus.STALE, EvidenceSource.STALE_MAP,
                        old.get("first_known_turn"), old.get("last_verified_turn"),
                        revision_hint, provenance, old.get("known_bounds"),
                    )
            stale_fields["last_seen_turn"] = EpistemicValue(
                contact.last_seen_turn, EpistemicStatus.STALE, EvidenceSource.STALE_MAP,
                contact.last_seen_turn, contact.last_seen_turn, revision_hint, provenance,
            )
            objects.append(WorldObject(
                contact.contact_ref, "foreign_contact", stale_fields,
                contact.last_location_ref, status="lost",
            ))
            temporal_events.append({
                "event_kind": "contact_lost", "contact_ref": contact.contact_ref,
                "location_ref": contact.last_location_ref, "turn": turn,
            })
        # Native mod_zoc_move is a non-Pact movement restriction, not a
        # Vendetta-only threat verdict. Treaty/truce/unknown contacts can
        # therefore constrain movement while remaining excluded from hostile
        # threat summaries. Keep those two semantics deliberately separate.
        zoc_unit_locations = {
            item.location_ref for item in objects
            if item.kind == "foreign_contact" and item.status == "active"
            and item.fields.get("relationship") is not None
            and item.fields["relationship"].value != "allied"
            and item.location_ref
        }
        hostile_positions = {
            (square.x, square.y) for square in squares
            if square.location_ref in zoc_unit_locations and not square.ocean
        }
        if hostile_positions:
            squares = [replace(square, hostile_zoc=any(
                neighbor in hostile_positions
                for neighbor in shape.neighbors((square.x, square.y)).values()
            ), blocking_contact_occupied=(square.x, square.y) in hostile_positions)
                       for square in squares]
        # Persist the derived, perspective-legitimate ZOC field on current
        # locations.  Calculators reconstruct topology from stored objects;
        # keeping this only on the transient KnownSquare list made production
        # routes silently ignore ZOC while synthetic fixtures passed.
        zoc_by_ref = {square.location_ref: square.hostile_zoc for square in squares}
        occupied_by_ref = {
            square.location_ref: square.blocking_contact_occupied for square in squares
        }
        rewritten: list[WorldObject] = []
        for item in objects:
            if item.kind != "location":
                rewritten.append(item)
                continue
            current = item.fields.get("terrain") is not None \
                and item.fields["terrain"].status is EpistemicStatus.CURRENT
            fields = dict(item.fields)
            fields["hostile_zoc"] = EpistemicValue(
                bool(zoc_by_ref.get(item.object_ref, False)) if current else None,
                EpistemicStatus.DERIVED if current else EpistemicStatus.UNKNOWN,
                EvidenceSource.DIRECT_SIGHT if current else EvidenceSource.STALE_MAP,
                turn if current else None, turn if current else None,
                revision_hint, provenance,
            )
            fields["blocking_contact_occupied"] = EpistemicValue(
                bool(occupied_by_ref.get(item.object_ref, False)) if current else None,
                EpistemicStatus.DERIVED if current else EpistemicStatus.UNKNOWN,
                EvidenceSource.DIRECT_SIGHT if current else EvidenceSource.STALE_MAP,
                turn if current else None, turn if current else None,
                revision_hint, provenance,
            )
            rewritten.append(WorldObject(
                item.object_ref, item.kind, fields, item.location_ref,
                item.parent_ref, item.status, dict(item.metadata),
            ))
        objects = rewritten
        for faction in bundle.get("factions", ()):
            if not isinstance(faction, Mapping):
                continue
            ref = str(faction.get("faction_ref") or f"faction-{faction.get('id')}")
            owned = bool(faction.get("owned"))
            source = EvidenceSource.OWNED_STATE if owned else EvidenceSource.PUBLIC_REPORT
            channels = faction.get("_entitlement_channels") \
                if isinstance(faction.get("_entitlement_channels"), Mapping) else {}
            fields = {name: EpistemicValue(
                                            value, EpistemicStatus.CURRENT,
                                            ENTITLEMENT_EVIDENCE_SOURCES.get(
                                                str(channels.get(name)), source),
                                            turn, turn, revision_hint, provenance)
                      for name, value in faction.items()
                      if name not in {"id", "faction_ref", "owned", "_entitlement_channels"}}
            fields["is_self"] = _evidence(
                owned, current=True, owned=True, turn=turn,
                world_revision=revision_hint, provenance_ref=provenance,
            )
            objects.append(WorldObject(ref, "faction", fields))
        for item in bundle.get("global", ()):
            if not isinstance(item, Mapping):
                continue
            ref = str(item.get("object_ref") or "global-" + content_hash(item)[:16])
            source = EvidenceSource(str(item.get("source", "public_report")))
            channels = item.get("_entitlement_channels") \
                if isinstance(item.get("_entitlement_channels"), Mapping) else {}
            fields = {name: EpistemicValue(
                                            value, EpistemicStatus.CURRENT,
                                            ENTITLEMENT_EVIDENCE_SOURCES.get(
                                                str(channels.get(name)), source),
                                            turn, turn, revision_hint, provenance)
                      for name, value in item.items()
                      if name not in {"object_ref", "source", "_entitlement_channels"}}
            objects.append(WorldObject(ref, str(item.get("kind") or "global_system"), fields))
        present_refs = {item.object_ref for item in objects}
        current_locations = {square.location_ref for square in squares if square.current}
        for ref, prior in self._prior_objects.items():
            if ref in present_refs or prior.get("kind") != "base" \
                    or prior.get("location_ref") in current_locations:
                continue
            prior_fields = prior.get("fields") if isinstance(prior.get("fields"), Mapping) else {}
            stale_fields = {}
            for name, old in prior_fields.items():
                if not isinstance(old, Mapping):
                    continue
                stale_fields[str(name)] = EpistemicValue(
                    old.get("value"), EpistemicStatus.STALE, EvidenceSource.STALE_MAP,
                    old.get("first_known_turn"), old.get("last_verified_turn"),
                    revision_hint, provenance, old.get("known_bounds"),
                )
            objects.append(WorldObject(
                ref, "base", stale_fields, prior.get("location_ref"),
                prior.get("parent_ref"), "stale", {},
            ))
        objects = self._stabilize_evidence(objects)
        return {
            "schema": WORLD_MODEL_VERSION, "identity": self.identity.as_dict(),
            "turn": turn, "year": bundle.get("year"), "map_shape": shape.__dict__,
            "action_revision": bundle.get("action_revision"),
            "observation_cursor": observation_sequence,
            "continuity": str(bundle.get("continuity", "complete")),
            "objects": objects, "known_squares": squares,
            "temporal_events": temporal_events,
        }


class SemanticLodProjector:
    """Create a bounded strategic anchor whose size follows active complexity."""

    def __init__(self, *, context_tier: str, token_cap: int | None = None) -> None:
        if context_tier not in {"64k", "256k"}:
            raise WorldContractError("invalid_context_tier")
        self.context_tier = context_tier
        self.token_cap = token_cap or (6000 if context_tier == "64k" else 16000)

    @staticmethod
    def _field_value(item: Mapping[str, Any], name: str) -> Any:
        field = item.get("fields", {}).get(name)
        return field.get("value") if isinstance(field, Mapping) else None

    @staticmethod
    def _strategic_summary(item: Mapping[str, Any]) -> dict[str, Any]:
        """Retain peripheral meaning without embedding a deep object dump."""
        kind = str(item.get("kind") or "unknown")
        keys = {
            "base": ("name", "owner_ref", "population", "production_name",
                     "mineral_surplus", "drone_riots", "eco_damage"),
            "faction": ("faction_name", "leader_name", "relations", "alien"),
            "game_settings": ("state",), "scenario_rules": ("state",),
            "economy_state": ("state",), "research_state": ("state",),
            "social_state": ("state",), "council_state": ("state",),
            "victory_state": ("state",), "project": ("name", "owner_ref", "state"),
            "project_state": ("state",), "orbital_state": ("state",),
            "governor_state": ("state",), "ecology_state": ("state",),
            "intelligence_entitlement_state": ("state",),
            "planetary_state": ("state",),
            "project_race_state": ("state",), "movement_rules": ("state",),
            "victory_posture": ("state",),
            "global_event": ("name", "state"), "victory": ("name", "state"),
        }.get(kind, ())
        fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
        selected = {name: fields[name] for name in keys if name in fields}
        if kind == "technology_state" and isinstance(fields.get("technologies"), Mapping):
            technologies = fields["technologies"]
            values = technologies.get("value") if isinstance(technologies, Mapping) else None
            selected["owned_technology_count"] = {
                **{key: technologies.get(key) for key in (
                    "epistemic_status", "source", "last_verified_turn", "provenance_ref")
                   if technologies.get(key) is not None},
                "value": len(values) if isinstance(values, list) else None,
            }
        return {key: item.get(key) for key in
                ("object_ref", "kind", "location_ref", "status")
                if item.get(key) is not None} | {"fields": selected}

    def build(self, projection: Mapping[str, Any], *, previous_regions: Iterable[Region] = (),
              focus_ref: str | None = None, operation_refs: Iterable[str] = (),
              triggered_watch_refs: Iterable[str] = ()) -> dict[str, Any]:
        objects = [provider_safe(item)
                   for item in projection.get("objects", ())]
        squares = list(projection.get("known_squares", ()))
        shape_data = projection["map_shape"]
        topology = PerspectiveTopology(MapShape(**shape_data), squares)
        previous = list(previous_regions)
        region_builder = RegionBuilder()
        regions: list[Region] = []
        aliases: dict[str, str] = {}
        for profile in (
            MobilityProfile("mobility-land-default", "land"),
            MobilityProfile("mobility-sea-default", "sea"),
        ):
            built, profile_aliases = region_builder.build(
                topology, profile,
                (item for item in previous if item.mobility_profile_ref == profile.profile_ref),
                world_revision=int(projection.get("world_revision", 0)),
            )
            regions.extend(built)
            aliases.update(profile_aliases)
        frontiers = region_builder.frontiers(
            topology,
            (item for item in regions if item.mobility_profile_ref == "mobility-land-default"),
        )
        location_to_region = {ref: region.region_ref for region in regions for ref in region.location_refs}
        theaters = build_theaters(objects, location_to_region,
                                  world_revision=int(projection.get("world_revision", 0)))
        pinned = {str(ref) for ref in operation_refs} | {str(ref) for ref in triggered_watch_refs}
        if focus_ref:
            pinned.add(focus_ref)
        counts: dict[str, int] = {}
        for item in objects:
            counts[str(item.get("kind"))] = counts.get(str(item.get("kind")), 0) + 1
        region_rows = []
        for region in regions:
            inhabitants = [item for item in objects
                           if item.get("location_ref") in region.location_refs]
            kind_counts: dict[str, int] = {}
            for item in inhabitants:
                kind = str(item.get("kind") or "unknown")
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
            pinned_in_region = [str(item.get("object_ref")) for item in inhabitants
                                if str(item.get("object_ref")) in pinned]
            hostile_count = sum(1 for item in inhabitants
                                if item.get("kind") == "foreign_contact"
                                and item.get("status") == "active"
                                and self._field_value(item, "relationship") == "hostile")
            region_rows.append({
                **region.as_dict(), "object_counts": kind_counts,
                "base_refs": [str(item["object_ref"]) for item in inhabitants
                              if item.get("kind") == "base"][:12],
                "has_current_foreign_contact": hostile_count > 0,
                "active_foreign_contacts": hostile_count,
                "lod_level": "operational" if hostile_count or pinned_in_region else "region",
                "promoted_by_refs": pinned_in_region[:8],
            })
        strategic_kinds = {
            "base", "faction", "game_settings", "scenario_rules", "economy_state",
            "research_state", "social_state", "council_state", "victory_state",
            "technology_state", "project", "project_state", "orbital_state",
            "project_race_state", "governor_state", "movement_rules",
            "intelligence_entitlement_state",
            "ecology_state", "planetary_state", "victory_posture", "global_event", "victory",
        }
        strategic = [self._strategic_summary(item) for item in objects
                     if item.get("kind") in strategic_kinds]
        strategic.sort(key=lambda item: (str(item.get("kind")), str(item.get("object_ref"))))
        # A semantic mipmap is an invariant, not a best-effort trimming pass.
        # Rank active/pinned regions first and aggregate the quiet tail so a
        # fragmented Huge map cannot grow the provider prefix with tile count.
        region_rows.sort(key=lambda item: (
            0 if item["lod_level"] == "operational" else 1,
            -int(item.get("active_foreign_contacts", 0)),
            -int(item.get("location_count", 0)),
            str(item.get("region_ref")),
        ))
        region_limit = 24 if self.context_tier == "64k" else 48
        omitted_regions = region_rows[region_limit:]
        visible_regions = region_rows[:region_limit]
        theater_limit = 12 if self.context_tier == "64k" else 32
        theaters.sort(key=lambda item: (-item.salience, item.theater_ref))
        frontier_limit = 12 if self.context_tier == "64k" else 24
        strategic_limit = 48 if self.context_tier == "64k" else 128
        active_limit = 24 if self.context_tier == "64k" else 64
        promotion_refs = sorted(pinned)
        anchor = {
            "schema": "smacx.world-anchor.v1",
            "identity": dict(projection["identity"]),
            "world_revision": int(projection.get("world_revision", 0)),
            "observation_cursor": int(projection.get("observation_cursor", 0)),
            "turn": projection.get("turn"), "year": projection.get("year"),
            "coverage": {
                "known_locations": len(squares),
                "current_visible_locations": sum(1 for square in squares if square.current),
                "unknown_not_enumerated": True,
            },
            "object_counts": counts,
            "planet": {
                "known_land_locations": sum(1 for square in squares if not square.ocean),
                "known_ocean_locations": sum(1 for square in squares if square.ocean),
                "land_region_count": sum(1 for region in regions
                                         if region.mobility_profile_ref == "mobility-land-default"),
                "ocean_region_count": sum(1 for region in regions
                                          if region.mobility_profile_ref == "mobility-sea-default"),
                "active_theater_count": len(theaters),
            },
            "regions": visible_regions,
            "region_overflow": {
                "omitted_count": len(omitted_regions),
                "omitted_locations": sum(int(item.get("location_count", 0))
                                         for item in omitted_regions),
                "omitted_active_contacts": sum(int(item.get("active_foreign_contacts", 0))
                                                for item in omitted_regions),
                "query_hint": "Use smac_world area/relation for omitted regional detail.",
            },
            "region_aliases": aliases,
            "frontiers": [item.as_dict() for item in frontiers[:frontier_limit]],
            "active_theaters": [item.as_dict() for item in theaters[:theater_limit]],
            "strategic_objects": strategic[:strategic_limit],
            "active_detail": [item for item in objects
                              if item.get("object_ref") in pinned][:active_limit],
            "lod": {
                "tier": self.context_tier,
                "promotion_refs": promotion_refs,
                "region_limit": region_limit,
                "theater_limit": theater_limit,
                "frontier_limit": frontier_limit,
                "strategic_object_limit": strategic_limit,
                "regions_truncated": bool(omitted_regions),
                "theaters_truncated": len(theaters) > theater_limit,
                "frontiers_truncated": len(frontiers) > frontier_limit,
                "strategic_objects_truncated": len(strategic) > strategic_limit,
                "principle": "Peripheral strategic awareness; use smac_world for deliberate zoom.",
            },
        }
        # Demote least salient detail first. Planet/region summaries are never raw tile lists.
        while anchor["active_detail"] and estimate_tokens(anchor) > self.token_cap:
            anchor["active_detail"].pop()
        while anchor["strategic_objects"] and estimate_tokens(anchor) > self.token_cap:
            anchor["strategic_objects"].pop()
            anchor["lod"]["strategic_objects_truncated"] = True
        if estimate_tokens(anchor) > self.token_cap:
            anchor["regions"] = [{key: value for key, value in item.items()
                                  if key in {"region_ref", "version", "location_count"}}
                                 for item in anchor["regions"]]
        if estimate_tokens(anchor) > self.token_cap:
            raise WorldContractError("world_anchor_budget_exhausted")
        anchor["token_estimate"] = estimate_tokens(anchor)
        anchor["projection_integrity_hash"] = content_hash(provider_safe(anchor))
        anchor["_region_projection"] = regions
        # _region_projection is collector persistence, not provider data.  The
        # caller removes it before storage; all other content is safe here.
        safe_anchor = provider_safe(anchor)
        safe_anchor["_region_projection"] = regions
        return safe_anchor


def net_deltas(previous_objects: Iterable[Mapping[str, Any]],
               current_objects: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    previous = {str(item["object_ref"]): item for item in previous_objects}
    current = {str(item["object_ref"]): item for item in current_objects}
    deltas = []
    for ref in sorted(set(previous) | set(current)):
        before, after = previous.get(ref), current.get(ref)
        if before is None:
            deltas.append({"object_ref": ref, "change": "appeared",
                           "current": provider_safe(after)})
        elif after is None:
            deltas.append({"object_ref": ref, "change": "removed"})
        elif material_hash(before) != material_hash(after):
            deltas.append({"object_ref": ref, "change": "changed",
                           "current": provider_safe(after)})
    return deltas
