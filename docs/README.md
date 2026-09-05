# SMACX Agent documentation

The in-app **Datalinks Wiki** teaches humans and AI players the game's rules and
mechanics. This directory documents the SMACX Agent platform itself: installing,
hosting, joining, operating, and developing it. Keeping those bodies separate
prevents deployment instructions from polluting game-knowledge retrieval.

## I want to host or join a game

| Goal | Start here |
| --- | --- |
| Install a new Linux host and play on the LAN | [Getting started: localhost and LAN](lan-installation.md) |
| Understand which LAN/Internet/native route to use | [Network access and play modes](network-access.md) |
| Invite friends through a private HTTPS hostname | [Internet hosting for invited friends](internet-hosting.md) |
| Join somebody else's server | [Joining a SMACX Agent server](joining-a-server.md) |
| Install the portal as a desktop/mobile app | [Installable command deck](installable-app.md) |
| Diagnose a failure | [Troubleshooting](troubleshooting.md) |

## I operate an existing host

- [Operator guide](control-center.md)
- [Managed play, display, chat, voting, and recovery](managed-play.md)
- [Runtime and campaign storage](storage-lifecycle.md)
- [Encrypted remote native-player LAN](virtual-lan.md)
- [Windows 11/WSL2 host notes](windows-wsl2.md)
- [Privacy and aggregate usage analytics](privacy.md)

## I configure AI players or knowledge

- [Agent loop and fair-play contract](agent-loop.md)
- [Strategic perception and sovereign cognition](strategic-world.md)
- [Disposable specialist investigations](specialists.md)
- [MCP tool reference](tools.md)
- [Mechanics encyclopedia and copyright boundary](reference-knowledge.md)
- [Optional Graphiti temporal memory](graphiti.md)
- [Campaign storage, journals, checkpoints, and retention](storage-lifecycle.md)
- [Faction identities and personalities](faction-identities-and-personalities.md)
- [Personality-card reference](personality-cards.md)

## I develop or review the platform

- [Architecture](architecture.md)
- [Contributor testing](testing.md)
- [Game Semantics Coverage Matrix](game-semantics-coverage.md)
- [Reproducible no-timer agent benchmarks](benchmarks/README.md)
- [Architecture decisions](adr/)

The canonical access model is [Network access and play
modes](network-access.md). Other documents should link to it instead of defining
a competing meaning for “LAN,” “Internet,” “native,” “public,” or “spectator.”
