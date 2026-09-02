# Architecture

SMACX Agent is a private household/friends application around the real Alien Crossfire executable,
not a replacement game engine. It separates human presentation, operator
authority, AI reasoning, durable knowledge, and native mutation so each can be
tested and secured independently.

## Component map

```text
trusted LAN or invited HTTPS browser
       |
       v
Caddy edge (HTTP LAN; automatic TLS for configured invited-friends hostname)
       |
       | authenticated portal/API and authorized stream proxy
       v
.NET 10 Blazor portal + controllers + SignalR ----> portal SQLite
       |                                                accounts/access/
       | purpose service token                         lobbies/projections
       v
Python control API --------------------------------> control SQLite
       |                                                identities/lifecycle/
       | Docker lifecycle                              operations/projections
       |
       +-------------------------------------------> campaign journals + Git
       v
one isolated seat
       |
       +--> Proton + terranx.exe + semantic bridge <--> exact-seat MCP
       |                |
       |                +--> Selkies video/audio/input (portal proxied)
       |                     + secure/loopback H.264 + audio
       |                     + plain-LAN HTTP JPEG compatibility view
       |
       +--> isolated SMACX-derived Hermes <--> OpenAI-compatible provider

portal/MCP ----> private .NET knowledge service ----> private acquired corpus
                       |                                + shared embeddings
                       |
campaign journal -> SQLite cursor -> optional Graphiti projector -> private FalkorDB
```

Optional Graphiti/FalkorDB reads committed authoritative events through a cursor
and writes only a derived temporal graph. It is never on the gameplay authority
path.

## Why the original executable

OpenSMACX is an incomplete historical engine-reimplementation scaffold and does
not supply playable Alpha Centauri state/control. The project instead extends a
pinned Thinker-derived DLL inside `terranx.exe`. Thinker provides maintained
reverse-engineered structures and hooks while the stock engine continues to
render, resolve rules, load scenarios, run DirectPlay, and write native saves.

The bridge exposes only reviewed semantic adapters. Unknown or unsupported
mandatory UI states latch a capability gap. The MCP has no generic screenshot,
mouse, keyboard, window, text-entry, arbitrary memory-scope, or raw bridge
proxy escape hatch.

## Authority split

### Portal authority

The Caddy edge is the only ordinary browser entry point. It serves trusted-LAN
HTTP and invitation-gated Internet HTTPS, then proxies to the portal. The portal
is the only writer of its canonical pre-release SQLite schema:

- ASP.NET Core Identity users/roles/password-reset grants;
- trusted-network classification behind the private Caddy proxy, single-use
  registration invitations, account activation, and content-free installation fingerprints;
- case-insensitive public display names, provisional invited identities, and
  collision-safe DirectPlay participant binding;
- lobby drafts, browser membership, UI policy, stream presence/tickets;
- stable, uniquely named provider-facing AI profiles with reversible deactivation; and
- public history/analytics projections, including evidence-backed per-seat
  native outcomes.

Client-side Blazor state is not an authorization boundary. Controllers recheck
the principal, role, match ownership/membership, seat, lobby policy, and stream
mode. SignalR carries notifications/presence, not game video.

Remote registration requires a single-use expiring invitation. A first remote
sign-in requires a content-free, browser-local installation fingerprint. These
Internet gates do not apply to ordinary trusted-LAN registration unless an
administrator explicitly enables the equivalent LAN policies. See [Network
access and play modes](network-access.md).

### Control authority

The private Python service is the only writer of the control SQLite schema and
canonical campaign journal tree. SQLite owns transactional platform state:

- installation, game source/runtime fingerprints, vault secrets;
- native match, seat, agent, perspective, process-session, and instance IDs;
- worker/MCP/Hermes lifecycle and Docker ownership labels;
- verified saves, recovery, schedules, operation runs, and backups;
- Graphiti/query projection cursors and rebuildable FTS compatibility indexes; and
- original/private mechanics search indexes.

The per-perspective hash-linked journal owns gameplay actions, chat, facts,
beliefs, relationships, commitments, goals, summaries, notebook records,
incidents, lifecycle, and checkpoint references. Match-local Git commits mark
coherent boundaries. Raw model thought, transcripts, native saves, and game
assets remain outside that repository.

The portal authenticates with a purpose file credential mounted read-only. The
control API is not host-published. Neither service opens or writes the other's
database.

### Native authority

The DLL on the real game UI thread decides what the current faction can observe
and whether a command is legal. The control plane may request named operations,
but it cannot forge a bridge revision, faction perspective, native participant,
or successful save.

## Identity and fair play

Every action is bound to:

```text
installation_id
  match_id
    seat_index
      agent_id (agent seats only)
      perspective_id (agent seats only)
      instance_id (managed process)
        session_id (fresh per native process)
        revision (fresh per observed game state)
```

Agent memory scope is exactly `match_id + agent_id + perspective_id`. Another
agent in the same match, the same model in another match, and a recovered native
process cannot reuse that scope accidentally. Match-local tile IDs are opaque
identifiers; coordinates remain inside the bridge.

Native state is filtered for the seat's faction. Unit/base ownership, map
visibility, contacts, council state, diplomacy, chat participants, and legal
choices are derived under that perspective. MCP independently checks the same
scope and stale revision before forwarding mutation.

## Native thread model

The socket worker never reads or mutates engine globals. It accepts one bounded
request into a synchronized slot and posts a private Windows message. The
Thinker-derived `ModWinProc` executes on the game's UI thread and returns the
result. Audited DirectPlay/diplomacy waits service the same serialized slot,
with re-entry and modal-depth guards preventing recursion.

Long-running native effects use small transactions. The bridge records an
`action_id`, intended objects, pending/completed/rejected state, and native
result. MCP waits for bounded completion so the model cannot confuse “queued”
with “applied.”

Nested systems such as the Planetary Council, paired diplomacy, Unit Workshop,
scenario setup, and DirectPlay lobby have purpose adapters that call original
engine handlers on the UI thread. They are not coordinate macros.

## Agent decision protocol

The preferred cycle is:

```text
smac_decision
  -> review one returned opaque legal choice
  -> smac_execute_choice(decision_id, choice_id)
  -> discard the frame
  -> observe again
```

Decision frames merge the current actionable surface: required interactions,
incoming diplomacy/council items, ready units, bases needing choices, research,
social engineering, unit design, strategic orders, chat, and end-turn guards.
Mutations invalidate earlier frames.

The server retains native commands, confirmation flags, and revision guards.
Consequential context is visible, but the model cannot fabricate those private
fields. One semantic rebase absorbs meaningless revision churn; a real conflict
requires a fresh decision. Repeating one semantically identical choice three
times against the same meaningful state opens a capability circuit before a
fourth native mutation. End turn is rejected while mandatory work or a pending
native action remains.

## Hermes integration and prompt layering

Hermes is the supported long-running agent harness, but it is managed as an
isolated runtime rather than a prerequisite desktop dashboard. Each agent
profile has:

- official digest-pinned image;
- private durable data volume and separate provider-secret volume;
- match-specific workspace and continued conversation key;
- exact MCP endpoint for its worker/perspective;
- the `smacx` toolset only; and
- independent error-restart budget plus clean-continuation/turn/run policy
  owned by control.

SMACX owns the complete provider-facing system message. A derived image uses an
audited startup hook over the digest-pinned official Hermes runtime and replaces
Hermes prompt assembly rather than adding another layer. The hook verifies the
stored prompt SHA-256 and fails closed when the file is missing or changed.
Captured-provider tests prove that the request contains exactly one system
message: the versioned fair-play contract, immutable match/seat/policy identity,
mandatory live-settings briefing protocol, and the match's resolved authored
personality card appended last. Hermes still supplies conversation continuity,
compression, provider transport, and MCP execution, but contributes no system
scaffold or workspace instructions.

Managed Qwen thinking profiles explicitly disable unlimited historical
`preserve_thinking`. The derived harness retains all interleaved reasoning after
the current episode's latest user boundary, so `think -> tool -> result ->
think` remains coherent. Before the next episode request it removes completed
reasoning fields and serialized think blocks. It retains durable ordinary
assistant output such as `TURN HANDOFF`, but omits completed tool-call/result
protocol pairs from the provider projection; those remain available in Hermes
SQLite and the campaign journal. Within the active episode it keeps every tool
pair while replacing superseded state payloads with compact markers and keeping
the newest state frame intact. The parser understands Hermes's real generic
`tool_call` dispatcher envelope as well as direct namespaced calls. A streaming
repetition fuse turns a large degenerate generation into Hermes's ordinary
recoverable repetition error.

A successful native turn end asks the model for one bounded `TURN HANDOFF`
assistant message: outcome, concise rationale, changed conclusions, next-turn
intent, and uncertainty. The message remains in Hermes history and is eligible
for normal compression; it is not raw scratch reasoning. The supervisor treats
that clean exit as a campaign yield, preserves the conversation, and does not
consume the error-restart budget. It compares a volatility-filtered semantic
fingerprint plus turn/year/phase/outcome markers across invocations. Three clean
yields without meaningful progress stop the run and raise an operator-required
incident. Actual nonzero exits retain their separate bounded restart budget.

Capability-gap reports take priority over both continuation and error restart.
The MCP sidecar appends one match/session-scoped report to the persistent control
volume. The operations supervisor ingests it before harness reconciliation,
records an operator-required incident, stops that seat's harness, preserves the
native worker, and publishes a redacted diagnostic archive. Portal polling and
the lobby SignalR channel surface the same durable incident to connected humans;
the browser dialog is therefore recoverable after a refresh or portal restart.
Dismissing the modal stores presentation preference only: lobby, managed-play,
and spectator views retain a compact **AI paused** banner. Explicit recovery
keeps the incident active while it checkpoints/parks the preserved worker,
refreshes its prepared runtime image, restores the verified save in a fresh
native/MCP session, and then recovers only the capability incident plus the
derived clean-yield incident. An unrelated operational incident is never
cleared by that action.

Chat messages and web content remain untrusted game information. They do not
become operator/system instructions. Lifecycle, Docker, backups, provider
secrets, stream tickets, and recovery are never agent tools.

## Human seats and streaming

Managed browser humans receive the same isolated Proton worker model without
MCP/Hermes authority. Selkies encodes the real X11 game display and audio,
accepts ordinary input for interactive tickets, and supports reconnect and
fullscreen. The portal reverse-proxies HTTP/WebSocket transport and rewrites
the WebSocket Origin expected by Selkies.

The worker chooses one configured native framebuffer for its lifetime and writes
the same dimensions into Xvfb, Selkies manual mode, and Thinker's custom window
configuration. Browser CSS performs ordinary resize/orientation fitting without
mutating that framebuffer. A native-profile change is a checkpointed worker
replacement, never an in-place DirectDraw resize.

Authorization happens before proxying:

- member controls their exact browser seat only while its per-tab lease is the
  active generation;
- administrator may control their own seat and, only when not a campaign participant, observe any seat;
- lobby opt-in permits signed-in non-participants to request spectator access
  in human games, while running AI-only simulations always permit it;
- participant identity is retained durably so leaving cannot unlock enemy views;
- observer mode is enforced read-only at transport; and
- direct worker credentials/passwords are not exposed.

Leases are process-local, expire after 30 seconds, and are bound to user plus
worker. A takeover cancels the former controller's in-flight proxy connection,
then reconnects that page with view-only credentials. The lease identifier in
the iframe URL is not sufficient without the authenticated seat owner.

An owner-only `human_ui_state` read crosses the same private service boundary.
The bridge exposes only native root-MENU visibility, submenu/modal state,
display metadata, and the exact quit-confirmation label, and only when the
worker controller kind is human. The portal uses it to show the managed control
rail and cancel `REALLYQUIT` through the native no-choice. No equivalent
operation exists in the agent MCP toolset.

`managed clients only` rejects external/native clients for a lobby. Otherwise,
native human seats have no agent/perspective/MCP identity and join the real
DirectPlay session using an assigned handle/faction.

## LAN lifecycle and governance

An agent-hosted match gives seat zero a game worker and native Host/Start
authority. A managed browser human host uses the same path without an agent.
Advanced external-human hosting gives seat zero no worker; managed clients
discover an explicitly selected IP/session, ready, and wait for the human's
native Start.

Lobby staging validates names, readiness, participant count, and saved faction
reclamation. Exact external join details are copied to the portal through the
private service call.

Parking is an ordered transaction:

```text
portal status=parking
  -> stop active Hermes runs
  -> native verified checkpoint
  -> stop/remove MCP and worker containers
  -> authoritative match=parked
  -> portal match=parked
```

The `parking` claim suppresses the supervisor's normal “ensure agent is
running” behavior. If the native save is currently illegal, the transaction
fails before worker teardown, records `park_failed`, and returns to running.

Compatibility storage is layered rather than cloned per seat. The first seat
for a game/source fingerprint creates one installation-local prepared Docker
image containing the imported game, semantic bridge, GE-Proton prefix, and
DirectPlay registration. Docker shares those immutable layers across every
seat. Per-seat containers supply isolated copy-on-write runtime state, while a
small named volume retains native saves. Parking prunes and zstd-compresses that
volume; completion moves one final verified save into the persistent control
archive before releasing the seat volume. See [Runtime and campaign
storage](storage-lifecycle.md).
The control API independently stops harness callers for direct API park users.

Recovery always assigns fresh native session/revision identities, loads the
verified save, reclaims exact factions, restores MCP sidecars, and continues
the durable Hermes conversation. A model must re-observe.

Portal governance rows are durable authorization records. Quorum is frozen from
the other connected, non-delegated human seats when a proposal opens. Approval
does not mutate native state directly: a separate maintenance coordinator must
still pass the control plane's three-sample quiescence and verified-save gates.
Queued work rotates by last attempt so one match waiting on a modal or active
simultaneous-turn packet cannot starve other lobbies.

## Chat and memory

Native and portal chat are normalized into durable match events with sender
handle, sender faction, recipient faction (`0` means broadcast), sequence,
channel, conversation, logical message ID, and deduplication marker. Consent
groups fan one logical message out as native private deliveries; portal/agent
consumers ingest the logical event once. This supports public/private/group
diplomacy and correct identity even when messages arrive outside the agent's
active turn.

The authoritative campaign journal separates:

- immutable observed events;
- facts with provenance/status;
- beliefs/suspicions and confidence;
- relationship dimensions such as trust/affinity/threat/respect;
- commitments/debts and deadlines;
- active/completed/abandoned goals; and
- bounded summaries/compression records;
- agent-authored notebook collections; and
- action, checkpoint, incident, and native-session history.

Journal replay produces a bounded current-state capsule. Each section has a
token budget and newest/highest-priority selection; overflow shortens only the
provider-facing projection and raises a compaction signal without deleting raw
history. SQLite/FTS retrieval remains perspective-scoped and rebuildable.

## Knowledge system

The separate .NET knowledge service acquires an explicit set of rules sources
at runtime, cleans them to heading/body Markdown, merges parallel native
records, organizes them into recursive collections, and synchronizes leaf
snapshots through SemanticKnowledge.NET. Installed Alien Crossfire
mechanics files come from the read-only game mount; canonical web pages fall
back to fixed Internet Archive captures. Acquired text and vectors remain in a
private persistent volume and never enter source, images, or release artifacts.

SQLite FTS5/BM25 and semantic stages use reciprocal-rank fusion for human and
metadata-oriented agent discovery. The portal renders a selected Markdown
document server-side with raw HTML disabled and sanitizes the result before the
WebAssembly client displays it. One reusable reader supplies both the normal
Datalinks page and the compact in-game Wiki tab.

One configurable embedding space serves both SemanticKnowledge and Graphiti.
The default registers one ONNX model instance. SemanticKnowledge retains its
multi-chunk vectors; an internal OpenAI-compatible facade combines chunks into
one vector only for Graphiti. External embeddings are an advanced alternative,
and changing their stable space ID triggers revalidation/rebuild.

Embedding work emits content-free, purpose-aware hourly aggregates into a
separate knowledge-volume SQLite database. Graphiti tags internal facade calls
as projection or recall; the corpus records initial build, changed-document
refresh, and search independently. A startup quality canary checks numerical
and semantic behavior. Telemetry contains neither text nor vector payloads and
is fail-open, preserving the knowledge service as an optional dependency.

## Graphiti

Graphiti is a derived temporal projection:

```text
curated canonical journal events
  -> SQLite projector watermark
  -> projector validates installation/match/agent/perspective/timeline
  -> Graphiti episodes
  -> isolated FalkorDB graph
```

Projection includes only durable chat/political/relationship/commitment/goal/
belief/summary history; routine moves and raw reasoning are skipped. Projection
may lag, be rebuilt, or be deleted. Relevant recall is bounded and fail-open.
Gameplay and journal/SQLite retrieval continue. Graphiti cannot create
canonical facts or broaden a perspective.

## Analytics

The native bridge mirrors only public progress (turn/year and own faction) into
control state. Portal supervisor polling is throttled to avoid competing with
the serialized agent bridge slot.

Hermes remains authoritative for provider usage. On an observed new turn, a
short-lived helper with no network and a read-only mount queries the exact
profile's `sessions` table. The portal stores nonnegative deltas for input,
output, cache-read, cache-write, reasoning tokens, and API calls under the
match/agent/profile/turn. This avoids estimates and keeps the Hermes
filesystem private.

Reports use the portal projection. The administrator SQL lab populates a new
in-memory database with a strict allowlist of report tables; it never attaches
the real Identity or secret/control databases.

Reproducible autonomous-play reports add two content-free views. The campaign
report verifies journal chains and counts causal before/after native progress;
the Hermes audit reports only token/API totals, tool names, safe error-code
labels, malformed records, exact repetition runs, compression health, and
handoff counts. Neither includes prompts, responses, chat, reasoning text, tool
arguments, provider endpoints, saves, or game assets. A benchmark is invalid
unless the native multiplayer turn clock is **None**.

## Docker and secret boundaries

Every dynamic container/volume/network carries:

- `io.smacx.managed=true`;
- exact installation label; and
- purpose label plus relevant match/agent/run identity.

The minimal Docker client mutates only resources whose labels match. Containers
drop all capabilities, use `no-new-privileges`, read-only roots where possible,
bounded tmpfs, and non-root users. Helpers run without network. Provider keys,
bridge tokens, and stream passwords live in separate purpose volumes/files and
are absent from inspect-visible environment values.

## Operations and concurrency

SQLite claims operation schedules transactionally and retains immutable run
outcomes. Reconciliation may repair MCP/harness sidecars. Native recovery
requires a recorded verified save. Backups use SQLite online backup and
no-network volume helpers; running game/Hermes containers are briefly paused
under the same exclusive operations lock so a consistency freeze is not
classified as a crash.

Multiple matches and agents may run concurrently because each has isolated
workers, displays, streams, MCP endpoints, sessions, volumes, and memory scope.
Capacity—not screen focus—is the limit. Build concurrency is separately forced
to one to avoid native/Blazor optimization exhausting home-lab memory.

## Deliberate limits

- The managed game host is Linux-first and expects Docker plus Wine/Proton.
- Browser players may connect from other operating systems; traditional native
  clients remain responsible for their own game compatibility and DirectPlay
  networking.
- Remote access is for a private invited-friends host, not public matchmaking.
- All matches are unranked.
- The authored personality library is deliberately finite and selected per AI
  seat; adding arbitrary user-authored cards is outside the current UI.
- Unknown game states remain fail-closed instead of invoking pixels.

See [ADR 0001](adr/0001-identities-and-authoritative-memory.md), [ADR
0002](adr/0002-control-plane-and-runtime-boundary.md), and [ADR
0003](adr/0003-lan-browser-platform.md) for the accepted design decisions.
