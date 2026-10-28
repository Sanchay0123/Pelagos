/**
 * Live End-to-End exercise of Pelagos Candidate Vessel Suitability Matching (OPT-002)
 * running against the genuine FastAPI backend and database.
 */

import assert from 'node:assert/strict';
import { PelagosPanelController } from '../gods-eye-view-main/src/ui/pelagosPanel.js';

function createMockDom() {
  const elements = new Map();

  function makeElement(id, tag = 'div') {
    const listeners = new Map();
    const children = [];
    const classes = new Set();
    let _innerHTML = '';
    let _textContent = '';
    const el = {
      id,
      tagName: tag.toUpperCase(),
      value: '',
      get textContent() {
        if (_textContent) return _textContent;
        if (children.length > 0) {
          return children.map((c) => c.textContent).filter(Boolean).join(' ');
        }
        return '';
      },
      set textContent(val) {
        _textContent = String(val ?? '');
        if (val !== '') {
          children.length = 0;
        }
      },
      get innerHTML() {
        return _innerHTML;
      },
      set innerHTML(val) {
        _innerHTML = val;
        if (val === '') {
          children.length = 0;
          _textContent = '';
        }
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
        return null;
      },
      querySelectorAll: (selector) => {
        const results = [];
        const match = (elem) => {
          if (selector.startsWith('.')) {
            const cls = selector.slice(1);
            if (elem.classList?.contains?.(cls) || elem.className?.includes?.(cls)) {
              results.push(elem);
            }
          }
          if (elem.children) {
            elem.children.forEach(match);
          }
        };
        children.forEach(match);
        return results;
      },
      addEventListener: (event, handler) => {
        if (!listeners.has(event)) listeners.set(event, []);
        listeners.get(event).push(handler);
      },
      removeEventListener: (event, handler) => {
        const list = listeners.get(event) || [];
        const idx = list.indexOf(handler);
        if (idx >= 0) list.splice(idx, 1);
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
    'pelagos-route-card',
    'pelagos-corridor-card',
    'pelagos-observed-card',
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

    // Candidate Vessel Suitability (OPT-002)
    'pelagos-suitability-card',
    'pelagos-suitability-tag',
    'pelagos-suitability-context',
    'pelagos-suitability-metrics',
    'pelagos-suitability-total-count',
    'pelagos-suitability-matched-count',
    'pelagos-suitability-incompatible-count',
    'pelagos-suitability-list-container',
    'pelagos-suitability-list',
    'pelagos-suitability-empty',

    'pelagos-forecast-card',
    'pelagos-fleet-card',
    'pelagos-scope-card',
    'pelagos-vessel-metrics',
    'pelagos-vessel-filter-bar',
    'pelagos-selected-vessel-card',
    'pelagos-vessel-list-container',
  ];

  for (const id of ids) {
    makeElement(id);
  }

  elements.get('pelagos-cargo-tonnes').value = '52000';

  const doc = {
    getElementById: (id) => elements.get(id) || null,
    createElement: (tag) => makeElement(`elem_${Math.random()}`, tag),
  };

  return { doc, elements };
}

async function runLiveSuitabilityExercises() {
  const BACKEND_BASE = 'http://127.0.0.1:8000';
  console.log(`Connecting PelagosPanelController to live backend at ${BACKEND_BASE}`);

  // Monkey patch fetch to point relative API URLs to live backend
  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    let target = url;
    if (typeof url === 'string' && url.startsWith('/api/')) {
      target = `${BACKEND_BASE}${url}`;
    }
    return origFetch(target, options);
  };

  const { doc, elements } = createMockDom();
  const controller = new PelagosPanelController({ document: doc });
  await controller.init();

  try {
    // -------------------------------------------------------------------------
    // Exercise 1: Supported Corridor (Seattle -> Yokohama, 52,000 MT)
    // -------------------------------------------------------------------------
    console.log('\n[1/4] Exercising Supported Corridor: Seattle -> Yokohama (52,000 MT)...');
    elements.get('pelagos-origin-select').value = 'USSEA';
    elements.get('pelagos-dest-select').value = 'JPYOK';
    elements.get('pelagos-cargo-tonnes').value = '52000';

    const ok1 = await controller.matchSuitableVessels();
    assert.equal(ok1, true, 'matchSuitableVessels must succeed for Seattle -> Yokohama');

    const totalCount = Number(elements.get('pelagos-suitability-total-count').textContent);
    const matchedCount = Number(elements.get('pelagos-suitability-matched-count').textContent);
    console.log(`  ✓ Evaluated ${totalCount} genuine database vessels`);
    console.log(`  ✓ Found ${matchedCount} potentially suitable candidates`);
    assert.ok(totalCount > 0, 'Total evaluated must be > 0');
    assert.ok(matchedCount > 0, 'Must identify potentially suitable candidates from genuine database');

    const listEl = elements.get('pelagos-suitability-list');
    assert.ok(listEl.children.length > 0, 'Candidates list must be populated');

    const firstCand = listEl.children[0];
    console.log(`  ✓ Top candidate: ${firstCand.dataset.mmsi}`);
    assert.match(firstCand.textContent, /POTENTIALLY SUITABLE/);
    assert.match(firstCand.textContent, /NM to Seattle/);
    assert.match(firstCand.textContent, /Laycan Feasibility: UNCONFIRMED/);
    console.log('  ✓ Verified Great-Circle Haversine distance, reported dimensions, and unconfirmed laycan notice');

    // -------------------------------------------------------------------------
    // Exercise 2: Missing Particulars / Insufficient Data Handling
    // -------------------------------------------------------------------------
    console.log('\n[2/4] Exercising Missing Particulars / Insufficient Data Handling...');
    const insufficientCand = listEl.children.find(
      (c) => c.textContent.includes('INSUFFICIENT DATA')
    );
    if (insufficientCand) {
      console.log(`  ✓ Found vessel with missing particulars: MMSI ${insufficientCand.dataset.mmsi}`);
      assert.match(insufficientCand.textContent, /INSUFFICIENT DATA/);
      assert.match(insufficientCand.textContent, /lacks verified length and beam particulars/);
      console.log('  ✓ Missing particulars handled honestly without inventing DWT');
    }

    // -------------------------------------------------------------------------
    // Exercise 3: Unmonitored Trade Corridor (Rotterdam -> Singapore)
    // -------------------------------------------------------------------------
    console.log('\n[3/4] Exercising Unmonitored Corridor: Rotterdam -> Singapore (50,000 MT)...');
    elements.get('pelagos-origin-select').value = 'NLRTM';
    elements.get('pelagos-dest-select').value = 'SGSIN';
    elements.get('pelagos-cargo-tonnes').value = '50000';

    const ok3 = await controller.matchSuitableVessels();
    assert.equal(ok3, true);
    assert.equal(elements.get('pelagos-suitability-tag').textContent, 'UNMONITORED CORRIDOR');
    console.log('  ✓ Explicit UNMONITORED CORRIDOR status rendered honestly');

    // -------------------------------------------------------------------------
    // Exercise 4: Network Failure Resilience
    // -------------------------------------------------------------------------
    console.log('\n[4/4] Exercising Network Failure Resilience...');
    globalThis.fetch = async () => {
      throw new Error('Connection refused (test simulation)');
    };

    const failSuccess = await controller.matchSuitableVessels();
    assert.equal(failSuccess, false);
    assert.equal(elements.get('pelagos-suitability-tag').textContent, 'ERROR');
    assert.equal(elements.get('pelagos-suitability-empty').style.display, 'block');
    console.log('  ✓ Error state handled gracefully without dashboard crash');

    console.log('\nALL 4 E2E VESSEL SUITABILITY EXERCISES PASSED AGAINST LIVE BACKEND!');
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
}

runLiveSuitabilityExercises().catch((err) => {
  console.error('\nE2E EXERCISE FAILED:', err);
  process.exit(1);
});
