#!/usr/bin/env python3
"""Controlled-clock real lease/HTTP heartbeat; no native mechanics or paid inference."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch, MagicMock

import smacx_mcp as m
from smacx_attention import AttentionService, AttentionError
from smacx_journal import CampaignJournal
from smacx_store import SmacxStore, MemoryScope, StoreError

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    store = SmacxStore(root / "state.sqlite3")
    store.ensure_agent("agent-lease", "Lease")
    store.create_match(match_id="match-lease", display_name="Lease", mode="solo")
    store.create_perspective("match-lease", "agent-lease", perspective_id="perspective-lease")
    scope = MemoryScope("match-lease", "agent-lease", "perspective-lease")
    store.register_instance(instance_id="instance-lease", scope=scope)
    store.start_session(scope, "instance-lease", session_id="session-lease")
    journal = CampaignJournal(root / "campaigns", timeline_resolver=store.active_timeline_id)
    attention = AttentionService(store, journal, scope)
    with store.transaction() as db:
        db.execute("INSERT INTO harness_profiles(harness_profile_id,display_name,adapter_kind,status,created_unix,updated_unix) VALUES('profile-lease','Lease','hermes','ready',0,0)")
        db.execute("INSERT INTO harness_runs(run_id,harness_profile_id,match_id,agent_id,perspective_id,instance_id,native_session_id,status,created_unix,updated_unix) VALUES('run-lease','profile-lease','match-lease','agent-lease','perspective-lease','instance-lease','session-lease','running',0,0)")
    clock = [1000.0]
    with patch("smacx_attention.time.time", side_effect=lambda: clock[0]):
        try:
            attention.acquire_sovereign("episode-wrong-owner", "gameplay", run_id="run-missing", session_id="session-lease")
            raise AssertionError("unverified run acquired ownership")
        except AttentionError: pass
        assert attention.sovereign_state() is None
        token = attention.acquire_sovereign("episode-lease", "gameplay")
        def renew(tok=token, episode="episode-lease", run="run-lease", session="session-lease"):
            attention.renew_sovereign(tok, episode, run_id=run, session_id=session)
        original = attention.sovereign_state()
        for tick in range(1200, 4001, 200):
            clock[0] = tick
            renew()
        assert attention.sovereign_state()["acquired_unix"] == original["acquired_unix"]
        for args in ({"tok":"wrong"}, {"episode":"episode-other"}, {"run":"run-other"}, {"session":"session-other"}):
            try: renew(**args); raise AssertionError(args)
            except (AttentionError, StoreError): pass
        with store.transaction() as db: db.execute("UPDATE harness_runs SET desired_status='stopped'")
        try: renew(); raise AssertionError("stopped owner renewed")
        except AttentionError: pass
        with store.transaction() as db: db.execute("UPDATE harness_runs SET desired_status='running'")
        clock[0] = 5000
        try: renew(); raise AssertionError("expired lease revived")
        except AttentionError: pass
        assert attention.sovereign_state() is None
        new_token = attention.acquire_sovereign("episode-new-owner", "gameplay")
        try: renew(); raise AssertionError("replaced lease revived")
        except AttentionError: pass
        attention.release_sovereign(new_token, committed=False)

    # Exercise the HTTP admission + private heartbeat transport, including the
    # long-provider interval (heartbeats happen without another context build).
    assembler = MagicMock()
    assembler.snapshot.return_value = {"turn": 1}
    def build(**kwargs):
        return {"schema":"smacx.runtime-context.v1", "identity":{},
                "episode":{"episode_id":kwargs['episode_id']},
                "attention":attention.lease(kwargs['episode_id'])}
    assembler.build.side_effect = build
    server = ThreadingHTTPServer(("127.0.0.1",0),m._RuntimeContextHandler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    base = f"http://127.0.0.1:{server.server_port}/runtime-context"
    with patch.object(m._RuntimeContextHandler,"_authorized",return_value=True), \
         patch.object(m,"_refresh_request_world",return_value={"ok":True}), \
         patch.object(m,"_managed_scope_identity",return_value=(scope.match_id,"session-lease",scope.agent_id,scope.perspective_id)), \
         patch.object(m,"controller_chat_attention"), patch.object(m,"diagnostic_record"), \
         patch.object(m,"_runtime_services",return_value=(assembler,attention)), \
         patch.object(m,"RUNTIME_EPISODE_TOKENS",{}), patch.object(m,"RUNTIME_EPISODE_TURNS",{}), \
         patch("smacx_attention.time.time",side_effect=lambda:clock[0]):
        thread.start()
        try:
            with urlopen(base+"?episode_id=episode-http&run_id=run-lease&session_id=session-lease") as response:
                value=json.load(response)
            receipt=value['authority_heartbeat']
            assert receipt['token'] not in json.dumps(value['runtime_context'])
            state=dict(m.RUNTIME_EPISODE_TURNS['episode-http'])
            state['boundary']={'turn_handoff_required':{'required':True}}
            m.RUNTIME_EPISODE_TURNS['episode-http']=state
            for tick in [5200,5600,6000,6400]:
                clock[0]=tick
                req=Request(base+'/heartbeat',data=json.dumps({**receipt,'run_id':'run-lease','session_id':'session-lease'}).encode(),headers={'Content-Type':'application/json'})
                assert json.load(urlopen(req))['ok']
            assert assembler.build.call_count == 1
            assert m.RUNTIME_EPISODE_TURNS['episode-http'] == state, 'heartbeat changed turn fence'
            with patch.object(store, 'active_timeline_id', return_value='timeline-recovered'):
                try: m._renew_runtime_authority('episode-http',receipt['token'],'run-lease','session-lease');raise AssertionError('old timeline renewed')
                except AttentionError:pass
            with patch.object(m,'MANAGED_ATTACHED',True):
                assert m._sovereign_gameplay_gate('Action')['turn_handoff_required']['required']
            clock[0]=7400
            try: urlopen(req);raise AssertionError('expired HTTP owner revived')
            except HTTPError as exc:assert exc.code==409
            with patch.object(m,'MANAGED_ATTACHED',True), patch.object(m,'_bound_scope_identity',return_value=(scope.match_id,'session-lease',scope.agent_id,scope.perspective_id)):
                denied=m.smac_memory_update('summary',scope.match_id,'session-lease','r1','{}')
                assert denied['episode_restart_required'] and denied['persistence']['journal_committed'] is False
                assert m._sovereign_gameplay_gate('Action')['episode_restart_required']
            # A lost MCP cache or timeline cannot adopt the old heartbeat.
            m.RUNTIME_EPISODE_TOKENS.clear()
            try: m._renew_runtime_authority('episode-http',receipt['token'],'run-lease','session-lease');raise AssertionError('restart adopted token')
            except AttentionError:pass
        finally:
            server.shutdown();server.server_close();thread.join(5)

print(json.dumps({'passed':True,'long_episode_renewed_without_context_rebuild':True,
 'expired_replaced_wrong_run_session_rejected':True,'turn_fence_unchanged':True,
 'private_receipt_not_provider_visible':True,'dead_episode_memory_blocked':True}))
