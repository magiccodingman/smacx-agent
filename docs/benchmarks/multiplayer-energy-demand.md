# Multiplayer energy demand — turn 7

The preserved two-agent campaign stopped at `DEMANDBRIBE1`. Every offered
response (`reject`, `accept`, `counter`) was rejected before native mutation by
`multiplayer_command_not_validated`. This was a semantic capability incident,
not a native process crash. Both peers and MCP collectors were quarantined.

The preserved turn-7 save SHA-256 is
`2a9b12d39a13e8a51698254d2ad9743f962b43cc5b7bc04f2e6205e8bbc6371f`, matching
the published paired checkpoint. Private diagnostic packet SHA-256:
`69182ae29d842ec6535fbfa1c991f853321d56dcf84a365b6109cb7ce6b995fd`.

The proposed bridge change admits only the active native-AI demand family:
DEMANDBRIBE reject/accept/counter, with a valid
nonlocal, nonhuman counterpart and live native popup. It rechecks the quoted
payment against current treasury and leaves simulation changes to the native
dialog continuation. Reviewed BULLY0–6 announcements also become acknowledgeable,
so refusing the demand does not strand the native war announcement. Receipts explicitly require subsequent treasury and
relationship observation; submission does not certify those effects.

Validation:

- Compiled production gate and payment guard pass rejected-context, response,
  exact-affordability, negative-price, and insufficient-credit cases.
- Popup prefix contract passes.
- Rebuilt worker cross-compilation passes.
- Choice transport regression: 16 cases pass.
- Final-build two-client replay: reject, accept and counter all pass. Both peers
  agree on all faction state, exact energy transfer (0/50/20 credits), unchanged
  units/bases, and resulting Vendetta on refusal. The refusal's BULLY5 notice
  closes; invalid and stale duplicate submissions are rejected. See
  [native results](multiplayer-energy-demand.json).
- Semantic choice binding and doctrine integration contracts pass.
- The built control approval and worker DLL fingerprint match.
- Production recovery succeeds on the original turn-7 paired checkpoint. It is
  marked `restore_tested` on `smacx-agent-worker:energy-demand`; both worker/MCP
  pairs are healthy, both sovereign processes are active, and the operator report
  has no active incidents or health reasons. The portal completes recovery only
  after both sovereigns are running. Autonomous reuse of a demand response is
  still unobserved; the mechanics proof above is the isolated replay.

One isolated worker startup stalled at the Firaxis splash and timed out. The same
image succeeded on a fresh worker retry. That failure is retained in private
runtime evidence; its cause has not been established. It is not reported as a
fixed startup defect.

The legacy `diplomacy_test.py` requires a configured native bridge; running it in
an MCP-only container is not a native validation and failed for the missing
bridge token. No gameplay effect is claimed from that attempt.

The separate WEASELOUT ultimatum is not newly admitted to multiplayer: its
continuation has not been demonstrated in the live two-client replay. The
affordability recheck also applies to its existing single-player response path.
