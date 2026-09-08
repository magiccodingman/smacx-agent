"""Operator-only, bounded observations. Never a sovereign gameplay tool."""
from __future__ import annotations
import base64
import hashlib
import json
import os
import time
from smacx_diagnostics import redact
from smacx_journal import CampaignJournal
from smacx_store import InvalidRecord

ACTIVE = {'queued', 'starting', 'running', 'restarting'}


def classify_health(match, runs, workers, incidents, now):
    """Unknown/freshness is explicit; elapsed turn time alone proves no deadlock."""
    reasons = []
    if incidents:
        return 'needs_attention', ['active_operator_incident']
    if match.get('status') == 'error':
        return 'needs_attention', ['match_lifecycle_error']
    if match.get('status') == 'starting':
        return 'starting', ['startup_completion_not_verified']
    if match.get('status') in {'completed', 'closed'}:
        return 'completed', []
    if any(w.get('mcp_status') == 'error' for w in workers):
        return 'needs_attention', ['mcp_startup_failed']
    if workers and all(not w.get('running') or w.get('paused') for w in workers if not w.get('error')) \
            and not any(w.get('error') for w in workers) and not any(r.get('status') in ACTIVE for r in runs):
        return 'paused', ['native_not_advancing']
    if any(w.get('error') for w in workers): reasons.append('worker_state_unavailable')
    if any(w.get('running') and not w.get('paused') and w.get('health') != 'healthy' for w in workers):
        reasons.append('worker_health_not_ready')
    if any(w.get('running') and not w.get('paused') and w.get('mcp_status') == 'running'
           and (not w.get('mcp', {}).get('running') or w.get('mcp', {}).get('health') != 'healthy') for w in workers):
        reasons.append('mcp_health_not_ready')
    for run in runs:
        if run.get('status') not in ACTIVE: continue
        metadata = run.get('metadata', {})
        if metadata.get('semantic_baseline_pending'):
            reasons.append('supervisor_usage_baseline_pending')
        sample = metadata.get('semantic_sample_unix')
        if not sample or now - float(sample) > 120:
            reasons.append('supervisor_sample_stale')
        if metadata.get('semantic_unavailable_samples', 0) >= 3:
            reasons.append('repeated_native_observation_unavailable')
        # The authoritative supervisor owns stall thresholds and containment.
        # Do not implement a contradictory turn-duration detector here.
    if reasons: return 'unknown', sorted(set(reasons))
    if any(r.get('status') in ACTIVE for r in runs): return 'observed_active', []
    return 'idle', ['no_active_sovereign_run']


class OperatorService:
    def __init__(self, control, manager):
        self.control, self.manager, self.store = control, manager, control.store
        self.journal = CampaignJournal(self.store.path.parent / 'campaigns',
                                      timeline_resolver=self.store.active_timeline_id)

    def preflight(self, harness):
        """Read-only installation identity and prerequisites; no gameplay claim."""
        checks, images = [], {}
        def check(name, operation):
            try:
                value = operation()
                checks.append({'name': name, 'ok': bool(value)})
                return value
            except Exception as error:
                checks.append({'name': name, 'ok': False, 'error': type(error).__name__})
        check('docker', self.manager.docker.ping)
        check('network', lambda: self.manager.docker.inspect_network(self.manager.network_name))
        check('control_volume', lambda: self.manager.docker.inspect_volume(self.manager.control_data_volume))
        for kind, ref in [('worker', self.manager.worker_image), ('mcp', self.manager.mcp_image),
                          ('harness', harness.image_ref)]:
            value = check(kind + '_image', lambda ref=ref: self.manager.docker.inspect_image(ref))
            images[kind] = {'reference': ref, 'id': value.get('Id') if value else None,
                'revision': (value.get('Config', {}).get('Labels') or {}).get('org.opencontainers.image.revision') if value else None}
        import shutil
        from pathlib import Path
        usage = shutil.disk_usage(self.store.path.parent)
        code = {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                for name in ('smacx_operator.py', 'smacx_ops.py', 'smacx_worker_manager.py',
                             'smacx_control_server.py', 'smacx_mcp.py')}
        return {'schema': 'smacx.operator-preflight.v1', 'sampled_unix': time.time(),
                'installation_id': self.manager.installation_id,
                'prerequisites_ready': all(c['ok'] for c in checks), 'checks': checks,
                'images': images, 'operator_code_sha256': code, 'control_storage_free_bytes': usage.free,
                'limitations': ['Image availability does not prove a fresh native startup.',
                    'Provider availability in the lobby catalog is cached; startup remains the live check.']}

    def health(self, match_id):
        match = self.control.get_match(match_id)
        now = time.time()
        runs = [r for r in self.control.list_harness_runs() if r['match_id'] == match_id]
        workers = []
        worker_error = False
        for spec in self.control.list_worker_specs():
            if spec['match_id'] != match_id: continue
            if worker_error:
                workers.append({'instance_id': spec['instance_id'], 'error': 'not_checked_after_worker_error',
                    'startup_failure': spec.get('network', {}).get('mcp_startup_failure')})
                continue
            try:
                worker = self.manager.worker_status(spec['instance_id'])
                workers.append({k: worker.get(k) for k in
                    ('instance_id', 'container_present', 'running', 'paused', 'health', 'session_id', 'mcp', 'image_id', 'image_ref')})
                workers[-1]['startup_failure'] = spec.get('network', {}).get('mcp_startup_failure')
                workers[-1]['mcp_status'] = spec.get('network', {}).get('mcp_status')
            except Exception as error:
                worker_error = True
                workers.append({'instance_id': spec['instance_id'], 'error': type(error).__name__,
                    'startup_failure': spec.get('network', {}).get('mcp_startup_failure')})
        incidents = self.control.list_supervision_incidents(match_id=match_id, active_only=True)
        with self.store._connect() as connection:
            missions = [dict(r) for r in connection.execute(
                'SELECT mission_id,status,updated_unix FROM specialist_missions WHERE match_id=? '
                'ORDER BY updated_unix DESC LIMIT 32', (match_id,))]
            active_children = connection.execute("SELECT COUNT(*) FROM specialist_missions WHERE match_id=? "
                "AND status IN ('queued','active','retry_wait')", (match_id,)).fetchone()[0]
            supervisor_row = connection.execute("SELECT value_json FROM control_settings WHERE "
                "setting_key='specialist.supervisor_health'").fetchone()
            supervisor = json.loads(supervisor_row[0]) if supervisor_row else {}
            child_ids = supervisor.get('active_child_attempt_ids')
            fresh_children = isinstance(child_ids, list) and now - float(supervisor.get('heartbeat_unix', 0)) < 15
            attempts_exist = connection.execute("SELECT 1 FROM specialist_attempts a JOIN specialist_missions m "
                "ON m.mission_id=a.mission_id WHERE m.match_id=? LIMIT 1", (match_id,)).fetchone()
            children = None
            if fresh_children:
                children = sum(bool(connection.execute("SELECT 1 FROM specialist_attempts a JOIN specialist_missions m "
                    "ON m.mission_id=a.mission_id WHERE m.match_id=? AND a.attempt_id=?", (match_id, ident)).fetchone())
                    for ident in child_ids)
            elif not attempts_exist and not active_children:
                children = 0
            heads = []
            for scope in self.store.scopes_for_match(match_id):
                row = connection.execute('SELECT agent_id,perspective_id,timeline_id,world_epoch,world_revision,'
                    'observation_cursor,updated_unix FROM world_heads WHERE match_id=? AND agent_id=? '
                    'AND perspective_id=? AND timeline_id=?', (match_id, scope.agent_id, scope.perspective_id,
                    self.store.active_timeline_id(scope))).fetchone()
                if row: heads.append(dict(row))
        state, reasons = classify_health(match, runs, workers, incidents, now)
        live_runs = 0
        run_verification_errors = []
        names = {r['container_name'] for r in runs if r.get('container_name')}
        try:
            inventory = self.manager.docker.list_owned_containers(self.manager.installation_id, 'harness-run')
            live_runs = sum(item.get('State') in {'running', 'paused', 'restarting'} and
                (item.get('Labels', {}).get('io.smacx.match') == match_id or
                 any(name.lstrip('/') in names for name in item.get('Names', []))) for item in inventory)
        except Exception as error:
            run_verification_errors.append({'error': type(error).__name__})
        if run_verification_errors and state not in {'needs_attention','completed'}:
            state = 'unknown'
            reasons.append('sovereign_process_verification_unavailable')
        if live_runs and state in {'paused', 'idle'}:
            state = 'needs_attention'
            reasons.append('live_sovereign_process_without_active_run')
        safe_runs = []
        for run in runs[:32]:
            metadata = run.get('metadata', {})
            safe_runs.append({**{k: run.get(k) for k in ('run_id','status','desired_status','instance_id')},
                'observation': {k: metadata.get(k) for k in ('semantic_sample_unix','semantic_progress_unix',
                    'semantic_progress','semantic_telemetry_unix','semantic_baseline_pending','semantic_unavailable_reason',
                    'semantic_unavailable_samples','consecutive_clean_yields_without_progress')}})
        return redact({'schema': 'smacx.operator-health.v1', 'match_id': match_id,
            'installation_id': self.manager.installation_id,
            'sampled_unix': now, 'state': state, 'reasons': reasons,
            'match_status': match.get('status'), 'turn': match.get('last_turn'), 'year': match.get('last_year'),
            'workers': workers, 'runs': safe_runs, 'world_heads': heads,
            'incidents': incidents[:16], 'specialists': missions, 'active_specialist_missions': active_children,
            'verified_live_specialist_children': children,
            'live_sovereign_processes': live_runs if not run_verification_errors else None,
            'run_verification_errors': run_verification_errors,
            'active_sovereign_runs': sum(r.get('status') in ACTIVE for r in runs),
            'limitations': ['Cached supervisor observations; this read does not probe or advance native UI.',
                           'No strategic competence or game victory inferred from process activity.']})

    def pause(self, match_id):
        with self.manager._lifecycle_lock:
            return self._pause_locked(match_id)

    def _pause_locked(self, match_id):
        """Persist a restart fence before freezing; no checkpoint claim is made."""
        self.control.get_match(match_id)
        specs = [s for s in self.control.list_worker_specs() if s['match_id'] == match_id]
        if not specs: raise InvalidRecord('operator_pause_requires_managed_worker')
        incidents = [self.control.record_supervision_incident(s['instance_id'], 'operator_pause',
            'operator_required', {'summary': 'Operator requested investigation pause.',
            'recovery_policy': 'Explicit verified recovery or end required; frozen RAM is not a checkpoint.'})
            for s in specs]
        self.control.update_match_lifecycle(match_id, 'error', metadata={
            'recovery_required': True, 'recovery_reason': 'operator_pause'})
        errors = []
        try:
            self.manager.quarantine_match(match_id, stop_collectors=True)
            from smacx_specialists import SpecialistService
            from smacx_world_store import WorldStore
            for scope in self.store.scopes_for_match(match_id):
                service = SpecialistService(self.store, WorldStore(self.store), scope)
                with self.store._connect() as connection:
                    missions = connection.execute("SELECT mission_id FROM specialist_missions WHERE match_id=? "
                        "AND agent_id=? AND perspective_id=? AND status IN ('queued','active','retry_wait')",
                        (match_id, scope.agent_id, scope.perspective_id)).fetchall()
                for mission in missions:
                    service.cancel(mission[0], 'cancelled_by_parent', authoritative=True)
        except Exception as error: errors.append(type(error).__name__)
        report = self.health(match_id)
        workers = report['workers']
        for worker in workers:
            if worker.get('mcp') is None: worker['mcp'] = {}
        native_frozen = bool(workers) and all(not w.get('error') and
            (w.get('paused') or not w.get('running')) for w in workers)
        collectors_frozen = all(not w.get('error') and
            (not w.get('mcp', {}).get('running') or w.get('mcp', {}).get('paused')) for w in workers)
        complete = native_frozen and collectors_frozen and report['active_sovereign_runs'] == 0 \
            and report['live_sovereign_processes'] == 0 and report['active_specialist_missions'] == 0 and report['verified_live_specialist_children'] == 0 and not errors
        return {'schema': 'smacx.operator-pause.v1', 'match_id': match_id,
            'containment_verified': complete, 'native_frozen': native_frozen,
            'collectors_frozen': collectors_frozen, 'checkpoint_created': False,
            'state_preservation': 'process_memory_only' if any(w.get('paused') for w in workers) else 'no_frozen_process_verified',
            'incidents': incidents, 'errors': errors, 'health': report,
            'required_next': 'Investigate; explicit verified recovery required before resuming.'}

    def resume(self, match_id, incident_id):
        # Retry is tied to one incident: it cannot restore an already resumed
        # campaign again or clear an unrelated bug incident.
        with self.manager._lifecycle_lock:
            incident = self.control.get_supervision_incident(incident_id)
            if incident['match_id'] != match_id or incident['incident_kind'] != 'operator_pause':
                raise InvalidRecord('operator_pause_incident_required')
            if incident['status'] == 'recovered':
                return {'already_recovered': True, 'health': self.health(match_id)}
            active = self.control.list_supervision_incidents(match_id=match_id, active_only=True)
            if any(row['incident_kind'] != 'operator_pause' for row in active):
                raise InvalidRecord('unresolved_incident_blocks_operator_resume')
            recovered = self.manager.recover_match(match_id, refresh_runtime=True)
            if not recovered.get('ok'):
                raise InvalidRecord('operator_recovery_not_verified')
            self.control.recover_supervision_incidents(match_id, kinds=('operator_pause',))
            return {'already_recovered': False, 'health': self.health(match_id)}

    def events(self, match_id, cursor=''):
        self.control.get_match(match_id)
        try:
            prior = json.loads(base64.urlsafe_b64decode(cursor).decode()) if cursor else {}
            if not isinstance(prior, dict) or len(prior) > 7: raise ValueError()
        except Exception as error: raise InvalidRecord('invalid_operator_cursor') from error
        result, next_cursor, gaps = [], {}, []
        for scope in self.store.scopes_for_match(match_id):
            timeline = self.store.active_timeline_id(scope)
            key = f'{scope.agent_id}/{scope.perspective_id}/{timeline}'
            manifest = self.journal._manifest(scope, timeline)
            head = int(manifest.get('sequence', 0))
            after = prior.get(key, max(0, head - 50))
            if type(after) is not int or after < 0 or after > head: raise InvalidRecord('invalid_operator_cursor')
            if prior and key not in prior: gaps.append({'scope': key, 'reason': 'timeline_or_scope_changed'})
            start = max(after, head - 500)
            if start > after: gaps.append({'scope': key, 'reason': 'retention_window', 'skipped_sequences': start-after})
            root = self.journal.perspective_root(scope, timeline) / 'events'
            processed = start
            stop = min(head, start + 50)
            files = {}
            if stop > start and root.is_dir():
                # Scan filenames once, not once per returned event.
                with os.scandir(root) as entries:
                    for entry in entries:
                        prefix = entry.name[:12]
                        if prefix.isdigit() and entry.name.endswith('.json') and start < int(prefix) <= stop:
                            files.setdefault(int(prefix), []).append(root / entry.name)
            for sequence in range(start + 1, stop + 1):
                paths = files.get(sequence, [])
                if len(paths) != 1:
                    gaps.append({'scope': key, 'sequence': sequence, 'reason': 'event_unavailable'})
                    break
                raw = paths[0].read_bytes()
                event = json.loads(raw)
                # Large world publication batches remain in the full export.
                payload = event.get('payload', {})
                if len(raw) > 8192:
                    payload = {'omitted': 'large_event', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                result.append(redact({'event_id': event['event_id'], 'scope': key,
                    'sequence': sequence, 'event_type': event['event_type'], 'turn': event.get('turn'),
                    'recorded_unix': event.get('recorded_unix'), 'payload': payload}))
                processed = sequence
            next_cursor[key] = processed
        token = base64.urlsafe_b64encode(json.dumps(next_cursor, sort_keys=True).encode()).decode()
        return {'schema': 'smacx.operator-events.v1', 'match_id': match_id, 'events': result,
                'next_cursor': token, 'gaps': gaps, 'sampled_unix': time.time()}

    def inspect(self, match_id, object_ref=''):
        """Read committed faction projections, retaining epistemic wrappers."""
        self.control.get_match(match_id)
        perspectives = []
        with self.store._connect() as connection:
            connection.execute('BEGIN')
            for scope in self.store.scopes_for_match(match_id):
                timeline = self.store.active_timeline_id(scope)
                key = (match_id, scope.agent_id, scope.perspective_id, timeline)
                counts = dict(connection.execute('SELECT object_kind,COUNT(*) FROM world_objects '
                    'WHERE match_id=? AND agent_id=? AND perspective_id=? AND timeline_id=? '
                    'GROUP BY object_kind', key).fetchall())
                rows = connection.execute("SELECT payload_json FROM world_objects WHERE match_id=? "
                    "AND agent_id=? AND perspective_id=? AND timeline_id=? AND object_kind IN "
                    "('own_unit','base','faction') AND status='active' ORDER BY object_ref LIMIT 64", key).fetchall()
                if object_ref:
                    rows = connection.execute('SELECT payload_json FROM world_objects WHERE match_id=? AND agent_id=? '
                        'AND perspective_id=? AND timeline_id=? AND object_ref=? LIMIT 1', (*key, object_ref)).fetchall()
                objects = []
                for row in rows:
                    obj = json.loads(row[0]); obj.pop('metadata', None)
                    objects.append(obj)
                perspectives.append({'agent_id': scope.agent_id, 'perspective_id': scope.perspective_id,
                    'timeline_id': timeline, 'counts': counts, 'objects': objects, 'object_limit': 64})
        return redact({'schema': 'smacx.operator-inspection.v1', 'match_id': match_id,
            'perspectives': perspectives, 'authority': 'committed faction observations; preserve epistemic status'})
