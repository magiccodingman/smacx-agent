"""Observed operational exceptions and commitments, without assigning strategy."""
from collections import defaultdict


def operational_context(objects, limit=8):
    rows, commitments, colonies = [], defaultdict(list), []
    for obj in sorted(objects.values(), key=lambda o: str(o.get("object_ref"))):
        if obj.get("status", "active") != "active": continue
        fields = obj.get("fields", {})
        def current(key):
            f = fields.get(key, {})
            return f.get("value") if f.get("epistemic_status") == "current" else None
        if obj.get("kind") == "base" and fields.get("owner_ref", {}).get("source") == "owned_state":
            reasons = []
            food, energy = current("nutrient_surplus"), current("energy_surplus")
            if type(food) in (int, float) and food < 0: reasons.append("negative_nutrient_surplus")
            if type(energy) in (int, float) and energy < 0: reasons.append("negative_energy_surplus")
            if current("drone_riots") is True: reasons.append("drone_riots")
            queue = current("production_queue")
            if isinstance(queue, list) and len(queue) <= 1: reasons.append("no_followup_queue_not_idle_production")
            if reasons:
                rows.append({"base_ref": obj["object_ref"], "review_reasons": reasons,
                             "observed": {k: fields[k] for k in ("production_name", "production_queue", "nutrient_surplus", "energy_surplus", "mineral_surplus", "population", "drone_riots") if k in fields},
                             "query": {"mode": "base", "subject_refs": [obj["object_ref"]]}})
        if obj.get("kind") == "own_unit":
            roles = current("roles") or {}
            if isinstance(roles, dict) and roles.get("colony") is True:
                colonies.append({"unit_ref": obj["object_ref"], "location_ref": obj.get("location_ref"),
                                 "order": fields.get("order_name", {}), "roles": fields.get("roles", {})})
            # Only explicit, current destinations can establish overlap. A unit's
            # home base and proximity are not an assignment.
            for key in ("destination_ref", "order_target_ref"):
                destination = current(key)
                if isinstance(destination, str):
                    commitments[destination].append(obj["object_ref"])
                    break
    return {"economic_review": rows[:limit], "economic_review_total": len(rows),
            "economic_review_omitted": max(0, len(rows)-limit),
            "observed_colony_units": colonies[:limit], "colony_units_omitted": max(0, len(colonies)-limit),
            "shared_explicit_destinations": [{"destination_ref": k, "unit_refs": v[:8], "unit_count": len(v)}
                                             for k, v in sorted(commitments.items()) if len(v) > 1][:limit],
            "meaning": "Review signals, not failure or strategic instructions. Empty follow-up queue does not mean no current production; negative surplus is an observed rate, not a predicted outcome. Only current explicit targets establish assignment overlap; missing targets mean unknown assignments. For task progress use changes and recorded intent; stationary defense/work is not a stall."}


def order_review(events, projection, limit=4):
    """Compare journal action baselines with a committed observed endpoint.

    Does not turn endpoint equality into evidence of inactivity between samples.
    """
    from smacx_order_attention import unit_state
    rows, seen = [], set()
    for event in sorted(events, key=lambda e: e.get("sequence", 0), reverse=True):
        payload = event.get("payload", {})
        attempt = payload.get("persistent_order_attempt") or payload.get("former_automation_attempt")
        if not isinstance(attempt, dict): continue
        before = attempt.get("before", {})
        ref = before.get("unit_ref")
        if not ref or ref in seen: continue
        seen.add(ref)
        if attempt.get("world_epoch") != projection.get("identity", {}).get("world_epoch"): continue
        if int(attempt.get("observation_cursor") or 0) >= int(projection.get("observation_cursor") or 0): continue
        after = unit_state(projection, ref)
        if after is None: continue
        rows.append({"unit_ref": ref, "assignment_turn": attempt.get("turn"),
                     "source_action_event_id": event.get("event_id"),
                     "recorded_command": attempt.get("mode"),
                     "assignment_observation_cursor": attempt.get("observation_cursor"),
                     "endpoint_observation_cursor": projection.get("observation_cursor"),
                     "observed_endpoint": after,
                     "location_differs_from_assignment": before.get("location_ref") != after.get("location_ref"),
                     "meaning": "Endpoint comparison only. Later commands may supersede assignment. Equal endpoints do not prove no intervening progress; changed location does not prove arrival or completion. Review changes and current intent before repeating an order."})
    return {"items": rows[:limit], "omitted_count": max(0, len(rows)-limit),
            "coverage": "bounded recent canonical action window; older assignments may be absent"}
