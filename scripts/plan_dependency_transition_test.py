#!/usr/bin/env python3
"""Explicit dependency reactivation is journaled, deduplicated and rollback-safe."""
from pathlib import Path
import json
import tempfile
from semantic_consumer_contract_test import Fixture, field
from smacx_attention import AttentionService
from smacx_store import SmacxStore


def main():
    with tempfile.TemporaryDirectory() as tmp:
        f=Fixture(Path(tmp))
        actor=f.actor("transport-review","own_unit",2,2,roles={"transport":True},available=False)
        plan=f.store.put_plan(f.scope,"landing","Western Landing","Explicit transport requirement",
            dependencies=["transport-review"],last_confirmation={"dependency_values":[
                {"ref":"transport-review","field":"available","value":True}]})
        f.journal.append(f.scope,"memory.plan",{"record":plan})
        def evaluate():
            f.save()
            projection=f.worlds.load(f.scope,f.identity.timeline_id)
            return f.attention.plan_health(projection,[],[],f.attention.semantic_dependency_hashes(projection))
        def notices():
            with f.store._connect() as c:
                return c.execute("SELECT COUNT(*) FROM attention_items WHERE attention_kind='plan_dependency_available' AND timeline_id=?",
                    (f.store.active_timeline_id(f.scope),)).fetchone()[0]
        assert evaluate()["dependency_exception_count"]==1
        evaluate(); assert notices()==0
        checkpoint=f.journal.append(f.scope,"checkpoint.created",{"test":"before-availability"})
        actor["fields"]["available"]=field(True,"stale")
        assert evaluate()["dependency_exception_count"]==1 and notices()==0
        actor["fields"]["available"]=field(True)
        assert evaluate()["dependency_exception_count"]==0 and notices()==1
        f.attention=AttentionService(SmacxStore(f.root/"state.sqlite3"),f.journal,f.scope)
        evaluate(); assert notices()==1
        actor["fields"]["available"]=field(False)
        assert evaluate()["dependency_exception_count"]==1
        actor["fields"]["available"]=field(True)
        evaluate(); assert notices()==2
        revised=f.store.put_plan(f.scope,"landing","Western Landing","Revised requirement",dependencies=["transport-review"],
            last_confirmation=plan["last_confirmation"])
        f.journal.append(f.scope,"memory.plan",{"record":revised})
        evaluate(); assert notices()==2
        completed=f.store.put_plan(f.scope,"landing","Western Landing","Complete",status="completed")
        f.journal.append(f.scope,"memory.plan",{"record":completed})
        evaluate(); assert f.journal.replay(f.scope)["plan_dependency_health"]=={}
        f.journal.fork_timeline(f.scope,"timeline-review-restore",native_save_sha256="a"*64,
            from_event_hash=checkpoint["event_hash"],parent_timeline_id=f.identity.timeline_id)
        with f.store.transaction() as c:
            c.execute("UPDATE matches SET metadata_json=json_set(metadata_json,'$.active_memory_timeline',?) WHERE match_id=?",
                ("timeline-review-restore",f.scope.match_id))
        f.worlds.discard_future(f.scope,"timeline-review-restore")
        restored=f.journal.replay(f.scope)["plan_dependency_health"]
        assert len(restored)==1 and next(iter(restored.values()))["state"]=="unavailable"
        assert notices()==0
    print(json.dumps({"passed":True,"one_positive_per_current_transition":True,"unknown_never_available":True,
        "restart_dedupe":True,"plan_revision_and_completion_isolated":True,"rollback_state_and_attention":True}))


if __name__=="__main__":main()
