#!/usr/bin/env python3
"""Plan-linked milestone transitions, epistemics and durable attention."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import smacx_mcp as mcp
from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_milestones import evaluate_milestone, validate_milestone
from smacx_observation import _delta_attention
from smacx_store import MemoryScope, SmacxStore
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, WorldObject


def main():
    def base(minerals, riots=False):
        return {"kind": "base", "fields": {"minerals": {"value": minerals}, "drone_riots": {"value": riots}}}
    assert _delta_attention({"change": "changed", "previous": base(1), "current": base(2)}) is None
    assert _delta_attention({"change": "changed", "previous": base(1, True), "current": base(2, True)}) is None
    assert _delta_attention({"change": "changed", "previous": base(1), "current": base(2, True)}) == (True, 90)
    with tempfile.TemporaryDirectory(prefix="smacx-milestone-") as raw:
        root = Path(raw)
        store = SmacxStore(root / "state.sqlite3")
        store.ensure_agent("agent-milestone", "Milestone")
        store.create_match(match_id="match-milestone", display_name="Milestone", mode="lan")
        store.create_perspective("match-milestone", "agent-milestone", perspective_id="perspective-milestone")
        scope = MemoryScope("match-milestone", "agent-milestone", "perspective-milestone")
        journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store)
        identity = WorldIdentity(scope.match_id, scope.perspective_id,
                                 store.active_timeline_id(scope), "epoch-milestone")
        objects = PerspectiveProjector(identity).project({
            "turn": 4, "map": {"width": 8, "height": 4, "horizontal_wrap": False},
            "tiles": [{"tile_id": 0, "x": 0, "y": 0, "visible_now": True, "terrain": "land"}],
            "bases": [], "units": [], "factions": [], "global": [],
        }, observation_sequence=1)["objects"]
        rows = [item.as_dict(provider_safe=False) for item in objects]
        def field(value):
            return {"value": value, "epistemic_status": "current", "source": "owned_state"}
        rows.extend({"object_ref": ref, "kind": "base", "status": "active",
                     "location_ref": "location-0", "fields": {
                         "owner_ref": field("faction-1"), "facilities": field([])}}
                    for ref in ("base-a", "base-b", "base-c"))
        def save(values, cursor):
            worlds.replace_projection(scope, identity, [WorldObject.from_dict(row) for row in values],
                                      observation_cursor=cursor, action_revision=f"r{cursor}",
                                      continuity="complete", journal_head_hash="0" * 64)
        save(rows, 1)
        attention = AttentionService(store, journal, scope)
        service = WorldService(worlds, scope)
        plan = store.put_plan(scope, "mobilization", "Western Mobilization", "Explicit staging requirements")
        journal.append(scope, "memory.plan", {"record": plan})
        definition = {"mode": "all", "requirements": [
            {"ref": "base-a", "kind": "exists"},
            {"ref": "base-b", "kind": "contains", "field": "facilities", "value": "Command Center"},
            {"ref": "base-c", "kind": "production_completed", "value": "Transport"}]}
        with patch.object(mcp, "_managed_scope_identity", return_value=(scope.match_id, "session-milestone", scope.agent_id, scope.perspective_id)), \
             patch.object(mcp, "controller_world_service", return_value=(scope, service, attention)):
            sql_only = store.put_plan(scope, "sql-only", "Unjournaled", "Cannot authorize intent")
            rejected = mcp.smac_cognition(action="watch_create", kind="milestone",
                                          subject_refs=["base-a", "base-b", "base-c"],
                                          predicate_json=json.dumps(definition), linked_plan_id=sql_only["plan_id"])
            assert not rejected["ok"] and "milestone_plan_not_active_in_journal" in str(rejected), rejected
            created = mcp.smac_cognition(action="watch_create", kind="milestone",
                                          subject_refs=["base-a", "base-b", "base-c"],
                                          predicate_json=json.dumps(definition), linked_plan_id=plan["plan_id"])
            assert created["ok"], created
            watch_id = created["watch_id"]
            assert mcp.smac_cognition(action="watch_inspect", subject_refs=[watch_id])["watch"]["milestone"]["state"] == "pending"
        completion = {"event_kind": "production_completed", "base_ref": "base-c",
                      "item_name": "Transport", "occurrence_ref": "production-one",
                      "evidence_kind": "owned_native_occurrence"}
        assert not attention.evaluate_watches([], temporal_events=[completion], observation_cursor=2, turn=4)
        ready = deepcopy(rows)
        next(row for row in ready if row["object_ref"] == "base-b")["fields"]["facilities"] = field(["Command Center"])
        save(ready, 3)
        triggered = attention.evaluate_watches([], observation_cursor=3, turn=4)
        assert len(triggered) == 1 and triggered[0]["matches"][0]["milestone"]["state"] == "ready"
        reopened = AttentionService(SmacxStore(root / "state.sqlite3"), journal, scope)
        assert not reopened.evaluate_watches([], temporal_events=[completion], observation_cursor=4, turn=4)
        destroyed = [row for row in ready if row["object_ref"] != "base-a"]
        save(destroyed, 5)
        reopened.gc_watches(4)
        blocked = reopened.evaluate_watches([], observation_cursor=5, turn=4)
        assert len(blocked) == 1 and blocked[0]["matches"][0]["milestone"]["state"] == "blocked"
        inspected = reopened.inspect_watch(watch_id)
        assert inspected["status"] == "active" and len(inspected["predicate"]["requirements"]) == 3
        assert "_completed" not in inspected["predicate"]
        stale = deepcopy(ready)
        next(row for row in stale if row["object_ref"] == "base-b")["fields"]["facilities"]["epistemic_status"] = "stale"
        value, _ = evaluate_milestone(definition, {row["object_ref"]: row for row in stale}, {}, [completion])
        assert value["state"] == "unknown"
        threshold = {"mode": "at_least", "at_least": 2, "requirements": definition["requirements"]}
        value, _ = evaluate_milestone(threshold, {row["object_ref"]: row for row in destroyed}, {}, [completion])
        assert value["state"] == "ready" and len(value["requirements"]) == 3
        for invalid in ({"mode": "at_least", "requirements": definition["requirements"]},
                        {"requirements": [{"ref": [], "kind": "exists"}]},
                        {"requirements": definition["requirements"] * 6}):
            try:
                validate_milestone(invalid, ["base-a", "base-b", "base-c"])
                raise AssertionError("invalid milestone accepted")
            except ValueError:
                pass
        # Existing attention delivery remains at-least-once, with one ID per
        # material transition, independent of repeated observation cursors.
        lease = reopened.lease("episode-milestone")
        assert len([item for item in lease["items"] if item["attention_kind"] == "watch_trigger"]) == 2
        revised = store.put_plan(scope, "mobilization", "Western Mobilization", "Revised explicit requirements")
        journal.append(scope, "memory.plan", {"record": revised})
        reopened.gc_watches(4)
        old = reopened.inspect_watch(watch_id)
        assert old["status"] == "expired" and old["linked_plan_id"] == plan["plan_id"]
        assert not reopened.evaluate_watches([], temporal_events=[completion], observation_cursor=8, turn=4)
    print(json.dumps({"ok": True, "managed_milestone_create_inspect": True,
                      "all_and_threshold": True, "destroyed_requirement_retained": True,
                      "stale_is_unknown": True, "repeat_completion_deduplication": True,
                      "restart_attention_delivery": True}))


if __name__ == "__main__":
    main()
