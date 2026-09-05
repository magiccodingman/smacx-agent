#!/usr/bin/env python3
"""Production collector publication with simultaneous scopes and observed crossing."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from observation_collector_benchmark import NativeFixture
from semantic_consumer_contract_test import Fixture
from smacx_observation import ObservationCollector


def main():
    width=int(os.environ.get("SMACX_SCOPE_BENCH_WIDTH","160"))
    height=int(os.environ.get("SMACX_SCOPE_BENCH_HEIGHT","80"))
    with tempfile.TemporaryDirectory() as tmp:
        f=Fixture(Path(tmp),width=width,height=height)
        native=NativeFixture(width,height,contacts=1,ready_drop_units=1)
        native.units[0]["tile_id"]=4
        collector=ObservationCollector(scope=f.scope,session_id="session-review",bridge_call=native,
            journal=f.journal,world_store=f.worlds,attention=f.attention)
        ticks=[];stop=threading.Event()
        def probe():
            while not stop.wait(.01):ticks.append(time.perf_counter())
        thread=threading.Thread(target=probe,daemon=True);thread.start()
        try:
            initial=collector.collect_once()
            f.identity=collector.projector.identity if hasattr(collector,"projector") else f.identity
            # Use the collector's authoritative identity, not the fixture seed.
            projection=f.worlds.load(f.scope,collector.timeline_id)
            scopes=[]
            def create(refs,definition):
                scopes.append(f.attention.create_watch("spatial_scope",refs,definition,current_turn=50)["watch_id"])
            started=time.perf_counter()
            for radius in (1,2,4,8):create(["base-0"],{"type":"proximity","radius":radius})
            registry=f.attention._semantic_registry(projection,[])
            for ref in [ref for ref,row in registry.items() if row["kind"]=="region"][:2]:create([ref],{"type":"geography"})
            route=f.service.query(mode="route",origin_ref="own-unit-2000",target_ref="location-1",detail="deep")
            create([route["route"]["route_ref"]],{"type":"route_corridor","radius":1})
            create(scopes[:2],{"type":"union"});create(scopes[2:4],{"type":"union"})
            for ref in scopes[:4]:f.attention.create_watch("region_entry",[ref],{"relationship":"hostile"},current_turn=50)
            creation_ms=(time.perf_counter()-started)*1000
            native.events=[{"sequence":1,"kind":"visible_unit_moved","turn":50,"subject_a":1000,"subject_b":2,
                "from_tile_id":4,"to_tile_id":1,"continuous_visibility":True,"relationship_at_occurrence":"hostile"},
                {"sequence":2,"kind":"visible_unit_lost","turn":50,"subject_a":1000,"subject_b":2}]
            native.units=native.units[1:];native.revision+=1
            published=collector.collect_once()
            with f.store._connect() as c:
                triggers=c.execute("SELECT COUNT(*) FROM attention_items WHERE attention_kind='watch_trigger'").fetchone()[0]
            assert triggers>0,"observed crossing disappeared when final contact was lost"
        finally:
            stop.set();thread.join(1)
        gap=max(((b-a)*1000 for a,b in zip(ticks,ticks[1:])),default=0)
        passed = gap<500 and published["collector_metrics"]["wall_ms"]<30000
        print(json.dumps({"passed":passed,"evidence":"native_shaped_full_collector_publication",
            "known_tiles":width*height//2,"active_scopes":len(scopes),"crossing_watches":4,
            "initial_collection_ms":initial["collector_metrics"]["wall_ms"],"scope_creation_total_ms":round(creation_ms,3),
            "active_collection_ms":published["collector_metrics"]["wall_ms"],"UI_probe_max_gap_ms":round(gap,3),
            "observed_crossing_attention_count":triggers,"loss_in_same_publication":True}), flush=True)
        assert passed,(gap,published["collector_metrics"])


if __name__=="__main__":main()
