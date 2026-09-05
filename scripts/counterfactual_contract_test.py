#!/usr/bin/env python3
"""Arithmetic contracts, not evidence of agreement with a running native game."""

from itertools import combinations
import json
import random
from pathlib import Path
import tempfile
from time import perf_counter

from smacx_counterfactual import action_relationships, deployment_alternatives, feasible_outputs, parse_scenario
from geographic_semantics_contract_test import initialized
from geographic_semantics_contract_test import obj
from smacx_mechanics import base_mechanics
from smacx_topology import KnownSquare, MapShape, PerspectiveTopology
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector
from smacx_world_types import WorldIdentity
from smacx_world_model import estimate_tokens


RESOURCES = ("nutrients", "minerals", "energy")


def rejected(fn):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("invalid input accepted")


def main():
    rng = random.Random(48)
    center = dict(zip(RESOURCES, (2, 1, 1)), location_ref="center",
                  epistemic_status="current")
    checked = 0
    for count in range(1, 11):
        squares = [{"location_ref": f"tile-{i}", "workable": True,
                    "epistemic_status": "current",
                    "yields": dict(zip(RESOURCES, (rng.randrange(7) for _ in RESOURCES)))}
                   for i in range(count)]
        by_ref = {row["location_ref"]: row for row in squares}
        for population in range(1, min(6, count) + 1):
            possible = {tuple(sum(row["yields"][key] for row in rows) for key in RESOURCES)
                        for rows in combinations(squares, population)}
            frontier = {value for value in possible if not any(
                other != value and all(a >= b for a, b in zip(other, value))
                for other in possible)}
            result = feasible_outputs(squares, center, population, alternative_limit=32)
            assert result["frontier_search_complete"]
            assert result["retained_frontier_count"] == len(frontier)
            for row in result["alternatives"]:
                workers = row["worker_refs"]
                assert len(workers) == len(set(workers)) == population
                output = tuple(row["gross_output"][key] - center[key] for key in RESOURCES)
                assert output in frontier
                assert output == tuple(sum(by_ref[ref]["yields"][key] for ref in workers)
                                       for key in RESOURCES)
            checked += 1
    assert feasible_outputs(squares, {**center, "epistemic_status": "stale"}, 1)["alternatives"] == []
    hidden = [{**row, "epistemic_status": "stale"} for row in squares]
    assert feasible_outputs(hidden, center, 1)["known_workable_square_count"] == 0
    assert feasible_outputs(squares, center, 1, alternative_limit=1)["alternatives"]
    rejected(lambda: feasible_outputs([squares[0], squares[0]], center, 1))
    rejected(lambda: feasible_outputs([{**squares[0], "location_ref": "center"}], center, 1))
    rejected(lambda: feasible_outputs(squares, center, True))
    rejected(lambda: feasible_outputs(squares, center, 1, alternative_limit=0))
    for scenario in ({"kind": []}, {"kind": "deployment", "capability": []},
                     {"kind": "site_economy", "populations": [True]},
                     {"kind": "social", "decision_id": "d", "choice_id": "c", "native_args": {}},
                     {"kind": "terraform", "decision_id": "d"}):
        rejected(lambda: parse_scenario(json.dumps(scenario)))
    assert parse_scenario('{"kind":"site_economy"}')["populations"] == [1, 2, 3]
    topology = PerspectiveTopology(MapShape(20, 8, False), [KnownSquare("start", 0, 2, "land"),
                                                         KnownSquare("target", 2, 2, "land")])
    base = obj("base", "base", "start", minerals_accumulated=5, mineral_surplus=5, production_cost=20)
    objects = {"base": base, "start": obj("start", "location"), "target": obj("target", "location"),
               "former": obj("former", "own_unit", "start", roles={"former": True},
                             triad="land", movement_points=1, home_base_ref="base")}
    assert base_mechanics(topology, objects, ["base"])[0]["production"]["turns_remaining"] == 3
    base["fields"]["production_cost"]["epistemic_status"] = "stale"
    assert base_mechanics(topology, objects, ["base"])[0]["production"]["turns_remaining"] is None
    deployed = deployment_alternatives(topology, objects, {"capability": "former"}, "target", [], [])
    assert deployed[0]["alternatives"][0]["total_turns"] == 1
    assert deployed[0]["alternatives"][0]["epistemic_status"] == "conditional"
    objects["former"]["fields"]["moves_remaining"] = {"value": 1, "epistemic_status": "stale"}
    unavailable = deployment_alternatives(topology, objects, {"capability": "former"}, "target", [], [])
    assert unavailable[0]["alternatives"][0]["total_turns"] is None
    del objects["former"]["fields"]["moves_remaining"]
    nomination = {"decision_id": "d", "choice_id": "c"}
    future = deployment_alternatives(topology, objects,
        {"capability": "former", "choice_refs": [nomination]}, "target", [], [{
            "proposed_action": "set_production", "origin_location_ref": "start",
            "estimated_production_turns": 3, "prototype": {
                "roles": {"former": True}, "triad": "land", "movement_points": 1}}])
    assert future[0]["alternatives"][1]["total_turns"] == 4
    assert future[0]["alternatives"][1]["epistemic_status"] == "conditional"
    air_objects = {**objects, "air-base": obj("air-base", "base", "start", owner_ref="faction-1"),
        "aircraft": obj("aircraft", "own_unit", "start", owner_ref="faction-1",
        roles={"combat": True}, triad="air", movement_points=2, moves_remaining=2,
        air_safe_range=4, air_full_safe_range=4, air_origin_refuels=True)}
    aircraft = deployment_alternatives(topology, air_objects, {"capability": "combat"}, "target", [], [])
    assert aircraft[0]["alternatives"][0]["total_turns"] == 1
    air_objects["aircraft"]["fields"]["air_safe_range"]["value"] = -1
    unavailable = deployment_alternatives(topology, air_objects, {"capability": "combat"}, "target", [], [])
    assert unavailable[0]["alternatives"][0]["coverage"] == "air_deployment_fuel_state_unavailable"
    sea_topology = PerspectiveTopology(MapShape(20, 8, False), [
        KnownSquare("port", 0, 2, "land", features=frozenset({"base"})), KnownSquare("water", 2, 2, "ocean"),
        KnownSquare("landing", 4, 2, "land")])
    sea_objects = {"port": obj("port", "location"), "landing": obj("landing", "location"),
        "base-port": obj("base-port", "base", "port", owner_ref="faction-1", coastal=True),
        "passenger": obj("passenger", "own_unit", "port", owner_ref="faction-1",
            roles={"former": True}, triad="land", movement_points=1, moves_remaining=1),
        "transport": obj("transport", "own_unit", "port", owner_ref="faction-1",
            roles={"transport": True}, triad="sea", movement_points=2, moves_remaining=2,
            cargo={"capacity": 2, "loaded": 0})}
    crossing = deployment_alternatives(sea_topology, sea_objects, {"capability": "former"}, "landing", [], [])
    assert crossing[0]["alternatives"][0]["transport_dependency"] == "transport", crossing
    assert crossing[0]["alternatives"][0]["total_turns"] is not None, crossing
    sea_objects["transport"]["fields"]["cargo"]["epistemic_status"] = "stale"
    stale_crossing = deployment_alternatives(sea_topology, sea_objects, {"capability": "former"}, "landing", [], [])
    assert stale_crossing[0]["alternatives"][0]["total_turns"] is None
    sea_objects["transport"]["fields"]["cargo"]["epistemic_status"] = "current"
    sea_objects["transport"]["fields"]["cargo"]["value"]["loaded"] = 2
    sea_objects["passenger"]["fields"]["roles"]["value"]["boarded"] = True
    sea_objects["passenger"]["fields"]["transport_unit_ref"] = {
        "value": "transport", "epistemic_status": "current"}
    sea_objects["passenger"]["location_ref"] = "water"
    sea_objects["transport"]["location_ref"] = "water"
    boarded = deployment_alternatives(sea_topology, sea_objects, {"capability": "former"}, "port", [], [])
    assert boarded[0]["alternatives"][0]["transport_dependency"] == "transport", boarded
    sea_objects["passenger"]["fields"]["transport_unit_ref"]["epistemic_status"] = "stale"
    stale_boarding = deployment_alternatives(sea_topology, sea_objects, {"capability": "former"}, "port", [], [])
    assert stale_boarding[0]["alternatives"][0]["total_turns"] is None
    linked = action_relationships(objects, {"command": "move_unit", "own_unit_ref": "former"},
                                  [{"plan_id": "plan", "participants": [{"ref": "former"}]}])
    assert linked["garrison_departures"] == [{"base_ref": "base", "unit_ref": "former"}]
    assert linked["linked_intent"][0]["plan_ref"] == "plan"
    with tempfile.TemporaryDirectory() as temporary:
        store, scope, world_store = initialized(Path(temporary))
        identity = WorldIdentity("match-geo", "perspective-geo", "timeline-main", "world-geo")
        native = {"turn": 40, "year": 2240,
                  "map": {"width": 20, "height": 8, "horizontal_wrap": False},
                  "tiles": [{"tile_id": 20, "x": 0, "y": 2, "visible_now": True,
                             "terrain": "land", "features": []}],
                  "bases": [], "units": [],
                  "factions": [{"id": 1, "faction_ref": "faction-1", "owned": True}]}
        world_store.replace_projection(scope, identity,
            PerspectiveProjector(identity).project(native, observation_sequence=1)["objects"],
            observation_cursor=1, action_revision="receipt-a", continuity="complete",
            journal_head_hash="0" * 64)
        service = WorldService(world_store, scope)
        economy = {"center": {"location_ref": "location-20", "epistemic_status": "conditional",
                              "yields": dict(zip(RESOURCES, (2, 1, 1)))},
                   "squares": squares, "epistemic_status": "conditional"}
        receipt = {"action_revision": "receipt-a", "site_economy": economy,
                   "legal_for_land_colony": True, "legal_for_sea_colony": False}
        query = dict(mode="counterfactual", subject_refs=["location-20"], detail="deep",
                     scenario_json='{"kind":"site_economy","populations":[1]}')
        result = service.query(**query, runtime_base_site_receipts={"location-20": receipt})
        assert result["ok"], result
        assert result["items"][0]["counterfactual"]["population_alternatives"][0]["alternatives"]
        cached = service.query(**query, runtime_base_site_receipts={"location-20": receipt})
        assert cached["valid_while"]["action_revision"] == "receipt-a"
        stale = service.query(**query, runtime_base_site_receipts={
            "location-20": {**receipt, "action_revision": "old"}})
        assert stale["items"][0]["counterfactual"]["coverage"] == "current_native_receipt_unavailable"
        social = service.query(mode="counterfactual", detail="deep",
            scenario_json='{"kind":"social","decision_id":"decision-test","choice_id":"choice-test"}',
            runtime_counterfactual_receipt={"ok": True, "kind": "social", "action_revision": "receipt-a",
                "confirmed_mechanics": {"resulting_ratings": {"support": 2}, "switch_energy_cost": 16},
                "epistemic_status": "conditional", "native_id": 123})
        assert social["items"][0]["confirmed_mechanics"]["switch_energy_cost"] == 16
        assert social["items"][0]["epistemic_status"] == "conditional"
        assert "native_id" not in social["items"][0]
        # Dense receipt detail must share the whole-result ceiling with its
        # attainable outputs. Truncation never means an empty economy is proved.
        dense = {**economy, "squares": [
            {"location_ref": f"dense-{i}", "epistemic_status": "conditional", "workable": True,
             "shared_known_base_refs": [f"base-{j}" for j in range(8)],
             "yields": dict(zip(RESOURCES, (i % 5, (20 - i) % 7, i % 3)))} for i in range(20)],
            "material_facility_unlocks": [{"facility": f"facility-{i}",
                "sample_deltas": [{"location_ref": f"dense-{j}", "before": {"nutrients": 1},
                                   "after": {"nutrients": 2}} for j in range(4)]} for i in range(6)]}
        query_metrics = {}
        for detail, budget in (("compact", 512), ("standard", 2048), ("deep", 3276)):
            started = perf_counter()
            bounded = service.query(**{**query, "detail": detail,
                "scenario_json": '{"kind":"site_economy","populations":[1,2,3,6]}'},
                runtime_base_site_receipts={"location-20": {**receipt, "site_economy": dense}})
            assert estimate_tokens(bounded) <= budget, (detail, estimate_tokens(bounded))
            assert not bounded.get("ok") or bounded.get("items"), bounded
            query_metrics[detail] = {"estimated_tokens": estimate_tokens(bounded),
                                     "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                                     "answer_available": bounded.get("ok") is True}
        nominated = {"choice_ref": {"decision_id": "requested", "choice_id": "requested"},
                     "epistemic_status": "conditional", "total_turns": 3}
        crowded = WorldService._trim({"ok": True, "mode": "counterfactual", "items": [{
            "alternatives": [{"unit_ref": f"own-unit-{i}", "assumptions": ["fixed terrain " * 40]}
                             for i in range(8)] + [nominated]}]}, 512)
        assert nominated in crowded["items"][0]["alternatives"], crowded
        assert crowded["items"][0]["alternatives_truncated"], crowded
    print(json.dumps({"ok": True, "exhaustive_assignment_cases": checked,
                      "stale_evidence_and_closed_parameters": "pass", "world_query_revision_binding": "pass",
                      "dense_site_query_metrics": query_metrics}))


if __name__ == "__main__":
    main()
