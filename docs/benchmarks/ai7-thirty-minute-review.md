# AI - 7 early live review

Capture: 2026-09-10 01:18 UTC, match `match-542a028ffab1c5dc9e868abd71ac87cd`,
turn 3/year 2103. Bounded diagnostics retained locally at `/tmp/ai7-review.zip`.
No new generation, gameplay mutations, repairs or deployment during this review.
The requested timer was disabled. Workers and MCP pairs are healthy; no incidents.
A waiting sovereign resumed during the review; the subsequent supervisor sample
reported usage baseline pending, so continuous health is not claimed from one sample.

## Provider measurements

Actual response usage, not the older incomplete tokenizer. Times are completed
provider responses, not whole faction turns. Different maps and server load mean
these comparisons are descriptive rather than causal.

| Seat | Completed responses AI5 / AI7 | Median seconds AI5 / AI7 | Median input AI5 / AI7 | Median output AI5 / AI7 | Responses >=60s AI5 / AI7 |
|---|---:|---:|---:|---:|---:|
| Spartans | 32 / 40 | 42.50 / 20.40 | 46,331.5 / 47,767 | 902 / 400 | 10 / 0 |
| Peacekeepers | 40 / 40 | 29.68 / 30.08 | 54,701 / 48,842 | 867 / 936 | 4 / 4 |

AI7 totals: 2249.97 provider seconds versus 33.95 tool-batch seconds. About
98.5% of these measured components is provider time, excluding startup/context
assembly and inter-faction waits. Ordinary movement medians are 0.35–0.37s;
the maximum execution batch is 8.32s. One request was still streaming at capture.
All 81 requests use low reasoning and contain at most one meaningful reasoning
segment (AI5 maxima were 11/21). This establishes deployed retention, not causal
quality improvement. Spartan output totals fell to 22,742 over 40 responses;
Peacekeeper output was 49,088 over 40, close to AI5's 49,921 over 40.

## Behaviour and guarded recovery

All 81 runtime envelopes contain acknowledgement arguments. All 12 lease-only
acknowledgements succeeded, including two empty batches; no cursor confusion.
Seven memory writes: five succeeded, one stale guard and one invalid reference.
The stale guard followed a state change; explicit retry with the supplied fresh
guard succeeded. The invalid plan was retried abstractly and succeeded, but
lost its concrete participant binding unnecessarily. One native pod move was
rejected with unknown cause and did not repeat into a failure loop; later evidence
records collection while leaving its random reward unknown.

The Spartan has a Colony Pod under production (9/33 at the latest captured
opening), an active Mine order and scouting units. The Peacekeeper retained its
farm intent across turns and moved a new Former to a non-rocky tile, but has two
Formers with no active terraforming order in the last captured force summary.
This is mid-turn evidence, not proof the farm will be completed or ignored.

## Remaining issues

The Peacekeeper's slowest response took 116.53s and emitted 6224 tokens, including
5901 reasoning tokens. It repeatedly debated attacking a Mind Worm versus
exploring, then alternated between exploration destinations without new evidence.
Shorter retained reasoning does not eliminate repeated reasoning within one response.

Two concrete semantic problems merit follow-up:

* A plan put a prose condition in `dependencies`, which accepts references.
  `SmacxStore` validates these with `_require_id(..., "plan_reference")`.
  The generic `invalid_plan_reference` caused the model to blame its valid-looking
  unit participant and remove the binding. Field-specific error guidance and an
  explicit dependencies schema would support a more precise repair.
* The model used `base_facility_bonus: 10` as combat-defense evidence. This is a
  repair modifier (`smacx_mechanics.py` repair calculation), not evidence of a
  combat bonus. Its garrison was observed, but the asserted defensive strength
  was not established by that number. Review provider-facing repair/defense
  labeling and units before attributing this wholly to model weakness.

No incident required pausing. Leave the match running for the user. Acceptance:
attention delivery/use and reasoning-retention pass; general speed improvement,
strategic equivalence and complete semantic correctness remain open.
