# Typed memory cleanup acceptance

AI - 7 was safely parked at turn 8/year 2108 before deployment. The first park
request did not complete; the second completed the paired checkpoint and reported
`parked`, with no last error. No game or database reset is required.

## Interface and deterministic acceptance

The live review found full-record plan revisions missing title and then objective,
leaving the old intent unresolved. Controlled reproductions also found malformed
participants silently removed and misplaced top-level reconciliation ignored.

`smacx_memory_contract.py` now defines the seven managed record schemas used for
field discovery, detailed contract reads, input validation and editable templates.
Updates retain replacement semantics. Goal key omission creates a new goal;
claims append events with the current journal view keyed by topic. Other records
replace their current stable identity. Omitted optional fields reset; explicit
empty lists remain valid. No inferred merge, automatic retry or strategy is added.

`smac_memory(action="contract", record_kind=...)` provides nested types and defaults.
`smac_memory(action="editable_record", record_kind=..., key=...)` returns one complete
canonical writable JSON record without SQL metadata. Edit and explicitly submit
through the existing guarded writer. Reading does not reserve native freshness.
This also retrieves inactive/completed records, without working-set truncation.
Oversized legacy records are preserved and fail the editable budget explicitly;
they never produce a silently truncated replacement template.

Shape validation collects up to 32 errors, including nested paths, before memory
projection or journal writes. Unknown/misplaced fields, malformed participants,
numeric strings/booleans, invalid windows, non-finite values and duplicate JSON
keys are rejected. Intent and confirmation extension metadata remains preserved
but is explicitly not a mechanical predicate. Current/stale/unknown applicability
still belongs to current world evidence and plan health, not accepted syntax.

Persistence receipts distinguish no write, uncertain journal commit after SQL,
and known journal commit with failed runtime projection. Existing scoped evidence,
observed faction identity, native revision and journal authority checks remain.

Passed source-mounted checks:

- `memory_contract_test.py`: all seven record types through actual managed tool
  functions and controlled native revision adapter; read-edit-write preserves
  bindings, metrics and uncertainty; multi-error rejection leaves replay unchanged;
  explicit clearing, stale guards, journal reopening and failure stages.
- Existing memory status, scope, discovery, faction-reference, repair-context and
  journal-evidence contracts.
- Intent reconciliation, managed memory scale, runtime context and plan health.
- Cognition delivery through the real journal/runtime/Hermes sanitizer and controlled
  HTTP transport, including next request, handoff, semantic GC and restart.
- Provider schema budget: 15 tools, 8,180 conservative tokens total; world schema
  842. These are proxies, not exact Qwen counts. No system prompt text changed.

The generic memory read regression reached 2,010 tokens; the new explicit single
editable-record path has its separate 65,536-character record ceiling. It is an
on-demand edit operation, not an automatically injected context section.

## Deployment and live acceptance

Installed control-image checks passed with only test scripts mounted: seven-family
contract, status feedback, guard discovery and schema budget. Source files were
read from the rebuilt image. Controlled real-Hermes capture also passed: initial/resumed tool schemas,
provider request capture, resume preservation and generation settings. Paired
turn-8 restore passed for both seats; both sovereign processes are now verified active, with healthy workers/MCP and no incidents. Controlled tests
prove interface/adapter behavior, not that the sovereign will choose the right
revision or improve strategy. The prior repair-bonus interpretation problem remains
open. No speed improvement is claimed for this correctness cleanup.

All existing check-in automations remain paused; no new timer is authorized.

Deployment uses control/MCP image `smacx-agent-control:memory-contract`
(`07748bf272a0cfb80a136d57447e3c8b83989400cc2d3cda08f68040536ed851`)
and harness image `smacx-agent-harness:memory-contract`
(`a89d8b7b2f874e1566ec336b1db7037413c965a943777c73304733e94e185f29`).
Running control source hashes match the committed contract, controller and MCP.
Compose comparison verified unchanged unrelated services, provider/Graphiti
settings and storage. The readiness stack was not modified. Both restarted MCP
sidecars use the new control image. Checkpoint
`checkpoint-aa1a4ee01aee49cb94a8e85f4aa0a8bb` is `restore_tested` for two seats.

Final health: `observed_active`, two active runs/two live sovereign processes,
turn 8, no incidents or run-verification errors. Sanitized operational receipt:
[`memory-contract-deployment.json`](memory-contract-deployment.json). Timers remain
paused. The owner continues live behavioral evaluation.
