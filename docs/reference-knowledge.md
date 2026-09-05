# Local Alien Crossfire rules library

SMACX Agent builds a private mechanics library on the operator's machine. The
repository and container images contain only acquisition metadata and parser
code; they do not contain the game's manual, Datalinks text, wiki prose, game
binaries, strategy guides, or generated embeddings.

## First run and refresh

The `knowledge-service` starts automatically with the normal Compose stack. On
its first enabled run it:

1. reads approved mechanics files from the operator-mounted Alien Crossfire
   directory;
2. fetches the explicit pages in `knowledge/sources.json`, trying the canonical
   address first and its fixed Internet Archive snapshot second;
3. removes site navigation, tables of contents, editing chrome, URLs, reference
   markers, and sections labelled as strategies, walkthroughs, tips, cheats,
   exploits, recommendations, or opening moves;
4. converts useful headings and body content to clean Markdown in a private
   volume, removes renderer templates, merges each technology's parallel short
   and long records, and consolidates paired land/ocean terraforming text into
   one named factual article;
5. organizes the corpus into a stable recursive Datalinks taxonomy whose
   collection names, descriptions, and tags express actual game concepts—not
   alphabetic or numbered pagination buckets;
6. synchronizes collection snapshots through `SemanticKnowledge.NET`, deleting
   documents removed by a successful later snapshot; and
7. creates compact searchable embeddings without writing acquired content into
   the repository or an image.

A parser revision, source-manifest change, successful daily source refresh, or
administrator-forced refresh causes re-evaluation. Transient loss of one web
source does not delete its last good private snapshot. Manifest removals and
renames are handled by snapshot synchronization rather than an accumulating
append-only dump.

The built-in first build can take several minutes because it downloads and
initializes the ONNX model and embeds the complete local rules set. Model files,
cleaned content, SQLite metadata, and vectors persist in the
`smacx-knowledge-data` Docker volume; subsequent starts normally reuse them.
The model is downloaded into that runtime cache, never baked into a distributed
image, and is fetched again only for an empty cache or a changed selected model
artifact—not on every container restart.

## One embedding runtime

The default is the CPU-oriented
`magiccodingman/Jasper-Token-Compression-600M-ONNX-INT8` runtime supplied by
`OnnxTextEmbeddings.NET`. Exactly one model instance is registered:

- `SemanticKnowledge.NET` receives its native multi-chunk document embeddings;
- Graphiti calls the service's internal OpenAI-compatible `/v1/embeddings`
  facade and receives one combined vector per normal API input.

This avoids loading duplicate copies of the same large model. The admin can
instead select an external OpenAI-compatible embedding model and must provide
its exact vector dimensions and a stable embedding-space ID. A space/model
change restarts the knowledge service cleanly and makes SemanticKnowledge
revalidate/rebuild its vectors. Disabling embeddings also disables semantic
rules and Graphiti; the authoritative campaign journal and its lexical working
index remain available.

The Jasper model supports long inputs, but retrieval documents are deliberately
chunked around 768 tokens to avoid weak long-tail facts and to give the model
precise evidence. SemanticKnowledge retains the chunk array. Only Graphiti's
single-vector compatibility facade combines chunks.

## Embedding observability

The **Analytics** page includes an embedding observatory for both the built-in
CPU model and an administrator-selected external provider. It separates:

- initial encyclopedia construction and later changed-document refreshes;
- human/agent semantic wiki searches;
- Graphiti episode projection and scoped memory recall; and
- a repeatable semantic quality canary.

Hourly aggregates record operation and input counts, source/provider token
counts, vector/chunk counts, elapsed time, effective tokens per second, model,
dimension, embedding-space identity, and failures. Local source and query token
counts come from the actual tokenizer. External counts use provider usage when
available and are visibly labelled estimates when it is absent. Wiki refresh
throughput is an end-to-end operation measurement, including synchronization;
it is not presented as raw model-only benchmark throughput.

The canary verifies vector shape, finite values, normalization, repeatability,
semantic separation, and a real encyclopedia retrieval. It runs once after the
active embedding configuration and corpus are ready, and again after a clean
restart caused by an embedding-space change.

Audit storage is a compact hourly SQLite aggregate in the private knowledge
volume, with only the latest 200 quality results retained. It has no columns for
input text, acquired prose, embeddings, credentials, chat, or model reasoning.
Audit writes are fail-open and cannot block rules retrieval or gameplay.

## Agent retrieval

The sovereign commissions focused rules research through
`smac_investigate(faculty="reference")`. Its disposable Hermes child receives
exactly one private `reference_query` instrument with this bounded surface:

- `topics` lists top-level organized collections and descendant counts;
- `tree` returns the recursive semantic collection map and can include each
  collection's direct article links;
- `collection_documents` browses one collection without flattening its child
  collections;
- `search` uses SemanticKnowledge Smart routing, native FTS5/BM25, semantic
  retrieval, and reciprocal-rank fusion, with exact normalized titles and
  title-token matches preferred;
- `get` returns one selected document;
- `lookup` and `related` translate named-mechanic requests into focused semantic
  searches for compatibility with existing agents.

Compact search returns ranked titles and descriptions. `include_body=true`
returns a bounded evidence pack rather than an unbounded corpus dump. Current
native state and enumerated legal choices always override general rules.

The taxonomy begins with seven recognizable domains: rules and setup; bases
and economy; diplomacy and society; factions and leaders; Planet and
terraforming; research and technology; and units and combat. Deeper nodes use
gameplay meaning such as citizens and drone control, Council politics, resource
terraforming, defensive abilities, or Discover-oriented technologies. A
collection is both a visible folder and SemanticKnowledge routing signal: its
title, purpose-built description, and tags help Smart search decide which
subtree to inspect. This is why the corpus never uses `A–C`, `Part 2`, or other
presentation-only labels.

The system prompt requires every managed player to read and acknowledge the
match-configuration briefing before its first mutation. That briefing carries
actual victory conditions, non-default rules, scenario restrictions, faction,
clock, and host policy so static mechanics knowledge cannot make the agent
assume defaults. The catalog itself is queried on demand rather than copied
into every briefing, and ordinary gameplay changes never invalidate the
configuration hash.

## Human Datalinks Wiki

The portal exposes the same private corpus through one reusable responsive
reader. It is available as **Datalinks Wiki** in normal navigation and as a
compact **Wiki** tab in the managed in-game control center. The full reader has
a recursive contents tree containing both semantic folders and direct article
links, breadcrumbs, an article outline, and hybrid search. Selecting an article
opens that article immediately. Selecting a folder shows its meaningful
description and child domains rather than a second index of the same pages.
Tablet/mobile and in-game layouts collapse navigation without creating a
second implementation.

Markdown is parsed on the portal server with Markdig, raw source HTML is
disabled, and the generated HTML is sanitized before the WebAssembly client
receives it. Human search queries use the embedding tokenizer and are capped at
512 model tokens. Selecting a result closes search, expands its ancestor
collections, and opens the exact article.

The SemanticKnowledge database is a rebuildable private projection. A taxonomy
revision deliberately recreates that projection before publishing the new
snapshot so removed routing nodes cannot continue influencing retrieval.
Operator accounts, matches, saves, analytics, and memory live elsewhere and
are not migrated or reset by this process.

## Copyright boundary

The application does not include or distribute Sid Meier's Alpha Centauri,
Alien Crossfire, their data files, or acquired reference content. Reference
material is fetched or read locally into a private persistent volume at runtime.
Only source addresses, archive fallbacks, hashes, status, and parser code are
part of this project.

## Operations and verification

The Operations page reports rules and Graphiti health and selects Graphiti's
independent extraction profile. **Models & AI profiles** contains the shared
local, external, or disabled embedding configuration beside its provider.

Useful contained checks:

```bash
dotnet test knowledge_service/Smacx.KnowledgeService.Tests/Smacx.KnowledgeService.Tests.csproj
dotnet test portal/Smacx.Portal.Tests/Smacx.Portal.Tests.csproj
PYTHONPATH=src python3 scripts/reference_corpus_test.py
docker compose build knowledge-service
```

The service endpoints are internal to the Compose network. The public portal
proxies only authenticated, bounded search/read operations.
