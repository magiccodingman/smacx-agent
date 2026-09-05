"""Single provider-facing semantic world facade over separated calculators."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from smacx_counterfactual import deployment_alternatives, feasible_outputs, parse_scenario

from smacx_mechanics import (
    base_mechanics, connector_analysis, logistics as logistics_projection,
    location_affordances, lost_contact_envelopes, mobility_profile, rendezvous_matrix,
    response_matrix,
)
from smacx_regions import (
    PHYSICAL_LAND_PROFILE, PHYSICAL_OCEAN_PROFILE, RegionBuilder, build_theaters,
)
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
    "base", "forces", "logistics", "intel", "changes", "global", "render", "counterfactual",
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
                str(_value(item, "terrain") or "unknown"),
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

    def _derived_geography(self, projection: Mapping[str, Any], *, persist_regions: bool = True) -> dict[str, Any]:
        """Build the complete server-held geography, independently of provider LOD."""
        identity = WorldIdentity(**projection["identity"])
        # Reproduce every geography ref that the current provider-facing
        # anchors could have issued, including quiet plan/watch/inspection
        # promotion.  The distinction among promotion causes matters in the
        # anchor summary; ref reconstruction only needs the bounded target set.
        promoted: set[str] = set()
        for tier in ("64k", "256k"):
            issued = self.store.current_anchor(self.scope, identity.timeline_id, tier)
            if not issued:
                continue
            promoted.update(map(str, issued.get("payload", {}).get(
                "lod", {}
            ).get("promotion_refs", ())))
        cache_key = content_hash({"identity": identity.as_dict(), "agent": self.scope.agent_id,
                                  "revision": projection["world_revision"], "promoted": sorted(promoted)})
        with self.store.store._spatial_cache_lock:
            cached = self.store.store._geography_cache.get(cache_key)
        if cached is not None and not persist_regions:
            return cached
        topology = self._topology(projection)
        previous = [
            *self.store.load_regions(self.scope, identity.timeline_id, "mobility-land-default"),
            *self.store.load_regions(self.scope, identity.timeline_id, "mobility-sea-default"),
            *self.store.load_regions(self.scope, identity.timeline_id, PHYSICAL_LAND_PROFILE),
            *self.store.load_regions(self.scope, identity.timeline_id, PHYSICAL_OCEAN_PROFILE),
        ]
        payload = SemanticLodProjector(context_tier="256k", token_cap=16000).build(
            {**projection, "known_squares": list(topology.by_ref.values()),
             "map_shape": topology.shape.__dict__},
            previous_regions=previous,
            operation_refs=sorted(promoted), registry_only=True,
        )
        with self.store.store._spatial_cache_lock:
            cache = self.store.store._geography_cache
            cache[cache_key] = payload
            while len(cache) > 2:
                cache.pop(next(iter(cache)))
        if persist_regions:
            self.store.save_regions(self.scope, identity.timeline_id, payload["_region_projection"],
                                    int(projection["world_revision"]), mobility_profiles=(
                                        "mobility-land-default", "mobility-sea-default",
                                        PHYSICAL_LAND_PROFILE, PHYSICAL_OCEAN_PROFILE))
        return payload

    @staticmethod
    def _dependency_hash(mode, objects, subjects, origin_ref, dependency_refs):
        def dependency_digest(ref):
            item = objects.get(ref)
            if item is None:
                return None
            if mode == "base" and subjects and item.get("kind") == "base" and ref not in subjects:
                # Remote base population/production is not read by response mechanics;
                # access/refuelling/gates still are, even outside the queried base.
                item = {**item, "fields": {name: value for name, value in item.get("fields", {}).items()
                        if name in {"owner_ref", "coastal", "is_ocean", "facilities", "psi_gate_ready"}}}
            digest = material_hash(item)
            if mode == "area" and origin_ref not in objects and item.get("kind") == "location":
                return content_hash({"material": digest, "map_verification": {
                    name: value.get("last_verified_turn") for name, value in item.get("fields", {}).items()
                    if name in {"terrain", "altitude", "features", "owner_ref"} and isinstance(value, Mapping)}})
            return digest
        return content_hash({ref: dependency_digest(ref) for ref in dependency_refs})

    def valid_derived_results(self, projection, *, inspections: bool = False):
        """Validate issued route/rendezvous handles using the query's own contract.

        Creation revision is diagnostic, not lifetime authority. Recompute the
        complete dependency set too, so newly appeared relevant objects count.
        """
        identity = WorldIdentity(**projection["identity"])
        self.store.prune_query_cache(self.scope, identity.timeline_id, identity.world_epoch)
        with self.store.store._connect() as connection:
            rows = connection.execute(
                "SELECT request_json,result_json,dependency_hash FROM world_query_cache "
                "WHERE match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=? "
                "AND world_epoch=? AND ruleset_hash=? AND calculator_version=?" + (
                    " AND json_extract(result_json,'$._inspection') IS NOT NULL "
                    "ORDER BY json_extract(result_json,'$._inspection.validated_unix') DESC LIMIT 32"
                    if inspections else ""),
                (self.scope.match_id, self.scope.agent_id, self.scope.perspective_id,
                 identity.timeline_id, identity.world_epoch, self.ruleset_hash, CALCULATOR_VERSION)).fetchall()
        if not rows:
            return []
        base_objects = self._objects(projection)
        results = []
        digests = {}
        for row in rows:
            request = json.loads(row["request_json"])
            result = json.loads(row["result_json"])
            if not inspections and not result.get("route", {}).get("route_ref") and not any(
                    isinstance(item, Mapping) and item.get("rendezvous_ref") for item in result.get("items", ())):
                continue
            if inspections:
                if result.get("ok") is False:
                    continue
                authority_revision = result.get("valid_while", {}).get("action_revision") or request.get("action_revision")
                if authority_revision is not None and authority_revision != projection.get("action_revision"):
                    continue
                if request.get("committed_observation_cursor", projection["observation_cursor"]) != projection["observation_cursor"]:
                    continue
                if request.get("spatial_scope_dependency"):
                    from smacx_spatial_scope import semantic_spatial_registry
                    resolved = semantic_spatial_registry(self.store, self.scope, projection).get(request.get("origin_ref") or (request.get("subject_refs") or [""])[0])
                    if not resolved:
                        continue
                    if "descriptor" not in resolved:
                        resolved = {**resolved, "descriptor": {"kind": resolved["kind"],
                            "source_ref": request.get("origin_ref") or (request.get("subject_refs") or [""])[0],
                            "known_coverage_count": len(resolved["location_refs"]),
                            "coverage_kind": "perspective_known_geometry"}}
                    if content_hash(resolved) != request["spatial_scope_dependency"]:
                        continue
            objects = base_objects
            receipt = request.get("_airdrop_evidence")
            if receipt:
                # Native legality has the stronger action-revision lifetime.
                # Keep its private evidence available to downstream consumers,
                # but never renew it from a dependency-only world cache hit.
                if receipt.get("action_revision") != projection.get("action_revision"):
                    continue
                origin_ref = request.get("origin_ref", "")
                if origin_ref not in objects:
                    continue
                objects = dict(objects)
                origin = dict(objects[origin_ref])
                origin["fields"] = {**origin.get("fields", {}), **receipt["fields"]}
                objects[origin_ref] = origin
            mode = request["mode"]
            subjects = tuple(request.get("subject_refs", ()))
            origin = request.get("origin_ref", "")
            refs = self._dependency_refs(mode, objects, subjects=subjects, origin_ref=origin,
                target_ref=request.get("target_ref", ""), radius=request.get("radius", 0), projection=projection)
            digest_key = (mode, refs) if mode not in {"base", "area"} and not receipt else None
            digest = digests.get(digest_key) if digest_key is not None else None
            if digest is None:
                digest = self._dependency_hash(mode, objects, subjects, origin, refs)
                if digest_key is not None: digests[digest_key] = digest
            if digest == row["dependency_hash"]:
                if inspections:
                    result = {**result, "_inspection_refs": [value for value in (
                        request.get("origin_ref"), request.get("target_ref"),
                        *(request.get("subject_refs") or ())) if value]}
                results.append(result)
        return results

    def recent_inspection_refs(self, projection, *, limit: int = 8):
        # Automatic validation does not renew the time of explicit inspection.
        results = self.valid_derived_results(projection, inspections=True)
        return list(dict.fromkeys(str(ref) for result in results[:max(1, min(limit, 32))]
                                 for ref in result["_inspection_refs"]))[:32]

    def _dependency_refs(self, mode: str, objects: Mapping[str, Mapping[str, Any]], *,
                         subjects: tuple[str, ...], origin_ref: str, target_ref: str,
                         radius: int,
                         projection: Mapping[str, Any] | None = None) -> tuple[str, ...]:
        mechanical_kinds = {"location", "mobility_profile", "route", "convoy", "base",
                            "own_unit", "foreign_contact", "faction", "movement_rules",
                            "repair_rules", "project", "project_state", "game_settings", "map_state",
                            "scenario_rules", "technology_state", "social_state", "world_state"}
        if mode in {"base", "logistics", "relation", "route", "reachability", "compare", "intel"}:
            refs = {ref for ref, item in objects.items() if item.get("kind") in mechanical_kinds}
            if mode == "intel":
                refs.update(ref for ref, item in objects.items() if item.get("kind") in {"turn_state", "claim"})
            if mode == "base" and subjects:
                base_locations = {str(objects.get(ref, {}).get("location_ref")) for ref in subjects}
                for ref in tuple(refs):
                    item = objects.get(ref, {})
                    roles = _value(item, "roles", {})
                    if item.get("kind") == "own_unit" and isinstance(roles, Mapping) and not any(
                        roles.get(name) for name in ("combat", "carrier", "transport")
                    ) and str(item.get("location_ref")) not in base_locations and _value(item, "home_base_ref") not in subjects:
                        refs.remove(ref)
            refs.update(subjects)
            refs.update(value for value in (origin_ref, target_ref) if value)
            return tuple(sorted(refs))
        subject_refs = set(subjects)
        for ref in subjects:
            location = objects.get(ref, {}).get("location_ref")
            if location:
                subject_refs.add(str(location))
        kinds = {
            "forces": {"own_unit", "foreign_contact"},
            "global": {"global_system", "game_settings", "scenario_rules", "economy_state",
                       "research_state", "social_state", "council_state", "victory_state",
                       "technology_state", "global_event", "project", "project_state",
                       "project_race_state", "orbital_state", "governor_state",
                       "intelligence_entitlement_state",
                       "movement_rules", "ecology_state", "planetary_state",
                       "repair_rules",
                       "victory_posture", "victory"},
        }.get(mode)
        if kinds is not None:
            refs = {
                ref for ref, item in objects.items()
                if item.get("kind") in kinds and (not subjects or ref in subject_refs)
            }
            refs.update(subject_refs)
            return tuple(sorted(refs))
        if mode == "area":
            if origin_ref and origin_ref not in objects:
                return tuple(sorted(
                    ref for ref, item in objects.items()
                    if item.get("kind") in mechanical_kinds | {"landmark"}
                ))
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
        # Rendering, change history, and the strategic anchor deliberately
        # carry perspective-wide coverage dependencies.
        return tuple(sorted(objects))

    def anchor(self, *, context_length: int, focus_ref: str | None = None,
               operation_refs: Iterable[str] = (), triggered_watch_refs: Iterable[str] = (),
               active_plan_refs: Iterable[str] = (),
               recent_material_refs: Iterable[str] = (),
               inspection_refs: Iterable[str] = (),
               token_cap: int | None = None,
               captured_projection: tuple | None = None) -> dict[str, Any]:
        identity, projection = captured_projection or self._projection()
        operation_refs = tuple(operation_refs)
        triggered_watch_refs = tuple(triggered_watch_refs)
        active_plan_refs = tuple(active_plan_refs)
        recent_material_refs = tuple(recent_material_refs)
        explicit_inspections = tuple(inspection_refs)
        inspection_refs = explicit_inspections or tuple(self.recent_inspection_refs(projection))
        tier = "64k" if int(context_length) < 131072 else "256k"
        current = self.store.current_anchor(self.scope, identity.timeline_id, tier)
        # A sovereign may pin an issued theater itself. Preserve its known
        # participants as promotion dependencies when the activity becomes quiet;
        # never infer membership from the theater reference's spelling.
        prior_theaters = {str(row.get("theater_ref")): row.get("subject_refs", ())
                          for row in (current or {}).get("payload", {}).get("active_theaters", ())}
        def expand_theaters(refs):
            return tuple(dict.fromkeys(value for ref in refs
                         for value in (ref, *prior_theaters.get(ref, ()))))
        operation_refs = expand_theaters(operation_refs)
        triggered_watch_refs = expand_theaters(triggered_watch_refs)
        active_plan_refs = expand_theaters(active_plan_refs)
        recent_material_refs = expand_theaters(recent_material_refs)
        inspection_refs = expand_theaters(inspection_refs)
        if focus_ref in prior_theaters:
            operation_refs = expand_theaters((*operation_refs, focus_ref))
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
        turn = next((_value(item, "turn") for item in projection.get("objects", ())
                     if item.get("kind") == "turn_state"), None)
        recent_material_refs = tuple(dict.fromkeys((
            *recent_material_refs,
            *self.store.recent_material_refs(
                self.scope, identity.timeline_id,
                int(projection["observation_cursor"]),
                int(turn) if isinstance(turn, int) else None,
            ),
        )))[:64]
        promotion_refs = sorted({str(value) for value in (
            *((focus_ref,) if focus_ref else ()), *operation_refs, *triggered_watch_refs,
            *active_plan_refs, *recent_material_refs, *inspection_refs,
        ) if value})
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
                    "repair_rules",
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
                    str(_value(item, "terrain") or "unknown"),
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
                *self.store.load_regions(self.scope, identity.timeline_id,
                                         PHYSICAL_LAND_PROFILE),
                *self.store.load_regions(self.scope, identity.timeline_id,
                                         PHYSICAL_OCEAN_PROFILE),
            ]
            payload = SemanticLodProjector(
                context_tier=tier, token_cap=effective_token_cap,
            ).build(
                model_projection, previous_regions=previous_regions,
                focus_ref=focus_ref, operation_refs=operation_refs,
                triggered_watch_refs=triggered_watch_refs,
                active_plan_refs=active_plan_refs,
                recent_material_refs=recent_material_refs,
                inspection_refs=inspection_refs,
            )
            region_projection = payload.pop("_region_projection", [])
            self.store.save_regions(
                self.scope, identity.timeline_id, region_projection,
                int(projection["world_revision"]),
                ("mobility-land-default", "mobility-sea-default",
                 PHYSICAL_LAND_PROFILE, PHYSICAL_OCEAN_PROFILE),
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
        # Keep one primary item while demoting auxiliary detail. Otherwise a
        # large multi-base object envelope can evict every small mechanics
        # row and falsely report that no single item fits the page.
        while len(body) > 1 and estimate_tokens(result) > budget:
            body.pop()
            result["truncated"] = True
        if result.get("mode") == "counterfactual" and body:
            row = body[0]
            economy = row.get("counterfactual", {})
            # Keep achievable outputs and their qualifications ahead of the
            # underlying tile list; deep detail can recover the full receipt.
            radius = row.get("known_base_radius", {})
            for values in (radius.get("locations"), economy.get("squares")):
                if isinstance(values, list) and values and estimate_tokens(result) > budget:
                    values.clear()
                    economy["tile_details_truncated"] = True
                    result["truncated"] = True
            for population in economy.get("population_alternatives", ()):
                alternatives = population.get("alternatives", [])
                while len(alternatives) > 1 and estimate_tokens(result) > budget:
                    alternatives.pop()
                    population["alternatives_truncated"] = True
                    result["truncated"] = True
            for field in ("material_facility_unlocks", "material_improvement_changes"):
                for unlock in economy.get(field, ()):
                    samples = unlock.get("sample_deltas", [])
                    while len(samples) > 1 and estimate_tokens(result) > budget:
                        samples.pop()
                        unlock["samples_truncated"] = True
                        result["truncated"] = True
            alternatives = row.get("alternatives", [])
            if alternatives:
                row["alternative_count"] = len(alternatives)
                while len(alternatives) > 1 and estimate_tokens(result) > budget:
                    # Keep explicitly nominated choices ahead of the routine
                    # existing-unit enumeration when one page cannot fit all.
                    index = next((index for index in range(len(alternatives) - 1, -1, -1)
                                  if not alternatives[index].get("choice_ref")), len(alternatives) - 1)
                    alternatives.pop(index)
                    row["alternatives_truncated"] = True
                    result["truncated"] = True
            support = row.get("derived", {}).get("support_by_base", [])
            while support and estimate_tokens(result) > budget:
                support.pop()
                row["derived"]["base_details_truncated"] = True
                result["truncated"] = True
        if result.get("mode") == "base" and body and isinstance(result.get("objects"), list):
            retained = {item.get("base_ref") for item in body if isinstance(item, Mapping)}
            result["objects"] = [item for item in result["objects"] if item.get("object_ref") in retained]
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
            for field in ("damaged_unit_repair_options", "repair_locations", "staging_bases",
                          "transport_route_options", "convoys", "aircraft", "transports"):
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
        if result.get("mode") == "base" and len(body) == 1:
            # A compact base page must still issue a discoverable base ref
            # when many units contribute response/support detail. Keep scalar
            # summaries and qualify omission; deep subject queries recover it.
            row = body[0]
            for field in ("friendly_response", "visible_hostile_response", "supported_unit_refs", "garrison_refs"):
                values = row.get(field) if isinstance(row, dict) else None
                while isinstance(values, list) and values and WorldService._seal_token_estimate(result) > budget:
                    values.pop()
                    row["mechanics_detail_truncated"] = True
                    result["truncated"] = True
        if body and estimate_tokens(result) > budget:
            body.pop()
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
        runtime_airdrop_receipt: Mapping[str, Any] | None = None,
        runtime_base_site_receipts: Mapping[str, Mapping[str, Any]] | None = None,
        scenario_json: str = "",
        runtime_counterfactual_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if mode not in WORLD_MODES:
            raise WorldQueryError("invalid_world_mode")
        if not 65536 <= int(context_length) <= 16_777_216:
            raise WorldQueryError("invalid_world_context_length")
        budget = self._budget(detail, context_length)
        radius = min(max(int(radius), 0), 32)
        subjects = tuple(dict.fromkeys(str(item) for item in subject_refs))[:32]
        scenario = parse_scenario(scenario_json) if mode == "counterfactual" else None
        if scenario_json and scenario is None:
            raise WorldQueryError("scenario_requires_counterfactual_mode")
        if scenario and scenario["kind"] == "site_economy" and not 1 <= len(subjects) <= 4:
            raise WorldQueryError("site_economy_requires_one_to_four_nominated_sites")
        identity, projection = self._projection()
        objects = self._objects(projection)
        spatial_center = None
        area_ref = origin_ref or (subjects[0] if subjects else "")
        if mode == "area" and area_ref not in objects and area_ref not in {"world-geography", "world-map", ""}:
            from smacx_spatial_scope import semantic_spatial_registry
            resolved = semantic_spatial_registry(self.store, self.scope, projection).get(area_ref)
            if resolved and resolved.get("kind") in {"scope", "route", "rendezvous", "region", "frontier", "theater"}:
                spatial_center = resolved
                if "descriptor" not in spatial_center:
                    spatial_center = {**resolved, "descriptor": {"kind": resolved["kind"],
                        "source_ref": area_ref, "known_coverage_count": len(resolved["location_refs"]),
                        "coverage_kind": "perspective_known_geometry"}}
            elif area_ref.startswith("watch-"):
                raise WorldQueryError("scope_not_current_or_unknown")
        airdrop_evidence = None
        if runtime_airdrop_receipt and origin_ref in objects:
            receipt_revision = str(runtime_airdrop_receipt.get("action_revision") or "")
            if receipt_revision == str(projection.get("action_revision") or ""):
                origin = dict(objects[origin_ref])
                fields = dict(origin.get("fields") or {})
                template = dict(fields.get("airdrop_ready") or {})
                epistemic = {
                    "epistemic_status": "current", "source": "owned_state",
                    "first_known_turn": template.get("first_known_turn"),
                    "last_verified_turn": template.get("last_verified_turn"),
                    "world_revision": int(projection.get("world_revision", 0)),
                    "provenance_ref": "native-airdrop-receipt",
                }
                targets = [
                    int(item["target_tile_id"])
                    for item in runtime_airdrop_receipt.get("targets", ())
                    if isinstance(item, Mapping)
                    and isinstance(item.get("target_tile_id"), int)
                ]
                fields["airdrop_target_tile_ids"] = {**epistemic, "value": targets}
                fields["airdrop_target_count"] = {
                    **epistemic, "value": int(runtime_airdrop_receipt.get("target_count", len(targets))),
                }
                fields["airdrop_targets_truncated"] = {
                    **epistemic, "value": bool(runtime_airdrop_receipt.get("targets_truncated", False)),
                }
                origin["fields"] = fields
                objects[origin_ref] = origin
                airdrop_evidence = {"action_revision": receipt_revision, "fields": {
                    name: {key: value for key, value in fields[name].items() if key != "world_revision"}
                    for name in ("airdrop_target_tile_ids", "airdrop_target_count", "airdrop_targets_truncated")}}
        request = {
            "mode": mode, "subject_refs": subjects, "origin_ref": origin_ref,
            "target_ref": target_ref, "movement_profile_ref": movement_profile_ref,
            "radius": radius, "since_cursor": int(since_cursor), "detail": detail,
            "continuation": continuation,
        }
        if mode == "changes":
            request["committed_observation_cursor"] = int(projection["observation_cursor"])
        if airdrop_evidence:
            request["_airdrop_evidence"] = airdrop_evidence
        if spatial_center:
            request["spatial_scope_dependency"] = content_hash(spatial_center)
        if scenario:
            request["scenario"] = scenario
            request["action_revision"] = projection.get("action_revision")
        if runtime_counterfactual_receipt:
            request["native_counterfactual_receipt_hash"] = content_hash(provider_safe(runtime_counterfactual_receipt))
        if runtime_base_site_receipts:
            # Guarded native founding legality may change without changing a
            # provider-visible square. It is therefore part of the private
            # query-cache key, never an untracked side input.
            request["native_base_site_receipt_hash"] = content_hash(
                provider_safe(runtime_base_site_receipts)
            )
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
        dependency_hash = self._dependency_hash(mode, objects, subjects, origin_ref, dependency_refs)
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
            if scenario or airdrop_evidence or runtime_base_site_receipts or runtime_counterfactual_receipt:
                cached["valid_while"]["action_revision"] = projection.get("action_revision")
                cached["valid_while"]["condition"] += "; native action_revision remains unchanged"
            cached = self._trim(provider_safe(cached), budget)
            if cached.get("ok") is not False:
                self.store.record_inspection(fingerprint, int(projection["world_revision"]), cached.get("valid_while", {}).get("action_revision"))
            self.store.telemetry("world_query", "cache_hit", 1, scope=self.scope,
                                 timeline_id=identity.timeline_id, dimensions={"mode": mode})
            return provider_safe(cached)
        known_refs = set(objects) | ({"world-geography", "world-map"} if mode == "area" else set())
        geography: dict[str, Any] | None = None
        derived_registry: dict[str, dict[str, Any]] = {}
        if mode == "counterfactual" or mode == "relation" or mode == "compare" and not origin_ref and not target_ref or (
            mode == "area" and (origin_ref or (subjects[0] if subjects else "")) not in objects
        ):
            geography = self._derived_geography(projection)
            for row in geography.get("physical_masses", ()):
                if not isinstance(row, Mapping):
                    continue
                ref = row.get("landmass_ref") or row.get("ocean_mass_ref")
                if ref:
                    derived_registry[str(ref)] = dict(row)
            for field, key in (("regions", "region_ref"), ("frontiers", "frontier_ref"),
                               ("active_theaters", "theater_ref"),
                               ("ownership_interfaces", "ownership_interface_ref")):
                for row in geography.get(field, ()):
                    if isinstance(row, Mapping) and row.get(key):
                        derived_registry[str(row[key])] = dict(row)
        if spatial_center:
            derived_registry.setdefault(area_ref, {
                "scope_ref" if spatial_center["kind"] == "scope" else "spatial_ref": area_ref,
                **spatial_center["descriptor"]})
        supplied_refs = [*subjects]
        if mode in {"area", "relation", "route", "reachability", "compare", "counterfactual"}:
            supplied_refs.extend(value for value in (origin_ref, target_ref) if value)
        invalid_refs = sorted({ref for ref in supplied_refs
                               if ref not in known_refs and ref not in derived_registry})
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
        if airdrop_evidence or runtime_base_site_receipts or runtime_counterfactual_receipt:
            result["valid_while"]["action_revision"] = projection.get("action_revision")
            result["valid_while"]["condition"] += "; native action_revision remains unchanged"
        topology: PerspectiveTopology | None = None
        if mode == "counterfactual":
            result["scenario"] = scenario
            if scenario["kind"] in {"social", "terraform", "action", "deployment"}:
                receipt = runtime_counterfactual_receipt or {}
                if receipt.get("ok") is not True or not receipt.get("action_revision") \
                        or receipt.get("action_revision") != projection.get("action_revision") \
                        or receipt.get("kind") != scenario["kind"]:
                    raise WorldQueryError("current_counterfactual_receipt_unavailable")
                result["items"] = deployment_alternatives(self._topology(projection), objects,
                    scenario, target_ref, subjects, receipt.get("alternatives", [])) \
                    if scenario["kind"] == "deployment" else [{key: value for key, value in receipt.items()
                                    if key not in {"ok", "action_revision"}}]
            elif scenario["kind"] == "site_economy":
                topology = self._topology(projection)
                if any(objects.get(ref, {}).get("kind") != "location" for ref in subjects):
                    raise WorldQueryError("site_economy_requires_location_references")
                receipts = {ref: receipt for ref, receipt in (runtime_base_site_receipts or {}).items()
                            if receipt.get("action_revision") == projection.get("action_revision")
                            and receipt.get("action_revision")}
                result["scenario"] = scenario
                regions = geography.get("_region_projection", ()) if geography else ()
                masses = {ref: region.region_ref for region in regions
                          if region.mobility_profile_ref in {PHYSICAL_LAND_PROFILE, PHYSICAL_OCEAN_PROFILE}
                          for ref in region.location_refs}
                mobility: dict[str, list[str]] = {}
                for region in regions:
                    if region.mobility_profile_ref in {"mobility-land-default", "mobility-sea-default"}:
                        for ref in region.location_refs:
                            mobility.setdefault(ref, []).append(region.region_ref)
                result["items"] = location_affordances(topology, objects, subjects, native_receipts=receipts,
                    physical_mass_by_location=masses, mobility_region_by_location=mobility)
                for row in result["items"]:
                    ref = row.get("location_ref")
                    receipt = receipts.get(ref, {})
                    economy = receipt.get("site_economy") or {}
                    row["counterfactual"] = dict(economy) if economy else {"coverage": "current_native_receipt_unavailable"}
                    center = economy.get("center") or {}
                    if center:
                        row["counterfactual"]["population_alternatives"] = [
                            feasible_outputs(economy.get("squares", []),
                                             {**center.get("yields", {}),
                                              "location_ref": center.get("location_ref"),
                                              "epistemic_status": center.get("epistemic_status")}, population)
                            for population in scenario["populations"]]
            else:
                raise WorldQueryError("counterfactual_kind_not_yet_supported")
            result["valid_while"]["action_revision"] = projection.get("action_revision")
            result["valid_while"]["condition"] += "; native action_revision remains unchanged"
        elif mode == "overview":
            result["anchor"] = self.anchor(context_length=context_length, captured_projection=(identity, projection))
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
                           "repair_rules",
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
            derived_center = derived_registry.get(origin_ref or (subjects[0] if subjects else ""))
            center_ref = str(center.get("location_ref") if center and center.get("location_ref")
                             else center.get("object_ref") if center else origin_ref)
            topology = self._topology(projection)
            if center_ref == "world-geography":
                nominated_locations = {str(objects.get(ref, {}).get("location_ref") or ref)
                                       for ref in subjects}
                nominated_regions = {region.region_ref for region in geography.get("_region_projection", ())
                                     if region.location_refs & nominated_locations}
                result["items"] = [
                    {key: value for key, value in row.items() if key in {
                        "landmass_ref", "ocean_mass_ref", "region_ref", "frontier_ref",
                        "theater_ref", "ownership_interface_ref", "anchor_location_ref", "known_location_count",
                        "location_count", "mobility_profile_ref", "owned_base_count",
                        "current_foreign_base_count"}}
                    for ref, row in sorted(derived_registry.items())
                    if not subjects or ref in nominated_regions
                ]
                result["coverage"] = {"registry_complete": True, "membership_server_held": True,
                                      "subject_filter_applied": bool(subjects)}
                center_ref = ""
            if derived_center is not None:
                result["geographic_object"] = derived_center
                boundary_refs = [str(value) for value in derived_center.get("boundary_refs", ())]
                if derived_center.get("frontier_ref"):
                    scouts = []
                    for unit in objects.values():
                        if unit.get("kind") != "own_unit":
                            continue
                        roles = _value(unit, "roles", {})
                        if not isinstance(roles, Mapping) or not (
                            roles.get("scout") or roles.get("explore") or roles.get("combat")
                        ):
                            continue
                        start = str(unit.get("location_ref") or "")
                        if start not in topology.by_ref:
                            continue
                        profile = mobility_profile(
                            objects, "mobility-land-default",
                            subject_ref=str(unit["object_ref"]), topology=topology,
                        )
                        candidates = [
                            (topology.route(start, target, profile), target)
                            for target in boundary_refs[:24] if target in topology.by_ref
                        ]
                        reachable = [(route, target) for route, target in candidates if route.reachable]
                        if not reachable:
                            continue
                        route, target = min(reachable, key=lambda value: (
                            int(value[0].turns if value[0].turns is not None else 10**9), float(value[0].movement_cost if value[0].movement_cost is not None else 10**9),
                            value[1],
                        ))
                        scouts.append({
                            "scout_ref": unit["object_ref"], "frontier_location_ref": target,
                            "arrival_turns": route.turns, "movement_cost": route.movement_cost,
                            "eta_kind": route.eta_kind, "uncertainty": list(route.uncertainty),
                        })
                    scouts.sort(key=lambda row: (
                        int((row.get("arrival_turns") if row.get("arrival_turns") is not None else 10**9)), str(row["scout_ref"]),
                    ))
                    result["frontier_access"] = {
                        "reachable_scouts": scouts[:8],
                        "nearest_scout_arrival_turns": scouts[0]["arrival_turns"] if scouts else None,
                        "known_land_route_available": bool(scouts),
                        "transport_dependency": None if scouts else
                            "possible_or_required; query logistics with a scout and frontier location",
                        "calculation_scope": "lazy_query_only",
                    }
                if spatial_center:
                    membership = set(spatial_center["location_refs"])
                    result["items"] = [_public_object(item) for item in objects.values()
                                       if str(item.get("location_ref") or item.get("object_ref")) in membership]
                    result["coverage"] = {
                        "scope_ref" if spatial_center["kind"] == "scope" else "spatial_ref": area_ref,
                        "validity": "current_dependencies",
                        "membership_server_held": True, **spatial_center["descriptor"]}
                mass_locations: set[str] = set()
                mass_ref = derived_center.get("landmass_ref") or derived_center.get("ocean_mass_ref") or derived_center.get("region_ref")
                if mass_ref:
                    geography_regions = geography.get("_region_projection", ()) \
                        if isinstance(geography, Mapping) else ()
                    for region in geography_regions:
                        if region.region_ref == mass_ref:
                            mass_locations = set(region.location_refs)
                            break
                    result["items"] = [_public_object(item) for item in objects.values()
                                       if str(item.get("location_ref") or item.get("object_ref")) in mass_locations]
                center_ref = ""
            if center_ref == "world-map":
                known = set(topology.by_ref)
                shape = topology.shape
                # This is a bounded opaque address book, not hidden terrain
                # observation. It gives orbital insertion the same ability as
                # the stock map cursor to nominate an unexplored square while
                # revealing no terrain, occupant, base, owner, or legality.
                result["origin_ref"] = center_ref
                result["coverage"] = {
                    "planet_address_space": True,
                    "unmapped_only": True,
                    "hidden_state_disclosed": False,
                    "use": "Exact target nomination; native legality remains guarded.",
                }
                result["items"] = [
                    {"object_ref": f"location-{tile_id}", "kind": "location",
                     "status": "active", "epistemic_status": "unknown"}
                    for tile_id in range(shape.width * shape.height // 2)
                    if f"location-{tile_id}" not in known
                ]
                center_ref = ""
            if not center_ref:
                pass
            elif center_ref not in topology.by_ref:
                raise WorldQueryError("unknown_area_origin")
            else:
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
            origin_location = (objects.get(origin_ref, {}).get("location_ref")
                               or derived_registry.get(origin_ref, {}).get("anchor_location_ref")
                               or origin_ref)
            target_location = (objects.get(target_ref, {}).get("location_ref")
                               or derived_registry.get(target_ref, {}).get("anchor_location_ref")
                               or target_ref)
            if origin_location not in topology.by_ref or target_location not in topology.by_ref:
                raise WorldQueryError("unknown_relation_endpoint")
            a, b = topology.by_ref[origin_location], topology.by_ref[target_location]
            geography_regions = geography.get("_region_projection", ()) \
                if isinstance(geography, Mapping) else ()
            land_by_location = {ref: region.region_ref for region in geography_regions
                                if region.mobility_profile_ref == PHYSICAL_LAND_PROFILE
                                for ref in region.location_refs}
            ocean_by_location = {ref: region.region_ref for region in geography_regions
                                 if region.mobility_profile_ref == PHYSICAL_OCEAN_PROFILE
                                 for ref in region.location_refs}
            result["relation"] = {
                "origin_ref": origin_ref, "target_ref": target_ref,
                "geometric_distance": topology.shape.distance((a.x, a.y), (b.x, b.y)),
                "bearing": topology.shape.bearing((a.x, a.y), (b.x, b.y)),
                "same_known_landmass": bool(
                    land_by_location.get(str(origin_location))
                    and land_by_location.get(str(origin_location))
                    == land_by_location.get(str(target_location))
                ),
                "same_known_ocean_mass": bool(
                    ocean_by_location.get(str(origin_location))
                    and ocean_by_location.get(str(origin_location))
                    == ocean_by_location.get(str(target_location))
                ),
                "origin_physical_mass_ref": land_by_location.get(str(origin_location))
                    or ocean_by_location.get(str(origin_location)),
                "target_physical_mass_ref": land_by_location.get(str(target_location))
                    or ocean_by_location.get(str(target_location)),
            }
            left = result["relation"]["origin_physical_mass_ref"]
            right = result["relation"]["target_physical_mass_ref"]
            possible = bool(left and right and left != right
                            and topology.potential_physical_connection(origin_location, target_location))
            result["relation"]["physical_connectivity"] = {
                "qualification": "same_known_physical_mass" if left and left == right else
                    "distinct_known_components_unknown_connection_possible" if possible else
                    "separation_established_by_known_geography" if left and right else "unknown",
                "unknown_geography_may_connect": possible if left and right else None,
                "epistemic_status": "derived",
                "evidence": "potential connectivity through matching known terrain and unknown cells; opposite terrain blocks",
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
                    geography_regions = geography.get("_region_projection", ()) \
                        if isinstance(geography, Mapping) else ()
                    land_regions = [region for region in geography_regions
                                    if region.mobility_profile_ref == PHYSICAL_LAND_PROFILE]
                    ocean_regions = [region for region in geography_regions
                                     if region.mobility_profile_ref == PHYSICAL_OCEAN_PROFILE]
                    mobility_regions = [region for region in geography_regions
                                        if region.mobility_profile_ref in {
                                            "mobility-land-default", "mobility-sea-default",
                                        }]
                    mass_by_location = {
                        ref: region.region_ref for region in [*land_regions, *ocean_regions]
                        for ref in region.location_refs
                    }
                    mobility_by_location: dict[str, list[str]] = {}
                    for region in mobility_regions:
                        for ref in region.location_refs:
                            mobility_by_location.setdefault(ref, []).append(region.region_ref)
                    result["items"] = location_affordances(
                        topology, objects, subjects,
                        native_receipts=runtime_base_site_receipts,
                        physical_mass_by_location=mass_by_location,
                        mobility_region_by_location=mobility_by_location,
                    )
            else:
                start = objects.get(origin_ref, {}).get("location_ref") or origin_ref
                if start not in topology.by_ref:
                    raise WorldQueryError("unknown_reachability_origin")
                reached = topology.arrival_map(start, profile, max_turns=radius)
                result["items"] = [{
                    "location_ref": ref,
                    "minimum_movement_cost": value.get("movement_cost"),
                    "minimum_turns": value.get("turns"),
                    "eta_kind": value.get("eta_kind"),
                    "latest_turns": value.get("latest_turns"),
                    "uncertainty": value.get("uncertainty", []),
                } for ref, value in sorted(
                    reached.items(), key=lambda item: (
                        int(item[1].get("turns") or 0), item[0]
                    )
                )]
                result["coverage"] = {"known_world_only": True, "unknown_not_traversed": True}
        elif mode == "changes":
            anchor = self.anchor(context_length=context_length, captured_projection=(identity, projection))
            result["anchor_identity"] = {
                "world_anchor_id": anchor["world_anchor_id"],
                "world_anchor_revision": anchor["world_anchor_revision"],
                "anchor_observation_cursor": anchor["anchor_observation_cursor"],
            }
            result["items"] = self.store.changes_since(
                self.scope, identity.timeline_id, int(since_cursor), limit=512, through_cursor=int(projection["observation_cursor"]),
            )
            result["temporal_events"] = self.store.temporal_events_since(
                self.scope, identity.timeline_id, int(since_cursor), limit=256, through_cursor=int(projection["observation_cursor"]),
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
        # Reserve the largest practical continuation representation before the
        # budget seal.  Replacing it with the real cursor (or null) can only
        # shrink the final payload, preserving the trim decision.
        result["continuation"] = (
            "cursor-1000000000000" if isinstance(result.get("items"), list) else None
        )
        result = self._trim(provider_safe(result), budget)
        returned = len(result.get("items", [])) if isinstance(result.get("items"), list) else 0
        if result.get("ok") is False:
            result["continuation"] = None
        elif returned < available_before_trim:
            result["continuation"] = f"cursor-{continuation_offset + returned}"
        else:
            result["continuation"] = None
        # Continuation metadata is part of the provider result and therefore
        # part of the authoritative token/cache/telemetry estimate.
        result["result_token_estimate"] = self._seal_token_estimate(result)
        token_estimate = int(result["result_token_estimate"])
        if token_estimate > budget:
            raise WorldQueryError("world_result_budget_seal_failed")
        if result.get("ok") is not False:
            self.store.put_cached_query(
                self.scope, identity, world_revision=int(projection["world_revision"]),
                observation_cursor=int(projection["observation_cursor"]),
                ruleset_hash=self.ruleset_hash, calculator_version=CALCULATOR_VERSION,
                dependency_hash=dependency_hash, request=request, result=result,
                token_estimate=token_estimate, action_revision=projection.get("action_revision"),
            )
        self.store.telemetry("world_query", "result_tokens", token_estimate,
                             scope=self.scope, timeline_id=identity.timeline_id,
                             dimensions={"mode": mode, "detail": detail, "cache": False})
        return provider_safe(result)
