"""Bounded, value-neutral mechanical alternatives over qualified evidence."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from smacx_mechanics import field_is_current, field_value, mobility_profile, object_location, transport_route


KINDS = {"site_economy", "terraform", "deployment", "social", "action"}
CAPABILITIES = {"combat", "colony", "former", "transport", "probe", "supply"}
MOBILITY_FIELDS = {"triad", "movement_points", "moves_remaining", "movement_scale",
                   "roles", "abilities", "owner_ref", "cargo", "air_safe_range",
                   "air_full_safe_range", "air_origin_refuels", "airdrop_ready",
                   "airdrop_range", "airdrop_target_tile_ids", "airdrop_targets_truncated",
                   "fungus_movement_cost", "fungus_connects_to_road", "ignores_rough_movement",
                   "road_movement_cost", "magtube_movement_cost", "transport_unit_ref"}


def parse_scenario(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4096:
        raise ValueError("counterfactual_definition_too_large")
    try:
        value = json.loads(raw)
    except RecursionError as error:
        raise ValueError("counterfactual_definition_too_deep") from error
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str) \
            or value["kind"] not in KINDS:
        raise ValueError("invalid_counterfactual_kind")
    kind = value["kind"]
    allowed = {"kind"} | ({"populations"} if kind == "site_economy" else
                         {"capability", "choice_refs"} if kind == "deployment" else
                         {"decision_id", "choice_id"})
    if set(value) - allowed:
        raise ValueError("unsupported_counterfactual_parameter")
    if kind == "site_economy":
        levels = value.get("populations", [1, 2, 3])
        if not isinstance(levels, list) or not 1 <= len(levels) <= 4 \
                or any(type(level) is not int or not 1 <= level <= 6 for level in levels):
            raise ValueError("site_populations_require_1_to_4_integers_between_1_and_6")
        value["populations"] = sorted(set(levels))
    elif kind == "deployment":
        if not isinstance(value.get("capability"), str) or value["capability"] not in CAPABILITIES:
            raise ValueError("deployment_requires_explicit_capability")
        choices = value.get("choice_refs", [])
        if not isinstance(choices, list) or len(choices) > 4:
            raise ValueError("deployment_allows_at_most_four_build_or_upgrade_choices")
        for choice in choices:
            if not isinstance(choice, dict) or set(choice) != {"decision_id", "choice_id"}:
                raise ValueError("invalid_counterfactual_choice_reference")
            _validate_choice_ref(choice)
    else:
        _validate_choice_ref(value)
    return value


def _validate_choice_ref(value: Mapping[str, Any]) -> None:
    if any(not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= 160
           for key in ("decision_id", "choice_id")):
        raise ValueError("counterfactual_requires_current_decision_and_choice")


def feasible_outputs(squares: Sequence[Mapping[str, Any]], center: Mapping[str, Any],
                     population: int, *, alternative_limit: int = 8) -> dict[str, Any]:
    """Choose distinct workers, preserving feasible joint N/M/E assignments.

    Dominance pruning is sound only within a processed-square prefix and an
    equal worker count. A safety cap returns a qualified feasible subset; it
    never labels that subset a complete Pareto frontier.
    """
    if type(population) is not int or not 1 <= population <= 6 or len(squares) > 20 \
            or type(alternative_limit) is not int or not 1 <= alternative_limit <= 32:
        raise ValueError("site_economy_input_bound_exceeded")
    resources = ("nutrients", "minerals", "energy")
    if center.get("epistemic_status") not in {"current", "conditional"} \
            or any(type(center.get(key)) is not int for key in resources):
        return {"population": population, "alternatives": [], "coverage": "center_yield_unknown"}
    usable = []
    seen = {center.get("location_ref")}
    for square in squares:
        ref = square.get("location_ref")
        if not isinstance(ref, str) or not ref or ref in seen:
            raise ValueError("site_workers_require_distinct_noncenter_locations")
        seen.add(ref)
        yields = square.get("yields", {})
        if square.get("workable") is True and square.get("epistemic_status") in {"current", "conditional"} \
                and isinstance(yields, Mapping) \
                and all(type(yields.get(key)) is int for key in resources):
            usable.append((ref, tuple(yields[key] for key in resources)))
    states: list[dict[tuple[int, int, int], tuple[str, ...]]] = [{(0, 0, 0): ()}] + [{} for _ in range(population)]
    capped = False
    for ref, output in usable:
        for workers in range(population, 0, -1):
            candidates = dict(states[workers])
            for prior, allocation in states[workers - 1].items():
                total = tuple(prior[i] + output[i] for i in range(3))
                candidates.setdefault(total, (*allocation, ref))
            frontier: dict[tuple[int, int, int], tuple[str, ...]] = {}
            # With nutrients descending, dominance reduces to a prefix max
            # over descending mineral ranks. This avoids quadratic scans of
            # thousands of attainable states on heavily modified rulesets.
            ranks = {value: index + 1 for index, value in enumerate(
                sorted({total[1] for total in candidates}, reverse=True))}
            energy_max: list[int | None] = [None] * (len(ranks) + 1)
            for total in sorted(candidates, reverse=True):
                rank = ranks[total[1]]
                cursor, maximum = rank, None
                while cursor:
                    value = energy_max[cursor]
                    if value is not None:
                        maximum = value if maximum is None else max(maximum, value)
                    cursor -= cursor & -cursor
                if maximum is not None and maximum >= total[2]:
                    continue
                frontier[total] = candidates[total]
                cursor = rank
                while cursor < len(energy_max):
                    value = energy_max[cursor]
                    energy_max[cursor] = total[2] if value is None else max(value, total[2])
                    cursor += cursor & -cursor
                if len(frontier) >= 4096:
                    capped = True
                    break
            states[workers] = frontier
    values = sorted(states[population])
    # Evenly sample the deterministic frontier order; no weighted ranking or
    # "best" alternative is assigned. Every returned row remains attainable.
    selected = values if len(values) <= alternative_limit else [values[len(values) // 2]] \
        if alternative_limit == 1 else [
        values[index * (len(values) - 1) // (alternative_limit - 1)]
        for index in range(alternative_limit)]
    return {"population": population,
            "epistemic_status": "conditional",
            "alternatives": [{"gross_output": {key: total[index] + center[key]
                                                 for index, key in enumerate(resources)},
                              "worker_refs": list(states[population][total])} for total in selected],
            "known_workable_square_count": len(usable),
            "retained_frontier_count": len(values), "frontier_search_complete": not capped,
            "alternatives_truncated": len(selected) != len(values) or capped,
            "coverage": "known_current_workable_subset",
            "assumptions": ["all requested citizens work distinct squares", "center output is additional",
                            "gross output before support, inefficiency, facilities multiplying totals, and psych effects"]}


def deployment_alternatives(topology, objects: Mapping[str, Mapping[str, Any]],
                            scenario: Mapping[str, Any], target_ref: str,
                            subject_refs: Sequence[str], receipts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Compare nominated production choices with bounded existing capabilities."""
    target = object_location(objects.get(target_ref), target_ref)
    if target not in topology.by_ref:
        raise ValueError("deployment_requires_known_target")
    selected = set(subject_refs)
    candidates = [item for item in objects.values() if item.get("kind") == "own_unit"
                  and (not selected or item.get("object_ref") in selected)
                  and field_is_current(item, "roles")
                  and isinstance(field_value(item, "roles"), Mapping)
                  and field_value(item, "roles").get(scenario["capability"]) is True]
    candidates.sort(key=lambda item: str(item["object_ref"]))
    rows = []

    def travel(actor, pool, *, preparation, label):
        ref = actor["object_ref"]
        row = {**label, "epistemic_status": "conditional", "target_ref": target_ref,
               "preparation_turns": preparation, "travel_turns": None, "total_turns": None,
               "assumptions": ["current terrain, diplomacy, movement rules, and transport availability remain fixed"],
               "coverage": "bounded_known_world_routes"}
        if field_value(actor, "triad") == "air" and (
                type(field_value(actor, "air_safe_range")) is not int
                or field_value(actor, "air_safe_range") < 0
                or label.get("alternative") == "upgrade_unit"):
            row["coverage"] = "air_deployment_fuel_state_unavailable"
            return row
        profile = mobility_profile(pool, "counterfactual-deployment", subject_ref=ref, topology=topology)
        route = topology.route(object_location(actor), target, profile)
        row.update(reachable=route.reachable, travel_turns=route.turns,
                   route_evidence=route.eta_kind, uncertainty=list(route.uncertainty),
                   transport_dependency=None)
        boarded = bool(field_value(actor, "roles", {}).get("boarded"))
        if boarded:
            # The passenger's nominal speed cannot stand in for its carrier.
            row.update(reachable=False, travel_turns=None,
                       route_evidence="unknown", coverage="boarded_transport_route_unavailable")
        if not route.reachable or boarded:
            assisted = transport_route(topology, pool, ref, target_ref)
            if assisted:
                row.update(reachable=assisted.get("reachable", False),
                           travel_turns=assisted.get("eta_turns"),
                           route_evidence=assisted.get("eta_kind", "unknown"),
                           transport_dependency=assisted.get("transport_ref"),
                           transport_search=assisted.get("search"),
                           uncertainty=list(assisted.get("uncertainty", [])))
                row["coverage"] = "bounded_known_world_routes"
                transport = pool.get(str(assisted.get("transport_ref")), {})
                fields = MOBILITY_FIELDS & transport.get("fields", {}).keys()
                if transport and any(not field_is_current(transport, key) for key in fields):
                    row.update(travel_turns=None, coverage="current_transport_inputs_unavailable")
                    row["uncertainty"].append("The nominated transport has stale or unknown mobility/capacity evidence.")
        if type(preparation) is int and type(row["travel_turns"]) is int and row.get("reachable"):
            row["total_turns"] = preparation + row["travel_turns"]
        return row

    for actor in candidates[:8]:
        required = {"triad", "movement_points"} | (MOBILITY_FIELDS & actor.get("fields", {}).keys())
        if not all(field_is_current(actor, key) for key in required):
            rows.append({"unit_ref": actor["object_ref"], "coverage": "current_mobility_inputs_unavailable",
                         "epistemic_status": "unknown", "total_turns": None})
            continue
        rows.append(travel(actor, objects, preparation=0, label={"alternative": "existing_unit",
                                                                "unit_ref": actor["object_ref"]}))
    for index, receipt in enumerate(receipts[:4]):
        prototype = receipt.get("prototype", {})
        if not isinstance(prototype, Mapping) or not isinstance(prototype.get("roles"), Mapping) \
                or prototype["roles"].get(scenario["capability"]) is not True:
            rows.append({"choice_ref": scenario.get("choice_refs", [])[index],
                         "coverage": "nominated_choice_does_not_supply_requested_capability",
                         "epistemic_status": "current", "total_turns": None})
            continue
        actor_ref = "hypothetical-deployment-actor"
        actor = {"kind": "own_unit", "object_ref": actor_ref,
                 "location_ref": receipt.get("origin_location_ref"),
                 "fields": {key: {"value": value, "epistemic_status": "conditional"}
                            for key, value in prototype.items()}}
        if receipt.get("proposed_action") in {"set_production", "hurry_production"}:
            actor["fields"]["air_origin_refuels"] = {"value": True, "epistemic_status": "conditional"}
        pool = {**objects, actor_ref: actor}
        preparation = receipt.get("estimated_production_turns", receipt.get("estimated_preparation_turns"))
        row = travel(actor, pool, preparation=preparation, label={
            "alternative": receipt.get("proposed_action"),
            "choice_ref": scenario.get("choice_refs", [])[index],
            "base_ref": receipt.get("base_ref"), "unit_ref": receipt.get("own_unit_ref") or receipt.get("unit_ref"),
            "production": {key: receipt.get(key) for key in ("current_progress", "resulting_progress",
                "mineral_cost", "mineral_surplus", "retool_penalty", "energy_cost")}})
        row["assumptions"].extend(receipt.get("assumptions", []))
        row["assumptions"].append("travel starts after preparation using the displayed current transport readiness; future transport movement is not scheduled")
        row["assumptions"].append(str(prototype.get("movement_assumption")))
        rows.append(row)
    return [{"capability": scenario["capability"], "target_ref": target_ref,
             "existing_candidate_count": len(candidates), "existing_candidates_truncated": len(candidates) > 8,
             "alternatives": rows, "ranking": "none", "epistemic_status": "conditional"}]


def action_relationships(objects: Mapping[str, Mapping[str, Any]], choice: Mapping[str, Any],
                         plans: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Expose explicit relationships affected by a nominated action, not doctrine."""
    ref = str(choice.get("own_unit_ref") or choice.get("unit_ref") or choice.get("base_ref") or "")
    actor = objects.get(ref, {})
    command = choice.get("command")
    removes_local = command in {"move_unit", "disband_unit"}
    local_bases = [base for base in objects.values() if base.get("kind") == "base"
                   and object_location(base) == object_location(actor)] if actor.get("kind") == "own_unit" else []
    links = []
    for plan in plans[:128]:
        participants = plan.get("participants") or []
        dependencies = plan.get("dependencies") or []
        if ref and (ref in dependencies or any(isinstance(p, Mapping) and p.get("ref") == ref
                                               for p in participants[:64])):
            links.append({"plan_ref": plan.get("plan_id") or plan.get("journal_stable_key"),
                          "relation": "explicit_subject_dependency_or_participant"})
    return {"subject_ref": ref or None, "epistemic_status": "conditional",
            "garrison_departures": [{"base_ref": base["object_ref"], "unit_ref": ref}
                                     for base in local_bases] if removes_local else [],
            "current_home_base_ref": field_value(actor, "home_base_ref")
                if field_is_current(actor, "home_base_ref") else None,
            "proposed_home_base_ref": choice.get("base_ref") if command == "rehome_unit" else None,
            "linked_intent": links[:16], "linked_intent_truncated": len(links) > 16 or len(plans) > 128,
            "assumptions": ["the nominated action succeeds"],
            "limitations": ["garrison departure does not by itself prove lost police suppression or a riot",
                            "linked plans require sovereign review; they are not automatically changed"]}
