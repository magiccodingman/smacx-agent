# Disposable specialist investigations

The sovereign player owns every strategy, belief, relationship, promise, goal,
plan, diplomatic commitment, and game action. Specialists are disposable
read-only information processors for questions that would otherwise flood the
sovereign transcript with many world or rules queries.

## Model-facing contract

The sovereign sees one compact tool, `smac_investigate`:

- `commission` creates an idempotent `world` or `reference` mission from a
  bounded objective, provider-safe subject references, and an optional
  operation link; the authenticated seat scope is implicit and the platform
  chooses the execution class;
- `result` returns compact lifecycle state or the accepted/stale strict result;
- `retry` requests one policy-bounded fresh attempt for an eligible mission;
- `cancel` closes pending work without granting any new authority.

The sovereign cannot select a provider, model, raw toolset, capability, budget,
deadline, retry count, or internal execution class.

`action=direct_reference` is the bounded exception for one focused mechanics
lookup. It returns at most four compact results under a 2,048-token content
ceiling through the same `smac_investigate` schema and does not create a child.
Collection and large-document reads use bounded excerpts and monotonic
continuations. Multi-document or context-heavy synthesis remains specialist
work.

A mission may be useful for a multi-base defense comparison, a route/rendezvous
analysis, or research that spans several mechanics articles. It is not required
for a routine unit or modal decision. Bounded direct `smac_world` queries remain
available to the sovereign.

## Supervisor and attempt isolation

One installation supervisor claims durable queued missions fairly across seats.
Every attempt creates a new:

- Hermes process and process group;
- temporary `HERMES_HOME`, workspace, session, config, and profile;
- nonce-bearing capability document; and
- short faculty-specific system prompt.

The process receives no sovereign Hermes home, game/worker volume, personality,
runtime context, chat, notebook, Graphiti namespace, or campaign memory. Built-in
terminal, file, browser/web, memory, skills, code execution, and delegation
toolsets are disabled. The MCP capability exposes exactly one stdio tool:
`world_query` for a world analyst or `reference_query` for a mechanics
researcher. Neither faculty can mutate the game or itself create another
specialist.

The world tool reads a content-addressed perspective snapshot pinned at mission
creation. The reference tool talks only to the private knowledge service at the
mission's corpus revision. A new attempt never inherits a prior attempt's
transcript or reasoning, including a retry or schema repair.

## Authority, dependencies, and publication

World snapshots are immutable accelerators tied to exact match, seat,
perspective, timeline, world epoch/revision, observation cursor, projection
hash, and journal head. The snapshot remains pinned while a mission can run.
If an otherwise retry-eligible world mission exhausts automatic retries, its
mission pin becomes a bounded manual-retry retention lease rather than being
released immediately. Normal snapshot GC preserves that exact frozen view.
Manual retry must occur before both the mission deadline and retention lease
expire and atomically reclaims the same snapshot while moving the mission back
to the queue. No fresh/current world is silently substituted.

Dependencies are recorded by the platform from each successful child query:
world objects and query fingerprints/hashes for world work; document and corpus
revisions for reference work; plus calculator/coverage dependencies where
applicable. Child prose cannot expand or weaken this set.

The result is accepted only if it matches the strict schema:

```text
mission_id
answer
claims[] { claim, citations[], epistemic_status }
limitations[]
unresolved_questions[]
```

Every material non-unknown claim must cite an exact evidence reference actually
returned during that attempt; an empty citation list is invalid. The compact
sovereign result also includes a provenance receipt with source timeline/world
or corpus revision, dependency hash, bounded representative evidence refs,
measured provider/tool usage, latency, result hash, staleness, and limitations.
The child transcript and full internal dependency graph are never copied into
sovereign context.

Publication uses compare-and-swap. A changed active timeline or world epoch
cancels late publication. A changed actual world dependency or corpus revision
marks the result stale. Unrelated world changes do not invalidate narrowly
derived evidence. Journal publication is idempotent and startup reconciliation
repairs the narrow database/journal crash window. A terminal result enqueues
attention for the persistent sovereign; it never invokes sovereign cognition
concurrently.

## Hard leashes and recovery

Both synthesis and investigation classes enforce hard limits for MCP calls,
provider calls, cumulative provider tokens, per-request context, published result,
wall time, retries, and strict-schema repairs. An attempt-local loopback provider
proxy measures actual OpenAI-compatible HTTP/SSE traffic and reserves provider-call output
headroom before forwarding each request; child-authored usage files are never
trusted as the production leash. Defaults are conservative but operator
configurable. A timeout, cancellation, shutdown, rollback, operation
completion, or turn handoff reaps the child process group and records a typed
outcome. The locked baseline permits exactly one running child per sovereign
seat; installation-wide concurrency remains independently configurable, and
round-robin admission prevents one seat from starving another.

Failed provider usage is accounted just like successful usage. Missing provider
usage is charged at the reservation rather than treated as free. A repair is a
fresh attempt with an explicit typed rejection reason and strict-JSON/citation
correction, never a continuation of the rejected child. Claim citations must be
exact evidence references mechanically returned during that fresh attempt.

## Traces, backup, and operations

Each retained attempt trace is JSONL compressed with Zstandard and hashed. It
contains the mission envelope, exact specialist-system-prompt hash,
provider/model/profile and generation controls, sanitized provider-visible
assistant/reasoning trajectory, every sanitized MCP request/result, retries and
failures, usage/context/latency telemetry, raw final result, and validated final
result when one exists. Secret-shaped fields and bearer/key material are
redacted. Traces are diagnostic only: they are never placed in model context or
become authoritative campaign history.

The Operations page provides:

- provider/profile selection and independent specialist policy;
- synthesis and investigation leash controls;
- current concurrency and supervisor health;
- content-free mission/attempt usage, latency, stale/failure, and trace sizes;
- retained trace download; and
- manual trace garbage collection.

Successful and unsuccessful traces have separate completed-campaign-checkpoint
generation retention horizons. An attempt is tagged with the generation that
exists when the attempt terminates, so specialist snapshots cannot age traces
and an attempt spanning a checkpoint receives the completion generation.
Protected recent failures are never silently removed to satisfy the byte
ceiling; the operator receives a warning and new full trace capture stops until
policy or storage is corrected. Normal control backup and restore include the
retained trace tree and manifests.

## Verification

The deterministic and captured-wire gates are:

```bash
PYTHONPATH=src python3 scripts/specialist_contract_test.py
PYTHONPATH=src python3 scripts/specialist_supervisor_contract_test.py
PYTHONPATH=src python3 scripts/specialist_provider_capture_test.py
PYTHONPATH=src python3 scripts/specialist_provider_meter_test.py
```

An opt-in real-provider run uses real Hermes and emits content-free telemetry:

```bash
PYTHONPATH=src python3 scripts/specialist_provider_live_test.py \
  --base-url http://provider.example/v1 --model model-id --reasoning low
```

These gates prove mission/attempt separation, immutable input, actual-call
dependencies, isolated provider requests, stable system/tool prefixes, strict
results, stale/CAS behavior, attention delivery, retries, schema repair, hard
cancellation, concurrency fairness, compressed trace integrity, and retention.
Terminally failed world missions keep their immutable snapshot pinned only for
the configured manual-retry horizon. Throttled housekeeping releases expired
pins during normal supervisor operation (restart is unnecessary), and `retry`
independently rejects an expired horizon even if housekeeping has not run yet.
