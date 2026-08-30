// SMACX gameplay, authentication, and streams are intentionally network-only.
// This worker establishes app identity without caching secrets or stale matches.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
