"""Finite, bounded plan-linked milestone calculations; no policy or actions."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from smacx_mechanics import field_is_current, field_value


def validate_milestone(predicate: Mapping[str, Any], subjects: Sequence[str]) -> None:
    if set(predicate) - {"mode", "at_least", "requirements"}:
        raise ValueError("unsupported_milestone_field")
    requirements = predicate.get("requirements")
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 16:
        raise ValueError("milestone_requires_1_to_16_requirements")
    if predicate.get("mode", "all") not in {"all", "at_least"}:
        raise ValueError("invalid_milestone_mode")
    if predicate.get("mode") == "at_least" and "at_least" not in predicate:
        raise ValueError("milestone_threshold_required")
    threshold = predicate.get("at_least", len(requirements))
    if type(threshold) is not int or not 1 <= threshold <= len(requirements):
        raise ValueError("invalid_milestone_threshold")
    if predicate.get("mode", "all") == "all" and "at_least" in predicate:
        raise ValueError("at_least_requires_threshold_mode")
    refs = set()
    for requirement in requirements:
        if not isinstance(requirement, dict) or set(requirement) - {"ref", "kind", "field", "value", "count"}:
            raise ValueError("invalid_milestone_requirement")
        if not isinstance(requirement.get("ref"), str):
            raise ValueError("milestone_requirement_ref_required")
        refs.add(requirement["ref"])
        if isinstance(requirement.get("value"), str) and len(requirement["value"]) > 200:
            raise ValueError("milestone_value_too_long")
        kind = requirement.get("kind")
        if kind not in {"exists", "current_field", "contains", "production_completed", "garrison_count", "dependency_valid"}:
            raise ValueError("invalid_milestone_requirement_kind")
        if kind in {"current_field", "contains"}:
            if not isinstance(requirement.get("field"), str) or not requirement["field"] \
                    or requirement["field"].startswith(("_", "native_", "hidden_")):
                raise ValueError("milestone_requires_public_field")
            if type(requirement.get("value")) not in {str, int, float, bool}:
                raise ValueError("milestone_requires_scalar_value")
        if kind == "production_completed" and (
                not isinstance(requirement.get("value"), str) or not requirement["value"]):
            raise ValueError("production_requirement_needs_item_name")
        count = requirement.get("count", 1)
        if type(count) is not int or not 1 <= count <= 16:
            raise ValueError("milestone_count_must_be_1_to_16")
    if refs != set(subjects):
        raise ValueError("milestone_subjects_must_match_requirement_refs")


def evaluate_milestone(
    predicate: Mapping[str, Any], objects: Mapping[str, Mapping[str, Any]],
    registry: Mapping[str, Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Preserve unknown/stale evidence and required destroyed refs explicitly."""
    completed = dict(predicate.get("_completed") or {})
    rows = []
    for index, requirement in enumerate(predicate["requirements"]):
        ref, kind = requirement["ref"], requirement["kind"]
        item = objects.get(ref)
        state = "unknown"
        if kind == "dependency_valid":
            if ref in registry:
                state = "ready"
            elif item is None or item.get("status", "active") in {"destroyed", "removed"}:
                state = "blocked"
            else:
                state = "ready" if item.get("status", "active") == "active" and (
                    item.get("kind") != "foreign_contact" or field_is_current(item, "last_seen_turn")) and any(
                    field_is_current(item, name) for name in item.get("fields", {}) if name != "owner_ref") else "unknown"
        elif item is None or item.get("status", "active") in {"destroyed", "removed"}:
            state = "blocked"
        elif item.get("status", "active") != "active":
            state = "unknown"
        elif kind == "exists":
            # An active record alone may be an old retained sighting.
            current = any(name != "owner_ref" and isinstance(field, Mapping)
                          and field.get("epistemic_status") == "current"
                          for name, field in item.get("fields", {}).items())
            state = "ready" if current else "unknown"
        elif kind in {"current_field", "contains"}:
            field, expected = requirement["field"], requirement["value"]
            if field_is_current(item, field):
                actual = field_value(item, field)
                if kind == "contains":
                    matched = isinstance(actual, list) and any(
                        (value.get("name") if isinstance(value, Mapping) else value) == expected
                        for value in actual)
                else:
                    matched = type(actual) is type(expected) and actual == expected
                state = "ready" if matched else "pending"
        elif kind == "garrison_count":
            # Count explicitly current owned combat units, not stale sightings
            # or units whose role is absent/unknown.
            if field_is_current(item, "owner_ref"):
                units = [unit for unit in objects.values() if unit.get("kind") == "own_unit"
                         and unit.get("status", "active") == "active"
                         and unit.get("location_ref") == item.get("location_ref")]
                known = [unit for unit in units if field_is_current(unit, "roles")
                         and isinstance(field_value(unit, "roles"), Mapping)
                         and type(field_value(unit, "roles").get("combat")) is bool]
                count = sum(field_value(unit, "roles")["combat"] is True for unit in known)
                state = "ready" if count >= requirement.get("count", 1) else \
                    "unknown" if len(known) != len(units) else "pending"
        elif kind == "production_completed":
            receipts = dict(completed.get(str(index)) or {})
            for event in events:
                if event.get("event_kind") == "production_completed" \
                        and event.get("base_ref") == ref and event.get("item_name") == requirement["value"] \
                        and event.get("evidence_kind") == "owned_native_occurrence" \
                        and event.get("occurrence_ref") and len(receipts) < requirement.get("count", 1):
                    receipts[str(event["occurrence_ref"])] = event.get("unit_ref")
            completed[str(index)] = receipts
            destroyed = any(unit_ref and (unit_ref not in objects or
                            objects[unit_ref].get("status", "active") != "active")
                            for unit_ref in receipts.values())
            state = "blocked" if destroyed else "ready" if len(receipts) >= requirement.get("count", 1) else "pending"
        rows.append({"requirement_index": index, "ref": ref, "state": state})
    threshold = len(rows) if predicate.get("mode", "all") == "all" else predicate["at_least"]
    ready = sum(row["state"] == "ready" for row in rows)
    potential = sum(row["state"] != "blocked" for row in rows)
    known_possible = sum(row["state"] in {"ready", "pending"} for row in rows)
    state = "ready" if ready >= threshold else "blocked" if potential < threshold \
        else "unknown" if known_possible < threshold else "pending"
    return {"state": state, "ready_count": ready, "potential_count": potential,
            "required_count": threshold,
            "requirements": rows}, completed
