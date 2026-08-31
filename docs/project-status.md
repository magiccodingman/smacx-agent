# Project status

This document distinguishes implementation from evidence. “Verified” means a
test actually crossed the stated boundary; it does not mean every future host,
network, mod, provider, or rare native state is certified.

## Delivered platform

| Area | Implementation | Evidence |
| --- | --- | --- |
| Blazor LAN portal | .NET 10 Blazor Web App, MudBlazor, WebAssembly client pages, controllers, SignalR, responsive navigation, installable PWA identity and guided install page | .NET integration test, PWA event/manifest contract, and Chrome desktop/mobile interaction |
| Accounts | first-run token, no default password, Identity cookie sessions, admin/member roles, CLI/admin reset tickets, case-insensitive handles, provisional invited accounts, duplicate lobby-handle rejection | contained HTTP/database flow and duplicate/case contracts |
| Lobby directory | listed/waiting/running/parked history, membership, seven seats, unranked policy, future-ranked field | .NET integration flow and Chrome |
| Typed setup | Alien Crossfire, standard/scenario, world size, six difficulties, four planet traits, five timers, five victory paths, ten advanced rules, Do or Die | validation contracts and native solo/LAN/scenario tests |
| Browser human play | Selkies stream with audio/input/fullscreen/reconnect, aspect-correct instant fit, adaptive bounded H.264 bitrate, validated 800×600–5120×1440 native catalog, touch recommendations, device-local lock, one-controller tab leases, navigation guard, human-only native-MENU rail and semantic native-quit cancellation | real 800×600 and 1920×1080 game windows; 390×844, 844×390, 1024×768, and 1280×720 browser QA; keyboard reached the game; lease tests and native worker compile/contracts |
| Spectating | admin cross-seat, opt-in anonymous LAN observer deck, seat switching, worker-enforced read-only mode | real stream plus authorization/read-only checks |
| Native human seats | exact host/session/handle/faction join details and durable account association | mixed independent native process locally tested; external physical client pending |
| Portal/native chat | global/private/consent-group conversations, logical fan-out delivery, participant filtering, player + faction attribution, sequence/deduplication, messages outside active turn | contained store/controller and native mixed-LAN tests |
| Provider management | OpenAI-compatible discovery, keyed/unkeyed providers, chosen model/context, generic sampling controls, typed custom request parameters, and import/export | contract tests, browser form QA, and real Qwen endpoint |
| AI profiles | versioned model/reasoning/context/notes, deactivation, provider-neutral custom settings, official Qwen3.8 templates, and per-seat personality selection | .NET/Control contracts and real managed run |
| Managed Hermes | SMACX-derived/digest-pinned official image, isolated home/conversation, exact SMACX-owned system message, `smacx` only, provider secret volume, restart supervision | contract/secret inspection, captured provider request, and real Qwen run |
| Match lifecycle | provision, mode-aware three-sample checkpoint, persisted connected-player votes, fair multi-match maintenance, race-safe park/reconfigure/recover, temporary native-AI delegation/reclaim, explicit browser presence states, ten-minute never-connected/all-away park, exit/crash reconciliation | real native 800×600 checkpoint/park/profile-change/recover, unexpected worker exit/reconnect, .NET quorum/cooldown/lease tests |
| Analytics | scoped history, turn duration, Hermes input/output/cache/reasoning/API counters, CSV, isolated read-only SQL lab | fresh-schema .NET tests and real Hermes telemetry query |
| Knowledge | automatic runtime-only installed-game and canonical/Wayback acquisition, cleaned Markdown snapshot synchronization, SemanticKnowledge weighted search, one shared local or configurable external embedding space | real 530-document/12-source build with zero warnings, one 2,048-dimension ONNX instance, contained HTTP contract, live portal search/read, and Docker build |
| Memory | events, facts, beliefs, relationships, commitments, goals, summaries, compression budgets, chat, scoped recall | contained adversarial scope/compression/retrieval tests and real Qwen writes |
| Graphiti | optional curated FalkorDB temporal derivative, separate extraction profile, asynchronous ingestion, bounded exact-scope recall, deterministic projection/rebuild/isolation | contracts plus live Qwen extraction → shared ONNX embedding → isolated Falkor graph → semantic recall → exact cleanup |
| Operations | schedules, immutable runs, worker/MCP/Hermes reconciliation, online/volume backups, offline restore guard | contract tests, native recovery, verified live backup |
| Multiple concurrent seats/matches | separate workers/displays/streams/MCP/sessions/volumes/perspectives | two managed clients plus independent native client locally; capacity-dependent |

## Real semantic model certification

On the Linux reference host, Qwen3.8-27B ran at low reasoning in a managed
Hermes container with only the `smacx` toolset. It:

- acknowledged the native Planetfall state;
- named the first base;
- moved/automated units and advanced from turn 1 to turn 13/year 2113;
- searched and retrieved the mechanics corpus when it needed colony rules;
- wrote scoped durable goals and facts;
- handled stale-revision guards by re-observing; and
- was stopped, checkpointed, and parked without screenshot/click fallback.

That pre-overhaul Hermes session recorded 97 provider calls, 5,785,165 input
tokens, 38,224 output tokens, and 21,743 reasoning tokens. Those abnormal input
economics and observed malformed-call retries are an investigation record, not
current performance certification. New Qwen3.8 templates explicitly disable
historical `preserve_thinking`, use project-owned provider-neutral generation
settings, and require a fresh short/long-run comparison before claims about
turn latency, token efficiency, or decision quality are updated.

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

## Managed human evidence

The isolated Linux reference run also launched a human-only match with one
browser seat and six stock game-controlled factions. It required no model or
agent profile. The same match:

- rendered as an exact 800×600 game/X11 window with no clipping;
- survived a verified checkpoint, park, native-profile change, and recovery;
- rendered again as an exact 1920×1080 game/X11 window;
- remained aspect-correct and scroll-free in phone portrait/landscape, tablet,
  and desktop browser viewports;
- reconnected the same portal route after an unexpected game-worker exit; and
- retained the saved faction at turn 1/year 2101.

The profile catalog and worker bounds are contract-tested through 5120×1440.
Those larger framebuffers are implemented but were not all rendered on the
reference display during this run.

These tests use multiple processes on one Linux host/network fixture. They do
not substitute for the explicitly deferred physical two-computer test.

## Knowledge/copyright boundary

The repository ships no manual pages, Datalinks extraction, strategy guide,
scenario solution, copied wiki prose, generated Markdown corpus, or embeddings.
It contains only acquisition metadata, fixed canonical/Internet Archive
addresses, parser code, and retrieval contracts.

At runtime, the separate knowledge service reads the operator's mounted game
files and fetches the explicitly configured factual pages, using the fixed
archive address when a canonical page is unavailable. On the reference legal
copy it built 530 private searchable documents from 12 local/web sources with
strategy sections excluded and no acquisition warnings. Cleaned content,
source hashes, SemanticKnowledge metadata, and compact vectors remain in the
private persistent knowledge volume and are not copied into source or images.

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
