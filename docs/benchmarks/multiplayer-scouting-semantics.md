# Multiplayer scouting and threat semantics

Live review of the two September 12 campaigns found that multiplayer Scouts had
no persistent Explore choice. Every explored tile therefore required another
provider decision and adjacent movement command. AI9 also kept one fresh Scout
at its base for about six turns while repeatedly describing a two-turn-reachable
Spore Launcher as imminent and counting Colony Pods and Formers as part of its
defense. AI10 showed the same action-surface limitation across multiple ready or
held Scouts. These observations establish a product gap and a behavioral symptom;
they do not establish that native Auto Explore will always be strategically best.

The repair exposes native Explore only for a fresh ready combat unit in LAN,
guards it in the multiplayer allowlist, synchronizes the authoritative vehicle
state, and exposes only activation while it persists. A focused real two-process
DirectPlay run passed assignment, peer convergence, activate-only enumeration,
cancellation, and peer convergence after cancellation. The engine retains path,
fog, movement-cost, encounter, and waking authority.

Base-defense calculation now distinguishes every co-located owned unit from
current combat-capable defenders, current noncombat units, and units whose role
evidence is stale or unknown. Minimum visible-contact ETA remains a lower-bound
reachability result, not an attack forecast. Observed hit-point loss has no
attributed cause unless a separate current native event supplies it.

Focused checks passed: runtime spatial context, sovereign hardening, movement
mechanics, counterfactual mechanics, managed action paths, request-only runtime
context, fair-play hidden-state isolation, doctrine integration, reviewed engine
fingerprint, native cross-build, and the two-client Explore run. The broad LAN
suite encountered existing seed-sensitive failures in earlier unrelated combat,
diplomacy, and skip stages before a complete run; those exits are not counted as
Explore evidence. Installed-image delivery and autonomous strategic uptake remain
deployment gates.

Before deployment, AI9 reached turn 47 and stopped on four `invalid_choice`
submissions. The captured provider episode copied each fresh `decision_id`
exactly but generated four UUID-shaped `choice_id` values that did not occur
anywhere in the episode. Its current 11-choice Former menu used a different
random UUID for every choice and every recovery enumeration. Because the
short-lived decision already provides the scoped, one-use capability boundary,
choice handles are now bounded frame-local ordinals (`choice-01`, and so on).
The private command binding, exact paired-handle lookup, expiry, scope, revision,
single-use, native validation, journal, and four-failure stop remain unchanged.
Controlled decision-recovery and opaque-execution suites pass selection after a
fresh recovery frame and reject invented, expired, consumed, cross-scope, and
replayed handles. Installed provider uptake remains a deployment gate.

Deployment also exercised an ordinary parked restore across the doctrine change.
The worker correctly rebased and the doctrine check failed closed, but containment
produced an `operator_pause` that `retry-after-update` did not admit. The repaired
path accepts the exact operator-pause incident only when no unrelated incident is
active, passes that incident into verified recovery, refreshes the runtime, and
clears it only after recovery succeeds. Explicit portal doctrine-recompile
approval is still required. The focused incident regression passes; live AI10
recovery remains the installed gate.
