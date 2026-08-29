# Optional Graphiti projection

Graphiti is a derived temporal graph, never the SMACX Agent source of truth. The authoritative event stream, chat ledger, facts, beliefs, relationships, commitments, goals, and summaries remain in SQLite. If Graphiti, Neo4j, an embedding endpoint, or an extraction model fails, game control and SQLite recall continue normally.

## Isolation contract

Every episode group is derived internally as:

```text
smacx:{installation_id}:{match_id}:{agent_id}:{perspective_id}
```

The agent cannot supply or widen that namespace. The projector reads only events already constrained to that exact `MemoryScope`. Stable UUIDv5 episode identifiers make retries deterministic. A cursor advances only after `graphiti-core.add_episode` completes; errors are recorded without skipping the failed event. Rebuild clears only the derived group and replays immutable SQLite events.

Chat remains explicitly tagged as untrusted in-game speech. Extraction instructions distinguish observations, player claims, and the agent's own beliefs, and prohibit hidden-state inference.

## Why direct `graphiti-core`

SMACX Agent embeds the optional library in a private projector process instead of exposing Graphiti's HTTP/MCP service. That keeps the graph database on an internal container network and avoids creating a second agent-facing write authority. The current Graphiti project documents temporal episodes, explicit `group_id` filtering, OpenAI-compatible local models, and Neo4j/FalkorDB backends in its [official repository](https://github.com/getzep/graphiti).

Graphiti requires both a structured-output-capable LLM and an embedding model. A chat-only Qwen endpoint is insufficient unless it also serves embeddings. The official Graphiti guidance warns that smaller/local models may fail structured extraction, so this projection stays optional until evaluation demonstrates a benefit.

## Installation and configuration

Install the pinned optional dependency in the projector environment:

```bash
python -m pip install -r requirements-graphiti.txt
```

Required environment variables:

```text
SMACX_GRAPHITI_NEO4J_URI=bolt://neo4j:7687
SMACX_GRAPHITI_NEO4J_USER=neo4j
SMACX_GRAPHITI_NEO4J_PASSWORD=...
SMACX_GRAPHITI_LLM_BASE_URL=http://model-host:8000/v1
SMACX_GRAPHITI_LLM_API_KEY=local
SMACX_GRAPHITI_LLM_MODEL=...
SMACX_GRAPHITI_EMBED_BASE_URL=http://embedding-host:8000/v1
SMACX_GRAPHITI_EMBED_API_KEY=local
SMACX_GRAPHITI_EMBED_MODEL=...
SMACX_GRAPHITI_EMBED_DIM=...
```

Optional:

```text
SMACX_GRAPHITI_SMALL_MODEL=...
SMACX_GRAPHITI_CONCURRENCY=2
SMACX_GRAPHITI_STRUCTURED_OUTPUT_MODE=json_schema
GRAPHITI_TELEMETRY_ENABLED=false
```

Project one bounded batch:

```bash
PYTHONPATH=src python -m smacx_graphiti \
  --database /data/smacx.sqlite3 \
  --match-id MATCH_ID \
  --agent-id AGENT_ID \
  --perspective-id PERSPECTIVE_ID
```

Add `--rebuild` only after confirming the exact scope. It deletes and recreates that derived Graphiti group; it never deletes SQLite data.

## Tests

The contained regression uses a fake sink and needs no Graphiti or Neo4j installation:

```bash
PYTHONPATH=src python scripts/graphiti_projection_test.py
```

It verifies scope isolation, deterministic episode IDs, failure-safe cursoring, resume, and group-local rebuild.
