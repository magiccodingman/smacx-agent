# Joining a SMACX Agent server

This guide is for players. The person running the server should send you either
its LAN address or a private invitation link.

## Joining from the same LAN

1. Open the address supplied by the host, normally
   `http://HOST-LAN-IP:8080`.
2. Choose **Create an account**.
3. Pick a private sign-in username, public display name, and password.
4. Return to the sign-in page and log in.
5. Open **Lobbies**, enter a waiting room, and take an available human seat.

No email is required. The username is used only for signing in. The public
display name is shown in lobbies, game chat, history, and the native game.

Some hosts require invitations or a one-time installation check even on their
LAN. If so, follow the prompts; the rest of the flow is unchanged.

## Joining over the Internet

The host sends a private link similar to:

```text
https://planet.example.net/join#invite=...
```

Use a desktop or laptop with your own Alpha Centauri installation available for
the first setup:

1. Open the complete invitation link. Do not remove the part after `#invite=`.
2. The link is redeemed automatically and opens account creation.
3. Choose a private sign-in username, public display name, and password.
4. Registration returns you to sign-in; log in with the account you just made.
5. When prompted, select the directory containing your own `terranx.exe`.
6. Choose **Verify locally**.
7. After verification, open **Lobbies** and join the host's waiting room.

The invitation works once and expires after 24 hours. Ask the host for a new one
if it is expired, revoked, or was already used.

The browser computes installation fingerprints locally. No executable, DLL,
artwork, audio, text, mod, or other game file is uploaded. Mods are welcome; the
check recognizes supporting game content rather than demanding byte-for-byte
mod parity.

Verification belongs to your account. After completing it once on a desktop,
you may sign in to the same server from a phone, tablet, another browser, or the
installed SMACX Agent PWA without selecting the installation again.

## Playing in the browser

A browser seat streams the real game running on the host. Click or tap the game
to focus input. Keyboard shortcuts, mouse/touch dragging, audio, chat,
fullscreen, reconnect, and adaptive display profiles are handled by the managed
game view.

If the same account opens its seat in two tabs, only one tab controls it. Use
**Take control here** deliberately in the new tab; the previous tab becomes
read-only.

Use the managed **Exit game view** action when leaving. Closing, refreshing, or
navigating away triggers a warning and does not surrender the faction. The seat
remains recoverable according to the lobby's reconnect and parking rules.

## Installing the app

Open **Install app** after signing in. Internet HTTPS addresses and localhost
are eligible for normal PWA installation. A plain `http://HOST-LAN-IP:8080` LAN
address remains fully playable, but browsers are not required to offer app
installation for that insecure origin.

The installed app is a focused window and launcher icon for the same server. It
does not install the original game locally and it does not make matches work
offline. See [Installable command deck](installable-app.md) for browser-specific
instructions.

## Joining with the original game

A host may reserve a native-client seat. That route requires your own compatible
game installation, the exact handle/faction from staging, and network access to
the host's DirectPlay player network. It is intended for a physical LAN or the
host's private Tailscale configuration, not ordinary public Internet port
forwarding.

If the host did not specifically give you native-client instructions, use the
browser route.

## Common problems

- **The link opens but says it is invalid:** ask the host for a fresh invitation.
- **Remote login refuses HTTP:** use the exact `https://` hostname from the host.
- **The folder picker cannot find the game:** select the directory containing
  `terranx.exe`, not the executable itself.
- **You are on a phone during first verification:** complete the one-time check
  on a desktop, then sign in on the phone.
- **The stream is read-only:** another tab may own control, or you may have
  opened a spectator view instead of your player seat.
- **The app-install button is unavailable:** continue in the browser or follow
  the manual instructions on **Install app**.

For additional diagnosis, see [Troubleshooting](troubleshooting.md).
