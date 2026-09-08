# Expansion prerequisite guidance — 2026-09-08

Added the approved paragraph under “Expand and Consolidate Deliberately”:
consider other known landmasses and sea settlements; distinguish missing
prerequisites from impossibility; verify a feasible path; record the next step
and blocker; revisit stalled plans without inventing mechanics or repeating
rejected actions without new evidence. No prescribed build order or base quota.

The claim inventory now includes that authored strategic principle and an
already-existing zero-mineral-surplus paragraph missing from the inventory.
Regenerated content fixtures reflect both paragraphs and current template hashes;
the existing production paragraph itself was not changed.

Validation: doctrine content/golden checks, doctrine integration (including
explicit recompilation and exact restart bytes), and strict prompt contracts
pass on host. The doctrine integration test also passes against installed control
image source. These are prompt-content/assembly checks, not evidence of improved
strategy or expansion in gameplay.

Main Docker project `smacx-agent` on port 8080 was redeployed with rebuilt portal,
control/MCP and harness/specialist images tagged `expansion-review`. This includes
merged PR67's spectator activity UI. Portal and control health checks pass; HTTP
8080 returns 200; the installed template contains the approved paragraph. Saved
harness profiles remain low reasoning with 262144 context. Data volumes were
preserved. There were no active sovereign runs and no game was started.

Base compose: `/home/dmint/Documents/ai/smacx-agent-2/runtime/deploy-pr48/compose.json`.
Deployment override:
`/home/dmint/Documents/ai/smacx-agent-astra/runtime/astra/main-expansion.override.yaml`.
Use both files with project `smacx-agent` to retain these image selections.

New games compile the updated doctrine. Existing campaigns retain frozen prompts
unless explicitly recompiled through the supported preparation flow. The timer
remains paused. Actual provider delivery and strategic effects await the next game.
