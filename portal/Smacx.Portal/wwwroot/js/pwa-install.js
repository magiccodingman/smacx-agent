(function () {
  const listeners = new Set();
  let deferredPrompt = null;
  let promptConsumed = false;
  let installedEventSeen = sessionStorage.getItem('smacx.pwa.installed') === '1';

  const detect = () => {
    const ua = navigator.userAgent || '';
    const ios = /iPad|iPhone|iPod/.test(ua) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const android = /Android/i.test(ua);
    const edge = /Edg\//.test(ua);
    const firefox = /Firefox\//.test(ua) || /FxiOS\//.test(ua);
    const chrome = !edge && (/Chrome\//.test(ua) || /CriOS\//.test(ua));
    const safari = !chrome && !edge && /Safari\//.test(ua);
    return {
      platform: ios ? 'ios' : android ? 'android' : /Win/.test(ua) ? 'windows' :
        /Mac/.test(ua) ? 'macos' : /Linux/.test(ua) ? 'linux' : 'unknown',
      browser: edge ? 'edge' : firefox ? 'firefox' : chrome ? 'chrome' :
        safari ? 'safari' : 'unknown'
    };
  };

  const displayModeInstalled = () =>
    matchMedia('(display-mode: standalone)').matches ||
    matchMedia('(display-mode: fullscreen)').matches ||
    navigator.standalone === true;

  const state = () => {
    const environment = detect();
    const installed = displayModeInstalled() || installedEventSeen;
    return {
      ...environment,
      installed,
      promptReady: !!deferredPrompt && !promptConsumed && !installed,
      secureContext: window.isSecureContext,
      localhost: ['localhost', '127.0.0.1', '::1'].includes(location.hostname),
      displayMode: displayModeInstalled() ? 'standalone' : 'browser',
      promptConsumed
    };
  };

  const publish = () => {
    const next = state();
    for (const listener of listeners) {
      try { listener(next); } catch { }
    }
  };

  addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    deferredPrompt = event;
    promptConsumed = false;
    publish();
  });

  addEventListener('appinstalled', () => {
    installedEventSeen = true;
    deferredPrompt = null;
    sessionStorage.setItem('smacx.pwa.installed', '1');
    publish();
  });
  addEventListener('focus', publish);
  document.addEventListener('visibilitychange', publish);
  matchMedia('(display-mode: standalone)').addEventListener?.('change', publish);

  if ('serviceWorker' in navigator && window.isSecureContext) {
    addEventListener('load', () => navigator.serviceWorker.register('/service-worker.js', {
      scope: '/'
    }).catch(() => {}));
  }

  window.smacxPwaInstall = {
    getState: state,
    async promptInstall() {
      if (!deferredPrompt || promptConsumed) return { outcome: 'unavailable', state: state() };
      const prompt = deferredPrompt;
      promptConsumed = true;
      deferredPrompt = null;
      await prompt.prompt();
      const choice = await prompt.userChoice;
      publish();
      return { outcome: choice?.outcome || 'dismissed', state: state() };
    },
    subscribe(listener) { listeners.add(listener); listener(state()); return listener; },
    unsubscribe(listener) { listeners.delete(listener); }
  };
})();
