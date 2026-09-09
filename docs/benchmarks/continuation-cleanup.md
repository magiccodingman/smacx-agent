# Continuation cleanup acceptance

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

Installed-image provider checks and deployment read-back: pending.
