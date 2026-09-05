#!/usr/bin/env python3
"""One native handle can have distinct visible episodes within one publication."""
import json
from pathlib import Path
import tempfile
from semantic_consumer_contract_test import Fixture
from smacx_observation import ObservationCollector


def main():
    with tempfile.TemporaryDirectory() as tmp:
        f=Fixture(Path(tmp))
        collector=ObservationCollector(scope=f.scope,session_id="session-review",bridge_call=lambda *_a,**_k:{},
            journal=f.journal,world_store=f.worlds,attention=f.attention)
        prior={"object_ref":"contact-old","kind":"foreign_contact","metadata":{"native_observation_key":"vehicle-handle-9"}}
        current={**prior,"object_ref":"contact-new"}
        raw=[{"native_kind":kind,"native_sequence":i+1,"subject_a":9,"turn":4,
              "from_tile_id":i,"to_tile_id":i+1,"continuous_visibility":True,
              "relationship_at_occurrence":"hostile" if i==0 else "allied"}
             for i,kind in enumerate(("visible_unit_moved","visible_unit_lost","visible_unit_appeared","visible_unit_moved"))]
        collector._pending_native_events=raw
        events=collector._coalesce_native_events(prior_objects=[prior],current_objects=[current],turn=4)
        moved={row["contact_ref"]:row for row in events if row["event_kind"]=="contact_moved"}
        assert moved["contact-old"]["path"][0]["occurrence_sequence"]==1
        assert moved["contact-new"]["path"][0]["occurrence_sequence"]==4
        assert moved["contact-old"]["path"][0]["relationship"]["value"]=="hostile"
        assert moved["contact-new"]["path"][0]["relationship"]["value"]=="allied"
        assert all(len(row["path"])==1 for row in moved.values())
        collector._pending_native_events=[{**raw[0],"relationship_at_occurrence":"unknown"}]
        uncertain=collector._coalesce_native_events(prior_objects=[prior],current_objects=[prior],turn=4)
        assert uncertain[0]["path"][0]["relationship"]["epistemic_status"]=="unknown"
        # With no legitimate new episode identity, do not extend the retired one.
        collector._pending_native_events=[raw[0],raw[1],raw[3]]
        unknown=collector._coalesce_native_events(prior_objects=[prior],current_objects=[],turn=4)
        assert len([row for row in unknown if row["event_kind"]=="contact_moved"][0]["path"])==1
        collector._pending_native_events=[raw[0],raw[1],{**raw[3],"native_kind":"visible_unit_destroyed"}]
        destroyed=collector._coalesce_native_events(prior_objects=[prior],current_objects=[],turn=4)
        assert any(row["event_kind"]=="contact_destroyed" for row in destroyed)
        assert not any(row["event_kind"]=="contact_lost" for row in destroyed)
        collector._pending_native_events=[raw[0],{"native_kind":"contact_identity_reset"},raw[3]]
        reset=collector._coalesce_native_events(prior_objects=[prior],current_objects=[current],turn=4)
        assert len([row for row in reset if row["event_kind"]=="contact_moved"][0]["path"])==1
    print(json.dumps({"passed":True,"event_time_episode_identity_preserved":True,"visibility_gap_never_bridged":True}))


if __name__=="__main__":main()
