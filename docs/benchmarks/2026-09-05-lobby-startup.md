# Lobby startup UX acceptance — 2026-09-05

Scope: startup visibility and navigation only. Seat ownership, participant
spectator exclusion (including participating administrators), controller leases,
reclaim voting and verified-checkpoint recovery are unchanged.

## Implementation evidence

- Original user Loading.png copied byte-for-byte to the portal static assets.
  The illustration scales proportionally without cropping. Blue indeterminate
  MudProgressLinear, live phase text, elapsed time, status confirmation age,
  focus trap, and Stay in lobby are separate accessible UI elements.
- The initial HTTP materialization step exposes a process-local
  StartupRequestedAt marker. It is cleared in finally, is not a native health
  receipt, and disappears on process restart. Existing durable lifecycle states
  remain authoritative. Duplicate concurrent launch requests are rejected.
- Existing startup phases are observed through SignalR and five-second polling.
  Failed/invalid/timeout responses remove the overlay and disarm automatic entry.
- A visit beginning with running does not arm entry. A visit witnessing startup
  navigates once using existing CanControl/CanSpectate permissions and managed
  instance availability. Pending player-seat synchronization has a 60-second
  bound; it does not strand the player behind an indefinite overlay.
- Stay in lobby applies to the current page visit. Reloading during startup is a
  new visit and shows startup again; reloading an already running lobby stays put.
- Browser disconnect/delegation/reclaim rules are unchanged. No schema migration
  or database reset is needed for these additive response/UI changes.

## Validation

- Portal suite: 72 passing tests, including launch-state transitions and an
  authenticated HTTP assertion for the early startup marker and seat flags.
- Chrome exercised the actual Lobby Razor component and production overlay in
  an isolated local preview host with deterministic API responses. It verified
  Start → overlay → spectator navigation; later running-lobby visit → Watch live
  without navigation; startup failure → visible error with no overlay; lost
  connection → connection warning with automatic entry off; Stay in lobby →
  continuing startup without the overlay.
- Visual inspection: desktop, 768×1024 tablet and 390×844 and 320×568 phones. Image remains
  intact; phone footer stacks and the entry button spans the card width.
- These fixture checks prove UI behavior, not a new successful native campaign
  run. Existing stream/controller endpoints remain the authority at destination.

Deployment verification is recorded below after the image is rebuilt.

## Deployment

- Release portal image built successfully from implementation commit cca618f.
- Recreated control-api, control-center and specialist-supervisor using the
  existing local Compose configuration; the Compose health wait completed.
  Control API and portal are healthy; specialist supervisor is running (no
  configured health probe). Shared storage/services were preserved.
- Running portal image matches the rebuilt tag exactly:
  `sha256:c6f2456e2f7ddbcb6ab69b3c75caa045dbc1053f6767ae50257eed6f406a303e`.
- The deployed loading image is served successfully and matches the original:
  `010f90ca5358c7142efbbf9e7d8c98a5e509a9d965a26a579ef9e00c2e0ccd58`.
- No database reset, new native match, or unverified campaign recovery was
  performed as part of this UI deployment.
