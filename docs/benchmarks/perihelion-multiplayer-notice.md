# Multiplayer perihelion notice

AI9 and AI10 independently stopped at turn 60 on the same native `PERIHELION`
popup. Both decision frames correctly represented a mandatory interaction and
withheld an unreviewed multiplayer acknowledgement. Both campaigns were
quarantined with their native clients preserved and verified turn-59
checkpoints available.

The reviewed engine path commits `STATE_PERIHELION_ACTIVE` before opening the
one-button `PERIHELION` notice and immediately returns afterward. The inverse
`PERIHELIONENDS` path clears the same flag before its one-button notice. Neither
popup result selects the ecology transition. Both labels therefore join the
bounded reviewed-information allowlist. Their provider frame includes the event,
the current native flag, and an explicit statement that acknowledgement closes
local presentation only.

An isolated two-client DirectPlay replay used the exact AI9 turn-59 save with
SHA-256 `e0ce041e9b54f4356d8ae4ab2eaed6c9c11f603c992014942df56f524c5f1e03`.
It reached the real turn-60 notice, advertised the guarded acknowledgement and
structured ecology context, invoked the native popup callback once, and left the
modal stack. Both replicas retained `perihelion_active=true`; their complete unit
and base synchronization records were unchanged across dismissal. No production
campaign was resumed or mutated by this replay.

`PERIHELIONENDS` shares the audited one-button continuation path and passes the
compiled label contract. An eighty-year live replay was not performed. Public
deployment, verified recovery of both campaigns, and post-recovery progression
remain open gates.
