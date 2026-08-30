export function measureGameViewport(element) {
  const rect = element.getBoundingClientRect();
  return { width: rect.width, height: rect.height, devicePixelRatio: window.devicePixelRatio || 1,
    touch: matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0,
    orientation: screen.orientation?.type || (rect.width >= rect.height ? 'landscape' : 'portrait'),
    fullscreen: !!document.fullscreenElement };
}
export function watchGameViewport(element, receiver) {
  let timer;
  const notify = () => {
    clearTimeout(timer);
    timer = setTimeout(() => receiver.invokeMethodAsync('OnViewportChanged',
      measureGameViewport(element)), 180);
  };
  const observer = new ResizeObserver(notify);
  observer.observe(element);
  window.addEventListener('orientationchange', notify);
  document.addEventListener('fullscreenchange', notify);
  notify();
  return { dispose: () => {
    clearTimeout(timer); observer.disconnect();
    window.removeEventListener('orientationchange', notify);
    document.removeEventListener('fullscreenchange', notify);
  }};
}
export function loadDisplayPreference() {
  try { return JSON.parse(localStorage.getItem('smacx.display.preference') || '') }
  catch { return { automatic: true, locked: false, profileId: null }; }
}
export function saveDisplayPreference(automatic, locked, profileId) {
  localStorage.setItem('smacx.display.preference', JSON.stringify({ automatic, locked, profileId }));
}
export async function toggleGameFullscreen(element, landscapeOnTouch) {
  if (document.fullscreenElement) {
    await document.exitFullscreen();
    try { screen.orientation?.unlock(); } catch {}
    return false;
  }
  await element.requestFullscreen({ navigationUI: 'hide' });
  if (landscapeOnTouch && (matchMedia('(pointer: coarse)').matches || navigator.maxTouchPoints > 0)) {
    try { await screen.orientation?.lock('landscape'); } catch {}
  }
  return true;
}
