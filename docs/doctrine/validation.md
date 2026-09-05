# Reproducing the doctrine checkpoints

Run from the repository root. Provider addresses and the licensed local game-source path are supplied by the operator; no credentials or game installation files are committed. Review images use separate tags and do not replace the running `:dev` stack.

## Content and source

```bash
PYTHONPATH=src python3 scripts/doctrine_content_contract_test.py
PYTHONPATH=src python3 scripts/doctrine_integration_contract_test.py
PYTHONPATH=src python3 scripts/doctrine_engine_contract.py
```

The content test checks 21 cases, exact golden outputs, paragraph inventory hashes, placeholder completeness and excluded top-level inputs. `--write-goldens` is an explicit authoring action, never runtime behavior. Engine registration likewise requires an explicit source review before `--register-reviewed`.

## Review builds

```bash
docker build -f worker/Dockerfile -t smacx-agent-worker:doctrine-review .
docker build -f control_center/Dockerfile -t smacx-agent-control:doctrine-review .
docker build -f harness/Dockerfile -t smacx-agent-harness:doctrine-review .
```

The worker build cross-compiles and links the native bridge (39 build steps); these are build steps, not 39 game-mechanics tests. No .NET code changed in this follow-up.

## Control/container contracts

```bash
for test in control_plane_test control_http_test strict_prompt_contract_test capability_incident_contract_test provider_schema_budget_test; do
  docker run --rm --entrypoint /opt/smacx/mcp-venv/bin/python \
    -v "$PWD:/workspace:ro" -w /workspace -e PYTHONPATH=/workspace/src \
    smacx-agent-control:doctrine-review "scripts/$test.py"
done
docker run --rm --entrypoint /opt/smacx/mcp-venv/bin/python \
  -v "$PWD/scripts:/tests:ro" smacx-agent-control:doctrine-review \
  /tests/doctrine_integration_contract_test.py
docker run --rm --entrypoint /opt/hermes/.venv/bin/python \
  -v "$PWD/scripts:/tests:ro" smacx-agent-harness:doctrine-review \
  /tests/harness_context_policy_test.py
SMACX_TEST_HARNESS_IMAGE=smacx-agent-harness:doctrine-review \
  PYTHONPATH=src python3 scripts/hermes_provider_capture_test.py
```

The profile test reopens actual SQLite storage and re-prepares the profile, asserting byte-identical text and doctrine metadata. The strict test simulates Python site startup swallowing an exception: bad hashes and insufficient headroom must still fail at request assembly, never leave the upstream additive builder active. Provider capture uses the real pinned Hermes harness and a local capture endpoint, checking one exact system message, request-only runtime context, reasoning settings and gameplay/communication tool boundaries.

## Isolated native receipt

```bash
SMACX_TEST_GAME_SOURCE="$LICENSED_GAME_SOURCE" \
  PYTHONPATH=src python3 scripts/doctrine_native_live_test.py
```

This uses a temporary control database, owned disposable Docker worker and fresh Tiny/Librarian game. It validates the source through the normal manager, captures actual resolved public mechanics, compiles twice, and removes its owned worker/data/secrets in `finally`. It does not deploy the branch or mutate the user's active match.

## Live behavior and cost

`DOCTRINE_PROVIDER_BASE` is the provider origin without `/v1`; the measured provider served `Qwen3.8-27B`. The first 23-case run is preserved. After the documented narrow clarifications, B/C were repeated with unchanged corpus/settings and the byte-identical A requests reused. The Progenitor C case was then repeated after correcting its fixture to match the native adapter's potential eligibility and directed-research bit. There were 116 main-corpus live generations plus three supplemental responses.

```bash
PYTHONPATH=src python3 scripts/doctrine_behavior_eval.py \
  --base-url "$DOCTRINE_PROVIDER_BASE" --model Qwen3.8-27B \
  --output work/doctrine/behavior
PYTHONPATH=src python3 scripts/doctrine_behavior_eval.py \
  --base-url "$DOCTRINE_PROVIDER_BASE" --model Qwen3.8-27B \
  --corpus docs/doctrine/evaluation-supplement.json \
  --output work/doctrine/behavior-supplement
PYTHONPATH=src python3 scripts/doctrine_cost_test.py \
  --base-url "$DOCTRINE_PROVIDER_BASE" --model Qwen3.8-27B \
  --output work/doctrine/cost.json
docker run --rm --entrypoint /opt/smacx/mcp-venv/bin/python \
  -v "$PWD:/workspace:ro" -w /workspace -e PYTHONPATH=/workspace/src \
  -e SMACX_QWEN_TOKENIZE_URL="$DOCTRINE_PROVIDER_BASE/tokenize" \
  -e SMACX_QWEN_TOKENIZE_MODEL=Qwen3.8-27B \
  smacx-agent-control:doctrine-review scripts/provider_schema_budget_test.py
```

Run the cache probe without concurrent evaluation traffic. It checks global prefix-cache query deltas equal each request's own prompt tokens before attributing hit deltas. If that equality fails, the counters are contaminated and do not prove request-specific reuse. Output timing alone is not cache proof. Exact tokenizer counts for serialized tool schemas exclude provider-specific chat/tool framing.

The live ablation is read-only advisory inference, not autonomous gameplay execution. Review all raw responses and `evidence/behavior-review.json`; format validation alone does not establish correct mechanics. No benchmark uses a unique required build order or strategy.
