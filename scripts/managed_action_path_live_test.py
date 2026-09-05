#!/usr/bin/env python3
"""Managed action checks inside control_worker_mcp_live_test's isolated stack."""
from __future__ import annotations
import time
import json


def exercise_managed_actions(call, native, fixture):
    """`call` invokes the actual 15-tool MCP endpoint; `native` only verifies."""
    evidence = {}

    def record(key):
        evidence[key] = True
        print(json.dumps({"event": "managed_action_verified", "capability": key}), flush=True)

    def choices(kind, **selectors):
        result = call("smac_choices", {"kind": kind, **selectors})
        assert result.get("ok"), result
        return result

    def execute(frame, selected, **text):
        result = call("smac_execute_choice", {"decision_id": frame["decision_id"],
                                               "choice_id": selected["choice_id"], **text})
        assert result.get("ok"), result
        return result

    def preview(frame, selected, kind="action"):
        result = call("smac_world", {"mode": "counterfactual", "detail": "deep",
            "scenario_json": json.dumps({"kind": kind, "decision_id": frame["decision_id"],
                                         "choice_id": selected["choice_id"]})})
        assert result.get("ok") and result.get("items"), result
        return result["items"][0]

    def select(frame, predicate):
        row = next((row for row in frame.get("choices", ()) if predicate(row)), None)
        assert row is not None, {"missing_managed_choice": frame}
        return row

    def prepare(kind, select_option, purpose=None):
        first = choices(kind)
        preparation = next(row for row in first["preparations"] if row["purpose"] == (purpose or kind))
        for _ in range(8):
            option = select_option(preparation)
            result = choices(kind, preparation_ref=preparation["preparation_ref"],
                             option_ref=option["option_ref"])
            if "preparation" not in result:
                assert result["choices"], result
                return result
            preparation = result["preparation"]
        raise AssertionError("managed preparation did not terminate within eight selections")

    def fields(mode, ref):
        answer = call("smac_world", {"mode": mode, "subject_refs": [ref], "detail": "deep"})
        assert answer.get("ok"), answer
        row = next(row for row in answer.get("objects", answer.get("items", ()))
                   if row.get("object_ref") == ref)
        return {key: value.get("value") for key, value in row["fields"].items()}

    # Fixture handles are verification oracles. The managed provider must
    # independently discover the actors through its ordinary world queries.
    for mode, expected in (
        ("forces", {fixture[key] for key in ("scout_ref", "colony_ref", "former_ref",
                                            "passenger_ref", "transport_ref")}),
        ("base", {fixture["base_ref"]}),
    ):
        discovered = set()
        continuation = ""
        for _ in range(16):
            page = call("smac_world", {"mode": mode, "detail": "deep", "continuation": continuation})
            assert page.get("ok"), page
            discovered.update(row.get("object_ref") for row in
                              [*page.get("items", ()), *page.get("objects", ())])
            if expected <= discovered:
                break
            continuation = page.get("continuation")
            assert continuation, {"undiscovered_managed_actors": sorted(expected - discovered)}
        assert expected <= discovered
    record("actors_discovered_through_managed_world")

    design_name = "Checkpoint Sentinel"
    design = prepare("unit_design", lambda step: next(
        option for option in step["options"]
        if option["label"] == fixture["design_labels"].get(step["step"], "No special ability")))
    execute(design, select(design, lambda row: row["label"] == "Create selected custom design"), text=design_name)
    listed = choices("unit_design")
    assert any(row.get("name") == design_name for row in listed["choices"]), listed
    record("custom_design_create_and_discover")

    upgrade = choices("unit_actions", own_unit_ref=fixture["scout_ref"])
    upgrade_choice = select(upgrade, lambda row: row.get("target_name") == design_name)
    upgrade_preview = preview(upgrade, upgrade_choice)
    upgraded = execute(upgrade, upgrade_choice)
    assert upgraded["energy_spent"] == upgrade_preview["energy_cost"]
    unit = fields("forces", fixture["scout_ref"])
    assert design_name in str(unit), unit
    record("single_unit_upgrade_and_observation")
    record("upgrade_preview_cost_matches_execution")

    allocation = prepare("energy_allocation", lambda step: next(
        option for option in step["options"]
        if option["label"] == ("40% economy" if step["step"] == "economy" else "20% psych")))
    execute(allocation, allocation["choices"][0])
    assert native("semantic_choices", kind="energy_allocation")["current"] == {"economy": 4, "psych": 2, "labs": 4}
    assert fields("global", "global-economy")["state"]["allocation"] == {"economy": 4, "psych": 2, "labs": 4}
    record("energy_allocation_effect")

    social = prepare("social_engineering", lambda step: step["options"][min(1, len(step["options"]) - 1)])
    social_choice = social["choices"][0]
    social_preview = call("smac_world", {"mode": "counterfactual", "detail": "deep",
        "scenario_json": json.dumps({"kind": "social", "decision_id": social["decision_id"],
                                     "choice_id": social_choice["choice_id"]})})
    assert social_preview.get("ok"), social_preview
    predicted_social = social_preview["items"][0]["confirmed_mechanics"]
    predicted_support = social_preview["items"][0]["derived"]["support_by_base"]
    prior_social = native("semantic_choices", kind="social_engineering")
    safe = native("test_counterfactual_read_safety", kind="social", expected_revision=prior_social["revision"],
                  **{key: social_choice[key] for key in ("politics", "economics", "values", "future")})
    assert safe.get("ok") and safe["receipt"].get("ok"), safe
    execute(social, social_choice)
    after_social = native("semantic_choices", kind="social_engineering")
    observed_social = after_social["selected"]
    assert predicted_social["resulting_ratings"] == after_social["effective_ratings"], social_preview
    assert prior_social["energy_credits"] - after_social["energy_credits"] == predicted_social["switch_energy_cost"]
    for row in predicted_support:
        observed_base = fields("base", row["base_ref"])
        assert observed_base["minerals"]["unit_support_cost"] == row["resulting_support_minerals"], (row, observed_base["minerals"])
        assert observed_base["minerals_accumulated"] == row["resulting_minerals_accumulated"], (row, observed_base["minerals"])
    record("social_preview_native_ratings_cost_and_read_safety")
    assert fields("global", "global-social-engineering")["state"]["selected"] == observed_social
    for key in ("politics", "economics", "values", "future"):
        observed = observed_social[key]
        assert (observed.get("model_id") if isinstance(observed, dict) else observed) == social_choice[key], observed_social
    record("social_models_atomic_effect")

    base_ref = fixture["base_ref"]
    production = choices("production", base_ref=base_ref)
    production_choice = select(production, lambda row: row.get("name") == design_name)
    production_preview = preview(production, production_choice)
    deployment = call("smac_world", {"mode": "counterfactual", "target_ref": base_ref, "detail": "deep",
        "scenario_json": json.dumps({"kind": "deployment", "capability": "combat", "choice_refs": [
            {"decision_id": production["decision_id"], "choice_id": production_choice["choice_id"]}]})})
    assert deployment.get("ok"), deployment
    build = next(row for row in deployment["items"][0]["alternatives"] if row.get("alternative") == "set_production")
    assert build["travel_turns"] == 0 and build["preparation_turns"] == production_preview["estimated_production_turns"]
    execute(production, production_choice)
    base = fields("base", base_ref)
    assert base["production_queue"][0]["name"] == design_name, base
    assert base["minerals_accumulated"] == production_preview["resulting_progress"]
    record("production_loss_preview_and_deployment_composition")
    management = choices("base_management", base_ref=base_ref)
    execute(management, select(management, lambda row: row.get("label") == "Queue production" and row.get("name") == design_name))
    base = fields("base", base_ref)
    assert len(base["production_queue"]) >= 2 and base["production_queue"][-1]["name"] == design_name, base
    management = choices("base_management", base_ref=base_ref)
    execute(management, select(management, lambda row: row.get("label") == "Remove queued production" and row.get("queue_position") == 1))
    assert len(fields("base", base_ref)["production_queue"]) == 1
    record("production_set_append_remove_effect")

    management = choices("base_management", base_ref=base_ref)
    governor = select(management, lambda row: row.get("label") == "Set base governor")
    execute(management, governor)
    assert fields("base", base_ref)["governor"]["active"] == bool(governor["active"])
    management = choices("base_management", base_ref=base_ref)
    if governor.get("active"):
        execute(management, select(management, lambda row: row.get("label") == "Set base governor" and not row.get("active")))
    citizens = choices("base_citizens", base_ref=base_ref)
    if not any(row["label"] == "Convert worker to specialist" for row in citizens["choices"]):
        execute(citizens, select(citizens, lambda row: row["label"] == "Assign specialist to tile"))
        citizens = choices("base_citizens", base_ref=base_ref)
    specialists_before = len(fields("base", base_ref)["citizens"]["specialists"])
    execute(citizens, select(citizens, lambda row: row.get("label") == "Convert worker to specialist"))
    assert len(fields("base", base_ref)["citizens"]["specialists"]) == specialists_before + 1
    after_citizens = choices("base_citizens", base_ref=base_ref)
    assert any(row.get("label") == "Assign specialist to tile" for row in after_citizens["choices"])
    record("governor_and_citizen_managed_workflow")

    rendezvous = call("smac_world", {"mode": "compare",
        "subject_refs": [fixture["passenger_ref"], fixture["transport_ref"]],
        "target_ref": fixture["transport_ref"], "detail": "deep"})
    assert rendezvous.get("ok"), rendezvous
    meeting = rendezvous["items"][0]
    assert meeting["all_reachable"] and meeting["earliest_common_arrival_turns"] == 0, meeting
    assert len(meeting["arrivals"]) == 2 and all(row["eta_turns"] == 0 for row in meeting["arrivals"])
    loaded_before = fields("forces", fixture["transport_ref"])["cargo"]["loaded"]
    boarding = choices("unit_actions", own_unit_ref=fixture["passenger_ref"])
    execute(boarding, select(boarding, lambda row: row.get("label") == "Board transport"
                            and row.get("transport_unit_ref") == fixture["transport_ref"]))
    assert fields("forces", fixture["transport_ref"])["cargo"]["loaded"] == loaded_before + 1
    passenger = fields("forces", fixture["passenger_ref"])
    assert passenger["roles"]["boarded"] and passenger["transport_unit_ref"] == fixture["transport_ref"], passenger
    record("colocated_rendezvous_query_and_transport_boarding_effect")

    former = choices("unit_actions", own_unit_ref=fixture["former_ref"])
    former_choice = select(former, lambda row: row.get("label") == "Terraform")
    terra_preview = call("smac_world", {"mode": "counterfactual", "detail": "deep",
        "scenario_json": json.dumps({"kind": "terraform", "decision_id": former["decision_id"],
                                     "choice_id": former_choice["choice_id"]})})
    assert terra_preview.get("ok") and terra_preview["items"][0]["current_legality"], terra_preview
    terraformed = execute(former, select(former, lambda row: row.get("label") == "Terraform"))
    former_state = fields("forces", fixture["former_ref"])
    assert terraformed.get("accepted") and former_state.get("order_name") == "terraform", former_state
    record("terraform_order_effect_verified")

    colony = choices("unit_actions", own_unit_ref=fixture["colony_ref"])
    before_units = call("smac_world", {"mode": "forces", "subject_refs": [fixture["colony_ref"]]})
    at = next(row["location_ref"] for row in before_units["items"] if row["object_ref"] == fixture["colony_ref"])
    site_preview = call("smac_world", {"mode": "counterfactual", "subject_refs": [at], "detail": "deep",
        "scenario_json": json.dumps({"kind": "site_economy", "populations": [1]})})
    assert site_preview.get("ok"), site_preview
    predicted_center = site_preview["items"][0]["counterfactual"]["center"]["yields"]
    tanks = next(row for row in site_preview["items"][0]["counterfactual"]["material_facility_unlocks"]
                 if row["facility"] == "Recycling Tanks")
    tank_delta = next(row for row in tanks["sample_deltas"] if row["location_ref"] == at)
    evidence["site_tanks_center_delta"] = {key: tank_delta["after"][key] - tank_delta["before"][key]
                                          for key in ("nutrients", "minerals", "energy")}
    safe_site = native("test_counterfactual_read_safety", kind="site_economy", include_economy=True,
                      target_tile_ids=[int(at.removeprefix("location-"))], check_hidden_independence=True)
    assert safe_site.get("ok") and safe_site["receipt"].get("ok"), safe_site
    assert safe_site["changed_hidden_tile_count"] > 0, safe_site
    record("site_variants_restore_state_and_ignore_hidden_mirrors_and_foreign_workers")
    execute(colony, select(colony, lambda row: row.get("label") == "Found base"))
    area = call("smac_world", {"mode": "area", "origin_ref": at, "radius": 0, "detail": "deep"})
    assert area.get("ok") and any(row.get("kind") == "base" and row.get("location_ref") == at
                                  for row in area.get("items", ())), area
    founded = next(row for row in area["items"] if row.get("kind") == "base" and row.get("location_ref") == at)
    observed_radius = fields("base", founded["object_ref"])["base_radius"]
    assert next(row["yields"] for row in observed_radius if row["location_ref"] == at) == predicted_center
    record("site_center_preview_matches_managed_founding")
    record("colony_founding_effect_verified")
    bulk = prepare("unit_design", lambda step: next(
        row for row in step["options"] if row["label"] == (
            fixture["source_design_name"] if step["step"] == "source_prototype" else design_name)),
        purpose="unit_upgrade")
    bulk_choice = bulk["choices"][0]
    assert bulk_choice["active_unit_count"] >= 1 and bulk_choice["energy_cost_total"] >= 0
    result = execute(bulk, bulk_choice)
    assert result["units_upgraded"] == bulk_choice["active_unit_count"]
    assert result["energy_spent"] == bulk_choice["energy_cost_total"]
    assert design_name in str(fields("forces", fixture["passenger_ref"]))
    record("bulk_upgrade_staged_cost_and_observed_effect")

    def await_dialog(labels):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            snapshot = native("semantic_snapshot")["snapshot"]
            if snapshot.get("interaction", {}).get("popup_label") in labels:
                return snapshot
            time.sleep(0.1)
        raise AssertionError({"missing_dialog": sorted(labels)})

    def finish_dialogs():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            snapshot = native("semantic_snapshot")["snapshot"]
            if snapshot.get("interaction", {}).get("kind") == "turn":
                return snapshot
            frame = choices("interaction")
            options = frame["choices"]
            closing = next((row for row in options if row.get("option") == "finish"
                            or (row.get("label") == "Respond to design offer"
                                and row.get("response") == "decline")), None)
            if closing or len(options) == 1:
                execute(frame, closing or options[0])
            elif not options or all(row["label"] == "Cast council vote" for row in options):
                # The bundled ballot is dispatched by the native UI loop.
                time.sleep(0.1)
            else:
                raise AssertionError({"unreviewed_dialog_continuation": frame})
        raise AssertionError("native dialog continuation did not finish")

    assert native("test_managed_action_fixture", phase="gift")["ok"]
    before = await_dialog({"COUNTER1", "OFFERENERGY"})
    frame = choices("interaction")
    prompt = next(row for row in frame["preparations"] if row["purpose"] == "energy_amount")
    assert 125 in prompt["amount_input"]["allowed_values"]
    rejected = call("smac_choices", {"kind": "interaction",
                    "preparation_ref": prompt["preparation_ref"], "amount": 37})
    assert not rejected.get("ok") and native("semantic_snapshot")["snapshot"]["faction"]["energy_credits"] == before["faction"]["energy_credits"]
    selected = choices("interaction", preparation_ref=prompt["preparation_ref"], amount=125)
    paid = execute(selected, selected["choices"][0])
    after = finish_dialogs()
    assert paid["native_amount_prompt_seen"] and before["faction"]["energy_credits"] - after["faction"]["energy_credits"] == 125
    record("selected_diplomatic_gift_native_effect")

    assert native("test_managed_action_fixture", phase="commerce")["ok"]
    before = await_dialog({"BUYTECH0", "BUYTECH1"})
    frame = choices("interaction")
    terms = next(row for row in frame["information"] if row.get("offer_type") == "technology_purchase")
    execute(frame, select(frame, lambda row: row.get("response") == "accept"))
    after = finish_dialogs()
    assert before["faction"]["energy_credits"] - after["faction"]["energy_credits"] == terms["energy_credits"]
    technologies = fields("global", "global-owned-technologies")["technologies"]
    assert terms["technology_name"] in str(technologies), technologies
    record("technology_purchase_terms_and_observed_transfer")

    assert native("test_managed_action_fixture", phase="loan")["ok"]
    before = await_dialog({"ENERGYLOAN1", "ENERGYLOAN2"})
    frame = choices("interaction")
    terms = next(row for row in frame["information"] if row.get("offer_type") == "loan_offer")
    assert terms["scheduled_total"] == terms["payment_per_turn"] * terms["term_turns"] > 0
    execute(frame, select(frame, lambda row: row.get("response") == "accept"))
    after = finish_dialogs()
    assert after["faction"]["energy_credits"] - before["faction"]["energy_credits"] == terms["principal"]
    factions = native("list_factions")["items"]
    counterpart = next(row for row in factions if row["id"] == terms["counterpart_faction_id"])
    assert counterpart["loans"]["own_balance_owed_to_them"] == terms["scheduled_total"]
    assert counterpart["loans"]["own_payment_per_turn"] == terms["payment_per_turn"]
    record("native_quoted_loan_terms_and_effect")

    assert native("test_managed_action_fixture", phase="council")["can_convene"]
    frame = choices("council")
    execute(frame, select(frame, lambda row: row["label"] == "Convene council"))
    await_dialog({"COUNCILISSUES"})
    frame = choices("interaction")
    ballot = select(frame, lambda row: row.get("candidate_name") or row.get("response") in {"yea", "nay"})
    assert execute(frame, ballot)["ballot_scheduled"]
    finish_dialogs()
    result = fields("global", "global-last-council-result")["state"]
    assert result and result.get("proposal_id") == ballot["proposal_id"], result
    record("council_proposal_ballot_and_public_result")
    return {"passed": True, "managed_endpoint": True, "evidence": evidence}
