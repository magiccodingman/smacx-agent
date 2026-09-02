# Install SMACX Agent for localhost and LAN

This is the ordinary installation path. It starts the complete persistent
platform—including its Caddy edge and an idle dynamic-DNS helper—but requires
no domain, certificate, or Internet-facing configuration.

## Requirements

- Linux host with Docker Engine and Docker Compose v2
- a legally obtained Alpha Centauri/Alien Crossfire installation directory
  containing `terranx.exe`
- enough free storage for the images and active game workers

SMACX Agent does not include or distribute the game or its assets. The game
directory is mounted read-only and the project builds the managed Proton,
DirectPlay, stream, and agent environment around it.

## Start the platform

```bash
SMACX_GAME_SOURCE="/absolute/path/to/Sid Meier's Alpha Centauri" \
  ./scripts/control-center-up.sh
```

Read the one-time bootstrap token:

```bash
docker compose exec -T control-center dotnet Smacx.Portal.dll bootstrap-token
```

Open `http://127.0.0.1:8080`, sign in as `admin`, and choose the primary
administrator password. Another trusted device opens
`http://HOST-LAN-IP:8080`.

The default trusted ranges are loopback, `10.0.0.0/8`, `172.16.0.0/12`,
`192.168.0.0/16`, IPv6 unique-local, and IPv6 link-local. Override
`SMACX_TRUSTED_NETWORKS` with a comma-separated CIDR list only when the host's
network requires it.

LAN account creation is open by default. An administrator can require
invitations or installation verification on trusted networks from
**Administration → Network access**. Registration never signs the new account
in automatically; the player returns to the login screen.

The `smacx-portal-data`, `smacx-control-data`, knowledge, Graphiti, and Caddy
volumes are persistent. Rebuilding containers does not reset accounts or
campaigns. Back them up through the Control Center operations page before host
maintenance.

To invite remote friends later, do not replace this deployment or create a
second database. Continue with [Internet hosting for friends](internet-hosting.md).
