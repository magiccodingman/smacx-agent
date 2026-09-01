# Follow-up: AI runtime efficiency and capability visibility

This investigation is deliberately separate from runtime-storage work so its
performance changes are measured rather than guessed.

## Questions to answer

- Why did two five-turn agents each submit about four million prompt tokens?
- Was `low` reasoning passed through the portal, control plane, Hermes profile,
  chat request, and provider, or replaced by a model default such as `xhigh`?
- How much prompt space came from system instructions, tool schemas,
  transcript, observations, durable-memory recall, or compression input?
- Why did an ordinary turn require roughly 20 model calls, and which calls can
  be combined without weakening fresh-state mutation guards?
- What were the six malformed tool calls: model argument mistakes, adapter
  translation failures, or stale tool definitions?
- Why did Hermes compression take roughly 251–278 seconds per agent?
- How effective was provider prefix caching, and did concurrent agents contend?

## Baseline corrections completed before retest

- Qwen3.8 instant/low/medium/xhigh templates start from official sampling values,
  explicit current-turn thinking selection, and `preserve_thinking=false`.
- Templates are editable starting points. Stored explicit fields—not a preset
  name reconstructed later—are the provider-facing contract.
- Historical reasoning is no longer carried forward by default.
- Arbitrary provider parameters are JSON-typed and validated instead of being
  hardcoded as Qwen fields in the generic profile path.
- Blank context uses the discovered advertised value; manual contexts are
  clamped to the advertised maximum and Hermes's 65,536-token minimum.
- The provider-facing system message is the exact hashed project contract,
  replacing rather than appending Hermes's general system prompt.
- Static rules use bounded SemanticKnowledge evidence and Graphiti ingests only
  curated asynchronous political memory.

The remaining questions require a fresh controlled run; do not compare the old
four-million-token trace as though it used this baseline.

## Required telemetry

Capture request-level time to first token, prefill and decode tokens/second,
provider queue time, prefix-cache reads/writes, prompt-component sizes,
reasoning effort as requested and observed, tool-validation failures,
compression input/output size, and compression duration. Never infer raw TPS
from whole-turn wall time.

## Remaining supervision follow-up

Capability reports are now durable portal incidents with a **Needs attention**
dialog, restart suppression, preserved native state, and a redacted diagnostic
bundle. Future runtime work should extend the same operator experience to silent
stalls and repeated invalid decisions that never reach `smac_report_capability_gap`.
