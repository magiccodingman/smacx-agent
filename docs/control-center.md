# Control Center

The Control Center is the always-on operator service. Starting or stopping a
match will not require taking this service down. Its current foundation owns:

- first-run administrator bootstrap and authenticated browser sessions;
- the authoritative SQLite identity/memory database;
- a permission-restricted local secret vault;
- OpenAI-compatible provider discovery and explicit model/context selection;
- inventories for legal game sources and managed compatibility runtimes.

Worker creation, Hermes profile control, and LAN match orchestration are the
next APIs built on these contracts. The UI intentionally does not claim those
actions are available before their lifecycle managers pass live tests.

## Start once

```bash
docker compose up -d --build control-center
docker compose exec control-center smacx-control bootstrap-token
```

Open `http://127.0.0.1:8080`, leave the username as `admin`, paste the one-time
token, and choose a password of at least 12 characters. The token is stored in
a mode-0600 file, is printed only by the explicit command above, and is deleted
after successful setup. There is no default password. Later restarts preserve
the database and secret vault in `smacx-control-data` and return directly to
the sign-in page.

The default port publication is loopback-only. To listen on a trusted home LAN:

```bash
SMACX_CONTROL_PUBLISH=0.0.0.0:8080 docker compose up -d
```

Plain HTTP exposes the login exchange to devices that can observe that network.
For anything beyond a trusted LAN, place the service behind an HTTPS reverse
proxy and set `SMACX_SECURE_COOKIES=1`. Do not expose it directly to the public
Internet.

## Provider behavior

Enter the complete OpenAI-compatible base URL, normally ending in `/v1`, such
as `http://10.26.26.20:8000/v1`. Discovery requests `GET /models`. If the
endpoint advertises exactly one usable model, it becomes the selected model.
If it advertises more than one, the operator must select one. An optional
context override takes precedence over discovered context metadata.

API keys are written to the secret volume as mode-0600 files and represented
in SQLite only by a reference and SHA-256 integrity fingerprint. List and
status responses expose only `has_api_key`; they never return the value.

## Security model

- Passwords use scrypt with a unique 256-bit salt.
- Browser session and CSRF values are random; only their SHA-256 digests are
  stored in SQLite.
- Session cookies are HttpOnly and SameSite Strict. Cookie-authenticated
  mutations require a matching CSRF header and server-side digest.
- Login/setup attempts are rate-limited per source address.
- Control actions have an append-only audit table protected by SQLite triggers.
- The container runs as UID 10001 with all capabilities dropped, a read-only
  root filesystem, and writable state only in its named volume.

The later worker manager necessarily receives access to the Docker daemon and
therefore becomes a high-authority component. That socket is not mounted in
this foundation service until the manager has its own constrained contract and
tests.
