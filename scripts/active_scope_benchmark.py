#!/usr/bin/env python3
"""Simultaneous scope/watch workload; actual timings, not linear extrapolation."""
import os
import json
from pathlib import Path
import tempfile
import time
from semantic_consumer_contract_test import Fixture
from smacx_world_model import estimate_tokens


def timed(fn):
    start=time.perf_counter(); value=fn()
    return value,round((time.perf_counter()-start)*1000,3)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        width=int(os.environ.get("SMACX_SCOPE_BENCH_WIDTH","160"))
        height=int(os.environ.get("SMACX_SCOPE_BENCH_HEIGHT","80"))
        f=Fixture(Path(tmp),width=width,height=height)
        f.actor("base-scope","base",4,4,owner_ref="faction-1")
        f.actor("own-unit-scope","own_unit",4,4,owner_ref="faction-1",triad="land",movement_points=3,roles={"combat":True})
        f.save()
        registry,initial=timed(f.registry)
        scopes=[];creation=[]
        def create(subjects,definition):
            row,ms=timed(lambda:f.attention.create_watch("spatial_scope",subjects,definition,current_turn=4))
            scopes.append(row["watch_id"]);creation.append(ms)
        for radius in (1,2,4,8):create(["base-scope"],{"type":"proximity","radius":radius})
        regions=[ref for ref,row in registry.items() if row["kind"]=="region"]
        for ref in regions[:2]:create([ref],{"type":"geography"})
        route=f.service.query(mode="route",origin_ref="own-unit-scope",target_ref=f.at(20,4),detail="deep")
        create([route["route"]["route_ref"]],{"type":"route_corridor","radius":2})
        create(scopes[:2],{"type":"union"})
        create(scopes[2:4],{"type":"union"})
        for ref in scopes[:4]:f.attention.create_watch("region_entry",[ref],{},current_turn=4)
        _,registry_ms=timed(f.registry)
        descriptions,inspection=timed(lambda:[f.attention.inspect_scope(ref) for ref in scopes])
        events=f.movement(f.at(8,4),f.at(6,4))
        _,watch_ms=timed(lambda:f.attention.evaluate_watches([],temporal_events=events,observation_cursor=2,turn=4))
        print(json.dumps({"passed":True,"known_tiles":width*height//2,"active_scopes":len(scopes),"active_crossing_watches":4,
            "initial_registry_ms":initial,"creation_ms":creation,"registry_ms":registry_ms,
            "all_scope_inspection_ms":inspection,"watch_evaluation_ms":watch_ms,
            "maximum_descriptor_tokens":max(map(estimate_tokens,descriptions)),
            "native_interaction":False,"workload":"four proximities, two geography, one corridor, two unions, observed movement"}))


if __name__=="__main__":main()
