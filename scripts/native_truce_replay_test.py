#!/usr/bin/env python3
"""Replay an operator-supplied native save in isolated workers; never resumes its campaign."""
import json,os,subprocess,tempfile,time,zipfile
from pathlib import Path
import semantic_playthrough as play
from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import SmacxStore,MemoryScope
from smacx_worker_manager import WorkerManager

def main():
 bundle=Path(os.environ['SMACX_TRUCE_REPLAY_BUNDLE'])
 with zipfile.ZipFile(bundle) as z:
  name=os.environ.get('SMACX_TRUCE_REPLAY_ENTRY') or next(x for x in z.namelist() if x.startswith('saves/') and x.endswith('.sav'))
  saved=z.read(name)
 results=[]
 with tempfile.TemporaryDirectory(prefix='smacx-truce-replay-') as tmp:
  control=ControlPlane(SmacxStore(Path(tmp)/'state.sqlite3'),Path(tmp)/'secrets');docker=DockerClient()
  manager=WorkerManager(control,docker,worker_image=os.environ['SMACX_TEST_WORKER_IMAGE'])
  source=manager.validate_game_source(os.environ['SMACX_TEST_GAME_SOURCE'],display_name='Isolated truce replay')
  runtime=manager.ensure_bundled_runtime();control.store.ensure_agent('agent-truce-replay','Truce replay')
  for response in ('accept','reject'):
   worker=None
   try:
    match=control.create_solo_match('Isolated truce '+response,'agent-truce-replay',faction_id=1)
    scope=MemoryScope(match['match']['match_id'],'agent-truce-replay',match['perspective']['perspective_id'])
    worker=manager.provision_worker(scope,source['game_source_id'],runtime['runtime_id'],autostart={'startup_save':'replay','faction_id':1},view_enabled=False)
    seed="from pathlib import Path;import os,sys;p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(sys.stdin.buffer.read());[os.chown(x,10001,10001) for x in [p,*list(p.parents)[:5]]]"
    subprocess.run(['docker','run','--rm','-i','--user','0','--entrypoint','python3','-v',worker['data_volume']+':/data',os.environ.get('SMACX_TEST_CONTROL_IMAGE','smacx-agent-control:dev'),'-c',seed,'/data/game/saves/agent/'+scope.match_id+'/replay.sav'],input=saved,check=True)
    manager.start_worker(worker['instance_id'],timeout=300)
    def call(op,**args):return manager._native_request(worker['instance_id'],op,timeout=args.pop('timeout',20),**args)
    play.bridge_request=call
    before=None;submitted=None;labels=[];deadline=time.monotonic()+240
    while time.monotonic()<deadline:
     s=call('semantic_snapshot').get('snapshot',{});kind=s.get('interaction',{}).get('kind');label=s.get('interaction',{}).get('popup_label','')
     if not s:time.sleep(.1);continue
     if before is None:
      before=s['turn'];print(json.dumps({'case':response,'loaded_turn':before,'save_entry':name}),flush=True)
     if not labels or labels[-1]!=label:labels.append(label)
     if kind in ('waiting_for_engine','waiting_for_turn'):
      time.sleep(.1);continue
     if submitted and label not in ('WANTTOTRUCE0','WANTTOTRUCE1','WANTTOTRUCE2','OFFERTRUCE','MUSTTRUCE'):
      factions=call('list_factions')['items'];other=next(f for f in factions if f['id']==5)
      relations=other.get('relations',other)
      assert relations.get('truce') is (response=='accept') and relations.get('vendetta') is (response=='reject'),other
      results.append({'response':response,'turn_before':before,'turn_after':s['turn'],'receipt':submitted,'relations':relations,'labels':labels,'observed_follow_on':{'kind':kind,'label':label},'full_turn_completed':s['turn']>before and kind=='turn'})
      break
     frame=call('semantic_choices',kind='interaction' if kind!='turn' else 'game_management')
     choices=frame.get('choices',[])
     if label in ('WANTTOTRUCE0','WANTTOTRUCE1','WANTTOTRUCE2','OFFERTRUCE','MUSTTRUCE'):
      assert submitted is None,'truce offer repeated before completion'
      assert {c.get('response') for c in choices if c.get('command')=='respond_to_diplomatic_offer'}=={'accept','reject'},frame
      submitted=play.command(frame,'respond_to_diplomatic_offer',response=response)
      assert submitted.get('ok') and submitted.get('relationship_change_verified') is False,submitted
      print(json.dumps({'case':response,'truce_label':label,'submitted':submitted}),flush=True)
     else:
      wanted=('respond_to_contact' if label in ('COMM','COMMDIPLO') else 'continue_diplomacy' if label.startswith('INTRO') else 'choose_diplomacy_option' if label=='DIPLO' else 'end_turn' if kind=='turn' and not submitted else 'acknowledge_popup')
      choice=next((c for c in choices if c.get('command')==wanted and (wanted!='choose_diplomacy_option' or c.get('option')=='finish')),None)
      assert choice,{'unexpected_interaction':kind,'label':label,'turn':s['turn'],'choices':choices}
      args={'response':'accept'} if wanted=='respond_to_contact' else {'option':'finish'} if wanted=='choose_diplomacy_option' else {}
      result=play.command(frame,wanted,**args);assert result.get('ok'),result
     time.sleep(.15)
    else:raise AssertionError({'case':response,'timeout':True,'labels':labels})
   finally:
    if worker:
     manager.park_worker(worker['instance_id'])
     for volume,purpose in ((worker['network']['secret_volume'],'worker-secret'),(worker['data_volume'],'worker-data')):
      docker.require_owned(docker.inspect_volume(volume),manager.installation_id,purpose=purpose);docker.remove_volume(volume)
 print(json.dumps({'passed':True,'classification':'isolated matched-save native truce accept/reject and observed relationship effects','cases':results}),flush=True)

if __name__=='__main__':main()
