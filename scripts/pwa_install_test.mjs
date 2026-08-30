import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const root = new URL('../portal/Smacx.Portal/wwwroot/', import.meta.url);
const manifest = JSON.parse(fs.readFileSync(new URL('manifest.webmanifest', root), 'utf8'));
assert.equal(manifest.id, '/');
assert.equal(manifest.scope, '/');
assert.equal(manifest.display, 'standalone');
for (const size of ['192x192', '512x512']) {
  assert.ok(manifest.icons.some(icon => icon.sizes === size && icon.purpose === 'any'));
  assert.ok(manifest.icons.some(icon => icon.sizes === size && icon.purpose === 'maskable'));
}

let installHandler;
let installedHandler;
const listeners = new Map();
const media = { matches: false, addEventListener() {} };
const session = new Map();
const context = {
  navigator: {
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) Chrome/140.0.0.0',
    platform: 'Linux x86_64', maxTouchPoints: 0,
    serviceWorker: { register: async () => ({}) }
  },
  location: { hostname: '127.0.0.1' },
  window: { isSecureContext: true },
  document: { addEventListener(type, handler) { listeners.set(type, handler); } },
  sessionStorage: {
    getItem(key) { return session.get(key) ?? null; },
    setItem(key, value) { session.set(key, value); }
  },
  matchMedia() { return media; },
  addEventListener(type, handler) {
    if (type === 'beforeinstallprompt') installHandler = handler;
    else if (type === 'appinstalled') installedHandler = handler;
    else listeners.set(type, handler);
  },
  setTimeout, clearTimeout, Set, Promise
};
context.window.window = context.window;
Object.assign(context.window, context);
vm.createContext(context);
vm.runInContext(fs.readFileSync(new URL('js/pwa-install.js', root), 'utf8'), context);

assert.equal(context.window.smacxPwaInstall.getState().browser, 'chrome');
assert.equal(context.window.smacxPwaInstall.getState().promptReady, false);

let prevented = false;
let prompted = false;
installHandler({
  preventDefault() { prevented = true; },
  async prompt() { prompted = true; },
  userChoice: Promise.resolve({ outcome: 'accepted' })
});
assert.equal(prevented, true);
assert.equal(context.window.smacxPwaInstall.getState().promptReady, true);
const result = await context.window.smacxPwaInstall.promptInstall();
assert.equal(prompted, true);
assert.equal(result.outcome, 'accepted');
assert.equal(context.window.smacxPwaInstall.getState().promptReady, false);

installedHandler();
assert.equal(context.window.smacxPwaInstall.getState().installed, true);
console.log('PWA install contract passed');
