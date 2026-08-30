# Optional Graphiti projection

Graphiti is a derived temporal index, never the SMACX Agent source of truth.
The authoritative event stream, chat ledger, facts, beliefs, relationships,
commitments, goals, and summaries remain in SQLite. Gameplay and SQLite recall
continue if Graphiti, Neo4j, an embedding endpoint, or an extraction model is
stopped or unhealthy.

## Delivered integration

The optional `graphiti` Compose profile provides:

- a digest-pinned Neo4j Community container with no published host ports;
- an internal `graphiti-core==0.29.3` projector with a read-only root,
  all Linux capabilities dropped, and bounded per-perspective work;
- Docker file secrets for Neo4j, the extraction model, and embeddings;
- durable health, failure counts, and exact-scope rebuild requests in the
  canonical pre-release SQLite schema;
- enable/disable, health, and exact-perspective rebuild controls in the
  authenticated Control Center; and
- deterministic replay and adversarial scope-isolation regressions.

New lobbies prefer Graphiti by default, but projection remains inert until an
administrator configures both compatible endpoints, starts the optional stack,
and the projector reports healthy. A chat endpoint that returns
404 for `/embeddings` is not sufficient. The current reference Qwen deployment
has that limitation, so SQLite/BM25 is the correct production path there.

## Fair-play isolation

Every episode group is derived internally as:

```text
smacx:{installation_id}:{match_id}:{agent_id}:{perspective_id}
```

The agent cannot supply or widen this namespace. The projector reads only
events already constrained to that exact scope. Stable UUIDv5 episode IDs make
retries deterministic. A cursor advances only after `add_episode` succeeds;
errors are recorded without skipping the failed event. Rebuild clears only the
selected derived group and replays immutable SQLite events.

Chat is tagged as untrusted in-game speech. Extraction instructions distinguish
observations, player claims, and the agent's own beliefs and prohibit hidden
state inference. Graphiti has no agent-facing HTTP or MCP write surface.

## Configure secrets

Create a private directory outside version control (the default path is
`runtime/graphiti-secrets`) containing four non-empty mode-0600 files:

```text
neo4j_auth       neo4j/a-long-random-password
neo4j_password   a-long-random-password
llm_api_key      extraction-provider-key-or-local
embed_api_key    embedding-provider-key-or-local
```

`neo4j_auth` uses the official Neo4j `username/password` file format;
`neo4j_password` contains only the matching password for the projector. Secret
values are not placed in Compose environment variables, container commands,
the browser, or the database.

Export endpoint metadata, then start the optional profile:

```bash
export SMACX_GRAPHITI_LLM_BASE_URL=http://model-host:8000/v1
export SMACX_GRAPHITI_LLM_MODEL=structured-output-model
export SMACX_GRAPHITI_EMBED_BASE_URL=http://embedding-host:8000/v1
export SMACX_GRAPHITI_EMBED_MODEL=embedding-model
export SMACX_GRAPHITI_EMBED_DIM=1024
./scripts/graphiti-up.sh
```

Set `SMACX_GRAPHITI_SECRET_DIR` if the files are elsewhere. The script validates
the four secret files and matching Neo4j credentials before starting anything.
It leaves the ordinary Control Center up. Enable projection globally only after
the projector is healthy. The per-match checkbox can still disable projection
for an experiment without changing the global service.

## Evaluation result

The packaged stack was built and exercised against the real pinned Neo4j image.
Neo4j and the disabled projector reached healthy state with no published ports;
container inspection confirmed secret paths rather than values. A deliberate
event projection to unavailable model endpoints made only the projector
`degraded`, retained the SQLite event, and exposed the failure through health
state. This proves deployment and failure isolation, not decision-quality gain.

Graphiti quality still depends on the selected structured-output and embedding
models. Keep it optional and compare recall/evaluation results before enabling
it by default for a deployment. See the [official Graphiti repository](https://github.com/getzep/graphiti)
and [Neo4j Docker secrets documentation](https://neo4j.com/docs/operations-manual/current/docker/docker-compose-standalone/).

## Tests

```bash
PYTHONPATH=src python scripts/graphiti_projection_test.py
PYTHONPATH=src python scripts/graphiti_worker_contract_test.py
```

The first verifies event isolation, deterministic IDs, failure-safe cursoring,
resume, and group-local rebuild. The second verifies fail-inert service policy,
Control Center state, exact-scope rebuild guards, observable failure isolation,
file-secret loading, and canonical schema revision 1.
