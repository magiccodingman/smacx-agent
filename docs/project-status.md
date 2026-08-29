# Project status

This document distinguishes four words that matter in a project spanning a
native game, container orchestration, model harnesses, and optional memory
services:

- **Implemented** — production code and its local contract exist.
- **Contained-tested** — behavior is verified without requiring the real game
  or an external service.
- **Live-tested** — the real Alien Crossfire executable, Proton, Docker, or
  harness completed the described workflow.
- **Certified** — the workflow has been tested in the deployment environment
  being claimed. Linux-local success is not Windows, Wi-Fi, or arbitrary-LAN
  certification.

## Delivered and live-tested

### Semantic game control

The Thinker-derived bridge exposes fair-play observations, legal choices, and
guarded game-thread mutations from the real `terranx.exe`. It covers the core
strategic loop plus broad production, research, Social Engineering, base,
unit, diplomacy, Council, Workshop, save/load, and endgame interactions.

Long semantic soaks have reached 100 turns without screenshots or UI input.
Unsupported mandatory states stop and emit a structured capability gap instead
of granting the model a coordinate-click escape hatch.

### Durable identity, chat, and memory

SQLite owns installation, match, agent, perspective, instance, and process
session identity. It stores immutable events and chat plus versioned facts,
beliefs, relationships, commitments, goals, and summaries. Recall is scoped to
the exact match/agent/perspective and uses local FTS5/BM25 search.

This is the delivered memory path. It does not depend on Graphiti and continues
to work when every optional external service is absent.

### Control Center and Linux workers

The authenticated Control Center validates a user-supplied legal game source,
imports a private checksummed Proton runtime, discovers OpenAI-compatible
models, creates durable agents/matches, provisions isolated workers and private
MCP sidecars, parks/resumes workers, and prepares isolated Hermes sessions.

The live worker regression validates read-only game/Proton mounts, file-based
bridge secrets, a read-only container root, semantic opening, process-session
rotation, durable match identity, and exact owned-resource cleanup.

### Managed agent LAN

Two real contained game workers have completed native DirectPlay
host/discover/join/configure/ready/start, entered one shared match through
distinct factions and process sessions, saved only from the actual native
host, parked completely, reopened the stock **Load Multiplayer Game** lobby,
rejoined, restored exact faction-to-seat bindings, and returned to gameplay.

A separate three-process live regression has now exercised the AI-hosted mixed
path with two managed agents and one independent native client. The external
client joined by exact DirectPlay session identity, appeared under its assigned
name and faction in chat, disconnected after a host-only native checkpoint,
rejoined the stock loaded lobby, reclaimed the saved faction, and exchanged
chat after resume. This was a production-equivalent local network test with no
pixels or UI input; it is not a claim that a physical second computer has been
certified yet.

The inverse path is also live-tested: a named external human owns seat zero,
the native lobby, Start, Save, and Load, while two managed agents discover the
exact session, join, ready, exchange faction-attributed chat, park, reclaim
their loaded factions, and continue. Control Center never sends the native
Start command on this path.

### View-only spectators

Per-worker noVNC is optional, password-protected, published to loopback by
default, and starts `x11vnc` in enforced view-only mode. The live test verifies
the socket and confirms the password is not present in container configuration.
Agents and MCP tools never receive spectator access.

## Implemented and contained-tested

### Named external human seats

Mixed matches support either an agent or an exact named human as native host.
Human seats receive no agent identity, perspective, worker, or MCP endpoint.
For an AI host, the first Start stages the lobby and the second validates exact
names, participant count, readiness, and saved-faction reclamation. For a human
host, managed clients discover/select one exact session, join and ready, then
wait for the human's native Start.

The manager refuses an arbitrary Docker bridge. It requires either an
operator-created non-internal macvlan/ipvlan network or the exact labeled,
firewalled routed-player bridge. Identity, readiness,
faction, and network-driver guards are contained-tested, and the entire native
lifecycle is locally live-tested with an independent third process. A physical
second machine has not yet certified this path.

### Managed Hermes harness

Worker, MCP, identity, and memory have explicit component boundaries inside the
managed Hermes architecture. The host integration creates one isolated Hermes
profile per durable agent, uses the exact provider/model and MCP binding
resolved by the Control Center, and defaults Qwen/Hermes reasoning to low.
Hermes is the supported permanent harness for this project. Control Center owns
the official digest-pinned Hermes container, preserves its per-match
conversation volume, stops/resumes it, and restarts bounded exits. Protocol
separation keeps that integration secure and testable.

## Optional Graphiti: delivered, isolated, and default-off

The optional Compose profile now provides a digest-pinned Neo4j service and a
`graphiti-core` projector. It derives each namespace from the installation,
match, agent, and perspective; advances deterministic event cursors only after
successful projection; retries failures; and supports one exact-perspective
rebuild through the authenticated Control Center. Graph/model/embedding
credentials use Docker file secrets and neither service publishes a host port.

The real pinned Neo4j stack reached healthy state. A deliberate projection to
unavailable model endpoints degraded only the projector while the authoritative
SQLite event remained intact. The current Qwen endpoint does not implement
`/embeddings`, so this installation correctly keeps Graphiti disabled. This is
deployment and failure-isolation evidence, not evidence that graph recall
improves play; SQLite FTS5/BM25 remains the production default.

## Deployment paths implemented but awaiting external certification

The project includes an encrypted Tailscale subnet router, persistent auth
state, explicit-IP DirectPlay joining, and a firewall that admits only TCP
47624 plus TCP/UDP 2300–2400 into a dedicated player network. A local live
route test passed real TCP and UDP traffic across two isolated subnets.

The Windows 11 path is WSL2 plus Linux Docker and the same Proton worker. Its
preflight checks WSL2, x86-64, Docker/Compose, `/dev/net/tun`, legal game and
DirectX paths, and an actual read-only Docker bind. The Linux reference host
passed that preflight. No Windows host or physical second computer exists in
the development environment, so neither physical two-machine LAN nor Windows
11/WSL2 is labeled certified.

## Profiles and capability accounting

Five guarded random-map profiles—Citizen/Tiny, Citizen/Small,
Librarian/Standard, Thinker/Large, and Transcend/Huge—passed a fresh
two-process native DirectPlay configuration matrix with synchronized host and
client state and no visual input. The MCP and authenticated Control Center API
expose the reviewed capability ledger, including external-certification
boundaries and exact fail-closed gaps.

## Not yet delivered

- First-class personality-card editing and behavioral evaluation. Durable
  agents have a `personality_ref`, and the Hermes system prompt preserves
  player autonomy, but the complete personality workflow remains future work.
- Physical two-machine human-LAN and Windows 11/WSL2 certification. The
  implementation and runnable checklists exist, but those external environments
  are required to produce honest evidence.
- Typed single-player and multiplayer scenario selection/launch. The legal
  Steam source contains scenario files, but the isolated native test import did
  not include them and the bridge exposes no scenario-selector contract; it
  never falls back to menu clicking.
- Every rare scenario interaction or LAN synchronization path. See
  [Coverage and limits](coverage.md); missing mandatory interactions fail
  closed.

## Remaining release gates

The implementation milestone is complete only up to the evidence boundary
above. Release certification still requires a physical mixed game across two
machines, the same matrix on Windows 11/WSL2, and supplied scenario content for
typed scenario-launch development and tests. Rare consequential LAN mutations
remain withheld until their two-client effects converge in native tests.

Personality-card semantics are intentionally outside this milestone. The
opaque attachment seam remains reserved, but card format, editing, prompting,
and behavioral evaluation will be designed separately.
