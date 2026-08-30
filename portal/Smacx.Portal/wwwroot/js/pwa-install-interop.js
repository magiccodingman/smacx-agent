let subscription;

export function getPwaInstallState() {
  return window.smacxPwaInstall?.getState() ?? {
    installed: false, promptReady: false, secureContext: window.isSecureContext,
    localhost: false, displayMode: 'browser', platform: 'unknown',
    browser: 'unknown', promptConsumed: false
  };
}

export function subscribePwaInstall(receiver) {
  subscription = state => receiver.invokeMethodAsync('OnPwaInstallStateChanged', state);
  window.smacxPwaInstall?.subscribe(subscription);
}

export function unsubscribePwaInstall() {
  if (subscription) window.smacxPwaInstall?.unsubscribe(subscription);
  subscription = null;
}

export function promptPwaInstall() {
  return window.smacxPwaInstall?.promptInstall() ?? Promise.resolve({ outcome: 'unavailable' });
}
