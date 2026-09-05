#!/usr/bin/env python3
"""Optional blockers do not emit aggregate blocked transitions for thresholds."""
from pathlib import Path
import tempfile
import json
from semantic_consumer_contract_test import Fixture,field


def main():
    with tempfile.TemporaryDirectory() as tmp:
        f=Fixture(Path(tmp))
        rows=[f.actor(ref,"base",2+i*2,2,available=i==0) for i,ref in enumerate(("a","b","c"))]
        f.save()
        plan=f.store.put_plan(f.scope,"threshold","Threshold","Any two explicit requirements")
        f.journal.append(f.scope,"memory.plan",{"record":plan})
        watch=f.attention.create_watch("milestone",["a","b","c"],{"mode":"at_least","at_least":2,
            "requirements":[{"ref":ref,"kind":"current_field","field":"available","value":True} for ref in ("a","b","c")]},
            current_turn=4,linked_plan_id=plan["plan_id"])
        f.objects.remove(rows[2]);f.save()
        assert not f.attention.evaluate_watches([],observation_cursor=2,turn=4)
        assert f.attention.inspect_watch(watch["watch_id"])["milestone"]["state"]=="pending"
        rows[1]["fields"]["available"]=field(True);f.save()
        assert len(f.attention.evaluate_watches([],observation_cursor=3,turn=4))==1
        assert not f.attention.evaluate_watches([],observation_cursor=4,turn=4)
        rows[1]["fields"]["available"]=field(False);f.save()
        transition=f.attention.evaluate_watches([],observation_cursor=5,turn=4)
        assert len(transition)==1 and transition[0]["matches"][0]["milestone"]["state"]=="pending"
        assert not f.attention.evaluate_watches([],observation_cursor=6,turn=4)
    print(json.dumps({"passed":True,"optional_blocker_does_not_block":True,"ready_pending_transitions_deduplicated":True}))


if __name__=="__main__":main()
