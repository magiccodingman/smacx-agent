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
5. organizes the corpus into a stable recursive Datalinks taxonomy whose leaf
   collections remain small enough to browse and route effectively;
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
rules and Graphiti; gameplay's authoritative SQLite memory remains available.

The Jasper model supports long inputs, but retrieval documents are deliberately
chunked around 768 tokens to avoid weak long-tail facts and to give the model
precise evidence. SemanticKnowledge retains the chunk array. Only Graphiti's
single-vector compatibility facade combines chunks.

## Agent retrieval

`smac_reference` exposes a small, bounded surface:

- `topics` lists top-level organized collections and descendant counts;
- `search` uses native FTS5/BM25 plus semantic retrieval and reciprocal-rank
  fusion, with exact normalized titles and title-token matches preferred;
- `get` returns one selected document;
- `lookup` and `related` translate named-mechanic requests into focused semantic
  searches for compatibility with existing agents.

Compact search returns ranked titles and descriptions. `include_body=true`
returns a bounded evidence pack rather than an unbounded corpus dump. Current
native state and enumerated legal choices always override general rules.

The system prompt requires every managed player to read and acknowledge the
match briefing before mutating a game. That briefing carries actual victory
conditions, non-default rules, scenario restrictions, faction, clock, and host
policy so static mechanics knowledge cannot make the agent assume defaults.

## Human Datalinks Wiki

The portal exposes the same private corpus through one reusable responsive
reader. It is available as **Datalinks Wiki** in normal navigation and as a
compact **Wiki** tab in the managed in-game control center. The full reader has
a recursive contents tree, breadcrumbs, an article outline, and hybrid search;
tablet/mobile and in-game layouts collapse navigation without creating a
second implementation.

Markdown is parsed on the portal server with Markdig, raw source HTML is
disabled, and the generated HTML is sanitized before the WebAssembly client
receives it. Human search queries use the embedding tokenizer and are capped at
512 model tokens. Selecting a result closes search, expands its ancestor
collections, and opens the exact article.

## Copyright boundary

The application does not include or distribute Sid Meier's Alpha Centauri,
Alien Crossfire, their data files, or acquired reference content. Reference
material is fetched or read locally into a private persistent volume at runtime.
Only source addresses, archive fallbacks, hashes, status, and parser code are
part of this project.

## Operations and verification

The Operations page reports the rules service, embedding mode, corpus refresh,
and Graphiti status. It also lets an administrator choose local, external, or
disabled embeddings and select Graphiti's independent extraction profile.

Useful contained checks:

```bash
dotnet test knowledge_service/Smacx.KnowledgeService.Tests/Smacx.KnowledgeService.Tests.csproj
dotnet test portal/Smacx.Portal.Tests/Smacx.Portal.Tests.csproj
PYTHONPATH=src python3 scripts/reference_corpus_test.py
docker compose build knowledge-service
```

The service endpoints are internal to the Compose network. The public portal
proxies only authenticated, bounded search/read operations.
