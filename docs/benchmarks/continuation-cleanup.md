# Continuation cleanup acceptance

**Baseline correction:** the percentages below are incremental cleanup savings,
not net savings from pre-PR code. The [matched baseline audit](continuation-baseline-audit.md)
finds a remaining history-policy regression. Net performance acceptance is open.

Receipt cleanup checkpoint: expired bundled decisions no longer pin full historic
receipts. Completed receipts retain outcomes, journal references and all unknown
fields (which may contain verification obligations); only obsolete handles and
next-step instructions are removed. Unknown/failed outcomes remain intact apart
from transport decoding and expired prior-episode recovery catalogs. Latest
unseen tool results are protected. Strategic prose is unchanged.

Captured-request replay (no provider invocation):

| Capture | Requests | Original serialized bytes | Replayed bytes | Reduction |
|---|---:|---:|---:|---:|
| AI - 4 Spartan | 66 | 13,558,019 | 12,279,712 | 9.43% |
| AI - 4 Peacekeeper | 45 | 9,128,890 | 8,598,634 | 5.81% |

`continuation_capture_replay.py` verifies unchanged assistant prose, latest unseen
results and original transcripts. Measurements are provider message JSON bytes,
not exact tokens or wall-clock savings. Runtime context is protected with the
latest tool result; it must not be mistaken for disposable historic receipt text.

Semantic checkpoint: physical masses expose incomplete / indeterminate-stale /
observed-closed boundary coverage, including at demoted LOD and registry queries.
Unknown neighboring terrain is never classified as ocean. Closure is not evidence
of ownership or absence of rivals. A bounded text heuristic flags possible
geographic overstatement against incomplete observed areas, without changing
beliefs or confidence or claiming a definitive contradiction.

Movement receipts distinguish the earlier native-resolution sample from a later
current owned-unit position. Reconciliation requires the fresh decision revision;
it supersedes placement only, not path, cause, arrival or objective completion.
Unavailable or mismatched evidence leaves the comparison unavailable and never
replays an action.

Validation: receipt/prose/provenance/unknown-obligation tests, movement revision
and observation tests, geographic closed/incomplete/stale fixtures, full
geographic semantics and fair-play contracts pass. Runtime assembly exercises
the review signal with unchanged belief input at 64K and 256K; budgets remain
bounded (6,019 / 9,389; Huge chaotic 27,102 conservative tokens).

Installed-image acceptance: actual Hermes sanitizer tests pass current-batch,
complementary-query, nested-decision consumption and immutable-history checks.
Real Hermes HTTP capture passes initial/resumed gameplay and communication,
request/diagnostic correspondence, runtime-only context, lease release/retry,
reasoning settings and oversized SQLite-history preflight. No paid provider used.

Shared-stack deployment verified at source commit `652144a`: seven installed
source hashes match. Control API is healthy; the new MCP/harness image selection
is configured. Provider, Graphiti, portal, native worker, networks and volumes
were preserved by configuration comparison. AI - 4 remains parked at turn 5,
with zero active sovereign runs and zero incidents. No migration or game resume.

- Control image: `sha256:6b32db6a18a1e7ed284bcf7e2d3b0110c1dff53a282385703bcbaeaa9710a9cd`
- Harness image: `sha256:79707132b02ec7894102a48bbe34e8a1d9d8a28fd1df2eb69fabb8ef192594e5`

Live turn-time improvement and strategic response to the review signals remain
unmeasured. Captured replay establishes input reduction and retention only.
