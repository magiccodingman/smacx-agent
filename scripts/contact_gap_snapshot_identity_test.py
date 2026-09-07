#!/usr/bin/env python3
"""A same-square snapshot cannot reopen an episode closed by sentinel movement."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from publication_transaction_test import setup
from cross_publication_episode_test import raw, foreign, active, history, restart

for damage in (False,True):
 for failure in ('none','stage','frozen'):
  with tempfile.TemporaryDirectory() as tmp:
   f,native,collect,_=setup(Path(tmp));foreign(native,5);native.revision+=1
   collect().collect_once();old=active(f)
   native.events=[raw(1,'visible_unit_moved',5,-1),raw(2,'visible_unit_moved',-1,5)]
   if damage:
    native.events.append({**raw(3,'visible_unit_damaged',5,5),'value_before':10,'value_after':8})
    next(u for u in native.units if not u.get('owned'))['hp']=8
   native.revision+=1;c=collect()
   if failure=='stage':
    with patch.object(c,'_bundle',side_effect=RuntimeError('staged')):
     try:c.collect_once()
     except RuntimeError:pass
    restart(f);c=collect()
   if failure=='frozen':
    with patch.object(f.worlds,'replace_projection',side_effect=RuntimeError('frozen')):
     try:c.collect_once()
     except RuntimeError:pass
    restart(f);c=collect()
   c.collect_once();new=active(f)
   assert new and new!=old,(damage,failure,'snapshot resurrected closed identity',history(f))
   if damage:
    rows=[e for e in history(f) if e['event_kind']=='contact_damaged']
    assert len(rows)==1 and rows[0]['contact_ref']==new,rows
   restart(f)
   native.events.append({**raw(4,'visible_unit_damaged',5,5),'value_before':8 if damage else 10,'value_after':7})
   next(u for u in native.units if not u.get('owned'))['hp']=7
   native.revision+=1;collect().collect_once()
   assert active(f)==new
   assert [e for e in history(f) if e['event_kind']=='contact_damaged'][-1]['contact_ref']==new
   assert not any(e.get('contact_ref')==old and e['event_kind']=='contact_destroyed' for e in history(f))
   assert f.journal.verify(f.scope)['ok']
with tempfile.TemporaryDirectory() as tmp:
 f,native,collect,_=setup(Path(tmp));foreign(native,5);native.revision+=1
 collect().collect_once();old=active(f)
 native.events=[{**raw(1,'visible_unit_moved',5,5),'continuous_visibility':False}]
 native.revision+=1;collect().collect_once()
 assert active(f)!=old
 assert not any(e['event_kind']=='contact_moved' for e in history(f))

# Existing v1 checkpoints revalidate foreign continuity once, retaining owned
# lifecycles and normal frozen-publication recovery.
for failure in ('none','frozen'):
 with tempfile.TemporaryDirectory() as tmp:
  f,native,collect,_=setup(Path(tmp));foreign(native,5);native.revision+=1
  collect().collect_once();old=active(f)
  path=f.worlds._native_stage_path(f.scope,f.attention.timeline_id)
  stage=json.loads(path.read_text());stage['episode_state'].pop('schema_version',None)
  owned={'vehicle-handle-777':{'ref':'own-unit-777','birth_sequence':0}}
  stage['episode_state']['owned_lifecycles']=owned
  path.write_text(json.dumps(stage));restart(f);native.revision+=1;c=collect()
  if failure=='frozen':
   with patch.object(f.worlds,'replace_projection',side_effect=RuntimeError('frozen')):
    try:c.collect_once()
    except RuntimeError:pass
   restart(f);c=collect()
  c.collect_once();new=active(f);assert new!=old
  migrated=f.worlds.load_native_observation_stage(f.scope,f.attention.timeline_id)['episode_state']
  assert migrated['schema_version']==2 and migrated['owned_lifecycles']==owned
  restart(f);native.revision+=1;collect().collect_once();assert active(f)==new,'migration repeated'
  assert not any(e.get('contact_ref')==old and e['event_kind']=='contact_destroyed' for e in history(f))
  assert f.journal.verify(f.scope)['ok']
# New-timeline recovery may import current projection without a temporal stage.
# Its version marker must preserve checkpoint identities, unlike legacy data.
with tempfile.TemporaryDirectory() as tmp:
 f,native,collect,_=setup(Path(tmp));foreign(native,5);native.revision+=1
 collect().collect_once();original=active(f)
 path=f.worlds._native_stage_path(f.scope,f.attention.timeline_id)
 stage=json.loads(path.read_text());stage['episode_state']={};path.write_text(json.dumps(stage))
 restart(f);native.revision+=1;collect().collect_once()
 assert active(f)==original,'current-format projection was unnecessarily invalidated'
print(json.dumps({'passed':True,'evidence':'recorded turn97-shaped durable collector replay',
 'same_square_snapshot_does_not_resurrect_closed_episode':True,'post_gap_damage_binds_new_episode':True,
 'staged_frozen_and_subsequent_restart':True,'old_schema_revalidated_once_owned_identity_preserved':True,'current_projection_bootstrap_preserves_identity':True}))
