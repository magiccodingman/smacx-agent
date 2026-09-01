# Optional Graphiti political memory

Graphiti with FalkorDB is a derived temporal relationship index. It is never the source of
truth. The complete scoped event ledger, chat, facts, beliefs, relationships,
commitments, goals, and summaries remain in SQLite, and gameplay continues if
Graphiti, FalkorDB, embeddings, or its extraction model is unavailable.

## What is projected

The projector intentionally does **not** mirror the event stream. It queues only
durable social and strategic material:

- delivered global/private/group chat;
- diplomacy, Council activity, incidents, recovery history, and high-importance
  lifecycle events;
- relationship, commitment, goal, belief, and summary updates; and
- explicitly categorized political, promise, betrayal, threat, alliance,
  history, strategy, or territory facts.

Routine unit moves, raw snapshots, tool calls, wiki retrievals, and private
reasoning are skipped while their SQLite cursor still advances. Writes happen
asynchronously in the projector and never delay the gameplay agent.

## Recall behavior

Every group is derived internally from the exact
`(installation, match, agent, perspective)` identity. Neither a model nor a
caller may supply a wider namespace. The MCP automatically performs a small,
bounded recall only when new chat or a diplomatic/interaction decision makes
history relevant. The explicit `smac_memory(action="graph_recall", query=...)`
path exists for a deliberate deeper political question.

Recall waits because its answer is needed, but it has strict time/result limits
and fails open. SQLite and fresh native state remain authoritative. Graph facts
are labelled as fallible historical context.

## Models and embeddings

Graphiti is disabled until an administrator selects an independent extraction
AI profile under **Operations & recovery**. A fast non-thinking profile is
normally enough. Changing that profile reloads the projector without touching a
gameplay seat or carrying over the previous provider secret.

Graphiti uses the installation's one shared embedding configuration:

- by default, the local knowledge service exposes one combined 2,048-dimension
  vector through an internal OpenAI-compatible endpoint while
  SemanticKnowledge retains native multi-chunk embeddings;
- an administrator may instead select an external embedding provider/model,
  exact dimension, and stable embedding-space ID; or
- disabling embeddings disables Graphiti and semantic rules together.

Provider keys stay in the Control Center vault. The projector reads its selected
profile and secret from the read-only control volume; endpoint metadata and keys
are no longer duplicated in Compose environment variables.

## Start the optional backend

Run:

```bash
./scripts/graphiti-up.sh
```

On first start the script generates a private random FalkorDB credential in
the ignored `runtime/graphiti-secrets` directory with restrictive owner/group
permissions. The projector receives only that file's host group and remains a
non-root container.
Set `SMACX_GRAPHITI_SECRET_DIR` to keep those files elsewhere. If an existing
deployment loses its credential, restore that file alongside the
persistent graph volume rather than generating an unrelated replacement.

FalkorDB and the recall endpoint have no published host ports. In the portal:

1. create or choose a dedicated AI profile;
2. open **Administration → Operations & recovery**;
3. select it as the Graphiti extraction profile, which enables Graphiti
   immediately.

Turning Graphiti off clears that extraction-profile selection. Select a profile
again whenever Graphiti should be re-enabled. Deactivating the selected AI
profile performs the same cleanup automatically; deactivating any other profile
leaves Graphiti untouched.

Each lobby may still opt out.

FalkorDB is server-only, internal to the Compose network, and persistent. Its
native multi-graph routing gives each exact SMACX fair-play scope a different
graph instead of relying on model-supplied filters inside a shared graph.

## Failure, rebuild, and retention

Stable UUIDv5 episode IDs make retries deterministic. The cursor advances only
after a selected episode succeeds; an extraction error remains replayable.
Rebuild clears one exact derived group and replays its selected SQLite events.
Runtime heartbeat, projected/failure counts, selected profile, and
embedding mode are visible in the portal.

Historical Graphiti group and Hermes conversation retention/garbage collection
is intentionally deferred. It is tracked as future operations work; current
match/event telemetry is small and should not be discarded implicitly.

## Tests

```bash
PYTHONPATH=src python3 scripts/graphiti_projection_test.py
PYTHONPATH=src python3 scripts/graphiti_worker_contract_test.py
docker compose build graphiti-projector
```

The tests cover perspective isolation, curated-event exclusion, failure-safe
cursors, deterministic IDs, exact-group rebuild, required extraction profile,
shared embedding resolution, and observable fail-open behavior.

The reference live contract additionally projected one synthetic diplomacy
episode twice, proved that its deterministic UUID produced one episode,
recalled the extracted treaty fact through the shared 2,048-dimension ONNX
facade, preserved its in-game validity year, and removed only that temporary
scope graph afterward. No full gameplay claim depends on this synthetic test.

See the [Graphiti project](https://github.com/getzep/graphiti) and
[FalkorDB Docker documentation](https://docs.falkordb.com/operations/docker).
