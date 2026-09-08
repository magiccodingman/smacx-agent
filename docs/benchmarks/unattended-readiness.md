# Unattended readiness checkpoint — 2026-09-08

Based on main `136ed8a` (including PRs 64 and 65). This checkpoint prepares an
operator-led fresh run; it does not claim full-game or strategic acceptance.

## Repairs

- Native progress is sampled, not timestamped at execution. Preserve the prior
  sample as the earliest possible effect time when a changed fingerprint is
  observed. A provider request begun in that interval can drain. The ordinary
  deadline still uses detection time; the 180-second hard bound, same-request
  latch, failure handling, and 30-second completed-response dispatch bound remain.
- Profile preparation with omitted reasoning preserves the existing selection
  for the same agent/provider/profile. First preparation defaults to low; an
  explicit selection remains authoritative. Both HTTP preparation/run routes
  preserve omission. This does not prevent an operator explicitly selecting xhigh.
  The previous Astra xhigh configuration was an explicit script override.

## Acceptance evidence

Passed host tests: provider_drain_window_test, harness_continuation_contract_test,
control_plane_test. The drain test exercises actual supervisor reconciliation,
including a request started between samples and quarantine at the hard deadline.
The profile test uses a persisted store and proves initial low, omitted high
preservation, and explicit return to low.

Passed container tests: production_support_surplus_test,
managed_action_path_contract_test, strategic_world_fixtures_test,
memory_scope_feedback_test, journal_memory_evidence_test,
campaign_diagnostics_contract_test, operator_recovery_guard_test,
diagnostic_history_readonly_test, persistent_order_attention_test,
memory_guard_discovery_test, incident_recovery_test. The control/profile and drain
tests also pass against installed image source (test scripts mounted separately).

These establish adapter/state-machine behavior, scoped evidence, uncertainty
preservation, order follow-through attention, guarded writes and recovery. They
are not new live native comparisons. No new expansion or bookkeeping defect was
demonstrated by this bounded audit. Earlier live evidence remains in the Astra
benchmark records; its strategic conclusions are confounded by xhigh and repairs.

## Deployment and handoff

Main project `smacx-agent`, portal http://localhost:8080/: HTTP 200; control healthy.
Control and future MCP sidecars use `smacx-agent-control:readiness-pr66`, built from
this branch. No native, portal, harness, Graphiti, or prompt changes were needed.
The active portal AI profile `SMACX player` has Qwen3.8-27B and reasoning low.
Existing main harness profiles use low and 262144 context. Settings were not edited.
No active sovereign runs were present. No new game or provider request was started;
actual first-request setting verification remains a next-run check.

Compose base: `/home/dmint/Documents/ai/smacx-agent-2/runtime/deploy-pr48/compose.json`.
Local deployment override:
`/home/dmint/Documents/ai/smacx-agent-astra/runtime/astra/main-readiness.override.yaml`.
Include both with `-p smacx-agent` to preserve the deployed image selection.
Only control-api was recreated. The separate Astra stack and timer remain stopped.
Data volumes were preserved. Disk had 53 GiB available before this small build.

The operator can start the agreed fresh Peacekeeper game and report an incident,
completion, or sustained strategic stagnation. Preserve the campaign diagnostic
download before restarting/recovering an incident when practical. Export manifests
explicitly report truncation and damaged tails; a bundle must not be assumed complete.

Remaining live gates: first-request low setting; sustained expansion and plan
updates; spontaneous specialist delegation and use; natural canonical citations
and revised memory-error feedback consumption; historical identity fallback;
broader native dialogs; uninterrupted full-game completion. No timer is required
for this handoff. No PR is merged by the agent.
