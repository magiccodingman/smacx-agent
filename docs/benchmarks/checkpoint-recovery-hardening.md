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
