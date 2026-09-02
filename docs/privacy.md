# Privacy and aggregate usage analytics

SMACX Agent uses a maintainer-operated Plausible Analytics deployment to learn
whether the project is being used. This applies to the maintainer's deployment
and to self-hosted copies unless an operator changes the source.

The purpose is deliberately narrow. Small open-source projects can support a
real community without producing many GitHub stars, discussions, or direct
messages. Aggregate activity is a useful signal that continued maintenance,
issue review, and development may matter to someone. The analytics are not used
for advertising or personal profiling.

## What is measured

Top-level portal pages load the analytics script through a first-party path on
their own SMACX Agent host. The tracker source is not vendored into this
Apache-2.0 repository, and events are sent to the maintainer-operated Plausible
deployment.

The integration measures aggregate page views and client-side route changes,
eligible file downloads, outbound-link clicks, and the ordinary browser,
device, referrer, and coarse geographic statistics produced by Plausible.
Plausible does not use tracking cookies or construct cross-site advertising
profiles.

The portal does not add usernames, display names, account credentials, API
keys, lobby or game chat, AI prompts or responses, memories, model-provider
details, game binaries, saves, match state, or installation fingerprints to
analytics events.

Tracking is disabled when the portal is running inside a frame or when an
explicit embedded-view query is present. Managed game and spectator frames are
therefore not treated as ordinary portal navigation.

The in-app `/privacy` page presents the same disclosure to players and server
operators.
