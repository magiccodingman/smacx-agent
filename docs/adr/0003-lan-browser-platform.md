# ADR 0003: LAN browser platform and split control authority

- Status: Accepted
- Date: 2026-08-29

## Context

The semantic bridge, durable memory, managed workers, and Hermes integration
already allow fair-play AI seats to run the real game. The next product surface
must also let ordinary people create lobbies, play managed seats from a web
browser, reconnect, watch permitted perspectives, and inspect match history
without learning the lower-level MCP and Docker interfaces.

The application is intended for a private household or invited-friends host. It
is not a binary distributor, public matchmaking network, or competitive
anti-cheat platform. All matches are unranked.

## Decision

The browser-facing Control Center is a .NET 10 Blazor Web App using MudBlazor. It enables
Interactive Auto, Interactive WebAssembly, and server interactivity, with most
authenticated application components compiled into the client project.
ASP.NET Core controllers own the browser-facing HTTP API and SignalR hubs own
presence, lobby, chat, and runtime notifications. Game video and input bypass
SignalR and use a dedicated authenticated streaming transport.

ASP.NET Core Identity owns portal users, roles, password resets, and browser
sessions in a portal-only SQLite database. The Python control service remains
the only authority and writer for installations, native matches, seats,
workers, saves, memory, harness runs, operations, and game telemetry. The two
services never write one another's database. The portal calls a versioned,
private control API using a purpose-scoped service credential and supplies the
authenticated portal principal for immutable audit attribution.

The Python service is a private control API and advanced host CLI. Its legacy
HTML is not published by the ordinary deployment; the Blazor portal is the
supported operator experience.

Managed human seats run the same isolated Proton and native game worker model
as managed AI seats. Selkies is the primary interactive browser transport,
using its single-port WebSocket mode first; noVNC remains a view-only fallback.
The streaming backend is never exposed directly. The portal issues short-lived,
seat-scoped tickets after checking ownership or spectator policy.

Only non-participating administrators may view every managed seat. A lobby may
opt into authenticated non-player spectating; the default is disabled. Anyone
who has controlled a faction in the campaign remains excluded from enemy views.
Authorized viewers receive read-only, revocable access and may switch among
permitted seat streams. A separate
`managed clients only` option disallows external native clients when operators
want one reproducible runtime for every human seat.

All matches use classification `unranked`. The durable match record retains its
classification and policy snapshot, and the current API rejects `ranked`.

AI profiles are stable, uniquely named identities that may be edited in place,
deactivated, and later reactivated without severing their analytics history.
Operators who need separate experimental cohorts create separately named
profiles. Hermes retains its
tool-critical system layer. The application injects a stable SMACX player
contract, immutable match context, an optional personality block, and scoped
memory in that order. Personality selection belongs to each AI seat; `None`
remains available when no personality should be injected.

Graphiti remains an optional, derived projection. Match configuration may use
the server default, enable it, or disable it. SQLite remains authoritative and
fully functional when Graphiti is unavailable.

Because the repository is unreleased, both databases are defined by one
canonical initial schema. No numbered historical migration chain is created.
Migration history begins only after a public schema is released.

## Security and distribution posture

The Caddy edge is the only ordinary browser entry point and proxies only to the
portal. The Python service,
Docker authority, MCP endpoints, provider secrets, bridge tokens, and stream
credentials stay on private networks or purpose volumes. Client-side Blazor
authorization is never treated as enforcement. Authenticated spectator tickets
are short-lived and read-only. Human games revoke them when the lobby disables
spectators; running AI-only simulations remain observable to authenticated
nonparticipants so unattended workers cannot become opaque.

The project documentation contains the following complete game-assets notice:

> This application does not include or distribute Sid Meier's Alpha Centauri,
> Alien Crossfire, or other proprietary game assets. Users provide their own
> installation.

The software does not enforce mod parity. For remote accounts, the browser
locally hashes a small selection of user-chosen installation files and sends
only a compact fingerprint for a good-faith ownership check; no executable,
DLL, or game asset is uploaded. Managed browser clients use the host's
installation.

## Consequences

The portal can evolve independently without weakening the semantic fair-play
boundary. Browser users gain a modern experience while native clients and MCP
automation remain possible. The split introduces an internal authenticated API
and two databases, but each has a single writer and a narrow responsibility.
Public matchmaking and competitive ratings are not part of this architecture.
