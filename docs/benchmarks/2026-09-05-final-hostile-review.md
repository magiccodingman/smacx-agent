# Final hostile-review repairs

Reviewed baseline: `234bcc2f277e48288e16c0b97a3d1458087d53b2`. PR remains unmerged.

## Gate A — F1/F3 publication transaction

Accepted targeted native-shaped evidence: `publication_transaction_test.py`,
`plan_dependency_publication_test.py`, `native_observation_contract_test.py`,
`observation_batch_recovery_test.py` passed in the supplied MCP container.
[Crash matrix](results/2026-09-05-publication-transaction.json): ten boundaries
× unchanged/reversed native state, all 20 passed (including interruption inside
acknowledgement). Service, store and journal instances are reconstructed on retry. Birth completion, garrison
arrival/departure, current-field changes and scope invalidation use candidate N
on its first evaluation, without pre-saving the fixture's candidate.

Invariant: canonical N evidence and idempotent watch/milestone effects computed
from immutable candidate N precede head N. Head installation certifies completion
of those pre-head effects, but does not certify dependency attention. A remaining
frozen package plus exact installed cursor/identity/action revision requires
finishing dependency attention and acknowledgement before draining N+1. A failed
pre-head publication replays its immutable input; a failed post-head publication
finishes from the installed N head. No native mutations are replayed. Existing
attention dedupe and enqueue-before-state ordering provide one logical transition
with at-least-once delivery. No extra phase table or duplicate map snapshot was
introduced. The final repeated distribution and retained failures appear below.

## Gate B — F2 visible episodes

Accepted: `transient_episode_publication_test.py`, `event_time_episode_test.py`,
`native_observation_contract_test.py`, `semantic_consumer_contract_test.py`.
[Six staging/publication cases](results/2026-09-05-transient-episodes.json) retain
transient appear→cross→loss/destruction with neither snapshot containing the
contact, including a 256-event page boundary, stage-page restart and post-head
publication retry. Four episodes of one private handle remain distinct; reset,
discontinuity and cross-perspective cases pass.

Ordered appearances issue scoped hashed temporal episode refs when no legitimate
surviving snapshot contact exists. Only the final continuously visible episode
may associate with the final snapshot contact. Loss/reset closes continuity;
current whereabouts become unknown, or destruction is explicitly confirmed.
These records are temporal evidence, not invented current world contacts. Native
handles remain private. All evidence is deterministic/native-shaped; the native
bridge is unchanged.

## Gate C — F4/F5/F6 reference lifecycle

**Accepted for final hostile peer review, without merging.** Targeted and final
integrated contracts pass; all collector distributions are recorded below. [Targeted results](results/2026-09-05-derived-lifecycle.json)
cover land/ocean one-to-one changes, splits and merges before persisted history
refresh. Only newly calculated geography is current authority; old rows are
lineage inputs. Direct watches follow the existing deterministic one-successor
migration policy and invalidate ambiguous splits. Scopes and explicit required
refs retain frozen identity and reject stale footprints rather than silently
retargeting. Operation and plan/milestone dependency invalidation is verified.
Frontier/theater anchor summaries no longer resurrect absent current handles.

Warm successes record a private validated inspection revision/action revision
and timestamp independently of creation revision. Ordinary anchors promote the
inspected target after unrelated-change reuse and restart, without manually
supplied inspection refs. True dependency changes stop current promotion.

Query results retain **64 recent rows plus explicitly pinned results** in the
active epoch. Active watches, scopes, operations and journal plan/milestone
requirements each independently preserve old valid receipts. Journal plans are
read from canonical current records, not SQL cognition mirrors or a truncated
HUD. Retention precedes full dependency validation; identical dependency sets
share their digest during a resolution pass. Native receipt action-revision and
perspective/timeline/epoch/rules lifetimes remain mandatory. No database schema
migration, second registry, or universal reference framework was introduced.

| Historical queries | Rows retained | Initial registry/cleanup | Watch | Scope inspection | Runtime context |
| --- | ---: | ---: | ---: | ---: | ---: |
| 100 | 65 | 56 ms | 18 ms | 21 ms | 280 ms |
| 1,000 | 65 | 71 ms | 20 ms | 19 ms | 342 ms |
| 5,000 | 65 | 364 ms | 17 ms | 19 ms | 305 ms |

The first cleanup necessarily visits old receipt metadata; subsequent work uses
active/recent complexity. Population consists of distinct historical cache
receipts shaped from production query output, not 5,000 live provider calls or
5,000 independently validated native movement predictions. An old pinned route
survives restart and unrelated revisions, invalidates on a real dependency and
becomes collectable after release. Existing checkpoint/fork tests verify no
future handles cross into the new timeline.

Legacy synthetic watch/lineage unit fixtures now explicitly inject current
calculator outputs, instead of treating stale persisted rows as authority.
Their migration assertions remain. The new topology contract exercises the
unmocked complete resolver across actual projection changes.

## Repeated collector tail acceptance

The final three-run distribution **passed**: **8.489 / 11.255 / 23.245 seconds**,
with maximum probes **242 / 316 / 327 ms**. Every run is retained in the
[distribution artifact](results/2026-09-05-collector-tail.json), including failures
before the final optimizations. This is native-shaped collector evidence; probes
measure Python pipeline responsiveness, not a new running-game UI comparison.

Two earlier three-run distributions are preserved:
25.011/26.907/34.955 seconds (third probe 533 ms), then after narrow replay copies
17.010/17.332/36.201 seconds. Thresholds were not changed. Source inspection then
identified repeated full canonical-event parsing on every new idempotency key.
A disposable two-perspective key-to-filename index avoids repeated historical
parsing while canonical event files remain authority. Directory changes rebuild
the index; marker deletion, restart and external writers are covered. No event,
manifest, marker or fsync write was removed. Narrow journal section copies retain
full hash-chain verification and detached-copy semantics. Empty watch sets avoid
unneeded geography construction. The original 55.9-second outlier remains
unexplained; these repairs do not retroactively establish its cause.


## Final integrated acceptance and limits

The final integrated batch passed all 31 scripts in that batch. Across this pass,
37 distinct scripts passed; the exact list follows. The
LLVM-MinGW bridge build was checked and already up to date. The production event
adapter was compiled and tested with controlled inputs. Bridge code did not
change, so no unrelated destructive game/LAN test was rerun.

F1/F3: native-shaped production publication with 20 crash cases, including N+1
reversal during downtime and full service reconstruction. F2: native-shaped
staging/publication plus deterministic identity/reset/perspective tests. F4/F5/F6:
deterministic real projection/query/runtime paths and explicit storage stress.
The native adapter run protects the existing event-time relationship contract;
it is not a running-game comparison of the new Python behavior. Prior native,
LAN, .NET, tokenization and prefix-cache evidence remains historical.

Current budgets: **15 tools**, world schema **619 conservative tokens**; Huge
quiet growth **0.285%** for 100× tiles; fragmented **5,449**, chaotic **5,669 /
15,809** anchor tokens remain under 6K/16K. Runtime stress also passes its anchor
ceiling. Prompt bytes/hash remain unchanged; exact provider tokenization was not
rerun. No database schema or top-level tool changed.

Nine scopes/four watches at 6,400 tiles: registry **72 ms**, all-scope inspection
**756 ms**, watch evaluation **149 ms**, maximum descriptor **113 tokens**.
The 25,600-square active collector run passed: initial **8,898 ms**, active
publication **11,599 ms**, maximum independent probe **479 ms**, and two crossing
alerts despite loss in the same publication. Creating its nine scopes took
**45,102 ms** total; this user-request setup cost is reported separately and is
not a native/UI call latency. Large-map scope setup and first legacy cache cleanup
remain real costs. Timing varies on this host; the complete failed and passing
collector distributions remain visible, without weakened thresholds.

Stock Pact contact/reopening map refresh still lacks a controlled comparison;
managed Pact effects do not imply continuous allied live vision. Unsupported
full psych/commerce/research/climate counterfactual simulation remains outside
coverage. No new strategist, universal ranking, doctrine rewrite or hidden-state
channel was introduced. Query GC never confers validity: real dependencies,
stronger native action revision, perspective, epoch and timeline remain authority.

[Sanitized final results](results/2026-09-05-final-hostile-review.json) contain
per-script results and numeric evidence. MCP-dependent scripts ran in the supplied
container; `native_event_time_contract_test.py` ran on the host.

- `scripts/active_scope_benchmark.py`
- `scripts/active_scope_collector_benchmark.py`
- `scripts/attention_communication_contract_test.py`
- `scripts/campaign_journal_test.py`
- `scripts/collector_tail_benchmark.py`
- `scripts/counterfactual_choice_contract_test.py`
- `scripts/counterfactual_contract_test.py`
- `scripts/derived_lifecycle_test.py`
- `scripts/event_time_episode_test.py`
- `scripts/fair_play_world_test.py`
- `scripts/geographic_semantics_contract_test.py`
- `scripts/journal_idempotency_index_test.py`
- `scripts/milestone_contract_test.py`
- `scripts/milestone_threshold_transition_test.py`
- `scripts/native_event_time_contract_test.py`
- `scripts/native_observation_contract_test.py`
- `scripts/observation_batch_recovery_test.py`
- `scripts/plan_dependency_publication_test.py`
- `scripts/plan_dependency_transition_test.py`
- `scripts/plan_health_contract_test.py`
- `scripts/provider_schema_budget_test.py`
- `scripts/publication_transaction_test.py`
- `scripts/query_history_scaling_test.py`
- `scripts/query_pin_consumers_test.py`
- `scripts/rollback_world_contract_test.py`
- `scripts/runtime_context_contract_test.py`
- `scripts/semantic_consumer_contract_test.py`
- `scripts/semantic_ref_matrix_test.py`
- `scripts/semantic_ref_recovery_test.py`
- `scripts/sovereign_geography_acceptance_test.py`
- `scripts/sovereign_hardening_contract_test.py`
- `scripts/spatial_scope_contract_test.py`
- `scripts/specialist_contract_test.py`
- `scripts/strategic_world_fixtures_test.py`
- `scripts/transient_episode_publication_test.py`
- `scripts/world_context_benchmark.py`
- `scripts/world_model_contract_test.py`
