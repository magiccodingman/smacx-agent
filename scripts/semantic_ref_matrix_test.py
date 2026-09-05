#!/usr/bin/env python3
"""Representative supported consumers agree; unsupported consumers reject explicitly."""
import json
from pathlib import Path
import tempfile
from semantic_consumer_contract_test import Fixture
from smacx_attention import AttentionError
from smacx_plan_health import dependency_states
from smacx_milestones import evaluate_milestone
from smacx_world_types import content_hash
from smacx_regions import PHYSICAL_LAND_PROFILE, PHYSICAL_OCEAN_PROFILE
from smacx_specialists import SpecialistService, SpecialistError


def main():
    matrix=[]
    with tempfile.TemporaryDirectory() as tmp:
        f=Fixture(Path(tmp))
        f.actor("base-matrix","base",2,2,owner_ref="faction-1",threatened=True)
        f.actor("unit-matrix","own_unit",2,2,owner_ref="faction-1",triad="land",movement_points=3,roles={"combat":True})
        f.actor("contact-matrix","foreign_contact",4,2,owner_ref="faction-2",relationship="hostile",last_seen_turn=4)
        f.save()
        geography=f.service._derived_geography(f.worlds.load(f.scope,f.identity.timeline_id))
        by_profile={r.mobility_profile_ref:r.region_ref for r in geography["_region_projection"]}
        route=f.service.query(mode="route",origin_ref="unit-matrix",target_ref=f.at(8,2),detail="deep")["route"]["route_ref"]
        scope=f.attention.create_watch("spatial_scope",["base-matrix"],{"type":"proximity","radius":2},current_turn=4)["watch_id"]
        refs=[("physical_landmass",by_profile[PHYSICAL_LAND_PROFILE]),("physical_ocean_mass",by_profile[PHYSICAL_OCEAN_PROFILE]),
              ("mobility_region",by_profile["mobility-land-default"]),("theater",geography["active_theaters"][0]["theater_ref"]),
              ("route",route),("spatial_scope",scope),("base","base-matrix"),("own_unit","unit-matrix"),("foreign_contact","contact-matrix")]
        operation=None
        for kind,ref in refs:
            registry=f.registry()
            objects={row["object_ref"]:row for row in f.objects}
            query=f.service.query(mode="area",origin_ref=ref,detail="deep")
            assert query["ok"] and query.get("items"),(kind,query)
            if ref in registry:
                members=set(registry[ref]["location_refs"])
                assert all(str(row.get("location_ref") or row.get("object_ref")) in members for row in query["items"]),kind
            state,_=evaluate_milestone({"requirements":[{"ref":ref,"kind":"dependency_valid"}]},objects,registry,[])
            assert state["state"]=="ready",(kind,state)
            if kind == "foreign_contact":
                objects[ref]["fields"]["last_seen_turn"]["epistemic_status"] = "stale"
                stale_state,_=evaluate_milestone({"requirements":[{"ref":ref,"kind":"dependency_valid"}]},objects,registry,[])
                assert stale_state["state"]=="unknown"
                objects[ref]["fields"]["last_seen_turn"]["epistemic_status"] = "current"
            health=dependency_states([{"plan_id":"plan-matrix","dependencies":[ref]}],objects,set(objects)|set(registry))
            assert health["plan-matrix:"+ref]["state"]=="available"
            deps=f.attention.semantic_dependency_hashes(f.worlds.load(f.scope,f.identity.timeline_id))
            result=f.attention.upsert_operation(operation_id=operation,kind="semantic-review",objective="Inspect explicit reference",
                referenced_world_objects=[ref],source_world_epoch=f.identity.world_epoch,source_dependency_hash=content_hash({ref:deps[ref]}),
                source_world_revision=f.worlds.load(f.scope,f.identity.timeline_id)["world_revision"],current_turn=4)
            operation=result["operation_id"]
            if kind in {"base","own_unit","foreign_contact"}:
                try:f.attention.create_watch("region_entry",[ref],{},current_turn=4)
                except AttentionError as e:assert "spatial_footprint" in str(e)
                else:raise AssertionError("non-spatial object accepted as direct perimeter")
                scope_definition={"type":"base_radius"} if kind=="base" else {"type":"geography"}
                if kind=="base":f.attention.create_watch("spatial_scope",[ref],scope_definition,current_turn=4)
                else:
                    try:f.attention.create_watch("spatial_scope",[ref],scope_definition,current_turn=4)
                    except ValueError:pass
                    else:raise AssertionError("unsupported geographic source accepted")
                spatial="explicit rejection; base radius supported for base"
                specialist="immutable object snapshot supported by specialist contract suite"
            else:
                f.attention.create_watch("region_entry",[ref],{},current_turn=4)
                definition={"type":"route_corridor","radius":1} if kind=="route" else {"type":"union"} if kind=="spatial_scope" else {"type":"geography"}
                f.attention.create_watch("spatial_scope",[ref],definition,current_turn=4)
                try:SpecialistService(f.store,f.worlds,f.scope,journal=f.journal).commission(
                    faculty="world",objective="Inspect this semantic input",subject_refs=[ref])
                except SpecialistError as e:assert str(e)=="specialist_subject_requires_immutable_world_object"
                else:raise AssertionError("live-derived input accepted by object-only immutable snapshot")
                spatial="supported; nonempty private footprint"
                specialist="explicit rejection; nominate immutable objects instead"
            matrix.append({"kind":kind,"world_area":"pass","operation":"pass","milestone_dependency":"pass",
                           "spatial_consumers":spatial,"specialist":specialist})
    # Only paginated discovery exposes this frontier/mass; a compact anchor omits it.
    with tempfile.TemporaryDirectory() as tmp:
        f=Fixture(Path(tmp),width=256,height=4)
        f.objects=[row for row in f.objects if row["kind"]!="location" or
                   row.get("metadata",{}).get("native_y")==0 and row.get("metadata",{}).get("native_x",1)%4==0]
        f.save()
        anchor=f.service.anchor(context_length=65536)["payload"]
        visible={row["frontier_ref"] for row in anchor["frontiers"]}
        found=[]; continuation=""
        for _ in range(64):
            page=f.service.query(mode="area",origin_ref="world-geography",detail="compact",continuation=continuation)
            found.extend(page["items"])
            continuation=page.get("continuation") or ""
            if not continuation:break
        frontier=next(row["frontier_ref"] for row in found if row.get("frontier_ref") not in visible and row.get("frontier_ref"))
        f.attention.create_watch("frontier_contact",[frontier],{},current_turn=4)
        f.attention.create_watch("region_entry",[frontier],{},current_turn=4)
        wrapped=f.attention.create_watch("spatial_scope",[frontier],{"type":"geography"},current_turn=4)
        assert f.service.query(mode="area",origin_ref=wrapped["watch_id"])["ok"]
        assert f.registry()[frontier]["location_refs"]
        matrix.append({"kind":"omitted_frontier","paginated_discovery_watch_scope_area":"pass"})
        visible_masses={row.get("landmass_ref") for row in anchor["physical_masses"]}
        mass=next(row["landmass_ref"] for row in found if row.get("landmass_ref") and row["landmass_ref"] not in visible_masses)
        f.attention.create_watch("region_entry",[mass],{},current_turn=4)
        mass_scope=f.attention.create_watch("spatial_scope",[mass],{"type":"geography"},current_turn=4)
        assert f.service.query(mode="area",origin_ref=mass_scope["watch_id"])["items"]
        matrix.append({"kind":"omitted_physical_landmass","paginated_discovery_watch_scope_area":"pass"})
        for ref in (frontier,mass):
            projection=f.worlds.load(f.scope,f.identity.timeline_id)
            deps=f.attention.semantic_dependency_hashes(projection)
            f.attention.upsert_operation(operation_id=None,kind="discovery-review",objective="Inspect discovered geography",
                referenced_world_objects=[ref],source_world_epoch=f.identity.world_epoch,
                source_dependency_hash=content_hash({ref:deps[ref]}),source_world_revision=projection["world_revision"],current_turn=4)
            state,_=evaluate_milestone({"requirements":[{"ref":ref,"kind":"dependency_valid"}]},{},f.registry(),[])
            assert state["state"]=="ready"
            assert dependency_states([{"plan_id":"plan-discovered","dependencies":[ref]}],{},set(f.registry()))["plan-discovered:"+ref]["state"]=="available"
            try:SpecialistService(f.store,f.worlds,f.scope,journal=f.journal).commission(
                faculty="world",objective="Inspect discovered geography",subject_refs=[ref])
            except SpecialistError as e:assert str(e)=="specialist_subject_requires_immutable_world_object"
            else:raise AssertionError("derived discovery accepted by object-only specialist")
    print(json.dumps({"passed":True,"matrix":matrix}))


if __name__=="__main__":main()
