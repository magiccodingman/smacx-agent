# Platform roadmap

The merged semantic bridge is the protected gameplay baseline. Platform work
is delivered as vertical, independently testable milestones.

1. **Durable foundation** — installation/match/agent/perspective/instance/session
   identities, versioned SQLite migrations, immutable events, structured
   memory, chat history, and scoped full-text retrieval.
2. **Runtime integration** — migrate the controller's manifests and knowledge
   ledger into SQLite while retaining guarded legacy import and the existing
   MCP contract.
3. **Memory projection** — default-on but optional Graphiti projection with
   derived namespaces, replay cursors, health reporting, and adversarial
   cross-scope leakage tests.
4. **Linux game worker** — user-supplied game import, private Wine/Proton
   prefix, semantic bridge, virtual display, health protocol, and view-only
   spectator stream.
5. **Control plane** — authenticated Control Center, provider discovery,
   agents/personalities, match builder, secrets, isolated per-worker MCP,
   and Hermes profile/session management. The solo-worker and host-Hermes
   vertical slice is implemented; scheduler/backups remain.
6. **Managed multiplayer** — one isolated worker per seat, managed hosting,
   exact-address joining, persistent chat attention, save/park/rejoin, and
   interactive human seats.
7. **Deployment validation** — Linux home lab first, then Windows Docker
   Desktop/WSL2 and remote virtual-LAN compatibility.

The ordinary operator experience remains:

```text
open Control Center -> create or resume match -> assign seats -> launch -> play
```

Docker Compose starts the control plane once and leaves it running. Per-match
workers are created, parked, resumed, and retired by the match manager rather
than by manual `docker compose down/up` cycles.
