# Spectator activity identity repair (2026-09-08)

The real match `match-3daf21457a03434c95468522e0114911` had active sovereign
diagnostics but the spectator endpoint returned `available:false`, empty events
and idle status. It queried the reusable portal AI profile identity instead of
the distinct runtime seat identity used by harness runs. The earlier controlled
UI fixture did not exercise this differing-ID seam.

Both live read and JSON export now resolve the selected seat index through the
authoritative control match before querying activity. The profile ID is never a
fallback. A missing runtime mapping becomes an explicit read error/export gap.
Spectator permissions and invalid-seat checks still precede the control lookup;
completed matches resolve their durable control seats as well.

Validation: all 87 portal tests passed. The activity regression uses differing
profile/runtime IDs and another runtime AI seat, verifies live read and two-page
export target the selected gameplay identity, rejects a missing mapping without
fallback, and verifies participant denial performs no control requests.

Live rollout exposed a second seam the small fixture missed: Docker fragments
large stdout writes, so a one-line log tail truncated the JSON activity page.
The reader now collects the bounded page's fragments; malformed output becomes
an explicit activity error. The portal error envelope now uses nullable object
data rather than an undefined JsonElement, which previously caused a secondary
HTTP 500 during serialization. The real-Docker fixture now includes 40,000
characters and verifies complete retrieval, cursor resume and seat isolation.

Deployed main control/portal images `smacx-agent-control:activity-identity` and
`smacx-portal:activity-identity`. Both health checks pass; the deployed control
still matches the reviewed worker fingerprint. Local rollout appends
`runtime/astra/main-activity-identity.override.yaml` after the prior overrides.

Real authenticated endpoint verification returned 150 events on the first page,
`available:true`, the correct runtime agent ID, and no gaps. Whole-match JSON
download returned 599 events with no gaps. Real Chrome, through the HTTPS
certificate-warning acceptance flow, opened the selected match's spectator page
and rendered 11 activity message cards. Screenshot inspection confirmed actual
sovereign output in the panel. The match was parked by this validation stage;
retained activity worked without a live game worker. New live inference was not
started for this check. Earlier deployed tests on the small fixture had missed
both the identity difference and Docker fragmentation; these are now explicit
regression cases.

This repair does not change inference settings, native gameplay, or diagnostics
storage. No campaign stop/resume command was issued during this repair.
