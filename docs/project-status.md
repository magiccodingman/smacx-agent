# Project status

This document distinguishes implementation from evidence. “Verified” means a
test actually crossed the stated boundary; it does not mean every future host,
network, mod, provider, or rare native state is certified.

## Delivered platform

| Area | Implementation | Evidence |
| --- | --- | --- |
| Blazor LAN portal | .NET 10 Blazor Web App, MudBlazor, WebAssembly client pages, controllers, SignalR, responsive navigation | .NET integration test plus Chrome desktop/mobile interaction |
| Accounts | first-run token, no default password, Identity cookie sessions, admin/member roles, CLI/admin reset tickets, case-insensitive handles, provisional invited accounts | contained HTTP/database flow |
| Lobby directory | listed/waiting/running/parked history, membership, seven seats, unranked policy, future-ranked field | .NET integration flow and Chrome |
| Typed setup | Alien Crossfire, standard/scenario, world size, six difficulties, four planet traits, five timers, five victory paths, ten advanced rules, Do or Die | validation contracts and native solo/LAN/scenario tests |
| Browser human play | Selkies H.264/WebRTC-style stream transport with audio/input/fullscreen/reconnect through authenticated portal proxy | real `terranx.exe` rendered in Chrome; keyboard/mouse reached the game |
| Spectating | admin cross-seat, opt-in anonymous LAN observer deck, seat switching, worker-enforced read-only mode | real stream plus authorization/read-only checks |
| Native human seats | exact host/session/handle/faction join details and durable account association | mixed independent native process locally tested; external physical client pending |
| Portal/native chat | public/private recipient, player + faction attribution, sequence/deduplication, messages outside active turn | contained and native mixed-LAN tests |
| Provider management | OpenAI-compatible discovery, keyed/unkeyed providers, chosen model/context | contract tests and real Qwen endpoint |
| AI profiles | versioned model/reasoning/context/notes, deactivation, `None` personality layer | .NET/Control contracts and real managed run |
| Managed Hermes | digest-pinned official image, isolated home/conversation, `smacx,web` only, provider secret volume, restart supervision | contract/secret inspection and real Qwen run |
| Match lifecycle | provision, checkpoint, race-safe park, recover, idle browser park, crash reconciliation | real native checkpoint/park/recover; live park race regression |
| Analytics | scoped history, turn duration, Hermes input/output/cache/reasoning/API counters, CSV, isolated read-only SQL lab | fresh-schema .NET tests and real Hermes telemetry query |
| Knowledge | 22 original mechanics primers, hierarchy, FTS5/BM25; optional private installed-doc extraction | corpus/copyright guard; 294 private documents from 22 local sources on reference install |
| Memory | events, facts, beliefs, relationships, commitments, goals, summaries, compression budgets, chat, scoped recall | contained adversarial scope/compression/retrieval tests and real Qwen writes |
| Graphiti | optional Neo4j temporal derivative, projection cursor/rebuild/isolation | contract and backend live test; disabled when endpoints are incompatible |
| Operations | schedules, immutable runs, worker/MCP/Hermes reconciliation, online/volume backups, offline restore guard | contract tests, native recovery, verified live backup |
| Multiple concurrent seats/matches | separate workers/displays/streams/MCP/sessions/volumes/perspectives | two managed clients plus independent native client locally; capacity-dependent |

## Real semantic model certification

On the Linux reference host, Qwen3.8-27B ran at low reasoning in a managed
Hermes container with only `smacx,web` toolsets. It:

- acknowledged the native Planetfall state;
- named the first base;
- moved/automated units and advanced from turn 1 to turn 13/year 2113;
- searched and retrieved the mechanics corpus when it needed colony rules;
- wrote scoped durable goals and facts;
- handled stale-revision guards by re-observing; and
- was stopped, checkpointed, and parked without screenshot/click fallback.

Hermes recorded one durable session, 97 provider calls, 5,785,165 input tokens,
38,224 output tokens, and 21,743 reasoning tokens. These figures are evidence
of the current prompting/context economics, not a performance target.

## Native gameplay coverage

Broad typed coverage exists for:

- opening/name/base decisions and normal turn boundaries;
- fair map/unit/base/research/social-engineering observation;
- unit movement, orders, automation, combat, transports, probe actions,
  artillery/bombardment, air/carrier operations, upgrades/gifts/disband;
- base production, purchasing, specialists/citizens, facilities, obliteration;
- prototype design/retire/bulk upgrade;
- research and technology commerce;
- social engineering;
- AI diplomacy, pacts/treaties/truces/vendetta, demands/offers, energy/tech/unit
  transfers, loans, joint attacks, atrocities, territorial incidents;
- Planetary Council proposals, votes, bribes/counteroffers;
- economic/Transcendence/conquest/cooperative/endgame interactions;
- native chat and participant identity;
- standard/custom/scenario setup, DirectPlay lobby, native save/load; and
- persistent orders/goals and fail-closed capability reporting.

The exact command surface and remaining named gaps live in
[coverage.md](coverage.md). An absent adapter is not treated as available through
mouse automation.

## LAN evidence

Locally verified with real native processes:

- two isolated managed DirectPlay clients host/discover/join/ready/start;
- distinct factions and perspectives under one durable match;
- host-only verified save, complete park, stock multiplayer reload, exact
  faction restoration, second entry into gameplay;
- an independent third native process acting as a human fixture;
- human disconnect/rejoin and post-resume faction-attributed chat;
- advanced external-human-host flow where the human exclusively owns native
  Host/Configure/Start/Save/Load; and
- five guarded random-map profiles plus typed custom rules/scenarios.

These tests use multiple processes on one Linux host/network fixture. They do
not substitute for the explicitly deferred physical two-computer test.

## Knowledge/copyright boundary

The repository ships 22 short, independently written mechanics primers. It
ships no installed manual pages, help database extraction, strategy guide,
scenario solution, or copied wiki corpus. The reference guard compared shipped
text against local `Manual.pdf`, `Script.txt`, and help text and found no
eight-word copied sequence under its normalization.

The optional extractor operates only after the operator validates a game
source. On the reference legal copy it produced 294 private searchable
documents from 22 sources, with guides excluded. That database stays in the
operator's control volume and is not part of source/image artifacts.

## Canonical pre-release schemas

The repository has not released a public database schema. Portal and control
databases each have one canonical initial schema created directly on a fresh
volume. There are no fictional v2/v3/v4 migrations and no
`__EFMigrationsHistory` table. After a first public release, future incompatible
changes can begin a real migration history.

Development volumes made by older commits are disposable and should be
re-created; they are not production upgrade fixtures.

## Designed or implemented but externally unverified

These are intentionally outside the current Linux-local certification:

1. a mixed human/AI game across two physical computers;
2. an actual remote Tailscale/Internet peer traversal;
3. Windows 11/WSL2 operation;
4. every third-party mod/binary combination;
5. every rare stock-game modal/state across arbitrary scenarios; and
6. provider-specific billing accuracy where the provider/Hermes does not report
   cost.

Contracts and deployment notes exist for the first three, but documentation
must not call them certified until the physical environments are exercised.

## Explicitly not delivered in this milestone

- public Internet hosting, matchmaking, or binary distribution;
- ranked ratings/anti-cheat policy (all matches are unranked);
- authored personality cards or faction personas (`None` only);
- strategy/cheese guides or scenario walkthroughs;
- screenshot/mouse/keyboard fallback tools for AI seats.

## Operational note from development

The original 8 GiB RAM/1 GiB swap VM was OOM-killed while three Docker builds
and Blazor optimization ran concurrently. The kernel killed the desktop Codex
renderer; native match data was not the cause. The startup script now forces
Compose build parallelism to one and builds control, portal, and worker images
sequentially. The verified continuation host has 16 GiB RAM and 16 GiB swap.

## Definition of a capability gap

A capability gap is a reproducible native state where the agent lacks a typed
fair observation/action needed to continue. The agent calls
`smac_report_capability_gap`; mutation latches closed for that session. An
operator can then add/test an adapter and recover into a fresh process. A model
playing poorly, choosing a bad strategy, or using many tokens is not itself a
control capability gap.
