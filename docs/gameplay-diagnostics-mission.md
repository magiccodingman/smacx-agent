# Gameplay diagnostics acceptance mission

Controlling specification: the user's 19-section gameplay observability review,
accepted proposal, and autonomous execution instruction. This mission does not
equate native turn progression with competent strategic play. No automatic merge.

Baseline: AI - 3, match-2a0fe070c51c48089642bfeb556d8b51, is parked at turn 26.
Preserve its saves, Hermes history, journal, attention and checkpoint evidence.
Previous action-progress PR #54 is merged. New work starts from updated main.

## Reviewable checkpoints

1. Causal diagnostics: Hermes pre-dispatch failures, managed tool calls/results,
   deferred native effects, actor/call/request/revision correlation, and explicit
   capture failures. Human summaries must never invent a verified effect.
2. Provider request audit: final wire content, exposed tools, runtime envelope,
   cognition revisions, omissions and history compaction. Distinguish assembled,
   submitted and responded; SQLite existence is not proof of provider receipt.
3. Cognition and specialist lifecycle: synchronous visibility, journal failure
   windows, handoff/resume/GC/recovery, reference tools and child traces, attention
   placement and acknowledgement. Do not equate acknowledgement with action.
4. Semantic/action corrections: named research preferences, qualified discovery
   changes, production consequences, management availability and nonblocking
   focus. Replay historical production/citizen scenarios against native state.
5. Bounded typed-intent reconciliation, including automatic native turn changes.
   Reuse plans/goals; explicit defer/cancel/block choices; no prose-to-obligation
   inference or mandatory completion of long-horizon strategy.
6. Authorized live/archive lobby diagnostic download, consistent event watermark,
   completeness manifest, performance/retention checks and integrated acceptance.
7. Deploy and resume or replace acceptance campaign. Monitor every ten minutes,
   inspect traces and strategic behavior, pause/fix/test/update PR/deploy as needed.

Update coverage and evidence at every checkpoint before moving on. Individual
classes or synthetic tests do not complete a checkpoint. Diagnostics stay outside
journal authority and outside sovereign input. Preserve fair-play and native safety.

## Initial storage substrate evidence

`smacx_diagnostics.py` introduces versioned per-actor streams with unique event and
stream IDs, sequence and timestamps, correlation fields, credential-field redaction,
bounded record payloads and a terminal capacity-gap record. Contract tests cover
concurrent records, actor isolation, redaction and explicit size-limit gaps.

This is only the storage substrate. No live interception, provider-wire audit,
export authorization, aggregate retention or gameplay correctness is claimed yet.
Generic field redaction is not sufficient for arbitrary raw provider/network data;
capture adapters must supply allowlisted metadata and separately sanitized content.
