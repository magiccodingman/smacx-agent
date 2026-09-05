#!/usr/bin/env python3
"""Current publication emits dependency availability before acknowledging its stage."""
import json
from pathlib import Path
import tempfile
from observation_collector_benchmark import NativeFixture
from semantic_consumer_contract_test import Fixture
from smacx_observation import ObservationCollector


def main():
    with tempfile.TemporaryDirectory() as tmp:
        f=Fixture(Path(tmp),width=32,height=16)
        native=NativeFixture(32,16)
        native.bases[0]["base_ref"]="base-review"
        def collector():return ObservationCollector(scope=f.scope,session_id="session-review",bridge_call=native,
            journal=f.journal,world_store=f.worlds,attention=f.attention)
        collector().collect_once()
        plan=f.store.put_plan(f.scope,"surplus","Explicit surplus","Wait for the stated mechanical dependency",
            dependencies=["base-review"],last_confirmation={"dependency_values":[
                {"ref":"base-review","field":"mineral_surplus","value":8}]})
        f.journal.append(f.scope,"memory.plan",{"record":plan})
        f.attention.capture_current_plan_dependencies()
        assert next(iter(f.journal.replay(f.scope)["plan_dependency_health"].values()))["state"]=="unavailable"
        native.bases[0]["mineral_surplus"]=8;native.revision+=1
        original=f.worlds.acknowledge_native_observation_publication
        def fail(*_a,**_k):raise RuntimeError("after_current_transition_before_ack")
        f.worlds.acknowledge_native_observation_publication=fail
        try:collector().collect_once()
        except RuntimeError as e:assert str(e)=="after_current_transition_before_ack"
        else:raise AssertionError("failure injection not reached")
        with f.store._connect() as c:
            assert c.execute("SELECT COUNT(*) FROM attention_items WHERE attention_kind='plan_dependency_available'").fetchone()[0]==1
        f.worlds.acknowledge_native_observation_publication=original
        collector().collect_once()
        native.revision+=1;collector().collect_once()
        with f.store._connect() as c:
            assert c.execute("SELECT COUNT(*) FROM attention_items WHERE attention_kind='plan_dependency_available'").fetchone()[0]==1
        assert f.journal.verify(f.scope)["ok"]
    print(json.dumps({"passed":True,"current_publication_wakeup":True,"post_transition_pre_ack_crash_retry_once":True,
        "unchanged_available_no_duplicate":True,"evidence":"native_shaped_collector_and_journal"}))


if __name__=="__main__":main()
