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

The Control Center container receives the Docker socket after the worker
lifecycle manager and its ownership tests pass. That manager may create
containers and named volumes only under installation-derived
names and labels, mounts game sources read-only, mounts a private managed Proton
copy, creates a unique bridge secret per worker, and never mutates Steam's live
game or Proton directories.

The short-lived Proton copy helper drops every capability except `CHOWN`,
`FOWNER`, and `DAC_OVERRIDE`, which are required to preserve the runtime tree
inside a brand-new managed volume. Its source bind is read-only, its only
writable mount is that new volume, it has no network, and it is removed after
the checksummed manifest is committed. Long-running game and Control Center
containers retain no capabilities.

The private import applies one narrow generated compatibility patch: Proton's
distribution lock path honors `SMACX_PROTON_DIST_LOCK`. Each worker points it
at private tmpfs, and the complete patched runtime is then checksummed and
mounted read-only. This avoids both cross-seat lock contention and mutation of
the shared runtime.

Hermes is the supported permanent harness. An agent maps to a dedicated Hermes
profile and match perspective, while the semantic bridge, MCP contract, and
SQLite memory retain explicit boundaries so they can be secured and tested
independently. Hermes remains the runtime on that boundary.

Each running game worker receives a dedicated MCP sidecar on the same private
network. The sidecar mounts only that worker's bridge secret/state and the
authoritative Control volume, publishes a random loopback-only port, and is
removed when the worker parks. Managed mode mechanically rejects all
agent-requested launch/load/stop operations. The Control Center issues a
secret-free descriptor only after checking the exact worker and sidecar health.
The host adapter converts that descriptor into one Hermes profile per durable
agent and one workspace/session name per match; it disables general Hermes
memory and every terminal, filesystem, computer-use, and visual tool during
gameplay.

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
part of the security boundary. Hermes is the supported permanent harness; the
boundary exists for isolation and testability.

Direct Docker-socket access means compromise of the authenticated Control
Center is equivalent to host compromise. Running as UID 10001, dropping Linux
capabilities, and using a read-only root filesystem reduce ordinary container
risk but do not weaken Docker daemon authority. Consequently the service is
loopback-only by default, has no generic Docker proxy API, validates every
operator input, and mutates only objects with exact installation and purpose
labels.
