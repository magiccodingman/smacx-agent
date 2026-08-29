# ADR 0001: Durable identities and authoritative match memory

- Status: Accepted
- Date: 2026-08-28

## Context

The semantic-control prototype has two useful identifiers: `match_id`, which
survives a save/reload, and `session_id`, which changes with the native game
process. Its durable knowledge is a JSON key/value ledger. That is sufficient
for one supervised player, but it cannot safely represent multiple agents,
concurrent games, controller/faction reassignment, chat history, evidence,
relationships, commitments, or replayable memory projections.

The platform must also guarantee that memory never becomes an omniscient side
channel. Two agents in the same match can legitimately observe different
facts. A process restart must not erase history, while a new game must not
inherit match-specific beliefs.

## Decision

SQLite is the authoritative local store. It owns identities, immutable events,
chat, structured memory, search documents, summaries, and projection cursors.
Graphiti is an optional asynchronous projection of this event stream. Graphiti
failure can reduce recall quality but can never block play, saving, loading, or
recovery.

The identity hierarchy is:

```text
installation_id
  agent_id                         persistent personality/player identity
  match_id                         durable playthrough, survives reload
    actor_id                       match-local participant identity
    perspective_id                 one agent's fair-play observation boundary
      session_id                   one native game-process lifetime
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
Callers never supply a Graphiti namespace. The platform derives it as:

```text
smacx:{installation_id}:{match_id}:{agent_id}:{perspective_id}
```

Only information available through the bridge's legitimate current-player
perspective may enter a perspective's event stream. Chat is stored as
untrusted in-game speech, never as operator or system instruction.

## Storage invariants

1. Raw events are append-only. SQLite triggers reject update and delete.
2. Structured facts, beliefs, relationships, goals, commitments, and summaries
   are projections with version history; replacing a current value never
   deletes its predecessor.
3. Session-local engine object IDs are forbidden in durable semantic memory.
4. Searches require an exact `(match_id, agent_id, perspective_id)` scope.
5. The static rules index is keyed by `ruleset_id` and is separate from match
   memory.
6. Compact working memory is derived from the database. Context-window
   compression never becomes the sole copy of game history.
7. A projection cursor is advanced only after the external projection accepts
   an event. Failed Graphiti work remains replayable.

## Lifecycle

- `new match`: create match, perspective, instance binding, then session.
- `save and park`: append lifecycle/save events, close the session, stop the
  worker, retain every durable identity and volume.
- `resume`: reuse match, agent, and perspective; start a new session.
- `reassign faction/controller`: close the old perspective and create a new
  one, even when the same agent continues.
- `archive`: change match status; do not delete events or memory.

## Consequences

The database can reconstruct an agent after Hermes context loss and can serve
other harnesses without changing the game bridge. Multi-agent isolation is
testable at the storage layer. More schema and projection code is required, but
the platform is no longer dependent on loose per-match files or one harness's
conversation memory.
