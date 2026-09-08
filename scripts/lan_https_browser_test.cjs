// Run against the configured LAN edge. No ignoreHTTPSErrors or browser bypass flags.
// SMACX_TEST_HTTP_URL=http://LAN-IP:8080 node scripts/lan_https_browser_test.cjs
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const url = process.env.SMACX_TEST_HTTP_URL;
  assert(url, 'Set SMACX_TEST_HTTP_URL to the LAN HTTP entry point');
  const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true });
  try {
    const page = await browser.newPage();
    try { await page.goto(url); }
    catch (error) { assert.match(error.message, /ERR_CERT_AUTHORITY_INVALID/); }
    await page.locator('#details-button').click();
    await page.locator('#proceed-link').click();
    await page.waitForURL(/^https:\/\//);
    await page.waitForLoadState('domcontentloaded');
    const result = await page.evaluate(async () => {
      const ctx = new AudioContext();
      const worklet = URL.createObjectURL(new Blob([
        'class Probe extends AudioWorkletProcessor { process() { return true; } } registerProcessor("probe", Probe);'
      ], { type: 'text/javascript' }));
      await ctx.audioWorklet.addModule(worklet);
      new AudioWorkletNode(ctx, 'probe').disconnect();
      URL.revokeObjectURL(worklet);
      await ctx.close();
      const opus = await AudioDecoder.isConfigSupported({ codec: 'opus', sampleRate: 48000, numberOfChannels: 2 });
      return { secureContext: isSecureContext, audioDecoder: typeof AudioDecoder,
        videoDecoder: typeof VideoDecoder, audioWorkletLoaded: true, opusSupported: opus.supported,
        url: location.href };
    });
    assert.equal(result.secureContext, true);
    assert.equal(result.audioDecoder, 'function');
    assert.equal(result.videoDecoder, 'function');
    assert.equal(result.opusSupported, true);
    console.log(JSON.stringify({ browser: browser.version(), ...result }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
