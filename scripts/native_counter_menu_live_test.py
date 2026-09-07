#!/usr/bin/env python3
"""Isolated stock proposal_menu counteroffer routing; production campaign stays paused."""
import json
import os
from pathlib import Path
import tempfile
import time
import semantic_playthrough as play
from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import SmacxStore, MemoryScope
from smacx_worker_manager import WorkerManager


def main():
    assert os.environ.get('SMACX_AGENT_TEST_MODE') == '1'
    assert os.environ.get('SMACX_ACCEPTANCE_MANAGED_ACTIONS') == '1'
    docker = DockerClient()
    with tempfile.TemporaryDirectory(prefix='smacx-end-turn-') as tmp:
        control = ControlPlane(SmacxStore(Path(tmp)/'state.sqlite3'), Path(tmp)/'secrets')
        manager = WorkerManager(control, docker,
            worker_image=os.environ['SMACX_TEST_WORKER_IMAGE'])
        worker = None
        try:
            source = manager.validate_game_source(os.environ['SMACX_TEST_GAME_SOURCE'], display_name='End turn native test')
            runtime = manager.ensure_bundled_runtime()
            control.store.ensure_agent('agent-end-turn-test', 'End turn test')
            match = control.create_solo_match('End turn native test', 'agent-end-turn-test', faction_id=4)
            scope = MemoryScope(match['match']['match_id'], 'agent-end-turn-test', match['perspective']['perspective_id'])
            worker = manager.provision_worker(scope, source['game_source_id'], runtime['runtime_id'],
                autostart={'enabled': True, 'difficulty': 0, 'world_size': 0, 'faction_id': 4}, view_enabled=False)
            manager.start_worker(worker['instance_id'], timeout=300)
            def call(op, **args):
                timeout = args.pop('timeout', 20)
                return manager._native_request(worker['instance_id'], op, timeout=timeout, **args)
            play.bridge_request = call
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                current=call('semantic_snapshot')['snapshot']
                if current['interaction']['kind']=='turn':break
                handled,result=play.handle_interaction(current)
                assert handled,(current['interaction'],result)
                time.sleep(.1)
            else:raise AssertionError('native startup did not reach turn')
            receipts=[]
            for proposal,option,expected in [('propose_pact','cancel',0),
                    ('propose_pact','goodwill',1),('propose_pact','name_price',2),
                    ('propose_pact','threaten',3),('propose_pact','energy_payment',5),
                    ('give_gift','all_research_data',7)]:
                assert call('test_counter_menu_start')['ok']
                wanted='COUNTER1' if proposal=='give_gift' else 'COUNTER0'
                deadline=time.monotonic()+45
                submitted=False
                while time.monotonic()<deadline:
                    status=call('test_counter_menu_status')
                    if status['stage']==2:
                        assert submitted and status['counter_id']==expected,status
                        assert status['proposal_id']==(0 if expected==0 else 1 if proposal=='give_gift' else 2),status
                        now=call('semantic_snapshot')['snapshot']
                        assert now['faction']['energy_credits']==500,now['faction']
                        receipts.append({'menu':wanted,'option':option,'native_return':status})
                        break
                    state=call('semantic_snapshot')['snapshot']
                    label=state['interaction'].get('popup_label','')
                    if label not in ('PROPOSAL',wanted):
                        time.sleep(.05);continue
                    choices=call('semantic_choices',kind='interaction')
                    name=proposal if label=='PROPOSAL' else option
                    selected=next((row for row in choices.get('choices',[]) if row.get('option')==name),None)
                    assert selected,(label,name,choices)
                    if label==wanted:
                        assert selected['native_option_id']==expected
                        assert not any(row.get('option')=='cancel_pact' for row in choices['choices'])
                        if wanted=='COUNTER0':
                            assert not any(row.get('command')=='give_energy_gift' for row in choices['choices'])
                            rejected=call('semantic_command',match_id=choices['match_id'],session_id=choices['session_id'],expected_revision=choices['revision'],command='give_energy_gift',amount=125)
                            assert not rejected.get('ok'),rejected
                    args=dict(match_id=choices['match_id'],session_id=choices['session_id'],expected_revision=choices['revision'],command='choose_diplomacy_option',option=name)
                    result=call('semantic_command',**args)
                    assert result.get('ok'),result
                    if label==wanted:
                        submitted=True
                        duplicate=call('semantic_command',**args)
                        assert not duplicate.get('ok'),duplicate
                    time.sleep(.05)
                else:raise AssertionError(('native counter menu deadline',proposal,option))
            print(json.dumps({'passed':True,'stock_native_proposal_menu_cases':receipts,
                'energy_not_transferred_by_selector':True,'duplicate_rejected':True,
                'classification':'Actual stock menu and native return values; downstream negotiated deal effects separate'}),flush=True)
        finally:
            if worker:
                manager.park_worker(worker['instance_id'])
                for name, purpose in ((worker['network']['secret_volume'], 'worker-secret'), (worker['data_volume'], 'worker-data')):
                    docker.require_owned(docker.inspect_volume(name), manager.installation_id, purpose=purpose)
                    docker.remove_volume(name)

if __name__ == '__main__':
    main()
