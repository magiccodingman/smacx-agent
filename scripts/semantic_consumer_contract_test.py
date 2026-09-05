#!/usr/bin/env python3
"""Managed semantic references retain membership, validity and event-time meaning."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import smacx_mcp as mcp
from smacx_attention import AttentionService, AttentionError
from smacx_journal import CampaignJournal
from smacx_observation import ObservationCollector
from smacx_regions import PHYSICAL_LAND_PROFILE, PHYSICAL_OCEAN_PROFILE
from smacx_store import MemoryScope, SmacxStore
from smacx_world import WorldService, WorldQueryError
from smacx_world_model import PerspectiveProjector
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, WorldObject, content_hash


def field(value, status="current"):
    return {"value": value, "epistemic_status": status, "source": "direct_sight"}


class Fixture:
    def __init__(self, root, width=24, height=10):
        self.root = root
        self.store = SmacxStore(root / "state.sqlite3")
        self.store.ensure_agent("agent-review", "Review")
        self.store.create_match(match_id="match-review", display_name="Review", mode="solo")
        self.store.create_perspective("match-review", "agent-review", perspective_id="perspective-review")
        self.scope = MemoryScope("match-review", "agent-review", "perspective-review")
        self.journal = CampaignJournal(root / "campaigns", timeline_resolver=self.store.active_timeline_id)
        self.worlds = WorldStore(self.store)
        self.identity = WorldIdentity(self.scope.match_id, self.scope.perspective_id,
                                      self.store.active_timeline_id(self.scope), "epoch-review")
        tiles = [{"tile_id": (x + width*y)//2, "x": x, "y": y, "visible_now": True,
                  "terrain": "land" if y < height-2 else "ocean"}
                 for y in range(height) for x in range(y % 2, width, 2)]
        projected = PerspectiveProjector(self.identity).project({"turn": 4,
            "map": {"width": width, "height": height, "horizontal_wrap": False},
            "tiles": tiles, "bases": [], "units": [], "factions": [], "global": []}, observation_sequence=1)
        self.objects = [row.as_dict(provider_safe=False) for row in projected["objects"]]
        self.width, self.height, self.cursor = width, height, 0
        self.attention = AttentionService(self.store, self.journal, self.scope)
        self.service = WorldService(self.worlds, self.scope)

    def at(self, x, y): return f"location-{(x + self.width*y)//2}"

    def actor(self, ref, kind, x, y, **values):
        row = {"object_ref": ref, "kind": kind, "status": "active", "location_ref": self.at(x,y),
               "fields": {key: field(value) for key,value in values.items()}}
        self.objects.append(row)
        return row

    def save(self):
        self.cursor += 1
        self.worlds.replace_projection(self.scope, self.identity, [WorldObject.from_dict(row) for row in self.objects],
            observation_cursor=self.cursor, action_revision=f"r{self.cursor}", continuity="complete", journal_head_hash="0"*64)

    def registry(self):
        return self.attention._semantic_registry(self.worlds.load(self.scope,self.identity.timeline_id), [])

    def movement(self, before, after, relationship="hostile", ending="visible_unit_lost", continuous=True):
        prior = {"object_ref":"contact-west", "kind":"foreign_contact", "status":"active",
                 "location_ref":before, "metadata":{"native_observation_key":"vehicle-handle-9"},
                 "fields":{"relationship":field("hostile"),"last_seen_turn":field(4)}}
        collector = ObservationCollector(scope=self.scope,session_id="session-review", bridge_call=lambda *_a,**_k: {},
            journal=self.journal,world_store=self.worlds,attention=self.attention)
        collector._pending_native_events = [{"native_kind":"visible_unit_moved", "subject_a":9,
            "from_tile_id":int(before.removeprefix("location-")), "to_tile_id":int(after.removeprefix("location-")),
            "native_sequence":self.cursor*10+1, "turn":4, "continuous_visibility":continuous,
            "relationship_at_occurrence":relationship}]
        if ending:
            collector._pending_native_events.append({"native_kind":ending,"subject_a":9,
                "native_sequence":self.cursor*10+2,"turn":4})
        return collector._coalesce_native_events(current_objects=[],prior_objects=[prior],turn=4)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        f=Fixture(Path(tmp))
        f.actor("base-west","base",2,2,owner_ref="faction-1",threatened=True)
        f.actor("base-east","base",20,2,owner_ref="faction-1",threatened=True)
        f.actor("contact-west","foreign_contact",4,2,owner_ref="faction-2",relationship="hostile",last_seen_turn=4)
        f.actor("contact-east","foreign_contact",22,2,owner_ref="faction-3",relationship="hostile",last_seen_turn=4)
        f.actor("own-unit-review","own_unit",2,2,owner_ref="faction-1",triad="land",movement_points=3,
                movement_scale=3,moves_remaining=3,roles={"combat":True})
        f.save()
        geography=f.service._derived_geography(f.worlds.load(f.scope,f.identity.timeline_id))
        masses={row.mobility_profile_ref:row for row in geography["_region_projection"]}
        direct_scope_pairs=[]
        for profile in (PHYSICAL_LAND_PROFILE,PHYSICAL_OCEAN_PROFILE,"mobility-land-default"):
            region=masses[profile]
            wrapped=f.attention.create_watch("spatial_scope",[region.region_ref],{"type":"geography"},current_turn=4)
            assert set(f.registry()[wrapped["watch_id"]]["location_refs"])==set(region.location_refs)
            pair=[]
            for kind in ("region_entry","region_exit"):
                pair.append([f.attention.create_watch(kind,[ref],{},current_turn=4)["watch_id"]
                             for ref in (region.region_ref,wrapped["watch_id"])])
            direct_scope_pairs.append(pair)
        events=f.movement(f.at(8,6),f.at(8,8))
        fired={row["watch_id"] for row in f.attention.evaluate_watches([],temporal_events=events,
            observation_cursor=20,turn=4)}
        for pair in direct_scope_pairs:
            assert all((a in fired)==(b in fired) for a,b in pair)
            assert sum(a in fired for a,_ in pair)==1
        theaters=geography["active_theaters"]
        west=next(row for row in theaters if "base-west" in row["subject_refs"])
        east=next(row for row in theaters if "base-east" in row["subject_refs"])
        assert west["theater_ref"]!=east["theater_ref"]
        western=f.attention.create_watch("spatial_scope",[west["theater_ref"]],{"type":"geography"},current_turn=4)
        watch=f.attention.create_watch("region_entry",[western["watch_id"]],{"relationship":"hostile"},current_turn=4)
        wrong=f.attention.evaluate_watches([],temporal_events=f.movement(f.at(18,2),f.at(20,2)),observation_cursor=21,turn=4)
        assert watch["watch_id"] not in {row["watch_id"] for row in wrong}
        perimeter=f.attention.create_watch("spatial_scope",["base-west"],{"type":"proximity","radius":1},current_turn=4)
        watch=f.attention.create_watch("region_entry",[perimeter["watch_id"]],{"relationship":"hostile"},current_turn=4)
        for index,ending in enumerate(("visible_unit_lost","visible_unit_destroyed",None)):
            # Final whereabouts/relationship are immaterial to an observed hostile occurrence.
            contact=next(row for row in f.objects if row["object_ref"]=="contact-west")
            contact["status"]="lost" if ending else "active"
            contact["fields"]["relationship"]=field("allied")
            contact["fields"]["last_seen_turn"]=field(4,"stale")
            f.save()
            matched=f.attention.evaluate_watches([],temporal_events=f.movement(f.at(6,2),f.at(4,2),ending=ending),
                observation_cursor=30+index,turn=4)
            assert watch["watch_id"] in {row["watch_id"] for row in matched}, ending
        assert not f.movement(f.at(6,2),f.at(4,2),continuous=False)[0].get("path")
        # Managed issuance, warm reuse, binding and world inspection share one private registry.
        with patch.object(mcp,"_managed_scope_identity",return_value=(f.scope.match_id,"session-review",f.scope.agent_id,f.scope.perspective_id)), \
             patch.object(mcp,"controller_world_service",return_value=(f.scope,f.service,f.attention)), \
             patch.object(mcp,"_refresh_managed_world",return_value={"ok":True}), \
             patch.object(mcp,"_resolve_managed_selectors",return_value=({"target_tile_id":29,"unit_id":1},{})), \
             patch.object(mcp,"_call",return_value={"ok":False,"error":"unit_has_no_airdrop_ability"}):
            route=mcp.smac_world(mode="route",origin_ref="own-unit-review",target_ref=f.at(10,2),detail="deep")
            ref=route["route"]["route_ref"]
            f.actor("unrelated-economy","economy_state",0,0,credits=10)
            f.save()
            reused=mcp.smac_world(mode="route",origin_ref="own-unit-review",target_ref=f.at(10,2),detail="deep")
            assert reused["cache"]["hit"] and reused["route"]["route_ref"]==ref
            with f.store._connect() as connection:
                stamp=connection.execute("SELECT world_revision FROM world_query_cache WHERE query_fingerprint=?",
                    (reused["cache"]["query_fingerprint"],)).fetchone()[0]
            assert stamp==route["world_revision"]<reused["world_revision"]
            corridor=mcp.smac_cognition(action="watch_create",kind="spatial_scope",subject_refs=[ref],
                predicate_json='{"type":"route_corridor","radius":1}')
            assert corridor["ok"],corridor
            scope_ref=corridor["watch_id"]
            inspected=mcp.smac_world(mode="area",origin_ref=scope_ref,detail="deep")
            assert inspected["ok"],inspected
            membership=set(f.registry()[scope_ref]["location_refs"])
            watched=mcp.smac_cognition(action="watch_create",kind="region_entry",subject_refs=[scope_ref],
                predicate_json='{"relationship":"hostile"}')
            assert watched["ok"],watched
            outside=next(row["object_ref"] for row in f.objects if row["kind"]=="location" and row["object_ref"] not in membership)
            crossing=f.attention.evaluate_watches([],temporal_events=f.movement(outside,next(iter(sorted(membership)))),
                observation_cursor=99,turn=4)
            assert watched["watch_id"] in {row["watch_id"] for row in crossing}
            assert mcp.smac_world(mode="area",origin_ref=scope_ref,detail="deep")["ok"]
            assert inspected["items"] and all(str(row.get("location_ref") or row.get("object_ref")) in membership
                for row in inspected["items"])
            assert inspected["result_token_estimate"]<=8192
            reopened=AttentionService(SmacxStore(f.root/"state.sqlite3"),f.journal,f.scope)
            assert reopened.inspect_scope(scope_ref)["validity"]=="current_dependencies"
            f.attention.create_watch("route_disruption",[ref],{},current_turn=4)
            current=f.worlds.load(f.scope,f.identity.timeline_id)
            deps=f.attention.semantic_dependency_hashes(current)
            f.attention.upsert_operation(operation_id=None,kind="route-review",objective="Use the nominated cached route",
                referenced_world_objects=[ref],source_world_epoch=f.identity.world_epoch,
                source_dependency_hash=content_hash({ref:deps[ref]}),
                source_world_revision=current["world_revision"],current_turn=4)
            tile=next(row for row in f.objects if row["object_ref"]==f.at(8,2))
            tile["fields"]["features"]=field(["fungus"])
            f.save()
            assert ref not in f.registry()
            try:f.service.query(mode="area",origin_ref=scope_ref)
            except WorldQueryError:pass
            else:raise AssertionError("invalidated scope remained inspectable")
    print(json.dumps({"passed":True,"direct_and_wrapped_mass_watches":True,"theater_footprints_do_not_expand":True,
        "event_time_survives_loss_destruction_and_diplomacy":True,"managed_route_cache_scope_area_chain":True}))


if __name__=="__main__":main()
