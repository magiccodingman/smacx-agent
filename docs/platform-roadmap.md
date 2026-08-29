# Platform roadmap

The merged semantic bridge is the protected gameplay baseline. Platform work
is delivered as vertical, independently testable milestones.

1. **Durable foundation** — installation/match/agent/perspective/instance/session
   identities, one canonical pre-release SQLite schema, immutable events,
   structured memory, chat history, and scoped full-text retrieval.
2. **Runtime integration** — migrate the controller's manifests and knowledge
   ledger into SQLite while retaining guarded legacy import and the existing
   MCP contract. A project-authored, citation-bearing rules corpus now adds
   global FTS5/BM25 mechanics lookup without importing proprietary game text.
3. **Memory projection** — the optional Graphiti adapter, derived namespaces,
   replay cursors, failure isolation, adversarial cross-scope leakage tests,
   digest-pinned Compose packaging, Control Center health/config/rebuild,
   scheduling, file-secret injection, and real Neo4j failure-isolation test are
   implemented. It remains default-off because the reference Qwen endpoint has
   no compatible embedding API and decision-quality benefit is not established.
4. **Linux game worker** — user-supplied game import, private Wine/Proton
   prefix, semantic bridge, virtual display, health protocol, and view-only
   spectator stream.
5. **Control plane** — authenticated Control Center, provider discovery,
   agents/personalities, match builder, secrets, isolated per-worker MCP,
   view-only spectators, and Hermes profile/session management. The durable
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
9. **Gameplay expansion** — typed scenario launch and remaining consequential
   LAN mutations stay fail-closed until legal scenario assets and convergent
   native two-client tests are available.

The ordinary operator experience remains:

```text
open Control Center -> create or resume match -> assign seats -> launch -> play
```

Docker Compose starts the control plane once and leaves it running. Per-match
workers are created, parked, resumed, and retired by the match manager rather
than by manual `docker compose down/up` cycles.

For a claim-by-claim accounting, see [Project status](project-status.md).
