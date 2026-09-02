# ADR 0001: Durable identities and authoritative match memory

- Status: Accepted
- Date: 2026-08-28

## Context

The platform needs identities that survive process recovery and a portable
record that can explain or reconstruct one agent's campaign without depending
on a particular Hermes database, Graphiti server, or SQLite cache.

The platform must also guarantee that memory never becomes an omniscient side
channel. Two agents in the same match can legitimately observe different
facts. A process restart must not erase history, while a new game must not
inherit match-specific beliefs.

## Decision

The hash-linked campaign journal is authoritative for match actions, chat,
structured memory, notebook records, lifecycle incidents, and checkpoint
references. It is partitioned by match, agent, fair-play perspective, and
timeline. Each event is an atomic JSON file chained to its predecessor; each
match directory has local Git history committed at coherent turn, checkpoint,
park, recovery, and completion boundaries.

SQLite remains authoritative for platform coordination: identities, worker and
harness lifecycle, portal accounts/lobbies, schedules, backup catalogues,
leases, and projection cursors. Its dynamic-memory/FTS tables are query
projections and compatibility indexes that can be rebuilt from the journal.
Graphiti is an optional asynchronous temporal projection. Failure of either
derived recall layer can reduce recall quality but cannot block native play,
saving, loading, or recovery.

The identity hierarchy is:

```text
installation_id
  agent_id                         persistent personality/player identity
  match_id                         durable playthrough, survives reload
    actor_id                       match-local participant identity
    perspective_id                 one agent's fair-play observation boundary
      session_id                   one native game-process lifetime
      timeline_id                  main or an explicit future rewind branch
        event/chat/memory records
  instance_id                      schedulable game-worker slot
```

An `instance_id` can host different matches over time. A `session_id` belongs
to exactly one match, instance, agent, and perspective. A `perspective_id`
belongs to exactly one match and agent and records one faction/controller
view. Faction reassignment closes that perspective and creates another; the
system does not merge their knowledge automatically.

Every match-specific event or memory record carries `match_id`, `agent_id`,
and `perspective_id`. Records learned from live play also carry `session_id`,
turn/year provenance, and the observed bridge revision when applicable.
Callers never supply a Graphiti namespace. The platform derives it from:

```text
installation_id + match_id + agent_id + perspective_id + timeline_id
```

Only information available through the bridge's legitimate current-player
perspective may enter a perspective's event stream. Chat is stored as
untrusted in-game speech, never as operator or system instruction.

## Storage invariants

1. Canonical events are append-only and hash-linked. A manifest head must equal
   the final verified event hash.
2. Structured facts, beliefs, relationships, goals, commitments, notes, and
   summaries are materialized by deterministic replay; replacing a current
   value never erases its historical event.
3. Session-local engine object IDs are forbidden in durable semantic memory.
4. Searches require an exact `(match_id, agent_id, perspective_id)` scope.
5. The static rules index is keyed by `ruleset_id` and is separate from match
   memory.
6. Compact working memory is derived from the journal. Context-window
   compression never becomes the sole copy of game history.
7. A SQLite/Graphiti cursor advances only after its projector accepts an event.
   Failed projection work remains replayable from the canonical event ID.
8. Native saves and Hermes transcripts are checkpointed separately and
   referenced by controlled identity/digest; neither is placed in journal Git.

## Lifecycle

- `new match`: create match, perspective, instance binding, then session.
- `save and park`: create a verified native checkpoint, append and commit its
  journal reference, close the session, stop the worker, and apply save
  retention/compression.
- `resume`: reuse match, agent, and perspective; start a new session.
- `reassign faction/controller`: close the old perspective and create a new
  one, even when the same agent continues.
- `future rewind`: fork an explicit child timeline at an existing event hash
  and matching save digest; never rewrite the parent.
- `archive`: change match status and compact native saves; retain the journal.

## Consequences

The journal can reconstruct an agent's bounded working state and disposable
SQLite query index after Hermes context loss. Multi-agent isolation, branch
ancestry, and integrity are testable at the storage layer. This adds replay and
Git-boundary machinery, but campaign history no longer depends on one mutable
database or Hermes conversation memory alone.
