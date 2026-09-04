#!/usr/bin/env python3
"""Managed semantic-reference to private opaque-choice boundary regression."""

from __future__ import annotations

import asyncio
import inspect
import json
import os

os.environ.setdefault("SMACX_MANAGED_ATTACHED", "1")

import smacx_mcp


def main() -> int:
    context = {
        "action_revision": "revision-current",
        "by_ref": {
            "own-unit-alpha": 17, "own-unit-beta": 18, "base-alpha": 4,
            "location-front": 321, "contact-raider": 23,
        },
        "objects": {
            "own-unit-alpha": {"kind": "own_unit", "status": "active"},
            "own-unit-beta": {"kind": "own_unit", "status": "active"},
            "base-alpha": {"kind": "base", "status": "active", "fields": {
                "owner_ref": {"source": "owned_state", "epistemic_status": "current"},
            }},
            "location-front": {"kind": "location", "status": "active"},
            "contact-raider": {"kind": "foreign_contact", "status": "active"},
            "contact-lost": {"kind": "foreign_contact", "status": "lost"},
        },
        "reverse_units": {
            17: "own-unit-alpha", 18: "own-unit-beta", 23: "contact-raider",
        },
        "reverse_bases": {4: "base-alpha"},
        "reverse_locations": {321: "location-front"},
    }
    original = smacx_mcp._semantic_selector_context
    smacx_mcp._semantic_selector_context = lambda revision: (
        context if revision == "revision-current"
        else (_ for _ in ()).throw(smacx_mcp.SemanticSelectorError("stale"))
    )
    try:
        resolved, resolved_context = smacx_mcp._resolve_managed_selectors(
            "revision-current", own_unit_ref="own-unit-alpha", base_ref="base-alpha",
            target_location_ref="location-front", target_unit_ref="contact-raider",
        )
        assert resolved == {
            "unit_id": 17, "base_id": 4, "target_tile_id": 321,
            "target_unit_id": 23,
        }
        for bad in ("contact-lost", "contact-other-seat", "own-unit-alpha-old"):
            try:
                smacx_mcp._resolve_managed_selectors(
                    "revision-current", target_unit_ref=bad,
                )
            except smacx_mcp.SemanticSelectorError:
                pass
            else:
                raise AssertionError(f"stale/cross-scope ref resolved: {bad}")
        try:
            smacx_mcp._resolve_managed_selectors(
                "revision-old", own_unit_ref="own-unit-alpha",
            )
        except smacx_mcp.SemanticSelectorError:
            pass
        else:
            raise AssertionError("superseded revision resolved")

        decision_id, choices = smacx_mcp._cache_decision_choices(
            {"match_id": "match", "session_id": "session", "revision": "revision-current"},
            [{
                "command": "airdrop_unit", "unit_id": 17,
                "base_id": 4, "target_unit_id": 23, "target_tile_id": 321,
                "targets": [{
                    "base_id": 4, "target_unit_id": 23,
                    "target_tile_id": 321, "range": 7,
                }],
                "ready_unit_ids": [17, 18],
            }],
            choice_kind="unit_actions",
            choice_arguments={"unit_id": 17, "target_tile_id": 321},
            semantic_context=resolved_context,
        )
        public = choices[0]
        encoded = json.dumps(public, separators=(",", ":"))
        assert public["own_unit_ref"] == "own-unit-alpha"
        assert public["base_ref"] == "base-alpha"
        assert public["target_unit_ref"] == "contact-raider"
        assert public["target_location_ref"] == "location-front"
        assert public["targets"][0]["base_ref"] == "base-alpha"
        assert public["targets"][0]["target_unit_ref"] == "contact-raider"
        assert public["targets"][0]["target_location_ref"] == "location-front"
        assert public["ready_unit_refs"] == ["own-unit-alpha", "own-unit-beta"]
        assert not any(name in encoded for name in (
            "unit_id", "base_id", "target_tile_id", "target_unit_id",
        ))
        private = next(iter(smacx_mcp.DECISION_CACHE[decision_id]["choices"].values()))
        assert private["unit_id"] == 17 and private["target_tile_id"] == 321

        decision_signature = inspect.signature(smacx_mcp.smac_decision)
        choice_signature = inspect.signature(smacx_mcp.smac_choices)
        assert "target_location_ref" in decision_signature.parameters
        assert "target_unit_ref" in decision_signature.parameters
        assert "target_tile_id" not in decision_signature.parameters
        assert "target_unit_id" not in decision_signature.parameters
        assert {"base_ref", "own_unit_ref", "target_location_ref", "target_unit_ref"} \
            <= set(choice_signature.parameters)
        assert not ({"base_id", "unit_id", "target_tile_id", "target_unit_id"}
                    & set(choice_signature.parameters))
        tools = asyncio.run(smacx_mcp.mcp.list_tools())
        serialized = json.dumps([
            tool.model_dump(mode="json") if hasattr(tool, "model_dump") else tool.dict()
            for tool in tools
            if tool.name in {"smac_decision", "smac_choices"}
        ], separators=(",", ":"))
        for raw in ("target_tile_id", "target_unit_id", "base_id", "unit_id"):
            if f'"{raw}"' in serialized:
                raise AssertionError(f"managed tool schema contains {raw}: {serialized}")
    finally:
        smacx_mcp._semantic_selector_context = original

    print(json.dumps({"event": "pass", "payload": {
        "semantic_refs_resolve_privately": True,
        "stale_cross_scope_rejected": True,
        "provider_choice_contains_no_native_selectors": True,
        "opaque_choice_retains_private_native_payload": True,
        "managed_provider_schema_is_semantic": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
