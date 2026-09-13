"""Durable, typed unit authorization; deterministic policy, never strategic prose.

The campaign journal is the authority. Routes, native rows and opaque choices are
not durable instructions. This module has no bridge, provider or background thread.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import json
from pathlib import Path
import threading
from typing import Any, Iterator, Mapping
import uuid

from smacx_mechanics import field_value, field_is_current, mobility_profile, relationship_class
from smacx_topology import DIRECTION_OFFSETS, PerspectiveTopology
from smacx_world import WorldService
from smacx_world_types import content_hash

MAX_BATCH = 16
MAX_ACTIVE = 64
MAX_SCOPE_SQUARES = 1024
MAX_CANDIDATES = 128
LIVE = frozenset({'active', 'waiting', 'working', 'paused'})
RUNNABLE = frozenset({'active', 'waiting', 'working'})
KINDS = frozenset({'travel', 'explore', 'work', 'follow', 'escort'})
# Native FormerItem enum (bridge/src/engine_veh.h); only observable, local jobs.
WORK = {'farm': (0, 'farm'), 'mine': (2, 'mine'),
        'solar_collector': (3, 'solar_collector'), 'forest': (4, 'forest'),
        'road': (5, 'road'), 'sensor': (9, 'sensor'), 'remove_fungus': (10, 'fungus')}
DEFAULT_POLICY = {'threat_radius': 2, 'foreign_territory': 'pact',
                  'detour_budget_turns': 3, 'max_idle_turns': 3,
                  'interrupt_on_new_contact': True}
_LOCK = threading.RLock()


class DirectiveError(ValueError):
    """A structured validation/conflict result, not an engine capability gap."""


def integer(value: Any, low: int, high: int, name: str) -> int:
    if type(value) is not int or not low <= value <= high:
        raise DirectiveError(f'{name}_must_be_integer_{low}_to_{high}')
    return value


def ref(value: Any, name: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 180 or any(ord(c) < 32 for c in value):
        raise DirectiveError(f'invalid_{name}')
    return value


def live(item: Mapping[str, Any]) -> bool:
    return item.get('status', 'active') == 'active'


@dataclass
class DirectiveWorld:
    """One revision-coherent, perspective-filtered projection plus native snapshot."""
    projection: dict
    snapshot: dict

    def __post_init__(self) -> None:
        self.objects = WorldService._objects(self.projection)
        self.topology = WorldService._topology(self.projection)
        identity = self.projection.get('identity', {})
        self.timeline = str(identity.get('timeline_id') or '')
        self.epoch = str(identity.get('world_epoch') or '')
        self.session = str(self.snapshot.get('session_id') or '')
        self.revision = str(self.snapshot.get('revision') or '')
        self.turn = integer(self.snapshot.get('turn'), 0, 1000000, 'turn')
        self.cursor = int(self.projection.get('observation_cursor') or 0)
        if identity.get('match_id') != self.snapshot.get('match_id'):
            raise DirectiveError('directive_world_match_mismatch')
        if not self.timeline or not self.epoch or not self.session or not self.revision:
            raise DirectiveError('directive_world_identity_unavailable')
        if str(self.projection.get('action_revision') or '') != self.revision:
            raise DirectiveError('directive_world_revision_changed')
        if self.projection.get('continuity', 'complete') != 'complete':
            raise DirectiveError('directive_observation_continuity_incomplete')
        map_state = next((o for o in self.objects.values() if o.get('kind') == 'map_state'), {})
        if not isinstance(self.projection.get('map_shape'), Mapping) and not all(
                field_is_current(map_state, k) for k in ('width', 'height', 'horizontal_wrap')):
            raise DirectiveError('directive_map_shape_unavailable')

    def actor(self, actor_ref: str) -> dict:
        actor = self.objects.get(actor_ref, {})
        if actor.get('kind') != 'own_unit' or not live(actor):
            raise DirectiveError('actor_not_current_owned_unit')
        if not all(field_is_current(actor, key) for key in
                   ('triad', 'roles', 'ready', 'order_name', 'hp', 'owner_ref')):
            raise DirectiveError('actor_mechanics_not_current')
        roles = field_value(actor, 'roles', {})
        if not isinstance(roles, dict) or roles.get('boarded') or field_value(actor, 'triad') not in {'land', 'sea'}:
            raise DirectiveError('directive_requires_unboarded_land_or_sea_unit')
        if actor.get('location_ref') not in self.topology.by_ref:
            raise DirectiveError('actor_location_unavailable')
        return actor

    def location(self, target_ref: str, *, moving: bool = False) -> str:
        target = self.objects.get(target_ref, {})
        if target.get('kind') == 'location' and target.get('status', 'active') in {'active', 'stale'}:
            location = target_ref
        elif target.get('kind') in ({'base', 'own_unit', 'foreign_contact'} if moving else {'base'}) and live(target):
            if target.get('kind') == 'foreign_contact' and not field_is_current(target, 'last_seen_turn'):
                raise DirectiveError('follow_contact_not_current')
            if target.get('kind') == 'base' and not field_is_current(target, 'owner_ref'):
                raise DirectiveError('target_base_access_not_current')
            location = str(target.get('location_ref') or '')
        else:
            raise DirectiveError('target_lost_or_not_current')
        if location not in self.topology.by_ref:
            raise DirectiveError('target_location_unknown')
        return location

    def position(self, location_ref: str) -> tuple[int, int]:
        square = self.topology.by_ref[location_ref]
        return square.x, square.y

    def contacts(self, location_ref: str, radius: int = 6) -> list[str]:
        origin = self.position(location_ref)
        return sorted(r for r, o in self.objects.items()
                      if o.get('kind') == 'foreign_contact' and live(o)
                      and field_is_current(o, 'last_seen_turn')
                      and o.get('location_ref') in self.topology.by_ref
                      and self.topology.shape.distance(origin, self.position(o['location_ref'])) <= radius)

    def bases(self, location_ref: str, radius: int = 6) -> list[str]:
        origin = self.position(location_ref)
        return sorted(r for r, o in self.objects.items()
                      if o.get('kind') == 'base' and live(o)
                      and field_is_current(o, 'owner_ref')
                      and o.get('location_ref') in self.topology.by_ref
                      and self.topology.shape.distance(origin, self.position(o['location_ref'])) <= radius)

    def owner_allowed(self, owner: Any, own: Any, policy: Mapping) -> bool:
        if owner in {None, '', 'faction-0', own}:
            return True
        return policy['foreign_territory'] == 'pact' and relationship_class(self.objects.get(str(owner), {})) == 'allied'

    def threats(self, location_ref: str, radius: int) -> list[str]:
        result = []
        for r in self.contacts(location_ref, radius):
            unit = self.objects[r]
            owner = field_value(unit, 'owner_ref')
            if relationship_class(self.objects.get(str(owner), {})) == 'hostile' or owner == 'faction-0':
                result.append(r)
        return result

    def safe_topology(self, actor_ref: str, policy: Mapping) -> PerspectiveTopology:
        actor = self.actor(actor_ref)
        owner = field_value(actor, 'owner_ref')
        origin = actor['location_ref']
        squares = [s for s in self.topology.by_ref.values()
                   if s.location_ref == origin or (
                       s.terrain in {'land', 'ocean'} and not s.blocking_contact_occupied
                       and not s.foreign_movement_zoc
                       and not (s.features & {'supply_pod', 'monolith'})
                       and self.owner_allowed(s.owner_ref, owner, policy))]
        return PerspectiveTopology(self.topology.shape, squares)

    def route(self, actor_ref: str, destination: str, policy: Mapping):
        topology = self.safe_topology(actor_ref, policy)
        actor = self.actor(actor_ref)
        profile = mobility_profile(self.objects, 'directive', subject_ref=actor_ref, topology=topology)
        return topology.route(actor['location_ref'], destination, profile)


def validate_spec(spec: Mapping, world: DirectiveWorld, registry: Mapping | None = None) -> dict:
    allowed = {'kind', 'actor_ref', 'target_ref', 'scope_ref', 'origin_ref', 'radius', 'direction',
               'work', 'escort_ref', 'purpose', 'policy', 'review_after_turns', 'linked_plan_id', 'follow_distance'}
    if not isinstance(spec, Mapping) or set(spec) - allowed:
        raise DirectiveError('unknown_directive_fields')
    kind = spec.get('kind')
    if kind not in KINDS:
        raise DirectiveError('invalid_directive_kind')
    actor_ref = ref(spec.get('actor_ref'), 'actor_ref')
    actor = world.actor(actor_ref)
    actors = [actor_ref]
    purpose = spec.get('purpose', '')
    if not isinstance(purpose, str) or len(purpose) > 400:
        raise DirectiveError('purpose_must_be_text_up_to_400_characters')
    policy = dict(DEFAULT_POLICY)
    supplied = spec.get('policy', {})
    if not isinstance(supplied, Mapping) or set(supplied) - set(policy):
        raise DirectiveError('unknown_directive_policy_fields')
    policy.update(supplied)
    integer(policy['threat_radius'], 0, 8, 'threat_radius')
    integer(policy['detour_budget_turns'], 0, 20, 'detour_budget_turns')
    integer(policy['max_idle_turns'], 1, 20, 'max_idle_turns')
    if policy['foreign_territory'] not in {'avoid', 'pact'} or type(policy['interrupt_on_new_contact']) is not bool:
        raise DirectiveError('invalid_directive_policy')
    # reject irrelevant fields rather than accepting a promise we do not implement
    specific = {'explore': {'scope_ref', 'origin_ref', 'radius', 'direction'},
                'work': {'target_ref', 'work'}, 'escort': {'target_ref', 'escort_ref'},
                'follow': {'target_ref', 'follow_distance'}, 'travel': {'target_ref'}}[kind]
    if (set(spec) & {'target_ref', 'scope_ref', 'origin_ref', 'radius', 'direction', 'work', 'escort_ref', 'follow_distance'}) - specific:
        raise DirectiveError('field_not_applicable_to_directive_kind')
    record = {'directive_id': 'directive-' + uuid.uuid4().hex, 'kind': kind,
              'actor_ref': actor_ref, 'actors': actors, 'purpose': purpose, 'policy': policy,
              'state': 'active', 'reason': 'assigned', 'created_turn': world.turn,
              'review_turn': world.turn + integer(spec.get('review_after_turns', 10), 1, 100, 'review_after_turns'),
              'epoch': world.epoch, 'shape': vars(world.topology.shape), 'pending': None,
              'last_progress_turn': world.turn, 'steps': 0, 'rejections': 0,
              'last_positions': {actor_ref: actor['location_ref']},
              'hp': {actor_ref: field_value(actor, 'hp')},
              'known_contacts': world.contacts(actor['location_ref']),
              'known_bases': world.bases(actor['location_ref']),
              'linked_plan_id': ref(spec['linked_plan_id'], 'linked_plan_id') if spec.get('linked_plan_id') else ''}
    if kind == 'explore':
        radius = integer(spec.get('radius', 4), 0, 16, 'radius')
        direction = spec.get('direction', '')
        if direction not in {'', *DIRECTION_OFFSETS}:
            raise DirectiveError('invalid_direction')
        if bool(spec.get('scope_ref')) == bool(spec.get('origin_ref')):
            raise DirectiveError('explore_requires_exactly_one_scope_or_origin')
        if spec.get('origin_ref'):
            source_ref = ref(spec['origin_ref'], 'origin_ref')
            anchors = [world.position(world.location(source_ref))]
        else:
            source_ref = ref(spec['scope_ref'], 'scope_ref')
            source = (registry or {}).get(source_ref, {})
            if source.get('kind') not in {'frontier', 'region', 'theater', 'scope'}:
                raise DirectiveError('explore_scope_not_current')
            anchors = [world.position(r) for r in source.get('location_refs', ()) if r in world.topology.by_ref]
            if not anchors:
                raise DirectiveError('explore_scope_has_no_known_anchors')
        positions = set()
        for x, y in anchors:
            for a in range(-radius, radius + 1):
                for b in range(-radius, radius + 1):
                    p = world.topology.shape.normalize((x + a + b, y + a - b))
                    if p is not None:
                        positions.add(p)
            if len(positions) > MAX_SCOPE_SQUARES:
                raise DirectiveError('exploration_scope_too_large_narrow_it')
        record.update({'scope_ref': source_ref, 'scope_positions': [list(p) for p in sorted(positions)],
                       'direction': direction, 'radius': radius, 'visited': [], 'frontier_target': ''})
    else:
        target_ref = ref(spec.get('target_ref'), 'target_ref')
        location = world.location(target_ref, moving=kind == 'follow')
        if kind == 'follow' and world.objects[target_ref].get('kind') not in {'own_unit', 'foreign_contact'}:
            raise DirectiveError('follow_requires_current_unit_target')
        if target_ref == actor_ref:
            raise DirectiveError('cannot_follow_self')
        record.update({'target_ref': target_ref, 'assigned_destination': location})
        target = world.objects.get(target_ref, {})
        record['target_owner'] = field_value(target, 'owner_ref') if target.get('kind') == 'base' else None
        if kind == 'follow':
            record['follow_distance'] = integer(spec.get('follow_distance', 1), 0, 4, 'follow_distance')
        if kind == 'escort':
            escort_ref = ref(spec.get('escort_ref'), 'escort_ref')
            escort = world.actor(escort_ref)
            if escort_ref == actor_ref or field_value(escort, 'roles', {}).get('combat') is not True:
                raise DirectiveError('escort_requires_distinct_owned_combat_unit')
            if field_value(actor, 'triad') != field_value(escort, 'triad'):
                raise DirectiveError('escort_participants_require_same_triad')
            record['escort_ref'] = escort_ref
            actors.append(escort_ref)
            record['hp'][escort_ref] = field_value(escort, 'hp')
            record['last_positions'][escort_ref] = escort['location_ref']
            record['known_contacts'] = sorted(set(record['known_contacts']) | set(world.contacts(escort['location_ref'])))
        if kind == 'work':
            job = spec.get('work')
            if job not in WORK or field_value(actor, 'roles', {}).get('former') is not True:
                raise DirectiveError('work_requires_former_and_supported_exact_job')
            record['work'] = job
        route = world.route(actor_ref, location, policy)
        record['initial_route_estimate'] = {'reachable': route.reachable, 'turns': route.turns,
                                            'uncertainty': list(route.uncertainty)}
        if route.reachable and route.turns is not None and kind in {'travel', 'work'}:
            record['arrival_review_turn'] = world.turn + route.turns + policy['detour_budget_turns'] + 1
    return record


def summary(record: Mapping) -> dict:
    keys = ('directive_id', 'kind', 'actor_ref', 'escort_ref', 'target_ref', 'scope_ref', 'work',
            'purpose', 'policy', 'state', 'reason', 'review_turn', 'steps', 'linked_plan_id', 'detail')
    result = {k: deepcopy(record[k]) for k in keys if k in record}
    result['pending_action'] = bool(record.get('pending'))
    if record.get('scope_positions') is not None:
        result['authorized_scope_squares'] = len(record['scope_positions'])
        result['scope_semantics'] = 'Frozen geometry; radius expansion is explicitly authorized, not hidden terrain.'
    return result


class DirectiveStore:
    """Journal-backed compare-and-swap transactions, scoped to one exact timeline."""
    def __init__(self, journal, scope, timeline_id: str):
        self.journal, self.scope, self.timeline_id = journal, scope, timeline_id

    @contextmanager
    def locked(self) -> Iterator[None]:
        # Separate from the journal lock: journal.append/replay acquire their own.
        with _LOCK:
            path = self.journal.perspective_root(self.scope, self.timeline_id)
            path.mkdir(parents=True, exist_ok=True)
            with (path / '.directives.lock').open('a+b') as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def read(self) -> dict:
        state = self.journal.replay(self.scope, self.timeline_id, sections=('unit_directives', 'directive_generation'))
        return {'generation': int(state.get('directive_generation') or 0),
                'records': state.get('unit_directives', {})}

    def commit(self, records: list[dict], *, expected_generation: int, world: DirectiveWorld,
               reason: str, transaction_id: str = '') -> int:
        if world.timeline != self.timeline_id:
            raise DirectiveError('directive_timeline_changed')
        with self.locked():
            state = self.read()
            if state['generation'] != expected_generation:
                raise DirectiveError('directive_generation_changed_prepare_again')
            generation = expected_generation + 1
            rows = deepcopy(records)
            for row in rows:
                row['updated_turn'] = world.turn
                row['generation'] = generation
            payload = {'generation': generation, 'updates': rows, 'reason': reason}
            if len(json.dumps(payload, ensure_ascii=False).encode()) > 190000:
                raise DirectiveError('directive_transaction_too_large_narrow_scope_or_batch')
            self.journal.append(self.scope, 'directive.transaction', payload,
                                timeline_id=self.timeline_id, session_id=world.session, turn=world.turn,
                                idempotency_key=('directive-' + transaction_id) if transaction_id else '')
            return generation

    def dashboard(self, *, limit: int = 8) -> dict:
        state = self.read()
        records = list(state['records'].values())
        active = [r for r in records if r['state'] in LIVE or r.get('pending')]
        ordered = sorted(active, key=lambda r: (r['state'] != 'paused', -r.get('updated_turn', 0), r['directive_id']))
        terminal = sorted((r for r in records if r['state'] not in LIVE),
                          key=lambda r: -r.get('generation', 0))[:3]
        return {'generation': state['generation'], 'active_count': len(active),
                'runnable_count': sum(r['state'] in RUNNABLE for r in active),
                'paused_count': sum(r['state'] == 'paused' for r in active),
                'items': [summary(r) for r in ordered[:limit]],
                'omitted_count': max(0, len(ordered) - limit),
                'recent_outcomes': [summary(r) for r in terminal],
                'detail_tool': 'smac_directives'}

    def prepare(self, request: Mapping, world: DirectiveWorld, registry: Mapping | None = None) -> dict:
        if not isinstance(request, Mapping):
            raise DirectiveError('directive_request_must_be_object')
        action = request.get('action', 'assign')
        state = self.read()
        if action == 'assign':
            if set(request) - {'action', 'directives', 'replace_existing'}:
                raise DirectiveError('unknown_assignment_fields')
            specs = request.get('directives')
            if not isinstance(specs, list) or not 1 <= len(specs) <= MAX_BATCH:
                raise DirectiveError('directive_batch_requires_1_to_16_specs')
            replace = request.get('replace_existing', False)
            if type(replace) is not bool:
                raise DirectiveError('replace_existing_must_be_boolean')
            records = [validate_spec(s, world, registry) for s in specs]
            actors = [a for r in records for a in r['actors']]
            if len(set(actors)) != len(actors):
                raise DirectiveError('actor_reserved_twice_in_batch')
            conflicts = [r for r in state['records'].values() if (r['state'] in LIVE or r.get('pending'))
                         and set(r['actors']).intersection(actors)]
            if any(r.get('pending') for r in conflicts):
                raise DirectiveError('cannot_replace_unresolved_native_action')
            if conflicts and not replace:
                raise DirectiveError('actor_already_directed_use_explicit_replace_existing')
            for r in conflicts:
                replacement = deepcopy(r)
                replacement.update(state='cancelled', reason='explicitly_superseded')
                records.insert(0, replacement)
            live_count = sum(r['state'] in LIVE for r in state['records'].values()) - len(conflicts) + len(specs)
            if live_count > MAX_ACTIVE:
                raise DirectiveError('active_directive_limit_reached')
        elif action in {'pause', 'cancel', 'resume'}:
            if set(request) - {'action', 'directive_ids', 'review_after_turns'}:
                raise DirectiveError('unknown_control_fields')
            ids = request.get('directive_ids')
            if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_BATCH or len(set(ids)) != len(ids):
                raise DirectiveError('control_requires_1_to_16_unique_directive_ids')
            records = []
            for did in ids:
                original = state['records'].get(ref(did, 'directive_id'))
                if not original or original['state'] not in LIVE:
                    raise DirectiveError('directive_not_active')
                r = deepcopy(original)
                if action == 'resume':
                    if r.get('pending'):
                        raise DirectiveError('resolve_pending_action_before_resume')
                    for actor_ref in r['actors']:
                        actor = world.actor(actor_ref)
                        r['hp'][actor_ref] = field_value(actor, 'hp')
                        r['last_positions'][actor_ref] = actor['location_ref']
                    if r['shape'] != vars(world.topology.shape):
                        raise DirectiveError('map_shape_changed_reassign_directive')
                    if r['kind'] != 'explore':
                        destination = world.location(r['target_ref'], moving=r['kind'] == 'follow')
                        r['target_owner'] = field_value(world.objects[r['target_ref']], 'owner_ref')
                        route = world.route(r['actor_ref'], destination, r['policy'])
                        r.pop('arrival_review_turn', None)
                        if route.reachable and route.turns is not None and r['kind'] in {'travel', 'work'}:
                            r['arrival_review_turn'] = world.turn + route.turns + r['policy']['detour_budget_turns'] + 1
                    r.update(state='active', reason='explicitly_resumed', epoch=world.epoch,
                             last_progress_turn=world.turn, rejections=0,
                             review_turn=world.turn + integer(request.get('review_after_turns', 10), 1, 100, 'review_after_turns'),
                             known_contacts=sorted({c for a in r['actors'] for c in world.contacts(world.actor(a)['location_ref'])}),
                             known_bases=sorted({b for a in r['actors'] for b in world.bases(world.actor(a)['location_ref'])}))
                else:
                    r.update(state='paused' if action == 'pause' else 'cancelled', reason='sovereign_' + action)
                    if action == 'cancel' and r.get('pending'):
                        phase = (world.snapshot.get('protocol') or {}).get('phase')
                        native_pending = (world.snapshot.get('last_deferred_action') or {}).get('status') == 'pending'
                        if phase == 'turn' and not native_pending:
                            # Cancelling from a settled, freshly observed world
                            # abandons uncertainty; it does not certify failure
                            # or authorize replay of the original action.
                            r['cancelled_outcome'] = 'unverified_no_replay_authorized'
                            r['pending'] = None
                r.pop('notice', None)
                records.append(r)
        elif action == 'advance':
            if set(request) - {'action', 'max_steps', 'end_turn'}:
                raise DirectiveError('unknown_advance_fields')
            if type(request.get('end_turn', False)) is not bool:
                raise DirectiveError('end_turn_must_be_boolean')
            return {'action': action, 'expected_generation': state['generation'],
                    'max_steps': integer(request.get('max_steps', 64), 1, 128, 'max_steps'),
                    'end_turn': request.get('end_turn', False), 'turn': world.turn}
        else:
            raise DirectiveError('unknown_directive_control')
        if len(json.dumps(records).encode()) > 180000:
            raise DirectiveError('directive_batch_too_large_narrow_it')
        return {'action': action, 'expected_generation': state['generation'], 'updates': records, 'turn': world.turn}

    def apply(self, prepared: Mapping, world: DirectiveWorld, transaction_id: str) -> dict:
        generation = self.commit(list(prepared['updates']), expected_generation=prepared['expected_generation'],
                                 world=world, reason=str(prepared['action']), transaction_id=transaction_id)
        return {'ok': True, 'completed': True, 'native_action_executed': False,
                'directive_generation': generation, 'directives': [summary(r) for r in prepared['updates']]}

    def flush_notices(self, attention) -> None:
        # Outbox is canonical. A crash after enqueue but before marking delivery
        # reuses the same payload/cursor/key, hence the same at-least-once item.
        for r in self.read()['records'].values():
            notice = r.get('notice')
            if not notice or r.get('delivered_notice') == notice['key']:
                continue
            receipt = attention.enqueue('unit_directive', notice['payload'],
                                        observation_cursor=notice['cursor'], turn=notice['turn'],
                                        priority=85 if notice['blocking'] else 45,
                                        critical=False, dedupe_key=notice['key'])
            self.journal.append(self.scope, 'directive.notification_delivered',
                                {'directive_id': r['directive_id'], 'key': notice['key'],
                                 'attention_id': receipt['attention_id']}, timeline_id=self.timeline_id)


def transition(record: dict, state: str, reason: str, world: DirectiveWorld, **detail) -> dict:
    changed = record.get('state') != state or record.get('reason') != reason or record.get('detail', {}) != detail
    record.update(state=state, reason=reason, detail=detail)
    if changed and state in {'paused', 'completed', 'failed'}:
        key = 'directive-notice-' + uuid.uuid4().hex
        record['notice'] = {'key': key, 'turn': world.turn, 'cursor': world.cursor,
                            'blocking': state == 'paused', 'payload': summary(record)}
    return record


@dataclass(frozen=True)
class Step:
    actor_ref: str
    command: str
    destination: str = ''
    # A next unknown square is geometry derived from an issued native adjacent
    # choice, not a route through undisclosed terrain. Never provider-facing.
    position: tuple[int, int] | None = None
    former_id: int | None = None


class DirectivePlanner:
    """Choose routine mechanical steps solely inside a pre-approved contract."""
    def __init__(self, world: DirectiveWorld):
        self.world = world

    def evaluate(self, original: Mapping, *, reserved: set[str] | None = None) -> tuple[dict, Step | None]:
        w = self.world
        r = deepcopy(original)
        if r['state'] not in RUNNABLE or r.get('pending'):
            return r, None
        if r['epoch'] != w.epoch or r['shape'] != vars(w.topology.shape):
            return transition(r, 'paused', 'world_epoch_changed_review_required', w), None
        try:
            actors = {a: w.actor(a) for a in r['actors']}
        except DirectiveError as exc:
            # Missing projection data is not proof of destruction.
            destroyed = any(w.objects.get(a, {}).get('status') == 'destroyed' for a in r['actors'])
            return transition(r, 'failed' if destroyed else 'paused', str(exc), w), None
        for a, unit in actors.items():
            hp = field_value(unit, 'hp')
            if not isinstance(hp, (int, float)) or hp < r['hp'].get(a, hp):
                return transition(r, 'paused', 'unit_damaged', w, actor_ref=a), None
            r['hp'][a] = hp
            if unit['location_ref'] != r['last_positions'].get(a):
                r['last_progress_turn'] = w.turn
                r['last_positions'][a] = unit['location_ref']
            threats = w.threats(unit['location_ref'], r['policy']['threat_radius']) if r['policy']['threat_radius'] else []
            if threats:
                return transition(r, 'paused', 'relevant_hostile_contact', w, contact_refs=threats[:8]), None
            contacts = w.contacts(unit['location_ref'])
            new = sorted(set(contacts) - set(r['known_contacts']))
            if new and r['policy']['interrupt_on_new_contact']:
                return transition(r, 'paused', 'new_local_contact', w, contact_refs=new[:8]), None
            new_bases = sorted(set(w.bases(unit['location_ref'])) - set(r.get('known_bases', [])))
            if new_bases and r['policy']['interrupt_on_new_contact']:
                return transition(r, 'paused', 'new_local_base_evidence', w, base_refs=new_bases[:8],
                                  meaning='New to this directive vicinity; not proof of construction or ownership change.'), None
        if w.turn >= r['review_turn']:
            return transition(r, 'paused', 'scheduled_strategic_review', w), None
        actor = actors[r['actor_ref']]
        if r['kind'] == 'explore':
            return self._explore(r, actor, reserved or set())
        try:
            destination = w.location(r['target_ref'], moving=r['kind'] == 'follow')
        except DirectiveError as exc:
            return transition(r, 'paused', str(exc), w), None
        target = w.objects[r['target_ref']]
        if target.get('kind') == 'base' and field_value(target, 'owner_ref') != r.get('target_owner'):
            return transition(r, 'paused', 'target_ownership_changed', w), None
        at_destination = actor['location_ref'] == destination
        if r['kind'] == 'escort':
            escort = actors[r['escort_ref']]
            if at_destination and escort['location_ref'] == destination:
                return transition(r, 'completed', 'both_participants_arrived', w), None
            if field_value(actor, 'ready') is not True:
                r.update(state='waiting', reason='waiting_for_protected_unit')
                return r, self._wait_step(escort)
            if actor['location_ref'] == escort['location_ref'] and field_value(escort, 'ready') is not True:
                r.update(state='waiting', reason='waiting_for_escort')
                return r, self._wait_step(actor)
            # Leapfrog co-located participants. The protected actor only steps
            # onto the guard's square; the guard never goes more than one step
            # ahead. A separated pair rendezvous before progressing.
            if actor['location_ref'] != escort['location_ref']:
                distance = w.topology.shape.distance(w.position(actor['location_ref']), w.position(escort['location_ref']))
                if distance > 1:
                    if field_value(escort, 'ready') is not True:
                        r.update(state='waiting', reason='waiting_for_escort_rendezvous')
                        return r, self._wait_step(actor)
                    return self._travel(r, escort, actor['location_ref'])
                return self._travel(r, actor, escort['location_ref'])
            return self._travel(r, escort, destination)
        if r['kind'] == 'follow':
            distance = w.topology.shape.distance(w.position(actor['location_ref']), w.position(destination))
            if distance <= r['follow_distance']:
                r.update(state='waiting', reason='within_follow_distance', last_progress_turn=w.turn)
                return r, self._wait_step(actor)
        if at_destination:
            if r['kind'] == 'work':
                return self._work(r, actor, destination)
            return transition(r, 'completed', 'arrival_verified', w), None
        return self._travel(r, actor, destination)

    def _wait_step(self, actor: Mapping) -> Step | None:
        # Waiting is explicit authorization to use this actor's skip, not a
        # permission to skip unrelated units or close a turn without consent.
        return Step(actor['object_ref'], 'skip_unit') if field_value(actor, 'ready') is True and field_value(actor, 'order_name') == 'none' else None

    def _travel(self, r: dict, actor: Mapping, destination: str) -> tuple[dict, Step | None]:
        w = self.world
        if field_value(actor, 'order_name') != 'none':
            return transition(r, 'paused', 'native_order_requires_explicit_activation', w, actor_ref=actor['object_ref']), None
        if field_value(actor, 'ready') is not True:
            r.update(state='waiting', reason='movement_exhausted')
            return r, None
        if w.turn - r['last_progress_turn'] >= r['policy']['max_idle_turns']:
            return transition(r, 'paused', 'no_objective_progress', w), None
        route = w.route(actor['object_ref'], destination, r['policy'])
        if not route.reachable or len(route.path) < 2:
            return transition(r, 'paused', 'no_permitted_known_route', w, target_ref=r.get('target_ref', destination)), None
        if 'arrival_review_turn' in r and route.turns is not None and w.turn + route.turns > r['arrival_review_turn']:
            return transition(r, 'paused', 'estimated_detour_exceeds_authorization', w,
                              estimated_remaining_turns=route.turns), None
        next_ref = route.path[1]
        threats = w.threats(next_ref, r['policy']['threat_radius']) if r['policy']['threat_radius'] else []
        if threats:
            return transition(r, 'paused', 'next_step_enters_known_threat_radius', w, contact_refs=threats[:8]), None
        r.update(state='active', reason='following_authorized_route')
        return r, Step(actor['object_ref'], 'move_unit', next_ref, w.position(next_ref))

    def _work(self, r: dict, actor: Mapping, destination: str) -> tuple[dict, Step | None]:
        w = self.world
        tile = w.objects[destination]
        if not field_is_current(tile, 'features'):
            return transition(r, 'paused', 'worksite_evidence_not_current', w), None
        former_id, feature = WORK[r['work']]
        features = set(field_value(tile, 'features', []))
        done = feature not in features if r['work'] == 'remove_fungus' else feature in features
        if done:
            return transition(r, 'completed', 'work_effect_observed', w, work=r['work']), None
        if field_value(actor, 'order_name') == 'terraform' and r.get('work_started'):
            task = field_value(actor, 'terraform_task', {})
            if not field_is_current(actor, 'terraform_task') or task.get('name') != r.get('work_task_name'):
                return transition(r, 'paused', 'terraform_task_changed_or_unknown', w), None
            r.update(state='working', reason='authorized_work_in_progress')
            return r, None
        if r.get('work_started'):
            return transition(r, 'paused', 'work_stopped_before_verified_effect', w), None
        if field_value(actor, 'order_name') != 'none':
            return transition(r, 'paused', 'different_native_order_present', w), None
        if field_value(actor, 'ready') is not True:
            r.update(state='waiting', reason='waiting_to_start_work')
            return r, None
        r.update(state='active', reason='ready_for_authorized_work')
        return r, Step(actor['object_ref'], 'terraform', destination, former_id=former_id)

    def _explore(self, r: dict, actor: Mapping, reserved: set[str]) -> tuple[dict, Step | None]:
        w = self.world
        if field_value(actor, 'order_name') != 'none':
            return transition(r, 'paused', 'native_order_requires_explicit_activation', w), None
        if field_value(actor, 'ready') is not True:
            r.update(state='waiting', reason='movement_exhausted')
            return r, None
        scope = {tuple(p) for p in r['scope_positions']}
        unknown = {p for p in scope if p not in w.topology.by_position or w.topology.by_position[p].terrain not in {'land', 'ocean'}}
        if not unknown:
            return transition(r, 'completed', 'authorized_scope_mapped', w), None
        origin = w.position(actor['location_ref'])
        # Only an adjacent unknown square may be nominated. The adapter must
        # match an actually issued legal native move and revalidate its risks.
        adjacent = [(direction, p) for direction, p in w.topology.shape.neighbors(origin).items() if p in unknown]
        if adjacent:
            adjacent.sort(key=lambda dp: (dp[0] != r['direction'], list(DIRECTION_OFFSETS).index(dp[0])))
            r.update(state='active', reason='exploring_authorized_frontier', frontier_target=actor['location_ref'])
            return r, Step(actor['object_ref'], 'move_unit', position=adjacent[0][1])
        safe = w.safe_topology(actor['object_ref'], r['policy'])
        candidates = [s for s in safe.by_ref.values() if (s.x, s.y) in scope and s.location_ref not in reserved
                      and any(p in unknown for p in w.topology.shape.neighbors((s.x, s.y)).values())]
        candidates.sort(key=lambda s: (w.topology.shape.distance(origin, (s.x, s.y)),
                                       w.topology.shape.bearing(origin, (s.x, s.y)) != r['direction'], s.location_ref))
        choices = []
        for s in candidates[:MAX_CANDIDATES]:
            route = w.route(actor['object_ref'], s.location_ref, r['policy'])
            if route.reachable and len(route.path) >= 2:
                choices.append((route.turns or 0, route.movement_cost or 0, s.location_ref, route))
        if not choices:
            reason = 'frontier_search_budget_exhausted' if len(candidates) > MAX_CANDIDATES else 'no_permitted_reachable_frontier'
            # Cannot certify an unknown continent is fully explored from a
            # bounded, policy-constrained miss.
            return transition(r, 'paused', reason, w, unmapped_scope_squares=len(unknown)), None
        _, _, target, route = min(choices, key=lambda item: item[:3])
        r['frontier_target'] = target
        r.update(state='active', reason='approaching_authorized_frontier')
        return r, Step(actor['object_ref'], 'move_unit', route.path[1], w.position(route.path[1]))
