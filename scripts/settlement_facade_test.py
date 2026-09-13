"""Provider facade with controlled native receipts; not a live game-effects claim."""
import json
from types import SimpleNamespace
import smacx_mcp as m
import smacx_spatial_scope as spatial
from smacx_topology import KnownSquare, MapShape, PerspectiveTopology
from smacx_world import WorldService
from smacx_settlement import candidate_locations
from settlement_assistance_test import r
from copy import deepcopy
shape=MapShape(20,8,True)
squares=[KnownSquare(f'location-{y*10+x//2}',x,y,'land') for y in range(8) for x in range(y%2,20,2)]
topology=PerspectiveTopology(shape,squares)
objects={s.location_ref:{'kind':'location','object_ref':s.location_ref} for s in squares}
objects['base-home']={'kind':'base','object_ref':'base-home','location_ref':'location-11'}
projection={'action_revision':'r1'}
class W:
 store=None;scope=None
 def _projection(self):return SimpleNamespace(),projection
 def _objects(self,p):return objects
 def _topology(self,p):return topology
 _trim=staticmethod(WorldService._trim)
 _budget=lambda self,d,c:2048
w=W()
m._managed_scope_identity=lambda:('match-s','session-s','agent-s','perspective-s')
m._refresh_managed_world=lambda:{'ok':True}
m.controller_world_service=lambda *a,**k:(None,w,None)
spatial.semantic_spatial_registry=lambda *a:{}
m._semantic_selector_context=lambda *a:{'by_ref':{s.location_ref:int(s.location_ref.split('-')[1]) for s in squares}}
calls=[]
def native(op,**kw):
 assert op=='semantic_base_site_receipts';calls.append(kw)
 rows=[]
 for i in kw['target_tile_ids']:
  row=deepcopy(r) if kw.get('include_economy') else {}
  row.update(location_ref=f'location-{i}',tile_id=i,legal_for_land_colony=True,terrain_kind='land',known_radius=[{'yields':{'nutrients':i%3,'minerals':i%5}}])
  rows.append(row)
 return {'ok':True,'action_revision':projection['action_revision'],'items':rows}
m._call=native
out=m.smac_world('settlement',origin_ref='base-home',radius=5)
assert out['ok'],out
assert len(calls)==2 and len(calls[0]['target_tile_ids'])<=32 and len(calls[1]['target_tile_ids'])<=4
assert len(out['items'])>=3,out
assert out['result_token_estimate']<=2048
assert not m.smac_world('settlement',origin_ref='invented')['ok']
assert not m.smac_world('settlement',origin_ref='base-home',scenario_json='{"max_travel_turns":2}')['ok']
m._call=lambda *a,**k:{'ok':True,'action_revision':'changed','items':[]}
assert not m.smac_world('settlement',origin_ref='base-home')['ok']
print(json.dumps({'facade':'pass','shortlist_count':len(out['items']),'tokens':out['result_token_estimate'],'native_calls':2,'stale_guard':'pass'}))
