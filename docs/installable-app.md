# Installable command deck

SMACX Agent is an installable Progressive Web App (PWA) for the portal, not an
offline copy of the game. Installing gives a focused standalone window,
launcher/home-screen icon, and shortcuts back to the command deck, lobbies,
and spectator deck. It does not cache authenticated pages, secrets, match
state, or game streams.

## Install flow

Open **Install app** from the side navigation, the signed-in account menu, or
the install icon on the signed-out header.

The page captures Chromium's one-use `beforeinstallprompt` event before Blazor
starts. When that event is available, **Install SMACX Agent** opens the native
browser confirmation. After the prompt is used it cannot be replayed; if the
user dismisses it, the page falls back to browser-specific menu instructions.
Safari/iOS and browsers that do not expose the event always receive the manual
steps and an official help link.

The manifest defines stable `/` identity and scope, standalone display,
192×192 and 512×512 ordinary icons, safe-zone maskable variants, theme colors,
and command-deck/lobby/spectator shortcuts. The service worker establishes app
identity only. It intentionally has no gameplay cache, so an installed app can
never show a stale lobby or imply that a live match works offline.

## Secure context

Browsers permit installation from HTTPS origins and from the loopback
development exceptions `localhost` and `127.0.0.1`. Therefore:

- the host can install directly from `http://127.0.0.1:8080`;
- a phone or tablet browsing `http://HOST_LAN_IP:8080` can still use the portal,
  but should not be promised an install prompt; and
- durable installation on another LAN device requires terminating HTTPS for
  the portal with a certificate that device trusts.

The included Caddy edge supplies trusted HTTPS when an invited-friends public
hostname is configured. Keep the host private and invitation-gated; HTTPS does
not turn the Control Center into public matchmaking or anonymous streaming.

Current browser behavior is documented by
[MDN's installability guide](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable)
and [Chrome's manifest requirements](https://developer.chrome.com/docs/lighthouse/pwa/installable-manifest).

## What is and is not installed

The browser installs only the SMACX Agent web identity. The host still owns the
legal game source, Docker-managed compatibility stack, workers, saves, and stream encoding. An
installed client has the same authorization as the corresponding browser
session and must reconnect to the same host to play.

Uninstalling the PWA removes the device shortcut/window only. It does not end a
match, surrender a faction, delete an account, or remove campaign data.
