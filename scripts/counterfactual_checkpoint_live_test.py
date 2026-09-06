#!/usr/bin/env python3
"""Controlled inputs followed by real managed previews, actions and effects."""

import json
import time


def exercise_counterfactual_checkpoint(call, native, site_tanks_center_delta):
    fixture = native("test_managed_action_fixture", phase="counterfactual_inputs")
    assert fixture.get("ok"), fixture
    evidence = {}

    def record(name):
        evidence[name] = True
        print(json.dumps({"event": "counterfactual_verified", "capability": name}), flush=True)

    def choices(kind, **selectors):
        result = call("smac_choices", {"kind": kind, **selectors})
        assert result.get("ok"), result
        return result

    def choose(frame, label):
        return next(row for row in frame["choices"] if row.get("label") == label)

    def preview(frame, row, kind="action"):
        result = call("smac_world", {"mode": "counterfactual", "detail": "deep",
            "scenario_json": json.dumps({"kind": kind, "decision_id": frame["decision_id"], "choice_id": row["choice_id"]})})
        assert result.get("ok") and result.get("items"), result
        return result["items"][0]

    def execute(frame, row):
        result = call("smac_execute_choice", {"decision_id": frame["decision_id"], "choice_id": row["choice_id"]})
        assert result.get("ok"), result
        return result

    def fields(mode, ref):
        page = call("smac_world", {"mode": mode, "subject_refs": [ref], "detail": "deep"})
        assert page.get("ok"), page
        row = next(row for row in page.get("objects", page.get("items", [])) if row.get("object_ref") == ref)
        return {key: value.get("value") for key, value in row["fields"].items()}

    assert fields("forces", fixture["former_ref"])["roles"]["former"]
    assert fields("forces", fixture["scout_ref"])["roles"]["combat"]
    record("actors_represented_with_mechanical_roles")
    former = choices("unit_actions", own_unit_ref=fixture["former_ref"])
    farm = choose(former, "Terraform")
    predicted = preview(former, farm, "terraform")
    assert predicted["estimated_remaining_turns"] == 0, predicted
    assert execute(former, farm).get("accepted")
    location = predicted["location_ref"]
    units = native("perspective_world_page", domain="units", cursor=0, limit=256)["items"]
    actor = next(row for row in units if row.get("own_unit_ref") == fixture["former_ref"])
    actual = native("semantic_base_site_receipts", target_tile_ids=[actor["tile_id"]])["items"][0]
    standalone = next(row for row in predicted["yield_cases"] if row.get("base_ref") is None)
    assert actual["current_tile_yields"] == standalone["resulting_yield"], {"actual": actual, "predicted": predicted}
    affected = [row for row in predicted["yield_cases"] if row.get("base_ref")]
    assert affected, predicted
    for row in affected:
        radius = fields("base", row["base_ref"])["base_radius"]
        assert next(tile["yields"] for tile in radius if tile["location_ref"] == location) == row["resulting_yield"]
    record("completed_terraform_matches_standalone_and_owned_base_yields")

    production = choices("production", base_ref=fixture["base_ref"])
    selected = next(row for row in production["choices"] if row.get("name") == fixture["facility_name"])
    predicted = preview(production, selected)
    assert predicted["retool_penalty"] > 0, predicted
    execute(production, selected)
    assert fields("base", fixture["base_ref"])["minerals_accumulated"] == predicted["resulting_progress"]
    record("positive_retool_penalty_matches_native_change")
    production = choices("production", base_ref=fixture["base_ref"])
    hurry = choose(production, "Hurry production")
    predicted = preview(production, hurry)
    executed = execute(production, hurry)
    assert executed["production_name"] == fixture["facility_name"], executed
    assert executed["production_switched"] is False and executed["completion_verified"] is False
    assert executed["minerals_accumulated"] == predicted["resulting_progress"]
    assert executed["energy_cost"] == predicted["energy_cost"]
    assert fields("base", fixture["base_ref"])["minerals_accumulated"] == predicted["resulting_progress"]
    assert predicted["estimated_production_turns"] == 1
    record("hurry_charge_and_progress_match_without_claiming_instant_completion")

    decision = call("smac_decision", {})["identity"]
    plan_data = {"plan_key": "checkpoint-counterfactual-support", "title": "Explicit support actor",
                 "objective": "Inspect support and garrison effects", "status": "active",
                 "participants": [{"ref": fixture["scout_ref"], "intended_role": "stationary reserve"}]}
    plan = call("smac_memory_update", {"action": "plan", "match_id": decision["match_id"],
        "session_id": decision["session_id"], "observed_revision": decision["revision"], "record_json": json.dumps(plan_data)})
    assert plan.get("ok"), plan
    frame = choices("unit_actions", own_unit_ref=fixture["scout_ref"])
    rehome = choose(frame, "Rehome unit")
    predicted = preview(frame, rehome)
    assert any(row["plan_ref"] == plan["record"]["plan_id"] for row in predicted["relationships"]["linked_intent"])
    execute(frame, rehome)
    assert fields("forces", fixture["scout_ref"])["home_base_ref"] == fixture["destination_base_ref"]
    for row in predicted["support_changes"]:
        assert fields("base", row["base_ref"])["minerals"]["unit_support_cost"] == row["resulting_support_minerals"]
    record("rehome_support_and_journal_link_match_observed_effect")
    frame = choices("unit_actions", own_unit_ref=fixture["scout_ref"])
    disband = choose(frame, "Disband unit")
    predicted = preview(frame, disband)
    assert predicted["relationships"]["garrison_departures"]
    execute(frame, disband)
    for row in predicted["support_changes"]:
        assert fields("base", row["base_ref"])["minerals"]["unit_support_cost"] == row["resulting_support_minerals"]
    decision = call("smac_decision", {})["identity"]
    closed = call("smac_memory_update", {"action": "plan", "match_id": decision["match_id"],
        "session_id": decision["session_id"], "observed_revision": decision["revision"],
        "record_json": json.dumps({**plan_data, "status": "completed"})})
    assert closed.get("ok"), closed
    record("disband_support_and_explicit_garrison_departure")
    destination = fixture["destination_base_ref"]
    before_radius = fields("base", destination)["base_radius"]
    frame = choices("production", base_ref=destination)
    execute(frame, next(row for row in frame["choices"] if row.get("name") == "Recycling Tanks"))
    frame = choices("production", base_ref=destination)
    execute(frame, choose(frame, "Hurry production"))
    bases = native("perspective_world_page", domain="bases", cursor=0, limit=256)["items"]
    target = next(row for row in bases if row.get("base_ref") == destination)
    pending = native("test_managed_action_fixture", phase="counterfactual_facility_step", base_id=target["id"])
    assert pending.get("ok") and pending.get("queued"), pending
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        status = native("action_status", action_id=pending["action_id"])
        if status.get("action", {}).get("status") == "completed":
            break
        frame = choices("interaction")
        advances = [row for row in frame.get("choices", []) if any(word in
            (str(row.get("label", "")) + " " + str(row.get("meaning", ""))).casefold()
            for word in ("continue", "close", "acknowledge"))]
        if advances:
            execute(frame, advances[0])
        time.sleep(0.25)
    else:
        raise AssertionError({"facility_completion_failed": status})
    radius = fields("base", destination)["base_radius"]
    center_ref = "location-" + str(target["tile_id"])
    before_center = next(row["yields"] for row in before_radius if row["location_ref"] == center_ref)
    after_center = next(row["yields"] for row in radius if row["location_ref"] == center_ref)
    assert {key: after_center[key] - before_center[key] for key in before_center} == site_tanks_center_delta
    record("site_facility_delta_matches_managed_build_and_actual_native_completion")
    research = native("semantic_snapshot")["snapshot"].get("research", {})
    if research.get("blind"):
        frame = choices("research")
        explore = next(row for row in frame["choices"] if row.get("name") == "Explore")
        execute(frame, explore)
        research = native("semantic_snapshot")["snapshot"]["research"]
        assert research["selected_priorities"] == ["Explore"] and research["priority"] == 0, research
        assert research["target_visibility"] == "hidden_by_blind_research"
        record("blind_research_explore_native_flags_and_named_snapshot")
    evidence["production_and_travel_timing"] = exercise_native_production_timing(
        call, native, fixture["destination_base_ref"])
    return evidence


def settle_native_move(call, native, execution):
    """Arrival alone does not prove a queued move's native continuation returned."""
    action_id = execution.get("action_id") or execution.get("execution", {}).get("action_id")
    assert action_id is not None, execution
    notices = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        snapshot = native("semantic_snapshot")["snapshot"]
        receipt = native("action_status", action_id=action_id)["action"]
        assert receipt.get("status") not in {"rejected", "failed"}, receipt
        interaction = snapshot.get("interaction", {})
        if receipt.get("status") == "completed" and interaction.get("kind") == "turn":
            return {"receipt": receipt, "acknowledged_notices": notices}
        if interaction.get("kind") not in {"turn", "wait"}:
            frame = call("smac_choices", {"kind": "interaction"})
            assert frame.get("ok"), {"snapshot": snapshot, "frame": frame}
            acknowledge = next((row for row in frame.get("choices", [])
                                if row.get("label") == "Acknowledge popup"), None)
            assert acknowledge is not None, {"unexpected_move_interaction": interaction, "frame": frame}
            result = call("smac_execute_choice", {"decision_id": frame["decision_id"],
                                                  "choice_id": acknowledge["choice_id"]})
            assert result.get("ok"), result
            notices.append(interaction.get("popup_label"))
        time.sleep(.1)
    raise AssertionError({"move_did_not_finish_native_processing": receipt, "interaction": interaction})


def exercise_native_production_timing(call, native, base_ref):
    """Compare constant-surplus timing with real production upkeep calls."""
    bases = native("perspective_world_page", domain="bases", cursor=0, limit=256)["items"]
    base = next(row for row in bases if row.get("base_ref") == base_ref)
    units = native("perspective_world_page", domain="units", cursor=0, limit=256)["items"]
    before = {row.get("own_unit_ref") for row in units if row.get("owned")}
    frame = call("smac_choices", {"kind": "production", "base_ref": base_ref})
    choice = next(row for row in frame["choices"] if row.get("name") == "Scout Patrol")
    reference = {"decision_id": frame["decision_id"], "choice_id": choice["choice_id"]}
    preview = call("smac_world", {"mode": "counterfactual", "detail": "deep",
        "target_ref": base_ref, "subject_refs": [], "scenario_json": json.dumps({
            "kind": "deployment", "capability": "combat", "choice_refs": [reference]})})
    assert preview.get("ok"), preview
    projected = next(row for row in preview["items"][0]["alternatives"] if row.get("choice_ref") == reference)
    expected = projected["preparation_turns"]
    assert type(expected) is int and 1 <= expected <= 16, projected
    assert projected["travel_turns"] == 0 and projected["total_turns"] == expected, projected
    executed = call("smac_execute_choice", reference)
    assert executed.get("ok"), executed
    for step in range(1, expected + 1):
        observed = native("test_managed_action_fixture", phase="counterfactual_production_step", base_id=base["id"])
        assert observed.get("ok"), observed
        assert observed["observed_surplus"] == projected["production"]["mineral_surplus"], observed
        assert observed["created_unit_count"] == (1 if step == expected else 0), (step, expected, observed)
    units = native("perspective_world_page", domain="units", cursor=0, limit=256)["items"]
    born = [row for row in units if row.get("owned") and row.get("own_unit_ref") not in before]
    assert len(born) == 1, born
    actor_ref = born[0]["own_unit_ref"]
    frame = call("smac_choices", {"kind": "unit_actions", "own_unit_ref": actor_ref})
    # Compact managed choices intentionally omit native tile booleans. Ask
    # the world calculator about each issued semantic target instead.
    candidates = [row for row in frame["choices"] if row.get("label") == "Move unit"]
    for choice in candidates:
        target = choice["target_location_ref"]
        route = call("smac_world", {"mode": "counterfactual", "detail": "deep",
            "subject_refs": [actor_ref], "target_ref": target,
            "scenario_json": '{"kind":"deployment","capability":"combat"}'})
        if not route.get("ok"):
            continue
        alternative = route["items"][0]["alternatives"][0]
        # Conditional-minimum routes can fail native fungus/overspend rolls.
        # An exact arrival assertion requires deterministic route evidence.
        if alternative.get("total_turns") != 1 or alternative.get("route_evidence") != "exact_known_state":
            continue
        executed = call("smac_execute_choice", {"decision_id": frame["decision_id"], "choice_id": choice["choice_id"]})
        assert executed.get("ok"), executed
        settled = settle_native_move(call, native, executed)
        world = call("smac_world", {"mode": "forces", "subject_refs": [actor_ref], "detail": "deep"})
        assert any(row.get("object_ref") == actor_ref and row.get("location_ref") == target
                   for row in world.get("items", [])), world
        return {"actual_native_production_upkeeps": expected, "production_timing_matches": True,
                "one_phase_native_move_matches": True, "native_move_completion": settled,
                "fixed_surplus_controlled_upkeeps_not_full_campaign_turns": True}
    raise AssertionError("No single-phase native movement comparison was available")
