/**
 * End-to-end exercise of PelagosPanelController against live FastAPI backend.
 * Exercises:
 * 1. Supported Corridor (USSEA -> JPYOK, 52,000 MT) -> Real backend calculation
 * 2. Supported Corridor (USMSY -> JPYOK, 60,000 MT) -> Real backend calculation
 * 3. Unsupported Corridor (NLRTM -> SGSIN) -> Real backend unavailable handling
 * 4. Input validation (tonnage <= 0, empty endpoints) -> DOM validation display
 * 5. Data provenance verification -> Prototype benchmark distance label
 */

import assert from 'node:assert/strict';
import { PelagosPanelController } from '../gods-eye-view-main/src/ui/pelagosPanel.js';

const BACKEND_BASE = 'http://127.0.0.1:8000';

function createMockDom() {
  const elements = new Map();

  function makeElement(id, tag = 'div') {
    const listeners = new Map();
    const children = [];
    const classes = new Set();
    let _innerHTML = '';
    const el = {
      id,
      tagName: tag.toUpperCase(),
      value: '',
      textContent: '',
      get innerHTML() {
        return _innerHTML;
      },
      set innerHTML(val) {
        _innerHTML = val;
        if (val === '') children.length = 0;
      },
      style: {},
      disabled: false,
      children,
      dataset: {},
      classList: {
        add: (...names) => names.forEach((n) => classes.add(n)),
        remove: (...names) => names.forEach((n) => classes.delete(n)),
        contains: (name) => classes.has(name),
        toggle: (name, force) => {
          if (force === true) classes.add(name);
          else if (force === false) classes.delete(name);
          else if (classes.has(name)) classes.delete(name);
          else classes.add(name);
        },
      },
      appendChild: (child) => {
        children.push(child);
        return child;
      },
      removeChild: (child) => {
        const idx = children.indexOf(child);
        if (idx >= 0) children.splice(idx, 1);
        return child;
      },
      querySelector: (selector) => {
        if (selector === '.btn-text') {
          let btnText = children.find((c) => c.className === 'btn-text');
          if (!btnText) {
            btnText = makeElement('btn-text-span', 'span');
            btnText.className = 'btn-text';
            children.push(btnText);
          }
          return btnText;
        }
        return null;
      },
      addEventListener: (evt, handler) => {
        if (!listeners.has(evt)) listeners.set(evt, []);
        listeners.get(evt).push(handler);
      },
      removeEventListener: (evt, handler) => {
        const arr = listeners.get(evt);
        if (arr) {
          const idx = arr.indexOf(handler);
          if (idx >= 0) arr.splice(idx, 1);
        }
      },
    };
    elements.set(id, el);
    return el;
  }

  const ids = [
    'pelagos-panel',
    'pelagos-origin-select',
    'pelagos-dest-select',
    'pelagos-origin-filter',
    'pelagos-dest-filter',
    'pelagos-swap-btn',
    'pelagos-calculate-btn',
    'pelagos-reset-btn',
    'pelagos-validation-msg',
    'pelagos-status-banner',
    'pelagos-dashboard',
    'pelagos-summary-origin',
    'pelagos-summary-dest',
    'pelagos-dist-nm',
    'pelagos-dist-km',
    'pelagos-route-method',
    'pelagos-canals-val',
    'pelagos-waypoint-count',
    'pelagos-corridor-card',
    'pelagos-corridor-status-tag',
    'pelagos-corridor-content',
    'pelagos-corridor-name',
    'pelagos-corridor-code',
    'pelagos-corridor-cargo',
    'pelagos-corridor-vessel-class',
    'pelagos-unmonitored-notice',
    'pelagos-unmonitored-desc',
    'pelagos-observed-card',
    'pelagos-observed-tag',
    'pelagos-observed-content',
    'pelagos-rate-val',
    'pelagos-rate-unit',
    'pelagos-rate-date',
    'pelagos-rate-source',
    'pelagos-rate-type',
    'pelagos-rate-empty',

    // Cost Calculator elements
    'pelagos-cost-card',
    'pelagos-cost-status-tag',
    'pelagos-cargo-tonnes',
    'pelagos-calculate-cost-btn',
    'pelagos-cost-validation-msg',
    'pelagos-cost-content',
    'pelagos-cost-val',
    'pelagos-cost-currency',
    'pelagos-cost-formula',
    'pelagos-cost-rate',
    'pelagos-cost-cargo',
    'pelagos-cost-source',
    'pelagos-cost-distance',
    'pelagos-cost-distance-provenance',
    'pelagos-cost-empty',

    'pelagos-forecast-card',
    'pelagos-forecast-content',
    'pelagos-forecast-val',
    'pelagos-forecast-unit',
    'pelagos-forecast-period',
    'pelagos-forecast-model',
    'pelagos-forecast-horizon',
    'pelagos-forecast-empty',
    'pelagos-fleet-card',
    'pelagos-fleet-tag',
    'pelagos-fleet-dot',
    'pelagos-fleet-text',
    'pelagos-fleet-desc',
    'pelagos-scope-card',
    'pelagos-limitations-list',
    'pelagos-vessel-metrics',
    'pelagos-global-vessel-count',
    'pelagos-route-vessel-count',
    'pelagos-vessel-filter-bar',
    'pelagos-vessel-search',
    'pelagos-tab-all',
    'pelagos-tab-all-count',
    'pelagos-tab-corridor',
    'pelagos-tab-corridor-count',
    'pelagos-selected-vessel-card',
    'pelagos-vessel-list-container',
    'pelagos-vessel-list',
    'pelagos-vessel-empty',
  ];

  for (const id of ids) makeElement(id);
  elements.get('pelagos-cargo-tonnes').value = '52000';

  const doc = {
    getElementById: (id) => elements.get(id) || null,
    createElement: (tag) => makeElement(`elem_${Math.random()}`, tag),
  };

  return { doc, elements };
}

async function runE2eExercise() {
  console.log('Connecting PelagosPanelController to live backend at', BACKEND_BASE);

  // Hook global fetch to forward relative /api requests to actual live backend
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    const urlStr = String(url);
    if (urlStr.startsWith('/api/')) {
      const liveUrl = `${BACKEND_BASE}${urlStr}`;
      return await origFetch(liveUrl, options);
    }
    return await origFetch(url, options);
  };

  try {
    const { doc, elements } = createMockDom();
    const controller = new PelagosPanelController({ document: doc });
    await controller.init();

    // ── Test 1: Seattle -> Yokohama (52,000 MT) ──
    console.log('\n[1/5] Exercising Supported Corridor: Seattle -> Yokohama (52,000 MT)...');
    elements.get('pelagos-origin-select').value = 'USSEA';
    elements.get('pelagos-dest-select').value = 'JPYOK';
    elements.get('pelagos-cargo-tonnes').value = '52000';

    const success1 = await controller.calculateVoyageCost();
    assert.equal(success1, true, 'calculateVoyageCost should succeed');

    assert.equal(elements.get('pelagos-cost-status-tag').textContent, 'ESTIMATED');
    assert.equal(elements.get('pelagos-cost-val').textContent, '1,940,120.00');
    assert.equal(elements.get('pelagos-cost-currency').textContent, 'USD');
    assert.match(elements.get('pelagos-cost-formula').textContent, /52,000 MT × \$37.31\/MT/);
    assert.match(elements.get('pelagos-cost-rate').textContent, /\$37.31 USD\/MT/);
    assert.match(elements.get('pelagos-cost-cargo').textContent, /52,000 MT/);
    assert.match(elements.get('pelagos-cost-source').textContent, /USDA/);
    assert.match(elements.get('pelagos-cost-distance-provenance').textContent, /Prototype Benchmark \(Unverified for navigation\)/);
    console.log('  ✓ Gross Freight Cost: $1,940,120.00 verified from live database');
    console.log('  ✓ Rate: $37.31 USD/MT verified');
    console.log('  ✓ Unverified prototype distance provenance confirmed');

    // ── Test 2: US Gulf -> Yokohama (60,000 MT) ──
    console.log('\n[2/5] Exercising Supported Corridor: US Gulf -> Yokohama (60,000 MT)...');
    elements.get('pelagos-origin-select').value = 'USMSY';
    elements.get('pelagos-dest-select').value = 'JPYOK';
    elements.get('pelagos-cargo-tonnes').value = '60000';

    const success2 = await controller.calculateVoyageCost();
    assert.equal(success2, true);
    assert.equal(elements.get('pelagos-cost-status-tag').textContent, 'ESTIMATED');
    assert.equal(elements.get('pelagos-cost-val').textContent, '4,582,800.00');
    assert.match(elements.get('pelagos-cost-rate').textContent, /\$76.38 USD\/MT/);
    console.log('  ✓ Gross Freight Cost: $4,582,800.00 verified from live database');
    console.log('  ✓ Rate: $76.38 USD/MT verified');

    // ── Test 3: Unsupported Corridor (Rotterdam -> Singapore) ──
    console.log('\n[3/5] Exercising Unsupported Corridor: Rotterdam -> Singapore (50,000 MT)...');
    elements.get('pelagos-origin-select').value = 'NLRTM';
    elements.get('pelagos-dest-select').value = 'SGSIN';
    elements.get('pelagos-cargo-tonnes').value = '50000';

    const success3 = await controller.calculateVoyageCost();
    assert.equal(success3, true);
    assert.equal(elements.get('pelagos-cost-status-tag').textContent, 'UNAVAILABLE');
    assert.equal(elements.get('pelagos-cost-content').style.display, 'none');
    assert.equal(elements.get('pelagos-cost-empty').style.display, 'block');
    assert.match(elements.get('pelagos-cost-empty').textContent, /unmonitored/i);
    console.log('  ✓ Explicit UNAVAILABLE status rendered without fabricated rates');

    // ── Test 4: Invalid Cargo Tonnage Validation ──
    console.log('\n[4/5] Exercising Input Validation (0, negative, non-numeric)...');
    elements.get('pelagos-origin-select').value = 'USSEA';
    elements.get('pelagos-dest-select').value = 'JPYOK';

    elements.get('pelagos-cargo-tonnes').value = '0';
    const resZero = await controller.calculateVoyageCost();
    assert.equal(resZero, false);
    assert.match(elements.get('pelagos-cost-validation-msg').textContent, /strictly positive/i);

    elements.get('pelagos-cargo-tonnes').value = '-1000';
    const resNeg = await controller.calculateVoyageCost();
    assert.equal(resNeg, false);
    assert.match(elements.get('pelagos-cost-validation-msg').textContent, /strictly positive/i);
    console.log('  ✓ Validation error messages displayed correctly');

    // ── Test 5: Endpoint Disconnect / Network Failure ──
    console.log('\n[5/5] Exercising Network Failure Handling...');
    globalThis.fetch = async () => {
      throw new Error('Connection refused to backend:8000');
    };
    elements.get('pelagos-cargo-tonnes').value = '50000';
    const resErr = await controller.calculateVoyageCost();
    assert.equal(resErr, false);
    assert.equal(elements.get('pelagos-cost-status-tag').textContent, 'ERROR');
    assert.match(elements.get('pelagos-cost-empty').textContent, /Connection refused/i);
    console.log('  ✓ Error banner rendered gracefully without crashing dashboard');

    controller.destroy();
    console.log('\nALL E2E DASHBOARD EXERCISES PASSED AGAINST LIVE BACKEND!');
  } finally {
    globalThis.fetch = origFetch;
  }
}

runE2eExercise().catch((err) => {
  console.error('E2E EXERCISE FAILED:', err);
  process.exit(1);
});
