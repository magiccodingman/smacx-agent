# ADR 0002: Control plane, secrets, and runtime authority

- Status: Accepted
- Date: 2026-08-29

## Context

The platform must stay running while operators create, park, resume, and watch
matches. It must support home-lab access, multiple isolated agent identities,
configurable OpenAI-compatible inference, and user-supplied proprietary game
assets. The eventual worker manager must control Docker, which is effectively
root-equivalent authority over the host and cannot be treated like an ordinary
web application permission.

Hermes already provides valuable long-context execution, profiles, sessions,
and an authenticated dashboard. Those capabilities should be reused without
making the game protocol or durable memory dependent on Hermes internals.

## Decision

SMACX Agent has one persistent, authenticated Control Center and ephemeral or
parked per-seat game workers. The Control Center uses the same authoritative
SQLite database as match memory. Its first-run flow creates a one-time local
bootstrap token and no default password. The default username is `admin`, as a
convenience rather than a credential. Passwords use scrypt; browser session and
CSRF values are stored only as digests.

Provider API keys, bridge tokens, and spectator passwords are file secrets with
mode 0600. SQLite stores only purpose-scoped references and integrity
fingerprints. HTTP responses, audit details, and logs never contain secret
values. Provider discovery follows the OpenAI-compatible `/v1/models`
contract. One model may be selected automatically; multiple models require an
operator choice. Context length can be discovered or explicitly overridden.

The Control Center container initially runs without the Docker socket. Docker
authority is added only with the worker lifecycle manager and its tests. That
manager may create containers and named volumes only under installation-derived
names and labels, mounts game sources read-only, mounts a private managed Proton
copy, creates a unique bridge secret per worker, and never mutates Steam's live
game or Proton directories.

Hermes is the first harness adapter. An agent maps to a dedicated Hermes
profile and match perspective, while the semantic bridge, MCP contract, and
SQLite memory remain harness-neutral. Replacing Hermes later does not require a
new game DLL or a memory migration.

## Network posture

The default Compose publication is `127.0.0.1`. A home-lab operator may bind a
trusted LAN address explicitly. Public exposure requires an HTTPS reverse proxy
and secure cookies. Legacy DirectPlay traffic uses a separately managed virtual
LAN; it is not tunneled through the Control Center's HTTP port.

## Consequences

Operators start one service and manage later matches without Compose teardown.
No agent receives Docker, spectator, provider-secret, or bootstrap credentials.
The web process becomes high authority only when worker orchestration is
enabled, so its request validation, audit trail, and resource-label checks are
part of the security boundary. Hermes remains useful but replaceable.
