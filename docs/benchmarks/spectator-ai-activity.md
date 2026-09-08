# Spectator AI activity acceptance — 2026-09-08

The spectator page now owns fullscreen and retains its Lobby and AI activity
controls when a stream ends. A resizable desktop panel / mobile bottom sheet
shows emitted provider output, collapsed reasoning and collapsed tool arguments
and results. Minimize preserves the current transcript and scroll position;
polling pauses while hidden. Following resumes only at the bottom or by clicking
the new-activity button. Native bot seats do not expose an AI activity panel.

## Capture and access

The existing diagnostic writer emits a bounded activity projection alongside
its raw audit stream, within the same match storage quota. Stream chunks are
published incrementally (first chunk immediately, then roughly every half second
while chunks arrive, or 16 KB), before the closing response receipt. Projection
excludes request prompts/history and headers; credential keys, structured result
credentials, bearer values and configured provider keys are filtered. No database
or migration was added. Diagnostic failures never change tool/transport behavior.

The portal resolves the requested seat's agent on the server and applies
spectator access checks. Durable participant exclusion also applies to admins.
AI-only stopped campaign transcripts remain available to nonparticipants.
Reads use owned, network-disabled Docker helpers with read-only harness volumes,
including after the sovereign container has stopped. The panel polls every three
seconds while visible (ten when idle); each page reads at most 1 MiB / 150 events
per profile using complete-record byte cursors. The live DOM retains 200 replies;
full retained history is available through JSON download.

Download streams JSON pages for every AI seat in the match without accumulating
the match in browser memory. It includes request/run/call correlation, emitted
reasoning, answers, tools, and explicit capture gaps. This is a latest retained
history export, not a native checkpoint or proof of gameplay effects. Older runs
without the new activity projection are explicitly directed to the diagnostic ZIP;
they are not silently represented as complete transcripts. The final provider
receipt does not replay chunks already displayed. Recovery runs remain distinct.

## Validation

- .NET: 86/86 tests. New tests verify ended-match access, durable participant
  exclusion, server-side seat selection, and real controller multi-page JSON export.
- `activity_contract_test.py`: incremental cursor reads, foreign match exclusion,
  no prompt exposure, credential filtering, incomplete tail retry and no final
  receipt duplication.
- `activity_stream_test.py`: real HTTPX transport with controlled streaming;
  activity is readable before completion and original transport bytes are unchanged.
- `activity_reducer_test.mjs`: repeated event IDs do not duplicate text; tool
  request/results reconcile into one entry; new runs do not overwrite old runs.
- Existing provider audit, provider request state and diagnostics contracts pass.
- `activity_docker_test.py`: installed control-image module through the production
  owned, read-only Docker helper; stopped history, cursor resume and agent scope.
  Only a unique fixture volume/container were created and removed.
- Chrome browser validation against the actual authenticated Blazor page on a
  separate loopback test portal: 1440px, 768px and 390px; no horizontal overflow;
  expanded thoughts/tools; fullscreen; completion with navigation still available;
  scroll-up preservation, resume-follow, minimize/reopen, replay deduplication.
  JSON download used the real portal endpoint with a controlled two-page control
  response. The map/video frame and live model events were controlled fixtures,
  not a newly started native match or live provider run.

Screenshots: [desktop](spectator-activity-desktop.png),
[fullscreen](spectator-activity-fullscreen.png), [phone](spectator-activity-phone.png).

Browser reproduction: build the portal, then run it on loopback 8182 with
`SMACX_PORTAL_DATA=$PWD/runtime/astra/activity-ui-data`,
`SMACX_CONTROL_URL=http://127.0.0.1:8183`,
`SMACX_PORTAL_SERVICE_TOKEN_FILE=$PWD/runtime/astra/activity-test-token`,
`ASPNETCORE_URLS=http://127.0.0.1:8182`, and `ASPNETCORE_ENVIRONMENT=Development`.
Run `scripts/spectator_activity_browser_test.cjs` with Node and Playwright available
(`NODE_PATH=runtime/astra/browser-tools/node_modules` in this validation).
The script bootstraps a disposable local admin, seeds only that local test database,
starts a controlled backend on 8183, and closes its browser/backend afterward.
It must not be aimed at the production portal. Stop the local portal after testing.

## Deployment boundary

This PR is not deployed to the running main stack. Deployment requires rebuilding
portal, control/MCP and harness images; native worker changes are unnecessary.
Existing sovereign processes need a normal stopped/reprovisioned start to load
the new capture module. No game, timer, provider setting or main-stack container
was changed for this UI review. Actual provider/game acceptance remains for the
next run; this checkpoint does not close the full-game mission.
