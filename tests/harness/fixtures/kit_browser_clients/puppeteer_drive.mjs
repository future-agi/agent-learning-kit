// The shop journey through Puppeteer's connect, as an agent using Puppeteer reaches the kit's browser.
// Usage: node puppeteer_drive.mjs <DevTools address>; ALK_PUPPETEER_PREFIX names the folder puppeteer-core is
// installed in. Every step is bounded, so a tab the browser holds fails the run instead of stalling it.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const prefix = process.env.ALK_PUPPETEER_PREFIX;
const resolved = createRequire(`${prefix}/`).resolve('puppeteer-core');
const { default: puppeteer } = await import(pathToFileURL(resolved).href);
const bound = { timeout: 30000 };

const browser = await puppeteer.connect({
  browserURL: process.argv[2],
  defaultViewport: null,
  protocolTimeout: 30000,
});
const page = await browser.newPage();
await page.goto('http://shop.test/journey', { waitUntil: 'load', ...bound });
await page.click('#buy');
await page.click('#q');
await page.keyboard.type('red shoes');
const [popup] = await Promise.all([new Promise((resolve) => page.once('popup', resolve)), page.click('#open')]);
await popup.waitForFunction("document.body && document.body.innerText.includes('popped')", bound);
await page.goto('http://www.example.com/deals', bound).catch(() => {});
await browser.disconnect();
console.log('done');
