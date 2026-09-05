"""Mechanical health of explicit journaled intent; never choose a plan."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from smacx_mechanics import field_is_current, field_value


def plan_health(
    plans: Sequence[Mapping[str, Any]], objects: Mapping[str, Mapping[str, Any]],
    operations: Sequence[Mapping[str, Any]], ready_refs: Sequence[str],
    dependency_refs: set[str], *, complete: bool,
) -> dict[str, Any]:
    """Participants use ref/target_ref/intended_role and explicit turn windows.

    Only overlapping declared intervals can conflict. Missing timing, amounts
    or reservations are unknown, never interpreted from objective prose.
    """
    assigned: set[str] = set()
    reservations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    dependencies = []
    active = [plan for plan in plans if plan.get("status", "active") == "active"]
    complete = complete and len(active) <= 128
    for plan in active[:128]:
        plan_ref = str(plan.get("plan_id") or plan.get("journal_stable_key") or plan.get("plan_key") or "")
        participants = plan.get("participants") or []
        if not isinstance(participants, list):
            complete = False
            continue
        complete &= len(participants) <= 64
        for participant in participants[:64]:
            if not isinstance(participant, Mapping):
                complete = False
                continue
            ref = participant.get("ref")
            if not isinstance(ref, str) or ref not in objects:
                complete = False
                continue
            # An explicit participant is an assignment even when it is an
            # intentionally stationary reserve and has no movement target.
            assigned.add(ref)
            timing = participant.get("timing", plan.get("timing", {}))
            if not isinstance(timing, Mapping):
                continue
            start, end = timing.get("start_turn"), timing.get("end_turn")
            if type(start) is not int or type(end) is not int or not 0 <= start <= end:
                continue
            if len(reservations[ref]) >= 64:
                complete = False
                continue
            reservations[ref].append({"plan_ref": plan_ref, "start": start, "end": end,
                                      "target_ref": participant.get("target_ref"),
                                      "exclusive": participant.get("exclusive") is True,
                                      "production_item": participant.get("production_item"),
                                      "energy_credits": participant.get("energy_credits")})
        declared = plan.get("dependencies") or []
        if not isinstance(declared, list):
            complete = False
            declared = []
        if isinstance(declared, list):
            complete &= len(declared) <= 64
            for ref in declared[:64]:
                if isinstance(ref, str) and ref not in dependency_refs:
                    dependencies.append({"plan_ref": plan_ref, "ref": ref, "state": "invalid"})
        confirmation = plan.get("last_confirmation") or {}
        expected = confirmation.get("dependency_values", []) if isinstance(confirmation, Mapping) else []
        if isinstance(expected, list):
            complete &= len(expected) <= 16
            for expectation in expected[:16]:
                if not isinstance(expectation, Mapping) or expectation.get("ref") not in declared:
                    continue
                ref, field = expectation["ref"], expectation.get("field")
                if not isinstance(field, str) or "value" not in expectation:
                    continue
                item = objects.get(ref, {})
                state = "unknown" if not field_is_current(item, field) else \
                    "unchanged" if field_value(item, field) == expectation["value"] else "changed"
                if state != "unchanged":
                    dependencies.append({"plan_ref": plan_ref, "ref": ref, "field": field, "state": state})
    conflicts = []
    def fixed_location(ref):
        target = objects.get(ref, {}) if isinstance(ref, str) else {}
        if target.get("status", "active") != "active":
            return None
        if target.get("kind") == "location":
            return ref
        if target.get("kind") == "base":
            return target.get("location_ref")
        return None
    for ref, rows in reservations.items():
        # Each plan reserves a concrete resource only once for this comparison;
        # repeated identical participant declarations are not extra demand.
        unique = {(row["plan_ref"], row["start"], row["end"], str(row["target_ref"]),
                   str(row["production_item"]), str(row["energy_credits"]), row["exclusive"]): row for row in rows}
        rows = list(unique.values())
        for index, first in enumerate(rows):
            for second in rows[index + 1:]:
                if first["plan_ref"] == second["plan_ref"] or max(first["start"], second["start"]) > min(first["end"], second["end"]):
                    continue
                first_location, second_location = fixed_location(first["target_ref"]), fixed_location(second["target_ref"])
                incompatible_target = first_location and second_location and first_location != second_location
                incompatible_production = objects[ref].get("kind") == "base" and first["production_item"] \
                    and second["production_item"] and first["production_item"] != second["production_item"]
                if incompatible_target or incompatible_production or first["exclusive"] or second["exclusive"]:
                    conflicts.append({"resource_ref": ref, "plan_refs": [first["plan_ref"], second["plan_ref"]],
                                      "kind": "overlapping_explicit_reservations"})
        item = objects[ref]
        if field_is_current(item, "energy_credits") and type(field_value(item, "energy_credits")) is int:
            financial = [row for row in rows if type(row["energy_credits"]) is int and row["energy_credits"] > 0]
            for start in sorted({row["start"] for row in financial}):
                simultaneous = [row for row in financial if row["start"] <= start <= row["end"]]
                # Multiple reservations from the same plan may describe the
                # same pool. Use its maximum explicit amount, not their sum.
                by_plan: dict[str, int] = {}
                for row in simultaneous:
                    by_plan[row["plan_ref"]] = max(by_plan.get(row["plan_ref"], 0), row["energy_credits"])
                if sum(by_plan.values()) > field_value(item, "energy_credits"):
                    conflicts.append({"resource_ref": ref, "plan_refs": sorted(by_plan)[:8],
                                      "kind": "reserved_current_credit_pool_exceeded",
                                      "reserved_credits": sum(by_plan.values()),
                                      "currently_observed_credits": field_value(item, "energy_credits")})
                    break
    own = {ref for ref, item in objects.items() if item.get("kind") == "own_unit" and item.get("status", "active") == "active"}
    operational = {str(ref) for operation in operations if operation.get("status") == "active"
                   for ref in operation.get("referenced_world_objects", [])} & own
    ordered = {ref for ref in own if field_is_current(objects[ref], "order_name")
               and field_value(objects[ref], "order_name") not in {None, "none"}}
    ready = set(ready_refs) & own
    return {"active_plan_count": len(active), "intent_coverage_complete": bool(complete),
            "assigned_owned_unit_count": len(assigned & own),
            "mechanically_ordered_unit_count": len(ordered), "active_operation_unit_count": len(operational),
            "snapshot_actionable_unassigned_count": len(ready - assigned - operational) if complete else None,
            "conflict_count": len(conflicts), "conflicts": conflicts[:8],
            "dependency_exception_count": len(dependencies), "dependency_exceptions": dependencies[:8],
            "exception_details_truncated": len(conflicts) > 8 or len(dependencies) > 8}
