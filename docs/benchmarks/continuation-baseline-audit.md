# Continuation baseline audit

The PR's net speed and context-reduction targets are **not accepted**. The earlier
6–9% byte reduction compared cleanup with the first PR deployment, not with the
original baseline. It must not be presented as net improvement from pre-PR code.

## Matched history-policy comparison

`continuation_baseline_audit.py` reconstructs the original assistant/tool history
from full opening-to-capture diagnostic traces (or retained-message exports).
For each request it runs that same history cut through the real installed Hermes
sanitizer with a selected policy revision. Captured system prompt, runtime tail,
tool schema and model template settings remain fixed. It calls only `/tokenize`,
never generation, the game, or a memory writer. Durable history is immutable.

Replaying the first PR policy reproduced **111/111 captured request tokenizer
counts exactly**: 45 Peacekeeper and 66 Spartan requests. The captures span the
opening and multiple turn/episode boundaries. This is not a synthetic long-history
fixture and does not compare two different trajectories as if they were identical.

| Policy | Peacekeeper count sum | Spartan count sum | Combined delta from original policy |
| --- | ---: | ---: | ---: |
| Original, `fd7c2bc` | 1,884,307 | 2,869,347 | baseline |
| First PR deployment, `203857f` | 2,317,919 | 3,554,160 | +23.53% |
| Deployed cleanup, `7f0d03f` | 2,162,290 | 3,217,923 | +13.18% |
| Additional lossless transport formatting | 2,125,718 | 3,177,325 | +11.56% |

These are sums of current tokenizer chat-template counts over identical request
positions, not unique tokens, billing, or measured live generation usage. Both
policies receive the same post-PR outcomes, including bundled decisions. Therefore
this isolates history policy; it cannot reconstruct all requests/actions that a
model playing under the original PR baseline would have generated.

The original policy deletes assistant messages accompanying old tool calls, and
their tool results, at episode boundaries. The new policy retains strategic prose,
unresolved failures and outcome evidence. That has a measurable cost. On these
cuts the original policy retains 277,900 aggregate assistant prose characters;
the current policy retains 660,319. These are repeated per-request counts, not
unique prose. Reverting blanket deletion would sacrifice that retention contract.

The added formatting pass only removes JSON whitespace and sole `result` transport
wrapping from previously delivered managed results. It preserves untrusted-wrapper
text, unknown fields, sibling metadata, evidence status, assistant prose and raw
history. Latest unseen results stay byte-for-byte intact. It reduces the deployed
cleanup's count by 1.43%; it does **not** solve the remaining regression.

## Live observations and limits

The earlier AI3 sample (original policy) had 64 explicit decision queries / 81
execution attempts. AI4 had 29 / 59, and the sovereign used 28 of 31 bundled next
decisions. This supports the mechanism's intended use, not a causal speed claim.
The samples contain different actions, failures, maps and provider conditions.

Across those samples, recorded prompt tokens per execution attempt were about
104,640 versus 104,358 (essentially flat), while aggregate provider response time
per attempt rose from about 44 to 54 seconds. Those ratios include non-execution
requests and are descriptive only. They do not establish that the PR caused the
latency change or that a saved query yields a fixed token/time saving.

The AI5 early checkpoint likewise does not show a live speedup: median provider
responses were about 37–38 seconds, versus tool batches around 0.12–0.13 seconds.
Captured requests specify low reasoning. No paid comparison campaign was run.

Accounting discrepancy: the current tokenizer returns 54,680 for one exact AI5
captured request whose corresponding response recorded 82,077 prompt tokens.
Including tools and reasoning settings does not reconcile it. The replay matches
that request's current tokenizer count, but recorded provider usage must remain a
separate measurement until the server/template/accounting difference is explained.

## Acceptance and disposition

- Keep the guarded next-decision mechanism: actual sovereign selections use it.
- Keep retention of strategic prose, uncertain evidence and action outcomes;
  do not describe stricter retention as a compression improvement.
- Do not merge/accept the PR on a claim of net token reduction or faster turns.
  Performance acceptance remains open; a broader revert is not justified solely
  by this comparison because the old deletion behavior violates retention goals.
- The local-base-tile advisory now explicitly distinguishes current-location
  restriction from whole-turn impossibility. Relocation is conditional on fresh
  guarded legality, not a forced strategy or promised action.
- Retention/transport contracts, real Hermes complementary-result and pressure
  tests, and opaque-choice/advisory adapter tests pass. No native mechanic changed.
- These follow-up changes are not deployed by this audit. The current campaign
  is untouched. No checkpoint restore or improved model behavior is asserted.

See [sanitized numeric evidence](continuation-baseline-audit.json). To reproduce,
export the chosen revision's `harness/smacx_strict_prompt.py` (and
`smacx_continuation.py` when present), put it first on `PYTHONPATH` in the pinned
Hermes image, mount the capture read-only, and invoke the script with `--tokenizer`,
`--label`, and `--output`. Use `--verify-captured` only with the capture's deployed
policy; it asserts tokenizer-count equality at every request.
