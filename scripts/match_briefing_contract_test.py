#!/usr/bin/env python3
"""Contract for the live settings briefing and exact acknowledgement gate."""

from __future__ import annotations

import json

import smacx_mcp


SNAPSHOT = {
    "match_id": "match-briefing-test",
    "session_id": "session-briefing-test",
    "revision": "revision-briefing-test",
    "turn": 0,
    "year": 2100,
    "faction": {"id": 8, "name": "Data Angels"},
    "game_settings": {
        "difficulty": {"id": 1, "name": "Specialist"},
        "victories": {
            "transcendence": True, "conquest": True,
            "diplomatic": False, "economic": False, "cooperative": False,
        },
        "rules": {"blind_research": True, "tech_stagnation": True},
        "world": {"size": 0, "ocean_coverage": 2},
        "time_control": {"id": 1, "name": "Timed Turn"},
    },
    "scenario": {"scenario_id": "scenario-contract", "restrictions": ["fixed_factions"]},
    "protocol": {"phase": "turn", "required_action": "choose_unit_action"},
    "ready_unit_refs": [],
}


def main() -> int:
    originals = {
        "call": smacx_mcp._call,
        "context": smacx_mcp.controller_match_briefing_context,
        "check": smacx_mcp.controller_match_briefing_is_acknowledged,
        "status": smacx_mcp.controller_match_briefing_acknowledgement_status,
        "ack": smacx_mcp.controller_acknowledge_match_briefing,
        "chat": smacx_mcp.controller_chat_attention,
        "journal": smacx_mcp.controller_record_campaign_action,
    }
    acknowledged: dict[str, str] = {}
    commands: list[dict] = []
    try:
        def call(operation: str, **arguments: object) -> dict:
            if operation == "semantic_snapshot":
                return {"ok": True, "snapshot": dict(SNAPSHOT)}
            if operation == "semantic_choices":
                return {
                    "ok": True, "match_id": SNAPSHOT["match_id"],
                    "session_id": SNAPSHOT["session_id"], "revision": SNAPSHOT["revision"],
                    "choices": [{"command": "end_turn"}],
                }
            if operation == "semantic_command":
                commands.append(dict(arguments))
                return {"ok": True, "executed": arguments.get("command")}
            raise AssertionError(operation)

        smacx_mcp._call = call
        smacx_mcp.controller_chat_attention = lambda match_id, session_id: {
            "ok": True, "messages": [], "participants": [],
        }
        smacx_mcp.controller_record_campaign_action = lambda *args, **kwargs: {
            "ok": True, "journal_event_id": "journal-briefing-test",
        }
        smacx_mcp.controller_match_briefing_context = lambda match_id, session_id: {
            "ok": True,
            "scope": {
                "agent_id": "agent-briefing-test",
                "perspective_id": "perspective-briefing-test",
            },
            "match": {
                "display_name": "Briefing test", "mode": "lan",
                "ruleset_id": "smacx", "status": "running",
            },
            "seat": {
                "seat_index": 3, "controller_kind": "agent",
                "assigned_faction_id": 8, "assigned_faction_name": "Data Angels",
            },
            "policy": {"ranking_mode": "unranked", "managed_clients_only": False},
            "requested_settings": {"difficulty": 1, "victory_diplomatic": False},
            "game_source": {"game_source_id": "game-source-test", "executable_sha256": "c" * 64},
            "reference_topics": [{"topic": "setup", "document_count": 4}],
            "reference_status": "ready",
        }
        smacx_mcp.controller_match_briefing_is_acknowledged = \
            lambda match_id, session_id, briefing_hash: briefing_hash in acknowledged
        smacx_mcp.controller_match_briefing_acknowledgement_status = \
            lambda match_id, session_id, briefing_hash: {
                "ok": True,
                "acknowledged": briefing_hash in acknowledged,
                "current_session": acknowledged.get(briefing_hash) == session_id,
                "previous_briefing_hash": next(reversed(acknowledged), None),
                "previous_session_id": (
                    acknowledged.get(next(reversed(acknowledged), ""))
                    if acknowledged else None
                ),
            }

        def acknowledge(match_id: str, session_id: str, briefing_hash: str) -> dict:
            acknowledged[briefing_hash] = session_id
            return {"ok": True, "acknowledgement": {
                "briefing_hash": briefing_hash, "acknowledged_unix": 1234.5,
            }}

        smacx_mcp.controller_acknowledge_match_briefing = acknowledge
        smacx_mcp.MATCH_BRIEFING_CACHE.clear()
        smacx_mcp.MATCH_CONFIGURATION_CACHE.clear()
        smacx_mcp.MATCH_BRIEFING_RESUME_NOTICES.clear()

        locked = smacx_mcp.smac_decision()
        if locked.get("kind") != "match_briefing_required" or locked.get("choices") != []:
            raise AssertionError(f"decision surface was not locked: {locked}")
        direct = smacx_mcp.smac_command(
            command="end_turn", match_id=SNAPSHOT["match_id"],
            session_id=SNAPSHOT["session_id"], expected_revision=SNAPSHOT["revision"],
        )
        if direct.get("error", {}).get("code") != "match_briefing_required" or commands:
            raise AssertionError(f"direct mutation bypassed briefing gate: {direct}")

        briefing = smacx_mcp.smac_match_briefing("read")
        body = briefing.get("briefing", {})
        configuration = body.get("configuration", {})
        if configuration.get("native_game_settings") != SNAPSHOT["game_settings"] \
                or configuration.get("scenario") != SNAPSHOT["scenario"] \
                or configuration.get("seat", {}).get("faction_id") != 8:
            raise AssertionError(f"authoritative settings were omitted: {briefing}")
        if len(json.dumps(briefing, separators=(",", ":"))) > 3000:
            raise AssertionError(f"opening briefing exceeded compact budget: {briefing}")
        stale = smacx_mcp.smac_match_briefing("acknowledge", "f" * 64)
        if stale.get("error", {}).get("code") != "stale_match_briefing":
            raise AssertionError(f"stale acknowledgement was accepted: {stale}")
        accepted = smacx_mcp.smac_match_briefing(
            "acknowledge", str(briefing["briefing_hash"]),
        )
        if not accepted.get("acknowledged") or "briefing" in accepted \
                or len(json.dumps(accepted, separators=(",", ":"))) > 700:
            raise AssertionError(f"current briefing was not acknowledged: {accepted}")
        unlocked = smacx_mcp.smac_decision()
        if unlocked.get("required_next", {}).get("tool") != "smac_execute_choice":
            raise AssertionError(f"decision surface did not unlock: {unlocked}")
        executed = smacx_mcp.smac_execute_choice(
            decision_id=str(unlocked["decision_id"]),
            choice_id=str(unlocked["choices"][0]["choice_id"]),
        )
        if not executed.get("ok") or len(commands) != 1:
            raise AssertionError(f"acknowledged mutation did not execute: {executed}")
        original_energy = SNAPSHOT["faction"].get("energy_credits", 0)
        stable_hash = briefing["briefing_hash"]
        for turn in range(1, 101):
            SNAPSHOT["turn"] = turn
            SNAPSHOT["year"] = 2100 + turn
            SNAPSHOT["revision"] = f"revision-{turn}"
            SNAPSHOT["faction"].update({
                "energy_credits": original_energy + turn,
                "bases": 1 + turn // 10,
                "units": 1 + turn,
                "ready_units": turn % 7,
            })
            stable = smacx_mcp.smac_decision()
            if stable.get("kind") == "match_briefing_required" \
                    or stable.get("configuration_hash") != stable_hash:
                raise AssertionError(f"runtime state invalidated briefing: {stable}")

        original_difficulty = SNAPSHOT["game_settings"]["difficulty"]
        SNAPSHOT["game_settings"]["difficulty"] = {"id": 5, "name": "Transcend"}
        changed = smacx_mcp.smac_decision()
        if changed.get("kind") != "match_briefing_required" \
                or changed.get("briefing_hash") == briefing.get("briefing_hash"):
            raise AssertionError(f"changed native settings did not invalidate acknowledgement: {changed}")
        delta = changed.get("change", {})
        if delta.get("kind") != "configuration_changed" or not any(
                item.get("field") == "native_game_settings.difficulty.id"
                for item in delta.get("changes", [])):
            raise AssertionError(f"configuration delta was not precise: {changed}")
        SNAPSHOT["game_settings"]["difficulty"] = original_difficulty

        # A recovered process with the same durable configuration receives one
        # compact notice and remains unlocked under its new command session.
        SNAPSHOT["session_id"] = "session-briefing-resumed"
        resumed = smacx_mcp.smac_decision()
        if resumed.get("resume_notice", {}).get("configuration_unchanged") is not True \
                or resumed.get("kind") == "match_briefing_required":
            raise AssertionError(f"same-configuration recovery relocked play: {resumed}")
        repeated = smacx_mcp.smac_decision()
        if "resume_notice" in repeated:
            raise AssertionError(f"resume notice repeated: {repeated}")
        smacx_mcp.MATCH_BRIEFING_CACHE.clear()
        smacx_mcp.MATCH_CONFIGURATION_CACHE.clear()
        smacx_mcp.MATCH_BRIEFING_RESUME_NOTICES.clear()
        sidecar_recovered = smacx_mcp.smac_decision()
        if "resume_notice" in sidecar_recovered \
                or sidecar_recovered.get("kind") == "match_briefing_required":
            raise AssertionError(
                f"carried acknowledgement did not survive MCP recovery: {sidecar_recovered}"
            )
    finally:
        smacx_mcp._call = originals["call"]
        smacx_mcp.controller_match_briefing_context = originals["context"]
        smacx_mcp.controller_match_briefing_is_acknowledged = originals["check"]
        smacx_mcp.controller_match_briefing_acknowledgement_status = originals["status"]
        smacx_mcp.controller_acknowledge_match_briefing = originals["ack"]
        smacx_mcp.controller_chat_attention = originals["chat"]
        smacx_mcp.controller_record_campaign_action = originals["journal"]
        smacx_mcp.MATCH_BRIEFING_CACHE.clear()
        smacx_mcp.MATCH_CONFIGURATION_CACHE.clear()
        smacx_mcp.MATCH_BRIEFING_RESUME_NOTICES.clear()
    print(json.dumps({
        "event": "pass",
        "payload": {
            "live_native_settings_included": True,
            "scenario_restrictions_included": True,
            "exact_hash_required": True,
            "decisions_and_mutations_locked": True,
            "acknowledgement_unlocks_current_session": True,
            "runtime_state_does_not_invalidate": True,
            "settings_change_invalidates_acknowledgement": True,
            "configuration_delta_is_precise": True,
            "same_configuration_recovery_stays_unlocked": True,
            "resume_notice_is_once_per_session": True,
            "resume_acknowledgement_is_carried_forward": True,
            "acknowledgement_is_compact": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
