# Testing

The public no-coordinate contract has a dedicated contained regression. It recursively inspects every main fair-play state surface, citizen and unit choices, tile listings, and deferred-action records; rejects a direct x/y-only mutation; and proves adjacent movement plus persistent routing by opaque tile ID:

```bash
scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/tile_id_semantics_test.py
```

Never run development playthroughs on the host display. The wrapper below creates Xephyr `:99`, starts exactly one test, enforces a timeout, and terminates only the isolated game afterward.

```bash
cd /path/to/smacx-agent
SMACX_TEST_TIMEOUT=220 scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/semantic_playthrough.py \
  --target-turn 8 --deadline 190
```

Focused regressions:

```bash
PYTHONPATH=src python3 scripts/operations_contract_test.py
PYTHONPATH=src python3 scripts/harness_manager_contract_test.py
PYTHONPATH=src python3 scripts/reference_corpus_test.py
PYTHONPATH=src "$HOME/.hermes/hermes-agent/venv/bin/python" scripts/mcp_command_schema_test.py
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/base_management_test.py
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/base_citizen_test.py
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/unit_lifecycle_test.py
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/hurry_production_test.py
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/social_engineering_test.py
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/save_load_test.py
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/end_turn_guard_test.py
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/skip_all_ready_test.py
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/ready_unit_refs_test.py
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/settlement_status_test.py
SMACX_TEST_TIMEOUT=120 scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/semantic_soak.py --target-turn 1 --deadline 90
```

`operations_contract_test.py` proves one canonical schema, exactly-once due
schedule claims, immutable completed-run history, consistent SQLite snapshots,
secret backup/restore, automatic pre-restore rollback, and hash tamper
detection. The opt-in `control_worker_mcp_live_test.py` additionally creates a
real bridge checkpoint, archives the active worker volume, kills the native
container, and waits for the supervisor to restore turn 1 with a new MCP
sidecar. Its certified result includes
`live_worker_volume_backup_verified=true` and
`native_crash_recovered_without_ui=true`.

`harness_manager_contract_test.py` proves the official image digest pin,
`key_env`-only profile configuration, absence of key material from Docker
inspect-visible configuration, purpose-volume injection, secret rotation on
reprovision, and the read-only/capability-dropped semantic runtime. The opt-in
`harness_backup_live_test.py` creates a real UID-10000 private profile volume
inside a disposable Control Center data volume and verifies its conversation
archive and hash manifest.

The real two-client LAN regression creates two isolated game processes and a nested join display, then drives only the semantic bridge. It covers exact-session discovery/join, guarded lobby configuration/readiness/start, startup decisions, paired human Treaty negotiation, focused technology/energy transfers, the post-diplomacy settlement phase, combat, turn transfer, synchronized strategy/base state, and two-way chat:

```bash
PYTHONPATH=src python3 scripts/lan_two_client_join_test.py
SMACX_TEST_HUMAN_RELATIONSHIP=pact PYTHONPATH=src python3 scripts/lan_two_client_join_test.py
SMACX_TEST_HUMAN_RELATIONSHIP=truce PYTHONPATH=src python3 scripts/lan_two_client_join_test.py
SMACX_TEST_HUMAN_TRADE=technology PYTHONPATH=src python3 scripts/lan_two_client_join_test.py
SMACX_TEST_HUMAN_TRADE=energy PYTHONPATH=src python3 scripts/lan_two_client_join_test.py
SMACX_TEST_MULTIPLAYER_AI_CONTACT=1 PYTHONPATH=src python3 scripts/lan_two_client_join_test.py
```

After the human-diplomacy packet ordering was implemented, fresh-process runs pass every asserted family with `pixels_or_ui_input_used=false`. The Treaty creator atomically composes, network-drains, verifies, and commits first; the peer observes the exact clause and accepts; both processes report symmetric Treaty status before the test crosses the guarded engine-settlement boundary and executes combat. The regression also tolerates only the stock protocol's observed `DIPLOCLOSE` collision by returning to a fresh turn and re-enumerating the commlink; stale commands are never replayed.

The focused Pact and Truce modes begin fresh matches with symmetric contained Treaty or Vendetta prerequisites, respectively. They prove native clause types 2 and 4, peer-visible structured clauses, acceptance in both paired windows, symmetric final relationship bits, and the post-transmission phase boundary. The combat fixture now clears both movement counters and native `VSTATE_HAS_MOVED`/automation readiness flags and selects a living AI that actually owns a vehicle, eliminating its former randomized no-op/empty-owner cases.

The focused technology mode gives only the host one exact named technology, atomically commits native clause type 0, proves the recipient sees the identical structured clause before its acceptance, advances the resulting LAN semantic presentation queue, and verifies converged recipient ownership while the donor retains its research. Three consecutive fresh two-client runs passed after the atomic proposal and game-thread dispatch fixes. The energy mode begins both process copies at 500/100 credits, advertises a single bounded amount descriptor, proves an attempted 501-credit overdraw fails before mutation, atomically commits native clause type 1 for exactly 75 credits, and verifies both clients converge to 425/175. Both report `pixels_or_ui_input_used=false`. The same opening-interaction resolver now acknowledges text-only first-discovery notices `THESECRET0/1` after their research burst has already been applied.

The focused AI-contact mode creates the same two-process LAN game, establishes a symmetric contained Pact/commlink with one living non-alien AI, and opens the real native channel. It proves the exact `COMM -> INTRONEW* -> HELLOPACT -> DIPLO -> finish` chain, guarded accept/continue/finish semantics, a nonmodal peer, and zero visual or raw-UI input. Discovery runs also reached fully structured `TRADETECH*`, `DEMANDTECH*`, `PROTORIVAL`, `MILDTREATY`, and `SWEARAPACT` continuations; their no-transfer rejection or passive acknowledgment paths are allowlisted, while consequential acceptance remains blocked.

The ready-unit reference regression cross-checks the snapshot's compact IDs/names/positions against the full fair-play unit list, requires exact revision continuity into `unit_actions`, executes one guarded action directly from a reference, and verifies the reference disappears after mutation.

The one-turn semantic soak adversarially submits a wrong `session_id` and stale `revision`, requires `wrong_game_identity` and `stale_state` without mutation, and follows transient `waiting_for_engine`/`waiting_for_turn` states by waiting and re-observing.

## Container worker contracts

The worker bootstrap has a fast host-side contract suite. It validates legal
game import and source immutability, rejects symlinks and non-PE executables,
enforces identity and file-secret precedence, checks Proton `runinprefix`
selection and immutable prefix architecture, exercises an authenticated fake
bridge health check, and verifies that no proprietary runtime directories can
enter the image build context:

```bash
PYTHONPATH=src python3 scripts/worker_contract_test.py
python3 -m compileall -q worker
```

The Linux reference image was also validated against a legal Steam installation
and a private copy of Proton Experimental. The real container reached Docker's
healthy state as UID 10001, launched `terranx.exe -windowed` on its private
Xvfb display, served an authenticated bridge through the container proxy, and
reported the exact assigned match, session, agent, perspective, and instance
identities. With native autostart enabled, a fair-play `semantic_snapshot`
reported the opening `PLANETFALL` interaction. Its sole enumerated guarded
`acknowledge_popup` command succeeded and the next snapshot reported
`FIRSTBASE`. No screen capture, mouse, keyboard, text entry, or coordinate UI
operation was used in this validation.

The full managed vertical slice additionally starts an authenticated Control
Center, real Proton game worker, exact MCP sidecar, and official digest-pinned
Hermes container. Qwen3.8-27B runs at low reasoning with only semantic
toolsets, uses status/decision/command semantically, and must advance the
bridge's native revision before the test passes. The same run inspects the
container for secret leaks and verifies a recovery set containing both worker
state and the durable Hermes conversation:

```bash
SMACX_TEST_GAME_SOURCE=/absolute/path/to/legal/game \
SMACX_TEST_PROTON_SOURCE=/absolute/path/to/proton \
SMACX_TEST_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
SMACX_TEST_PROVIDER_URL=http://model-host:8000/v1 \
SMACX_TEST_PROVIDER_MODEL=Qwen3.8-27B \
PYTHONPATH=src python3 scripts/control_worker_mcp_live_test.py
```

Every container, network, volume, and managed Hermes home in this regression
is uniquely test-owned and removed afterward. The user's default Hermes
profile/dashboard and legacy MCP service are not read or restarted.

The managed LAN vertical slice launches two real game workers on one private
Docker network and proves the complete native host/discover/join/configure/
ready/start sequence, shared match identity, distinct process sessions and
factions, one MCP sidecar per seat, host-only checkpoint creation, complete
park, stock multiplayer reload, exact faction restoration, and second entry
into gameplay:

```bash
SMACX_TEST_GAME_SOURCE=/absolute/path/to/legal/game \
SMACX_TEST_PROTON_SOURCE=/absolute/path/to/proton \
SMACX_TEST_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
PYTHONPATH=src python3 scripts/control_lan_live_test.py
```

It passed against the legal reference installation with
`pixels_or_ui_input_used=false`. The regression observes the actual DirectPlay
host rather than assuming seat order, resolves only audited opening interactions
through MCP, invokes the host-only guarded `save_game` choice, and verifies a
roughly 242 KiB native campaign file in that worker's persistent data volume.
Cleanup selects only resources carrying that test installation's exact
ownership label.

The mixed native LAN regression adds a third, independent game process that
has no seat, perspective, MCP sidecar, or worker binding in the match under
test. It automates only the actions a physical human performs: discover the
exact session, join as the assigned name, ready, chat, disconnect, rejoin a
loaded checkpoint, reclaim the saved faction, and chat again. Run it from a
Docker-capable shell; the wrapper creates an isolated macvlan plus an
in-network Control Center test runner because a Linux host cannot directly
reach its macvlan children:

```bash
SMACX_TEST_GAME_SOURCE=/absolute/path/to/legal/game \
SMACX_TEST_PROTON_SOURCE=/absolute/path/to/proton \
SMACX_TEST_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
scripts/mixed-lan-live-test.sh
```

The reference run passed with two managed agent seats and the external player
`Alice`. It proved exact name-to-faction chat attribution, host-only native
save on turn 1, complete external disconnect/rejoin, saved-faction restoration,
and post-resume chat with `pixels_or_ui_input_used=false`. Randomized Alien
Crossfire factions also exposed the stock `INTRO` label's dual use for AI
diplomacy and information-only faction introductions; the bridge now
distinguishes those states semantically.

Mixed human/agent staging also has a fast contained contract test. It proves
that a human seat receives no agent perspective, a private bridge network
cannot masquerade as external publication, exact names/readiness are mandatory,
unexpected players are rejected, and a resumed human must reclaim the recorded
faction:

```bash
PYTHONPATH=src python3 scripts/external_lan_contract_test.py
```

The complementary human-host contract proves that seat zero has no worker or
MCP, every managed client discovers one exact session, joins and readies, no
client can issue native Start, and all player/faction identities bind durably
after the human-owned transition:

```bash
PYTHONPATH=src python3 scripts/human_hosted_lan_contract_test.py
```

Its full native regression uses an independent third process as the external
human fixture and gives that process exclusive ownership of Host, Configure,
Start, Save, and Load:

```bash
SMACX_TEST_GAME_SOURCE=/absolute/path/to/legal/game \
SMACX_TEST_PROTON_SOURCE=/absolute/path/to/proton \
SMACX_TEST_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
scripts/human-hosted-lan-live-test.sh
```

The reference run passed fresh and loaded lobbies with two managed agents,
bidirectional faction-attributed chat, external-host checkpointing, complete
managed disconnect/rejoin, exact faction restoration, and post-resume chat.
It reported `pixels_or_ui_input_used=false`.

`scripts/worker_manager_live_test.py` provisions a real view-enabled worker,
connects to its random noVNC port, verifies the operator-only password is not
present in container environment/configuration, parks it, and starts the same
durable worker again.

Stock Debian Wine is retained as a diagnostic fallback, but it stalled at the
Firaxis presentation screen in the current reference environment and is not a
certified game runtime. A Proton distribution must be a private, writable,
checksummed copy because Proton maintains `dist.lock`; never mount Steam's live
runtime read-write into a worker.

The skip-all-ready regression cross-checks the exact game-management unit list against `ready_unit_refs`, proves that missing confirmation and a changed count preserve the original revision and set, applies native skip to every reviewed unit, accepts either an exposed `end_turn` or the stock engine's immediate auto-end transition, and rejects replay of the consumed guard.

The native phase-mutation regression fabricates a confirmed `disband_unit` command while `PLANETFALL` is active. The bridge must return `not_actionable` while preserving the unit, popup, and revision:

```bash
SMACX_TEST_TIMEOUT=120 scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/phase_mutation_guard_test.py
```

The native self-destruct regression reviews a visible adjacent target, proves that omission of `confirm_self_destruct` preserves both units, then executes the original reactor-overload routine and verifies both lethal effects plus stale-replay rejection:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_SELF_DESTRUCT=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/self_destruct_test.py
```

The native Nerve Gas regression covers both branches of `USENERVE` without UI input. Conventional combat must complete without changing the atrocity counter; commitment must first reject a missing confirmation, remain open, then increment the native atrocity counter after `confirm_atrocity=1`:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_NERVE_GAS=conventional SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/nerve_gas_combat_test.py
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_NERVE_GAS=commit SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/nerve_gas_combat_test.py
```

Base-notice regressions use contained-only triggers but let the native popup/upkeep paths produce the event:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_BASE_STATUS=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/base_status_notice_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_PRODUCTION_NOTICE=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/production_notice_test.py
```

For longer interaction discovery, add `--trace-actions` and retain the JSONL output. The validated 100-turn configuration is:

```bash
SMACX_TEST_TIMEOUT=900 scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/semantic_playthrough.py \
  --target-turn 100 --deadline 850 --trace-actions
```

The driver treats a revision change between its snapshot and a later `game_management` query as an ordinary optimistic-concurrency retry. A returned `turn:end_blocked` status is also a re-plan signal, not a capability gap. One post-hardening 100-turn run survived base seizure, total-base loss, the native escape-pod recovery state, repeated wipeout notices, and a natural AI-called Governor election without visual input. The final v0.40-code run also passed to turn 100 with 383 semantic movement decisions and 607 guarded deferred-action IDs. It exercised allocation, production, base founding, research, terraforming, Council, Vendetta/territorial incidents, and repeated contacts. At turn 53, a native `COMM` prompt interrupted a confirmed end-turn transition before the turn counter changed; the bridge correctly released only the transition latch, preserved the modal phase guard, accepted `respond_to_contact`, and resumed native turn processing without pixels or coordinate input.

Diplomacy has an explicit contained-only commlink fixture. The production MCP never sets these variables.

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_CONTACT=2 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/diplomacy_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_LOANS=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/loan_diplomacy_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_COMMERCE=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/technology_commerce_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_DIPLO_PURCHASES=1 SMACX_TEST_TIMEOUT=200 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/diplomatic_purchase_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_BASE_PURCHASE=1 SMACX_TEST_TIMEOUT=200 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/base_purchase_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_COUNCIL_BARGAIN=1 SMACX_TEST_TIMEOUT=210 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/council_bargain_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_ENERGY_GIFT=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/energy_gift_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_UNIT_GIFT=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/unit_gift_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_JOINT_ATTACK_COUNTEROFFER=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/joint_attack_counteroffer_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_TECH_DEMAND=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/technology_demand_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_TECH_DEMAND=energy SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/technology_demand_counter_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_TECH_DEMAND=tech SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/technology_demand_counter_test.py
```

The loan regression drives the game's native energy-trade evaluator, accepts its generated principal/payment/term offer semantically, then verifies the treasury transfer and native recurring-debt ledger.

The technology-commerce regression drives the native price evaluator, accepts the structured purchase offer, and verifies both exact energy payment and technology ownership transfer.

The diplomatic-purchase regression drives native prototype-plan and commlink-frequency offers, accepts both semantically, and verifies the exact treasury deductions, copied Unit Workshop design, and acquired target commlink. The ordinary diplomacy regression also verifies the non-sequential native proposal-ID mapping and a real `PROPOSECOMMLINK` target selector/cancel round trip.

The joint-attack counteroffer regression invokes the executable's real `propose_attack` evaluator, accepts its generated 25-credit price through the typed interaction, verifies the exact player treasury deduction, observes the native `INCITED` Vendetta outcome, and returns to the turn without pixels or input.

The technology-demand regressions use real `DEMANDTECH15`, `DEMANDTECH9A`, `DEMANDTECHAGAIN1`, and `DEMANDTECHAGAIN2` dialogs. They prove exact variant-to-script-slot mapping, suppression of an unrelated scratch technology, native energy and reciprocal-technology counters, fresh follow-up guards, exact native transfer, and stale-replay rejection. These tests also exercise the reviewed button-only popup completion path that writes the guarded native result and runs the executable's common completion tail without mouse, keyboard, coordinates, or pixels.

The base-purchase regression creates a second AI-owned test base, lets the native valuation and sale routine quote it, accepts only through the guarded semantic offer, and verifies exact treasury payment plus real ownership transfer.

The Council-bargain regression requests an AI leader's YEA commitment through the native vote-price routine, accepts its exact semantic energy quote, and verifies the full native treasury deduction. Technology-bundle alternatives are typed but still need a deterministic native-effect fixture.

The incoming-vote-offer regression opens the real native energy or two-technology Governor-vote dialog, verifies the explicit ballot-commitment gate, and covers both safe rejection and acceptance without pixels. Because the contained fixture opens the dialog outside its ordinary Council call stack, its test-only owner resumes the disassembled native post-dialog payment continuation. Acceptance must then show the exact 125-credit treasury delta or both sequential native technology acquisitions; their acquisition notices are resolved semantically in order. The ordinary Council regressions independently cover chamber ballot continuation and public results.

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_INCOMING_VOTE_OFFER=1 \
  SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/incoming_vote_offer_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_INCOMING_VOTE_OFFER=tech \
  SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/incoming_vote_offer_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_INCOMING_VOTE_OFFER=energy \
  SMACX_AGENT_TEST_INCOMING_VOTE_ACCEPT=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/incoming_vote_offer_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_INCOMING_VOTE_OFFER=tech \
  SMACX_AGENT_TEST_INCOMING_VOTE_ACCEPT=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/incoming_vote_offer_test.py
```

The energy-gift regression constructs a reviewed native `COUNTER1` selector, submits 75 of 500 credits through the atomic semantic action, drives the real nested amount/receipt chain, and verifies player/counterpart balances of 425/1075. The unit-transfer regression creates one visible unit in Pact territory, proves the explicit confirmation gate, invokes the native ownership-change routine, and verifies that the unit leaves the player's fair-play unit list.

The proposal-guard regression opens a contained native `PROPOSAL` selector and test-injects the otherwise unreachable `Script.txt` trained-unit row. It verifies that the bridge does not expose that row at all, proves a fabricated legacy selection is rejected without closing the modal, and exits through the returned native cancel choice. This protects the executable finding that native proposal ID 12 is reserved for the separate Council-vote request path, not unit negotiation:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_PROPOSAL_GUARD=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/proposal_guard_test.py
```

The hostility regression repositions an existing foreign scout in test mode, queues a real treaty-protected attack, proves cancellation preserves the treaty, proves omission of `confirm_hostility` is rejected, then explicitly creates Vendetta and resolves native combat. It uses no pixel or UI input.

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_HOSTILITY=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/territorial_incident_test.py
```

The combat-confirmation regression drives the native `BADIDEA` odds warning, proves cancel and explicit-confirmation paths, and verifies native combat completion:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_COMBAT_CONFIRMATION=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/combat_confirmation_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_COMBAT_CONFIRMATION=1 \
  SMACX_AGENT_TEST_HASTY=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/hasty_attack_test.py
```

The HASTY regression exposes the native current/full strength fraction, proves that proceeding without `confirm_attack=1` is rejected, covers the safe wait/cancel branch, and follows the complete proceed chain through any later hostility/odds confirmation to native combat resolution.

Supreme Leader accession/defiance and the final-score finish/continue prompt are covered with native alternative-row dialogs:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_ENDGAME=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/endgame_interaction_test.py
```

The complete production endgame stack is covered by contained full-victory fixtures. The first run enters the actual Transcendence victory path; the second enters the economic-victory path with its native narrative interlude enabled:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_FULL_ENDGAME=1 SMACX_TEST_TIMEOUT=220 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/full_endgame_pipeline_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_FULL_ENDGAME=narrative \
  SMACX_TEST_TIMEOUT=240 scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/full_endgame_pipeline_test.py
```

These regressions traverse each native passive presentation window through its semantic close callback, then use the real `GAMEOVERMAN` finish choice. They verify the exact phase sequence, final-score flag, native exit controls, rejection of a fabricated unit mutation during presentation, and stale replay rejection without pixels or UI input.

The economic-victory regression grants only the contained fixture prerequisites, then verifies the native cost quote, explicit commitment gate, treasury deduction, `CORNERING` notice, and observable countdown:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_ECONOMIC_VICTORY=1 \
  SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/economic_victory_test.py
```

The base-status regression also immediately replays the same acknowledgement guard and requires `stale_state` or `popup_transition_pending` while the game remains healthy.

Planetary Council regressions likewise use contained-only contact/technology fixtures:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_COUNCIL=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/council_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_COUNCIL=1 \
  SMACX_AGENT_TEST_COUNCIL_POLICY=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/council_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_COUNCIL=1 \
  SMACX_AGENT_TEST_INCOMING_COUNCIL=1 SMACX_TEST_TIMEOUT=240 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/incoming_council_test.py
```

The incoming-Council regression lets the normal AI-turn `call_council` path convene an election. Its contained fixture supplies mutual contacts, resets call cooldowns, and uses mutual pacts so random starting positions cannot eliminate the human test faction before its ballot. It proves the exact `CALLSCOUNCIL` acknowledgement is single-use, the following `CouncilWindow` is a separate semantic state, a candidate vote reaches the native chamber, and the public result is observed without pixels or UI input. These fixture variables are never set by the production launcher or MCP.

The Unit Workshop regression uses a contained-only component/energy fixture:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_WORKSHOP=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/unit_design_test.py
```

Advanced unit regressions use contained-only unit fixtures:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_WORKSHOP=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/advanced_unit_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_TRANSPORT=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/transport_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_ARTILLERY=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/artillery_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_MISSILES=1 SMACX_TEST_TIMEOUT=180 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/missile_launch_test.py
```

The missile regression validates the compact opaque-tile-ID query/exact-target protocol, native conventional combat, tectonic and fungal effects, and both refusal and explicit confirmation of a native Planet Buster atrocity. No map coordinates, pixels, or UI input cross the client boundary, and it re-discovers unit IDs after every consumed missile.

The probe regression creates a contained-only visible enemy-base encounter, verifies that omission of the explicit incident confirmation is rejected without mutation, then resolves an energy-drain mission through the native probe routine:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_PROBE=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/probe_mission_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_PROBE=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/probe_subversion_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_PROBE=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/probe_targeted_sabotage_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_PROBE=1 SMACX_TEST_SABOTAGE_ABORT=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/probe_targeted_sabotage_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_PROBE=1 SMACX_AGENT_TEST_PROBE_PLAGUE=1 \
  SMACX_TEST_PROBE_SPECIAL=plague scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/probe_special_mission_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_PROBE=1 SMACX_AGENT_TEST_PROBE_LEADER=1 \
  SMACX_TEST_PROBE_SPECIAL=leader scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/probe_special_mission_test.py
```

The staged regressions prove that no facility or captive identity appears before its native disclosure point, enumerate only IDs present in the live native menu, cover protected-target confirmation and clean sabotage abort, and require a separate atrocity confirmation for genetic plague.

The Psi Gate regression creates two contained-only owned gates and verifies native relocation, consumption of both endpoint gates, and suppression of a repeat transfer:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_PSI_GATE=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/psi_gate_test.py
```

Persistent-order coverage creates contained-only land units and verifies native patrol validation, Road To assignment, decision-gate suppression while ordered, and explicit activation/cancellation:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_ORDERS=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/persistent_orders_test.py
```

Native automated exploration is independently exercised on an ordinary starting Scout. The regression verifies choice eligibility, native persistent state, decision-gate suppression, the activate-only ordered state, noncombat command rejection, native cancellation, and stale replay rejection after an apply/cancel cycle:

```bash
scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/auto_explore_test.py
```

The native defender-designation regression toggles the exact state-relative choice and restores the original role, proving structured state visibility, no movement consumption, opposite-only enumeration, 0/1 request parsing, and monotonic stale-token rejection after the full cycle:

```bash
scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/designated_defender_test.py
```

Coordinate-free native return-to-base uses a contained off-base land unit. It verifies a known base-ID-only tuple, absence of target coordinates, fabricated-base rejection, exact `Console::go_home` assignment, persistent decision-gate suppression, cancellation, at-base suppression, and stale replay rejection:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_RETURN_HOME=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/return_to_base_test.py
```

Native automation has an immediate contract regression and a full-turn lifecycle regression. The first covers combat-only On Alert, named currently eligible Former modes, fabricated mode rejection, structured policy state, activate-only cancellation, stale guards, and the patrol shared-bit distinction. The second proves a full-auto Former actually moves under native control, persists into the next actionable turn, stays absent from `ready_unit_refs`, and remains cancellable:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_ORDERS=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/native_automation_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_ORDERS=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/native_automation_turn_test.py
```

Persistent bombing runs have both an immediate guard regression and a full-turn native lifecycle regression. They prove visible Vendetta-base enumeration through opaque tile IDs, non-sacrificial round-trip fuel policy, invalid-target rejection without mutation, stock-handler acceptance plus the patched-host destination repair, activate-only cancellation, stale replay rejection, native movement, and persistence outside `ready_unit_refs`:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_BOMBING_RUN=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/bombing_run_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_BOMBING_RUN=1 \
  SMACX_TEST_NATIVE_AUTOMATION_KIND=bombing_run \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/native_automation_turn_test.py
```

Aircraft semantics use a contained Air Superiority design, a visible friendly standalone airbase, and near plus deliberately fuel-unsafe owned bases. The immediate regression proves two-stage base-ID routing, tile-ID standalone-airbase recovery, safe route classification, no destination coordinates, remaining-fuel rejection through both `go_to_base` and generic `go_to`, native Air Defense mode 12, activation-only gating, non-air rejection, and stale guards. The full-turn path proves semantic `REALLYOVER` handling, duplicate-transition suppression, and safe re-listing when the delegated native policy removes its aircraft:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_AIR_AUTOMATION=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/air_semantics_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_AIR_AUTOMATION=1 \
  SMACX_TEST_NATIVE_AUTOMATION_KIND=air_defense \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/native_automation_turn_test.py
```

Carrier recovery uses the same contained aircraft fixture plus a two-slot carrier, two staging aircraft, and one co-located deck aircraft. The regression proves object-targeted choice enumeration, generic-route bypass rejection, native deck-capacity reservation, a mechanically enforced carrier hold, full-deck rejection, native arrival, real boarded/refueled state, safe re-discovery after unit IDs shift, and both passengers moving with the carrier:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_AIR_AUTOMATION=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/carrier_recovery_test.py
```

Terrain-improvement destruction has three contained paths. They verify the original compound-layer ordering, explicit destruction confirmation, peaceful-territory hostility confirmation and native Vendetta consequences, Pact suppression, exact item removal, underlying-item preservation, deferred revision changes, and zero pixel/coordinate input:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_TERRAIN_DESTRUCTION=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/terrain_destruction_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_TERRAIN_DESTRUCTION=foreign \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/terrain_destruction_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_TERRAIN_DESTRUCTION=pact \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/terrain_destruction_test.py
```

Single-vehicle upgrade coverage creates two identical ready Scouts plus one stronger custom design, then verifies native candidate filtering, exact confirmation/cost, one-and-only-one vehicle mutation, preservation of the second Scout, turn consumption, and stale replay rejection:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_SINGLE_UNIT_UPGRADE=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/single_unit_upgrade_test.py
```

The base-atrocity regression verifies explicit confirmation, deferred native nerve stapling, the resulting duration, and repeat suppression while the effect is active:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_BASE_ACTIONS=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/base_atrocity_test.py
```

Base obliteration has three contained native paths: default atrocity rules, an objective-base rejection, and the non-atrocity rules variant. They verify harmless initiation, the real `OBLIT`/`OBLITOK` interaction, conditional confirmation tokens, native base-count change, the `OBLITTED` notice, continued process health, and no coordinate or pixel input:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_BASE_OBLITERATION=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/base_obliteration_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_BASE_OBLITERATION=objective \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/base_obliteration_test.py

SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_BASE_OBLITERATION=noatrocity \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/base_obliteration_test.py
```

Facility-recycling coverage verifies the exact refund, destructive confirmation, physical removal, Headquarters exclusion, and the native one-per-base-per-turn limit:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_BASE_ACTIONS=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts python3 scripts/facility_recycling_test.py
```

For a supervised Qwen/Hermes run, stop the persistent service so the contained MCP can use port 47814, run the test under Xephyr, then restore the service even if the model times out. Qwen3.8-27B can take several minutes between tool calls on this installation, so allow roughly 30 minutes for six turns.

```bash
systemctl --user stop smacx-agent-mcp.service
SMACX_TEST_TIMEOUT=1800 scripts/nested_display_test.sh scripts/hermes_semantic_test.sh
systemctl --user start smacx-agent-mcp.service
```

To exercise the compact action-ordered loop with the shorter four-turn prompt, select it explicitly:

```bash
systemctl --user stop smacx-agent-mcp.service
SMACX_HERMES_PROMPT_FILE="$PWD/scripts/hermes_decision_test_prompt.txt" \
  scripts/hermes_semantic_test.sh
systemctl --user start smacx-agent-mcp.service
```

The latest supervised decision-frame run used Qwen3.8-27B with low reasoning in Hermes session `20260828_075454_e2a6fd`. It started a fresh Tiny/Citizen match, handled `PLANETFALL` and `FIRSTBASE`, named the first base, moved two freshly discovered Colony Pods, assigned native exploration to a Scout, reached and completed turn 4, created and listed `qwen_decision_test`, and stopped the game itself. All gameplay followed `smac_decision -> one guarded smac_command -> fresh smac_decision`; shifted unit IDs were rediscovered from new frames. Its 29 model/API calls used only `smac_status`, `smac_new_game`, `smac_decision`, `smac_command`, `smac_saves`, and `smac_stop`. It made no screenshot, vision, mouse, keyboard, computer-use, terminal, filesystem, browser, web, or raw-UI call, retried no stale choice, and reported no capability gap. The run intentionally stopped when turn 4 was complete, so a legal founding choice first returned at turn 5 was observed but not executed. The usage report records 1,053,588 total tokens.

The preceding comprehensive supervised run used the older explicit snapshot/choice loop and completed at turn 8 in Hermes session `20260828_072215_1c0604`. It founded a second base after following structured settlement reasons, placed two Scouts under native exploration, reached 95% blind-research progress, handled `PLANETFALL` and `ALIENSARRIVE`, created and verified `qwen_test`, wrote three guarded facts through `smac_knowledge`, and stopped the isolated game. Its 55 model/API calls used only SMACX MCP tools—no screenshot, mouse, keyboard, computer-use, terminal-UI, web, or raw-UI path—and reported no capability gap. The run exposed one attempted durable reference to session-local `unit id 26`; the knowledge boundary now rejects such unit/base/prototype references, with `knowledge_reference_guard_test.py` proving rejected values never reach storage. That run's fixed-path ledger is `$HOME/Documents/ai/SidMeiers/games/match-0cd565272084430dabab7313c8f1b621/knowledge.json`.

The match-knowledge regressions prove active match/session/revision write guards, stale-write rejection, correction history, cross-match read rejection during active play, absence of arbitrary path access, and live rejection of the retired raw bridge-input operation:

```bash
PYTHONPATH=src python3 scripts/match_knowledge_test.py

SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/live_match_knowledge_test.py
```

The durable-platform regressions are fully contained and require neither a game process nor Graphiti/Neo4j. They prove concurrent atomic initialization of the canonical pre-release schema, immutable events, perspective isolation, versioned structured memory records, bounded recall, chat player/faction mapping and exactly-once attention, guarded memory writes, legacy JSON history import, Graphiti failure-safe cursors, and group-local rebuild:

```bash
PYTHONPATH=src python3 scripts/platform_store_test.py
PYTHONPATH=src python3 scripts/platform_controller_test.py
PYTHONPATH=src python3 scripts/graphiti_projection_test.py
PYTHONPATH=src python3 scripts/control_plane_test.py
PYTHONPATH=src python3 scripts/control_http_test.py
PYTHONPATH=src python3 scripts/worker_contract_test.py
```

The Docker client contract requires access to a local Docker socket. It creates
only uniquely named, installation-labeled scratch resources and removes them:

```bash
PYTHONPATH=src python3 scripts/docker_client_test.py
```

The opt-in worker-manager regression validates the legal source in a read-only
container, imports Proton into a private checksummed volume, transfers the
bridge token without an environment variable, boots the real game twice,
observes a healthy authenticated semantic opening both times, and proves the
match identity survives while the process session rotates. It removes its
containers and volumes on success or failure:

```bash
SMACX_TEST_GAME_SOURCE=/absolute/path/to/game \
SMACX_TEST_PROTON_SOURCE=/absolute/path/to/proton \
SMACX_TEST_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
PYTHONPATH=src python3 scripts/worker_manager_live_test.py
```

The capability-gap safety regression proves one audit record per match/session, zero native command calls after the latch, blocked launch/new/load escape paths, visibility through status, absence of an agent clear tool, and restoration only after a developer-controlled MCP restart:

```bash
PYTHONPATH=src "$HOME/.hermes/hermes-agent/venv/bin/python" \
  scripts/capability_gap_latch_test.py
```

The ordered decision-frame regression proves automatic ready-unit selection, interaction/wait/gap routing, inseparable command guards, and rejection after three unstable snapshot/choice assemblies:

```bash
PYTHONPATH=src "$HOME/.hermes/hermes-agent/venv/bin/python" \
  scripts/decision_frame_test.py

SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts "$HOME/.hermes/hermes-agent/venv/bin/python" \
  scripts/decision_frame_native_test.py
```

The live decision-frame regression requires compact/full identity, command guard, focus, and choice signatures to be identical while the default compact serialization is at least 25% smaller. The initial native measurement retained all 15 action signatures while shrinking 5,006 bytes to 2,944 bytes (41.2%). The v0.41 run retained all 13 choices in its generated position while shrinking 4,898 bytes to 2,837 bytes (42.1%) and independently proved that `finish_ready_units=true` exposes the guarded skip-all family at the same identity/revision. Only redundant default-safe movement fields may be removed; real combat/contact, transport, feature, confirmation, price, and other choice context remains intact.

The Alien Artifact regression enters a base through an ordinary semantic move, verifies exact context and the consumption-confirmation guard, links through the native menu, acknowledges the technology notice, and proves both unit consumption and one acquired technology:

```bash
SMACX_AGENT_TEST_MODE=1 SMACX_AGENT_TEST_ARTIFACT=1 \
  scripts/nested_display_test.sh env PYTHONPATH=src:scripts \
  python3 scripts/artifact_interaction_test.py
```

`base_management_test.py` additionally enumerates all named advanced governor permissions, toggles Secret Project authority for an active production governor, verifies native recalculation plus future-base-default scope, and rejects stale replay:

```bash
SMACX_TEST_TIMEOUT=180 scripts/nested_display_test.sh \
  env PYTHONPATH=src:scripts python3 scripts/base_management_test.py
```

## Platform, routed-LAN, and physical certification

Run the platform and routed transport checks before involving another machine:

```bash
python3 scripts/platform_preflight.py \
  --game-path /absolute/legal/game --directx-redist /absolute/directx-redist.exe
PYTHONPATH=src python3 scripts/virtual_lan_contract_test.py
./scripts/virtual_lan_route_live_test.sh
PYTHONPATH=src python3 scripts/lan_profile_contract_test.py
```

For WSL2 add `--require-wsl2`. A physical mixed human/AI certification is one
complete matrix, not a successful ping:

1. Put the human game and managed worker on different physical computers and
   record OS, Docker, WSL (if any), Tailscale, Proton, and game hashes.
2. Confirm only the intended player subnet route is approved and that TCP
   47624 plus TCP/UDP 2300–2400 pass; verify an unrelated container port is
   rejected.
3. Run one AI-hosted and one human-hosted game. In each, validate exact names,
   distinct factions, private and public faction-attributed chat, and at least
   one paired-diplomacy offer/accept/decline path.
4. Create the native host checkpoint, disconnect the remote participant, park
   every managed seat, resume the checkpoint, reclaim exact saved factions,
   exchange chat, and complete another turn.
5. Stop and resume one managed harness and one worker; confirm match identity
   and scoped memory remain while process-session identity rotates.
6. Save logs and fill `docs/certification-record.example.md`; do not promote an
   unrun row to certified.

The latest regression artifacts are written under `runtime/`. A successful
cleanup leaves no `terranx.exe` or Xephyr `:99` process. The persistent MCP
should finish `active` and expose exactly 19 semantic tools.
