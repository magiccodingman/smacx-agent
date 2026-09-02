#!/usr/bin/env node
// Browser-independent contract for capability-gap dismissal and ZIP downloads.

import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const storage = new Map();
globalThis.localStorage = {
  getItem: key => storage.has(key) ? storage.get(key) : null,
  setItem: (key, value) => storage.set(key, value),
};

let clicked = false;
let appended = false;
let removed = false;
let requestedUrl = "";
const anchor = {
  href: "", download: "", style: {},
  click: () => { clicked = true; },
  remove: () => { removed = true; },
};
globalThis.document = {
  createElement: tag => {
    assert.equal(tag, "a");
    return anchor;
  },
  body: { appendChild: value => { assert.equal(value, anchor); appended = true; } },
};
globalThis.fetch = async (url, options) => {
  requestedUrl = url;
  assert.equal(options.credentials, "same-origin");
  assert.equal(options.cache, "no-store");
  return { ok: true, blob: async () => new Blob(["fixture"]) };
};

const moduleUrl = pathToFileURL(
  new URL("../portal/Smacx.Portal.Client/wwwroot/js/incident-actions.js", import.meta.url).pathname,
);
const actions = await import(moduleUrl.href);

assert.equal(actions.isIncidentDismissed("incident-one"), false);
actions.dismissIncident("incident-one");
assert.equal(actions.isIncidentDismissed("incident-one"), true);

await actions.downloadFile("api/incidents/one/diagnostic", "incident-one.zip");
assert.equal(requestedUrl, "api/incidents/one/diagnostic");
assert.equal(anchor.download, "incident-one.zip");
assert.equal(appended, true);
assert.equal(clicked, true);
assert.equal(removed, true);

console.log(JSON.stringify({
  event: "pass",
  payload: {
    dismissal_persists: true,
    explicit_fetch_download: true,
    same_origin_credentials: true,
    navigation_not_used: true,
  },
}));
