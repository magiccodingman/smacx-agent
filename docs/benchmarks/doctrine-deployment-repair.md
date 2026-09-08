# Doctrine deployment repair (2026-09-08)

## Failure and cause

New match `match-8a9962adfe714243a082a0731443a6d1` launched native gameplay but
could not start its AI. An authenticated native `doctrine_context` read reported
engine fingerprint `737fc9e3e38fa9821c96b73b33dc646eb51b07528b96d24fe2b0e71299ff7f20`.
The control image's reviewed contract expected
`e5032701b0a6849914cc2bdec87a99611a4f9f54406b9eeebf043f55d02d19f5`.
Its public rules file matched the allowlist. The actual rejection was
`doctrine_unreviewed_engine_build`, before context persistence/profile creation.
The generic error incorrectly suggested explicit prompt recompilation.

The current source and committed compatibility registration already agree.
The deployed worker image was stale; no new engine hash was approved and no
compatibility guard was relaxed. The repaired worker is built from that source.

Repeated supervision treated the missing AI run as a reason to retry. Normal
end maintenance waited for a verified checkpoint which this failed startup
could not supply. The repair latches a doctrine failure as a portal error before
requesting existing operator containment, enabling the failed-campaign end path.

## Acceptance chain

- Real old worker DLL versus control-image registration: new deployment probe
  rejects the mismatch (exit 2). Rebuilt worker passes. The standard launcher
  runs this check after builds, before replacing services. It checks actual
  fingerprint bytes in the compiled DLL, not just matching image tags.
- Isolated running native game: authenticated UI-thread receipt reported the
  expected engine fingerprint and compatible public rules. Context was accepted,
  doctrine compiled, and a repeated native read produced identical context.
- That actual native context was composed into the managed system prompt;
  persisted-prompt reuse returned exact bytes. No provider inference was launched.
- Doctrine integration checks retain rejection of unreviewed engines, modified
  rules, invalid scope and unauthorized recompilation; error messages now
  distinguish engine mismatch, missing context and saved-contract changes.
- Portal suite: 87 passed. New real SQLite/HTTP-adapter test verifies the error
  is durable before containment, one pause request occurs, and another call
  does not retry AI startup or duplicate the failure event.
- User's failed campaign: authenticated operator pause verified native/collector
  containment with zero sovereign processes. Authenticated failed-campaign end
  returned completed and removed its worker. History was preserved.

The isolated native fixture uses a small Librarian game with the loaded faction;
it is not a full five-seat portal launch or a completed sovereign game. Operator
containment and failed-campaign termination used real Docker resources. HTTPS,
provider reasoning settings, fair-play and journal authority are unchanged.

## Deployment

Main-stack rollout uses explicit `doctrine-aligned` worker/control/portal image
references, retaining the previous expansion harness and LAN HTTPS overrides.
Control and portal are healthy after rollout. Read-back confirms worker
`smacx-agent-worker:doctrine-aligned`, MCP/control
`smacx-agent-control:doctrine-aligned`, and unchanged harness
`smacx-agent-harness:expansion-review`. The deployed image pair passes the
artifact probe, localhost health returns 200 and LAN HTTP still redirects to
HTTPS. Authenticated read-back confirms the failed campaign remains completed.
The isolated validation worker/volumes and its prepared image were cleaned up.
No replacement user campaign is started; the owner will create the next game.

Local deployment appends `runtime/astra/main-doctrine.override.yaml` after the
expansion and LAN HTTPS overrides in the Astra checkout. All persisted gameplay
and portal data remain on the existing volumes. Other installations were not
changed. There was no DB reset or prompt recompilation override.
