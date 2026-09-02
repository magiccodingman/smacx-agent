# Network access and play modes

SMACX Agent is one private game host with several supported ways to reach it.
“Internet hostname” means the server is reachable on the Internet; it does not
mean registration, matchmaking, streams, or games are open to the public.

The ordinary choice is simple:

- people in the house use a browser on the trusted LAN;
- invited remote friends use a browser over HTTPS; and
- traditional native clients stay on a physical LAN or a private virtual LAN.

## Capability matrix

| Route | Address | What the player needs | Account entry | Installation check | Game transport |
| --- | --- | --- | --- | --- | --- |
| Host browser | `http://127.0.0.1:8080` | Browser | First administrator bootstraps; later accounts follow LAN policy | Off by default | Managed browser stream |
| Trusted-LAN browser | `http://HOST-LAN-IP:8080` | Browser | Open registration by default; administrator may require an invitation | Off by default; administrator may require it | Managed browser stream |
| Invited Internet browser | `https://PUBLIC-HOSTNAME` | Browser; desktop once for verification | One-time, 24-hour invitation for a new account | Required once per account | Managed browser stream over HTTPS |
| Physical-LAN native client | Worker/session details from staging | Own compatible game installation | Signed-in/provisional lobby identity | Not the browser verification flow | DirectPlay on the private player network |
| Tailscale native client | Exact worker IPv4 through the tailnet | Own compatible game installation and Tailscale | Signed-in/provisional lobby identity | Not the browser verification flow | DirectPlay over a private routed tailnet |

An administrator can tighten trusted-LAN registration and verification under
**Administration → Network access**. Internet rules are not loosened by those
switches: remote registration still needs an invitation, remote sign-in still
needs HTTPS, and remote accounts still need the one-time installation check.

## Browser play

A managed browser seat runs the original game in an isolated worker on the host.
Video, audio, mouse, keyboard, chat, reconnect, and mobile/PWA access travel
through the authenticated portal. The player's device does not receive a copy of
the host's game files.

Only the edge is browser-facing:

```text
browser -> Caddy edge -> authenticated portal -> authorized seat stream
```

The control API, Docker socket, workers, MCP sidecars, AI harnesses, model
credentials, Graphiti/FalkorDB, and knowledge service stay private.

## Accounts, invitations, and verification

### Trusted LAN

The default trusted CIDRs are loopback, RFC 1918 IPv4 networks, IPv6
unique-local, and IPv6 link-local. On those networks:

- account creation is available without email;
- the administrator may optionally require invitations;
- the administrator may optionally require installation verification; and
- registration returns the player to the sign-in page instead of automatically
  starting a session.

### Internet

For a request outside the configured trusted networks:

- plain HTTP login and invitation redemption fail closed;
- a new account requires an unused invitation that expires after 24 hours;
- the first remote sign-in requires a one-time local installation check; and
- later sign-ins work from the player's other devices, including phones and
  tablets.

The installation check hashes selected files in the player's desktop browser.
Only names, sizes, and SHA-256 fingerprints are submitted. No executable, DLL,
artwork, audio, text, mod, or other game content is uploaded. The check is a
good-faith ownership boundary, not mod-parity enforcement.

The original `admin` account is associated with the host's mounted game source,
is already installation-verified, and is blocked from remote login by default.
Promote a separate account when remote administration is needed. The host can
explicitly opt the primary administrator into remote login, but that is not the
recommended everyday configuration.

## Spectators

Spectating always requires an active signed-in account. Enabling **Allow
spectators** for a lobby permits authenticated non-participants to receive
read-only streams. It never creates an anonymous link.

A user who has occupied a faction in a campaign cannot later watch another
faction's perspective. This remains true after leaving the seat and also applies
to administrators. A non-participating administrator may observe seats for
household support and debugging.

## Native DirectPlay clients

A native client runs its own local copy of the game and joins the managed host's
real DirectPlay session. This is an advanced alternative to browser play.

Do not expose DirectPlay directly to the public Internet. Physical-LAN native
seats require a reachable macvlan, ipvlan, or routed player network. Remote
native seats use the supported private Tailscale subnet-router path in
[Encrypted remote player LAN](virtual-lan.md).

Native DirectPlay commonly uses TCP 47624 and TCP/UDP 2300–2400. Those ports
belong only on the physical/private virtual player network. They are not the
ports for remote browser play and should not be forwarded publicly.

## Ports at a glance

| Port | Purpose | Where it may be reachable |
| --- | --- | --- |
| TCP 8080 | Plain-HTTP portal | Localhost and trusted LAN only |
| TCP 443 | Caddy HTTPS portal | Internet, when invited-friends hosting is configured |
| TCP 47624 and TCP/UDP 2300–2400 | Native DirectPlay | Physical LAN or private routed tailnet only |
| Worker/control/MCP/database/model ports | Internal services | Never publish as player entry points |

In the normal Internet setup, forward only public TCP 443 to the Docker host's
TCP 443. Never forward TCP 8080, the DirectPlay range, worker stream ports, or
control-plane ports.

## Choose the right guide

- New host: [Getting started: localhost and LAN](lan-installation.md)
- Remote host: [Internet hosting for invited friends](internet-hosting.md)
- Player: [Joining a SMACX Agent server](joining-a-server.md)
- Native remote client: [Encrypted remote player LAN](virtual-lan.md)
- Problem diagnosis: [Troubleshooting](troubleshooting.md)
