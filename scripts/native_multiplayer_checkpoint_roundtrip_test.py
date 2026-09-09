#!/usr/bin/env python3
"""Explicit operator-only native save/reload test; never resumes sovereigns.

Run in the control environment with normal supervisors stopped. The match must
already be stopped. Uses a separate native save slot, leaves paired checkpoint
metadata untouched, and freezes native workers in finally. This does not certify
an existing legacy AI-memory capsule: it tests newly captured native identities.
"""
import argparse, json, os, time
from pathlib import Path
from smacx_control_server import build_control
from smacx_worker_manager import WorkerManager
from smacx_docker import DockerClient
from smacx_checkpoint_policy import validate_peer_capsules

p=argparse.ArgumentParser();p.add_argument('--match',required=True);p.add_argument('--allow-native-restart',action='store_true')
a=p.parse_args()
if not a.allow_native_restart:raise SystemExit('Explicit --allow-native-restart required')
c=build_control(Path('/var/lib/smacx'))
m=WorkerManager(c,DockerClient('/var/run/docker.sock'),worker_image=os.environ['SMACX_WORKER_IMAGE'],mcp_image=os.environ['SMACX_MCP_IMAGE'],network_name=os.environ['SMACX_DOCKER_NETWORK'],control_data_volume=os.environ['SMACX_CONTROL_DATA_VOLUME'])
match=c.get_match(a.match)
assert match['mode']=='lan'
assert not any(r['desired_status']=='running' for r in c.list_harness_runs() if r['match_id']==a.match)
ids=[s['instance_id'] for s in c.list_seats(a.match) if s.get('instance_id') and s.get('metadata',{}).get('delegation_status')!='active']
assert len(ids)>=2
host=match['host_instance_id'];slot='checkpoint_roundtrip_test'
def acknowledge_intros():
 for i in ids:
  snapshot=m._native_request(i,'semantic_snapshot',timeout=20)['snapshot']
  if snapshot.get('interaction',{}).get('popup_label') in ('INTRO','SIMULYOU','SIMULWHOSE'):
   options=m._native_request(i,'semantic_choices',kind='interaction',timeout=20)
   assert any(x.get('command')=='acknowledge_popup' for x in options.get('choices',[]))
   response=m._native_request(i,'semantic_command',command='acknowledge_popup',match_id=options['match_id'],session_id=options['session_id'],expected_revision=options['revision'],timeout=20)
   assert response.get('ok'),response
try:
 # Reload the untouched source save to avoid resuming paused LAN sockets whose
 # wall-clock disconnect timers expired during an operator investigation.
 m.ensure_bundled_runtime()
 m.park_match(a.match)
 source_slot=match['metadata']['recovery_checkpoint']['native_save_slot']
 m.start_lan_match(a.match,session_name='Checkpoint regression',profile='small_easy',resume_slot=source_slot,_defer_ready=True)
 acknowledge_intros()
 previous=None; stable=0
 for _ in range(60):
  acknowledge_intros()
  observations=[m._native_request(i,'semantic_snapshot',timeout=20)['snapshot'] for i in ids]
  signature=[(o['turn'],o['revision'],o['protocol']['phase']) for o in observations]
  stable=stable+1 if signature==previous else 0
  if stable>=3 and all(o['protocol']['phase'] in ('turn','wait') for o in observations):break
  previous=signature;time.sleep(.35)
 else:raise AssertionError({'native_replicas_did_not_settle':[(o['protocol'],o.get('interaction',{}).get('kind'),o.get('interaction',{}).get('popup_label')) for o in observations]})
 capsules={i:m._native_request(i,'semantic_identity_state',action='export',timeout=20) for i in ids}
 validate_peer_capsules(capsules)
 choices=m._native_request(host,'semantic_choices',kind='game_management',timeout=30)
 assert any(x.get('command')=='save_game' for x in choices.get('choices',[])),choices
 saved=m._native_request(host,'semantic_command',command='save_game',slot=slot,match_id=choices['match_id'],session_id=choices['session_id'],expected_revision=choices['revision'],timeout=30)
 assert saved.get('ok'),saved
 digest=m._checkpoint_save_digest(host,slot)
 for i in ids:
  assert m._native_request(i,'semantic_identity_state',action='export',timeout=20)['native_validation_hash']==capsules[i]['native_validation_hash']
 m.park_match(a.match)
 m.start_lan_match(a.match,session_name='Checkpoint regression',profile='small_easy',resume_slot=slot,_defer_ready=True)
 assert m._checkpoint_save_digest(host,slot)==digest
 for i in ids:
  capsule={k:v for k,v in capsules[i].items() if k!='ok'}
  result=m._native_request(i,'semantic_identity_state',action='import',timeout=20,**capsule)
  assert result.get('restored'),result
  observed=m._native_request(i,'semantic_snapshot',timeout=20)['snapshot']
  assert observed['turn']==capsule['turn']
 for _ in range(3):
  acknowledge_intros();time.sleep(.2)
 for i in ids:
  assert m._native_request(i,'semantic_snapshot',timeout=20)['snapshot']['protocol']['phase'] in ('turn','wait')
 print(json.dumps({'passed':True,'managed_seats':len(ids),'turn':saved.get('turn'),'exact_native_hash_and_handle_roundtrip':True,'collectors_and_sovereigns_started':False,'legacy_checkpoint_repaired':False}),flush=True)
finally:
 c.update_match_lifecycle(a.match,'error',metadata={'recovery_required':True})
 m.quarantine_match(a.match,stop_collectors=True)
