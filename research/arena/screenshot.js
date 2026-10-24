// Screenshot the running console with the pre-installed Playwright Chromium (Node).
//
//   node research/arena/screenshot.js http://127.0.0.1:8765/ out.png [minEvents]
//
// Waits until the page reports it has rendered (data-ready) and has received at least minEvents events over
// SSE, so the capture is of real state, then writes a full-page PNG and prints what the page had.
const { chromium } = require('playwright');

(async () => {
  const [url, out, minEventsArg] = process.argv.slice(2);
  const minEvents = parseInt(minEventsArg || '1', 10);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 2 });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector("[data-ready='1']", { timeout: 30000 });
  await page.waitForFunction(n => window.ARENA && window.ARENA.events.length >= n, minEvents, { timeout: 30000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: out, fullPage: true });
  const n = await page.evaluate(() => window.ARENA.events.length);
  const conn = await page.evaluate(() => window.ARENA.conn);
  const omega = await page.evaluate(() => window.ARENA.state && window.ARENA.state.concurrency.omega_peak);
  await browser.close();
  console.log(`wrote ${out}: ${n} events on the page, connection ${conn}, omega_peak ${omega}`);
})().catch(e => { console.error(e); process.exit(1); });
