# Autonomous-play benchmarks

This directory holds small, sanitized evidence reports for repeatable agent
runtime regressions. It is not a validation diary and never contains game
files, screenshots, model conversations, chat, prompts, responses, reasoning,
tool arguments, provider addresses, credentials, or private host details.

- [2026-09-05 focused PR #48 semantic corrections](2026-09-05-peer-review-corrections.md)

- [2026-09-05 H1–H5 publication, episode, journal and inspection acceptance](2026-09-05-h-review.md)
- [2026-09-05 final hostile-review gates and collector distribution](2026-09-05-final-hostile-review.md)

## Required test conditions

- Use a test-owned match and isolated managed workers.
- Set the native multiplayer turn clock to **None**.
- Record the game profile, participant mix, AI profile intent, target duration,
  code revision, and explicit pass criteria.
- Treat only journaled agent actions with observed native before/after progress
  as causal success. A turn number moving by itself is insufficient.
- Preserve failures in aggregate form: malformed call counts, error codes,
  repetition circuits, supervision incidents, and missing handoffs.

## Report artifacts

`scripts/agent_simulation_report.py` verifies each perspective's hash chain and
summarizes causal actions, turn advancement, stale rebases, checkpoints,
incidents, and portal turn telemetry.

`scripts/hermes_session_audit.py` reads one Hermes state database and emits only
counts: sessions, token/API totals, tool names, safe error-code labels, malformed
records, exact repetition runs, compression health, and bounded `TURN HANDOFF`
compliance.

Commit the generated JSON under `results/` alongside a short Markdown report
that names the exact Git commit, conditions, observed limitations, and commands
needed to reproduce it. Do not hand-edit generated JSON.

Published reports:

- [2026-09-05 integrated sovereign acceptance](2026-09-05-integrated-acceptance.md)
- [2026-09-05 counterfactual mechanics](2026-09-05-counterfactual.md)
- [2026-09-04 intent and semantic attention](2026-09-04-intent-attention.md)
- [2026-09-04 managed action paths](2026-09-04-managed-action-paths.md)
- [2026-09-04 sovereign correctness](2026-09-04-sovereign-correctness.md)

- [2026-09-04 geographic semantics and hierarchical LOD acceptance](2026-09-04-geographic-semantics.md)
- [2026-09-03 strategic world and provider-context acceptance](2026-09-03-strategic-world-rebuild.md)
- [2026-09-02 bounded runtime no-timer smoke test](2026-09-02-bounded-runtime.md)
- [2026-09-02 AI-2 turn-transition regression](2026-09-02-ai2-turn-transition-regression.md)

Content-free machine-readable acceptance artifacts include the
[live disposable specialist run](results/2026-09-03-specialist-live.json) and
[live prefix-cache measurement](results/2026-09-03-prefix-cache-live.json), plus
the [native demanded orbital-receipt run](results/2026-09-04-semantic-airdrop-live.json).

## Interpreting results

A smoke test passes only when the agent—not a native timer—causes multiple
turns of progress, every executed operation came from an opaque legal choice,
no unresolved repetition/capability circuit remains, and turn handoffs remain
bounded. Strategy quality is reported separately from runtime correctness.

Token totals are cumulative provider work, not context-window occupancy.
Compare per-session and per-turn deltas rather than dividing a lifetime total by
the latest game turn. Large prompt totals combined with low semantic progress
indicate repeated context transmission or a loop and require investigation.
