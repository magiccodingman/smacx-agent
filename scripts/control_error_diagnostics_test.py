#!/usr/bin/env python3
"""Authenticated lifecycle failures reach diagnostics without request secrets."""
import gzip
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from smacx_control import ControlPlane
from smacx_control_server import ControlHTTPServer
from smacx_store import SmacxStore
from smacx_worker_manager import WorkerManagerError
from smacx_diagnostic_summary import Metrics


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        os.environ['SMACX_DIAGNOSTICS_ROOT'] = str(root/'diagnostics')
        store = SmacxStore(root/'state.sqlite3')
        control = ControlPlane(store, root/'secrets')
        store.create_match(match_id='match-control-diagnostic', display_name='Test', mode='singleplayer')
        def fail(_, **kwargs):
            raise WorkerManagerError('checkpoint_test_failure:private-fixture-detail')
        server = ControlHTTPServer(('127.0.0.1',0), control, root,
            worker_manager=SimpleNamespace(recover_match=fail), service_token='fixture-service')
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            for authenticated in [False, True]:
                headers={'Content-Type':'application/json'}
                if authenticated: headers['X-SMACX-Service-Token']='fixture-service'
                request=Request(f'http://127.0.0.1:{server.server_port}/api/v1/matches/match-control-diagnostic/recover',
                    data=b'{"password":"private-request-detail"}',headers=headers)
                try: urlopen(request)
                except HTTPError as exc: assert exc.code == (409 if authenticated else 401)
                else: raise AssertionError('expected controlled failure')
                paths=list((root/'diagnostics').rglob('*.gz'))
                assert bool(paths) == authenticated
            rows=[json.loads(line) for path in paths for line in gzip.open(path,'rt')]
            assert len(rows)==1 and rows[0]['actor']=='control-api'
            assert rows[0]['payload']['error_code']=='checkpoint_test_failure'
            assert 'private-' not in json.dumps(rows) and 'fixture-service' not in json.dumps(rows)
            metrics=Metrics();metrics.add(rows[0])
            assert metrics.as_dict()['failure_observations_by_layer']=={'control_operation_failed:checkpoint_test_failure':1}
            server.worker_manager.recover_match = lambda _, **kwargs: (_ for _ in ()).throw(
                WorkerManagerError('checkpoint_waiting_for_quiescence'))
            try: urlopen(request)
            except HTTPError as exc: assert exc.code == 409
            rows=[json.loads(line) for path in paths for line in gzip.open(path,'rt')]
            assert rows[-1]['kind']=='control_operation_deferred'
            metrics.add(rows[-1])
            assert metrics.as_dict()['failure_observations_by_layer']=={'control_operation_failed:checkpoint_test_failure':1}
            server.operations = SimpleNamespace(campaign_diagnostics=fail)
            before = len(rows)
            for authenticated in (False, True):
                export_headers = {'X-SMACX-Service-Token': 'fixture-service'} if authenticated else {}
                export = Request(f'http://127.0.0.1:{server.server_port}/api/v1/matches/match-control-diagnostic/diagnostics', headers=export_headers)
                try: urlopen(export)
                except HTTPError as exc: assert exc.code == (409 if authenticated else 401)
                else: raise AssertionError('expected export failure')
                rows=[json.loads(line) for path in paths for line in gzip.open(path,'rt')]
                assert len(rows) == before + int(authenticated)
            assert rows[-1]['payload']['operation'] == 'diagnostics'
            assert rows[-1]['payload']['error_code'] == 'checkpoint_test_failure'
            assert 'private-' not in json.dumps(rows) and 'fixture-service' not in json.dumps(rows)
            refresh_requests=[]
            def recover(match_id, *, refresh_runtime=False):
                refresh_requests.append((match_id,refresh_runtime))
                return {'ok':True}
            server.worker_manager.recover_match=recover
            for body,expected in [({},False),({'refresh_runtime':True},True),
                                  ({'refresh_runtime':'true'},False),({'refresh_runtime':False},False)]:
                with urlopen(Request(request.full_url,data=json.dumps(body).encode(),headers=headers)) as response:
                    assert json.load(response)['ok']
                assert refresh_requests[-1]==('match-control-diagnostic',expected)
        finally:
            server.shutdown();server.server_close();thread.join(2)
    print(json.dumps({'passed':True,'authenticated_http_failure_captured':True,
        'anonymous_request_creates_no_stream':True,'raw_request_and_exception_details_omitted':True}))


if __name__=='__main__':main()
