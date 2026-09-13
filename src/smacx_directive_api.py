"""Provider-facing directive preparation and opaque authorization integration."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Mapping
import uuid

from smacx_directives import DirectiveError, DirectiveStore, LIVE, RUNNABLE, integer, summary
from smacx_directive_runtime import (DirectiveRuntime, MCPDirectivePort, identity, _phase,
                                     _native_pending)
from smacx_journal import JournalError


def services(api):
    if not api.MANAGED_ATTACHED:
        raise DirectiveError('unit_directives_require_managed_perspective')
    _, attention = api._runtime_services()
    store = DirectiveStore(attention.journal, attention.scope, attention.timeline_id)
    port = MCPDirectivePort(api, attention)
    return DirectiveRuntime(store, port, attention)


def error(exc: Exception) -> dict:
    return {'ok': False, 'error': {'code': 'directive_review_required', 'detail': str(exc)},
            'native_action_executed': False, 'execution_status': 'not_dispatched',
            'required_next': {'tool': 'smac_directives', 'action': 'status'}}


def _prepare_choice(api, prepared: dict, world, *, preview: list[dict]) -> dict:
    label = ('Advance approved directives' + (' and close this turn when resolved' if prepared.get('end_turn') else '')
             if prepared['action'] == 'advance' else prepared['action'].capitalize() + ' unit directives')
    decision_id, choices = api._cache_decision_choices(identity(world),
        [{'command': 'directive_control', 'label': label}], choice_kind='directive_control',
        choice_arguments={}, turn=world.turn, year=world.snapshot.get('year'), phase=_phase(world))
    with api.DECISION_LOCK:
        cached = api.DECISION_CACHE[decision_id]
        cached['directive_controls'] = {choices[0]['choice_id']: {
            'prepared': prepared, 'timeline_id': world.timeline, 'world_epoch': world.epoch}}
    return {'ok': True, 'kind': 'directive_authorization', 'identity': identity(world),
            'decision_id': decision_id, 'choices': choices, 'preview': preview,
            'authorization': {k: prepared[k] for k in ('action', 'turn', 'end_turn', 'max_steps') if k in prepared},
            'required_next': {'tool': 'smac_execute_choice', 'decision_id': decision_id, 'execute_at_most': 1}}


def tool(api, action: str, request_json: str, directive_id: str, offset: int, limit: int) -> dict:
    try:
        runtime = services(api)
        integer(offset, 0, 1000000, 'offset')
        integer(limit, 1, 16, 'limit')
        if action == 'status':
            if request_json:
                raise DirectiveError('status_does_not_accept_authorization_json')
            state = runtime.store.read()
            if directive_id:
                record = state['records'].get(directive_id)
                if record is None:
                    raise DirectiveError('unknown_directive_id')
                return {'ok': True, 'generation': state['generation'], 'directive': summary(record)}
            records = sorted(state['records'].values(), key=lambda r: (r['state'] not in LIVE, -r.get('generation', 0)))
            return {'ok': True, **runtime.store.dashboard(limit=limit),
                    'items': [summary(r) for r in records[offset:offset + limit]],
                    'next_offset': offset + limit if len(records) > offset + limit else None,
                    'total_records': len(records)}
        if action != 'prepare' or directive_id or offset:
            raise DirectiveError('prepare_requires_request_json_without_read_selectors')
        if not isinstance(request_json, str) or not 1 <= len(request_json) <= 32000:
            raise DirectiveError('request_json_must_be_bounded_object')
        request = json.loads(request_json)
        world = runtime.port.observe()
        gate = runtime.port.gate(world)
        if gate:
            return gate
        if _phase(world) != 'turn':
            raise DirectiveError('resolve_current_native_interaction_before_authorization')
        runtime._reconcile(world)
        runtime._notices()
        registry = None
        if isinstance(request, Mapping) and any(isinstance(s, Mapping) and s.get('scope_ref') for s in request.get('directives', [])):
            from smacx_spatial_scope import semantic_spatial_registry
            registry = semantic_spatial_registry(runtime.attention.world_store, runtime.attention.scope, world.projection)
        prepared = runtime.store.prepare(request, world, registry)
        plans = runtime.store.journal.replay(runtime.store.scope, runtime.store.timeline_id, sections=('plans',)).get('plans', {})
        active_plans = {p.get('record', {}).get('plan_id') for p in plans.values()
                        if p.get('record', {}).get('status', 'active') in {'active', 'proposed'}}
        if any(r.get('linked_plan_id') and r['linked_plan_id'] not in active_plans for r in prepared.get('updates', [])):
            raise DirectiveError('linked_plan_must_identify_active_journal_plan')
        return _prepare_choice(api, prepared, world, preview=[summary(r) for r in prepared.get('updates', [])])
    except (DirectiveError, ValueError, TypeError) as exc:
        return error(exc)


def fault(api, exc: Exception, *, native_call_attempted: bool = False) -> dict:
    """Latch without claiming an unobserved native effect succeeded or failed."""
    match_id, session_id, _, _ = api._managed_scope_identity()
    incident = {'code': 'directive_execution_state_unavailable',
                'message': 'Directive state or execution could not be reconciled. Further mutation is stopped; inspect the journal and native state before recovery.',
                'exception_type': type(exc).__name__, 'detail': str(exc)[:400]}
    with api.ACTION_PROGRESS_LOCK:
        api.RUNTIME_CIRCUITS[(match_id, session_id)] = incident
    return {'ok': False, 'error': incident, 'incident': incident,
            'native_call_attempted': native_call_attempted, 'execution_status': 'unverified',
            'required_next': {'stop_after': True, 'reason': 'Operator recovery is required.'}}


def approve(api, decision: dict, choice_id: str, decision_id: str) -> dict:
    runtime = None
    try:
        control = decision.get('directive_controls', {}).get(choice_id)
        if not isinstance(control, dict):
            raise DirectiveError('missing_directive_authorization')
        runtime = services(api)
        world = runtime.port.observe()
        if identity(world) != decision['identity'] or world.timeline != control['timeline_id'] or world.epoch != control['world_epoch']:
            raise DirectiveError('directive_authorization_state_changed_prepare_again')
        gate = runtime.port.gate(world)
        if gate:
            return gate
        prepared = control['prepared']
        if prepared['action'] == 'advance':
            result = runtime.run(prepared, world)
            api.diagnostic_record('directive_execution', result.get('directive_execution', {}),
                                  actor='managed-mcp')
            return result
        return runtime.store.apply(prepared, world, decision_id)
    except DirectiveError as exc:
        if runtime is not None and getattr(runtime.port, 'dispatch_count', 0):
            return fault(api, exc, native_call_attempted=True)
        return error(exc)
    except Exception as exc:
        return fault(api, exc, native_call_attempted=bool(runtime and getattr(runtime.port, 'dispatch_count', 0)))


def manual_override(api, decision: dict, selected_receipt: dict) -> dict | None:
    if not api.MANAGED_ATTACHED or decision.get('choice_kind') != 'unit_actions':
        return None
    actor_ref = selected_receipt.get('own_unit_ref')
    if not actor_ref:
        return None
    try:
        # Existing isolated MCP fixtures have no campaign service. Production
        # always has the canonical journal, required by _runtime_services.
        _, attention = api._runtime_services()
        if not getattr(attention, 'journal', None):
            return None
        runtime = services(api)
        records = runtime.store.read()['records']
        if not any(actor_ref in r['actors'] and r['state'] in LIVE for r in records.values()):
            return None
        world = runtime.port.observe()
        if identity(world) != decision['identity']:
            return error(DirectiveError('manual_override_world_changed_obtain_fresh_choice'))
        runtime._reconcile(world)
        runtime.manual_override(actor_ref, world)
    except DirectiveError as exc:
        return error(exc)
    except Exception as exc:
        return fault(api, exc)
    return None


def attach_frame(api, frame: dict, *, offer: bool) -> dict:
    """A read-only operational view and an explicitly selectable yield choice."""
    if not api.MANAGED_ATTACHED or not frame.get('ok') or frame.get('turn_handoff_required'):
        return frame
    try:
        _, attention = api._runtime_services()
        if not getattr(attention, 'journal', None):
            return frame
        store = DirectiveStore(attention.journal, attention.scope, attention.timeline_id)
        state = store.read()
        if not state['records']:
            return frame
        frame['unit_directives'] = store.dashboard(limit=4)
        projection = attention.world_store.load(attention.scope, attention.timeline_id)
        if not projection or str(projection.get('action_revision')) != str((frame.get('identity') or {}).get('revision')):
            return frame
        reserved = {actor for r in state['records'].values() if r['state'] in LIVE for actor in r['actors']}
        unassigned = [o['object_ref'] for o in projection.get('objects', []) if o.get('kind') == 'own_unit'
                      and o.get('status') == 'active' and o.get('fields', {}).get('ready', {}).get('value') is True
                      and o['object_ref'] not in reserved]
        frame['unit_directives']['unassigned_ready_count'] = len(unassigned)
        frame['unit_directives']['unassigned_ready_refs'] = unassigned[:16]
        if not offer or frame.get('phase') != 'turn' or frame.get('gameplay_mutations_blocked') or frame.get('sleep'):
            return frame
        if not any(r['state'] in RUNNABLE or r.get('pending') for r in state['records'].values()):
            return frame
        did = frame.get('decision_id')
        cid = 'choice-' + uuid.uuid4().hex
        # Default turn-review choice explicitly authorizes closure of THIS turn,
        # never future turns or skipping unresolved unrelated units.
        label = 'Advance approved unit directives; close this turn only when all decisions are resolved'
        with api.DECISION_LOCK:
            cached = api.DECISION_CACHE.get(did)
            if not cached or cached.get('consumed'):
                return frame
            cached['choices'][cid] = {'command': 'directive_control', 'label': label}
            cached['choice_labels'][cid] = label
            cached['receipt_subjects'][cid] = {}
            cached.setdefault('directive_controls', {})[cid] = {
                'timeline_id': attention.timeline_id,
                'world_epoch': projection['identity']['world_epoch'],
                'prepared': {'action': 'advance', 'expected_generation': state['generation'],
                             'max_steps': 64, 'end_turn': True, 'turn': frame['turn']}}
        frame['choices'].insert(0, {'choice_id': cid, 'label': label,
            'authorization': {'end_turn': 'this_turn_only', 'max_steps': 64,
                              'manual_orders_pause_affected_directives': True}})
    except Exception as exc:
        # Direct tactical control stays available if optional presentation fails.
        # Directive execution independently requires a healthy canonical store.
        frame['unit_directives'] = {'status': 'unavailable', 'error': type(exc).__name__,
                                    'detail_tool': 'smac_directives'}
    return frame
