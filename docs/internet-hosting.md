# Internet hosting for invited friends

This guide extends the same persistent installation used for localhost and LAN.
There is no separate Internet image, Compose profile, database, or game server.
The included Caddy edge adds HTTPS, and the included DDNS helper can keep a
hostname pointed at a changing home address.

The result is a private table for people you invite. It is not open registration,
public matchmaking, or anonymous game streaming.

Remote friends normally use managed browser seats: video, audio, mouse,
keyboard, chat, and reconnect remain inside one authenticated HTTPS connection.
Traditional native DirectPlay remains a physical-LAN/private-Tailscale route and
must not be exposed by public port forwarding.

## Before opening Internet access

Complete [Getting started: localhost and LAN](lan-installation.md) first. Confirm
that:

- the portal is healthy at `http://127.0.0.1:8080`;
- the administrator can sign in;
- a LAN browser can reach `http://HOST-LAN-IP:8080`;
- a test lobby can be created; and
- **Administration → Game runtime** reports the managed platform ready.

Back up the platform from **Administration → Operations** before changing host
networking.

You also need:

- a hostname you control, or a DuckDNS/Dynu/FreeDNS hostname;
- a publicly reachable home Internet connection;
- router access to forward public TCP 443; and
- a host firewall rule allowing TCP 443.

## 1. Check whether inbound hosting is possible

Find the public IPv4 address seen by the Internet:

```bash
curl -4 https://icanhazip.com
```

Compare it with the WAN/Internet IPv4 address shown by the router. If they do not
match, another upstream router or carrier-grade NAT may sit in front of the
network. Addresses in `100.64.0.0/10` are a common carrier-grade NAT signal.

- If there are two routers, forward TCP 443 through both or place the inner
  router in the outer router's appropriate passthrough/bridge configuration.
- If the ISP uses carrier-grade NAT, ordinary inbound port forwarding will not
  work. Request a public address from the ISP or keep the service LAN-only.
- If using IPv6, publish an AAAA record only when the Docker host actually has
  that public IPv6 address and its firewall admits TCP 443. A stale/unreachable
  AAAA record can make some clients fail even when IPv4 works.

## 2. Give the host a stable private address

Reserve the Docker host's LAN address in the router's DHCP settings, or configure
an appropriate static address. The port-forwarding rule must keep pointing to
the same machine after reboots.

For the examples below, assume:

```text
Docker host LAN address: 192.168.1.25
Public hostname:         planet.example.net
```

## 3. Choose and configure a hostname

### Domain you already own

Create an A record for a name such as `planet.example.net` pointing at the
public IPv4 address. Add an AAAA record only under the IPv6 condition above.

### Dynamic DNS hostname

The included updater supports:

- [DuckDNS](https://www.duckdns.org/)
- [Dynu Dynamic DNS](https://www.dynu.com/en-US/DynamicDNS)
- [FreeDNS](https://freedns.afraid.org/)

Create a hostname with the selected provider and obtain its update token or
password. The helper updates the current public address every five minutes by
default.

Check DNS after creating or changing the record:

```bash
getent ahostsv4 planet.example.net
```

DNS caches take time to expire. Continue only after the result contains the
expected public address.

## 4. Configure the existing deployment

Edit the repository's `.env`:

```dotenv
SMACX_GAME_SOURCE="/absolute/path/to/Sid Meier's Alpha Centauri"
SMACX_PUBLIC_HOSTNAME=planet.example.net
SMACX_ALLOW_PRIMARY_ADMIN_REMOTE_LOGIN=0
```

Use only the hostname—no `https://`, port, path, or trailing slash.

For a fixed public address or externally managed DNS, leave DDNS off:

```dotenv
SMACX_DDNS_PROVIDER=off
SMACX_DDNS_HOSTNAME=
```

For DuckDNS:

```dotenv
SMACX_DDNS_PROVIDER=duckdns
SMACX_DDNS_HOSTNAME=your-name.duckdns.org
```

For Dynu:

```dotenv
SMACX_DDNS_PROVIDER=dynu
SMACX_DDNS_HOSTNAME=your-name.example
SMACX_DDNS_USERNAME=your-dynu-username
```

For FreeDNS:

```dotenv
SMACX_DDNS_PROVIDER=freedns
SMACX_DDNS_HOSTNAME=your-name.example
```

Write the provider's update token/password to the host-only secret file. The
launcher creates the directory and blank file if needed:

```bash
printf '%s' 'replace-with-provider-update-secret' > runtime/edge-secrets/ddns-token
chmod 640 runtime/edge-secrets/ddns-token
```

FreeDNS expects the private token portion of its direct-update URL. Dynu expects
the credential accepted by its NIC update endpoint. Do not put the secret in
`.env`, commit it, paste it into an issue, or share the `runtime/edge-secrets`
directory.

Start or rebuild the same stack:

```bash
./scripts/control-center-up.sh
```

The `edge` and `ddns` containers are already part of the ordinary installation.
The DDNS container remains idle when no provider is configured.

## 5. Forward and allow only HTTPS

In the router, create this rule:

```text
Protocol:        TCP
External port:   443
Internal host:   192.168.1.25
Internal port:   443
```

Router terminology varies: **Port forwarding**, **NAT**, **Virtual server**, or
**Inbound rule** commonly lead to the same setting.

Allow inbound TCP 443 in the Linux host firewall. For a host using UFW:

```bash
sudo ufw allow 443/tcp
sudo ufw status
```

Keep TCP 8080 restricted to localhost/trusted LAN. Do not publicly forward:

- TCP 8080;
- DirectPlay TCP 47624 or TCP/UDP 2300–2400;
- a worker's temporary stream port;
- the control API, MCP, database, Graphiti, knowledge, or model-provider ports;
  or
- the Docker socket.

Caddy obtains and renews the public certificate automatically. SMACX Agent's
edge disables HTTP redirects, so normal deployment needs public TCP 443 rather
than exposing the LAN HTTP service. See Caddy's [automatic HTTPS
documentation](https://caddyserver.com/docs/automatic-https) for its certificate
requirements and challenge behavior.

## 6. Verify HTTPS from outside the LAN

Check container status and certificate/DDNS logs on the host:

```bash
docker compose ps edge ddns control-center
docker compose logs --tail=150 edge ddns
```

Then disconnect a phone from Wi-Fi and open:

```text
https://planet.example.net
```

Testing over mobile data avoids router hairpin-NAT ambiguity. The browser should
show a trusted certificate and the SMACX Agent sign-in page. You can also test:

```bash
curl --fail --show-error https://planet.example.net/healthz
```

Run that command from a machine outside the home network when the router cannot
loop a public hostname back inside.

Household devices may continue using `http://HOST-LAN-IP:8080`. If the router
supports hairpin NAT, the public HTTPS hostname may also work from the LAN. Both
addresses reach the same accounts, lobbies, and campaigns.

## 7. Prepare a remote administrator safely

The original `admin` account is tied to the host's mounted game source. It is
automatically verified and cannot be deleted, deactivated, or demoted. Remote
login for that account is blocked by default.

For remote administration:

1. Create an ordinary account while on the LAN.
2. Promote it under **Administration → Players**.
3. Confirm it has a strong, unique password.
4. Use that account remotely.

Only when there is a specific recovery need should the host opt in:

```dotenv
SMACX_ALLOW_PRIMARY_ADMIN_REMOTE_LOGIN=1
```

## 8. Invite a friend

1. Sign in as an administrator.
2. Open **Administration → Network access**.
3. Optionally enter a private label, such as the friend's name/device.
4. Choose **Create invitation**.
5. Copy the complete HTTPS link and share it privately.

The invitation:

- is single-use;
- expires after 24 hours;
- can be revoked before use;
- is stored as a digest rather than plaintext; and
- places its secret after `#` so ordinary proxy request logs do not receive it.

The friend follows [Joining a SMACX Agent server](joining-a-server.md): open the
link on a desktop, create an account, sign in, and select their own installation
directory once. No game file is uploaded. After verification, the account can
use phones, tablets, PWAs, or other browsers without repeating the check.

Existing verified accounts do not need a new invitation every time they sign in.
An administrator can deactivate an account to revoke its future access and
close its active lobby/stream connections.

## Security and privacy boundaries

- There is no open Internet registration endpoint without a valid invitation.
- Spectating requires an active signed-in account and lobby opt-in.
- Campaign participants cannot spectate enemy seats later.
- Browser stream authorization is seat-scoped and enforced by the server.
- The host's game directory is never served as downloadable files.
- The one-time player check submits only content-free fingerprints.
- Provider credentials and internal services remain behind the private edge.

## Updating or disabling Internet access

When the public IP changes, the configured DDNS helper updates it automatically.
Inspect recent success/failure output with:

```bash
docker compose logs --since=30m ddns
```

To change hostname, update both DNS/DDNS settings and
`SMACX_PUBLIC_HOSTNAME`, then rerun `./scripts/control-center-up.sh`.

To return to LAN-only operation:

1. remove the router's TCP 443 forwarding rule;
2. set `SMACX_PUBLIC_HOSTNAME=` and `SMACX_DDNS_PROVIDER=off` in `.env`;
3. clear the DDNS token file if it is no longer needed; and
4. rerun `./scripts/control-center-up.sh`.

Accounts and campaigns remain intact.

## Troubleshooting Internet access

Continue with [Network and Internet troubleshooting](troubleshooting.md#network-and-internet-access)
for DNS, Caddy certificates, CGNAT/double NAT, invitations, trusted-network
classification, and installation-verification failures.
