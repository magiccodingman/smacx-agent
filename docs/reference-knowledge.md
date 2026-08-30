# Alien Crossfire mechanics encyclopedia

SMACX Agent gives each managed player a local, provenance-tracked mechanics
encyclopedia. It is general game knowledge, never current match memory: it
cannot contain an unseen faction, private opponent state, unexplored map data,
save internals, or chat that was not delivered to that seat.

The encyclopedia has two complementary layers:

1. A distributable, independently written 52-document handbook explains the
   major mechanics and authority rules without build orders, walkthroughs,
   exploits, scenario solutions, or advice about how to win.
2. First validation of an operator's legal Alien Crossfire installation builds
   a much larger private index inside that installation's SQLite namespace.
   The reference Steam copy currently produces 672 documents from 18 approved
   sources. Counts are descriptive test evidence, not a protocol constant.

The private build is automatic. Registering or revalidating a game installation
runs the extractor in an isolated container with a read-only source mount and no
network. It replaces only that source's prior private namespace. No separate
wiki download, embedding service, or operator command is required.

## Structured private records

The extractor reads the expansion-native `alphax.txt` rules data before any
fallback prose and generates exact typed entities with stable normalized keys.
The reference installation yields:

- 87 technologies with prerequisite and reverse-unlock relations;
- 110 facilities, orbital assets, and Secret Projects;
- 29 special abilities, 26 weapons/modules, 14 defenses, 9 chassis, 4 reactors,
  23 predefined units, and morale records;
- all 14 original and Alien Crossfire faction mechanical headers;
- 15 social models plus every named rating band;
- Council proposals, difficulty levels, multiplayer clocks, world sizes,
  resource records, and terraforming orders; and
- 190 bounded expansion Datalinks/concepts and manual-rules fallback sections.

Expansion-native entity records have source priority 300, expansion help has
priority 200, and manual fallback has priority 100. The base-game duplicates
`alpha.txt`, `help.txt`, and `concepts.txt` are deliberately omitted from this
Alien Crossfire ruleset, so a base-game record cannot accidentally outrank or
contradict its expansion counterpart.

Entity metadata contains readable native fields, aliases, prerequisites, and
typed relations. Technology relations are bidirectional: a technology can
return what it requires and the facilities, components, orders, models, units,
or proposals it unlocks. Exact lookup therefore avoids spending context on
anonymous fixed-size chunks.

## Agent retrieval protocol

`smac_reference` supports five actions:

1. `topics` returns the hierarchy, document counts, and structured entity
   counts.
2. `search` performs compact FTS5/BM25 retrieval. It first requires all query
   terms and falls back to any-term search only when the strict query is empty.
   Exact titles and higher-priority expansion sources rank first.
3. `get` returns one complete document by `document_id`.
4. `lookup` resolves one entity or a batch of up to 30 `{kind,key}` pairs. This
   is the preferred path when the entity is known.
5. `related` returns an exact entity together with its prerequisite/unlock
   neighborhood.

Example calls:

```text
smac_reference(action="lookup", entity_kind="technology", entity_key="ecology", include_body=true)
smac_reference(action="related", entity_kind="technology", entity_key="ecology")
smac_reference(action="lookup", entities_json='[{"kind":"faction","key":"angels"},{"kind":"ability","key":"clean-reactor"}]')
```

The current native state and enumerated legal choices always override the
encyclopedia. The mandatory `smac_match_briefing` tells a managed player which
non-default settings and scenario restrictions apply before it plans.

## Included and excluded local sources

The private allowlist is intentionally narrow: the manual's rules chapters and
rules/options appendices; Alien Crossfire Datalinks and concepts; expansion
numeric rules; and the mechanical headers of fourteen faction records.

The extractor excludes `Script.txt`, scenario directories, tutorial and tips
sections, editor/customization material, faction dialogue and story prose,
walkthroughs, and guides. It does not index the web. Extracted text stays in the
operator's private SQLite volume and is not copied into an image, repository,
release archive, or public API response.

## Copyright and citation boundary

The repository does **not** contain or translate the game manual, Datalinks
text, `Script.txt`, `alpha.txt`/`alphax.txt`, faction prose, wiki page dumps,
images, or extracted assets. `knowledge/core.json` uses original project
wording about mechanics. The copyright audit fails on eight or more consecutive
normalized words shared with a supplied proprietary source and emits hashes and
overlap lengths only—never source passages.

Online pages are citations used to cross-check facts, not corpus input.
`knowledge/sources.json` records each canonical citation plus a fixed Internet
Archive snapshot, timestamp, and CDX digest verified as a captured HTTP 200 on
2026-08-29. These are durability fallbacks; startup never depends on either the
canonical site or the archive. StrategyWiki expression is not copied or adapted.

Copyright protects source expression rather than the underlying mechanics and
facts. This conservative engineering boundary is not legal advice.

## Rebuild and verification

Control Center idempotently seeds the authored handbook at startup. This
unreleased project retains one canonical schema and no migration chain; delete
pre-release data volumes when the canonical schema changes.

```bash
PYTHONPATH=src python3 -m smacx_reference \
  --database /tmp/smacx-reference.sqlite3 --query "Treaty Pact trust"
PYTHONPATH=src python3 scripts/reference_corpus_test.py
PYTHONPATH=src python3 scripts/private_reference_test.py \
  --game-source "/path/to/legal/game" --live-docker
python3 scripts/reference_copyright_audit.py \
  --source "/path/to/legal/game/Manual.pdf" \
  --source "/path/to/legal/game/helpx.txt" \
  --source "/path/to/legal/game/alphax.txt"
```

SQLite FTS5 is the authoritative retrieval path and requires no embeddings.
Optional Graphiti remains a perspective-scoped projection of dynamic match
memory; it is not used for static rules documents.
