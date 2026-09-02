# Getting started: localhost and LAN

This is the recommended first installation path. It produces one persistent
SMACX Agent host for localhost, household LAN play, and—if enabled later—the
same invitation-only Internet service. Do not create a second deployment when
you decide to invite remote friends.

SMACX Agent currently targets a Linux Docker host. Browser players can use any
modern desktop or mobile operating system after the host is running.

## What the host needs

- a 64-bit Linux machine;
- [Docker Engine](https://docs.docker.com/engine/install/) and the Docker
  Compose v2 plugin;
- Git;
- outbound HTTPS during the first image build; and
- an existing Alpha Centauri/Alien Crossfire installation directory containing
  `terranx.exe`.

SMACX Agent does not include or distribute the game or its assets. The selected
directory is mounted read-only. The project builds and manages Proton,
DirectPlay, streaming, saves, browser seats, and optional AI seats around that
copy.

The first build downloads and prepares several container images. Allow roughly
30 GB of free storage for the platform, build cache, active workers, and growing
campaign data. Actual use depends on concurrent seats and retained campaigns.

## 1. Verify Docker

Install Docker using the instructions for your distribution, then verify both
the daemon and Compose plugin:

```bash
docker ps
docker compose version
```

SMACX Agent must be able to use Docker without `sudo`. If `docker ps` reports a
permission error, add your account to Docker's group and start a new login
session:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
docker ps
```

Membership in the Docker group is effectively root-level host access. Grant it
only to accounts that are allowed to administer this machine.

## 2. Download SMACX Agent

```bash
git clone https://github.com/magiccodingman/smacx-agent.git
cd smacx-agent
cp .env.example .env
```

Keep this directory. The Compose project name and named volumes provide the
stable identity for accounts, settings, saves, AI conversations, knowledge, and
certificates.

## 3. Locate the game directory

Choose the directory that directly contains `terranx.exe`, not the executable
itself. Common Steam locations include:

```text
~/.local/share/Steam/steamapps/common/Sid Meier's Alpha Centauri
~/.steam/steam/steamapps/common/Sid Meier's Alpha Centauri
~/.var/app/com.valvesoftware.Steam/data/Steam/steamapps/common/Sid Meier's Alpha Centauri
```

GOG and original-disc installations work as well when their extracted/install
directory contains `terranx.exe`. Confirm the exact absolute path:

```bash
find "$HOME" -type f -iname terranx.exe -print 2>/dev/null
```

Edit `.env` and set the discovered directory. Quotes are useful when the path
contains spaces:

```dotenv
SMACX_GAME_SOURCE="/absolute/path/to/Sid Meier's Alpha Centauri"
```

For the ordinary LAN installation, leave these defaults unchanged:

```dotenv
SMACX_PUBLIC_HOSTNAME=
SMACX_DDNS_PROVIDER=off
SMACX_ALLOW_PRIMARY_ADMIN_REMOTE_LOGIN=0
```

There is no Proton/runtime selector in the website. The launcher validates the
game source and prepares the supported compatibility runtime automatically.

## 4. Start the platform

From the repository directory:

```bash
./scripts/control-center-up.sh
```

The first run is substantially slower because it builds the portal, knowledge,
worker, streaming, and managed Hermes images in memory-safe sequence. Later
runs reuse Docker layers.

Verify the persistent services:

```bash
docker compose ps
curl --fail http://127.0.0.1:8080/healthz
```

The normal stack contains `knowledge-service`, `control-api`, `control-center`,
`edge`, and an idle-or-configured `ddns` helper. Game and AI seat containers are
created only when matches need them.

If startup fails, begin with [Troubleshooting](troubleshooting.md). Do not delete
volumes to solve an ordinary startup error.

## 5. Create the first administrator

There is no default password. Read the one-time bootstrap token:

```bash
docker compose exec -T control-center dotnet Smacx.Portal.dll bootstrap-token
```

Open <http://127.0.0.1:8080>, sign in as `admin`, enter the token, and choose the
administrator password. The token stops working after successful setup.

If that password is ever lost, create a 30-minute reset ticket without deleting
data:

```bash
docker compose exec -T control-center dotnet Smacx.Portal.dll admin-reset-token admin
```

## 6. Open the table on the LAN

Find the host's private address:

```bash
hostname -I
```

Another household device opens:

```text
http://HOST-LAN-IP:8080
```

For example, a host at `192.168.1.25` is reached at
`http://192.168.1.25:8080`. Allow inbound TCP 8080 from the trusted LAN in the
host firewall if needed. Do **not** forward port 8080 through the Internet
router.

LAN registration is open by default: a friend creates an account and then signs
in. Registration deliberately does not sign the new account in automatically.
An administrator can require invitations, one-time installation verification,
or both on trusted networks under **Administration → Network access**.

The default trusted ranges cover loopback, RFC 1918 private IPv4, IPv6
unique-local, and IPv6 link-local networks. If the portal incorrectly treats a
private routed network as remote, set `SMACX_TRUSTED_NETWORKS` to a
comma-separated CIDR allowlist before restarting.

Browser players need only the URL. A traditional native DirectPlay player uses
their own game installation and the advanced player-network setup described in
[Network access and play modes](network-access.md).

Plain LAN HTTP supports the full portal plus managed video, mouse, keyboard,
touch, and spectating. Because browsers expose WebCodecs only to secure
contexts, the stream automatically selects its JPEG/WebSocket compatibility
path and omits game audio on that origin. Use the configured trusted HTTPS
hostname (or another certificate trusted by the device) for H.264/WebCodecs
video, game audio, and PWA installation.

## 7. Create the first game

1. Sign in and open **Lobbies**.
2. Choose **Create match** and select the world/rules.
3. Create the waiting room.
4. In staging, take or leave the creator's seat and add humans, native players,
   stock computer factions, or configured AI profiles.
5. Choose available factions or leave them random.
6. Launch when the staging room reports ready.

AI is optional. Human-only and stock-bot games do not need a model provider or
Graphiti.

## Everyday operation

Leave the persistent stack running between games. It uses
`restart: unless-stopped` and keeps its state in named Docker volumes.

Check health and logs:

```bash
docker compose ps
docker compose logs --tail=150 edge control-center control-api knowledge-service ddns
```

Stop the platform without deleting data:

```bash
docker compose stop
```

Start or rebuild it again:

```bash
./scripts/control-center-up.sh
```

Update the source and rebuild while retaining data:

```bash
git pull --ff-only
./scripts/control-center-up.sh
```

Use **Administration → Operations** for platform backups before significant
host maintenance. Never run `docker compose down -v` unless you intentionally
want to erase persistent platform data.

## Next steps

- Learn exactly which access route fits each player in [Network access and play
  modes](network-access.md).
- Send a household player [Joining a SMACX Agent server](joining-a-server.md).
- Extend this same installation for remote friends with [Internet hosting for
  invited friends](internet-hosting.md).
- Install the portal as a desktop/mobile app with [Installable command
  deck](installable-app.md).
