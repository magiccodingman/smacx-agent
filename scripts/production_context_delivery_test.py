#!/usr/bin/env python3
"""Managed production frame keeps native affordability without inventing actions."""
import copy
import json
from unittest.mock import patch
import smacx_mcp as mcp


def main():
    identity = {"match_id": "match-production-context", "session_id": "session-context", "revision": "r1"}
    snapshot = {**identity, "turn": 22, "protocol": {"phase": "turn"}}
    catalog = {**identity, "ok": True, "kind": "production", "base_id": 7,
        "current": {"name": "Colony Pod", "mineral_cost": 30, "minerals_accumulated": 0,
                    "mineral_surplus": 2, "private_extra": "hidden"},
        "hurry": {"legal": True, "affordable": False, "minerals_added": 30,
                  "energy_cost": 120, "available_energy": 58},
        "queue": {"entries": 1, "capacity": 10, "append_command": "private_command"},
        "choices": [{"command": "set_production", "base_id": 7, "item_id": 0, "name": "Colony Pod"}]}
    original = copy.deepcopy(catalog)
    def native(operation, **arguments):
        assert operation in ("semantic_snapshot", "semantic_choices")
        return {"ok": True, "snapshot": snapshot} if operation == "semantic_snapshot" else catalog
    with patch.object(mcp, "_call", side_effect=native), \
         patch.object(mcp, "_resolve_managed_selectors", return_value=({"base_id": 7}, {"base_reverse": {7: "base-public"}})), \
         patch.object(mcp, "_pending_capability_gap", return_value=None), \
         patch.object(mcp, "_match_briefing_gate", return_value=None):
        frame = mcp.smac_choices(kind="production", base_ref="base-public")
    assert frame["ok"], frame
    context = frame["production_context"]
    assert context["hurry"] == catalog["hurry"]
    assert context["current"]["name"] == "Colony Pod"
    assert len(frame["choices"]) == 1
    assert "hidden" not in json.dumps(context) and "private_command" not in json.dumps(context)
    assert catalog == original
    assert mcp._production_catalog_context({}) == {}, "missing evidence became a false fact"
    # Native receipts can contain private own-entity slots. Strip these only
    # after _execute_choice_once (which journals the raw receipt) returns.
    raw = {"ok": True, "base_id": 7, "unit_id": 42,
           "nested": {"target_unit_id": 55}, "name": "Colony Pod", "completed": True}
    saved = copy.deepcopy(raw)
    with patch.object(mcp, "_sovereign_gameplay_gate", return_value=None), \
         patch.object(mcp, "_execute_choice_once", return_value=raw):
        receipt = mcp.smac_execute_choice("unknown-test-decision", "choice-test")
    assert receipt["name"] == "Colony Pod" and receipt["completed"]
    assert not mcp._choice_contains_private_selector(receipt)
    assert raw == saved
    print(json.dumps({"passed": True, "native_affordability_delivered": True,
        "no_added_actions_or_fabricated_defaults": True, "private_receipt_slots_removed": True}))


if __name__ == "__main__":
    main()
