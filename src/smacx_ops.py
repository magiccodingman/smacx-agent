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
            or (i == 0 and seat['requestedPersonalityId'] != 'none') for i, seat in enumerate(seats)):
        raise OpsError(f'Roster verification failed for {match_id}; lobby was not started.')
    return {'match_id': match_id, 'roster_verified': True, 'lobby': lobby}


def emit(value, compact=False):
    print(json.dumps(value, ensure_ascii=False, indent=None if compact else 2), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('SMACX_OPS_URL', 'http://localhost:8080'))
    parser.add_argument('--session-file', default=os.environ.get('SMACX_OPS_SESSION', str(Path.home()/'.config/smacx-ops/session.cookies')))
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--json', action='store_true', help='Compact machine-readable JSON / JSON Lines')
    subs = parser.add_subparsers(dest='command', required=True)
    login = subs.add_parser('login'); login.add_argument('--username', default='admin'); login.add_argument('--password-stdin', action='store_true')
    subs.add_parser('catalog'); subs.add_parser('lobbies')
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
    client = Client(args.url, args.session_file, args.timeout)
    if args.command == 'login':
        password = sys.stdin.readline().rstrip('\r\n') if args.password_stdin else getpass.getpass('Password: ')
        result = client.login(args.username, password)
    elif args.command == 'catalog': result = client.request('api/catalog/lobby')
    elif args.command == 'lobbies': result = client.request('api/lobbies')
    elif args.command == 'create': result = create_match(client, args)
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
