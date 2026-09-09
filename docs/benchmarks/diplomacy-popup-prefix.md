# Native popup prefix correction

Incident `incident-7a4b1cefd5924a4da453e767d0407298`, turn 10/year 2110,
blocked Peacekeepers on Zakharov's DEMANDBRIBE0 popup. The model requested
interaction choices and retried observation before reporting the missing adapter.
Spartans were correctly sleeping. Native workers were preserved by quarantine.
Diagnostic archive: smacx-gap-6a623f9025e244329ba21959edab6d35.zip.

DEMANDBRIBE has length 11, but the native matcher compared 12 characters;
numbered demands could not reach existing refuse/pay/counter choices or their
execution handler. LIBERATEBASE has length 12, but its notice matcher compared
11, also making it unreachable. Both now derive prefix lengths from literals.
No choice or native execution guard has been loosened.

Compiled native-predicate regression passes for numbered demand and liberation
labels and negative demand labels. A source audit checks all remaining literal
prefix lengths. Native worker Docker cross-build passes. Compiled worker fingerprint matches
the explicitly reviewed control manifest; doctrine integration contracts pass.
Shared control/worker/MCP/harness images are deployed; checkpoint recovery is
in progress.
These tests prove classification, not live payment/counteroffer effects.


Deployment completed with popup-prefix worker/control/harness images. Recovery
then failed closed on Spartan native vehicle-identity hash mismatch, before MCP
and sovereign startup. This is separate from the popup matcher; no identity
validation was bypassed. Operator pause returned containment_verified=true.
Campaign remains stopped. Live popup execution validation remains outstanding.
