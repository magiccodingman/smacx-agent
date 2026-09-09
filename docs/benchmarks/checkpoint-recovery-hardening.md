# Checkpoint recovery hardening

## Layer 1: retention, fallback, containment

The turn-9 paired checkpoint failed Spartan identity import during recovery of
the turn-10 diplomacy incident. Its older paired archives had been deleted.
New capture retains the latest three complete pairs plus the newest successfully
restored pair. Five native staging slots avoid overwriting retained saves, including
failed capture retries. World snapshot pins and Hermes archives use the same
retention set. Legacy checkpoint metadata remains readable.

`verification.status=save_verified` describes capture/digest verification;
`restore_tested` is recorded only after all native identities and MCP startup
succeed. It does not claim strategic correctness or every future runtime version.
Recovery tries retained pairs in order only for save integrity or identity failure.
Each failed attempt is contained before fallback; exhausting the set leaves error
state and no provider continuation. Other failures do not trigger blind retries.

Deterministic tests: checkpoint slot atomicity, five-slot bound, snapshot ownership,
recovery observation ordering, bounded fallback, failed-all containment, and
operator-pause recovery guard pass. Multi-seat operator pauses now authorize a
match-wide recovery with an exact active incident ID; automatic or stale-ID
recovery remains blocked. Controlled live native reproduction is still pending.

## Layer 2: private diagnostics

New native exports include ordered fields used by the existing validation hash.
Identity mismatches record up to 32 differing fields in operator-only metadata.
The strict hash/handle import guard is unchanged. Old capsules without fields
remain supported but cannot reveal which historical field differed.

## Layer 3: native cause and cross-peer capture proof

A controlled reload of the preserved turn-9 save produced host hash
12053438051710929130, exactly matching its recorded capsule. The restored peer
hash was 10514326628721837733. The two complete native field vectors were identical
except for perspective ID; recomputing the host vector with perspective 2 produced
that same peer hash. The checkpoint's original peer hash was 407252985726212019.
Therefore the recorded peer state disagreed with the saved host state, despite
three locally stable observations at the same turn/year. The old capsule does not
contain fields, so the particular historical differing field remains unknown.

Capture now validates each private field vector against its hash, then requires
cross-peer equality of turn and every vehicle field, excluding only the expected
perspective difference. Divergent peers defer capture without publishing a new
checkpoint or discarding the previous pair. Native state and handle vectors are
rechecked after saving and after AI archive capture. The deferred-capture reason
and bounded differences are operator-only. Capture holds the lifecycle lock shared
with recovery. Aborted captures reclaim unreferenced archives and snapshot pins.

Actual capture orchestration regression: two individually stable peers with a
vehicle-position disagreement never reach the save command. Perspective-only
differences pass. Compiled native hash/field tests prove the private field vector
reproduces the unchanged hash exactly. UI now calls these paired checkpoints;
operator reports distinguish capture verification from historical restore tests.

## Layer 4: controlled native round-trip acceptance

`native_multiplayer_checkpoint_roundtrip_test.py` passed on the two-seat turn-9
native game: load source → acknowledge only informational startup notices → wait
for stability → capture and compare both identity capsules → guarded save to a
separate test slot → verify digest → restart both replicas → import exact native
hashes/handle vectors → acknowledge startup notices → verify playable turn/wait
states. No collectors or sovereigns started. Original recovery metadata was not
replaced and no legacy identity guard was bypassed. This demonstrates new native
capsule round-trip correctness, not repair of the original inconsistent capsule.

Memory-pairing, compressed/plain save digest, snapshot-pin retention, fallback,
and recovery observation-order tests pass separately. Portal tests: 87/87.
