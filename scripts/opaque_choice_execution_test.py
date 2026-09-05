#!/usr/bin/env python3
"""Contract regression for server-owned, single-use gameplay choices."""

from __future__ import annotations

from pathlib import Path
import tempfile

import smacx_mcp


def main() -> int:
    calls: list[tuple[str, dict]] = []
    original_call = smacx_mcp._call
    original_gap = smacx_mcp._pending_capability_gap
    original_briefing = smacx_mcp._match_briefing_gate
    original_journal = smacx_mcp.controller_record_campaign_action
    original_gap_log = smacx_mcp.GAP_LOG
    temporary = tempfile.TemporaryDirectory(prefix="smacx-opaque-choice-")
    try:
        smacx_mcp.GAP_LOG = Path(temporary.name) / "capability-gaps.jsonl"
        smacx_mcp._pending_capability_gap = lambda: None
        smacx_mcp._match_briefing_gate = lambda _match, _session: None
        smacx_mcp.controller_record_campaign_action = lambda *args, **kwargs: {
            "ok": True, "journal_event_id": "journal-test",
        }

        def bridge(operation: str, **arguments: object) -> dict:
            calls.append((operation, dict(arguments)))
            if operation == "semantic_command":
                return {"ok": True, "changed": True, "revision": "r2"}
            if operation == "semantic_snapshot":
                return {"ok": True, "snapshot": {"turn": 2, "year": 2102, "revision": "r2"}}
            raise AssertionError(f"unexpected operation {operation}")

        smacx_mcp._call = bridge
        decision_id, choices = smacx_mcp._cache_decision_choices(
            {"match_id": "match-test", "session_id": "session-test", "revision": "r1"},
            [{
                "id": "native:7", "command": "respond_to_combat_confirmation",
                "response": "proceed", "confirm_attack": 1,
                "target_unit_id": 44, "risk": "may_start_war",
            }],
            choice_kind="interaction", choice_arguments={},
            focus={"kind": "interaction"},
        )
        public = choices[0]
        if "command" in public or "action" in public or "confirm_attack" in public:
            raise AssertionError(f"private command details leaked: {public}")
        if public.get("label") != "Respond to combat confirmation":
            raise AssertionError(f"semantic label missing: {public}")

        result = smacx_mcp.smac_execute_choice(decision_id, str(public["choice_id"]))
        if not result.get("ok") \
                or result.get("executed_choice", {}).get("label") != "Respond to combat confirmation" \
                or "selected_action" in result or "command" in result:
            raise AssertionError(f"opaque execution failed: {result}")
        operation, payload = calls[0]
        if operation != "semantic_command" or payload.get("confirm_attack") != 1 \
                or payload.get("target_unit_id") != 44 \
                or payload.get("expected_revision") != "r1":
            raise AssertionError(f"server-owned payload was incomplete: {calls[-1]}")

        repeated = smacx_mcp.smac_execute_choice(decision_id, str(public["choice_id"]))
        if repeated.get("error", {}).get("code") != "consumed_decision":
            raise AssertionError(f"decision replay was not rejected: {repeated}")
        invalid_id, _ = smacx_mcp._cache_decision_choices(
            {"match_id": "match-test", "session_id": "session-test", "revision": "r2"},
            [{"command": "end_turn"}], choice_kind="game_management",
            choice_arguments={},
        )
        invalid = smacx_mcp.smac_execute_choice(invalid_id, "choice-not-real")
        if invalid.get("error", {}).get("code") != "invalid_choice":
            raise AssertionError(f"invented choice was not rejected: {invalid}")

        base_id, base_choices = smacx_mcp._cache_decision_choices(
            {"match_id": "match-test", "session_id": "session-test", "revision": "r2"},
            [{"command": "set_first_base_name", "suggested_name": "Safe Landing"}],
            choice_kind="interaction", choice_arguments={"base_id": 6},
        )
        base_choice = base_choices[0]
        if base_choice.get("text_input", {}).get("default") != "Safe Landing":
            raise AssertionError(f"opening name contract was not exposed: {base_choice}")
        named = smacx_mcp.smac_execute_choice(base_id, str(base_choice["choice_id"]))
        if not named.get("ok"):
            raise AssertionError(f"suggested opening base name was not executable: {named}")
        _, named_payload = calls[-2]
        if named_payload.get("name") != "Safe Landing" or named_payload.get("base_id") != 6:
            raise AssertionError(f"choice text/default selector binding failed: {named_payload}")

        rename_id, rename_choices = smacx_mcp._cache_decision_choices(
            {"match_id": "match-test", "session_id": "session-test", "revision": "r2"},
            [{"command": "rename_base", "parameters": {
                "name": {"type": "string", "min_length": 1, "max_length": 24},
            }}], choice_kind="base_management", choice_arguments={"base_id": 6},
        )
        missing_name = smacx_mcp.smac_execute_choice(
            rename_id, str(rename_choices[0]["choice_id"]),
        )
        if missing_name.get("error", {}).get("code") != "invalid_choice_text":
            raise AssertionError(f"missing required choice text was not rejected: {missing_name}")
        renamed = smacx_mcp.smac_execute_choice(
            rename_id, str(rename_choices[0]["choice_id"]), text="Second Harbor",
        )
        if not renamed.get("ok") or calls[-2][1].get("name") != "Second Harbor":
            raise AssertionError(f"bounded custom choice text failed: {renamed}")

        disband_id, disband_choices = smacx_mcp._cache_decision_choices(
            {"match_id": "match-test", "session_id": "session-test", "revision": "r2"},
            [{"command": "disband_unit", "unit_id": 12, "destructive": True,
              "requires": {"confirm_disband": 1}}],
            choice_kind="unit_actions", choice_arguments={"unit_id": 12},
        )
        if "requires" in disband_choices[0] or "confirm_disband" in disband_choices[0]:
            raise AssertionError(f"native confirmation leaked through opaque choice: {disband_choices[0]}")
        disbanded = smacx_mcp.smac_execute_choice(
            disband_id, str(disband_choices[0]["choice_id"]),
        )
        if not disbanded.get("ok") or calls[-2][1].get("confirm_disband") != 1:
            raise AssertionError(f"server-owned nested confirmation was omitted: {disbanded}")
        raw_key = smacx_mcp._choice_semantic_key({
            "command": "disband_unit", "unit_id": 12,
            "requires": {"confirm_disband": 1},
        })
        bound_key = smacx_mcp._choice_semantic_key({
            "command": "disband_unit", "unit_id": 12,
            "confirm_disband": 1,
        })
        if raw_key != bound_key:
            raise AssertionError("nested confirmation changed stale-rebase semantics")

        unresolved_id, unresolved_choices = smacx_mcp._cache_decision_choices(
            {"match_id": "match-test", "session_id": "session-test", "revision": "r2"},
            [{
                "command": "set_base_governor",
                "label": "Configure governor",
                "parameters": {
                    "active": {"type": "integer"},
                    "manage_citizens": {"type": "integer"},
                },
            }],
            choice_kind="base_management",
            choice_arguments={"base_id": 7},
            focus={"kind": "base_management", "base_id": 7},
        )
        if unresolved_choices or smacx_mcp.DECISION_CACHE[unresolved_id]["choices"]:
            raise AssertionError(
                "an unresolved native parameter schema was exposed as executable"
            )

        settlement_id, settlement_choices = smacx_mcp._cache_decision_choices(
            {"match_id": "match-settlement", "session_id": "session-settlement",
             "revision": "r-settlement"},
            [
                {"command": "move_unit", "unit_id": 14, "target_tile_id": 812},
                {"id": "settlement:unavailable:14", "kind": "rule_status",
                 "available": False, "unit_id": 14,
                 "reason": "too_close_to_known_base", "minimum_base_range": 3,
                 "nearest_known_base_range": 2,
                 "meaning": "The native rules do not allow this Colony Pod to found a base "
                            "on its current tile. Move it and request fresh unit choices."},
            ],
            choice_kind="unit_actions", choice_arguments={"unit_id": 14},
            focus={"kind": "unit_actions"}, turn=9, year=2109, phase="turn",
        )
        if len(settlement_choices) != 1:
            raise AssertionError("rule status was incorrectly exposed as an executable choice")
        advisories = smacx_mcp.DECISION_CACHE[settlement_id].get("advisories", [])
        if not advisories or advisories[0].get("reason") != "too_close_to_known_base":
            raise AssertionError(f"native settlement explanation was lost: {advisories}")
        smacx_mcp._call = lambda operation, **arguments: (
            {"ok": True, "snapshot": {
                "match_id": "match-settlement", "session_id": "session-settlement",
                "revision": "r-settlement", "turn": 9, "year": 2109,
                "protocol": {"phase": "turn"},
            }} if operation == "semantic_snapshot" else
            (_ for _ in ()).throw(AssertionError(f"unexpected operation {operation}"))
        )
        false_gap = smacx_mcp.smac_report_capability_gap(
            "Colony Pod cannot settle here", "Expand to a second base",
            "No found-base choice is visible", "found_base",
            "The current tile does not expose the action",
        )
        if false_gap.get("error", {}).get("code") != \
                "native_rule_explains_unavailable_action" \
                or false_gap.get("recorded") is not False:
            raise AssertionError(f"native rule was misreported as a capability gap: {false_gap}")

        # The same accept action on different displayed agreement terms is
        # meaningful progress; identical terms remain a repeated state.
        fingerprints = []
        for amount in (37, 50, 50):
            frame_id, _ = smacx_mcp._cache_decision_choices(
                {"match_id": "terms", "session_id": "terms", "revision": "r1"},
                [{"command": "respond_human_diplomacy", "response": "accept"}],
                choice_kind="interaction", choice_arguments={},
                turn=1, year=2101, phase="interaction",
                catalog_information={"clauses": [{"kind": "energy", "amount": amount}]},
            )
            fingerprints.append(smacx_mcp.DECISION_CACHE[frame_id]["state_fingerprint"])
        assert fingerprints[0] != fingerprints[1] == fingerprints[2], fingerprints

        smacx_mcp.ACTION_PROGRESS.clear()
        smacx_mcp._call = lambda operation, **arguments: (
            {"ok": True, "changed": True} if operation == "semantic_command" else
            {"ok": True, "snapshot": {
                "turn": 3, "year": 2103, "revision": "r3",
                "protocol": {"phase": "wait"},
            }}
        )
        end_id, end_choices = smacx_mcp._cache_decision_choices(
            {"match_id": "match-test", "session_id": "session-test", "revision": "r2"},
            [{"command": "end_turn"}], choice_kind="game_management",
            choice_arguments={}, turn=2, year=2102, phase="turn",
        )
        ended = smacx_mcp.smac_execute_choice(
            end_id, str(end_choices[0]["choice_id"]),
        )
        handoff = ended.get("turn_handoff_required", {})
        if handoff.get("required") is not True or handoff.get("maximum_words") != 120:
            raise AssertionError(f"native turn end omitted bounded handoff: {ended}")

        # Native automation can return control directly on the following turn
        # without exposing a wait phase or an end_turn response. The next
        # decision must become a handoff boundary before it offers choices.
        smacx_mcp.TURN_HANDOFF_STATE.clear()
        boundary_identity = {"match_id": "match-boundary", "session_id": "session-boundary"}
        if smacx_mcp._implicit_turn_handoff(
                {"turn": 5, "year": 2105, "protocol": {"phase": "turn"}},
                boundary_identity) is not None:
            raise AssertionError("the first observed native turn was mistaken for a boundary")
        implicit = smacx_mcp._implicit_turn_handoff(
            {"turn": 6, "year": 2106, "protocol": {"phase": "interaction"}},
            boundary_identity,
        )
        if implicit is None or implicit.get("choices") != [] \
                or implicit.get("required_next", {}).get("stop_after") is not True \
                or implicit.get("turn_handoff_required", {}).get("completed_turns") != "5":
            raise AssertionError(f"implicit native turn boundary was not fenced: {implicit}")
        if smacx_mcp._implicit_turn_handoff(
                {"turn": 6, "year": 2106, "protocol": {"phase": "interaction"}},
                boundary_identity) is not None:
            raise AssertionError("one native boundary requested duplicate handoffs")

        # An explicit handoff for turn 2 must not be repeated merely because
        # the next episode first observes turn 3.
        smacx_mcp.TURN_HANDOFF_STATE.clear()
        explicit_identity = {"match_id": "match-explicit", "session_id": "session-explicit"}
        smacx_mcp._track_observed_turn(explicit_identity, 2)
        explicit_response: dict = {}
        smacx_mcp._attach_turn_handoff(
            explicit_response, {"command": "end_turn"},
            {"identity": explicit_identity, "turn": 2},
            {"turn": 2, "year": 2102, "protocol": {"phase": "wait"}},
        )
        if "turn_handoff_required" not in explicit_response or smacx_mcp._implicit_turn_handoff(
                {"turn": 3, "year": 2103, "protocol": {"phase": "turn"}},
                explicit_identity) is not None:
            raise AssertionError("explicit native turn handoff was duplicated on reacquisition")

        smacx_mcp.ACTION_PROGRESS.clear()
        smacx_mcp._call = lambda operation, **arguments: (
            {"ok": True, "snapshot": {
                "match_id": "match-loop", "session_id": "session-loop",
                "revision": "r5", "turn": 1,
                "protocol": {"phase": "interaction"},
            }} if operation == "semantic_snapshot" else
            {"ok": False, "error": {"code": "native_action_rejected"}}
        )
        third = None
        before = len(calls)
        for revision in ("r3", "r4", "r5"):
            loop_id, loop_choices = smacx_mcp._cache_decision_choices(
                {"match_id": "match-loop", "session_id": "session-loop", "revision": revision},
                [{"command": "acknowledge_popup"}], choice_kind="interaction",
                choice_arguments={}, focus={"kind": "interaction", "label": "COOPERATE"},
                turn=1, year=2101, phase="interaction",
            )
            third = smacx_mcp.smac_execute_choice(
                loop_id, str(loop_choices[0]["choice_id"]),
            )
        if third is None or third.get("error", {}).get("code") != "repetition_circuit_open" \
                or third.get("native_action_executed") is not False:
            raise AssertionError(f"no-progress choice circuit did not open: {third}")
        if ("match-loop", "session-loop") not in smacx_mcp.RUNTIME_CIRCUITS:
            raise AssertionError("runtime circuit was not latched")
        if not third.get("capability_gap") or not smacx_mcp.GAP_LOG.is_file():
            raise AssertionError("runtime circuit was not automatically reported")

        smacx_mcp.ACTION_PROGRESS.clear()
        smacx_mcp.RUNTIME_CIRCUITS.clear()
        smacx_mcp.CAPABILITY_GAPS.clear()
        smacx_mcp.controller_record_campaign_action = lambda *args, **kwargs: {
            "ok": False, "error": "simulated_journal_failure",
        }
        smacx_mcp._call = lambda operation, **arguments: (
            {"ok": True, "changed": True}
            if operation == "semantic_command" else
            {"ok": True, "snapshot": {
                "match_id": "match-durable", "session_id": "session-durable",
                "revision": "r7", "turn": 4, "year": 2104,
                "protocol": {"phase": "turn"},
            }}
        )
        durable_id, durable_choices = smacx_mcp._cache_decision_choices(
            {"match_id": "match-durable", "session_id": "session-durable", "revision": "r6"},
            [{"command": "skip_unit", "unit_id": 3}],
            choice_kind="unit_actions", choice_arguments={"unit_id": 3},
            focus={"kind": "unit", "unit_id": 3}, turn=4, year=2104,
            phase="turn",
        )
        durability = smacx_mcp.smac_execute_choice(
            durable_id, str(durable_choices[0]["choice_id"]),
        )
        if durability.get("error", {}).get("code") != "campaign_journal_write_failed" \
                or durability.get("native_action_executed") is not True \
                or ("match-durable", "session-durable") not in smacx_mcp.RUNTIME_CIRCUITS:
            raise AssertionError(f"journal failure did not stop further mutation: {durability}")
    finally:
        smacx_mcp._call = original_call
        smacx_mcp._pending_capability_gap = original_gap
        smacx_mcp._match_briefing_gate = original_briefing
        smacx_mcp.controller_record_campaign_action = original_journal
        smacx_mcp.GAP_LOG = original_gap_log
        smacx_mcp.DECISION_CACHE.clear()
        smacx_mcp.ACTION_PROGRESS.clear()
        smacx_mcp.RUNTIME_CIRCUITS.clear()
        smacx_mcp.TURN_HANDOFF_STATE.clear()
        temporary.cleanup()
    print("opaque choice execution tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
