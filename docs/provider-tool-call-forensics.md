# Provider tool-call forensics

This procedure investigates malformed model tool calls without starting
Hermes, an MCP server, a game worker, or Alpha Centauri.

## Historical finding

The preserved August 31 endurance sessions contain exactly six rejected
deferred calls:

| Underlying tool | Preserved invalid arguments | MCP execution |
| --- | --- | --- |
| `smac_command` | `{}` | Rejected before invocation |
| `smac_command` | `command` and `expected_revision`; missing match/session | Rejected before invocation |
| `smac_lan` | `{}` | Rejected before invocation |
| `smac_list` | `{}` | Rejected before invocation |
| `smac_chat` | `{}` | Rejected before invocation |
| `smac_memory` | invented `memory_key`/`content`; missing action/match | Rejected before invocation |

All six outer calls were valid JSON. Hermes's deferred-tool validator noticed
the missing required fields and did not let any malformed mutation reach MCP or
the game. The historical state databases preserve the normalized call, not the
provider's original SSE bytes, so those databases alone cannot identify where
the arguments were lost.

The current direct probe reproduces the defect before Hermes. With an outer
`tool_call` schema containing a nested `arguments` object, the provider returns
a shape equivalent to:

```json
{
  "name": "mcp__smacx__smac_command",
  "arguments": "",
  "command": "acknowledge_popup",
  "match_id": "match-provider-wire-probe",
  "session_id": "session-provider-wire-probe",
  "expected_revision": "16893526205145507771"
}
```

The required fields have been flattened beside `arguments`, while `arguments`
itself has become an empty string. The same defect occurs in streaming and
non-streaming Chat Completions, and with `preserve_thinking` both enabled and
disabled. The observed provider fingerprint was
`vllm-0.28.0-tp2-76893fa0`; the provider also ended the forced tool call with
`finish_reason: "stop"` rather than `"tool_calls"`.

This proves the reproduced corruption exists in the provider/model-serving
boundary before the SMACX client stack. Server-side token/parser diagnostics
are still required to distinguish model-emitted syntax from the serving
engine's tool parser or serializer, but Hermes, MCP, and the game cannot be the
source of this reproduced response.

## Standalone reproduction

The probe uses only Python's standard library. Point it directly at any
OpenAI-compatible endpoint:

```bash
python3 scripts/provider_tool_call_probe.py \
  --base-url http://model-host:8000/v1 \
  --model Qwen3.8-27B \
  --capture /tmp/smacx-tool-wire.jsonl
```

The default suite makes five requests covering the six historical call slots,
including the parallel list/chat case. Historical sampling values and
`preserve_thinking=true` are used by default for faithful reproduction. Test
the corrected SMACX setting independently with:

```bash
python3 scripts/provider_tool_call_probe.py \
  --base-url http://model-host:8000/v1 \
  --model Qwen3.8-27B \
  --no-preserve-thinking
```

Test non-streaming serialization with `--no-stream`, increase repetition with
`--runs 20`, or isolate one shape with `--case command_full`. For a protected
endpoint, place the key in `OPENAI_API_KEY` or name another environment
variable with `--api-key-env`. The key is never written to the capture.

Each request prints one JSON result. `provider_side_reproduction: true` means
the invalid shape was observed on the raw provider wire. Exit status is zero
only when every requested shape is returned intact; status one means at least
one request failed validation. Captures contain the synthetic request, every
raw SSE line or response body, and the classification result so the serving
stack can be debugged without any private game transcript.

Run the local parser regression without contacting a model:

```bash
PYTHONPATH=scripts python3 scripts/provider_tool_call_probe_test.py
```
