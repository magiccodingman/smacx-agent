#!/usr/bin/env python3
"""Real authenticated HTTP + journal/projection; native process state is a fixture."""
import json
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from observation_collector_benchmark import (NativeFixture, SmacxStore, MemoryScope,
    CampaignJournal, WorldStore, ObservationCollector, AttentionService)
from smacx_control import ControlPlane
from smacx_control_server import ControlHTTPServer
from smacx_operator import OperatorService, classify_health

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    store = SmacxStore(root/'state.sqlite3')
    store.ensure_agent('agent-operator', 'Operator')
    store.create_match(match_id='match-operator', display_name='Operator', mode='solo')
    store.create_perspective('match-operator','agent-operator',perspective_id='perspective-operator')
    scope = MemoryScope('match-operator','agent-operator','perspective-operator')
    store.register_instance(instance_id='instance-operator', scope=scope)
    control = ControlPlane(store, root/'secrets')
    spec = {'match_id': scope.match_id, 'instance_id': 'instance-operator'}
    control.list_worker_specs = lambda: [spec]
    control.get_worker_spec = lambda _: spec
    journal = CampaignJournal(root/'campaigns', timeline_resolver=store.active_timeline_id)
    worlds = WorldStore(store, root/'snapshots')
    attention = AttentionService(store, journal, scope)
    fixture = NativeFixture(32,16,contacts=1,ready_drop_units=1)
    ObservationCollector(scope=scope,session_id='session-operator',bridge_call=fixture,
        journal=journal,world_store=worlds,attention=attention).collect_once()
    state = {'instance_id':'instance-operator','running': True, 'paused':False,'health':'healthy',
             'mcp':{'running':True,'paused':False}}
    def quarantine(_, **kwargs):
        assert control.list_supervision_incidents(match_id=scope.match_id, active_only=True), 'missing durable fence'
        state['paused'] = True; state['mcp']['paused'] = True
    manager = SimpleNamespace(docker=SimpleNamespace(list_owned_containers=lambda *args: []), installation_id=store.installation_id(), _lifecycle_lock=threading.RLock(), worker_status=lambda _: dict(state), quarantine_match=quarantine)
    server = ControlHTTPServer(('127.0.0.1',0),control,root,worker_manager=manager,service_token='operator-fixture')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def call(action, method='GET', auth=True, data=None):
        headers={'Content-Type':'application/json'}
        if auth: headers['X-SMACX-Service-Token']='operator-fixture'
        request=Request(f'http://127.0.0.1:{server.server_port}/api/v1/matches/match-operator/operator/{action}',
            method=method,headers=headers,data=json.dumps(data or {}).encode() if method=='POST' else None)
        with urlopen(request) as response: return json.load(response)['report']
    try:
        for route, method in [('health','GET'),('inspect','GET'),('events','GET'),('pause','POST')]:
            try: call(route,method,False)
            except HTTPError as error: assert error.code==401
            else: raise AssertionError('unauthenticated operator access')
        assert call('health')['state']=='idle'
        view=call('inspect'); assert view['perspectives'][0]['objects']
        assert all('metadata' not in o for o in view['perspectives'][0]['objects'])
        first=call('events'); assert first['events']
        second=call('events?cursor='+first['next_cursor']); assert not second['events']
        journal.append(scope,'game.action',{'selected_action':'skip_unit','password':'secret'})
        third=call('events?cursor='+second['next_cursor'])
        assert len(third['events'])==1 and third['events'][0]['payload']['password']=='[redacted]'
        assert 'secret' not in json.dumps(third)
        paused=call('pause','POST');assert paused['containment_verified'] and not paused['checkpoint_created']
        again=call('pause','POST');assert again['incidents'][0]['incident_id']==paused['incidents'][0]['incident_id']
        assert call('health')['state']=='needs_attention'
        incident_id = paused['incidents'][0]['incident_id']
        resume_body = {'incident_id': incident_id}
        manager.recover_match = lambda *args, **kwargs: {'ok': False}
        try: call('resume', 'POST', data=resume_body)
        except HTTPError as error: assert error.code in (400,409)
        else: raise AssertionError('unverified recovery accepted')
        assert control.get_supervision_incident(incident_id)['status'] == 'operator_required'
        recovered_calls = []
        def recover(*args, **kwargs):
            recovered_calls.append(args)
            control.update_match_lifecycle(scope.match_id, 'running')
            return {'ok': True}
        manager.recover_match = recover
        assert not call('resume','POST',data=resume_body)['already_recovered']
        assert call('resume','POST',data=resume_body)['already_recovered']
        assert len(recovered_calls) == 1
        call('pause','POST')
        control.record_supervision_incident('instance-operator','capability_gap:fixture','operator_required',{})
        current = next(i for i in control.list_supervision_incidents(match_id=scope.match_id,active_only=True)
                       if i['incident_kind']=='operator_pause')
        try:call('resume','POST',data={'incident_id':current['incident_id']})
        except HTTPError as error:assert error.code in (400,409)
        else:raise AssertionError('unrelated incident bypassed')
        state['mcp']['paused']=False
        manager.quarantine_match=lambda _, **kwargs: None
        assert not call('pause','POST')['containment_verified'], 'must verify actual read-back'
        manager.worker_status=lambda _: (_ for _ in ()).throw(RuntimeError('private detail'))
        report=call('pause','POST');assert not report['containment_verified'] and 'private detail' not in json.dumps(report)
        assert journal.verify(scope)['ok']
        assert classify_health({'status':'error'},[],[],[],time.time())[0]=='needs_attention'
        assert classify_health({'status':'starting'},[],[],[],time.time())[0]=='starting'
        now=time.time()
        run={'status':'running','metadata':{'semantic_sample_unix':now,'semantic_progress_unix':now-10000}}
        # A long turn alone is not a deterministic stall.
        assert classify_health({},[run],[{'running':True,'health':'healthy'}],[],now)[0]=='observed_active'
        run['metadata']['semantic_sample_unix']=now-121
        assert classify_health({},[run],[{'running':True,'health':'healthy'}],[],now)[0]=='unknown'
        print(json.dumps({'passed':True,'http_authorization':True,'cursor_replay':True,
            'native_shaped_inspection':True,'pause_readback_and_fence':True,'checkpoint_not_invented':True,
            'slow_turn_not_deadlock':True,'journal_verified':True}))
    finally:server.shutdown();server.server_close();thread.join()
