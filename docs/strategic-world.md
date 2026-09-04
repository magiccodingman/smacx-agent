# Strategic perception and sovereign cognition

SMACX Agent gives each autonomous seat a complete, fair-play mechanical world
outside the provider transcript and a bounded view of that world inside each
request. The model is still the sovereign player: deterministic services expose
facts, geometry, constraints, and evidence, while the model alone owns strategy,
beliefs, goals, diplomacy, commitments, plans, and actions.

## Authority and data flow

```text
native UI thread
  -> bounded observation ring and perspective pages
  -> external collector
  -> hash-linked campaign journal (temporal authority)
  -> perspective projection + checkpoint snapshots (rebuildable)
  -> hierarchical semantic world and query cache (rebuildable)
  -> one current anchor + net deltas + focus + attention + cognition
  -> request-only trusted tail runtime context
  -> sovereign provider request
```

The native bridge is the first fair-play boundary. It exposes owned state,
currently visible foreign objects, remembered map information, contacted
factions, and explicitly entitled Pact/infiltration/Governor/satellite/scenario
fields. A second entitlement pass rejects admin, spectator, and hidden candidate
data before projection. Unknown geography never becomes a route or region.

Global intelligence follows the same rule. Owned economy, research, Planetary
state, orbitals, base radius/yields/facilities, scenario and victory posture are
native perspective facts. Foreign economy, research, and orbital report fields
exist only when their exact Pact, infiltration, Governor, or Empath Guild
entitlement is present. Secret Project races enter observed history through the
public BEGIN/CHANGE/HALT/SURVIVE/DONE report popups; a builder is named only
when that report actually disclosed one. Missing rival production remains
`unknown` rather than being read from hidden native arrays.

The campaign journal is authoritative temporal evidence. SQLite world tables,
region graphs, anchors, search indexes, query caches, and Graphiti are derived.
A projection head names an exact `(match_id, agent_id, perspective_id,
timeline_id, world_epoch, world_revision, observation_cursor)`. Mechanical
world revisions change only when projected material changes; action revisions
may change independently as the native engine pumps.

## Observation and continuity

The native ring holds 1,024 compact events and can return at most 256 at once.
The collector drains it independently of model tool calls and then reconciles
bounded paged perspective state. If a consumer falls behind the oldest retained
event, the bridge returns `continuity=incomplete` and
`reconciliation_required=true`. The collector records that gap in the journal,
marks the observation projection incomplete, queues critical attention, and
performs a full perspective reconciliation. It never invents missing events.

Native request handling remains UI-thread bounded. A socket thread only posts a
request into the serialized Windows message slot. Potentially synchronous game
effects—including Turn Complete—are recorded as deferred actions and begin only
after the bridge request frame unwinds. This is a safety invariant, not a
performance optimization.

## Epistemic fields and identities

Every material field carries value, epistemic status, provenance, and a last
verified turn/cursor where available:

- `current`: presently verified from an allowed channel;
- `stale`: remembered but not presently visible;
- `reported`: attributed speech or public report;
- `derived`: deterministic output from named known inputs;
- `estimated`: a bounded possibility from incomplete known inputs; and
- `unknown`: deliberately unknown.

Mechanical observations do not receive invented numeric confidence scores.
Subjective likelihoods—such as whether a speaker is lying or whether a lost
contact is probably the same rover—belong in sovereign beliefs.

Owned entities use stable perspective-local references where the native domain
permits it. A foreign unit receives an opaque contact identity only for one
continuous visible episode. When it leaves vision that identity is retired; a
later similar unit is a new contact. A lost-contact envelope describes possible
known-world locations and explicitly denies identity continuity.

## Geometry, mechanics, and semantic zoom

`smacx_topology.py` models SMACX's parity lattice, horizontal wrap, flat maps,
eight neighbors, and known-square boundary. Mobility profiles separately model
land, sea, air, roads, Mag Tubes, fungus, hostile ZOC, refueling, air drops,
transports, carriers, and special connections. Deterministic calculators expose
routes, reachability, ETA, bearings, response matrices, rendezvous windows,
base defense/production facts, support, convoys, transport capacity, air
recovery, lost-contact envelopes, site affordances, and narrow connectors.
They do not rank strategy.

The semantic mipmap makes prompt size track strategic complexity rather than
tile count:

1. known squares form versioned mobility-specific regions;
2. region lineages record merges/splits across revisions;
3. regions roll into frontiers and theaters;
4. active focus, operations, triggered watches, contacts, threatened bases,
   projects, and global systems promote local detail;
5. quiet peripheral regions demote to summaries; and
6. overflow reports omitted counts and query handles instead of silently
   disappearing.

The 64K tier caps the anchor at 6,000 estimated tokens; the 256K tier caps it at
16,000. These are ceilings inside one coherent runtime allocator, not separate
full-envelope budgets. Identity, focus, live cognition, critical attention,
operations, and delta reserve are allocated first; anchor generation receives
the smaller of its tier ceiling and the remaining 13,107-token (64K) or
32,768-token (256K) runtime envelope. Both tiers derive from the same
perspective facts. A Huge quiet map must
remain within 15 percent of an equivalent small quiet map. Active complexity,
not raw tile count, earns detail.

## Anchor and query contracts

There is exactly one materialized current anchor per perspective/context tier.
It has a content identity, world-anchor revision, and observation cursor. It is
regenerated when the world epoch or turn changes, material delta pressure
crosses its threshold, strategic structures change, observation age expires, or
focus/operation/watch promotion changes. Regeneration replaces the prior
materialized anchor; history remains in the journal rather than accumulating in
provider context or anchor tables.

Between regenerations, `net_deltas` is the difference between the anchor
baseline and current projection—not an append-only stream. The anchor plus that
delta reconstructs the current perspective. If delta complexity grows too
large, the service creates a new anchor and the delta resets.

`smac_world` is the single provider-facing semantic-zoom facade. Its modes are
`overview`, `area`, `relation`, `route`, `reachability`, `compare`, `base`,
`forces`, `logistics`, `intel`, `changes`, `global`, and `render`. Compact,
standard, and deep results have fixed ceilings and explicit truncation. Cached
results bind scope, timeline, world epoch/revision, ruleset hash, calculator
version, request fingerprint, and dependency hash. Unrelated changes may retain
a result; a changed dependency invalidates it.

`render` returns an optional perspective-safe semantic SVG string inside the
ordinary `smac_world` tool result. It is explanatory, not authoritative, and
has no native screenshot or pixel data. It is not a PNG, image-content message,
or completed multimodal-provider path.

## Focus, operations, plans, and watches

- **Focus** is regenerated from the current native snapshot. It identifies the
  immediate modal, ready unit, wait, gap, or other current concern. It remains
  present after attention acknowledgement and cannot be acknowledged away.
- **Operation** is bounded disposable working context for a genuine multi-query
  or multi-unit problem. At most eight may remain active/stale; only one is
  foreground. Dependencies make an operation stale, a world-epoch change makes
  it invalid, completion removes it from runtime, and turn handoff expires
  unlinked scratch work.
- **Plan** is durable sovereign intent in the campaign journal. An operation may
  promote a useful conclusion into a plan.
- **Watch** is a typed attention preference over up to 16 world references.
  At most 32 may remain active. Watches have priority, typed predicates,
  optional goal/plan links, default ten-turn TTL, renewal, deduplication, and
  garbage collection. Platform-critical attention never depends on a watch.

## Attention and communication

Attention is at-least-once and uses a sequence independent from the observation
cursor. Its lifecycle is `captured -> persisted/queued -> leased -> placed ->
responded -> acknowledged`. Placement means only that an item entered a provider
request. A failed/aborted invocation abandons the lease and redelivers the same
`attention_id`; it cannot falsely consume the event. New events arriving during
one provider call wait for the next lease. Acknowledgement is contiguous,
idempotent, and batchable through `smac_attention_ack` after actual cognition.

Chat captured mid-generation, mid-tool, outside the native turn, or across a
restart follows the same queue. A communication episode acquires the same
per-seat sovereign lease and therefore cannot overlap a gameplay invocation.
It has the same personality and durable cognition, may read, reason, negotiate,
write typed cognition, send chat, and acknowledge attention, but its native
gameplay mutation gate is closed. Player speech remains `reported` evidence.

## Provider context and memory

The stable v6 system message contains behavioral invariants, immutable seat
identity/policy, fair-play rules, epistemics, and cognitive responsibilities. It
contains no live board state. Hermes retains its durable conversation and the
current episode's valid assistant/tool sequence.

Immediately before provider transport, the harness deep-copies Hermes messages,
runs semantic garbage collection, obtains a fresh runtime context, escapes
untrusted look-alike tags, and appends exactly one trusted
`SMACX_RUNTIME_CONTEXT` envelope to the latest existing user/tool message. It
does not create a synthetic user boundary and never changes the durable Hermes
messages or `api_content`. This keeps the static system/history prefix stable
for vLLM prefix caching while placing current truth at the authoritative request
tail.

The runtime envelope contains identity, one current anchor plus net deltas,
focus, a bounded attention lease, projected durable cognition, active operation
context, watch count, and optional Graphiti interpretive recall. Token
composition is measured per component. The 64K minimum reserves output,
reasoning, system/tools, and pinned runtime truth before accepting disposable
history.

Within an episode, reasoning survives tool calls. After an episode boundary,
historical reasoning and completed tool protocol leave provider replay, while
ordinary durable assistant cognition remains. Superseded state frames and
duplicate query evidence compact first; old complete disposable tool pairs are
removed only under pressure. Older committed cognition writes collapse to
typed journal receipts, and mature memory/notebook reads become evictable query
evidence while the newest active tool pairing remains intact. Current focus,
anchor, commitments, critical
attention, and the newest relevant evidence are reconstructed and cannot be
evicted by this cleanup. The shared policy runs semantic cleanup at no later
than 40 percent of the provider window, ahead of Hermes's 50-percent generic
compression trigger. Hermes compression is therefore a fallback after the
SMACX semantic manager, not its primary current-game memory mechanism.

The bounded `TURN HANDOFF` fields are `Outcome`, `Rationale`, `Changed
conclusions`, `Next intent`, and `Uncertainty`. It preserves cognitive residue,
not routine board narration.

## Disposable specialists

`smac_investigate` commissions one durable mission for either the
`reference_researcher` or `world_analyst` faculty. A long-lived supervisor starts
a fresh Hermes process, home, workspace, session, profile, and capability for
every attempt. The child receives its own short system contract and exactly one
MCP instrument: `reference_query` or `world_query`. It can therefore conduct a
bounded multi-step investigation instead of receiving a caller-curated evidence
dump.

The child receives no sovereign transcript, personality, live runtime envelope,
game/sovereign volume, terminal, files, web, chat, memory writer, delegation, or
mutation tool. A world mission reads one immutable content-addressed perspective
snapshot pinned to its timeline, epoch, revision, and observation cursor. A
reference mission is pinned to one corpus revision. Actual child calls create
the dependency set; the model cannot declare its own freshness boundary.

Missions and attempts are separate durable records. Each attempt has hard wall,
tool-call, provider-call, cumulative-token, context, output, retry, and schema-
repair limits. Cancellation reaps the process group. Successful output must
match the strict cited-claim schema. Publication uses compare-and-swap and
becomes stale or is rejected when an actual dependency, corpus revision,
timeline, or world epoch no longer matches. Completion only queues at-least-once
attention; it never starts a second sovereign invocation. Results remain
fallible evidence and cannot own strategy or action.

Compressed, secret-redacted attempt traces are diagnostics, not campaign state
or model memory. Their manifests participate in normal backup/restore; success
and failure generations have separate retention floors and an operator byte
ceiling. See [Disposable specialists](specialists.md) for the full contract.

## Checkpoint and rollback

A world snapshot is a content-addressed projection accelerator tied to the exact
journal head, sequence, observation cursor, world epoch/revision, checksum, and
calculator versions. Recovery verifies the native save, journal head, Hermes
slice, and world snapshot before activating a new timeline. It then restores the
projection and removes future attention acknowledgements, watches, operations,
contact identities, query caches, specialist jobs/results, Graphiti namespace,
chat projection, and other derived future state. Source checkpoint snapshots
remain available for deterministic retry; ordinary retention owns their later
collection.

See [Game semantics coverage](game-semantics-coverage.md), [Agent loop](agent-loop.md),
[MCP tools](tools.md), [Storage](storage-lifecycle.md), and [Testing](testing.md).
