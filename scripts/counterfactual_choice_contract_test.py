#!/usr/bin/env python3
"""Opaque preview authorization; fixtures do not prove native predictions."""

import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import smacx_mcp as mcp
from smacx_world import WorldQueryError
from smacx_plan_health import plan_health
from smacx_counterfactual import action_relationships


def rejects(fn):
    try:
        fn()
    except WorldQueryError:
        return
    raise AssertionError("invalid preview authorization accepted")


def main():
    semantic = mcp._semanticize_choice({"command": "move_unit", "unit_id": 7},
                                     {"reverse_units": {7: "own-unit-8"}})
    linked = action_relationships({}, semantic,
        [{"plan_id": "journal-plan", "participants": [{"ref": "own-unit-8"}]}])
    assert linked["subject_ref"] == "own-unit-8" and linked["linked_intent"][0]["plan_ref"] == "journal-plan"
    scope = ("match-preview", "session-preview", "agent-preview", "perspective-preview")
    with patch.object(mcp, "_managed_scope_identity", return_value=scope):
        decision, choices = mcp._cache_decision_choices(
            {"match_id": scope[0], "session_id": scope[1], "revision": "r1"},
            [{"command": "set_social_engineering", "politics": 0, "economics": 1,
              "values": 2, "future": 0}], choice_kind="social_engineering", choice_arguments={})
        scenario = {"kind": "social", "decision_id": decision, "choice_id": choices[0]["choice_id"]}
        for _ in range(2):
            choice = mcp._counterfactual_choice(scenario, "r1")
            assert choice["politics"] == 0
            assert not mcp.DECISION_CACHE[decision]["consumed"]
            choice["politics"] = 3  # Returned copies cannot rewrite execution.
        rejects(lambda: mcp._counterfactual_choice(scenario, "r2"))
        rejects(lambda: mcp._counterfactual_choice({**scenario, "kind": "terraform"}, "r1"))
        rejects(lambda: mcp._counterfactual_choice({**scenario, "choice_id": "invented"}, "r1"))
        with patch.object(mcp, "_managed_scope_identity", return_value=(*scope[:2], "other-agent", scope[3])):
            rejects(lambda: mcp._counterfactual_choice(scenario, "r1"))
        mcp.DECISION_CACHE[decision]["consumed"] = True
        rejects(lambda: mcp._counterfactual_choice(scenario, "r1"))
        mcp.DECISION_CACHE[decision]["consumed"] = False
        mcp.DECISION_CACHE[decision]["created_monotonic"] = time.monotonic() - mcp.DECISION_TTL_SECONDS - 1
        rejects(lambda: mcp._counterfactual_choice(scenario, "r1"))
        mcp.DECISION_CACHE.pop(decision)
    # The journal can become available before the restored world collector.
    # A first plan-health query must reconcile the native-owned participant,
    # not report it absent until some unrelated world query warms the view.
    projection = {"world_revision": 1, "objects": []}
    plans = [{"plan_id": "restored-plan", "participants": [{"ref": "restored-unit"}]}]
    def refresh():
        projection["objects"] = [{"object_ref": "restored-unit", "kind": "own_unit", "fields": {}}]
        return {"ok": True}
    world = SimpleNamespace(_projection=lambda: (SimpleNamespace(world_epoch="epoch"), projection))
    attention = SimpleNamespace(semantic_dependency_hashes=lambda projection: {},
        runtime_state=lambda **kwargs: {"operations": []},
        plan_health=lambda projection, operations, ready, dependencies: plan_health(plans,
            {row["object_ref"]: row for row in projection["objects"]}, operations, ready, set(), complete=True))
    with patch.object(mcp, "_refresh_managed_world", side_effect=refresh), \
            patch.object(mcp, "controller_world_service", return_value=(None, world, attention)), \
            patch.object(mcp, "_call", return_value={"snapshot": {"ready_unit_refs": []}}):
        result = mcp.smac_cognition(action="plan_health")
        assert result["plan_health"]["assigned_owned_unit_count"] == 1, result
        assert result["plan_health"]["intent_coverage_complete"], result
    with patch.object(mcp, "_refresh_managed_world", return_value={"ok": False, "error": "observer_unavailable"}):
        assert mcp.smac_cognition(action="plan_health") == {"ok": False, "error": "observer_unavailable"}
    print(json.dumps({"ok": True, "choice_preview_scope_revision_ttl_consumption": "pass",
                      "preview_does_not_consume_or_modify_choice": "pass",
                      "first_plan_health_query_reconciles_restored_world": "pass"}))


if __name__ == "__main__":
    main()
