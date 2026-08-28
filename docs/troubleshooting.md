# Troubleshooting

## Hermes cannot see the tools

Start a new conversation, then run:

```bash
systemctl --user restart smacx-agent-mcp.service
hermes mcp test smacx
```

The configured endpoint is `http://127.0.0.1:47814/mcp` with no HTTP authentication. The in-game bridge has separate token authentication.

## MCP works but the game is disconnected

Call `smac_launch`, or inspect:

```bash
tail -n 100 runtime/game-launch.log
ss -ltnp | grep 47813
```

The launcher must run through `SteamLinuxRuntime_4`; invoking Proton directly fails on this host's older glibc.

## LAN hosting returns to the main menu

Reinstall native DirectPlay in the isolated prefix:

```bash
./scripts/install_directplay.sh
```

The script verifies the archived Microsoft redistributable checksum, replaces only per-prefix Proton symlinks, registers the 32-bit DLLs, and writes native overrides only to the isolated prefix.

## The game reaches an unsupported modal

Do not use vision or UI input. Call `smac_report_capability_gap` once with the popup label/state, intended decision, missing observation, and missing action, then stop the run. The exact match/session is audited and the MCP process refuses commands, launch, new-game, and load operations. The orchestrator can add and test a typed handler, restart MCP, then resume only in a fresh native session.

The MCP intentionally has no screenshot, click, keyboard, or raw-text tools. If Hermes shows any legacy version of those tools, restart the MCP service and start a new Hermes conversation so its tool schema is rediscovered.
