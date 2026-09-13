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
