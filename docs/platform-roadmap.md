# Platform roadmap

The merged semantic bridge is the protected gameplay baseline. Platform work
is delivered as vertical, independently testable milestones.

1. **Durable foundation** — installation/match/agent/perspective/instance/session
   identities, one canonical pre-release SQLite schema, immutable events,
   structured memory, chat history, and scoped full-text retrieval.
2. **Runtime integration** — controller manifests and player knowledge use the
   canonical scoped SQLite store. A private runtime-built SemanticKnowledge
   corpus supplies organized mechanics retrieval from the operator's game and
   explicit canonical/Wayback sources without shipping acquired prose or
   vectors. The retired in-database reference index and extractor are gone.
3. **Memory projection** — optional Graphiti/FalkorDB uses derived namespaces,
   replay cursors, failure isolation, adversarial cross-scope leakage tests,
   curated asynchronous political episodes, deterministic bounded recall, and
   an independently selected extraction profile. It shares one configurable
   embedding runtime with SemanticKnowledge and remains fail-open to the
   authoritative scoped SQLite memory.
4. **Linux game worker** — user-supplied game import, private Wine/Proton
   prefix, semantic bridge, virtual display, health protocol, and Selkies
   interactive/read-only browser stream.
5. **Control plane and portal** — authenticated Blazor Control Center, provider
   discovery, versioned AI profiles, typed match builder, secrets, isolated
   per-worker MCP, browser human seats, observer deck, and managed Hermes
   profile/session management. The durable
   operator and host-Hermes vertical slices, durable schedules, verified
   backups, MCP repair, checkpoint-gated native crash recovery, digest-pinned
   managed Hermes processes, durable continuation, heartbeat/restart ownership,
   and purpose-volume provider secret injection are implemented.
6. **Managed multiplayer** — one isolated worker per seat, managed hosting,
   exact-address joining, persistent chat attention, save/park/rejoin, and
   interactive human seats. Multi-agent host/join/lobby/start, human-owned
   lobby discovery/join/readiness, native checkpoint save/load with exact
   faction restoration, match-wide park, five guarded random-map profiles, and
   opt-in macvlan/ipvlan human-seat publication are implemented and locally
   native-tested.
7. **Remote transport** — a persistent, firewalled Tailscale subnet-router
   profile supports physical player LANs and dedicated routed Docker bridges.
   DirectPlay TCP/UDP routing is live-tested locally; an actual Internet peer
   remains an external certification step.
8. **Deployment validation** — Linux preflight and native-process certification
   are complete. Windows Docker Desktop/WSL2 and physical two-machine runs have
   runnable checklists but require those external hosts.
9. **Gameplay expansion** — typed solo/LAN scenario launch and fully typed
   custom game setup are implemented and native-live-tested. Remaining
   consequential LAN mutations stay fail-closed until their native two-client
   effects converge.
10. **LAN browser product surface** — accounts, lobbies, browser/native human
    modes, portal/native chat, stream tickets, reconnect, race-safe park,
    activity/history, authoritative Hermes telemetry, CSV and isolated SQL
    analytics, canonical pre-release schemas, and complete operator docs are
    implemented and locally verified.

The ordinary operator experience remains:

```text
open Control Center -> create or resume match -> assign seats -> launch -> play
```

Docker Compose starts the control plane once and leaves it running. Per-match
workers are created, parked, resumed, and retired by the match manager rather
than by manual `docker compose down/up` cycles.

The remaining roadmap is certification, not missing product architecture: a
physical second computer, a real remote Tailscale peer, and Windows/WSL2. Ranked
policy and authored personality content are separate future design work and are
not silently implied by the current schema bones.

For a claim-by-claim accounting, see [Project status](project-status.md).
