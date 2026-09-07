#!/usr/bin/env python3
"""Verify quoted incoming credits and peace effects in isolated matched-save workers."""
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
 results=[];completed=set();unobserved=[]
 with tempfile.TemporaryDirectory(prefix='smacx-truce-replay-') as tmp:
  control=ControlPlane(SmacxStore(Path(tmp)/'state.sqlite3'),Path(tmp)/'secrets');docker=DockerClient()
  manager=WorkerManager(control,docker,worker_image=os.environ['SMACX_TEST_WORKER_IMAGE'])
  source=manager.validate_game_source(os.environ['SMACX_TEST_GAME_SOURCE'],display_name='Isolated truce replay')
  runtime=manager.ensure_bundled_runtime();control.store.ensure_agent('agent-truce-replay','Truce replay')
  cases=[(t,r) for t in ('ENERGYTRUCE','ENERGYTREATY') for r in ('accept','reject')]
  for target,response in cases*5:
   if (target,response) in completed:continue
   worker=None
   try:
    match=control.create_solo_match('Isolated '+target+' '+response,'agent-truce-replay',faction_id=1)
    scope=MemoryScope(match['match']['match_id'],'agent-truce-replay',match['perspective']['perspective_id'])
    worker=manager.provision_worker(scope,source['game_source_id'],runtime['runtime_id'],autostart={'startup_save':'replay','faction_id':1},view_enabled=False)
    seed="from pathlib import Path;import os,sys;p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(sys.stdin.buffer.read());[os.chown(x,10001,10001) for x in [p,*list(p.parents)[:5]]]"
    subprocess.run(['docker','run','--rm','-i','--user','0','--entrypoint','python3','-v',worker['data_volume']+':/data',os.environ.get('SMACX_TEST_CONTROL_IMAGE','smacx-agent-control:dev'),'-c',seed,'/data/game/saves/agent/'+scope.match_id+'/replay.sav'],input=saved,check=True)
    manager.start_worker(worker['instance_id'],timeout=300)
    def call(op,**args):return manager._native_request(worker['instance_id'],op,timeout=args.pop('timeout',20),**args)
    play.bridge_request=call
    before=None;submitted=None;credits_before=None;quote=None;labels=[];deadline=time.monotonic()+240
    while time.monotonic()<deadline:
     s=call('semantic_snapshot').get('snapshot',{});kind=s.get('interaction',{}).get('kind');label=s.get('interaction',{}).get('popup_label','')
     if not s:time.sleep(.1);continue
     if before is None:
      before=s['turn'];print(json.dumps({'target':target,'case':response,'loaded_turn':before,'save_entry':name}),flush=True)
     if not labels or labels[-1]!=label:labels.append(label)
     if not submitted and s['turn']>before:
      unobserved.append({'target':target,'response':response,'reason':'target offer did not occur before turn advanced','labels':labels})
      print(json.dumps({'not_observed':unobserved[-1]}),flush=True);break
     if kind in ('waiting_for_engine','waiting_for_turn'):
      time.sleep(.1);continue
     if submitted and label!=target:
      factions=call('list_factions')['items'];other=next(f for f in factions if f['id']==5)
      relations=other['relations'];expected_truce=(target=='ENERGYTREATY' or response=='accept')
      assert relations['truce'] is expected_truce and relations['vendetta'] is (not expected_truce),other
      assert relations['treaty'] is (target=='ENERGYTREATY' and response=='accept'),other
      credits_after=s['faction']['energy_credits']
      assert credits_after-credits_before==(quote if response=='accept' else 0),{'before':credits_before,'after':credits_after,'quote':quote,'target':target,'response':response}
      results.append({'target':target,'response':response,'turn_before':before,'turn_after':s['turn'],
       'quote':quote,'credits_before':credits_before,'credits_after':credits_after,'receipt':submitted,
       'relations':relations,'labels':labels,'observed_follow_on':label,'full_turn_completed':False})
      completed.add((target,response));print(json.dumps({'native_case_passed':results[-1]}),flush=True)
      break
     frame=call('semantic_choices',kind='interaction' if kind!='turn' else 'game_management')
     choices=frame.get('choices',[])
     if label==target:
      options=[c for c in choices if c.get('command')=='respond_to_diplomatic_offer']
      assert {c.get('response') for c in options}=={'accept','reject'},frame
      selected=next(c for c in options if c['response']==response)
      terms=next(c for c in choices if c.get('kind')=='information')
      assert terms.get('counterpart_faction_id')==5,terms
      quote=selected['amount'];assert quote>0 and selected['incoming_energy_credits']==quote,selected
      assert selected['payment_direction']=='counterpart_to_self',selected
      credits_before=s['faction']['energy_credits']
      submitted=play.command(frame,'respond_to_diplomatic_offer',response=response,amount=quote)
      assert submitted.get('ok') and submitted.get('relationship_change_verified') is False and submitted.get('energy_change_verified') is False,submitted
      assert 'treasury and diplomatic state' in submitted.get('completion_semantics',''),submitted
      print(json.dumps({'target':target,'response':response,'quote':quote,'submitted':submitted}),flush=True)
     elif label in ('WANTTOTRUCE0','WANTTOTRUCE1','WANTTOTRUCE2','OFFERTRUCE','MUSTTRUCE','FACTIONTRUCE','FACTIONTREATY'):
      # Deliberately reach the conditional counteroffer only in this isolated test.
      first_response='reject' if target=='ENERGYTRUCE' or label=='FACTIONTREATY' else 'accept'
      result=play.command(frame,'respond_to_diplomatic_offer',response=first_response)
      assert result.get('ok'),result
     else:
      wanted=('respond_to_contact' if label in ('COMM','COMMDIPLO') else 'continue_diplomacy' if label.startswith('INTRO') else 'choose_diplomacy_option' if label=='DIPLO' else 'end_turn' if kind=='turn' and not submitted else 'acknowledge_popup')
      choice=next((c for c in choices if c.get('command')==wanted and (wanted!='choose_diplomacy_option' or c.get('option')=='finish')),None)
      if not choice:
       unobserved.append({'target':target,'response':response,'reason':'different negotiation branch','label':label,'choices':choices})
       print(json.dumps({'not_observed':unobserved[-1]}),flush=True);break
      args={'response':'accept'} if wanted=='respond_to_contact' else {'option':'finish'} if wanted=='choose_diplomacy_option' else {}
      result=play.command(frame,wanted,**args);assert result.get('ok'),result
     time.sleep(.15)
    else:raise AssertionError({'case':response,'timeout':True,'labels':labels})
   finally:
    if worker:
     manager.park_worker(worker['instance_id'])
     for volume,purpose in ((worker['network']['secret_volume'],'worker-secret'),(worker['data_volume'],'worker-data')):
      docker.require_owned(docker.inspect_volume(volume),manager.installation_id,purpose=purpose);docker.remove_volume(volume)
 assert len(completed)==4,{'missing_cases':sorted(set(cases)-completed),'unobserved_attempts':unobserved}
 print(json.dumps({'passed':True,'classification':'isolated matched-save energy-for-peace accept/reject and observed treasury/relationship effects','cases':results,'unobserved_attempts':unobserved}),flush=True)

if __name__=='__main__':main()
