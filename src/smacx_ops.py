"""Browser-free operator client. Uses the same authenticated portal APIs as the UI."""
from __future__ import annotations
import argparse
import getpass
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
import fcntl
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler


class OpsError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OpsError('Unexpected HTTP redirect; configure the portal URL directly.')


class Client:
    def __init__(self, url, session_file, timeout=60):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
            raise OpsError('Supply an http(s) portal URL without embedded credentials.')
        self.url, self.path, self.timeout = url.rstrip('/'), Path(session_file), timeout
        self.jar = http.cookiejar.LWPCookieJar(str(self.path))
        if self.path.exists():
            if self.path.stat().st_mode & 0o077: raise OpsError('Session file must have mode 0600.')
            self.jar.load(ignore_discard=True)
        self.opener = build_opener(HTTPCookieProcessor(self.jar), NoRedirect())
        self.csrf = None

    def save_session(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent)
        os.close(fd)
        try:
            self.jar.save(temporary, ignore_discard=True)
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def request(self, path, method='GET', data=None, output=None):
        headers = {'Accept': 'application/json'}
        if method != 'GET':
            if not self.csrf: self.csrf = self.request('api/auth/csrf')['token']
            headers['X-CSRF-TOKEN'] = self.csrf
            headers['Content-Type'] = 'application/json'
        request = Request(self.url + '/' + path, method=method, headers=headers,
                          data=json.dumps(data if data is not None else {}).encode() if method != 'GET' else None)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if output:
                    if response.headers.get_content_type() != 'application/zip':
                        raise OpsError('Diagnostic endpoint did not return a ZIP archive.')
                    target = Path(output)
                    if target.exists(): raise OpsError('Output already exists; choose a new path.')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, temporary = tempfile.mkstemp(dir=target.parent)
                    try:
                        total = 0
                        with os.fdopen(fd, 'wb') as stream:
                            while chunk := response.read(1024 * 1024):
                                total += len(chunk)
                                if total > 1024 ** 3: raise OpsError('Diagnostic archive exceeds 1 GiB client limit.')
                                stream.write(chunk)
                        os.link(temporary, target)  # Never replace another investigator's evidence.
                        return {'path': str(target.resolve()), 'bytes': total}
                    finally: os.unlink(temporary)
                payload = json.loads(response.read(8 * 1024 * 1024 + 1))
        except HTTPError as error:
            try: code = json.loads(error.read(8192)).get('error', {}).get('code', 'http_error')
            except Exception: code = 'http_error'
            raise OpsError(f'HTTP {error.code}: {code}; mutation was not automatically retried.') from None
        except (URLError, TimeoutError) as error:
            raise OpsError('Service unavailable or timed out. A submitted mutation may have completed; inspect its state before retrying.') from None
        if not payload.get('ok'): raise OpsError(str(payload.get('error', {}).get('code', 'request_failed')))
        return payload.get('data')

    def login(self, username, password):
        result = self.request('api/auth/login', 'POST', {'username': username, 'password': password, 'rememberMe': True})
        if not result.get('isAuthenticated', result.get('authenticated', False)):
            raise OpsError('Login requires additional account verification.')
        self.csrf = None  # Antiforgery identity changes at sign-in.
        self.save_session()
        return {'authenticated': True}


def choose(items, requested, description):
    if requested:
        if not any(item['id'] == requested for item in items): raise OpsError(f'Unknown {description} ID.')
        return requested
    if len(items) != 1: raise OpsError(f'Select a {description} ID explicitly from catalog; {len(items)} available.')
    return items[0]['id']


ROSTER = ['peacekeepers', 'hive', 'university', 'morgan', 'spartans']


def create_match(client, args):
    catalog = client.request('api/catalog/lobby')
    source = choose(catalog['gameSources'], args.source, 'game source')
    runtime = choose(catalog['runtimes'], args.runtime, 'runtime')
    agent = choose(catalog['agents'], args.agent, 'AI profile')
    lobby = client.request('api/lobbies', 'POST', {
        'displayName': args.name, 'gameSourceId': source, 'runtimeId': runtime,
        'profile': 'alien-crossfire', 'mode': 'standard', 'worldSize': 'standard',
        'difficulty': 'librarian', 'randomMap': True, 'doOrDie': False,
        'allowSpectators': True, 'managedClientsOnly': False, 'graphitiEnabled': True,
        'requestId': args.request_id})
    match_id = lobby['matchId']
    if lobby['status'] == 'waiting' and not lobby.get('startupRequestedAt'):
        for index in range(7):
            lobby = client.request(f'api/lobbies/{quote(match_id, safe="")}/seats/{index}', 'PUT', {
                'controllerKind': 'agent' if index == 0 else 'native' if index < 5 else 'open',
                'agentId': agent if index == 0 else None,
                'factionId': ROSTER[index] if index < 5 else 'random', 'personalityId': 'none'})
    seats = sorted(lobby['seats'], key=lambda seat: seat['seatIndex'])
    if len(seats) != 7 or any(seat['controllerKind'] != ('agent' if i == 0 else 'native' if i < 5 else 'open')
            or (i < 5 and seat['requestedFactionId'] != ROSTER[i])
            or (i == 0 and (seat['requestedPersonalityId'] != 'none' or seat.get('agentId') != agent)) for i, seat in enumerate(seats)):
        raise OpsError(f'Roster verification failed for {match_id}; lobby was not started.')
    return {'match_id': match_id, 'roster_verified': True, 'lobby': lobby}


def emit(value, compact=False):
    print(json.dumps(value, ensure_ascii=False, indent=None if compact else 2), flush=True)


def preflight(client, expected=None, verify_checkout=False, images_file=None):
    report = client.request('api/operator/preflight')
    if expected and report['installation_id'] != expected:
        raise OpsError('Installation mismatch; no mutation submitted.')
    if verify_checkout:
        for name in ('smacx_operator.py', 'smacx_ops.py', 'smacx_worker_manager.py', 'smacx_control_server.py', 'smacx_mcp.py'):
            if report.get('operator_code_sha256', {}).get(name) != hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest():
                raise OpsError(f'Deployed operator code differs from this checkout: {name}. No mutation submitted.')
    if images_file:
        expected_images = json.loads(Path(images_file).read_text())
        for role in ('worker', 'mcp', 'harness'):
            if not expected_images.get(role) or report['images'][role]['id'] != expected_images[role]:
                raise OpsError(f'Deployed {role} image differs from release receipt. No mutation submitted.')
    catalog = client.request('api/catalog/lobby')
    report['catalog'] = catalog
    report['prerequisites_ready'] = report['prerequisites_ready'] and bool(
        catalog.get('controlAvailable', True) and all(catalog.get(k) for k in ('gameSources', 'runtimes', 'agents')))
    return report


def write_state(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def deployment(args):
    """Host-only read-back; no secrets, builds, tagging or lifecycle mutations."""
    def docker(*parts):
        try: result = subprocess.run(['docker', *parts], capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired: raise OpsError('Docker inspection timed out; deployment remains unverified.') from None
        if result.returncode: raise OpsError('Docker deployment inspection failed; verify Compose path, project and Docker access.')
        return result.stdout
    compose = ['compose', '-f', str(Path(args.compose).resolve()), '-p', args.project]
    config = json.loads(docker(*compose, 'config', '--format', 'json'))
    if not all(name in config.get('services', {}) for name in ('control-api', 'control-center')):
        raise OpsError('Compose must contain control-api and control-center services.')
    checks = []
    for service in ('control-api', 'control-center', 'specialist-supervisor', 'knowledge-service', 'graphiti-projector', 'graphiti-db'):
        if service not in config['services']: continue
        names = docker(*compose, 'ps', '-a', '-q', service).split()
        desired = config['services'][service]['image']
        image = json.loads(docker('image', 'inspect', desired))[0]['Id']
        actual = json.loads(docker('inspect', names[0]))[0] if len(names) == 1 else {}
        status = actual.get('State', {})
        labels = actual.get('Config', {}).get('Labels', {})
        checks.append({'service': service, 'expected_image_id': image, 'running_image_id': actual.get('Image'),
            'ok': bool(actual.get('Image') == image and status.get('Running') and not status.get('Paused')
                and status.get('Health', {}).get('Status', 'healthy') == 'healthy'
                and labels.get('com.docker.compose.project') == args.project),
            'health': status.get('Health', {}).get('Status')})
        if service == 'control-api':
            expected_env = config['services'][service].get('environment', {})
            actual_env = dict(v.split('=', 1) for v in actual.get('Config', {}).get('Env', []) if '=' in v)
            for name in ('SMACX_WORKER_IMAGE', 'SMACX_MCP_IMAGE', 'SMACX_HERMES_IMAGE', 'SMACX_DOCKER_NETWORK', 'SMACX_CONTROL_DATA_VOLUME'):
                checks.append({'setting': name, 'ok': bool(expected_env.get(name) and expected_env[name] == actual_env.get(name))})
    env = config['services']['control-api']['environment']
    images = {role: json.loads(docker('image', 'inspect', env[key]))[0]['Id'] for role, key in
              [('worker', 'SMACX_WORKER_IMAGE'), ('mcp', 'SMACX_MCP_IMAGE'), ('harness', 'SMACX_HERMES_IMAGE')]}
    report = {'schema': 'smacx.operator-deployment.v1', 'project': args.project,
              'sampled_unix': time.time(), 'verified': bool(checks) and all(c['ok'] for c in checks),
              'checks': checks, 'images': images}
    if args.output and report['verified']:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if output.exists(): raise OpsError('Image receipt already exists; use a new filename.')
        with output.open('x') as stream: json.dump(images, stream, indent=2)
    emit(report, True)
    return 0 if report['verified'] else 2


def bootstrap(client, args):
    """Explicit preset startup with durable identity and streamed JSON progress."""
    path = Path(args.state_file).resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix(path.suffix + '.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise OpsError('Another bootstrap owns this state file.') from None
        state = json.loads(path.read_text()) if path.exists() else {}
        identity = {'url': client.url, 'installation_id': args.expect_installation,
                    'request_id': args.request_id, 'name': args.name,
                    'source': args.source, 'runtime': args.runtime, 'agent': args.agent}
        if state and state['identity'] != identity:
            raise OpsError('Saved startup identity differs; use its original arguments or a new state file.')
        report = preflight(client, args.expect_installation, args.verify_checkout, args.images_file)
        if not report['prerequisites_ready']:
            emit({'phase': 'preflight_failed', 'report': report}, True)
            return 2
        state.update({'schema': 'smacx.operator-mission.v1', 'identity': identity,
                      'preflight': report, 'updated_unix': time.time()})
        write_state(path, state)
        if not state.get('match_id'):
            created = create_match(client, args)
            state.update({'match_id': created['match_id'], 'phase': 'created'})
            write_state(path, state)  # Before any startup mutation.
        match = quote(state['match_id'], safe='')
        lobby_path, health_path = f'api/lobbies/{match}', f'api/operator/matches/{match}/health'
        lobby = client.request(lobby_path)
        should_start = lobby['status'] == 'waiting' and not lobby.get('startupRequestedAt')
        if should_start and state.get('start_submitted'):
            raise OpsError('Previous start outcome is unresolved. Inspect status/health; bootstrap will not resubmit it.')
        if should_start:
            state['start_submitted'] = True
            write_state(path, state)
        # A cold native prepare may outlast a normal read deadline. One bounded
        # POST runs concurrently with read-only progress, without mutation retries.
        deadline = time.monotonic() + args.wait
        start_client = Client(client.url, client.path, timeout=max(client.timeout, args.wait))
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(start_client.request, lobby_path + '/start', 'POST') if should_start else None
            while True:
                if future and future.done():
                    try: future.result()
                    except OpsError as error: state['submission_notice'] = str(error)
                    future = None
                try:
                    lobby = client.request(lobby_path)
                    health = client.request(health_path) if lobby['status'] != 'waiting' else None
                    if health and health.get('installation_id') != args.expect_installation:
                        raise OpsError('Installation changed during startup.')
                    state.update({'lobby_status': lobby['status'], 'health': health, 'updated_unix': time.time()})
                    ready = bool(health and health['live_sovereign_processes'] == 1
                        and health['world_heads'] and health['workers']
                        and all(w.get('running') and not w.get('paused') and w.get('health') == 'healthy'
                            and w.get('mcp', {}).get('running') and w.get('mcp', {}).get('health') == 'healthy'
                            for w in health['workers']) and not health['incidents'])
                    failed = bool(lobby.get('needsAttention') or lobby.get('lastError') or
                                  health and (health['incidents'] or health['match_status'] == 'error'))
                    ready = ready and not failed and lobby['status'] == 'running'
                    state['phase'] = 'native_ready' if ready else 'needs_attention' if failed else 'starting'
                except OpsError as error:
                    state.update({'phase': 'observation_unavailable', 'observation_error': str(error)})
                    ready = failed = False
                timed_out = time.monotonic() >= deadline
                if timed_out and not (ready or failed): state['phase'] = 'startup_unverified'
                write_state(path, state)
                emit({'schema': state['schema'], 'phase': state['phase'], 'match_id': state['match_id'],
                      'lobby_status': state.get('lobby_status'), 'state_file': str(path),
                      'sampled_unix': state['updated_unix']}, True)
                if ready or failed or timed_out: return 0 if ready else 2
                time.sleep(min(5, max(0, deadline-time.monotonic())))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('SMACX_OPS_URL', 'http://localhost:8080'))
    parser.add_argument('--session-file', default=os.environ.get('SMACX_OPS_SESSION', str(Path.home()/'.config/smacx-ops/session.cookies')))
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--json', action='store_true', help='Compact machine-readable JSON / JSON Lines')
    subs = parser.add_subparsers(dest='command', required=True)
    login = subs.add_parser('login'); login.add_argument('--username', default='admin'); login.add_argument('--password-stdin', action='store_true')
    subs.add_parser('catalog'); subs.add_parser('lobbies')
    deploy = subs.add_parser('deployment', help='Host-only read-back of Compose images/settings; never deploys')
    deploy.add_argument('--compose', required=True); deploy.add_argument('--project', required=True)
    deploy.add_argument('--output', help='Write verified worker/mcp/harness image IDs for preflight')
    doctor = subs.add_parser('preflight', help='Read-only installation identity, image availability and catalog')
    doctor.add_argument('--expect-installation')
    doctor.add_argument('--verify-checkout', action='store_true')
    doctor.add_argument('--images-file', help='Release receipt JSON with worker/mcp/harness image IDs')
    boot = subs.add_parser('bootstrap', help='Create and start the acceptance preset; save identity and stream startup progress')
    boot.add_argument('--expect-installation', required=True)
    boot.add_argument('--state-file', required=True)
    boot.add_argument('--verify-checkout', action='store_true')
    boot.add_argument('--images-file')
    boot.add_argument('--name', required=True); boot.add_argument('--request-id', required=True)
    boot.add_argument('--wait', type=float, default=900)
    for flag in ('source', 'runtime', 'agent'): boot.add_argument('--'+flag, required=True)
    create = subs.add_parser('create', help='Create the Peacekeepers + four native bots acceptance preset; does not start it')
    create.add_argument('--name', required=True); create.add_argument('--request-id', required=True)
    for flag in ('source','runtime','agent'): create.add_argument('--'+flag)
    for command in ('status','health','inspect','start','pause','park','resume','events','watch','diagnostics','packet'):
        sub = subs.add_parser(command)
        sub.add_argument('match_id', help='Exact stable match ID (list lobbies to resolve names)')
        if command in ('events','watch'): sub.add_argument('--cursor-file')
        if command == 'inspect': sub.add_argument('--object-ref', default='', help='Inspect one known semantic object, including a map location')
        if command == 'resume': sub.add_argument('--incident-id', help='Exact operator-pause incident to recover; cannot clear unrelated incidents')
        if command == 'watch': sub.add_argument('--interval', type=float, default=10)
        if command in ('diagnostics','packet'): sub.add_argument('--output', required=True)
        if command in ('start','park'): sub.add_argument('--wait', type=float, default=0, help='Seconds to poll for completion')
    args = parser.parse_args(argv)
    if args.command == 'deployment': return deployment(args)
    client = Client(args.url, args.session_file, args.timeout)
    if args.command == 'login':
        password = sys.stdin.readline().rstrip('\r\n') if args.password_stdin else getpass.getpass('Password: ')
        result = client.login(args.username, password)
    elif args.command == 'catalog': result = client.request('api/catalog/lobby')
    elif args.command == 'lobbies': result = client.request('api/lobbies')
    elif args.command == 'create': result = create_match(client, args)
    elif args.command == 'preflight':
        result = preflight(client, args.expect_installation, args.verify_checkout, args.images_file)
        emit(result, args.json)
        return 0 if result['prerequisites_ready'] else 2
    elif args.command == 'bootstrap':
        if args.wait <= 0 or args.wait > 1800: raise OpsError('--wait must be between 0 and 1800 seconds.')
        return bootstrap(client, args)
    else:
        match = quote(args.match_id, safe='')
        lobby = f'api/lobbies/{match}'
        operator = f'api/operator/matches/{match}'
        if args.command == 'status': result = client.request(lobby)
        elif args.command in ('health','inspect'): result = client.request(operator+'/'+args.command + ('?objectRef='+quote(args.object_ref, safe='') if args.command == 'inspect' else ''))
        elif args.command == 'pause':
            result = client.request(operator+'/pause', 'POST')
            emit(result, args.json)
            return 0 if result['containment_verified'] else 2
        elif args.command == 'packet':
            directory = Path(args.output)
            directory.mkdir(parents=True, exist_ok=False, mode=0o700)
            manifest = {'match_id': args.match_id, 'capture_started_unix': time.time(), 'files': [],
                'consistency': 'Separate read watermarks; not a recovery checkpoint.'}
            try:
                for kind in ('health', 'inspect', 'events'):
                    value = client.request(operator+'/'+kind)
                    path = directory/(kind+'.json')
                    path.write_text(json.dumps(value, indent=2))
                client.request(lobby+'/diagnostics', output=directory/'diagnostics.zip')
                manifest['complete'] = True
            except Exception:
                manifest['complete'] = False
                raise
            finally:
                for path in sorted(directory.iterdir()):
                    digest = hashlib.sha256()
                    with path.open('rb') as stream:
                        while chunk := stream.read(1024*1024): digest.update(chunk)
                    manifest['files'].append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest.hexdigest()})
                manifest['capture_finished_unix'] = time.time()
                (directory/'manifest.json').write_text(json.dumps(manifest, indent=2))
            result = {'path': str(directory.resolve()), **manifest}
        elif args.command == 'diagnostics': result = client.request(lobby+'/diagnostics', output=args.output)
        elif args.command in ('start','park','resume'):
            if args.command == 'start':
                result = client.request(lobby)
                if result['status'] == 'waiting' and not result.get('startupRequestedAt'):
                    result = client.request(lobby+'/start', 'POST')
                elif result['status'] not in ('running','starting') and not result.get('startupRequestedAt'):
                    raise OpsError('Match is not waiting, starting, or running.')
            elif args.command == 'resume' and args.incident_id:
                emit(client.request(operator+'/resume', 'POST', {'action': 'recover', 'incidentId': args.incident_id}), args.json)
                return 0
            else:
                result = client.request(lobby+'/lifecycle', 'POST', {'action': 'recover' if args.command == 'resume' else 'park'})
            deadline = time.monotonic() + getattr(args, 'wait', 0)
            desired = 'running' if args.command in ('start','resume') else 'parked'
            while time.monotonic() < deadline and result['status'] != desired:
                if result.get('needsAttention') or result.get('lastError'): break
                time.sleep(min(5, max(0, deadline-time.monotonic())))
                result = client.request(lobby)
            completed = result['status'] == desired
            verification = None
            if args.command == 'park' and completed:
                verification = client.request(operator+'/health')
                completed = (result.get('hasVerifiedRecoveryCheckpoint') is True
                    and verification['active_sovereign_runs'] == 0
                    and verification['verified_live_specialist_children'] == 0
                    and bool(verification['workers'])
                    and all(not w.get('error') and w.get('running') is False for w in verification['workers']))
            result = {'operation': args.command, 'lobby': result,
                      'completed': completed, 'verification': verification}
            if getattr(args, 'wait', 0) and not completed:
                emit(result, args.json)
                return 2
        else:
            cursor_path = Path(args.cursor_file) if args.cursor_file else None
            cursor = cursor_path.read_text().strip() if cursor_path and cursor_path.exists() else ''
            while True:
                result = client.request(operator+'/events?cursor='+quote(cursor, safe=''))
                emit(result, args.json)
                cursor = result['next_cursor']
                if cursor_path:
                    cursor_path.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp = tempfile.mkstemp(dir=cursor_path.parent)
                    with os.fdopen(fd, 'w') as out: out.write(cursor)
                    os.replace(tmp, cursor_path)
                if args.command != 'watch': return 0
                emit({'health': client.request(operator+'/health')}, args.json)
                time.sleep(max(1, args.interval))
    emit(result, args.json)
    return 0


if __name__ == '__main__':
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
    except (OpsError, OSError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}), file=sys.stderr)
        sys.exit(1)
