#!/usr/bin/env python3
"""Two isolated native clients: legal development, synchronization and native save/reload.

Requires an operator-supplied multiplayer save; the production campaign is untouched.
Test-only fixtures are applied identically on both peers before guarded commands.
"""
import hashlib,json,os,subprocess,tempfile,time
from pathlib import Path
from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import MemoryScope,SmacxStore
from smacx_worker_manager import WorkerManager


def exercise(manager, ids, *, full=True, pod_only=False):
    def call(i, op, **args):return manager._native_request(i,op,timeout=30,**args)
    def command(i,frame,name,**args):
        return call(i,'semantic_command',command=name,match_id=frame['match_id'],
                    session_id=frame['session_id'],expected_revision=frame['revision'],**args)
    def settled():
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            states=[call(i,'test_network_sync_status') for i in ids]
            if all(all(k in state for k in ('vehicles','bases','factions')) for state in states) \
            and all(states[0][k]==states[1][k] for k in ('vehicles','bases','factions')):
                return states[0]
            time.sleep(.2)
        raise AssertionError(('replicas diverged',states))
    def actionable():
        stable=0
        for _ in range(100):
            for i in ids:
                s=call(i,'semantic_snapshot')['snapshot']
                if s['interaction']['kind'] in ('turn','waiting_for_turn','waiting_for_engine'):continue
                f=call(i,'semantic_choices',kind='interaction')
                ack=next((r for r in f.get('choices',[]) if r.get('command')=='acknowledge_popup'),None)
                assert ack,(s['interaction'],f)
                r=command(i,f,'acknowledge_popup')
                assert r.get('ok') or r.get('error',{}).get('code')=='stale_state',r
            active=call(ids[0],'semantic_snapshot')['snapshot']['interaction']['kind']=='turn'
            stable=stable+1 if active else 0
            if stable>=4:return
            time.sleep(.2)
        raise AssertionError('host did not settle into actionable turn')
    actionable()
    faction=call(ids[0],'semantic_snapshot')['snapshot']['faction']['id']
    # Retire the save's pre-existing ready units through real guarded actions.
    # Native turn-entry selection can alter local working flags before any
    # development command; establish a synchronized baseline, never mask bits.
    for v in call(ids[0],'test_network_sync_status')['vehicles']:
        if v['faction_id']!=faction:continue
        f=call(ids[0],'semantic_choices',kind='unit_actions',unit_id=v['id'])
        if any(r.get('command')=='skip_unit' for r in f.get('choices',[])):
            r=command(ids[0],f,'skip_unit',unit_id=v['id']);assert r.get('ok'),r
    settled()
    results=[]
    def fixture(former,complete=0,moved=0):
        rows=[call(i,'test_multiplayer_development_fixture',faction_id=faction,former_id=former,complete=complete,moved=moved) for i in ids]
        assert rows[0]==rows[1] and rows[0].get('ok'),rows
        settled()
        return rows[0]
    def execute(fx,name,**args):
        actionable()
        frame=call(ids[0],'semantic_choices',kind='unit_actions',unit_id=fx['unit_id'])
        assert frame.get('ok') and 'choices' in frame, frame
        assert any(r.get('command')==name and all(r.get(k)==v for k,v in args.items()) for r in frame['choices']),(name,args,frame)
        bad=command(ids[0],frame,name,unit_id=999999,**args)
        assert not bad.get('ok'),bad
        receipt=command(ids[0],frame,name,unit_id=fx['unit_id'],**args)
        assert receipt.get('ok'),receipt
        duplicate=command(ids[0],frame,name,unit_id=fx['unit_id'],**args)
        assert not duplicate.get('ok'),duplicate
        if receipt.get('queued'):
            for _ in range(200):
                status=call(ids[0],'action_status',action_id=receipt['action_id'])
                if status.get('action',{}).get('status')!='pending':break
                time.sleep(.1)
            assert status['action']['status']=='completed',(receipt,status)
        return settled()
    pod_rows=[call(i,'test_multiplayer_supply_pod_fixture',faction_id=faction) for i in ids]
    assert pod_rows[0]==pod_rows[1] and pod_rows[0].get('supply_pod_present'),pod_rows
    pod_after=execute(pod_rows[0],'collect_supply_pod',target_tile_id=pod_rows[0]['target_tile_id'])
    pod_status=[call(i,'test_multiplayer_supply_pod_fixture',
                     target_tile_id=pod_rows[0]['target_tile_id']) for i in ids]
    assert all(row.get('ok') and row.get('supply_pod_present') is False for row in pod_status),pod_status
    results.append({'action':'collect_supply_pod','two_peers_verified':True,
                    'pod_removed':True,'native_outcome_not_predicted':True})
    if pod_only:
        print(json.dumps({'development_cases':results}),flush=True)
        return results,call,command,settled
    pod=fixture(-1);before=settled()
    after=execute(pod,'found_base')
    assert len(after['bases'])==len(before['bases'])+1 and len(after['vehicles'])==len(before['vehicles'])-1
    assert any(b['tile_id']==pod['tile_id'] and b['faction_id']==faction for b in after['bases'])
    results.append({'action':'found_base','two_peers_verified':True,'pod_consumed':True})
    for former in ((0,2,3,4,5,9,10) if full else (5,)):
        for complete in (0,1):
            fx=fixture(former,complete)
            before=settled();v=next(v for v in before['vehicles'] if v['id']==fx['unit_id'])
            after=execute(fx,'terraform',former_id=former)
            a=next(v for v in after['vehicles'] if v['id']==fx['unit_id'])
            if complete:
                assert a['tile_items']!=v['tile_items'] and a['order']==0,(former,v,a)
            else:
                assert a['movement_turns']>v['movement_turns'] and a['order']==former+4,(former,v,a)
            results.append({'action':'terraform','former_id':former,'completion':bool(complete),'order_after':a['order'],'work_after':a['movement_turns'],'two_peers_verified':True})
    fx=fixture(5)
    working=execute(fx,'terraform',former_id=5)
    wv=next(v for v in working['vehicles'] if v['id']==fx['unit_id'])
    cancelled=execute(fx,'activate_unit')
    cv=next(v for v in cancelled['vehicles'] if v['id']==fx['unit_id'])
    assert cv['order']==0 and cv['movement_turns']<=wv['movement_turns'],(wv,cv)
    results.append({'action':'activate_working_former','two_peers_verified':True})
    for moved in (0,1):
        fx=fixture(5,moved=moved);held=execute(fx,'hold_unit')
        hv=next(v for v in held['vehicles'] if v['id']==fx['unit_id'])
        activated=execute(fx,'activate_unit')
        av=next(v for v in activated['vehicles'] if v['id']==fx['unit_id'])
        assert av['order']==0,(hv,av)
        if moved:assert av['moves_spent']==hv['moves_spent'],(hv,av)
        else:assert av['moves_spent']==0,(hv,av) # Native Thinker refunds an unused skip.
        results.append({'action':'activate_unit','two_peers_verified':True,'had_moved':bool(moved),
                        'native_unused_skip_refund':not bool(moved)})
    print(json.dumps({'development_cases':results}),flush=True)
    return results,call,command,settled


def main():
    saved=Path(os.environ['SMACX_DEVELOPMENT_TEST_SAVE']).read_bytes()
    os.environ['SMACX_AGENT_TEST_MODE']='1';os.environ['SMACX_AGENT_TEST_LAN_HOST']='1'
    docker=DockerClient()
    with tempfile.TemporaryDirectory(prefix='smacx-development-') as tmp:
        root=Path(tmp);control=ControlPlane(SmacxStore(root/'state.sqlite3'),root/'secrets')
        manager=WorkerManager(control,docker,worker_image=os.environ['SMACX_TEST_WORKER_IMAGE'])
        source=manager.validate_game_source(os.environ['SMACX_TEST_GAME_SOURCE'],display_name='Isolated development')
        runtime=manager.ensure_bundled_runtime()
        agents=['agent-development-host','agent-development-peer']
        for a in agents:control.store.ensure_agent(a,a)
        created=control.create_lan_match('Isolated native development',agents);mid=created['match']['match_id'];workers=[]
        try:
            for n,seat in enumerate(created['seats']):
                control.update_lan_seat(mid,n,faction_id=n+1)
                w=manager.provision_worker(MemoryScope(mid,seat['agent_id'],seat['perspective_id']),source['game_source_id'],runtime['runtime_id'],autostart={'enabled':False},view_enabled=False);workers.append(w)
                seed='from pathlib import Path;import sys,os;p=Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(sys.stdin.buffer.read());[os.chown(x,10001,10001) for x in [p,*list(p.parents)[:5]]]'
                subprocess.run(['docker','run','--rm','-i','--user','0','--entrypoint','python3','-v',w['data_volume']+':/data',os.environ['SMACX_TEST_CONTROL_IMAGE'],'-c',seed,'/data/game/saves/agent/'+mid+'/replay.sav'],input=saved,check=True)
            print(json.dumps({'isolated_match':mid,'workers':[w['instance_id'] for w in workers]}),flush=True)
            manager.start_lan_match(mid,session_name='Development regression',resume_slot='replay',_defer_ready=True)
            ids=[w['instance_id'] for w in workers]
            pod_only=os.environ.get('SMACX_DEVELOPMENT_POD_ONLY')=='1'
            cases,call,command,settled=exercise(manager,ids,pod_only=pod_only)
            if pod_only:
                print(json.dumps({'passed':True,'cases':cases,
                    'save_sha256':hashlib.sha256(saved).hexdigest(),
                    'two_peer_effect_verified':True}),flush=True)
                return
            before=settled()
            f=call(ids[0],'semantic_choices',kind='game_management')
            save=command(ids[0],f,'save_game',slot='development')
            assert save.get('ok'),save
            digest=manager._checkpoint_save_digest(ids[0],'development')
            manager.park_match(mid)
            manager.start_lan_match(mid,session_name='Development restored',resume_slot='development',_defer_ready=True)
            assert manager._checkpoint_save_digest(ids[0],'development')==digest
            after=settled()
            for k in ('vehicles','bases'):assert before[k]==after[k],('save_reload_changed',k)
            # Cross the native host turn normally, then repeat from the remote
            # player's seat: both directions must synchronize, not only host writes.
            for _ in range(100):
                # A restored peer may have a passive introduction/result
                # popup even while the authority is completing the prior
                # player's turn. Resolve those interactions on their owning
                # client before evaluating the network handoff.
                for peer in ids:
                    peer_snap=call(peer,'semantic_snapshot')['snapshot']
                    if peer_snap['interaction']['kind'] in ('turn','waiting_for_turn','waiting_for_engine'):
                        continue
                    peer_frame=call(peer,'semantic_choices',kind='interaction')
                    peer_ack=next((r for r in peer_frame.get('choices',[])
                                   if r.get('command')=='acknowledge_popup'),None)
                    if peer_ack:
                        r=command(peer,peer_frame,'acknowledge_popup')
                        assert r.get('ok') or r.get('error',{}).get('code')=='stale_state',r
                snap=call(ids[0],'semantic_snapshot')['snapshot']
                if snap['interaction']['engine_state'].get('current_faction_id')==2:break
                kind='interaction' if snap['interaction']['kind']!='turn' else 'game_management'
                f=call(ids[0],'semantic_choices',kind=kind)
                ack=next((r for r in f.get('choices',[]) if r.get('command')=='acknowledge_popup'),None)
                if ack:command(ids[0],f,'acknowledge_popup');time.sleep(.2);continue
                issued=False
                for v in call(ids[0],'test_network_sync_status')['vehicles']:
                    if v['faction_id']!=1:continue
                    choices=call(ids[0],'semantic_choices',kind='unit_actions',unit_id=v['id'])
                    if any(r.get('command')=='skip_unit' for r in choices.get('choices',[])):
                        r=command(ids[0],choices,'skip_unit',unit_id=v['id']);assert r.get('ok'),r
                        issued=True;break
                if not issued:
                    f=call(ids[0],'semantic_choices',kind='game_management')
                    if any(r.get('command')=='end_turn' for r in f.get('choices',[])):
                        r=command(ids[0],f,'end_turn')
                        assert r.get('ok') or r.get('error',{}).get('code') in ('not_actionable','stale_state'),r
                time.sleep(.2)
            else:raise AssertionError('host did not hand off to remote player')
            remote_cases,*_=exercise(manager,list(reversed(ids)),full=False)
            cases.extend([{**r,'initiator':'remote_peer'} for r in remote_cases])
            print(json.dumps({'passed':True,'cases':cases,'save_sha256':hashlib.sha256(saved).hexdigest(),'native_save_reload_verified':True}),flush=True)
        except Exception:
            for w in workers:
                try:
                    print(json.dumps({'failed_native_snapshot':manager._native_request(w['instance_id'],'semantic_snapshot',timeout=10)}),flush=True)
                except Exception as exc:print(type(exc).__name__,str(exc)[:500],flush=True)
            raise
        finally:
            for w in reversed(workers):
                manager.park_worker(w['instance_id'])
                for name,purpose in ((w['data_volume'],'worker-data'),(w['network']['secret_volume'],'worker-secret')):
                    docker.require_owned(docker.inspect_volume(name),manager.installation_id,purpose=purpose);docker.remove_volume(name)

if __name__=='__main__':main()
