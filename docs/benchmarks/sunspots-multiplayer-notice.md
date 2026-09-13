# Multiplayer sunspot notices

AI9 stopped safely at turn 70 on native popup `SUNSPOTS`. The native snapshot
already reported `sunspot_duration=12`, but the managed interaction catalog
correctly withheld an acknowledgement because that multiplayer popup had not
been reviewed. Incident `incident-b30654cb207e4d7bb688f1ee27a7558c` preserved
the native clients and a verified recovery checkpoint.

The reviewed engine path assigns a positive `SunspotDuration`, formats the
duration, and then opens the one-button `SUNSPOTS` notice. Its return value is
unused. The inverse path decrements the duration and opens `NOMORESPOTS` only
after it reaches zero; that return value is also unused. Both labels therefore
join the exact reviewed-information allowlist. Their provider-facing interaction
frame reports the committed duration, active state, event name, and that the
effect was already completed before presentation.

An isolated two-client DirectPlay replay used the exact incident save with
SHA-256 `79f66aecd5c976410f88e2d14758f5729fbaae885a2020f68716233afa91efb7`.
Both clients received and guardedly acknowledged `SUNSPOTS`, left its modal
stack, and agreed on duration 12. The replay then advanced through the native
turn engine. Both clients received and guardedly acknowledged `NOMORESPOTS`,
left its modal stack, and agreed on duration 0. Helper commands tolerated only
documented multiplayer freshness races; the two target acknowledgements were
required to succeed on each client.

The compiled exact-label predicate, reviewed engine fingerprint, native bridge
cross-build, managed action path, decision recovery, opaque choice execution,
notification drain, and doctrine integration contracts pass. Public deployment,
verified AI9 recovery across the original popup, and sustained post-recovery
progress remain open gates.

Public deployment installed control image
`sha256:f7eaf6ffc2b333ea3550e5fc6acbc65248c1e5ca782c7c6245c86060c794be0f`
and worker image
`sha256:09d40c9c62e5325737dc52a1ad5b38ec487c99c526a7970b33c514624de6a297`.
AI9 completed supported 5/5 recovery from its verified turn-69 checkpoint on
prepared worker `31f20d357478-b829e813a01e-09d40c9c62e5`.

Both AI9 perspectives then observed current duration 12, captured
`sunspot_activity_started` before dismissal, and executed exactly one guarded
`choice-01` `acknowledge_popup` action. The capability incident cleared, both
sovereigns remained active, and checkpoint
`checkpoint-3e6d121b2022495e8968517184a8a1f7` was save-verified at turn 70.
This proves the repaired start notice is observed, represented,
provider-queryable, sovereign-expressible, executable, effect-verified, and
recovery-safe. Sustained progression and a production recurrence of the ending
notice remain pending.
