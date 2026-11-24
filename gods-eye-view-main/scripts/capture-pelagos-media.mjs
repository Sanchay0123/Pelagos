import puppeteer from 'puppeteer';
import path from 'node:path';
import fs from 'node:fs';

const SCREENSHOTS_DIR = path.resolve('../docs/media/screenshots');
const FRAMES_DIR = path.resolve('../scratch/video_frames');
fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });
fs.mkdirSync(FRAMES_DIR, { recursive: true });

async function run() {
  console.log('Launching headless browser...');
  const browser = await puppeteer.launch({
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-web-security',
      '--use-gl=angle',
      '--use-angle=swiftshader',
      '--window-size=1920,1080',
    ],
    defaultViewport: {
      width: 1920,
      height: 1080,
      deviceScaleFactor: 1.5,
    },
  });

  const page = await browser.newPage();
  page.on('console', msg => console.log('PAGE LOG:', msg.text()));
  page.on('pageerror', err => console.log('PAGE ERROR:', err.message));

  console.log('Navigating to Pelagos command deck (http://localhost:4173/?welcome=0)...');
  await page.goto('http://localhost:4173/?welcome=0', {
    waitUntil: 'networkidle2',
    timeout: 45000,
  });

  // Wait for Cesium canvas or container
  console.log('Waiting for application canvas/container...');
  await page.waitForSelector('#cesiumContainer, canvas', { timeout: 30000 });
  await new Promise(r => setTimeout(r, 4000));

  // Screenshot 1: Command Deck 3D Overview
  const shot1 = path.join(SCREENSHOTS_DIR, 'command_deck_3d.png');
  await page.screenshot({ path: shot1 });
  console.log(`Saved Screenshot 1: ${shot1}`);

  // Expand Pelagos Panel
  console.log('Expanding Pelagos Intelligence Panel...');
  await page.evaluate(() => {
    const panel = document.getElementById('pelagos-panel');
    if (panel) {
      panel.classList.remove('collapsed');
      panel.style.display = 'block';
    }
  });
  await new Promise(r => setTimeout(r, 2000));

  // Select Ports: USHOU to JPYOK
  console.log('Configuring Route: USHOU -> JPYOK...');
  await page.evaluate(() => {
    const orig = document.getElementById('pelagos-origin-select');
    const dest = document.getElementById('pelagos-dest-select');
    if (orig) {
      orig.value = 'USHOU';
      orig.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (dest) {
      dest.value = 'JPYOK';
      dest.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  await new Promise(r => setTimeout(r, 1000));

  // Click Calculate Route
  console.log('Clicking Calculate Route...');
  await page.evaluate(() => {
    const btn = document.getElementById('pelagos-calculate-btn');
    if (btn) btn.click();
  });

  console.log('Waiting for route calculation and Cesium polyline render...');
  await new Promise(r => setTimeout(r, 6000));

  // Screenshot 2: Maritime Routing & Corridor
  const shot2 = path.join(SCREENSHOTS_DIR, 'maritime_routing_corridor.png');
  await page.screenshot({ path: shot2 });
  console.log(`Saved Screenshot 2: ${shot2}`);

  // Scroll to Charter Readiness Card
  console.log('Scrolling to Charter Readiness Audit...');
  await page.evaluate(() => {
    const el = document.getElementById('pelagos-readiness-card');
    if (el) el.scrollIntoView({ behavior: 'instant', block: 'start' });
  });
  await new Promise(r => setTimeout(r, 2000));

  // Screenshot 3: Charter Readiness Audit
  const shot3 = path.join(SCREENSHOTS_DIR, 'charter_readiness_audit.png');
  await page.screenshot({ path: shot3 });
  console.log(`Saved Screenshot 3: ${shot3}`);

  // Scroll to Candidate Vessel Suitability
  console.log('Scrolling to Candidate Vessel Suitability...');
  await page.evaluate(() => {
    const el = document.getElementById('pelagos-suitability-card');
    if (el) el.scrollIntoView({ behavior: 'instant', block: 'start' });
  });
  await new Promise(r => setTimeout(r, 2000));

  // Screenshot 4: Candidate Vessel Suitability
  const shot4 = path.join(SCREENSHOTS_DIR, 'candidate_vessel_suitability.png');
  await page.screenshot({ path: shot4 });
  console.log(`Saved Screenshot 4: ${shot4}`);

  // Navigate to Platform Data Explorer
  console.log('Navigating to Platform Data Explorer (http://localhost:4173/#/explorer)...');
  await page.goto('http://localhost:4173/#/explorer', {
    waitUntil: 'networkidle2',
    timeout: 30000,
  });
  await new Promise(r => setTimeout(r, 4000));

  // Screenshot 5: Platform Data Explorer
  const shot5 = path.join(SCREENSHOTS_DIR, 'platform_data_explorer.png');
  await page.screenshot({ path: shot5 });
  console.log(`Saved Screenshot 5: ${shot5}`);

  console.log('All 5 screenshots captured successfully!');
  await browser.close();
}

run().catch(err => {
  console.error('Capture failed:', err);
  process.exit(1);
});
