# Rules and mechanics reference

SMACX Agent ships a small, project-authored Alien Crossfire mechanics corpus.
It is general game knowledge, not match memory: it cannot contain a current
map, unseen factions, private chat, save data, or another player's state.

This is a focused 19-document primer, not a claim to contain every official
manual, Datalinks entry, scenario rule, numeric table, or strategy guide. Exact
match settings and live semantic choices remain authoritative.

The corpus is deliberately a reference rather than a walkthrough. It covers
the turn loop, economy, bases and citizens, expansion, terraforming, ecology,
research, unit design, combat, Social Engineering, diplomacy, Council votes,
probe operations, victory paths, all original and Alien Crossfire faction
families, multiplayer continuity, and uncertainty-aware planning. It explains
concepts without prescribing a fixed exploit or revealing a scenario solution.

## Agent protocol

`smac_reference` has three actions:

1. `topics` returns the hierarchy and document counts.
2. `search` runs BM25 over titles, summaries, bodies, and tags. Its default
   result is compact and includes `document_id`, summary, source URL, license,
   provenance, and content hash.
3. `get` returns one complete document selected from search.

An agent should search only when a mechanic is unclear and fetch only the
document needed. Fresh semantic state and legal choices always override a
general reference.

## Copyright and provenance boundary

The repository does **not** contain or translate the game manual, Datalinks text,
`Script.txt`, `alpha.txt`/`alphax.txt`, faction prose, wiki page dumps, images,
or extracted game assets. `knowledge/core.json` consists of original project
wording about game mechanics. Every document records its provenance, source,
license characterization, and a deterministic content hash. Citations identify
materials used only to cross-check facts; no cited expression is incorporated
into the Apache-2.0 corpus. StrategyWiki text is CC BY-SA 4.0 and would carry
attribution/share-alike obligations if copied or adapted, so this corpus does
neither. The operator's legal manual and game data are local verification
sources and are never committed.

Copyright protects source expression, not the underlying mechanics, systems,
or facts. Consequently, reference documents explain rules independently in
project-authored language rather than translating source paragraphs. This is a
conservative engineering boundary, not legal advice.

The game still comes from the operator's legal installation. A future optional
local-only verifier may consult that user's manual or modified rule files, but
source text and generated excerpts must never be committed or shipped by this
project.

## Rebuild and test

Control Center seeds or updates the bundled corpus idempotently at startup. It
does not create schema migrations; the unreleased project still initializes
one canonical schema revision.

```bash
PYTHONPATH=src python3 -m smacx_reference \
  --database /path/to/smacx.sqlite3 --query "Treaty Pact trust"
PYTHONPATH=src python3 scripts/reference_corpus_test.py
python3 scripts/reference_copyright_audit.py \
  --source /path/to/legal/game/Manual.pdf \
  --source /path/to/legal/game/Script.txt \
  --source /path/to/legal/game/help.txt
```

The audit reports hashes, document identifiers, and overlap lengths only. It
does not print or persist source passages. A match of eight or more consecutive
normalized words fails by default so maintainers can rewrite the project text
without copying or translating the source expression.

SQLite FTS5 is the production search path and requires no embedding service.
Optional Graphiti remains a derived match-memory projection and is not used for
these static rules documents.
