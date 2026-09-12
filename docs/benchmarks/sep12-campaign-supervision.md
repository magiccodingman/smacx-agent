# September 12 campaign supervision repair

Preserved operator packets identify two different failures after successful recovery:

- AI9 instruct, turn 21: incident `incident-14829baa6d7e4a3f92d611998a706ba7`. Three non-progressing clean yields correctly stopped repetition. The last decision explicitly offered End turn, with own/current faction 2, phase turn and zero ready units. The model nevertheless described a foreign-turn wait. Runtime context also correctly reported turn; this is model misinterpretation, not evidence of native deadlock. Current runtime evidence now includes both faction IDs and explicitly distinguishes no ready units from foreign ownership and verbal WAITING from native end turn. No action is chosen automatically.
- AI10 low thinking, turn 10: incident `incident-8e32a10a97124218a33994547820ac59`. A live streaming request was interrupted after 372 seconds without a gameplay effect, two completed calls and 2,133 output tokens. Its stream timestamp exceeded the supervisor's pre-telemetry clock. The telemetry helper reads later than that clock; production reconciliation now validates at telemetry-read completion. Finite/future timestamp validation, the fixed request identity, silence deadline, absolute generation bound and gameplay-progress clock remain intact.

Validation: production watchdog regression simulates telemetry advancing during the read and proves the request is admitted without resetting gameplay progress. Existing hard-deadline, request replacement, silence, failure, clean-yield, foreign-wait and semantic-progress contracts pass. Runtime assembly tests preserve authoritative own-turn IDs with zero ready units and existing tier/attention/continuity budgets. Live recovery and sustained progression are separate acceptance gates, to be recorded after deployment. This does not establish that the instruct model will always follow the corrected guidance.

Local raw diagnostic packets remain in ignored runtime/astra/ai9-sep12.json and ai10-sep12.json; private transcripts are not committed.

## Recovery-route follow-up

Live recovery found a third defect: the portal accepts clean-yield incidents for retry-after-update, while the worker manager rejected them as not a capability incident. The exact clean-yield kind is now admitted through that same verified-image/checkpoint restore path. Tests prove failed restores retain the incident, successful restores clear it only afterward, repeat requests are idempotent, and unrelated incident kinds/gaps remain protected. Recovery observation-order tests pass for identity failures, digest failures, staged slots and LAN/single-player paths. AI10 recovery proceeded through the existing capability route; AI9 requires this follow-up deployment.

## Deployment and recovery read-back

On September 12 at 14:30 UTC, both portal recovery operations completed and both campaigns returned to running. AI9 restored turn 21 with two live sovereign processes and healthy worker/MCP state; AI10 restored the paired turn-9 checkpoint and restarted its two sovereigns (foreign-turn sleep can subsequently remove a process normally). Deployed control source hashes match the tested supervisor, runtime context and recovery manager. Only the public smacx-agent control/supervisor services and campaign recovery resources were replaced; the readiness stack was untouched.

A thread heartbeat now checks every 15 minutes. Sustained stability is not yet claimed: acceptance requires three consecutive healthy checks with real gameplay advancement across both campaigns and no unresolved errors. Model adherence to own-turn guidance remains a live gate. Baselines/check results are kept in ignored runtime/astra/sep12-stability.json.

## First 15-minute check: not stable yet

AI10 advanced from restored turn 9 to turn 11 at the check, then turn 12 during investigation, without a new incident. AI9 raised `gap-8bf1295e8d7e4196ab8dca243c57dfa3` after four identical summary calls in a verified foreign-turn wait. Captured provider requests contain the prior saved/already-persisted receipts and growing history: this is not missing tool history. The model repeated its narration and attempted an already-consumed automatic popup choice.

Two bounded corrections follow. Empty recovery frames retain their wait/handoff directive without also telling the sovereign to select nonexistent choices. At the memory repetition threshold, a fresh same-match/session native foreign-wait observation delegates containment to existing bounded per-agent foreign-wait suspension instead of quarantining the whole campaign; this does not reset progress or permit unbounded provider activity. Own-turn, unknown, interaction and session-mismatch cases retain the hard circuit. Real journal/facade tests, decision-cache recovery tests and supervisor foreign-wait suspension/wake tests pass. Improved live behavior remains pending; stability counter resets to zero.

First-check deployment completed at 14:56 UTC: both campaigns running on refreshed MCP images, AI9 with two live sovereigns after memory-graph restore completed, AI10 resumed from verified turn 12. AI10's first park reported a transient control connection failure; the checkpoint endpoint subsequently returned success and a second verified park completed. No checkpoint mismatch was observed. The 15-minute loop remains active with zero healthy checks after this repair; model compliance is still not established.
