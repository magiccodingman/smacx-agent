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

## Required telemetry

Capture request-level time to first token, prefill and decode tokens/second,
provider queue time, prefix-cache reads/writes, prompt-component sizes,
reasoning effort as requested and observed, tool-validation failures,
compression input/output size, and compression duration. Never infer raw TPS
from whole-turn wall time.

## Capability-gap follow-up

Promote worker-local capability reports into durable portal incidents, surface
an immediate **Needs attention** state, stop restart loops, detect silent stalls
and repeated invalid decisions, and offer a redacted diagnostic bundle. This is
deferred to the focused AI-runtime investigation.
