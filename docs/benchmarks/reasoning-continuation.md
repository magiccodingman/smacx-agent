# Reasoning continuity and interface clarity

## Correctness checkpoint

AI - 5 captures established two avoidable interface ambiguities. Runtime
attention omitted `acknowledgement.tool_arguments` while the tool description
instructed the sovereign to copy it. The bounded envelope now retains it and
explains that `responded` does not mean acknowledged. Placement still restricts
the lease to serialized items before acknowledgement; omitted items are requeued.
Fresh bundled decision identities are explicitly accepted by memory guidance.
No guard, acknowledgement authority, native action, or journal rule changes.

## Retention checkpoint

The provider wire retains the latest nonempty current-episode reasoning segment,
including across interleaved tools. Older private reasoning is removed only from
the request copy. Visible prose, tool evidence and durable cognition retain their
independent existing policies. Full diagnostic history remains unchanged.
This deliberately removes earlier private alternatives: it is not a claim of
lossless reasoning retention or proven strategic equivalence.

Real Hermes policy tests cover earlier uncertainty prose, latest reasoning,
whitespace placeholders, original transcript immutability, execution receipts,
pending results, episode boundaries and pressure recovery. Attention tests cover
full acknowledgement from delivered arguments and omission/requeue behavior.

## Measurement correction

The tested Qwen `/tokenize` endpoint silently ignores `reasoning_content`, while
generation includes it. Explicitly aliasing that field to `reasoning` for
measurement reproduced two historical provider input counts within 17 tokens.
This is an endpoint-specific measurement adapter, not a generation payload change.
The earlier 111-cut audit measures the non-reasoning portion; its percentage
comparisons must not be represented as total generation input savings. Retained
message exports also omit reasoning, so replay reports now disclose this limit.

`scripts/reasoning_continuation_audit.py` measures captured requests and optionally
generates bounded continuations without executing returned tools. Raw responses
stay outside Git. The sanitized report records counts and usage. Historical vs
fresh response timing is descriptive: server load and sampling differ.

Live turn-time, output-token and strategic-quality acceptance remains open until
AI - 7 is reviewed. No hard reasoning cap or discouragement of reconsideration
was introduced by the deployed policy; the experiment alone uses an 8192-token
output bound. Existing low reasoning configuration remains unchanged.

## Bounded provider experiment and installed acceptance

Two reasoning-only replays reduced input by 21.70% and 13.53%. Fresh provider
usage exactly matched the corrected tokenizer counts. Output was 2666 versus
5080 and 3399 versus 6015 historical tokens. These two samples do not establish
a general output reduction. Both returned parseable allowed memory tools with
required arguments and captured guards; neither was executed against the game.
Plans and uncertainty remained, but repetitive reconsideration and the historical
Former deferral remained too. The attention example selected different bookkeeping,
so full behavioural acceptance is pending. See [sanitized results](reasoning-continuation-audit.json).

Passed: runtime-context contract (including omitted attention remaining queued),
attention communication/redelivery contracts, installed opaque-choice contracts,
installed Hermes context policy, and real Hermes controlled provider capture
including startup/resume, low reasoning passthrough, scoped tool errors and durable
history preservation. Native mechanics and doctrine are unchanged.

Deployment checkpoint (2026-09-10 UTC): installed source matches runtime commit
`8c3ce86`. Control image `4859f16187d428be504dfdf3cae38525509f63bd8d7f0e5f24c3401967dad5a3`;
harness image `45e2f20f53a083ab3c75a1eb8e449f087e83941836831d629d75ed85a019cff6`.
Control is healthy; installed MCP, runtime assembler and Hermes sanitizer hashes
match source. Only control-api and specialist-supervisor containers were replaced;
other main and readiness service containers and provider/Graphiti configuration
remain unchanged. AI - 6 is completed and its workers are retired.
