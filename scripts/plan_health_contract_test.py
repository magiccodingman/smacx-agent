#!/usr/bin/env python3
"""Explicit assignments/conflicts without inferred strategy or stale exactness."""

from copy import deepcopy
import json

from smacx_plan_health import plan_health


def main():
    def field(value):
        return {"value": value, "epistemic_status": "current"}
    objects = {"own-unit-1": {"kind": "own_unit", "fields": {"order_name": field("hold")}},
               "own-unit-2": {"kind": "own_unit", "fields": {"order_name": field("none")}},
               "own-unit-3": {"kind": "own_unit", "fields": {"order_name": field("none")}},
               "own-unit-4": {"kind": "own_unit", "fields": {"order_name": field("none")}},
               "base-a": {"kind": "base", "fields": {}},
               "location-west": {"kind": "location", "fields": {}},
               "location-east": {"kind": "location", "fields": {}},
               "economy": {"kind": "economy", "fields": {"energy_credits": field(100)}},
               "faction-2": {"kind": "faction", "fields": {"relationship": field("neutral")}}}
    first = {"plan_id": "plan-a", "status": "active", "timing": {"start_turn": 4, "end_turn": 6},
             "objective": "Invade everything and spend a million credits",  # prose has no machine authority
             "participants": [{"ref": "own-unit-1", "intended_role": "reserve", "target_ref": "location-west"},
                              {"ref": "own-unit-2", "intended_role": "stationary escort"},
                              {"ref": "base-a", "production_item": "Scout"},
                              {"ref": "economy", "energy_credits": 60}],
             "dependencies": ["faction-2", "route-west"],
             "last_confirmation": {"dependency_values": [{"ref": "faction-2", "field": "relationship", "value": "allied"}]}}
    second = {"plan_id": "plan-b", "status": "active", "timing": {"start_turn": 5, "end_turn": 7},
              "participants": [{"ref": "own-unit-1", "target_ref": "location-east"},
                               {"ref": "base-a", "production_item": "Transport"},
                               {"ref": "economy", "energy_credits": 50}]}
    operations = [{"status": "active", "referenced_world_objects": ["own-unit-3"]}]
    ready = ["own-unit-2", "own-unit-3", "own-unit-4"]
    result = plan_health([first, second], objects, operations, ready, set(objects), complete=True)
    assert result["assigned_owned_unit_count"] == 2
    assert result["mechanically_ordered_unit_count"] == 1
    assert result["active_operation_unit_count"] == 1
    assert result["snapshot_actionable_unassigned_count"] == 1
    assert result["conflict_count"] == 3, result
    broad = deepcopy(second)
    broad["participants"][0]["target_ref"] = "region-possibly-overlapping"
    assert plan_health([first, broad], objects, operations, ready, set(objects), complete=True)["conflict_count"] == 2
    assert {row["state"] for row in result["dependency_exceptions"]} == {"invalid", "changed"}
    later = deepcopy(second)
    later["timing"] = {"start_turn": 7, "end_turn": 9}
    assert plan_health([first, later], objects, operations, ready, set(objects), complete=True)["conflict_count"] == 0
    unpriced = deepcopy(objects)
    unpriced["economy"]["fields"]["energy_credits"]["epistemic_status"] = "stale"
    unpriced["faction-2"]["fields"]["relationship"]["epistemic_status"] = "stale"
    qualified = plan_health([first, second], unpriced, operations, ready, set(objects), complete=True)
    assert qualified["conflict_count"] == 2
    assert any(row["state"] == "unknown" for row in qualified["dependency_exceptions"])
    partial = plan_health([first], objects, operations, ready, set(objects), complete=False)
    assert partial["snapshot_actionable_unassigned_count"] is None
    vague = {"plan_id": "plan-vague", "status": "active", "objective": first["objective"]}
    before = deepcopy(vague)
    unbound = plan_health([vague], objects, [], ready, set(objects), complete=True)
    assert unbound["conflict_count"] == 0 and unbound["assigned_owned_unit_count"] == 0
    assert unbound["intent_coverage_complete"]  # Enumeration, not narrative verification.
    assert unbound["plans_without_world_bindings_count"] == 1
    assert unbound["plans_without_world_bindings"] == ["plan-vague"]
    assert "Objective prose is not interpreted or verified" in unbound["assessment_scope"]
    assert "not that a plan is achievable" in unbound["assessment_scope"]
    assert vague == before
    target_only = {**vague, "target_refs": ["base-a"]}
    bound = plan_health([target_only], objects, [], ready, set(objects), complete=True)
    assert bound["plans_without_world_bindings_count"] == 0 and bound["assigned_owned_unit_count"] == 0
    many = [{**vague, "plan_id": f"plan-{i}"} for i in range(12)]
    bounded = plan_health(many, objects, [], ready, set(objects), complete=True)
    assert bounded["plans_without_world_bindings_count"] == 12
    assert len(bounded["plans_without_world_bindings"]) == 8
    assert bounded["world_binding_details_truncated"]
    print(json.dumps({"ok": True, "explicit_assignment_distinct_from_order": True,
                      "timed_unit_production_credit_conflicts": True, "stale_credit_withheld": True,
                      "dependency_change_qualified": True, "incomplete_intent_not_unassigned": True,
                      "prose_has_no_mechanical_authority": True,
                      "unbound_plan_scope_explicit_and_bounded": True}))


if __name__ == "__main__":
    main()
