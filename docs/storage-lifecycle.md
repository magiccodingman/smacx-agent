# Runtime and campaign storage

SMACX Agent separates reproducible compatibility bytes from durable campaign
state. This keeps concurrent seats isolated without storing a complete game and
Wine installation for every player.

## Installation-local prepared image

The first managed seat for a validated game source creates a local Docker image
identified by this installation, the supplied game-tree fingerprint, and the base
worker image. A short-lived, network-isolated preparation container imports the
operator's game directory, overlays the semantic bridge, initializes GE-Proton,
and registers DirectPlay. The manager commits that stopped container to the
local Docker Engine. It is never pushed or included in this repository's
published images.

All seats use Docker's shared read-only image layers for those files. Each
running seat still receives its own container, display, processes, registry
changes, network identity, secrets, and copy-on-write layer. Only its small
managed state volume is durable.

Changing any supplied game/mod file (personal save folders are excluded) or
rebuilding the worker image produces a new prepared image automatically;
existing match records retain their exact image reference.
The control service refreshes the installation fingerprint at startup while
preserving the stable game-source identity used by lobbies and history.

## Durable worker state

The durable volume contains native saves, a compact archive manifest, and small
worker metadata. Disposable Wine and desktop mutations live only in the
container copy-on-write layer. Recovery starts from the known prepared
compatibility base and loads the last bridge-verified native save.

Parking reaches a stable boundary, verifies a recovery save, stops the native
processes, removes the disposable container, applies retention, compresses
retained saves with zstd, and finally marks the match parked. Starting a parked
seat expands retained saves before Proton launches. Failed decompression stops
startup rather than launching incomplete state.

## Portable campaign journal

Every AI perspective has an append-only, hash-linked JSON timeline in the
persistent control volume:

```text
campaigns/<match>/perspectives/<agent>/<perspective>/timelines/<timeline>/
  manifest.json
  events/
  notebook/
  state/working-state.json
```

This journal is the campaign authority for actions, chat, facts, beliefs,
relationships, commitments, goals, summaries, notes, incidents,
native-session lifecycle, and verified checkpoint references. Each event
carries its perspective/timeline identity, sequence, prior hash, event hash,
and turn/year provenance where known. Native saves are referenced by
controlled logical metadata; game assets, provider prompts, responses, and raw
reasoning are never committed.

Each match directory is an installation-local Git repository. The platform
commits coherent lifecycle, checkpoint, turn, park, recovery, and completion
boundaries rather than creating a commit for every unit action. Uncommitted
events are still atomically durable and join the next boundary commit.
`working-state.json`, notebook entry files, SQLite/FTS indexes, and Graphiti are
rebuildable projections of the journal.

Normal control backups freeze and include the complete campaign tree and Git
history alongside the platform database, worker archives, and Hermes volumes.
Restore verifies hashes and rejects traversal, links, devices, and unexpected
archive roots before replacing the campaign tree.

## Retention defaults

The default policy keeps the latest 10 saves, the verified
`control_recovery` save, every 25th chronology point as a milestone, and one
final verified save for a completed campaign. Administrators can change these
values or opt into full native turn history under **Operations & recovery**.
Completed archives live beside the control database in its persistent volume
and are included in normal control backups. Analytics and the journal remain
independent of native-save retention.

When the native final-score state is detected, the portal idempotently stops
the campaign's autonomous callers and retires every seat. Only the managed host
checkpoint is promoted to the completed archive; redundant client volumes are
released. Repeated completion reconciliation is safe.

A `.sav` is resumable world state, not a replay. The journal already supports
an explicit child timeline anchored to an immutable parent event hash and
native-save digest. The current UI does not expose rewind: future activation
must restore that exact save, fork every affected perspective, rotate native
sessions, and rebuild SQLite/Graphiti projections for the child timeline.
Retaining native saves alone is never treated as safe rewind.

Prepared game and compatibility layers are paid once per distinct fingerprint.
Running containers add only changed blocks; parked matches retain compressed
saves rather than hydrated desktops. The Operations page reports completed
archive size. Docker image/build-cache maintenance remains an explicit operator
action and is never mixed with campaign retention.
