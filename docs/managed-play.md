# Managed play

Managed play is the human-facing runtime around the original Alien Crossfire
process. It combines a real Proton game worker, a browser stream, durable portal
state, native checkpoint verification, and match-local governance. None of
these controls are agent tools: AI seats continue to use only the fair-play
semantic bridge.

## Display model

Each managed seat owns an independent native framebuffer. A browser never
resizes another player's game and a spectator never changes the seat it is
watching.

Two layers deliberately remain separate:

1. **Browser fit** is immediate. CSS centers and scales the current framebuffer
   to the available width and height without cropping, scrolling, changing
   aspect ratio, saving, or pausing the game.
2. **Native profile** changes the actual X11 and Thinker render dimensions for
   the next worker lifetime. It is optional and runs only through the stable
   checkpoint/recovery workflow.

The managed profile catalog is:

| Use | Profiles |
| --- | --- |
| Touch/classic | 800×600, 1024×768 |
| Compact desktop | 1280×720, 1280×800, 1440×900, 1600×900, 1600×1200 |
| Full-size desktop | 1920×1080, 1920×1200, 2560×1440, 2560×1600, 3840×2160 |
| Ultrawide | 2560×1080, 3440×1440, 3840×1600, 5120×1440 |

The worker configures both Xvfb and Thinker's custom window mode from the same
managed dimensions. This matters at 800×600: asking only the stream server
for 800×600 while leaving Thinker at its default 1024×768 produces a clipped
game. The worker writes `video_mode=1`, `window_width`, and `window_height`
before every launch and rejects dimensions below 800×600 or above the supported
pixel envelope.

### Device recommendation

The display panel recommends rather than silently applies a native restart:

- a coarse-pointer device whose shorter side is below 700 CSS pixels selects
  800×600;
- a coarse-pointer tablet whose shorter side is below 1100 selects 1024×768;
- other devices select the largest profile that fits their physical-pixel
  viewport; and
- 800×600 is the safe fallback.

Automatic recommendation and an optional native-profile lock are stored on the
device, not the player account. This lets the same account use a phone and a
desktop without making either one's preference global. A locked profile keeps
the selected native target; browser fitting still reacts instantly.

Fullscreen uses the browser Fullscreen API. On touch devices the portal asks
for landscape orientation when the platform permits it and unlocks orientation
on exit. Safe-area insets are respected. Browser-reserved shortcuts may remain
reserved, but ordinary game keyboard input and text entry pass through the
interactive stream.

The H.264 target is selected from the native framebuffer rather than the
viewer's CSS size: 2.2 Mbps at 800×600, 3.5 Mbps through 1280×800, 5.5 Mbps
through 1920×1200, 8 Mbps through 2560×1600, 12 Mbps through 4K, and a bounded
14 Mbps for the 5K-ultrawide ceiling. A 4K browser watching an
800×600 seat therefore receives the 800×600 stream and scales it locally; it
does not manufacture a wasteful 4K encode. Spectators inherit the seat's
stream and cannot change it.

Each worker serves the same X display through two private, authenticated
Selkies endpoints. HTTPS and loopback clients use the primary H.264 stream with
audio. A browser opened through a plain non-loopback LAN HTTP address is routed
to the worker's JPEG/WebSocket endpoint because WebCodecs is unavailable in
that browser security context. Both endpoints preserve the same controller or
view-only credential boundary; the compatibility endpoint omits audio. Neither
worker endpoint is exposed as a public browser route—the portal remains the
authorization and reverse-proxy boundary.

## One controller per seat

Every open Play page receives an ephemeral, server-owned, user/worker-bound
lease. The first page controls the seat. A second tab or window for the same
account opens in view-only mode and offers **Take control here**. Taking control
cancels the previous reverse-proxy connection immediately; its next connection
is issued view-only credentials. The old page then reflects the new generation
on its heartbeat. Leases expire after 30 seconds without a heartbeat and are
not campaign state.

This remains safe even though ordinary browser cookies are shared between
tabs: each iframe carries its own short-lived lease identifier, and the server
still requires the authenticated seat owner and exact managed worker. A copied
identifier is not a standalone bearer credential.

## Native MENU control rail

The portal polls a read-only `human_ui_state` contract from the native bridge.
That contract exists only in a worker launched with
`SMACX_CONTROLLER_KIND=human`; an agent worker receives
`human_ui_state_unavailable`.

The compact control rail is shown only when all of these are true:

- the exact signed-in user owns the browser-managed human seat;
- the game map is active;
- the native top-level MENU window is visible;
- no child GAME/MAP/ACTION/etc. submenu is visible; and
- no native popup, diplomatic window, or other modal page is active.

Opening a native submenu or modal closes the portal panel and removes the rail.
The rail contains return-to-lobby confirmation, fullscreen, the current native
dimensions, and the managed control center. It does not imitate a native menu
hitbox, cover ordinary play continuously, or create a screen-control path for
an AI.

The control center has four surfaces:

- **Display** — recommendation, device lock, instant fit, and native-profile
  request;
- **Chat** — global, contacted-faction private, and consent-group
  conversations;
- **Votes** — current proposals, quorum, deadline, and the local player's vote;
- **Session** — disconnected/delegated seats, managed-host transfer, recovery
  progress, confirmed safe park/end proposals, and safe return to the lobby.

**Exit game view** is available both on the rail and in the Session tab. It
warns before releasing that tab's controller and returning to the command
deck. Internal portal navigation uses the same themed confirmation; external
navigation, back, refresh, and tab/window close use the browser's native
leave-page warning.

The bridge also labels the exact native `REALLYQUIT` confirmation. On a managed
human worker only, the platform submits the native choice-zero cancel path
(`Oops, no, wait!`) and shows a snackbar explaining the managed alternatives.
No screenshot, OCR, coordinate, or synthetic click is involved. If a rare
quit path bypasses that guard and reaches the native menu, ordinary checkpoint
recovery remains the second line of defense.

## Modern chat over native chat

The portal stores normalized messages with sender handle, sender faction,
recipient faction, native deduplication marker, channel, conversation, and
logical-message identity.

Waiting-lobby Comms is for the humans assembling the table. AI runtimes do not
exist until **Start match**, so those staging messages are neither delivered to
an AI nor replayed into its initial prompt. Once running, browser and native
messages use the game transport and enter the AI's semantic chat view normally.

### Global and private

Global messages use native recipient `0`. Private targets come only from the
local faction's current, fair-play contact list. A player cannot type an
uncontacted faction ID into the API and bypass the native contact rule.

Incoming native messages are polled independently of whose turn it is and are
deduplicated by worker/sequence/sender. The sender's public display name and faction
are kept distinct when both are known.

### Consent groups

A player may invite only currently private-eligible factions. Every invited
member must accept before a group becomes active. Rejecting or leaving changes
that participant's membership rather than pretending the group remained
unanimous.

DirectPlay has no native group-message primitive. The sender's worker therefore
delivers one private native message to each accepted recipient with a compact
group prefix, while the platform records one logical message plus per-recipient
delivery rows. Portal clients render the logical message once. Agent memory
also ingests it once, preventing a three-member group from turning one claim
into three apparently independent observations.

Private and group message bodies are never sent through the broad SignalR lobby
group. SignalR announces that state changed; each client refetches through the
member/faction authorization filter. Authenticated, eligible spectators see
only global chat; former participants cannot enter spectator mode.

Chat text is bounded to the printable ASCII supported reliably by the stock
game transport. Voice chat is not managed by the platform.

## Connected-player governance

Operations that can replace a native process are durable proposals, not direct
browser commands:

- native resolution change;
- waiver of the multiplayer resolution cooldown;
- temporary stock-AI control for a disconnected browser player;
- reclaim of that delegated faction;
- managed-host transfer;
- safe park; and
- safe end.

The requester does not vote on their own request. Eligible voters are the other
connected, non-delegated human seats at proposal creation. Browser presence is
measured from active stream connections; direct/native players use their native
ready/running state. A majority of eligible voters passes. With one other
connected human, that one vote decides. With no other connected humans, the
request is approved immediately. Votes, eligibility, expiry, and outcome are
stored in SQLite and survive a portal restart.

Only one proposal of a kind may be open per match. Native resolution changes
have a five-minute multiplayer cooldown because CSS fitting is always
available instantly. The connected players may separately vote to waive that
cooldown. Maintenance scheduling rotates among matches by last attempt, so a
lobby waiting for a safe native boundary cannot starve approved work for other
games.

Approval is authorization, not proof that mutation is safe. The native
checkpoint gate remains mandatory after a vote passes.

## Stable checkpoints and maintenance

The control plane identifies the current mode and requires three unchanged,
synchronized semantic samples before saving. A browser-human seat must be in
the game, with no root MENU, modal, or native page active. Agent seats must be
at a legal turn/wait boundary with no deferred action. Multiplayer peers must
agree on turn/session state and have no unsettled packet activity.

An approved disruptive operation follows this order:

```text
approved proposal
  -> wait while ordinary play continues
  -> three-sample native quiescence
  -> verified control_recovery save (turn/year recorded)
  -> stop autonomous harness callers
  -> park every managed worker
  -> apply resolution/controller/host change, if any
  -> recover exact seats and factions from the verified save
  -> publish completion and resume browser streams
```

Before the verified checkpoint, the portal shows a non-blocking notice and
players keep playing normally. After the safe boundary is claimed, a full
maintenance curtain explains the current phase. A failed checkpoint does not
tear workers down. The operation returns to waiting when the native state is
temporarily unsafe, or records a bounded error for operator review.

At ordinary turn boundaries the supervisor opportunistically records one
verified recovery checkpoint per turn. Simultaneous-turn activity can defer a
checkpoint; it is retried instead of rolling anyone's unsaved actions back.

## Disconnect, exit, crash, and reclaim

A browser refresh or navigation closes only that stream connection. The native
seat and faction remain reserved. After 30 seconds, other connected humans can
propose temporary stock-AI control. If approved, the system checkpoints,
rehosts, marks the seat delegated, and lets the game's own AI continue that
faction. The returning owner can later request reclaim through the same
checkpoint-first process.

Returning to the native main menu is different from closing the browser. The
supervisor records `returned_to_menu`, treats the native session as lost, and
recovers every managed seat from the latest verified checkpoint. A worker or
game-process crash uses the same durable recovery authority. If no verified
checkpoint exists, the platform stops in operator review rather than inventing
a safe state.

A semantic capability incident is also an explicit operator-review state. The
full report may be dismissed, but a compact paused banner remains on the lobby,
managed game, and spectator views. Restarting the portal or host never treats
dismissal as resolution. After bridge coverage is deployed, **Retry from
verified checkpoint** parks the preserved old session, refreshes the managed
runtime image, restores the save, and acknowledges the capability/no-progress
incidents only after the new native session is healthy. If recovery is
interrupted, startup reconciliation derives running/parked state from the
control plane and retains the unresolved incident.

When every human is a managed browser player and all have been absent for ten
minutes, the platform checkpoints and parks the match. It does not auto-park an
AI-only simulation and does not infer browser presence for external/native
clients.

The lobby exposes this lifecycle as `awaiting_first_connection`,
`temporarily_disconnected`, `idle_grace_period`, or `checkpoint_pending`, with
the remaining countdown where applicable. A started browser match that has
never been opened is not immortal: its ten-minute abandoned-game timer starts
from match creation.
Reconnecting any managed human before maintenance begins cancels the idle
condition. Parking still waits for native stability, so simultaneous turns or
an open modal are not discarded merely because a browser left.

A never-started waiting room has no native workers to park. Its owner can close
it immediately, and inactive waiting rooms expire after 24 hours by default.
An active SignalR lobby connection protects the room; disconnecting refreshes
its activity timestamp so the full idle period begins when the staging room is
actually left.

## Identity collision rules

Public display names are globally case-insensitive. The private sign-in
username may differ. Invitations reserve provisional accounts by display name,
so registering the invited name later claims the same seat and history. A lobby
rejects duplicate invited names and rejects inviting its owner a second time. A
signed-in account cannot claim a seat reserved for another display name. The 14
faction-leader names are reserved for managed agents so a human or anonymous
native participant cannot impersonate one.

The exact public display name is also the normal DirectPlay name. It is bounded
to the game's 31-character limit and collision-checked against every other seat
and managed worker. A deterministic suffix remains a defensive internal
fallback for malformed legacy data; ordinary users never need to understand or
type a transport-only alias.

Direct/native lobby finalization compares the observed DirectPlay participant
set case-insensitively to the reserved display names. The earliest correctly
named participant keeps the seat. A later duplicate, a faction-leader
impersonator, or an unreserved name is rejected rather than silently merged
with somebody else's identity. In an agent-hosted lobby the semantic native
host removes the invalid participant and the portal reports why. In a
human-hosted lobby the mismatch blocks start because the managed process cannot
exercise host authority. An invited player may join natively before creating a
password and claim that provisional account later.

## Spectators and human-only games

A non-participating administrator may cross-seat spectate any managed worker. A
lobby may opt into signed-in non-player spectating; it is disabled by default.
Campaign participation is durable, so leaving a faction never unlocks enemy
views. Every observer ticket is read-only in the stream transport itself, so a
modified client cannot turn a watch URL into game input. Spectators receive CSS
fitting only and can never request native resolution changes or vote as players.

Nothing in managed play requires a model provider. A human-only match uses the
same typed lobby, worker isolation, display profiles, chat, checkpoints,
governance, idle parking, history, and recovery. Hermes/MCP containers are
created only for assigned agent seats.

## Boundaries

- This is a private household/friends portal, not public matchmaking. Remote
  access uses one-time invitations, HTTPS, and one-time installation verification.
- Direct/native players remain subject to legacy DirectPlay reconnect behavior;
  managed browser reconnect is the seamless path.
- Native profile changes replace worker lifetimes; they are intentionally not
  live X11 resizes of a running DirectDraw surface.
- Ranked play and microphone/voice management are not supported.
- The application does not include or distribute the game.
