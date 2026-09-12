# Bookkeeping continuation acceptance

The AI-8 Peacekeeper recorded four identical situation summaries at one native
revision in the same episode. The captured provider requests retained preceding
successful calls and the current saved summary. They contained approximately
111K–117K prompt tokens. This establishes repetition despite available evidence;
it does not establish a context-length failure. A historical notification also
carried obsolete readiness fields beside the correct current native protocol.

## Implemented boundary

- Guarded typed writes compare normalized writable fields with the active
  canonical journal record input. Identical content returns the existing durable
  identity without another record/event. Schema, observation and canonical
  citation validation precede comparison. Evidence, intent, status and content
  changes remain writes. Keyless goal creation remains intentional creation.
- The internal result and audit retain the full record. Provider rendering keeps
  a compact explicit saved/already-persisted receipt; ambiguous failures are not
  compacted. Emergency cognition cleanup preserves the completion receipt.
- Four consecutive writes to the same durable record at the same native revision
  (including the initial save) open the existing incident/circuit path when the
  subsequent writes are unchanged. Revised content or evidence breaks the
  sequence. Restart clears the in-process counter, not durable idempotence.
- Notification projection separates historical informational state from current
  readiness. Original journal capture remains intact. Obsolete protocol and
  ready-unit counts are removed; informational content remains available.
- Choice execution can explicitly acknowledge a reviewed attention lease before
  dispatch. Invalid acknowledgement prevents dispatch. Successful acknowledgement
  remains successful if execution fails. Native, scope, authority and critical
  attention guards remain in place; acknowledgement does not mean resolution.
- Prompt wording favors existing valid frames and deliberate bulk skip, and
  explicitly rejects routine unchanged situation-summary rituals.

## Deterministic evidence

`bookkeeping_continuation_test.py`: real journal head unchanged after four
identical summary writes; changed rationale/evidence persists; canonical reopen;
invalid citation rejected; bounded facade repetition; independent attention and
execution outcomes; historical notification capture unmodified.

`memory_contract_test.py`: all seven families read/edit/write successfully;
explicit-key identical submissions retain canonical event identity; failures and
binding-preservation contracts pass. Evidence and faction-reference suites pass.

`continuation_retention_test.py`: completion receipt flattened, durable identity
and success preserved, ambiguous failure unchanged; strategic prose and unresolved
receipts retained. Strict prompt, opaque execution/rebase, attention communication,
empty-attention and runtime-context contracts pass.

These tests demonstrate interface, persistence, recovery and projection behavior.
No new campaign or native mechanical prediction is required for this change.
Live long-run reduction in repeated calls remains unverified; no speedup is claimed.

## Isolated provider replay

Two nonstreamed, 1,024-output-token-capped completions used the captured request,
with tool execution disabled externally (no returned call was dispatched). The
updated variant applied compact receipt rendering, historical notification
projection, and the no-summary-ritual wording. It intentionally retained the
original tool schema; combined acknowledgement was not evaluated in this replay.

The baseline acknowledged the saved summary, then chose a goal update (424 output
tokens). The updated variant acknowledged the saved summary and chose attention
acknowledgement (120 output tokens). Neither reproduced the identical-summary
loop in this single sample. This is an inconclusive stochastic comparison, not
proof of causal improvement or context-length degradation. Sanitized counts are
in [bookkeeping-replay.json](bookkeeping-replay.json). The deterministic
idempotence and repetition tests remain the acceptance basis.

## Deployment

The public `smacx-agent` stack now runs control and harness images tagged
`bookkeeping-continuation`; new workers use the matching worker tag with the
unchanged reviewed native bridge. Control health passed and installed controller,
MCP, runtime projection, prompt and Hermes-hook hashes matched the checkout.
AI-7 and AI-8 remained completed. No timers or new games were started. Existing
data volumes, provider configuration and Graphiti settings were preserved.
