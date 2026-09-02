# Internet hosting for invited friends

Internet access extends the same SMACX Agent installation used on localhost and
LAN. There is no separate Internet image, Compose profile, database, or game
service. Caddy remains the only browser-facing edge; its TLS site activates when
a public hostname is configured. The DDNS helper remains idle when it is not.

This is intended for a small private friend group, not public matchmaking or an
open streaming service.

Remote friends normally use managed browser seats: video, audio, mouse,
keyboard, chat, and reconnect stay inside the HTTPS connection. Traditional
native DirectPlay joining remains a LAN/advanced virtual-LAN path and is not
made Internet-safe by Caddy.

## 1. Choose a hostname

Use a domain you own or a hostname from DuckDNS, Dynu, or FreeDNS. Point it at
the host's public IP. Forward public TCP port `443` to the Docker host's port
`443`. Caddy obtains and renews the public certificate automatically.

Set the hostname before starting the same Compose project:

```bash
SMACX_PUBLIC_HOSTNAME="planet.example.net" \
SMACX_GAME_SOURCE="/absolute/path/to/Sid Meier's Alpha Centauri" \
  ./scripts/control-center-up.sh
```

For a persistent configuration, copy `.env.example` to `.env`, set the game
source and public hostname there, then use the ordinary launcher. This is still
the same installation and the same persistent data volumes used for LAN play.

The LAN URL on port 8080 continues to work. Remote players use only
`https://planet.example.net`. Remote login and invitation redemption fail
closed on plain HTTP because passwords, session cookies, and browser hashing
must not cross the Internet without TLS.

## 2. Optional dynamic DNS

The always-present `ddns` service sleeps when unconfigured. Activate one
provider in the host `.env` file:

```dotenv
SMACX_PUBLIC_HOSTNAME=your-name.duckdns.org
SMACX_DDNS_PROVIDER=duckdns
SMACX_DDNS_HOSTNAME=your-name.duckdns.org
```

Write the provider token to the host-only secret file (it is created on first
startup and ignored by Git):

```bash
printf '%s' 'replace-with-provider-token' > runtime/edge-secrets/ddns-token
chmod 640 runtime/edge-secrets/ddns-token
```

Supported provider values are `duckdns`, `dynu`, and `freedns`. Dynu may also
set `SMACX_DDNS_USERNAME`; FreeDNS uses its private update token. The refresh
interval defaults to 300 seconds and may be changed with
`SMACX_DDNS_INTERVAL_SECONDS` (minimum 60).

The token is mounted read-only and is absent from Compose environment output and
Docker inspect-visible container configuration. Do not commit or share `.env`
or the secret directory.

## 3. Invite a player

1. Sign in locally as an administrator.
2. Open **Administration → Network access**.
3. Create a one-time invitation and share its full link privately.
4. The friend opens the link and creates an account within 24 hours.
5. Registration consumes the invitation and returns them to sign-in.

The secret is placed in the URL fragment, so it is not sent in the initial HTTP
request or written to ordinary reverse-proxy access logs. Invitations are
single-use, hashed in the database, rate-limited when redeemed, and revocable.

## 4. One-time local installation check

At the first remote sign-in, a desktop browser asks the player to select their
own Alpha Centauri installation directory. All inspection happens locally in
the browser:

- the directory picker grants access only to the folder the player chose;
- several characteristic game files are hashed with WebCrypto SHA-256;
- only candidate IDs, sizes, and hashes are submitted;
- no executable, DLL, artwork, audio, text, or other game content is uploaded;
- several known content anchors must agree, so renaming an unrelated file is
  insufficient; and
- executable and modded files may differ without invalidating otherwise
  recognized game content.

After success, verification belongs to the account, not that browser. The
player can subsequently sign in from a phone, tablet, PWA, or another desktop.
An administrator can approve a legitimate unsupported release manually from
**Administration → Players**.

The check is a good-faith ownership boundary, not invasive DRM. Its purpose is
to keep a private browser host from becoming an anonymous game distributor.

## Primary administrator and account safety

The original `admin` account is tied to the server's mounted game source. It is
automatically verified and cannot be deleted, deactivated, or demoted. Remote
login for that account is blocked by default. Prefer a separately promoted
administrator for remote administration.

If absolutely necessary, the host may opt in with:

```dotenv
SMACX_ALLOW_PRIMARY_ADMIN_REMOTE_LOGIN=1
```

Deactivating another account invalidates its cookie on the next request and
actively closes its lobby and game-stream connections. Reactivation is
available from the same Players page.

## Spectators and cheating boundary

Spectating always requires a signed-in active account. **Allow spectators**
means authenticated non-participants may receive transport-enforced read-only
streams. A user who has ever occupied a player faction in that campaign cannot
spectate another seat later, even if that user is an administrator or leaves
the match. Non-participating administrators retain observation access for
household support and debugging.
