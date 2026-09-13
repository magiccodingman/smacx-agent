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
