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

### View-only spectators

Per-worker noVNC is optional, password-protected, published to loopback by
default, and starts `x11vnc` in enforced view-only mode. The live test verifies
the socket and confirms the password is not present in container configuration.
Agents and MCP tools never receive spectator access.

## Implemented and contained-tested

### Named external human seats

Mixed matches require an agent host but may assign remaining seats to exact
human player names. Human seats receive no agent identity, perspective, worker,
or MCP endpoint. The first Start stages the native lobby; the second Start
validates exact names, participant count, readiness, and saved-faction
reclamation before allowing the host to launch.

The manager refuses ordinary Docker bridge publication and requires an
operator-created, non-internal macvlan/ipvlan network. Identity, readiness,
faction, and network-driver guards are contained-tested, and the entire native
lifecycle is locally live-tested with an independent third process. A physical
second machine has not yet certified this path.

### Harness-neutral contracts with Hermes as reference

Worker, MCP, identity, and memory contracts are not coupled to Hermes. The
current host adapter creates one isolated Hermes profile per durable agent,
uses the exact provider/model and MCP binding resolved by the Control Center,
and defaults Qwen/Hermes reasoning to low. A custom in-project harness has not
been built; Hermes remains the supported reference runtime.

## Optional Graphiti: implemented core, unfinished product integration

The following Graphiti work is delivered:

- a direct `graphiti-core` adapter;
- internally derived namespace
  `smacx:{installation}:{match}:{agent}:{perspective}`;
- immutable SQLite event projection with deterministic episode IDs;
- advance-on-success cursors, retry after failure, bounded draining, and
  scope-local rebuild;
- untrusted-chat extraction instructions; and
- adversarial contained tests preventing cross-perspective leakage.

The following is **not** yet delivered:

- a Neo4j/Graphiti service in the default Compose deployment;
- Control Center configuration, health, enable/disable, and rebuild controls;
- automatic projector scheduling for every active perspective;
- secure secret injection for graph/model/embedding credentials;
- a real end-to-end evaluation using the intended Qwen extraction model and a
  compatible embedding model; and
- evidence that Graphiti improves decisions enough to justify enabling it by
  default.

Until those items are complete, Graphiti is an optional manually operated
projection. SQLite memory and BM25 recall are the production default.

## Not yet delivered

- A curated, locally searchable Alien Crossfire rules/wiki corpus or RAG
  service. The model may use an explicitly allowed web tool, but no bundled
  copyrighted corpus is shipped.
- First-class personality-card editing and behavioral evaluation. Durable
  agents have a `personality_ref`, and the Hermes system prompt preserves
  player autonomy, but the complete personality workflow remains future work.
- Automated long-running scheduling, crash policy, backups, and unattended
  agent supervision in the Control Center.
- Managed secret injection for keyed remote model providers. The current
  Hermes path intentionally rejects a keyed provider rather than exposing its
  credential.
- Physical two-machine human-LAN certification, remote virtual-LAN transport,
  Internet matchmaking, and automated Windows Docker Desktop/WSL2 networking.
- Menu/lobby automation for every game profile and scenario. Managed LAN
  currently validates the `small_easy` profile and exact checkpoint resumes.
- Every rare scenario interaction or LAN synchronization path. See
  [Coverage and limits](coverage.md); missing mandatory interactions fail
  closed.
- Training data, a LoRA, or gameplay-specific model fine-tuning.

## Definition of the next complete platform milestone

The next platform milestone should not be called complete until it includes:

1. one-command optional Graphiti/Neo4j deployment with per-perspective
   scheduling, Control Center health, and real-backend evaluation;
2. a legally sourced, reproducible rules knowledge layer with citations and
   no copied proprietary corpus;
3. a physical mixed human/AI LAN game across two machines, including chat,
   checkpoint, disconnect/rejoin, and continue; and
4. operator backup/restore and crash-recovery documentation validated from a
   clean installation.

Personality-card semantics are intentionally outside this milestone. The
opaque attachment seam remains reserved, but card format, editing, prompting,
and behavioral evaluation will be designed separately.
