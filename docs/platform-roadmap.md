# Platform roadmap

The merged semantic bridge is the protected gameplay baseline. Platform work
is delivered as vertical, independently testable milestones.

1. **Durable foundation** — installation/match/agent/perspective/instance/session
   identities, versioned SQLite migrations, immutable events, structured
   memory, chat history, and scoped full-text retrieval.
2. **Runtime integration** — migrate the controller's manifests and knowledge
   ledger into SQLite while retaining guarded legacy import and the existing
   MCP contract.
3. **Memory projection** — the optional Graphiti adapter, derived namespaces,
   replay cursors, failure isolation, and adversarial cross-scope leakage tests
   are implemented. Default Compose packaging, Control Center health/config,
   automatic scheduling, secure secret injection, and real-backend evaluation
   remain before it can be considered default-on.
4. **Linux game worker** — user-supplied game import, private Wine/Proton
   prefix, semantic bridge, virtual display, health protocol, and view-only
   spectator stream.
5. **Control plane** — authenticated Control Center, provider discovery,
   agents/personalities, match builder, secrets, isolated per-worker MCP,
   view-only spectators, and Hermes profile/session management. The durable
   operator and host-Hermes vertical slices are implemented; scheduler/backups
   remain.
6. **Managed multiplayer** — one isolated worker per seat, managed hosting,
   exact-address joining, persistent chat attention, save/park/rejoin, and
   interactive human seats. Multi-agent host/join/lobby/start, native
   checkpoint save/load with exact faction restoration, match-wide park, and
   opt-in macvlan/ipvlan human-seat publication are implemented. Broader
   Internet transports and automated Windows networking remain out of scope.
7. **Deployment validation** — Linux home lab first, then Windows Docker
   Desktop/WSL2 and remote virtual-LAN compatibility.

The ordinary operator experience remains:

```text
open Control Center -> create or resume match -> assign seats -> launch -> play
```

Docker Compose starts the control plane once and leaves it running. Per-match
workers are created, parked, resumed, and retired by the match manager rather
than by manual `docker compose down/up` cycles.

For a claim-by-claim accounting, see [Project status](project-status.md).
