#!/usr/bin/env python3
"""Restart, cross-perspective and checkpoint lineage boundaries for derived refs."""
import json
from pathlib import Path
import tempfile
from semantic_consumer_contract_test import Fixture
from smacx_attention import AttentionService,AttentionError
from smacx_store import SmacxStore,MemoryScope
from smacx_world import WorldService,WorldQueryError
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity,WorldObject
from smacx_world_model import CALCULATOR_VERSION


def main():
    with tempfile.TemporaryDirectory() as tmp:
        f=Fixture(Path(tmp))
        f.actor("unit-review","own_unit",2,2,triad="land",movement_points=3,owner_ref="faction-1",roles={"combat":True})
        f.save()
        route=f.service.query(mode="route",origin_ref="unit-review",target_ref=f.at(8,2),detail="deep")["route"]["route_ref"]
        scope=f.attention.create_watch("spatial_scope",[route],{"type":"route_corridor","radius":1},current_turn=4)["watch_id"]
        watch=f.attention.create_watch("region_entry",[scope],{},current_turn=4)["watch_id"]
        checkpoint=f.journal.append(f.scope,"checkpoint.native",{"turn":4})
        snapshot=f.worlds.snapshot(f.scope,f.identity,journal_head_hash=checkpoint["event_hash"],
            journal_sequence=checkpoint["sequence"],calculator_versions={"world":CALCULATOR_VERSION})
        restarted=SmacxStore(f.root/"state.sqlite3")
        attention=AttentionService(restarted,f.journal,f.scope)
        world=WorldService(WorldStore(restarted),f.scope)
        assert world.query(mode="area",origin_ref=scope)["ok"]
        assert route in attention.semantic_dependency_hashes()
        assert attention.inspect_watch(watch)["status"]=="active"
        f.store.ensure_agent("agent-other","Other")
        f.store.create_perspective(f.scope.match_id,"agent-other",perspective_id="perspective-other")
        other=MemoryScope(f.scope.match_id,"agent-other","perspective-other")
        other_identity=WorldIdentity(other.match_id,other.perspective_id,f.identity.timeline_id,f.identity.world_epoch)
        f.worlds.replace_projection(other,other_identity,[WorldObject.from_dict(row) for row in f.objects],
            observation_cursor=1,action_revision="r1",continuity="complete",journal_head_hash="0"*64)
        other_attention=AttentionService(f.store,f.journal,other)
        assert route not in other_attention.semantic_dependency_hashes()
        try:WorldService(f.worlds,other).query(mode="area",origin_ref=scope)
        except WorldQueryError:pass
        else:raise AssertionError("cross-perspective scope was accepted")
        target="timeline-review-restored"
        f.journal.fork_timeline(f.scope,target,native_save_sha256="b"*64,
            from_event_hash=checkpoint["event_hash"],parent_timeline_id=f.identity.timeline_id)
        with f.store.transaction() as c:
            c.execute("UPDATE matches SET metadata_json=json_set(metadata_json,'$.active_memory_timeline',?) WHERE match_id=?",
                (target,f.scope.match_id))
        payload=f.worlds.verify_snapshot(snapshot["snapshot_id"],journal_head_hash=checkpoint["event_hash"],journal_sequence=checkpoint["sequence"])
        f.worlds.restore_projection_from_snapshot(f.scope,payload,target_timeline_id=target,journal_head_hash=checkpoint["event_hash"])
        f.worlds.discard_future(f.scope,target)
        restored=AttentionService(f.store,f.journal,f.scope)
        assert route not in restored.semantic_dependency_hashes()
        try:restored.inspect_scope(scope)
        except AttentionError:pass
        else:raise AssertionError("old scope survived new timeline")
        fresh=f.service.query(mode="route",origin_ref="unit-review",target_ref=f.at(8,2),detail="deep")
        assert fresh["ok"] and fresh["cache"]["hit"] is False
    print(json.dumps({"passed":True,"restart_preserves_valid_route_scope_watch":True,
        "cross_perspective_rejected":True,"checkpoint_restore_invalidates_old_derived_handles":True,"fresh_timeline_recomputation":True}))


if __name__=="__main__":main()
