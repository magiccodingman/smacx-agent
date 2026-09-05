"""Managed intent checks within the isolated native acceptance campaign."""

import json
import time


def verify_intent_attention(call, get_context, end_episode, responded, watch_id, episode_prefix):
    """Read/acknowledge earlier batches through the existing response protocol."""
    seen_milestone, seen_production, seen_interruption = False, False, False
    for index in range(16):
        episode = episode_prefix + str(index)
        context = get_context(episode)
        assert context.get("ok"), context
        attention = context["runtime_context"]["attention"]
        items = attention.get("items", [])
        seen_milestone |= any(item.get("attention_kind") == "watch_trigger"
                              and item.get("payload", {}).get("watch_id") == watch_id for item in items)
        seen_production |= any(item.get("attention_kind") == "production_progress" for item in items)
        seen_interruption |= any(event.get("event_kind") == "production_interrupted"
                                 for item in items for event in item.get("payload", {}).get("events", []))
        if items:
            assert responded(attention["attention_lease_id"]).get("ok")
            acknowledgement = call("smac_attention_ack", {"attention_lease_id": attention["attention_lease_id"],
                                                            "through_cursor": attention["through_cursor"]})
            assert acknowledgement.get("ok"), acknowledgement
        end_episode(episode)
        if seen_milestone and seen_production and seen_interruption:
            return {"milestone_and_production_runtime_delivery": True, "acknowledged_batches": index + 1,
                    "native_project_interruption_delivered": True,
                    "provider_inference": False, "trusted_response_hook_simulated": True}
    raise AssertionError({"milestone_seen": seen_milestone, "production_seen": seen_production,
                          "interruption_seen": seen_interruption})


def exercise_intent_checkpoint(call, native, previously_discovered_base_ref):
    broad = call("smac_world", {"mode": "base", "detail": "compact"})
    assert broad.get("ok") and broad.get("items"), broad
    def world_units():
        refs, continuation = set(), None
        for _ in range(64):
            args = {"mode": "forces", "detail": "deep"}
            if continuation:
                args["continuation"] = continuation
            page = call("smac_world", args)
            assert page.get("ok"), page
            refs.update(row["object_ref"] for row in [*page.get("items", []), *page.get("objects", [])]
                        if row.get("kind") == "own_unit")
            continuation = page.get("continuation")
            if not continuation:
                return refs
        raise AssertionError("owned actor pagination exceeded acceptance bound")
    page = call("smac_world", {"mode": "base", "subject_refs": [previously_discovered_base_ref], "detail": "deep"})
    assert page.get("ok"), page
    base = next(row for row in [*page.get("items", []), *page.get("objects", [])]
                if row.get("kind") == "base" and row.get("object_ref") == previously_discovered_base_ref)
    base_ref = base["object_ref"]
    scope = call("smac_cognition", {"action": "scope_create", "subject_refs": [base_ref],
                                    "predicate_json": json.dumps({"type": "base_radius"})})
    assert scope.get("ok"), scope
    descriptor = call("smac_cognition", {"action": "scope_inspect", "subject_refs": [scope["watch_id"]]})
    assert descriptor.get("ok") and 0 < descriptor["scope"]["known_coverage_count"] <= 21, descriptor
    assert "_location_refs" not in json.dumps(descriptor)
    catalog = call("smac_choices", {"kind": "production", "base_ref": base_ref})
    assert catalog.get("ok") and any(row.get("name") == "Scout Patrol" for row in catalog.get("choices", [])), catalog
    decision = call("smac_decision", {})
    assert decision.get("ok"), decision
    identity = decision["identity"]
    plan = call("smac_memory_update", {"action": "plan", "match_id": identity["match_id"],
                                        "session_id": identity["session_id"], "observed_revision": identity["revision"],
                                        "record_json": json.dumps({"plan_key": "checkpoint-production", "title": "Controlled build-up",
                                                                   "objective": "Observe two completed Scouts", "status": "active",
                                                                   "target_refs": [base_ref], "participants": [{"ref": base_ref, "intended_role": "producer"}]})})
    assert plan.get("ok"), plan
    watch = call("smac_cognition", {"action": "watch_create", "kind": "milestone", "subject_refs": [base_ref],
                                    "linked_plan_id": plan["record"]["plan_id"], "predicate_json": json.dumps({
                                        "mode": "all", "requirements": [{"ref": base_ref, "kind": "production_completed",
                                                                            "value": "Scout Patrol", "count": 2}]})})
    assert watch.get("ok"), watch
    before = world_units()
    receipt = native("test_managed_action_fixture", phase="production")
    assert receipt.get("ok") and receipt["created_unit_count"] == 2 and receipt["base_ref"] == base_ref, receipt
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        after = world_units()
        milestone = call("smac_cognition", {"action": "watch_inspect", "subject_refs": [watch["watch_id"]]})
        if milestone.get("ok") and milestone["watch"]["milestone"]["state"] == "ready":
            break
        time.sleep(0.25)
    else:
        raise AssertionError({"native_completion_not_represented_in_managed_milestone": milestone})
    assert len(after - before) == 2, {"new_units": sorted(after - before)}
    assert native("test_managed_action_fixture", phase="production_prepare").get("ok")
    completed_categories = []
    project_name = None
    for phase, name_fragment in (("production_facility", "Recreation Commons"),
                                 ("production_project", "Weather Paradigm"),
                                 ("production_interrupt", "Weather Paradigm")):
        interrupted = phase == "production_interrupt"
        catalog = call("smac_choices", {"kind": "production", "base_ref": base_ref})
        assert catalog.get("ok"), catalog
        offered = next((row for row in catalog.get("choices", [])
                        if name_fragment.casefold() in str(row.get("name", "")).casefold()), None)
        if interrupted:
            offered = {"name": project_name}
            # The production watch intentionally merges an identical active
            # definition. Close the already-satisfied watch before testing a
            # fresh completion requirement against the refused attempt.
            assert call("smac_cognition", {"action": "watch_close", "subject_refs": [item_watch["watch_id"]]}).get("ok")
        assert offered is not None, {"missing_current_production_catalog": name_fragment, "catalog": catalog}
        project_name = offered["name"]
        item_watch = call("smac_cognition", {"action": "watch_create", "kind": "milestone", "subject_refs": [base_ref],
                                             "linked_plan_id": plan["record"]["plan_id"], "predicate_json": json.dumps({
                                                 "requirements": [{"ref": base_ref, "kind": "production_completed", "value": offered["name"]}]})})
        assert item_watch.get("ok"), item_watch
        queued = native("test_managed_action_fixture", phase=phase)
        assert queued.get("ok") and queued.get("queued"), queued
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = native("action_status", action_id=queued["action_id"])
            if status.get("action", {}).get("status") == "completed":
                break
            frame = call("smac_choices", {"kind": "interaction"})
            assert frame.get("ok"), frame
            advances = [row for row in frame.get("choices", []) if row.get("executable", True)
                        and any(word in (str(row.get("label", "")) + " " + str(row.get("meaning", ""))).casefold()
                                for word in ("continue", "close", "acknowledge"))]
            if advances:
                result = call("smac_execute_choice", {"decision_id": frame["decision_id"], "choice_id": advances[0]["choice_id"]})
                assert result.get("ok"), result
            time.sleep(0.25)
        else:
            raise AssertionError({"native_production_presentation_did_not_finish": phase, "last_frame": frame, "status": status})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            current = call("smac_cognition", {"action": "watch_inspect", "subject_refs": [item_watch["watch_id"]]})
            expected = "pending" if interrupted else "ready"
            if current.get("ok") and current["watch"]["milestone"]["state"] == expected:
                break
            time.sleep(0.25)
        else:
            raise AssertionError({"native_production_not_in_managed_milestone": phase, "watch": current})
        snapshot = native("semantic_snapshot")
        assert snapshot.get("ok"), snapshot
        if phase == "production_project":
            projects = snapshot["snapshot"].get("public_projects", [])
            assert any(name_fragment.casefold() in row["name"].casefold()
                       and row["owner_ref"].removeprefix("faction-").isdigit()
                       for row in projects), projects
        completed_categories.append(phase)
    catalog = call("smac_choices", {"kind": "production", "base_ref": base_ref})
    assert catalog.get("ok"), catalog
    names = sorted({row["name"] for row in catalog.get("choices", []) if row.get("name")})[:2]
    assert len(names) == 2, catalog
    turn = native("semantic_snapshot")["snapshot"]["turn"]
    for index, item_name in enumerate(names):
        identity = call("smac_decision", {})["identity"]
        reservation = call("smac_memory_update", {"action": "plan", "match_id": identity["match_id"],
            "session_id": identity["session_id"], "observed_revision": identity["revision"],
            "record_json": json.dumps({"plan_key": f"checkpoint-reservation-{index}",
                "title": f"Explicit production reservation {index}", "objective": "Test declared slot conflict",
                "status": "active", "timing": {"start_turn": turn, "end_turn": turn + 3},
                "participants": [{"ref": base_ref, "production_item": item_name},
                                 {"ref": sorted(after - before)[0], "intended_role": "stationary reserve"}]})})
        assert reservation.get("ok"), reservation
    health = call("smac_cognition", {"action": "plan_health"})
    assert health.get("ok") and health["plan_health"]["active_plan_count"] >= 3, health
    assert health["plan_health"]["conflict_count"] >= 1 and health["plan_health"]["assigned_owned_unit_count"] >= 1, health
    return {"managed_base_radius_scope": True, "private_membership": True,
            "journaled_plan_and_milestone": True, "native_repeat_completion_two_units": True,
            "native_occurrence_to_managed_ready": True, "provider_queryable_plan_health": True,
            "managed_journaled_reservation_conflict": True, "stationary_unit_assignment_visible": True,
            "native_production_cases": completed_categories,
            "milestone_watch_id": watch["watch_id"], "plan_id": plan["record"]["plan_id"]}
