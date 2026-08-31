# SMACX Agent

## Play Alien Crossfire like it is 2026

Sid Meier's Alpha Centauri is still brilliant. Getting a 1999 DirectPlay game
to behave like a modern multiplayer game is not.

Windows 11 commonly sends players looking for community compatibility patches.
Linux runs the game remarkably well through Wine and Proton, but multiplayer
still needs legacy DirectX components, a carefully prepared prefix, and enough
ritual that inviting someone for a casual game stops feeling casual. Then a
player disconnects, a long campaign needs to be resumed, or somebody wants to
join from a machine where the game was never configured.

SMACX Agent turns one Linux host and your existing Alien Crossfire installation
into a persistent private-LAN game table. Open a website, create the lobby you
actually want, and play the real game from a browser—or join with a traditional
native client. Add humans, stock computer factions, autonomous LLM players, or
no AI at all.

This is not a remake. It is the original game, made dramatically easier to
host, watch, resume, explore, and share.

<p align="center">
  <img src="docs/images/command-deck.jpg" alt="SMACX Agent command deck showing the private-LAN lobby and match controls" width="100%">
</p>
<p align="center"><sub><strong>The command deck:</strong> one private-LAN home for creating, joining, recovering, and watching games.</sub></p>

> Linux-first and built for localhost or a trusted private LAN. This project
> does not include or distribute Sid Meier's Alpha Centauri, Alien Crossfire,
> or other proprietary game assets. You provide your own installation.

## One host. Any screen. Same Planet.

Create a standard game, an installed scenario, or a deeply customized match
from a friendly web lobby. Pick the world, difficulty, Planet traits, turn
clock, victory conditions, advanced rules, humans, native bots, and optional AI
players without walking through the original setup screens.

<p align="center">
  <img src="docs/images/mixed-lobby.jpg" alt="A seven-seat SMACX lobby with three Qwen AI players, one human, and three native game bots" width="100%">
</p>
<p align="center"><sub><strong>Build the table you want:</strong> humans, named AI profiles, and the original game's bots can share all seven seats.</sub></p>

When the game starts, every managed human gets the real `terranx.exe` streamed
through the portal with video, audio, mouse, keyboard, shortcuts, text entry,
fullscreen, and reconnect support.

<p align="center">
  <img src="docs/images/browser-gameplay-800x600.jpg" alt="The original Alien Crossfire game running at a true native 800 by 600 resolution in a browser" width="100%">
</p>
<p align="center"><sub><strong>This is the real game:</strong> an isolated managed instance of <code>terranx.exe</code>, live in a browser at a mobile-friendly native resolution and ready to reconnect.</sub></p>

- Play at a desktop without installing or patching the game there.
- Open the same private site from a laptop, tablet, phone, Full HD monitor, or
  ultrawide display. Validated native profiles run from 800×600 through
  5120×1440, while instant CSS fitting preserves the game aspect ratio at any
  browser size. Landscape fullscreen is the sweet spot on small screens.
- Use your own native game client when that is what you prefer. The lobby gives
  you the exact host, session, public display name, and faction details.
- Keep one durable public display name and campaign history whether you prefer the
  managed browser path or a traditional native seat.

Your wife does not need to learn Wine prefixes to join from the next room. Your
friend does not need your carefully tuned Linux setup. They open the LAN site,
claim their display name, and take their seat.

<p align="center">
  <img src="docs/images/browser-gameplay-mobile-landscape.jpg" alt="Alien Crossfire fitted into an 844 by 390 phone landscape viewport" width="72%">
</p>
<p align="center"><sub><strong>Planet in your pocket:</strong> the complete 800×600 game, aspect-correct and scroll-free in a phone-sized landscape viewport.</sub></p>

Install the command deck from its own **Install app** page and it becomes a
focused desktop or home-screen app with the same lobbies, play links,
spectating, and reconnect behavior. Chromium gets a real one-click install
prompt when available; Safari, Firefox, and platform-specific fallbacks get
clear local instructions instead of a dead button. The installed experience
remains attached to your private host—gameplay and authentication are never
pretended to be offline.

On the host, loopback works immediately. Phones, tablets, and other LAN
devices use HTTPS so browsers can trust and install the app. Once installed,
Planet can sit beside any other game in a launcher even though the original
Windows executable remains isolated on the Linux host.

The original MENU button also becomes the doorway to managed play. While its
plain root menu is open, a compact human-only control rail appears for
fullscreen, display policy, modern chat, votes, and session recovery. It
vanishes before a native submenu or modal can cover it, never appears for an AI
seat, and never turns browser chrome into part of the agent's action surface.

## Table talk without a 1999 chat window

The native network still carries the conversation, but humans and agents get a
modern durable view of it. Broadcast to everyone, privately contact a faction
you have actually met, or create a named group whose members must each consent
before it becomes active. Durable history survives reconnects and tells you
which player and faction spoke.

A group message is one logical message even though DirectPlay delivers a
private copy to each member. Humans see one clean conversation; an AI sees one
semantic event, not duplicated evidence that accidentally looks more
important. Private and group bodies are authorization-filtered rather than
broadcast through the lobby's live-update channel.

## A campaign that survives real life

Alpha Centauri games can outlive an evening. SMACX Agent treats the campaign as
the durable thing and the running processes as replaceable.

Checkpoint a game, park every managed player, and shut down the disposable game
workers. Later, recover the verified save, restore the exact factions and
seats, reconnect the browsers, and resume the same campaign. AI conversations
and political memory return with it.

The Control Center stays running between games and host restarts. It keeps your
accounts, lobby history, active and parked matches, player associations, saves,
chat, AI profiles, and analytics in one place. Run separate matches for friends
or experiments without repeatedly rebuilding or taking the platform down.

If a browser refreshes, reconnect it. A second tab opens safely as a viewer;
it must explicitly take control, and doing so immediately revokes the old
tab's input stream. Browser back/close receives a leave warning, while the
managed **Exit game view** action leaves the faction reserved. If somebody
reaches the original game's Quit confirmation anyway, the human-only semantic
bridge cancels it and points them back to the managed exit path. If a managed
worker dies, the supervisor reconciles it.

If every managed browser player leaves an unfinished game, a visible ten-minute
grace period begins. Reconnecting cancels it. Otherwise the platform waits for
a verified safe checkpoint and parks the campaign instead of leaving forgotten
Windows processes burning forever in the corner. AI-only simulations keep
running; direct/native seats are never guessed from browser presence.

Disruptive changes are treated like table decisions, not surprise process
kills. Native resolution changes, temporary computer control for an absent
player, seat reclaim, host transfer, parking, and ending a match use persisted
votes among the other connected humans. A passed vote authorizes the request;
it still cannot bypass the game's stable-checkpoint gate. While the game is
unsafe to save, everyone keeps playing. Only after three synchronized native
samples and a verified save does the platform park, reconfigure, recover, and
put the same factions back in their seats.

## Humans-only is a first-class game

You do not need a model endpoint, an API key, Hermes, Graphiti, or any interest
in artificial intelligence to use SMACX Agent.

As a modern human game host it already gives you:

- one responsive lobby directory for standard, custom, and scenario games;
- lightweight private-LAN accounts with durable, case-insensitive public display names;
- browser play that removes per-player game installation and compatibility
  setup;
- installable desktop/mobile command deck with a guided cross-browser PWA
  flow;
- adaptive native resolution from phone-friendly 800×600 to 5K ultrawide,
  adaptive H.264 bitrate, instant per-device fitting, and fullscreen;
- ordinary native-client joining for players who want it;
- durable global, private, and consent-based group chat with player/faction
  attribution;
- reconnectable seats, safe player-approved temporary bot delegation,
  verified saves, parking, and automatic recovery;
- concurrent lobbies and campaigns;
- administrator cross-seat viewing; and
- optional anonymous, read-only spectating for a lobby.

An AI-only tournament is possible. So is a completely ordinary game with your
family. So is watching friends finish a match from your phone without taking a
seat.

## Then add players that have something to say

Connect any OpenAI-compatible model endpoint and turn a discovered model into a
named, versioned player profile. The platform starts and supervises the agent
for its assigned seat; there is no separate host Hermes dashboard to install or
babysit.

Every AI seat can choose its own faction and personality. Standard preserves
the faction's canonical worldview; Friendly, Aggressive, and Extreme explore
recognizable variations; Random locks one variant for that match; and None
leaves temperament entirely to the model. The built-in library contains 56
authored personalities across all fourteen original and Alien Crossfire
factions.

The model profile stays backstage. At the table the AI appears as the faction
leader it actually plays—Lady Deirdre Skye, Chairman Sheng-ji Yang, Datajack
Sinder Roze, Conqueror Marr, and the rest—in the lobby, native game, chat,
history, and spectator tooling. Factions are selected through the native game
semantically, duplicate assignments are prevented, and the final running
faction is verified before the personality-bearing agent is allowed to start.

These players do not aim a vision model at screenshots and hope it clicks the
right pixel. A native bridge inside the real game gives each model structured
observations and guarded semantic actions. The model can inspect its legitimate
world, manage bases and units, research, terraform, trade, negotiate, use
public or private chat, participate in the Planetary Council, pursue enabled
victories, and continue across an extremely long campaign.

Fog of war remains fog. The AI receives its own faction's perspective, not
omniscient save data. It cannot escape into mouse automation when confused.
Every consequential action is checked against current native state, and a
missing capability becomes an explicit development report rather than a secret
clicking fallback.

Before playing, an agent must read and acknowledge the actual match briefing:
its faction, scenario, timer, victory conditions, custom rules, and other
relevant settings. A local Alien Crossfire mechanics encyclopedia gives it the
rules and exact installed-game data it needs without feeding it walkthroughs,
cheese strategies, or hidden match information.

## Diplomacy with a memory longer than one prompt

The fun is not merely whether a model can move a rover. It is whether the
player across the table remembers why it no longer trusts you.

Each AI seat has durable, match-scoped memory for facts, beliefs,
relationships, commitments, goals, summaries, and chat history. An agent can
separate what it observed from what you claimed, remember an unpaid favor,
track a border agreement, question an ally's suspicious explanation, forgive a
betrayal—or decide not to.

Those memories are isolated by match, player, perspective, and native session.
One faction cannot inherit another faction's secrets, and a new campaign does
not begin with grudges from the last one.

For installations that want to push further, optional Graphiti and Neo4j add a
temporal knowledge graph over that political history. SQLite remains the
authoritative memory, so the game keeps working when the experimental graph or
embedding service is unavailable. Graphiti is an enhancement, not a new point
of failure.

## Watch the game—or the experiment

Every managed seat renders independently. An administrator can switch between
players and watch a human or AI screen without disturbing it. A lobby can also
opt into anonymous LAN spectating, with read-only enforcement at the stream
transport rather than a decorative disabled button.

The same Control Center makes SMACX Agent useful as an AI game laboratory.
Keep multiple named versions of a model profile, vary reasoning or context,
run unattended matches, and compare:

- match outcomes and victory types;
- turn duration and errors;
- provider calls;
- input, output, cache, and reasoning tokens; and
- results by durable model/profile version.

Use the built-in reports, CSV export, or the constrained read-only SQL lab.
Watch the screen when a model surprises you, inspect its scoped game records,
and turn a capability gap into a reproducible engineering task.

This makes it possible to ask more interesting questions than “did the bot
win?” Did extra reasoning matter? Did the model negotiate? Did it remember the
deal? Was it strong, merely expensive, or—most importantly—fun to play with?

## What the Control Center owns for you

| Experience | What you get |
| --- | --- |
| **Create** | Typed standard/custom/scenario setup, durable waiting lobbies, seven-seat composition, human/AI/native-bot mixing |
| **Play** | Installable command deck; real game streaming with audio/input/fullscreen; true 800×600-to-5120×1440 profiles; adaptive bitrate; one-controller tab safety; instant fit, reconnect, or native-client joining |
| **Host** | Isolated game workers, prepared Proton environments, DirectPlay setup, exact player identity, concurrent matches |
| **Watch** | Admin seat switching, AI observation, opt-in anonymous spectator deck, worker-enforced read-only streams |
| **Continue** | Connected-player votes, stable-boundary checkpoints, safe temporary bot delegation/reclaim, crash reconciliation, faction restoration |
| **Remember** | Chat history and scoped AI facts, beliefs, relationships, promises, goals, and summaries |
| **Experiment** | Versioned model profiles, telemetry, outcomes, analytics, CSV, and constrained SQL reports |

## Bring your copy and open the table

The managed host currently targets Linux with Docker Engine and Compose. You
also need:

- an existing Alien Crossfire directory containing `terranx.exe`;
- a Proton distribution directory; and
- the February 2010 DirectX redistributable used to prepare native DirectPlay.

Start the persistent platform:

```bash
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
  ./scripts/control-center-up.sh
```

Read the one-time administrator token:

```bash
docker compose exec -T control-center dotnet Smacx.Portal.dll bootstrap-token
```

Open <http://127.0.0.1:8080>, finish the one-time administrator setup, register
the game and Proton locations, and create a lobby. Publish the portal to a
trusted household LAN when other devices should join:

```bash
SMACX_PORTAL_PUBLISH=0.0.0.0:8080 \
SMACX_DIRECTX_REDIST=/absolute/path/to/directx_feb2010_redist.exe \
  ./scripts/control-center-up.sh
```

Browser players need only that private URL. Native players use their own game
installation and may still need the community compatibility work appropriate
to their operating system. AI seats are optional; add a model provider only if
you want them.

The portal is designed for localhost and trusted private LANs, not public
Internet hosting or matchmaking. Do not port-forward it as a public service.

For complete prerequisites, runtime registration, networking, accounts,
providers, lobbies, streams, saves, and recovery, use the
[operator guide](docs/control-center.md).

## Go deeper

The front page tells the product story. Detailed claims and engineering
evidence live where they can stay precise:

- [Project status and validation](docs/project-status.md)
- [Operator guide](docs/control-center.md)
- [Managed play, display, chat, voting, and recovery](docs/managed-play.md)
- [Installable app and LAN HTTPS](docs/installable-app.md)
- [Architecture and trust boundaries](docs/architecture.md)
- [Semantic gameplay coverage](docs/coverage.md)
- [Agent loop and fair-play contract](docs/agent-loop.md)
- [MCP tool reference](docs/tools.md)
- [Mechanics encyclopedia and copyright boundary](docs/reference-knowledge.md)
- [Optional Graphiti temporal memory](docs/graphiti.md)
- [Testing and reproducible evidence](docs/testing.md)
- [Troubleshooting](docs/troubleshooting.md)

## License

SMACX Agent is licensed under Apache License 2.0. Thinker-derived code retains
its MIT notice; see [NOTICE.md](NOTICE.md).
