"""Request-only sovereign runtime-context assembly.

This module deliberately returns data, never provider messages. The Hermes
wire hook owns the trusted terminal insertion boundary so none of this state
can enter durable transcript storage by accident.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Mapping

from smacx_operational_context import operational_context, order_review
from smacx_attention import AttentionService
from smacx_mechanics import base_mechanics
from smacx_store import MemoryScope
from smacx_topology import MapShape
from smacx_world import WorldService
from smacx_world_model import estimate_tokens
from smacx_world_types import canonical_json, content_hash


RUNTIME_CONTEXT_SCHEMA = "smacx.runtime-context.v1"

# One allocator owns the whole request-only envelope.  Component ceilings are
# reservations, not independent promises: their actual use determines the
# space offered to the anchor.  The rich envelope remains bounded at one eighth
# of a 256K context and can still contain a maximal 16K semantic anchor.
RUNTIME_BUDGETS = {
    "64k": {
        "total": 13_107, "anchor": 6_000, "cognition": 2_600,
        "operations": 1_200, "attention": 1_800, "recall": 0,
        "delta_reserve": 512,
    },
    "256k": {
        "total": 32_768, "anchor": 16_000, "cognition": 6_000,
        "operations": 2_500, "attention": 4_000, "recall": 1_000,
        "delta_reserve": 1_500,
    },
}


def _field(item: Mapping[str, Any], name: str, default: Any = None) -> Any:
    value = item.get("fields", {}).get(name) if isinstance(item.get("fields"), Mapping) else None
    return value.get("value", default) if isinstance(value, Mapping) else default


def _force_summary(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Count current owned evidence, never infer assignments from capabilities."""
    roles, orders, production, designs, home_bases = {}, {}, {}, {}, {}
    former_tasks, former_without_task, former_task_unknown = {}, 0, 0
    unknown = {"roles": 0, "orders": 0, "production": 0, "design": 0, "home_base": 0}
    units = 0
    constrained_bases = []
    production_evidence_unknown = 0
    for item in projection.get("objects", ()):
        if item.get("status") != "active":
            continue
        kind = item.get("kind")
        fields = item.get("fields", {})
        def current(name):
            value = fields.get(name, {})
            return value.get("value") if value.get("epistemic_status") == "current" else None
        if kind == "own_unit":
            units += 1
            design = current("name")
            if isinstance(design, str): designs[design] = designs.get(design, 0) + 1
            else: unknown["design"] += 1
            home = current("home_base_ref")
            if isinstance(home, str): home_bases[home] = home_bases.get(home, 0) + 1
            elif fields.get("home_base_ref", {}).get("epistemic_status") == "current":
                home_bases["no_home_base"] = home_bases.get("no_home_base", 0) + 1
            else: unknown["home_base"] += 1
            capabilities = current("roles")
            if isinstance(capabilities, Mapping):
                for role, enabled in capabilities.items():
                    if enabled is True: roles[str(role)] = roles.get(str(role), 0) + 1
            else: unknown["roles"] += 1
            if isinstance(capabilities, Mapping) and capabilities.get("former") is True:
                task = current("terraform_task")
                if isinstance(task, Mapping) and task.get("state") == "active_terraform_order" \
                        and isinstance(task.get("name"), str):
                    name = task["name"]
                    former_tasks[name] = former_tasks.get(name, 0) + 1
                elif isinstance(task, Mapping) and task.get("state") == "no_active_terraform_order":
                    former_without_task += 1
                else:
                    former_task_unknown += 1
            order = current("order_name")
            if isinstance(order, str): orders[order] = orders.get(order, 0) + 1
            else: unknown["orders"] += 1
        elif kind == "base" and fields.get("owner_ref", {}).get("source") == "owned_state":
            name = current("production_name")
            if isinstance(name, str): production[name] = production.get(name, 0) + 1
            else: unknown["production"] += 1
            surplus = current("mineral_surplus")
            cost = current("production_cost")
            accumulated = current("minerals_accumulated")
            if not all(type(value) is int for value in (surplus, cost, accumulated)):
                production_evidence_unknown += 1
            elif cost > 0 and accumulated < cost and surplus <= 0:
                constrained_bases.append({
                    "base_ref": item.get("object_ref"), "production_name": name,
                    "mineral_surplus": surplus, "minerals_accumulated": accumulated,
                    "production_cost": cost, "epistemic_status": "current",
                    "provenance_ref": fields["mineral_surplus"].get("provenance_ref"),
                    "last_verified_turn": fields["mineral_surplus"].get("last_verified_turn"),
                })
    def bounded(values):
        rows = sorted(values.items(), key=lambda row: (-row[1], row[0]))
        return {"counts": dict(rows[:24]), "omitted_categories": len(rows[24:]),
                "omitted_count": sum(count for _, count in rows[24:])}
    return {"scope": "active owned projection objects; current fields only",
            "world_revision": projection.get("world_revision"), "owned_unit_count": units,
            "capability_roles_overlap_not_assignments": bounded(roles),
            "observed_orders": bounded(orders), "current_production": bounded(production),
            "production_constraints": {
                "no_positive_mineral_surplus_count": len(constrained_bases),
                "bases": sorted(constrained_bases, key=lambda item: str(item["base_ref"]))[:8],
                "details_truncated": len(constrained_bases) > 8,
                "missing_or_noncurrent_evidence_count": production_evidence_unknown,
                "meaning": "Incomplete mineral production has no positive passive progress at the observed surplus. Future inputs, hurry and other mineral transfers can change this; completion timing is not predicted.",
                "review": "Use smac_choices kind=base_citizens or production with base_ref to review workforce and production options. Unit focus does not prevent base management; review before the final ready unit ends the turn.",
            },
            "former_tasks": {"active_task_names": bounded(former_tasks),
                             "no_active_terraform_order": former_without_task,
                             "missing_or_noncurrent_task": former_task_unknown,
                             "scope": "current owned Former roles and task evidence; orders do not prove completion or ETA"},
            "unit_names": bounded(designs), "support_home_base_not_defense_assignment": bounded(home_bases),
            "missing_or_noncurrent_fields": unknown}


def _spatial_context(projection: Mapping[str, Any], focus: Mapping[str, Any]) -> dict[str, Any]:
    """Bounded geometry from scoped evidence, never from semantic ID arithmetic."""
    objects = {item["object_ref"]: item for item in projection.get("objects", ())}
    result = {
        "world_revision": projection.get("world_revision"), "relations": [],
        "meaning": "Geometric square distance only: not a route, movement cost, base radius, threat ETA or guaranteed arrival. Stale endpoints describe last observed positions.",
        "query": {"tool": "smac_world", "mode": "relation",
                  "origin_ref": "<unit/contact/base ref>", "target_ref": "<base/location ref>"},
        "route_review": "Use smac_world mode=route with origin_ref set to the owned unit ref and target_ref set to the destination for movement planning; geometric proximity alone does not establish reachability.",
    }
    # Require explicit observed shape. Sparse known squares cannot establish wrap
    # or dimensions, and deriving them would silently turn absence into geometry.
    map_state = next((item for item in objects.values() if item.get("kind") == "map_state"), {})
    fields = map_state.get("fields", {})
    if any(fields.get(key, {}).get("epistemic_status") != "current"
           for key in ("width", "height", "horizontal_wrap")):
        return {**result, "unavailable_reason": "current_map_shape_unavailable"}
    width, height, wrap = (_field(map_state, key) for key in ("width", "height", "horizontal_wrap"))
    if type(width) is not int or type(height) is not int or type(wrap) is not bool \
            or width < 2 or width % 2 or height < 1:
        return {**result, "unavailable_reason": "invalid_observed_map_shape"}
    shape = MapShape(width, height, wrap)

    def endpoint(item):
        if item.get("status") not in {"active", "stale", "lost"}:
            return None
        item_fields = item.get("fields", {})
        evidence = item_fields.get("location_ref", item_fields.get("owner_ref", {}))
        epistemic = evidence.get("epistemic_status")
        if epistemic not in {"current", "stale"}:
            return None
        if item.get("status") in {"stale", "lost"}:
            epistemic = "stale"
        location = objects.get(item.get("location_ref"), {})
        metadata = location.get("metadata", {})
        x, y = metadata.get("native_x"), metadata.get("native_y")
        if location.get("kind") != "location" or type(x) is not int or type(y) is not int:
            return None
        position = shape.normalize((x, y))
        if position is None:
            return None
        return position, {"object_ref": item["object_ref"], "location_ref": item["location_ref"],
                          "epistemic_status": epistemic,
                          "provenance_ref": evidence.get("provenance_ref"),
                          "last_verified_turn": evidence.get("last_verified_turn")}

    bases = [(item, endpoint(item)) for item in objects.values()
             if item.get("kind") == "base" and item.get("status") == "active"
             and item.get("fields", {}).get("owner_ref", {}).get("source") == "owned_state"
             and item.get("fields", {}).get("owner_ref", {}).get("epistemic_status") == "current"]
    bases = [(item, data) for item, data in bases if data is not None]
    focus_ref = focus.get("unit", {}).get("own_unit_ref")
    contacts = sorted((item for item in objects.values()
                       if item.get("kind") == "foreign_contact" and endpoint(item) is not None),
                      key=lambda item: (item.get("status") != "active", item["object_ref"]))
    # Explicit sample, not a claim to enumerate the nearest or most dangerous
    # contacts. Avoid an all-contacts by all-bases product on fragmented Huge maps.
    subjects = ([objects[focus_ref]] if focus_ref in objects else []) + contacts[:4]
    result["contact_sample"] = {"included": min(4, len(contacts)), "omitted": max(0, len(contacts) - 4),
                                "selection": "current before stale, then stable ref; not a threat ranking"}
    for item in subjects:
        origin = endpoint(item)
        if origin is None:
            continue
        nearest = sorted(((shape.distance(origin[0], data[0]), base["object_ref"], data)
                          for base, data in bases), key=lambda row: (row[0], row[1]))[:2]
        for distance, _, target in nearest:
            result["relations"].append({"origin": origin[1], "target": target[1],
                                        "geometric_distance": distance,
                                        "bearing": shape.bearing(origin[0], target[0]),
                                        "epistemic_status": "stale" if "stale" in
                                        (origin[1]["epistemic_status"], target[1]["epistemic_status"]) else "current"})
    result["owned_bases_with_known_geometry"] = len(bases)
    return result


def _nearby_base_defense(world: WorldService, projection: Mapping[str, Any],
                         *, turn: Any, year: Any) -> dict[str, Any]:
    """Surface bounded current defense mechanics when visible combat is near."""
    objects = world._objects(projection)
    topology = world._topology(projection)
    owned_bases = []
    for item in objects.values():
        fields = item.get("fields", {}) if isinstance(item.get("fields"), Mapping) else {}
        owner = fields.get("owner_ref", {})
        square = topology.by_ref.get(str(item.get("location_ref") or ""))
        if item.get("kind") == "base" and item.get("status") == "active" \
                and isinstance(owner, Mapping) and owner.get("source") == "owned_state" \
                and owner.get("epistemic_status") == "current" and square is not None:
            owned_bases.append((str(item["object_ref"]), square))
    contact_squares = []
    for item in objects.values():
        fields = item.get("fields", {}) if isinstance(item.get("fields"), Mapping) else {}
        roles = fields.get("roles", {})
        square = topology.by_ref.get(str(item.get("location_ref") or ""))
        if item.get("kind") == "foreign_contact" and item.get("status") == "active" \
                and isinstance(roles, Mapping) and roles.get("epistemic_status") == "current" \
                and isinstance(roles.get("value"), Mapping) \
                and roles["value"].get("combat") is True and square is not None:
            contact_squares.append((str(item["object_ref"]), square))
    relevant_pairs = sorted(
        (base_ref, contact_ref)
        for base_ref, base_square in owned_bases
        for contact_ref, contact_square in contact_squares
        if topology.shape.distance(
            (base_square.x, base_square.y), (contact_square.x, contact_square.y),
        ) <= 3
    )
    candidate_base_refs = list(dict.fromkeys(base for base, _ in relevant_pairs))
    selected_base_refs = candidate_base_refs[:4]
    selected_contact_refs = {
        contact for base, contact in relevant_pairs if base in selected_base_refs
    }
    scoped_objects = {
        ref: item for ref, item in objects.items()
        if item.get("kind") != "foreign_contact" or ref in selected_contact_refs
    }
    rows = base_mechanics(topology, scoped_objects, selected_base_refs) \
        if selected_base_refs else []
    relevant = []
    for row in rows:
        base = objects.get(str(row.get("base_ref")), {})
        owner = base.get("fields", {}).get("owner_ref", {}) \
            if isinstance(base.get("fields"), Mapping) else {}
        if not isinstance(owner, Mapping) or owner.get("source") != "owned_state" \
                or owner.get("epistemic_status") != "current":
            continue
        nearby = [item for item in row.get("visible_foreign_response", ())
                  if isinstance(item, Mapping)
                  and isinstance(item.get("geometric_distance"), int)
                  and item["geometric_distance"] <= 3]
        if not nearby:
            continue
        relevant.append({
            "base_ref": row.get("base_ref"), "location_ref": row.get("location_ref"),
            "garrison_refs": list(row.get("garrison_refs", ()))[:12],
            "observed_defender_count": row.get("observed_defender_count"),
            "friendly_response": list(row.get("friendly_response", ()))[:8],
            "visible_foreign_response": nearby[:8],
        })
    return {
        "trigger": "current visible foreign combat unit within geometric range 3 of an owned base",
        "bases": relevant[:4], "bases_truncated": len(candidate_base_refs) > 4,
        "shared_era_context": {"turn": turn, "year": year,
                               "meaning": "Shared turn and year establish era context; they do not prove equal resources, research, production, forces, or readiness."},
        "evidence_boundaries": {
            "visible_forces": "lower_bound_only",
            "formal_relationship": "Treaty, Truce, Pact and Vendetta flags are reported separately when current.",
            "movement_zoc": "A foreign non-Pact movement constraint does not prove Vendetta or hostile intent.",
            "inferred_intent": "unknown unless separately supported by observed actions or communication.",
            "defense_strength": "Garrison counts and response ETA do not establish combat odds. Global repair-rule bonuses are not combat defense modifiers.",
        },
    }


def _focus(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    protocol = snapshot.get("protocol") if isinstance(snapshot.get("protocol"), Mapping) else {}
    phase = str(protocol.get("phase") or "unknown")
    interaction = snapshot.get("interaction") \
        if isinstance(snapshot.get("interaction"), Mapping) else {}
    if phase == "interaction":
        material = canonical_json({
            "kind": interaction.get("kind"),
            "label": interaction.get("popup_label"),
            "required_action": protocol.get("required_action"),
            "counterpart_ref": interaction.get("counterpart_faction_ref"),
            "base_ref": interaction.get("base_ref"),
            "unit_ref": interaction.get("own_unit_ref") or interaction.get("contact_ref"),
        })
        return {
            # Action revisions can change for unrelated animation/network ticks.
            # Focus identity changes only when the semantic concern changes.
            "focus_id": "focus-interaction-" + hashlib.sha256(
                material.encode()).hexdigest()[:24],
            "kind": "blocking_interaction", "mandatory": True,
            "label": str(interaction.get("popup_label") or "interaction"),
            "required_action": protocol.get("required_action"),
            "action_revision": snapshot.get("revision"),
        }
    if phase == "wait":
        return {"focus_id": "focus-wait", "kind": "wait", "mandatory": True,
                "interaction_kind": snapshot.get("interaction", {}).get("kind"),
                "required_action": "If waiting_for_turn, or waiting_for_engine with another faction owning the turn, communicate if useful through eligible chat, then yield WAITING. The supervisor wakes you; unchanged local state is expected. Engine processing is distinct from missing capabilities.",
                "action_revision": snapshot.get("revision")}
    ready = snapshot.get("ready_unit_refs") if isinstance(snapshot.get("ready_unit_refs"), list) else []
    if ready:
        unit = ready[0] if isinstance(ready[0], Mapping) else {}
        own_unit_ref = str(unit.get("own_unit_ref") or "unknown")
        return {
            "focus_id": "focus-unit-" + own_unit_ref,
            "kind": "ready_unit", "mandatory": False,
            "other_actions": "smac_choices exposes base/research/strategic management while units are ready, subject to native legality.",
            "unit": {key: unit.get(key) for key in
                     ("own_unit_ref", "name", "location_ref", "roles")
                     if unit.get(key) is not None},
            "ready_count": len(ready), "action_revision": snapshot.get("revision"),
        }
    return {
        "focus_id": "focus-phase-" + phase, "kind": phase,
        "mandatory": phase in {"wait", "capability_gap"},
        "required_action": protocol.get("required_action"),
        "action_revision": snapshot.get("revision"),
    }


def _compact_cognition_record(item: Mapping[str, Any], *, text_limit: int = 1200) -> dict[str, Any]:
    """Bound one routine runtime projection without changing durable truth."""
    result = dict(item)
    for field in ("terms", "description", "objective", "content", "reasons"):
        value = result.get(field)
        if isinstance(value, str) and len(value) > text_limit:
            result[field] = value[:text_limit - 3] + "..."
            result[f"{field}_truncated"] = True
    return result


def cognition_selection_audit(working: Mapping[str, Any], selected: Mapping[str, Any]) -> dict[str, Any]:
    """Explain runtime selection without claiming unobserved journal inventory."""
    sections = working.get("sections", {})
    audit = {}
    for kind in ("goals", "plans", "commitments", "relationships", "beliefs"):
        source = [row for row in sections.get(kind, ()) if isinstance(row, Mapping)]
        included = list(selected.get(kind, ()))
        def identity(row):
            return next((str(row[key]) for key in ("goal_id", "plan_id", "commitment_id",
                "belief_id", "relationship_id", "goal_key", "plan_key", "commitment_key")
                if row.get(key)), hashlib.sha256(canonical_json(_compact_cognition_record(row)).encode()).hexdigest())
        selected_ids = {identity(row) for row in included}
        allowed = {"goals": {"active", "paused"}, "plans": {"proposed", "active", "paused"},
                   "commitments": {"proposed", "accepted"}}.get(kind)
        audit[kind] = {"source_count": len(source), "included_ids": sorted(selected_ids),
            "omitted": [{"id": identity(row), "reason":
                "status_filter" if allowed and str(row.get("status") or ("proposed" if kind == "commitments" else "active")) not in allowed
                else "section_count_or_token_budget"} for row in source if identity(row) not in selected_ids]}
    return {"inventory_scope": "journal_working_set_before_runtime_selection",
            "working_projection": {
                "journal_head_hash": working.get("journal_head_hash"),
                "scope": dict(working.get("scope") or {}),
                "token_budgets": dict(working.get("token_budgets") or {}),
                "source_token_estimates": dict(working.get("source_token_estimates") or {}),
                "selected_token_estimates": dict(working.get("token_estimates") or {}),
            },
            "upstream_working_set_limits_not_reconstructed": True, "sections": audit}


def _cognition(working: Mapping[str, Any], *, token_budget: int,
               current_turn: int | None = None,
               current_year: int | None = None) -> dict[str, Any]:
    """Keep interpretations and intent; mechanical history stays in the world."""
    sections = working.get("sections") if isinstance(working.get("sections"), Mapping) else {}
    situation = sections.get("situation") \
        if isinstance(sections.get("situation"), Mapping) else {}
    def rows(name: str) -> list[dict[str, Any]]:
        value = sections.get(name)
        return [_compact_cognition_record(item) for item in (value or ())
                if isinstance(item, Mapping)]

    goals = [item for item in rows("goals")
             if str(item.get("status") or "active") in {"active", "paused"}]
    goals.sort(key=lambda item: (
        -int(item.get("priority") or 0),
        int(item.get("due_turn")) if item.get("due_turn") is not None else 10**9,
        -float(item.get("created_unix") or item.get("updated_unix") or 0),
    ))
    plans = [item for item in rows("plans")
             if str(item.get("status") or "active") in {"proposed", "active", "paused"}]
    commitments = [item for item in rows("commitments")
                   if str(item.get("status") or "proposed") in {"proposed", "accepted"}]

    def commitment_salience(item: Mapping[str, Any]) -> tuple[Any, ...]:
        status = str(item.get("status") or "proposed")
        due_turn = item.get("due_turn")
        due_year = item.get("due_year")
        if due_turn is not None and current_turn is not None:
            urgency = abs(int(due_turn) - int(current_turn))
        elif due_year is not None and current_year is not None:
            urgency = abs(int(due_year) - int(current_year))
        else:
            urgency = 10**9
        # Accepted promises are binding even when old.  Proposed commitments
        # follow, then urgency and recency break ties.
        return (0 if status == "accepted" else 1, urgency,
                -float(item.get("created_unix") or item.get("updated_unix") or 0))

    commitments.sort(key=commitment_salience)
    result = {
        "evidence_semantics": {
            "authority": "sovereign_interpretation_and_intent_not_current_mechanical_truth",
            "beliefs": "Confidence-scored hypotheses; repetition or persistence does not promote them to observed fact.",
            "plans": "Objectives and confirmation prose express intent. Only explicit structured dependencies are mechanically checked.",
            "completion": "An arrival or accepted order does not prove consumption, completion, or effect. Require an observed effect or qualified uncertainty.",
            "current_state": "Resolve present mechanics from current world, decision, choice, and execution evidence before relying on saved prose.",
        },
        "goals": goals[:12],
        "plans": plans[:12],
        "commitments": commitments[:12],
        "relationships": rows("relationships")[:12],
        "beliefs": rows("beliefs")[:12],
        "summaries": [_compact_cognition_record(item) for item in
                      (situation.get("summaries") or ())
                      if isinstance(item, Mapping)][:6],
    }
    # Durable cognition can be verbose on disk. Runtime carries the most recent
    # bounded interpretation/intent, never an unbounded notebook projection.
    removal_order = ("summaries", "relationships", "beliefs", "goals", "plans")
    while estimate_tokens(result) > token_budget:
        reduced = False
        for section in removal_order:
            values = result[section]
            if len(values) > 1:
                # Journal projections are valuable-first (newest first, or
                # highest-priority first for goals/commitments).  Remove the
                # least valuable tail rather than the head.
                values.pop()
                reduced = True
                break
        if not reduced:
            break
    if estimate_tokens(result) > token_budget:
        raise RuntimeError("context_budget_exhausted:cognition_and_commitments")
    return result


def _operation_context(operations: list[dict[str, Any]], *, token_budget: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in operations:
        compact = dict(item)
        if not compact.get("foreground"):
            compact = {key: compact.get(key) for key in (
                "operation_id", "operation_kind", "objective", "status",
                "linked_plan_id", "linked_goal_id", "last_renewed_turn",
            ) if compact.get(key) is not None}
            if len(str(compact.get("objective") or "")) > 300:
                compact["objective"] = str(compact["objective"])[:297] + "..."
        if estimate_tokens([*result, compact]) > token_budget:
            break
        result.append(compact)
    return result


def _attention_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
    if item.get("attention_kind") == "game_notification":
        # Original capture remains in the journal. Readiness belongs exclusively
        # to current native_protocol, not to the historical popup snapshot.
        historical = {key: value for key, value in (payload.get("state") or {}).items() if key != "protocol"}
        if isinstance(historical.get("faction"), Mapping):
            historical["faction"] = {key: value for key, value in historical["faction"].items() if key != "ready_units"}
        return {"historical_state": historical, "popup_label": payload.get("popup_label"), "turn": payload.get("turn"),
                "information": payload.get("information", []),
                "native_dismissal": "already_dismissed",
                "meaning": "Historical notification, pending cognitive review only. Use current native_protocol for readiness; this is not an active interaction."}
    removal_note = {}
    if item.get("attention_kind") in {"world_change", "world_changes"}:
        changes = [payload.get("delta", {})] if "delta" in payload else payload.get("deltas", ())
        if any(isinstance(delta, Mapping) and delta.get("change") == "removed" for delta in changes):
            removal_note = {"removal_semantics": "Removed from the current projection; this alone does not prove destruction. Query temporal events for confirmed destruction or loss of observation."}
            payload = {**payload, **removal_note}
    if item.get("attention_kind") == "world_change":
        delta = payload.get("delta") if isinstance(payload.get("delta"), Mapping) else {}
        current = delta.get("current") if isinstance(delta.get("current"), Mapping) else {}
        fields = current.get("fields") if isinstance(current.get("fields"), Mapping) else {}
        compact_fields = {
            name: value for name, value in fields.items()
            if name in {"name", "owner_ref", "threatened", "relations", "state",
                        "population", "production_name", "hp", "max_hp"}
        }
        return {**removal_note, "delta": {
            "object_ref": delta.get("object_ref"), "change": delta.get("change"),
            "kind": current.get("kind"), "location_ref": current.get("location_ref"),
            "fields": compact_fields,
        }}
    if estimate_tokens(payload) <= 768:
        return dict(payload)
    if item.get("attention_kind") == "watch_trigger":
        matches = []
        for match in payload.get("matches", ()):
            if not isinstance(match, Mapping):
                continue
            milestone = match.get("milestone")
            if isinstance(milestone, Mapping):
                matches.append({"milestone": {key: milestone.get(key) for key in
                                               ("state", "ready_count", "required_count")}})
            event = match.get("temporal_event")
            if isinstance(event, Mapping):
                matches.append({"temporal_event": {key: event.get(key) for key in
                                                    ("event_kind", "contact_ref", "unit_ref", "base_ref", "item_name", "turn")
                                                    if event.get(key) is not None}})
        return {"watch_id": payload.get("watch_id"), "watch_kind": payload.get("watch_kind"),
                "subject_refs": list(payload.get("subject_refs") or ())[:8],
                "matches": matches[:4], "detail_truncated": True,
                "detail": "Inspect the watch and its referenced world objects for qualified detail."}
    if item.get("attention_kind") == "unit_losses":
        return {key: value for key, value in {
            **payload, "events": list(payload.get("events") or ())[:4],
            "details_truncated": int(payload.get("event_count") or 0) > 4,
        }.items() if key in {"events", "event_count", "details_truncated", "observation_cursor",
                            "evidence_kind", "meaning", "source_journal_event_ids"}}
    if item.get("attention_kind") == "production_progress":
        return {"event_count": payload.get("event_count"), "details_truncated": True,
                "events": [{key: event.get(key) for key in ("event_kind", "base_ref", "item_name", "turn",
                                                                          "meaning", "selection_occurrences",
                                                                          "selection_details_truncated")}
                           for event in list(payload.get("events") or ())[:4]
                           if isinstance(event, Mapping)]}
    return {**removal_note, "payload_hash": content_hash(payload), "keys": sorted(payload)[:24],
            "detail": "Use smac_world/semantic chat recall for bounded detail."}


def _bounded_attention(lease: Mapping[str, Any], *, token_budget: int) -> dict[str, Any]:
    result = {key: lease.get(key) for key in
              ("attention_lease_id", "through_cursor", "status", "reused")
              if lease.get(key) is not None}
    result["items"] = []
    if lease.get("items") and lease.get("acknowledgement"):
        result["acknowledgement"] = lease["acknowledgement"]
    if lease.get("status") == "responded":
        result["status_meaning"] = "A model response occurred; this does not acknowledge these items."
    for raw in lease.get("items", ()):
        if not isinstance(raw, Mapping):
            continue
        item = {key: raw.get(key) for key in (
            "attention_id", "attention_kind", "observation_cursor", "priority",
            "attention_sequence", "critical", "redelivered", "captured_unix",
        ) if raw.get(key) is not None}
        item["payload"] = _attention_payload(raw)
        candidate = {**result, "items": [*result["items"], item]}
        if estimate_tokens(candidate) > token_budget:
            result["truncated"] = True
            result["remaining_count"] = len(lease.get("items", ())) - len(result["items"])
            break
        result["items"].append(item)
    if not result["items"]:
        result.pop("acknowledgement", None)
    return result


class RuntimeContextAssembler:
    def __init__(
        self, *, scope: MemoryScope, world: WorldService, attention: AttentionService,
        snapshot: Callable[[], Mapping[str, Any]],
        working_state: Callable[[], Mapping[str, Any]],
        interpretive_recall: Callable[[str], Mapping[str, Any]] | None = None,
        intent_review: Callable[[int], Mapping[str, Any]] | None = None,
    ) -> None:
        self.scope = scope
        self.world = world
        self.attention = attention
        self.snapshot = snapshot
        self.working_state = working_state
        self.interpretive_recall = interpretive_recall
        self.intent_review = intent_review

    def build(self, *, episode_id: str, episode_mode: str,
              context_length: int, episode_boundary: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if episode_mode not in {"gameplay", "communication", "recovery"}:
            raise ValueError("invalid_episode_mode")
        snapshot = dict(self.snapshot())
        projection_identity, projection = self.world._projection()
        turn_state = next((item for item in projection.get("objects", [])
                           if item.get("kind") == "turn_state"), {})
        turn = _field(turn_state, "turn", snapshot.get("turn"))
        attention_lease = self.attention.lease(episode_id, limit=32, committed_cursor=int(projection["observation_cursor"]))
        dependencies = self.attention.semantic_dependency_hashes(projection)
        active = self.attention.runtime_state(
            current_world_revision=int(projection["world_revision"]),
            current_world_epoch=projection_identity.world_epoch,
            object_dependency_hashes=dependencies,
            current_turn=turn,
        )
        focus = _focus(snapshot)
        handoff = (episode_boundary or {}).get("turn_handoff_required")
        handoff = dict(handoff) if isinstance(handoff, Mapping) and handoff.get("required") is True else None
        if handoff:
            focus = {"focus_id": "focus-episode-handoff", "kind": "turn_handoff",
                     "mandatory": True, "required_action": handoff.get("instruction")}
        operation_refs = [
            str(ref) for operation in active["operations"]
            for ref in operation.get("referenced_world_objects", ())
        ]
        triggered_watch_refs = [
            str(ref)
            for item in attention_lease.get("items", ())
            if isinstance(item, Mapping) and item.get("attention_kind") == "watch_trigger"
            for ref in (item.get("payload", {}).get("subject_refs", ())
                        if isinstance(item.get("payload"), Mapping) else ())
        ]
        for item in attention_lease.get("items", ()):
            if not isinstance(item, Mapping) or item.get("attention_kind") != "watch_trigger":
                continue
            payload = item.get("payload", {})
            if not isinstance(payload, Mapping):
                continue
            for match in payload.get("matches", ()):
                if not isinstance(match, Mapping):
                    continue
                event = match.get("temporal_event", {})
                if isinstance(event, Mapping):
                    triggered_watch_refs.extend(str(event[key]) for key in
                                                ("contact_ref", "unit_ref", "base_ref", "location_ref")
                                                if event.get(key))
                milestone = match.get("milestone", {})
                if isinstance(milestone, Mapping):
                    triggered_watch_refs.extend(str(row["ref"]) for row in milestone.get("requirements", ())
                                                if isinstance(row, Mapping) and row.get("ref"))
        triggered_watch_refs = list(dict.fromkeys(triggered_watch_refs))[:64]
        focus_ref = ""
        focus_unit = focus.get("unit") if isinstance(focus.get("unit"), Mapping) else {}
        if focus_unit.get("own_unit_ref"):
            focus_ref = str(focus_unit["own_unit_ref"])
        tier = "64k" if context_length < 131072 else "256k"
        budgets = RUNTIME_BUDGETS[tier]
        working = self.working_state()
        cognition = _cognition(
            working, token_budget=budgets["cognition"],
            current_turn=int(turn) if turn is not None else None,
            current_year=int(snapshot["year"]) if snapshot.get("year") is not None else None,
        )
        active_plan_refs = [
            str(ref) for plan in cognition.get("plans", ())
            if isinstance(plan, Mapping)
            for ref in [*plan.get("target_refs", ()),
                        *(item.get("ref") for item in plan.get("participants", ())
                          if isinstance(item, Mapping) and item.get("ref"))]
        ][:64]
        operations = _operation_context(
            active["operations"], token_budget=budgets["operations"],
        )
        attention_context = _bounded_attention(
            attention_lease, token_budget=budgets["attention"],
        )
        recent_material_refs: list[str] = []
        for attention_item in attention_context.get("items", ()):
            if not isinstance(attention_item, Mapping) \
                    or attention_item.get("attention_kind") != "world_change":
                continue
            delta = attention_item.get("payload", {}).get("delta", {})
            if not isinstance(delta, Mapping):
                continue
            recent_material_refs.extend(
                str(value) for value in (delta.get("object_ref"), delta.get("location_ref"))
                if value
            )
        recent_material_refs = list(dict.fromkeys(recent_material_refs))[:64]
        recall_context: dict[str, Any] | None = None
        recall_terms = []
        for item in attention_context.get("items", []):
            if item.get("attention_kind") != "chat":
                continue
            message = item.get("payload", {}).get("message", {})
            if isinstance(message, Mapping) and message.get("content"):
                recall_terms.append(str(message["content"]))
        if budgets["recall"] and recall_terms and self.interpretive_recall is not None:
            recalled = self.interpretive_recall("\n".join(recall_terms[-4:]))
            if recalled.get("ok") and isinstance(recalled.get("facts"), list):
                recall_context = {
                    "source": "optional_graphiti_interpretive_recall",
                    "facts": recalled["facts"][:8],
                    "authority": "fallible_history_not_current_mechanical_truth",
                }
        intent_review = dict(self.intent_review(int(turn))) if self.intent_review and turn is not None else {}
        intent_review.pop("resolution_options", None)
        protocol = snapshot.get("protocol") \
            if isinstance(snapshot.get("protocol"), Mapping) else {}
        # Reserve all non-anchor mandatory/current cognition first.  Semantic
        # LOD receives only the remaining coherent envelope budget.
        payload = {
            "schema": RUNTIME_CONTEXT_SCHEMA,
            "episode": {"episode_id": episode_id, "mode": episode_mode,
                        "mutation_authority": episode_mode == "gameplay" and not handoff},
            **({"turn_handoff_required": handoff, "gameplay_mutations_blocked": True} if handoff else {}),
            "identity": {
                **projection_identity.as_dict(),
                "world_revision": int(projection["world_revision"]),
                "observation_cursor": int(projection["observation_cursor"]),
                "action_revision": snapshot.get("revision"),
                "session_id": snapshot.get("session_id"),
                "continuity": projection.get("continuity", "incomplete"),
            },
            "world": {},
            "focus": focus,
            "native_protocol": {
                "source": "current_native_snapshot",
                "phase": protocol.get("phase"),
                "faction_id": (snapshot.get("faction") or {}).get("id"),
                "current_faction_id": ((snapshot.get("interaction") or {}).get("engine_state") or {}).get("current_faction_id"),
                "required_action": protocol.get("required_action"),
                "ready_unit_count": len(snapshot.get("ready_unit_refs", ()))
                    if isinstance(snapshot.get("ready_unit_refs"), list) else 0,
                "end_turn_blocked": protocol.get("end_turn_blocked"),
                "action_revision": snapshot.get("revision"),
                "meaning": "Subject to the current episode handoff fence, this native protocol controls action readiness. Zero ready units does not mean a foreign turn: phase=turn still requires management or a returned End turn choice. WAITING text does not end a native turn. Historical wait notices and previous handoffs do not override this protocol. Projected orders do not prove current readiness.",
            },
            "force_summary": _force_summary(projection),
            "operational_review": operational_context({o["object_ref"]: o for o in projection.get("objects", ())}, limit=4),
            "spatial_context": _spatial_context(projection, focus),
            **({"current_turn_intent_review": intent_review} if intent_review.get("total_pending") else {}),
            "attention": attention_context,
            "working_cognition": cognition,
            "operations": operations,
            "watch_summary": {"active_count": active["active_watch_count"]},
            "plan_health": self.attention.plan_health(
                projection, active["operations"],
                [str(item.get("own_unit_ref")) for item in snapshot.get("ready_unit_refs", [])
                 if isinstance(item, Mapping)], dependencies),
            "generated_unix": time.time(),
        }
        nearby_defense = _nearby_base_defense(
            self.world, projection, turn=turn, year=snapshot.get("year"),
        )
        if nearby_defense["bases"]:
            payload["nearby_base_defense"] = nearby_defense
        if recall_context is not None:
            payload["interpretive_recall"] = recall_context
        journal = getattr(self.attention, "journal", None)
        if journal is not None:
            recent_actions = journal.latest_events(self.scope,
                timeline_id=projection_identity.timeline_id, limit=128)
            review = order_review(recent_actions, projection)
            if review["items"]:
                payload["operational_review"]["order_followthrough"] = review
        non_anchor_tokens = estimate_tokens(payload)
        anchor_cap = min(
            budgets["anchor"],
            budgets["total"] - non_anchor_tokens - budgets["delta_reserve"],
        )
        if anchor_cap < 512:
            raise RuntimeError("context_budget_exhausted:mandatory_runtime_context")
        anchor = self.world.anchor(
            context_length=context_length, focus_ref=focus_ref or None,
            operation_refs=operation_refs, triggered_watch_refs=triggered_watch_refs,
            active_plan_refs=active_plan_refs,
            recent_material_refs=recent_material_refs,
            token_cap=anchor_cap, captured_projection=(projection_identity, projection),
        )
        payload["world"] = {
            "world_anchor_id": anchor["world_anchor_id"],
            "world_anchor_revision": anchor["world_anchor_revision"],
            "anchor_observation_cursor": anchor["anchor_observation_cursor"],
            "anchor": anchor["payload"],
            "net_deltas": anchor.get("net_deltas", []),
            "delta_semantics": "appeared/changed describe known representation, not creation/growth. Counts are known extent, not total size. Unknown neighbors have unknown land/ocean type. Observed boundary closure proves neither ownership nor absence of rivals. Use temporal events for observed changes; absence of events does not prove absence of change.",
            "net_deltas_truncated": bool(anchor.get("net_deltas_truncated")),
        }
        from smacx_operational_context import geographic_belief_review
        belief_review = geographic_belief_review(cognition, anchor['payload'])
        if belief_review:
            payload['operational_review']['geographic_belief_review'] = belief_review
        # The authoritative anchor/focus, binding commitments, and critical
        # attention are pinned. Optional interpretive recall is the first
        # runtime component discarded under pressure.
        runtime_cap = budgets["total"]
        if estimate_tokens(payload) > runtime_cap:
            payload.pop("interpretive_recall", None)
            recall_context = None
        if estimate_tokens(payload) > runtime_cap:
            payload["operations"] = [item for item in operations if item.get("foreground")][:1]
        if estimate_tokens(payload) > runtime_cap:
            items = payload["attention"].get("items", [])
            payload["attention"]["items"] = [item for item in items if item.get("critical")]
            payload["attention"]["truncated"] = len(items) != len(payload["attention"]["items"])
        if estimate_tokens(payload) > runtime_cap:
            raise RuntimeError("context_budget_exhausted:pinned_runtime_context")
        # Only attention that is present in this final serialized envelope may
        # transition to placed.  Anything removed by either local attention
        # budgeting or whole-envelope pressure is detached and requeued with
        # its original stable attention ID.
        if not payload["attention"].get("items"):
            payload["attention"].pop("acknowledgement", None)
        original_lease_count = len(attention_lease.get("items", ()))
        placement = self.attention.restrict_for_placement(
            str(payload["attention"]["attention_lease_id"]),
            [str(item["attention_id"]) for item in payload["attention"].get("items", ())],
        )
        payload["attention"]["through_cursor"] = placement["through_cursor"]
        if placement["requeued_ids"]:
            payload["attention"]["truncated"] = True
        # Compute once from the original lease and the final serialized IDs.
        # _bounded_attention and whole-envelope pressure can both omit rows;
        # summing their counters double-counts the same item.
        remaining = original_lease_count - len(payload["attention"].get("items", ()))
        if remaining:
            payload["attention"]["remaining_count"] = remaining
        else:
            payload["attention"].pop("remaining_count", None)
        payload["token_composition"] = {
            "anchor": estimate_tokens(payload["world"]["anchor"]),
            "deltas": estimate_tokens(payload["world"]["net_deltas"]),
            "focus": estimate_tokens(payload["focus"]),
            "native_protocol": estimate_tokens(payload["native_protocol"]),
            "force_summary": estimate_tokens(payload["force_summary"]),
            "operational_review": estimate_tokens(payload["operational_review"]),
            "spatial_context": estimate_tokens(payload["spatial_context"]),
            "intent_review": estimate_tokens(payload.get("current_turn_intent_review", {})),
            "attention": estimate_tokens(payload["attention"]),
            "cognition": estimate_tokens(cognition),
            "operations": estimate_tokens(payload["operations"]),
            "interpretive_recall": estimate_tokens(recall_context or {}),
        }
        payload["budget"] = {
            "tier": tier, "total": runtime_cap, "anchor_cap": anchor_cap,
        }
        payload["token_estimate"] = 0
        # Reach a fixed point after serializing the estimate itself.  Comparing
        # only the pre-value estimate can undercount a request exactly at the
        # envelope boundary by the digits added to this field.
        for _ in range(4):
            actual_tokens = estimate_tokens(payload)
            if payload["token_estimate"] == actual_tokens:
                break
            payload["token_estimate"] = actual_tokens
        actual_tokens = estimate_tokens(payload)
        payload["token_estimate"] = actual_tokens
        if actual_tokens > runtime_cap:
            raise RuntimeError("context_budget_exhausted:runtime_metadata")
        from smacx_diagnostics import record
        record("runtime_selection", {
            "identity": payload["identity"],
            "cognition": cognition_selection_audit(working, cognition),
            "operation_source_count": len(active["operations"]),
            "operation_selected_count": len(payload["operations"]),
            "attention_source_count": original_lease_count,
            "attention_selected_ids": [item["attention_id"] for item in payload["attention"].get("items", ())],
            "attention_requeued_ids": placement["requeued_ids"],
            "budget": payload["budget"], "token_composition": payload["token_composition"],
        }, actor="runtime-context-builder", correlation={"episode_id": episode_id,
            "attention_lease_id": str(payload["attention"]["attention_lease_id"])})
        for component, value in payload["token_composition"].items():
            self.world.store.telemetry(
                "runtime_context", f"tokens_{component}", value, scope=self.scope,
                timeline_id=projection_identity.timeline_id,
                dimensions={"episode_mode": episode_mode},
            )
        self.world.store.telemetry(
            "runtime_context", "tokens_total", payload["token_estimate"], scope=self.scope,
            timeline_id=projection_identity.timeline_id,
            dimensions={"episode_mode": episode_mode},
        )
        return payload


def envelope(payload: Mapping[str, Any]) -> str:
    return (
        '<SMACX_RUNTIME_CONTEXT schema="smacx.runtime-context.v1">\n'
        + canonical_json(payload)
        + "\n</SMACX_RUNTIME_CONTEXT>"
    )
