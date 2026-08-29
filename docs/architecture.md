# Architecture

```text
Authenticated Control Center -- owns match/seat/worker/harness lifecycle
             |
             v
Qwen 3.8 27B / isolated Hermes profile (one durable agent identity)
             |
             | exact MCP Streamable HTTP endpoint
             v
Dedicated MCP sidecar (one running worker/perspective; lifecycle blocked)
             |
             | authenticated JSON lines on a private Docker network
             v
Thinker-derived 32-bit bridge DLL
             |
             | UI-thread message marshalling
             v
Visible/view-only SMACX process under an isolated Proton prefix
```

The model should use MCP rather than connect to the game bridge directly. MCP gives Qwen compact schemas, bounded waits, stable match identity, scoped memory, and a deliberately semantic-only capability boundary. In the legacy single-instance flow MCP also owns launch lifecycle. In the managed flow, lifecycle tools are mechanically blocked and only the authenticated Control Center may start, park, or resume a worker. Keeping the bridge protocol independent makes it possible to add a different harness later without changing the DLL.

For managed LAN, every agent seat has a separate game/MCP pair but shares one
durable `match_id`. Seat zero hosts the real DirectPlay session and joining
workers use the host container's exact private IPv4 address plus the freshly
enumerated network-session GUID. The Control Center reads each worker's bridge
token directly from its purpose-scoped vault entry and may invoke only named
native semantic operations; it never exposes those tokens or a generic bridge
proxy through HTTP. Each process keeps a unique `session_id` and perspective,
so simultaneous chat, memory, and optimistic-concurrency guards cannot cross
seats.

External human seats deliberately have no agent identity, perspective, worker,
or MCP endpoint. They are durable lobby assignments keyed by an operator-chosen
native player name and, after first start, the observed faction. A mixed match
uses an explicitly configured macvlan/ipvlan network so every worker has a real
LAN address. The manager stages the lobby, then validates exact names,
readiness, participant count, and saved-faction reclamation before it permits
the semantic host Start action.

Native actions that must cross the Windows event loop are tracked as small transactions. The bridge assigns an `action_id`, records the intended objects, and publishes pending/completed/rejected status plus the native result and observed match-local tile IDs. The MCP waits for completion within a bound, preventing a model from confusing “message queued” with “game action applied.” Native map coordinates remain internal to the DLL.

The Planetary Council is a special nested native event loop. The bridge opens the engine's own proposal list, takes one compound semantic proposal-plus-ballot decision, and schedules the native Council vote/close handlers with a Windows timer on the same UI thread. The normal Council chamber remains visible; no synthetic input or cross-thread engine access is used.

The Unit Workshop does not open its coordinate-oriented window. The bridge derives fair component catalogs from the human faction's acquired technologies, applies role and ability compatibility checks, and then calls the engine's native prototype routines on the UI thread. Prototype creation is part of the semantic revision, so any prior guard becomes stale immediately. Retirement and bulk upgrade additionally validate ownership, live unit/queue references, affordability, and explicit confirmation.

## Why Thinker, not OpenSMACX

OpenSMACX is an old, incomplete engine-reimplementation scaffold and does not provide playable Alpha Centauri state or control. GLSMAC is active but has not reached full gameplay. Thinker is mature, MIT-licensed, injects into the original `terranx.exe`, and already contains maintained reverse-engineered engine structures and hooks. That lets this project preserve the real visible game while exposing a narrow control surface.

New native adapters are exposed only after their state and effect path are identified. For example, the Alien Crossfire Council-window path at `0x428110` handles `VOTEFORMETECH` by transferring the two publicly named technology IDs stored at `0x93F800` and `0x93F80C`, then casting the player's ballot; the path at `0x428285` handles `VOTEFORME` by transferring the parsed energy quote before the same ballot call. The semantic adapter reads only the terms named by that active dialog, requires explicit commitment confirmation, and lets the original suspended Council code apply the effect after the popup closes.

## Thread safety

The socket worker never reads or mutates engine memory. It accepts one bounded request, places it in a synchronized slot, and posts a private Windows message. `ModWinProc` executes ordinary requests on the game's UI thread and signals the worker with the response. Stock paired-diplomacy and DirectPlay waits can consume or starve that window message, so audited game-thread message and native wait boundaries service the same serialized slot. A re-entry latch prevents the active request from calling itself, while modal-depth tracking prevents a hook-serviced read from recursively entering DirectPlay's packet pump. Timeout diagnostics report which dispatch boundaries ran. This preserves single-threaded access to the 1999 engine's global data structures even inside its private loops.

## Fair-play boundary

- The perspective is always `CurrentPlayerFaction`; callers cannot request another faction.
- Bases are returned only when owned by that faction.
- Own units include full actionable fields. Foreign units appear only when the engine marks them currently visible and omit private order/home-base fields.
- Tiles must be known. Currently visible tiles include current terrain/owner; fogged tiles include only remembered feature bits and omit current terrain/owner.
- A transient engine pointer to a hidden AI unit is filtered before it becomes `current_vehicle_id`.
- Unit/base semantic actions validate ownership and wait for the human, non-modal turn.
- Every mutation uses optimistic concurrency over `match_id`, per-process `session_id`, and fair action-relevant `revision`.
- Durable strategic knowledge uses the same identity boundary: writes require the active match/session/revision, record turn/year provenance, retain correction history, and cannot name arbitrary filesystem paths. Reads from another match are rejected while play is active.
- A reported capability gap creates an MCP-side development latch whose audit identity is keyed by `(match_id, session_id)`. The agent can continue read-only diagnosis or stop the isolated game, but commands, launch, new-game, and load operations are refused for the remainder of that MCP process. There is intentionally no agent-accessible reset; after the bridge is extended and tested, the developer restarts MCP and begins a fresh native session.
- The authenticated native bridge itself rejects the former raw `act` operation. This defense is below MCP discovery: direct socket clients also cannot send mouse, keyboard, text-entry, chat keystrokes, or coordinate menu actions.
- The bridge does not expose scenario-editor omniscience or arbitrary memory reads.

## Security boundary

The in-process game bridge binds only to loopback and requires a 256-bit token.
Managed workers expose its authenticated proxy only to their selected container
network; MCP and spectator host ports default to loopback. Requests are capped
at 16 KiB and controller responses at 4 MB. External DirectPlay publication is
an explicit macvlan/ipvlan operator choice, never an implicit port-forward.
