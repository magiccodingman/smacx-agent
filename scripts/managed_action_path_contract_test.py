#!/usr/bin/env python3
"""Native-shaped adapter evidence for staged choices; live proof is separate."""
import copy
import json
from unittest.mock import patch

import smacx_mcp as mcp
from smacx_choice_preparation import ChoicePreparations, PreparationError


def main():
    support = {"epistemic_status": "conditional", "current_support_minerals": 2,
               "free_supported_units": 2, "current_eligible_units": 4,
               "gross_mineral_output": 2, "additional_support_minerals": 1,
               "support_after_one_completion": 3, "exceeds_current_gross_output": True,
               "condition": "Current population and mineral output remain unchanged. " * 6}
    assert mcp._production_catalog_context({"support_projection": support})["support_projection"] == support
    refused = {"action_id": 1, "status": "rejected", "native_call_attempted": True,
               "resolution": "native_turn_transition_not_accepted"}
    with patch.object(mcp, "_call", return_value={"ok": True, "action": refused}) as poll:
        result = mcp._await_deferred_action({"ok": True, "queued": True,
                                           "command": "end_turn", "action_id": 1})
        assert not result["ok"] and result["error"]["code"] == "native_action_rejected"
        assert result["execution"] == refused and poll.call_count == 1
        assert "movement has not been renewed" in result["error"]["message"]
        assert "do not keep waiting" in result["error"]["message"]
    transient={"ok":False,"error":{"code":"invalid_semantic_selector",
        "detail":"semantic_reference_stale_revision"}}
    with patch.object(mcp,"_smac_choices_once",side_effect=[transient,{"ok":True,"choices":[]}]) as enumerate_once:
        assert mcp.smac_choices("interaction")["ok"]
        assert enumerate_once.call_count==2
    with patch.object(mcp,"_smac_choices_once",return_value=transient) as enumerate_once:
        result=mcp.smac_choices("interaction")
        assert enumerate_once.call_count==2 and result["state_changed_during_enumeration"]
        assert result["native_action_executed"] is False
    with patch.object(mcp,"_smac_choices_once",return_value=transient) as enumerate_once:
        assert not mcp.smac_choices("interaction",preparation_ref="preparation-existing")["ok"]
        assert enumerate_once.call_count==1
    invalid={"ok":False,"error":{"code":"invalid_semantic_selector","detail":"unknown_reference"}}
    with patch.object(mcp,"_smac_choices_once",return_value=invalid) as enumerate_once:
        assert mcp.smac_choices("unit_actions",own_unit_ref="unknown")==invalid
        assert enumerate_once.call_count==1
    identity = {"match_id": "match-actions", "session_id": "session-actions", "revision": "r1"}
    context = {"identity": {"timeline_id": "timeline-main", "world_epoch": "epoch-one"},
               "action_revision": "r1", "objects": {}, "by_ref": {}}
    effects = []
    injected_catalog = None
    injected_receipt = None
    social = [{"key": key, "options": [{"model_id": value, "name": f"{key} model {value}",
                                        "intrinsic_effects": {"support": value}} for value in (0, 1)]}
              for key in ("politics", "economics", "values", "future")]
    design_catalog = {"mutations_supported": True, "custom_slots": {"available": 4},
                      "available_prototypes": [
                          {"prototype_id": 8, "name": "Existing Scout", "active_unit_count": 2},
                          {"prototype_id": 80, "name": "Replacement", "custom": True}],
                      "catalogs": {key: [{field: 7, "name": key + " option"}]
                                   for key, field in (("chassis", "chassis_id"), ("weapons", "weapon_id"),
                                                      ("armor", "armor_id"), ("reactors", "reactor_id"),
                                                      ("abilities", "ability_id"))}}

    def native(operation, **args):
        if operation == "semantic_snapshot":
            return {"ok": True, "snapshot": {**identity, "turn": 1, "year": 2101}}
        if operation == "semantic_command":
            assert args["expected_revision"] == identity["revision"]
            effects.append(copy.deepcopy(args))
            return copy.deepcopy(injected_receipt) if injected_receipt is not None else {"ok": True, "changed": True}
        assert operation == "semantic_choices", operation
        result = {"ok": True, **identity, "kind": args["kind"], "choices": []}
        if injected_catalog is not None:
            result["choices"] = copy.deepcopy(injected_catalog)
            return result
        if args["kind"] == "unit_design":
            result.update(design_catalog)
            if all(key in args for key in ("chassis_id", "weapon_id", "armor_id", "reactor_id")):
                result["choices"] = [{"command": "create_unit_design", "label": "Create selected design",
                                      **{key: value for key, value in args.items() if key != "kind"},
                                      "parameters": {"name": {"type": "string", "required": False}}}]
            if "source_prototype_id" in args and "target_prototype_id" in args:
                result["choices"] = [{"command": "upgrade_prototype", "confirm_upgrade": 1,
                                      "energy_cost_total": 40, "active_unit_count": 2,
                                      **{key: value for key, value in args.items() if key != "kind"}}]
        elif args["kind"] == "social_engineering":
            result.update(enabled=True, categories=social)
            if all(key in args for key in ("politics", "economics", "values", "future")):
                result["choices"] = [{"command": "set_social_engineering", **{key: value for key, value in args.items() if key != "kind"}}]
        elif args["kind"] == "energy_allocation":
            if all(key in args for key in ("economy", "psych", "labs")):
                result["choices"] = [{"command": "set_energy_allocation", **{key: value for key, value in args.items() if key != "kind"}}]
        elif args["kind"] == "interaction":
            result["choices"] = [{"command": "give_energy_gift", "amount_min": 1, "amount_max": 73, "amount_options": [50, 25],
                                  "meaning": "Give the selected energy credits"}]
        return result

    with patch.object(mcp, "_call", side_effect=native), \
         patch.object(mcp, "_semantic_selector_context", return_value=context), \
         patch.object(mcp, "_sovereign_gameplay_gate", return_value=None), \
         patch.object(mcp, "_refresh_managed_world", return_value={"ok": True}), \
         patch.object(mcp, "_pending_capability_gap", return_value=None), \
         patch.object(mcp, "_match_briefing_gate", return_value=None), \
         patch.object(mcp, "controller_record_campaign_action", return_value={"ok": True}), \
         patch.object(mcp, "CHOICE_PREPARATIONS", ChoicePreparations()):
        for kind in ("unit_design", "social_engineering", "energy_allocation", "interaction"):
            frame = mcp.smac_choices(kind=kind)
            assert frame["ok"] and not frame["choices"], frame
            preparation = frame["preparations"][0]
            first_ref = preparation["preparation_ref"]
            # Plain native integers and arbitrary option strings cannot bypass binding.
            bad = mcp.smac_choices(kind=kind, preparation_ref=first_ref, option_ref="7")
            assert not bad["ok"] and not effects
            while True:
                before = len(effects)
                if "amount_input" in preparation:
                    rejected = mcp.smac_choices(kind=kind, preparation_ref=preparation["preparation_ref"], amount=37)
                    assert not rejected["ok"] and len(effects) == before
                    frame = mcp.smac_choices(kind=kind, preparation_ref=preparation["preparation_ref"], amount=25)
                else:
                    frame = mcp.smac_choices(kind=kind, preparation_ref=preparation["preparation_ref"],
                                             option_ref=preparation["options"][0]["option_ref"])
                assert frame["ok"] and len(effects) == before, frame
                if "preparation" not in frame:
                    break
                preparation = frame["preparation"]
            assert len(frame["choices"]) == 1, frame
            serialized = json.dumps(frame)
            if kind == "unit_design":
                assert all(key not in serialized for key in ("chassis_id", "weapon_id", "armor_id", "reactor_id"))
            choice = frame["choices"][0]
            result = mcp.smac_execute_choice(frame["decision_id"], choice["choice_id"],
                                            text="Bounded Rover" if kind == "unit_design" else "")
            assert result["ok"] and len(effects) == before + 1, result
            effect = effects.pop()
            if kind == "interaction": assert effect["amount"] == 25
            if kind == "unit_design": assert effect["name"] == "Bounded Rover" and effect["chassis_id"] == 7
            if kind == "energy_allocation": assert effect["economy"] + effect["psych"] + effect["labs"] == 10
            assert mcp.smac_execute_choice(frame["decision_id"], choice["choice_id"])["error"]["code"] == "consumed_decision"
            assert not mcp.smac_choices(kind=kind, preparation_ref=first_ref, amount=1)["ok"]

        frame = mcp.smac_choices(kind="unit_design")
        preparation = next(row for row in frame["preparations"] if row["purpose"] == "unit_upgrade")
        for _ in range(2):
            frame = mcp.smac_choices(kind="unit_design", preparation_ref=preparation["preparation_ref"],
                                     option_ref=preparation["options"][0]["option_ref"])
            preparation = frame.get("preparation")
        assert "source_prototype_id" not in json.dumps(frame)
        assert frame["choices"][0]["energy_cost_total"] == 40
        assert mcp.smac_execute_choice(frame["decision_id"], frame["choices"][0]["choice_id"])["ok"]
        assert effects.pop()["source_prototype_id"] == 8

        # These are native-shaped interface proofs, not native gameplay proof.
        # Closed Council/research/human clauses need no arbitrary parameter API.
        for kind, row, information in (
            ("interaction", {"command": "acknowledge_popup"},
             [{"kind": "information", "event": "unit_support_shortage", "base_name": "HQ",
               "unit_name": "Garrison", "effect_status": "forced_disband_pending_native_processing",
               "meaning": "Verify the resulting owned units afterward; this is not a combat loss."}]),
            ("interaction", {"command": "defer_social_engineering", "meaning": "Continue current models; review social_engineering after interactions"},
             [{"kind": "information", "technology_name": "Industrial Economics", "unlocked_model_name": "Free Market"}]),
            ("research", {"command": "choose_research", "tech_id": 12, "name": "Test advance"}, []),
            ("interaction", {"command": "choose_council_proposal", "proposal_id": 3,
                              "ballot": {"type": "yea_nay", "responses": ["yea"]}}, []),
            ("interaction", {"command": "propose_human_technology", "technology_id": 12}, []),
            ("interaction", {"command": "propose_human_relationship", "relationship": "pact"}, []),
            ("interaction", {"command": "respond_human_diplomacy", "response": "accept"},
             [{"kind": "information", "clauses": [{"clause": "energy", "energy_credits": 37}]}]),
            ("interaction", {"command": "respond_to_diplomatic_offer", "response": "accept", "principal": 100},
             [{"kind": "information", "offer_type": "loan_offer", "payment_per_turn": 6, "term_turns": 20}]),
        ):
            injected_catalog = [row, *information]
            frame = mcp.smac_choices(kind=kind)
            assert frame["information"] == information and not frame.get("preparations"), frame
            assert len(frame["choices"]) == 1 and "command" not in frame["choices"][0]
            assert mcp.smac_execute_choice(frame["decision_id"], frame["choices"][0]["choice_id"])["ok"]
            effect = effects.pop()
            assert effect["command"] == row["command"]
            for key in ("tech_id", "relationship", "response", "proposal_id"):
                if key in row: assert effect[key] == row[key], effect
            if "technology_id" in row: assert effect["tech_id"] == row["technology_id"]
            if row["command"] == "choose_council_proposal": assert effect["response"] == "yea"
        # A submitted truce response must retain its unverified-effect qualifier.
        for response in ("accept", "reject"):
            injected_catalog = [{"command": "respond_to_diplomatic_offer", "response": response,
                                 "offer_type": "truce"}]
            injected_receipt = {"ok": True, "command": "respond_to_diplomatic_offer",
                                "response": response, "offer_type": "truce",
                                "relationship_change_verified": False,
                                "completion_semantics": "Inspect fresh diplomatic state after native processing."}
            frame = mcp.smac_choices(kind="interaction")
            result = mcp.smac_execute_choice(frame["decision_id"], frame["choices"][0]["choice_id"])
            assert result["ok"] and result["relationship_change_verified"] is False, result
            assert result["completion_semantics"] == injected_receipt["completion_semantics"], result
            assert effects.pop()["response"] == response
        injected_receipt = None

        for ballot, expected in (
            ({"type": "candidate", "candidates": [
                {"faction_id": 2, "faction_name": "Candidate A"},
                {"faction_id": 4, "faction_name": "Candidate B"}]},
             [{"candidate_faction_id": 2}, {"candidate_faction_id": 4}]),
            ({"type": "yea_nay", "responses": ["yea", "nay"]},
             [{"response": "yea"}, {"response": "nay"}]),
            ({"type": "unknown"}, []),
        ):
            injected_catalog = [{"command": "choose_council_proposal", "proposal_id": 3,
                                 "name": "Controlled proposal", "ballot": ballot}]
            frame = mcp.smac_choices(kind="interaction")
            assert len(frame["choices"]) == len(expected), frame
            for index, binding in enumerate(expected):
                # Executing consumes the decision, so obtain a fresh frame for
                # each ballot; each displayed option must bind its own vote.
                frame = mcp.smac_choices(kind="interaction")
                assert mcp.smac_execute_choice(frame["decision_id"], frame["choices"][index]["choice_id"])["ok"]
                effect = effects.pop()
                assert all(effect[key] == value for key, value in binding.items()), effect

        for offered in (None, []):
            row = {"command": "give_energy_gift", "amount_min": 1, "amount_max": 24}
            if offered is not None:
                row["amount_options"] = offered
            injected_catalog = [row]
            frame = mcp.smac_choices(kind="interaction")
            assert not frame["choices"] and not frame.get("preparations"), frame
        injected_catalog = [{"command": "propose_human_energy", "amount_min": 1, "amount_max": 99}]
        frame = mcp.smac_choices(kind="interaction")
        assert not frame["choices"]
        prepared = mcp.smac_choices(kind="interaction", preparation_ref=frame["preparations"][0]["preparation_ref"], amount=37)
        assert mcp.smac_execute_choice(prepared["decision_id"], prepared["choices"][0]["choice_id"])["ok"]
        assert effects.pop()["amount"] == 37
        injected_catalog = None

        frame = mcp.smac_choices(kind="energy_allocation")
        preparation = frame["preparations"][0]
        context["identity"]["world_epoch"] = "epoch-other"
        rejected = mcp.smac_choices(kind="energy_allocation", preparation_ref=preparation["preparation_ref"],
                                    option_ref=preparation["options"][0]["option_ref"])
        assert not rejected["ok"] and "scope_or_revision" in rejected["error"]["detail"]

        # A native revision change, expiry and process recovery each revoke drafts.
        for reason in ("revision", "expiry", "recovery"):
            preparation = mcp.smac_choices(kind="energy_allocation")["preparations"][0]
            if reason == "revision": identity["revision"] = "r2"
            elif reason == "expiry":
                mcp.CHOICE_PREPARATIONS.rows[preparation["preparation_ref"]]["expires"] = 0
            else: mcp.CHOICE_PREPARATIONS = ChoicePreparations()
            assert not mcp.smac_choices(kind="energy_allocation", preparation_ref=preparation["preparation_ref"],
                                        option_ref=preparation["options"][0]["option_ref"])["ok"]
            assert not effects
    print(json.dumps({"passed": True, "evidence": "native_shaped_managed_adapter",
                      "staged_families": 5, "arbitrary_parameters_rejected": True,
                      "closed_research_council_human_clauses_and_loan_terms": True,
                      "no_mutation_during_preparation": True, "epoch_and_replay_guards": True}))


if __name__ == "__main__":
    main()
