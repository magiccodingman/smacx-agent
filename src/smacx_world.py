"""Single provider-facing semantic world facade over separated calculators."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from smacx_mechanics import (
    base_mechanics, connector_analysis, logistics as logistics_projection,
    location_affordances, lost_contact_envelopes, mobility_profile, rendezvous_matrix,
    response_matrix,
)
from smacx_regions import RegionBuilder, build_theaters
from smacx_semantic_map import render_svg
from smacx_store import MemoryScope
from smacx_topology import KnownSquare, MapShape, MobilityProfile, PerspectiveTopology
from smacx_world_model import CALCULATOR_VERSION, SemanticLodProjector, estimate_tokens
from smacx_world_store import WorldStore
from smacx_world_types import (
    WorldContractError, WorldIdentity, content_hash, material_hash, provider_safe,
)


WORLD_MODES = frozenset({
    "overview", "area", "relation", "route", "reachability", "compare",
    "base", "forces", "logistics", "intel", "changes", "global", "render",
})
DETAIL_LIMITS = {"compact": 512, "standard": 2048}


class WorldQueryError(ValueError):
    pass


def _value(item: Mapping[str, Any], name: str, default: Any = None) -> Any:
    fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
    field = fields.get(name)
    return field.get("value", default) if isinstance(field, Mapping) else default


def _public_object(item: Mapping[str, Any], *, include_fields: bool = True) -> dict[str, Any]:
    result = {key: item.get(key) for key in
              ("object_ref", "kind", "location_ref", "parent_ref", "status")
              if item.get(key) is not None}
    if include_fields:
        result["fields"] = item.get("fields", {})
    return provider_safe(result)


class WorldService:
    """Read-only facade. It calculates facts and never chooses strategy."""

    def __init__(self, store: WorldStore, scope: MemoryScope, *, ruleset_hash: str = "smacx") -> None:
        self.store = store
        self.scope = scope
        self.ruleset_hash = ruleset_hash

    def _projection(self) -> tuple[WorldIdentity, dict[str, Any]]:
        timeline = self.store.store.active_timeline_id(self.scope)
        projection = self.store.load(self.scope, timeline)
        if not projection:
            raise WorldQueryError("world_projection_unavailable")
        identity = WorldIdentity(**projection["identity"])
        return identity, projection

    @staticmethod
    def _objects(projection: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        return {str(item["object_ref"]): dict(item) for item in projection.get("objects", ())}

    @staticmethod
    def _topology(projection: Mapping[str, Any]) -> PerspectiveTopology:
        objects = WorldService._objects(projection)
        locations = []
        for item in objects.values():
            if item.get("kind") != "location":
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
            if "native_x" not in metadata or "native_y" not in metadata:
                continue
            locations.append(KnownSquare(
                str(item["object_ref"]), int(metadata["native_x"]), int(metadata["native_y"]),
                str(_value(item, "terrain", "land")),
                str(item.get("fields", {}).get("terrain", {}).get("epistemic_status")) == "current",
                frozenset(str(value) for value in (_value(item, "features", []) or [])),
                _value(item, "owner_ref"), bool(_value(item, "hostile_zoc", False)),
                bool(_value(item, "blocking_contact_occupied", False)),
                int(_value(item, "altitude")) if _value(item, "altitude") is not None else None,
            ))
        shape_data = projection.get("map_shape")
        if not isinstance(shape_data, Mapping):
            map_state = next((item for item in objects.values()
                              if item.get("kind") == "map_state"), None)
            if map_state:
                shape_data = {
                    "width": int(_value(map_state, "width", 2)),
                    "height": int(_value(map_state, "height", 1)),
                    "horizontal_wrap": bool(_value(map_state, "horizontal_wrap", True)),
                }
        if not isinstance(shape_data, Mapping):
            # Older reconstructed projections retain coordinates but not the
            # explicit shape. Derive the smallest valid bounded shape.
            width = max((item.x for item in locations), default=1) + 1
            width += width % 2
            height = max((item.y for item in locations), default=0) + 1
            shape_data = {"width": max(2, width), "height": max(1, height),
                          "horizontal_wrap": True}
        return PerspectiveTopology(MapShape(**shape_data), locations)

    def _dependency_refs(self, mode: str, objects: Mapping[str, Mapping[str, Any]], *,
                         subjects: tuple[str, ...], origin_ref: str, target_ref: str,
                         radius: int,
                         projection: Mapping[str, Any] | None = None) -> tuple[str, ...]:
        subject_refs = set(subjects)
        for ref in subjects:
            location = objects.get(ref, {}).get("location_ref")
            if location:
                subject_refs.add(str(location))
        kinds = {
            "base": {"base"}, "forces": {"own_unit", "foreign_contact"},
            "intel": {"faction", "foreign_contact", "claim"},
            "global": {"global_system", "game_settings", "scenario_rules", "economy_state",
                       "research_state", "social_state", "council_state", "victory_state",
                       "technology_state", "global_event", "project", "project_state",
                       "project_race_state", "orbital_state", "governor_state",
                       "intelligence_entitlement_state",
                       "movement_rules", "ecology_state", "planetary_state",
                       "victory_posture", "victory"},
            "logistics": {"own_unit", "base", "route", "convoy"},
        }.get(mode)
        if kinds is not None:
            refs = {
                ref for ref, item in objects.items()
                if item.get("kind") in kinds and (not subjects or ref in subject_refs)
            }
            # These modes run perspective-wide deterministic auxiliaries even
            # when their primary rows are subject-filtered.  Their cache key
            # must therefore cover every strategic fact the calculation reads.
            if mode == "base":
                refs.update(ref for ref, item in objects.items()
                            if item.get("kind") in {"foreign_contact", "faction", "location"})
            elif mode == "intel":
                refs.update(ref for ref, item in objects.items()
                            if item.get("kind") in {"faction", "location"})
            elif mode == "logistics":
                refs.update(ref for ref, item in objects.items()
                            if item.get("kind") in {
                                "own_unit", "base", "route", "convoy", "location",
                                "mobility_profile", "faction",
                            })
            refs.update(subject_refs)
            return tuple(sorted(refs))
        if mode == "area":
            center = objects.get(origin_ref or (subjects[0] if subjects else ""))
            center_ref = str(center.get("location_ref") if center and center.get("location_ref")
                             else center.get("object_ref") if center else origin_ref)
            topology = self._topology(
                projection or {"objects": list(objects.values())},
            )
            if center_ref not in topology.by_ref:
                return tuple(sorted(value for value in (center_ref,) if value))
            origin = topology.by_ref[center_ref]
            locations = {
                ref for ref, square in topology.by_ref.items()
                if topology.shape.distance((origin.x, origin.y), (square.x, square.y)) <= radius
            }
            return tuple(sorted(
                ref for ref, item in objects.items()
                if ref in locations or str(item.get("location_ref") or "") in locations
            ))
        if mode in {"relation", "route", "reachability", "compare"}:
            # Route answers depend on known topology, not every unrelated base,
            # faction, project, or economic field in the perspective.
            refs = {
                ref for ref, item in objects.items()
                if item.get("kind") in {"location", "mobility_profile", "route", "convoy"}
            }
            refs.update(subjects)
            refs.update(value for value in (origin_ref, target_ref) if value)
            for value in (origin_ref, target_ref, *subjects):
                location = objects.get(value, {}).get("location_ref")
                if location:
                    refs.add(str(location))
            return tuple(sorted(refs))
        # Rendering, change history, and the strategic anchor deliberately
        # carry perspective-wide coverage dependencies.
        return tuple(sorted(objects))

    def anchor(self, *, context_length: int, focus_ref: str | None = None,
               operation_refs: Iterable[str] = (), triggered_watch_refs: Iterable[str] = (),
               token_cap: int | None = None) -> dict[str, Any]:
        identity, projection = self._projection()
        tier = "64k" if int(context_length) < 131072 else "256k"
        current = self.store.current_anchor(self.scope, identity.timeline_id, tier)
        now = {str(item["object_ref"]): item for item in projection["objects"]}
        preliminary_deltas: list[dict[str, Any]] = []
        if current is not None:
            preliminary_baseline = self.store.anchor_baseline(str(current["world_anchor_id"]))
            for ref in sorted(set(preliminary_baseline) | set(now)):
                digest = material_hash(now[ref]) if ref in now else None
                if preliminary_baseline.get(ref) != digest:
                    preliminary_deltas.append({
                        "object_ref": ref,
                        "change": "removed" if ref not in now else
                                  ("appeared" if ref not in preliminary_baseline else "changed"),
                        **({"current": _public_object(now[ref])} if ref in now else {}),
                    })
        promotion_refs = sorted({str(value) for value in (
            *((focus_ref,) if focus_ref else ()), *operation_refs, *triggered_watch_refs,
        ) if value})
        turn = next((_value(item, "turn") for item in projection.get("objects", ())
                     if item.get("kind") == "turn_state"), None)
        effective_token_cap = min(
            6000 if tier == "64k" else 16000,
            max(512, int(token_cap)) if token_cap is not None else
            (6000 if tier == "64k" else 16000),
        )
        regenerate = current is None or current["world_epoch"] != identity.world_epoch \
            or current["payload"].get("turn") != turn \
            or int(current.get("token_estimate") or estimate_tokens(current["payload"])) \
                > effective_token_cap \
            or int(projection["observation_cursor"]) - int(
                current["anchor_observation_cursor"] if current else 0) > 128 \
            or len(preliminary_deltas) > 64 \
            or estimate_tokens(preliminary_deltas) > (1200 if tier == "64k" else 3200) \
            or list(current["payload"].get("lod", {}).get("promotion_refs", ())) != promotion_refs
        if not regenerate:
            # One changed coastline, road/tube, base, faction relationship, or
            # global structure can invalidate strategic regions even when the
            # raw delta chain is small.
            for delta in preliminary_deltas:
                current_item = delta.get("current") if isinstance(delta.get("current"), Mapping) else {}
                if current_item.get("kind") in {
                    "location", "base", "faction", "game_settings", "scenario_rules",
                    "council_state", "victory_state", "global_event", "project",
                    "project_state", "orbital_state", "governor_state", "ecology_state",
                    "intelligence_entitlement_state",
                    "project_race_state", "movement_rules", "victory_posture",
                }:
                    regenerate = True
                    break
        if regenerate:
            location_objects = [item for item in projection["objects"] if item.get("kind") == "location"]
            squares = []
            for item in location_objects:
                metadata = item.get("metadata", {})
                squares.append(KnownSquare(
                    str(item["object_ref"]), int(metadata["native_x"]), int(metadata["native_y"]),
                    str(_value(item, "terrain", "land")),
                    item.get("fields", {}).get("terrain", {}).get("epistemic_status") == "current",
                    frozenset(_value(item, "features", []) or []), _value(item, "owner_ref"),
                    bool(_value(item, "hostile_zoc", False)),
                    bool(_value(item, "blocking_contact_occupied", False)),
                ))
            model_projection = {
                **projection, "known_squares": squares,
                "map_shape": projection.get("map_shape") or self._topology(projection).shape.__dict__,
                "turn": turn,
            }
            previous_regions = [
                *self.store.load_regions(self.scope, identity.timeline_id,
                                         "mobility-land-default"),
                *self.store.load_regions(self.scope, identity.timeline_id,
                                         "mobility-sea-default"),
            ]
            payload = SemanticLodProjector(
                context_tier=tier, token_cap=effective_token_cap,
            ).build(
                model_projection, previous_regions=previous_regions,
                focus_ref=focus_ref, operation_refs=operation_refs,
                triggered_watch_refs=triggered_watch_refs,
            )
            region_projection = payload.pop("_region_projection", [])
            self.store.save_regions(
                self.scope, identity.timeline_id, region_projection,
                int(projection["world_revision"]),
                ("mobility-land-default", "mobility-sea-default"),
            )
            hashes = {str(item["object_ref"]): material_hash(item)
                      for item in projection["objects"]}
            current = self.store.save_anchor(
                self.scope, identity, world_revision=int(projection["world_revision"]),
                observation_cursor=int(projection["observation_cursor"]), context_tier=tier,
                payload=payload, token_estimate=estimate_tokens(payload), object_hashes=hashes,
            )
        baseline = self.store.anchor_baseline(str(current["world_anchor_id"]))
        deltas = []
        for ref in sorted(set(baseline) | set(now)):
            digest = material_hash(now[ref]) if ref in now else None
            if baseline.get(ref) == digest:
                continue
            deltas.append({
                "object_ref": ref,
                "change": "removed" if ref not in now else
                          ("appeared" if ref not in baseline else "changed"),
                **({"current": _public_object(now[ref])} if ref in now else {}),
            })
        return {**current, "net_deltas": deltas,
                "net_deltas_truncated": False}

    def _budget(self, detail: str, context_length: int) -> int:
        if detail == "deep":
            return min(8192, max(512, int(context_length * 0.05)))
        if detail not in DETAIL_LIMITS:
            raise WorldQueryError("invalid_world_detail")
        return DETAIL_LIMITS[detail]

    @staticmethod
    def _seal_token_estimate(result: dict[str, Any]) -> int:
        """Set a self-consistent estimate including its own serialized field."""
        estimate = 0
        for _ in range(4):
            result["result_token_estimate"] = estimate
            updated = estimate_tokens(result)
            if updated == estimate:
                break
            estimate = updated
        result["result_token_estimate"] = estimate
        return estimate

    @staticmethod
    def _trim(result: dict[str, Any], budget: int) -> dict[str, Any]:
        dependency_refs = result.get("dependency_refs")
        if isinstance(dependency_refs, list):
            result["dependency_ref_count"] = len(dependency_refs)
            # The cache binds the complete server-held dependency hash. The
            # provider needs representative/queryable references, not a linear
            # copy of every quiet tile merely to validate that hash.
            while dependency_refs and estimate_tokens(result) > budget:
                dependency_refs.pop()
                result["dependency_refs_truncated"] = True
        body = result.get("items")
        if not isinstance(body, list):
            anchor = result.get("anchor")
            if isinstance(anchor, dict):
                payload = anchor.get("payload")
                while isinstance(payload, dict) and estimate_tokens(result) > budget:
                    reduced = False
                    for field in ("active_detail", "frontiers", "active_theaters",
                                  "strategic_objects", "regions"):
                        values = payload.get(field)
                        if isinstance(values, list) and values:
                            values.pop()
                            result["truncated"] = True
                            reduced = True
                            break
                    deltas = anchor.get("net_deltas")
                    if not reduced and isinstance(deltas, list) and deltas:
                        deltas.pop()
                        result["truncated"] = True
                        reduced = True
                    if not reduced:
                        break
            # Non-list modes (render/route/relation/overview) still share the
            # same whole-result ceiling and must pass through auxiliary
            # demotion plus the bounded typed-error fallback below.
            body = []
        original_items = len(body)
        while body and estimate_tokens(result) > budget:
            body.pop()
            result["truncated"] = True
        # Auxiliary sections are part of the same contractual ceiling.  Trim
        # them deterministically after primary items; callers can re-query the
        # named mode/subject rather than receiving an oversized side channel.
        for field in ("objects", "lost_contact_envelopes", "connectors",
                      "temporal_events"):
            values = result.get(field)
            while isinstance(values, list) and values and estimate_tokens(result) > budget:
                values.pop()
                result["truncated"] = True
        logistics = result.get("logistics")
        if isinstance(logistics, dict):
            for field in ("transport_route_options", "convoys", "aircraft", "transports"):
                values = logistics.get(field)
                while isinstance(values, list) and values and estimate_tokens(result) > budget:
                    values.pop()
                    result["truncated"] = True
            for field in ("support_details_by_home_base", "support_by_home_base"):
                support = logistics.get(field)
                if not isinstance(support, dict):
                    continue
                original_count = len(support)
                while support and estimate_tokens(result) > budget:
                    support.pop(sorted(support)[-1])
                    result["truncated"] = True
                if len(support) != original_count:
                    logistics[f"{field}_count"] = original_count
        rendering = result.get("rendering")
        if isinstance(rendering, dict) and estimate_tokens(result) > budget:
            result["rendering"] = {
                "kind": rendering.get("kind", "semantic_map"),
                "omitted": "rendering_exceeds_requested_budget",
            }
            result["truncated"] = True
        if estimate_tokens(result) > budget:
            # Compact-detail bookkeeping must not crowd out the semantic body.
            # Integrity remains bound by dependency_hash and identity even
            # when representative refs or explanatory prose are omitted.
            for field in ("epistemic_note", "dependency_refs", "dependency_ref_count",
                          "retention_class"):
                if estimate_tokens(result) <= budget:
                    break
                if field in result:
                    result.pop(field, None)
                    result["truncated"] = True
            valid = result.get("valid_while")
            if isinstance(valid, dict) and estimate_tokens(result) > budget:
                valid.pop("condition", None)
                result["truncated"] = True
        result["result_token_estimate"] = WorldService._seal_token_estimate(result)
        # The estimate field is itself serialized.  Re-apply deterministic
        # metadata compaction after sealing so a result that was exactly at the
        # ceiling before that field was added cannot cross the contract.
        dependency_refs = result.get("dependency_refs")
        while isinstance(dependency_refs, list) and dependency_refs \
                and result["result_token_estimate"] > budget:
            dependency_refs.pop()
            result["dependency_refs_truncated"] = True
            result["result_token_estimate"] = WorldService._seal_token_estimate(result)
        for field in ("epistemic_note", "dependency_refs", "dependency_ref_count",
                      "retention_class"):
            if result["result_token_estimate"] <= budget:
                break
            if field in result:
                result.pop(field, None)
                result["truncated"] = True
                result["result_token_estimate"] = WorldService._seal_token_estimate(result)
        if original_items and not body and result.get("truncated"):
            compact_error = {
                "ok": False, "schema": "smacx.world-result.v1",
                "error": {"code": "single_world_item_exceeds_budget"},
                "mode": result.get("mode"), "declared_token_ceiling": budget,
                "query_hint": "Narrow subject_refs or use deep detail.",
            }
            compact_error["result_token_estimate"] = WorldService._seal_token_estimate(compact_error)
            return compact_error
        if result["result_token_estimate"] > budget:
            # Never return a successful oversized result.  This bounded typed
            # response advances the caller past an individually oversized row.
            compact_error = {
                "ok": False, "error": "world_result_budget_exhausted",
                "mode": result.get("mode"), "declared_token_ceiling": budget,
                "oversized_item_count": original_items,
                "query_hint": "Use compact detail, a narrower subject_ref, or a continuation.",
            }
            compact_error["result_token_estimate"] = WorldService._seal_token_estimate(compact_error)
            return compact_error
        return result

    def query(
        self, *, mode: str, subject_refs: Iterable[str] = (), origin_ref: str = "",
        target_ref: str = "", movement_profile_ref: str = "mobility-land-default",
        radius: int = 3, since_cursor: int = 0, detail: str = "standard",
        continuation: str = "", context_length: int = 65536,
    ) -> dict[str, Any]:
        if mode not in WORLD_MODES:
            raise WorldQueryError("invalid_world_mode")
        if not 65536 <= int(context_length) <= 16_777_216:
            raise WorldQueryError("invalid_world_context_length")
        budget = self._budget(detail, context_length)
        radius = min(max(int(radius), 0), 32)
        subjects = tuple(dict.fromkeys(str(item) for item in subject_refs))[:32]
        identity, projection = self._projection()
        objects = self._objects(projection)
        known_refs = set(objects)
        supplied_refs = [*subjects]
        if mode in {"area", "relation", "route", "reachability", "compare"}:
            supplied_refs.extend(value for value in (origin_ref, target_ref) if value)
        invalid_refs = sorted({ref for ref in supplied_refs if ref not in known_refs})
        if invalid_refs:
            return {
                "ok": False, "schema": "smacx.world-result.v1", "mode": mode,
                "error": {"code": "unknown_or_superseded_world_ref",
                          "refs": invalid_refs[:8]},
                "identity": identity.as_dict(),
                "world_revision": projection["world_revision"],
                "observation_cursor": projection["observation_cursor"],
                "result_token_estimate": estimate_tokens(invalid_refs[:8]) + 48,
            }
        request = {
            "mode": mode, "subject_refs": subjects, "origin_ref": origin_ref,
            "target_ref": target_ref, "movement_profile_ref": movement_profile_ref,
            "radius": radius, "since_cursor": int(since_cursor), "detail": detail,
            "continuation": continuation,
        }
        if continuation and not continuation.startswith("cursor-"):
            raise WorldQueryError("invalid_world_continuation")
        try:
            continuation_offset = int(continuation.removeprefix("cursor-")) if continuation else 0
        except ValueError as exc:
            raise WorldQueryError("invalid_world_continuation") from exc
        if continuation_offset < 0 or continuation_offset > 1_000_000:
            raise WorldQueryError("invalid_world_continuation")
        dependency_refs = self._dependency_refs(
            mode, objects, subjects=subjects, origin_ref=origin_ref,
            target_ref=target_ref, radius=radius, projection=projection,
        )
        dependency_hash = content_hash({
            ref: material_hash(objects[ref]) if ref in objects else None
            for ref in dependency_refs
        })
        fingerprint = content_hash({
            "scope": identity.as_dict(),
            "ruleset_hash": self.ruleset_hash, "calculator_version": CALCULATOR_VERSION,
            "request": request,
        })
        cached = self.store.cached_query(fingerprint, dependency_hash)
        if cached:
            cached["world_revision"] = projection["world_revision"]
            cached["observation_cursor"] = projection["observation_cursor"]
            cached["valid_while"] = {
                "timeline_id": identity.timeline_id, "world_epoch": identity.world_epoch,
                "world_revision": projection["world_revision"],
                "condition": "listed dependency_refs retain dependency_hash",
            }
            cached = self._trim(provider_safe(cached), budget)
            self.store.telemetry("world_query", "cache_hit", 1, scope=self.scope,
                                 timeline_id=identity.timeline_id, dimensions={"mode": mode})
            return provider_safe(cached)
        result: dict[str, Any] = {
            "ok": True, "schema": "smacx.world-result.v1", "mode": mode,
            "identity": identity.as_dict(), "world_revision": projection["world_revision"],
            "observation_cursor": projection["observation_cursor"],
            "continuity": projection["continuity"], "dependency_hash": dependency_hash,
            "dependency_refs": list(dependency_refs)[:64],
            "retention_class": "query_scoped",
            "valid_while": {
                "timeline_id": identity.timeline_id, "world_epoch": identity.world_epoch,
                "world_revision": projection["world_revision"],
                "condition": "listed dependency_refs retain dependency_hash",
            },
            "epistemic_note": "Unknown and stale evidence remain explicit; absence is not negative evidence.",
            "truncated": False,
        }
        topology: PerspectiveTopology | None = None
        if mode == "overview":
            result["anchor"] = self.anchor(context_length=context_length)
        elif mode in {"base", "forces", "intel", "global", "logistics"}:
            kinds = {
                "base": {"base"}, "forces": {"own_unit", "foreign_contact"},
                "intel": {"faction", "foreign_contact", "claim"},
                "global": {"global_system", "game_settings", "scenario_rules", "economy_state",
                           "research_state", "social_state", "council_state", "victory_state",
                           "technology_state", "global_event", "project", "project_state",
                           "project_race_state", "orbital_state", "governor_state",
                           "intelligence_entitlement_state",
                           "movement_rules", "ecology_state", "planetary_state",
                           "victory_posture", "victory"},
                "logistics": {"own_unit", "base", "route", "convoy"},
            }[mode]
            selected = [item for item in objects.values() if item.get("kind") in kinds]
            if subjects:
                selected = [item for item in selected if item.get("object_ref") in subjects]
            if mode == "base":
                topology = self._topology(projection)
                result["items"] = base_mechanics(topology, objects, subjects)
                result["objects"] = [_public_object(item) for item in selected]
            elif mode == "logistics":
                topology = self._topology(projection)
                result["logistics"] = logistics_projection(
                    objects, topology, subjects,
                )
                result["items"] = [_public_object(item) for item in selected]
            elif mode == "intel":
                topology = self._topology(projection)
                turn_state = objects.get("world-turn", {})
                result["lost_contact_envelopes"] = lost_contact_envelopes(
                    topology, objects, current_turn=_value(turn_state, "turn"),
                    subject_refs=subjects,
                )
                result["items"] = [_public_object(item) for item in selected]
            else:
                result["items"] = [_public_object(item) for item in selected]
        elif mode == "area":
            center = objects.get(origin_ref or (subjects[0] if subjects else ""))
            center_ref = str(center.get("location_ref") if center and center.get("location_ref")
                             else center.get("object_ref") if center else origin_ref)
            topology = self._topology(projection)
            if center_ref not in topology.by_ref:
                raise WorldQueryError("unknown_area_origin")
            origin = topology.by_ref[center_ref]
            in_area = {ref for ref, square in topology.by_ref.items()
                       if topology.shape.distance((origin.x, origin.y), (square.x, square.y)) <= radius}
            result["origin_ref"] = center_ref
            result["coverage"] = {"radius": radius, "unknown_not_enumerated": True}
            result["items"] = [_public_object(item) for item in objects.values()
                               if item.get("object_ref") in in_area
                               or item.get("location_ref") in in_area]
        elif mode == "relation":
            topology = self._topology(projection)
            origin_location = objects.get(origin_ref, {}).get("location_ref") or origin_ref
            target_location = objects.get(target_ref, {}).get("location_ref") or target_ref
            if origin_location not in topology.by_ref or target_location not in topology.by_ref:
                raise WorldQueryError("unknown_relation_endpoint")
            a, b = topology.by_ref[origin_location], topology.by_ref[target_location]
            result["relation"] = {
                "origin_ref": origin_ref, "target_ref": target_ref,
                "geometric_distance": topology.shape.distance((a.x, a.y), (b.x, b.y)),
                "bearing": topology.shape.bearing((a.x, a.y), (b.x, b.y)),
            }
            profile = mobility_profile(objects, movement_profile_ref,
                                       subject_ref=origin_ref, topology=topology)
            route = topology.route(origin_location, target_location, profile)
            result["relation"].update({
                "known_world_reachable": route.reachable,
                "eta_turns": route.turns, "movement_cost": route.movement_cost,
                "uncertainty": list(route.uncertainty),
            })
        elif mode in {"route", "reachability", "compare"}:
            topology = self._topology(projection)
            profile = mobility_profile(objects, movement_profile_ref,
                                       subject_ref=origin_ref, topology=topology)
            if mode == "route":
                origin_location = objects.get(origin_ref, {}).get("location_ref") or origin_ref
                target_location = objects.get(target_ref, {}).get("location_ref") or target_ref
                route = topology.route(origin_location, target_location, profile).as_dict()
                route["route_ref"] = "route-" + content_hash({
                    "origin_ref": origin_ref, "target_ref": target_ref,
                    "movement_profile_ref": movement_profile_ref,
                    "path": route["path"], "dependency_hash": route["dependency_hash"],
                })[:24]
                result["route"] = route
            elif mode == "compare":
                if not subjects:
                    raise WorldQueryError("compare_requires_subjects")
                if origin_ref:
                    result["items"] = response_matrix(
                        topology, objects, [origin_ref], subjects, movement_profile_ref,
                    )[0]["responses"]
                    result["connectors"] = connector_analysis(topology, profile)[:24]
                elif target_ref:
                    result["items"] = rendezvous_matrix(
                        topology, objects, subjects, [target_ref], movement_profile_ref,
                    )
                    for item in result["items"]:
                        item["rendezvous_ref"] = "rendezvous-" + content_hash({
                            "participant_refs": subjects,
                            "candidate_ref": item.get("candidate_ref"),
                            "movement_profile_ref": movement_profile_ref,
                            "arrivals": item.get("arrivals"),
                        })[:24]
                else:
                    result["items"] = location_affordances(topology, objects, subjects)
            else:
                start = objects.get(origin_ref, {}).get("location_ref") or origin_ref
                if start not in topology.by_ref:
                    raise WorldQueryError("unknown_reachability_origin")
                reached = topology.reachable_costs(
                    start, profile, max_cost=float(radius * profile.movement_points),
                )
                result["items"] = [{
                    "location_ref": ref, "minimum_movement_cost": cost,
                    "minimum_turns": int((cost + profile.movement_points - 1)
                                         // profile.movement_points),
                } for ref, cost in sorted(reached.items(), key=lambda item: (item[1], item[0]))]
                result["coverage"] = {"known_world_only": True, "unknown_not_traversed": True}
        elif mode == "changes":
            anchor = self.anchor(context_length=context_length)
            result["anchor_identity"] = {
                "world_anchor_id": anchor["world_anchor_id"],
                "world_anchor_revision": anchor["world_anchor_revision"],
                "anchor_observation_cursor": anchor["anchor_observation_cursor"],
            }
            result["items"] = self.store.changes_since(
                self.scope, identity.timeline_id, int(since_cursor), limit=512,
            )
            result["temporal_events"] = self.store.temporal_events_since(
                self.scope, identity.timeline_id, int(since_cursor), limit=256,
            )
        elif mode == "render":
            topology = self._topology(projection)
            point_limit = min(
                {"compact": 96, "standard": 384, "deep": 1000}.get(detail, 384),
                max(1, budget * 4 // 140),
            )
            result["rendering"] = render_svg(topology, objects, max_cells=point_limit)
            while estimate_tokens(result) > budget and point_limit > 1:
                point_limit = max(1, point_limit // 2)
                result["rendering"] = render_svg(
                    topology, objects, max_cells=point_limit,
                )
                result["truncated"] = True
        if isinstance(result.get("items"), list) and continuation_offset:
            result["items"] = result["items"][continuation_offset:]
        available_before_trim = len(result.get("items", [])) \
            if isinstance(result.get("items"), list) else 0
        result["cache"] = {"hit": False, "query_fingerprint": fingerprint}
        result = self._trim(provider_safe(result), budget)
        returned = len(result.get("items", [])) if isinstance(result.get("items"), list) else 0
        if result.get("ok") is False:
            result["continuation"] = None
        elif returned < available_before_trim:
            result["continuation"] = f"cursor-{continuation_offset + returned}"
        else:
            result["continuation"] = None
        token_estimate = int(result["result_token_estimate"])
        if result.get("ok") is not False:
            self.store.put_cached_query(
                self.scope, identity, world_revision=int(projection["world_revision"]),
                observation_cursor=int(projection["observation_cursor"]),
                ruleset_hash=self.ruleset_hash, calculator_version=CALCULATOR_VERSION,
                dependency_hash=dependency_hash, request=request, result=result,
                token_estimate=token_estimate,
            )
        self.store.telemetry("world_query", "result_tokens", token_estimate,
                             scope=self.scope, timeline_id=identity.timeline_id,
                             dimensions={"mode": mode, "detail": detail, "cache": False})
        return provider_safe(result)
