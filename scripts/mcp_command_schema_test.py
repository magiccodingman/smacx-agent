#!/usr/bin/env python3
"""Regression for consequential fields crossing the public MCP boundary."""

from __future__ import annotations

import inspect
import json
import typing

import smacx_mcp


def main() -> int:
    captured: dict[str, object] = {}

    def fake_call(method: str, **payload: object) -> dict[str, object]:
        captured["method"] = method
        captured["payload"] = payload
        return {"ok": False, "error": {"code": "fixture_complete"}}

    original_call = smacx_mcp._call
    original_gap = smacx_mcp._pending_capability_gap
    original_briefing_gate = smacx_mcp._match_briefing_gate
    smacx_mcp._call = fake_call
    smacx_mcp._pending_capability_gap = lambda: None
    smacx_mcp._match_briefing_gate = lambda _match_id, _session_id: None
    try:
        signature = inspect.signature(smacx_mcp.smac_command)
        choice_signature = inspect.signature(smacx_mcp.smac_choices)
        list_signature = inspect.signature(smacx_mcp.smac_list)
        command_values = set(typing.get_args(
            typing.get_type_hints(smacx_mcp.smac_command)["command"],
        ))
        for command_name in {
            "go_to_base", "recover_to_carrier", "board_carrier",
            "automate_air_defense", "respond_to_end_turn_confirmation",
            "set_bombing_run",
            "skip_all_ready_units",
            "respond_to_artifact", "respond_to_nerve_gas",
            "self_destruct_unit",
            "advance_endgame_presentation",
            "set_governor_permission",
            "propose_human_joint_attack",
        }:
            if command_name not in command_values:
                raise AssertionError(f"smac_command does not expose {command_name}")
        if "confirm_attack" not in signature.parameters:
            raise AssertionError("smac_command does not expose confirm_attack")
        if "confirm_defiance" not in signature.parameters:
            raise AssertionError("smac_command does not expose confirm_defiance")
        if "confirm_corner_market" not in signature.parameters:
            raise AssertionError("smac_command does not expose confirm_corner_market")
        if "target_kind" not in signature.parameters:
            raise AssertionError("smac_command does not expose target_kind")
        if "target_faction_id" not in signature.parameters:
            raise AssertionError("smac_command does not expose target_faction_id")
        if "payment" not in signature.parameters:
            raise AssertionError("smac_command does not expose payment")
        if "amount" not in signature.parameters:
            raise AssertionError("smac_command does not expose amount")
        if "confirm_transfer" not in signature.parameters:
            raise AssertionError("smac_command does not expose confirm_transfer")
        if "confirm_vote_commitment" not in signature.parameters:
            raise AssertionError("smac_command does not expose confirm_vote_commitment")
        if "confirm_obliteration" not in signature.parameters:
            raise AssertionError("smac_command does not expose confirm_obliteration")
        if "confirm_destruction" not in signature.parameters:
            raise AssertionError("smac_command does not expose confirm_destruction")
        if "confirm_consume_artifact" not in signature.parameters:
            raise AssertionError("smac_command does not expose confirm_consume_artifact")
        if "confirm_atrocity" not in signature.parameters:
            raise AssertionError("smac_command does not expose confirm_atrocity")
        if "confirm_self_destruct" not in signature.parameters:
            raise AssertionError("smac_command does not expose confirm_self_destruct")
        if "confirm_skip_all_ready" not in signature.parameters:
            raise AssertionError("smac_command does not expose confirm_skip_all_ready")
        if "ready_unit_count" not in signature.parameters:
            raise AssertionError("smac_command does not expose ready_unit_count")
        if "governor_permission" not in signature.parameters:
            raise AssertionError("smac_command does not expose governor_permission")
        if "phase" not in signature.parameters:
            raise AssertionError("smac_command does not expose phase")
        if "target_tile_id" not in signature.parameters:
            raise AssertionError("standalone compatibility target_tile_id is missing")
        if "target_unit_id" not in signature.parameters:
            raise AssertionError("standalone compatibility target_unit_id is missing")
        required_semantic = {
            "base_ref", "own_unit_ref", "target_location_ref", "target_unit_ref",
        }
        if not required_semantic <= set(choice_signature.parameters):
            raise AssertionError("managed semantic choice selectors are incomplete")
        if {"base_id", "unit_id", "target_tile_id", "target_unit_id"} \
                & set(choice_signature.parameters):
            raise AssertionError("managed choice schema leaked native selectors")
        if "center_tile_id" not in list_signature.parameters:
            raise AssertionError("coordinate-free center_tile_id is missing")
        for public_signature in (signature, choice_signature, list_signature):
            if "x" in public_signature.parameters or "y" in public_signature.parameters:
                raise AssertionError("public MCP schema must not expose map x/y parameters")
        smacx_mcp.smac_command(
            command="respond_to_combat_confirmation",
            match_id="match-test",
            session_id="session-test",
            expected_revision="revision-test",
            response="proceed",
            phase="credits",
            confirm_attack=1,
            confirm_defiance=1,
            confirm_corner_market=1,
            target_kind="commlink",
            target_faction_id=6,
            payment="energy",
            amount=75,
            confirm_transfer=1,
            confirm_vote_commitment=1,
            confirm_obliteration=1,
            confirm_destruction=1,
            confirm_consume_artifact=1,
            confirm_atrocity=1,
            confirm_self_destruct=1,
            confirm_skip_all_ready=1,
            ready_unit_count=7,
            governor_permission="secret_projects",
            target_tile_id=321,
            target_unit_id=23,
        )
    finally:
        smacx_mcp._call = original_call
        smacx_mcp._pending_capability_gap = original_gap
        smacx_mcp._match_briefing_gate = original_briefing_gate

    payload = captured.get("payload", {})
    if captured.get("method") != "semantic_command":
        raise AssertionError(f"unexpected bridge method: {captured}")
    if not isinstance(payload, dict) or payload.get("confirm_attack") != 1:
        raise AssertionError(f"confirm_attack was not forwarded: {captured}")
    if payload.get("confirm_defiance") != 1:
        raise AssertionError(f"confirm_defiance was not forwarded: {captured}")
    if payload.get("confirm_corner_market") != 1:
        raise AssertionError(f"confirm_corner_market was not forwarded: {captured}")
    if payload.get("confirm_skip_all_ready") != 1:
        raise AssertionError(f"confirm_skip_all_ready was not forwarded: {captured}")
    if payload.get("ready_unit_count") != 7:
        raise AssertionError(f"ready_unit_count was not forwarded: {captured}")
    if payload.get("target_kind") != "commlink":
        raise AssertionError(f"target_kind was not forwarded: {captured}")
    if payload.get("target_faction_id") != 6:
        raise AssertionError(f"target_faction_id was not forwarded: {captured}")
    if payload.get("payment") != "energy":
        raise AssertionError(f"payment was not forwarded: {captured}")
    if payload.get("amount") != 75:
        raise AssertionError(f"amount was not forwarded: {captured}")
    if payload.get("confirm_transfer") != 1:
        raise AssertionError(f"confirm_transfer was not forwarded: {captured}")
    if payload.get("confirm_vote_commitment") != 1:
        raise AssertionError(f"confirm_vote_commitment was not forwarded: {captured}")
    if payload.get("confirm_obliteration") != 1:
        raise AssertionError(f"confirm_obliteration was not forwarded: {captured}")
    if payload.get("confirm_destruction") != 1:
        raise AssertionError(f"confirm_destruction was not forwarded: {captured}")
    if payload.get("confirm_consume_artifact") != 1:
        raise AssertionError(f"confirm_consume_artifact was not forwarded: {captured}")
    if payload.get("confirm_atrocity") != 1:
        raise AssertionError(f"confirm_atrocity was not forwarded: {captured}")
    if payload.get("confirm_self_destruct") != 1:
        raise AssertionError(f"confirm_self_destruct was not forwarded: {captured}")
    if payload.get("governor_permission") != "secret_projects":
        raise AssertionError(f"governor_permission was not forwarded: {captured}")
    if payload.get("phase") != "credits":
        raise AssertionError(f"phase was not forwarded: {captured}")
    if payload.get("target_tile_id") != 321:
        raise AssertionError(f"target_tile_id was not forwarded: {captured}")
    if payload.get("target_unit_id") != 23:
        raise AssertionError(f"target_unit_id was not forwarded: {captured}")

    print(json.dumps({
        "event": "pass",
        "payload": {
            "public_parameter": "confirm_attack",
            "forwarded_value": payload["confirm_attack"],
            "confirm_defiance_forwarded": payload["confirm_defiance"],
            "confirm_corner_market_forwarded": payload["confirm_corner_market"],
            "target_kind_forwarded": payload["target_kind"],
            "target_faction_id_forwarded": payload["target_faction_id"],
            "payment_forwarded": payload["payment"],
            "amount_forwarded": payload["amount"],
            "confirm_transfer_forwarded": payload["confirm_transfer"],
            "confirm_vote_commitment_forwarded": payload["confirm_vote_commitment"],
            "confirm_obliteration_forwarded": payload["confirm_obliteration"],
            "confirm_destruction_forwarded": payload["confirm_destruction"],
            "confirm_consume_artifact_forwarded": payload["confirm_consume_artifact"],
            "confirm_atrocity_forwarded": payload["confirm_atrocity"],
            "confirm_self_destruct_forwarded": payload["confirm_self_destruct"],
            "governor_permission_forwarded": payload["governor_permission"],
            "phase_forwarded": payload["phase"],
            "target_tile_id_forwarded": payload["target_tile_id"],
            "target_unit_id_forwarded": payload["target_unit_id"],
            "public_coordinate_parameters_absent": True,
            "command": payload["command"],
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
