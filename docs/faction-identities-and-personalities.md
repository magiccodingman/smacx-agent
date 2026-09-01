# Faction identities and personality cards

SMACX Agent resolves every occupied seat into a durable, unique faction before
its native game is created. Humans, autonomous agents, and stock computer
players may each reserve one of the fourteen official factions or leave the
choice on Random. Random is resolved once from the factions not already
reserved, then remains locked for that campaign.

For a fresh standard game, the resulting seven-seat roster is authoritative
below the portal: managed humans and agents claim their exact native selector
rows, direct native humans must choose their reserved row before the host may
start, and SMACX's computer-faction allocator is restricted to the reserved
stock-computer remainder. In a one-player managed game the same ordered roster
is installed before native Quick Start, so computer choices are not merely UI
labels.

Public DirectPlay names use the game's printable-ASCII transport spelling.
Accordingly, the Spartan public identity is **Colonel Corazon Santiago** while
the authored personality prose may retain the Spanish accent in **Corazón**.

For example, a lobby may assign the `Qwen 27B · Low` profile to Gaia's
Stepdaughters with the Friendly personality. The portal, native lobby, game
chat, durable chat history, telemetry, and system prompt then identify that
player as **Lady Deirdre Skye**. Internal values such as `agent-…` remain
implementation identifiers and are not presented as player names.

## Lobby flow

Every occupied seat has a **Faction** control in its staging Configure panel.
It offers one of the fourteen official SMAC/Alien Crossfire factions or Random,
and a specific faction cannot be reserved by any other human, AI, or stock
computer seat. AI seats additionally have their own controls:

- **Personality** — Standard by default, None, Random, or one of the selected
  faction's Friendly, Aggressive, and Extreme authored variants.

With a random faction, the lobby offers only None, Standard, and Random because
a faction-specific variant is meaningless until the faction is known. At
launch the portal deterministically chooses an unused faction, resolves its
leader, resolves the requested personality, and persists the card text and
SHA-256 hash on that match seat. Reopening, recovering, or retrying the same
match does not reroll either value.

`docs/personality-cards.md` is the authored source for the 56 built-in cards:
four variants for each of fourteen factions. The portal parses and validates
that complete library at startup. Missing or incomplete authored content is a
startup error rather than a silent generic fallback.

## Authoritative prompt order

The managed Hermes adapter does not use Hermes' ordinary general-purpose
system prompt. It installs one immutable SMACX system contract whose layers are:

1. semantic game and fair-play rules;
2. exact match identity, seat, rules, and opening-briefing requirements;
3. the resolved personality wrapper and authored card, when enabled.

The combined prompt is hashed before launch and the strict Hermes adapter
verifies that hash. A personality may influence strategy, diplomacy, risk,
trust, and interpretation, but cannot override tool guards, fair play, or the
actual game state.

## Why identity resolves before DirectPlay join

A live two-process Wine/DirectPlay experiment tested whether SMACX could rename
an already joined participant. DirectPlay returned success for the rename, but
neither lobby nor the running game's chat identity changed. Therefore the
public name must be final when the host or join operation creates the native
participant.

The bridge now exposes the stock new-game faction selector as a guarded
semantic action. Managed seats select their pre-resolved unique faction without
screenshots, keyboard input, or coordinates. A second live test proves that:

- both clients see the pre-resolved leader names in the lobby;
- duplicate faction selection is rejected;
- the selected factions propagate to the host; and
- both names survive game start and appear in native chat.

The running faction remains authoritative. Immediately before a Hermes profile
is prepared, the control plane compares the native faction choice and observed
faction name with the locked seat identity. If they diverge, it refuses to
start the agent instead of attaching the wrong leader or personality to a
different faction. This fail-closed behavior is necessary because the public
DirectPlay name cannot be repaired after joining.

Loaded games continue to use their required saved-faction bindings. Installed
multiplayer scenarios use their native scenario selector; an incompatible
requested stock identity is therefore reported rather than silently
transplanted.

## Human display names

Account usernames are private sign-in identifiers. A separate, freely editable
public display name is used in lobbies, native DirectPlay, chat, votes, and
history. Display names are 1–31 printable ASCII characters, unique without
regard to case, and all fourteen AI leader names are reserved.

Changing a display name does not rewrite an already materialized match. The
old snapshot remains reserved while any such match is unfinished, preventing a
second account from taking it and creating an ambiguous reconnect.

Invited names create provisional LAN identities. A later registration claims
the invitation by the exact case-insensitive public display name while still
allowing an unrelated sign-in username.

## Separate native clients and collision handling

People may still join an agent-hosted lobby from their own game when the lobby
allows native clients. Native names are matched case-insensitively to explicitly
reserved public display names.

The host bridge has a guarded semantic participant-removal action. A live
two-client test verifies its non-visual, no-confirmation path. When a native
client joins with an unreserved name, or a later client duplicates a name
already present, the managed host removes that participant before game start
and records a human-readable rejection reason. The earliest correctly reserved
participant keeps the seat. Managed-only lobbies continue to reject native
clients entirely.

In a human-hosted native lobby no managed process owns the host privilege, so
the platform cannot remove another person's client. It instead blocks readiness
and reports the duplicate or unexpected identity to the human host.

## Canonical storage

This project has not shipped a database release. Faction, leader, requested and
resolved personality, prompt text, and prompt hash are part of the single
canonical schema. There are no upgrade migrations. A development database from
an older branch must be backed up and recreated when its canonical schema ID no
longer matches.

## Verification

The ordinary automated suite validates the fourteen-faction catalog, all 56
cards, deterministic Random resolution, canonical schema, invitations, public
identity behavior, and portal flows:

```bash
dotnet test Smacx.Agent.slnx
python3 -m py_compile src/*.py scripts/lan_player_identity_live_test.py
```

The real native identity/faction test and the independent host-removal phase
run against two installed SMACX processes:

```bash
PYTHONPATH=src SMACX_RUNTIME_ROOT=/path/to/runtime \
SMACX_TEST_JOIN_GAME_PATH=/path/to/second-game-copy \
python3 scripts/lan_player_identity_live_test.py

PYTHONPATH=src SMACX_RUNTIME_ROOT=/path/to/runtime \
SMACX_TEST_JOIN_GAME_PATH=/path/to/second-game-copy \
SMACX_TEST_DROP_ONLY=1 \
python3 scripts/lan_player_identity_live_test.py
```
