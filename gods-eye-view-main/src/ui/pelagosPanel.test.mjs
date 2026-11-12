import assert from 'node:assert/strict';
import test from 'node:test';
import {
  PelagosPanelController,
  VERIFIED_HYDROGRAPHIC_PORTS,
} from './pelagosPanel.js';

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
          return children
            .map((c) => c.textContent)
            .filter(Boolean)
            .join(' ');
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
      querySelectorAll: (selector) => {
        const results = [];
        const match = (elem) => {
          if (selector.startsWith('.')) {
            const cls = selector.slice(1);
            if (
              elem.classList?.contains?.(cls) ||
              elem.className?.includes?.(cls)
            ) {
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
      dispatchEvent: (event) => {
        const list = listeners.get(event.type) || [];
        for (const handler of list) handler(event);
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
    'pelagos-canal-policy',
    'pelagos-swap-btn',
    'pelagos-calculate-btn',
    'pelagos-reset-btn',
    'pelagos-validation-msg',
    'pelagos-status-banner',
    'pelagos-dashboard',
    'pelagos-route-card',
    'pelagos-route-status-tag',
    'pelagos-summary-origin',
    'pelagos-summary-dest',
    'pelagos-dist-nm',
    'pelagos-dist-km',
    'pelagos-route-method',
    'pelagos-canals-val',
    'pelagos-route-canal-policy',
    'pelagos-waypoint-count',
    'pelagos-corridor-card',
    'pelagos-corridor-status-tag',
    'pelagos-corridor-content',
    'pelagos-corridor-name',
    'pelagos-corridor-code',
    'pelagos-corridor-cargo',
    'pelagos-corridor-vessel-class',
    'pelagos-corridor-dist',
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
    'pelagos-rate-dataset-id',
    'pelagos-rate-empty',

    // Voyage Freight Calculator (OPT-001)
    'pelagos-cost-card',
    'pelagos-cost-status-tag',
    'pelagos-cargo-tonnes',
    'pelagos-calculate-cost-btn',
    'pelagos-cost-validation-msg',
    'pelagos-cost-content',
    'pelagos-cost-val',
    'pelagos-cost-currency',
    'pelagos-cost-formula',
    'pelagos-cost-route',
    'pelagos-cost-rate',
    'pelagos-cost-cargo',
    'pelagos-cost-source',
    'pelagos-cost-rate-meta',
    'pelagos-cost-canal-policy',
    'pelagos-cost-distance',
    'pelagos-cost-distance-provenance',
    'pelagos-cost-disclaimer-callout',
    'pelagos-cost-disclaimers',
    'pelagos-cost-empty',

    // Candidate Vessel Suitability (OPT-002)
    'pelagos-suitability-card',
    'pelagos-suitability-tag',
    'pelagos-suitability-context',
    'pelagos-suitability-historical-banner',
    'pelagos-suitability-route-compat',
    'pelagos-suitability-metrics',
    'pelagos-suitability-total-count',
    'pelagos-suitability-matched-count',
    'pelagos-suitability-incompatible-count',
    'pelagos-suitability-list-container',
    'pelagos-suitability-list',
    'pelagos-suitability-empty',
    'pelagos-suitability-commercial-distinction',
    'pelagos-suitability-disclaimers',

    'pelagos-forecast-card',
    'pelagos-forecast-tag',
    'pelagos-forecast-content',
    'pelagos-forecast-val',
    'pelagos-forecast-unit',
    'pelagos-forecast-period',
    'pelagos-forecast-model',
    'pelagos-forecast-horizon',
    'pelagos-forecast-horizons-list',
    'pelagos-forecast-note',
    'pelagos-forecast-empty',
    'pelagos-env-card',
    'pelagos-env-tag',
    'pelagos-env-content',
    'pelagos-env-meta',
    'pelagos-env-stations',
    'pelagos-env-notes',
    'pelagos-env-empty',
    'pelagos-bunker-card',
    'pelagos-bunker-tag',
    'pelagos-bunker-content',
    'pelagos-bunker-provider',
    'pelagos-bunker-hubs',
    'pelagos-bunker-benchmarks',
    'pelagos-bunker-disclaimer',
    'pelagos-bunker-empty',
    'pelagos-fleet-card',
    'pelagos-fleet-tag',
    'pelagos-fleet-dot',
    'pelagos-fleet-text',
    'pelagos-fleet-desc',
    'pelagos-scope-card',
    'pelagos-limitations-list',

    // Vessel intelligence elements
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
    'pelagos-selected-vessel-name',
    'pelagos-selected-vessel-close',
    'pelagos-selected-vessel-mmsi',
    'pelagos-selected-vessel-imo',
    'pelagos-selected-vessel-type',
    'pelagos-selected-vessel-dest',
    'pelagos-selected-vessel-coords',
    'pelagos-selected-vessel-motion',
    'pelagos-selected-vessel-heading',
    'pelagos-selected-vessel-nav',
    'pelagos-selected-vessel-time',
    'pelagos-selected-corridor-row',
    'pelagos-selected-vessel-corridor',
    'pelagos-selected-vessel-focus',
    'pelagos-vessel-list-container',
    'pelagos-vessel-list',
    'pelagos-vessel-empty',
  ];

  for (const id of ids) {
    makeElement(id);
  }

  if (elements.has('pelagos-cargo-tonnes')) {
    elements.get('pelagos-cargo-tonnes').value = '52000';
  }
  if (elements.has('pelagos-canal-policy')) {
    elements.get('pelagos-canal-policy').value = 'allow';
  }

  const doc = {
    getElementById: (id) => elements.get(id) || null,
    createElement: (tag) => makeElement(`elem_${Math.random()}`, tag),
  };

  return { doc, elements };
}

function createMockViewer() {
  const entities = new Map();
  return {
    entities: {
      add: (spec) => {
        entities.set(spec.id, spec);
        return spec;
      },
      remove: (spec) => {
        entities.delete(spec?.id);
      },
      values: () => [...entities.values()],
    },
    camera: {
      flyToBoundingSphere: () => {},
    },
  };
}

test('PelagosPanelController initializes and populates hydrographic ports in alphabetical order', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  assert.ok(controller.ports.length >= 8);
  assert.equal(controller.ports[0].name, 'Baku'); // Alphabetical sort
  assert.ok(controller.ports.some((p) => p.port_id === 'USSEA'));
  assert.ok(controller.ports.some((p) => p.port_id === 'JPYOK'));
  assert.ok(controller.ports.some((p) => p.port_id === 'NLRTM'));
  assert.ok(controller.ports.some((p) => p.port_id === 'SGSIN'));

  controller.destroy();
});

test('Pelagos endpoint validation enforces selection and prevents identical origin and destination', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  // 1. Empty endpoints
  elements.get('pelagos-origin-select').value = '';
  elements.get('pelagos-dest-select').value = '';
  assert.equal(controller.validateEndpoints(), false);
  assert.match(
    elements.get('pelagos-validation-msg').textContent,
    /select both an origin and destination/i,
  );

  // 2. Identical endpoints
  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'USSEA';
  assert.equal(controller.validateEndpoints(), false);
  assert.match(
    elements.get('pelagos-validation-msg').textContent,
    /cannot be the same location/i,
  );

  // 3. Valid distinct endpoints
  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'JPYOK';
  assert.equal(controller.validateEndpoints(), true);
  assert.equal(elements.get('pelagos-validation-msg').textContent, '');

  controller.destroy();
});

test('Pelagos swap button swaps origin and destination values', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  const originSel = elements.get('pelagos-origin-select');
  const destSel = elements.get('pelagos-dest-select');

  originSel.value = 'USSEA';
  destSel.value = 'JPYOK';

  controller.swapEndpoints();

  assert.equal(originSel.value, 'JPYOK');
  assert.equal(destSel.value, 'USSEA');

  controller.destroy();
});

test('Pelagos filterPortOptions narrows dropdown options by port name, country, or UN/LOCODE', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  const originSel = elements.get('pelagos-origin-select');

  // Filter for 'Japan'
  controller.filterPortOptions(originSel, 'Japan');
  assert.equal(originSel.value, 'JPYOK'); // Single exact country match auto-selects

  // Filter for 'rot'
  controller.filterPortOptions(originSel, 'rot');
  assert.equal(originSel.value, 'NLRTM');

  controller.destroy();
});

test('Test A: Seattle -> Yokohama renders MARNET segments and populates monitored PNW-JPN-BULK intelligence', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  const mockRoutePayload = {
    status: 'calculated',
    route_method: 'marnet_50km_generalized_graph',
    routing_mode: 'marnet',
    geometry: [],
    geometry_segments: [
      [
        [-124.75, 48.5],
        [-170.0, 52.0],
        [-180.0, 52.5],
      ],
      [
        [180.0, 52.5],
        [160.0, 48.0],
        [139.73, 35.15],
      ],
    ],
    waypoints: [
      { name: 'Seattle Access', longitude: -124.75, latitude: 48.5 },
      { name: 'Yokohama Uraga Gate', longitude: 139.73, latitude: 35.15 },
    ],
    summary: {
      origin_id: 'USSEA',
      origin_name: 'Seattle',
      destination_id: 'JPYOK',
      destination_name: 'Yokohama',
      total_distance_nm: 4512.26,
      total_distance_km: 8356.7,
      canals_transited: [],
    },
    limitations: ['Bulk grain passage approximation'],
  };

  const mockFreightPayload = {
    status: 'monitored',
    corridor: {
      route_code: 'PNW-JPN-BULK',
      route_name: 'U.S. Pacific Northwest to Japan Bulk Grain Ocean Corridor',
      cargo_type: 'Grain / Dry Bulk',
      vessel_class: 'Panamax / Supramax',
    },
    origin_port: {
      port_id: 'USSEA',
      name: 'Seattle',
      region: 'U.S. Pacific Northwest',
    },
    destination_port: { port_id: 'JPYOK', name: 'Yokohama', region: 'Japan' },
    latest_observation: {
      observation_date: '2026-09-01',
      rate_value: 37.31,
      currency: 'USD',
      unit: 'USD/MT',
      source_name:
        "USDA Agricultural Marketing Service / O'Neil Commodity Consulting",
      is_derived: false,
    },
    baseline_forecast: {
      model_type: 'persistence',
      origin_date: '2026-09-01',
      target_date: '2026-10-01',
      horizon_months: 1,
      predicted_value: 37.31,
      unit: 'USD/MT',
    },
    vessel_availability: {
      status: 'unavailable',
      reason: 'Live AIS telemetry not connected',
    },
    disclaimers: ['Bulk grain scope only'],
  };

  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes('/routing/calculate')) {
      return { ok: true, json: async () => mockRoutePayload };
    }
    if (String(url).includes('/freight/route-intelligence')) {
      return { ok: true, json: async () => mockFreightPayload };
    }
    return { ok: true, json: async () => [] };
  };

  try {
    elements.get('pelagos-origin-select').value = 'USSEA';
    elements.get('pelagos-dest-select').value = 'JPYOK';

    await controller.calculateRoute();

    // Verify Globe Entities: 2 polyline segments + Origin Marker + Destination Marker
    const entities = viewer.entities.values();
    assert.ok(entities.some((e) => e.id === 'pelagos:route:segment_0'));
    assert.ok(entities.some((e) => e.id === 'pelagos:route:segment_1'));
    assert.ok(entities.some((e) => e.id === 'pelagos:marker:origin'));
    assert.ok(entities.some((e) => e.id === 'pelagos:marker:dest'));

    // Verify Dashboard Cards
    assert.equal(elements.get('pelagos-dashboard').style.display, 'flex');
    assert.match(elements.get('pelagos-summary-origin').textContent, /Seattle/);
    assert.match(elements.get('pelagos-summary-dest').textContent, /Yokohama/);
    assert.equal(elements.get('pelagos-dist-nm').textContent, '4,512.3');

    // Corridor
    assert.equal(
      elements.get('pelagos-corridor-status-tag').textContent,
      'MONITORED',
    );
    assert.equal(
      elements.get('pelagos-corridor-code').textContent,
      'PNW-JPN-BULK',
    );
    assert.equal(
      elements.get('pelagos-corridor-cargo').textContent,
      'Grain / Dry Bulk',
    );

    // Observed Rate
    assert.equal(elements.get('pelagos-rate-val').textContent, '37.31');
    assert.equal(elements.get('pelagos-rate-unit').textContent, 'USD/MT');
    assert.equal(elements.get('pelagos-rate-date').textContent, '2026-09-01');
    assert.match(elements.get('pelagos-rate-source').textContent, /USDA/);
    assert.match(elements.get('pelagos-rate-type').textContent, /Not Derived/i);

    // Forecast
    assert.equal(elements.get('pelagos-forecast-val').textContent, '37.31');
    assert.equal(
      elements.get('pelagos-forecast-period').textContent,
      '2026-10-01',
    );
    assert.match(
      elements.get('pelagos-forecast-model').textContent,
      /Persistence/i,
    );

    // Limitations
    assert.ok(elements.get('pelagos-limitations-list').children.length >= 1);
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Test B: Rotterdam -> Singapore calculates route and displays honest unmonitored intelligence without fabricated rates', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  const mockRoutePayload = {
    status: 'calculated',
    route_method: 'marnet_50km_generalized_graph',
    routing_mode: 'marnet',
    geometry: [
      [3.85, 52.0],
      [10.0, 40.0],
      [32.35, 31.4],
      [103.65, 1.22],
    ],
    geometry_segments: [
      [
        [3.85, 52.0],
        [10.0, 40.0],
        [32.35, 31.4],
        [103.65, 1.22],
      ],
    ],
    waypoints: [
      { name: 'Rotterdam Approach', longitude: 3.85, latitude: 52.0 },
      { name: 'Singapore TSS', longitude: 103.65, latitude: 1.22 },
    ],
    summary: {
      origin_id: 'NLRTM',
      origin_name: 'Rotterdam',
      destination_id: 'SGSIN',
      destination_name: 'Singapore',
      total_distance_nm: 8409.79,
      total_distance_km: 15574.93,
      canals_transited: [
        'dover',
        'gibraltar',
        'suez',
        'babelmandeb',
        'malacca',
      ],
    },
  };

  const mockFreightPayload = {
    status: 'unmonitored',
    corridor: null,
    origin_port: {
      port_id: 'NLRTM',
      name: 'Rotterdam',
      region: 'Northern Europe',
    },
    destination_port: {
      port_id: 'SGSIN',
      name: 'Singapore',
      region: 'Southeast Asia',
    },
    latest_observation: null,
    baseline_forecast: null,
    vessel_availability: {
      status: 'unavailable',
      reason: 'Live AIS not connected',
    },
    message: 'Trade lane Rotterdam -> Singapore is unmonitored.',
  };

  const origFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    if (String(url).includes('/routing/calculate')) {
      return { ok: true, json: async () => mockRoutePayload };
    }
    if (String(url).includes('/freight/route-intelligence')) {
      return { ok: true, json: async () => mockFreightPayload };
    }
    return { ok: true, json: async () => [] };
  };

  try {
    elements.get('pelagos-origin-select').value = 'NLRTM';
    elements.get('pelagos-dest-select').value = 'SGSIN';

    await controller.calculateRoute();

    // Verify Globe Entities: route polyline + Origin Marker + Destination Marker
    const entities = viewer.entities.values();
    assert.ok(entities.some((e) => e.id === 'pelagos:route:segment_0'));
    assert.ok(entities.some((e) => e.id === 'pelagos:marker:origin'));
    assert.ok(entities.some((e) => e.id === 'pelagos:marker:dest'));

    // Route overview shows canals
    assert.match(elements.get('pelagos-canals-val').textContent, /Suez/i);
    assert.match(elements.get('pelagos-canals-val').textContent, /Gibraltar/i);
    assert.equal(elements.get('pelagos-dist-nm').textContent, '8,409.8');

    // Corridor: UNMONITORED tag and notice
    assert.equal(
      elements.get('pelagos-corridor-status-tag').textContent,
      'UNMONITORED',
    );
    assert.equal(
      elements.get('pelagos-corridor-content').style.display,
      'none',
    );
    assert.equal(
      elements.get('pelagos-unmonitored-notice').style.display,
      'flex',
    );
    assert.match(
      elements.get('pelagos-unmonitored-desc').textContent,
      /unmonitored/i,
    );

    // No fabricated rate or forecast!
    assert.equal(
      elements.get('pelagos-observed-content').style.display,
      'none',
    );
    assert.equal(elements.get('pelagos-rate-empty').style.display, 'block');
    assert.match(
      elements.get('pelagos-rate-empty').textContent,
      /no freight rate observations/i,
    );

    assert.equal(
      elements.get('pelagos-forecast-content').style.display,
      'none',
    );
    assert.equal(elements.get('pelagos-forecast-empty').style.display, 'block');
    assert.match(
      elements.get('pelagos-forecast-empty').textContent,
      /no predictive forecast model/i,
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos clearRoute removes graphics and resets dashboard', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'JPYOK';

  controller.drawRouteOnGlobe(
    {
      geometrySegments: [
        [
          [-124.75, 48.5],
          [139.73, 35.15],
        ],
      ],
      steps: [],
    },
    { coordinates: [-124.75, 48.5], name: 'Seattle' },
    { coordinates: [139.73, 35.15], name: 'Yokohama' },
  );

  assert.ok(viewer.entities.values().length >= 2);

  controller.clearRoute();

  assert.equal(viewer.entities.values().length, 0);
  assert.equal(elements.get('pelagos-dashboard').style.display, 'none');
  assert.equal(elements.get('pelagos-origin-select').value, '');
  assert.equal(elements.get('pelagos-dest-select').value, '');

  controller.destroy();
});

test('Pelagos updateFleetAvailability handles live AISStream states and distinguishes telemetry from commercial charter availability', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  const origFetch = globalThis.fetch;
  try {
    // 1. Test unconfigured / missing-key state
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({
        status: 'missing-key',
        rows: [],
      }),
    });

    await controller.updateFleetAvailability({
      vessel_availability: { status: 'unavailable' },
    });
    assert.equal(elements.get('pelagos-fleet-tag').textContent, 'UNCONFIGURED');
    assert.match(
      elements.get('pelagos-fleet-text').textContent,
      /AISSTREAM_API_KEY is not configured/,
    );

    // 2. Test live connected state with valid messages
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({
        status: 'live',
        rows: [{ mmsi: '123456789', lat: 35.0, lon: 139.0 }],
        rawRowCount: 42,
      }),
    });

    await controller.updateFleetAvailability({
      vessel_availability: { status: 'unavailable' },
    });
    assert.equal(
      elements.get('pelagos-fleet-tag').textContent,
      'TELEMETRY LIVE',
    );
    assert.match(
      elements.get('pelagos-fleet-text').textContent,
      /Active — Live AIS Telemetry Connected/,
    );
    assert.match(
      elements.get('pelagos-fleet-desc').textContent,
      /AIS telemetry alone does not establish commercial vessel charter availability/,
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos vessel list normalizes and renders vessel details with missing fields handled honestly', async () => {
  const { doc, elements } = createMockDom();
  const controller = new PelagosPanelController({ document: doc });
  await controller.init();

  const nowEpoch = Math.floor(Date.now() / 1000);
  const rows = [
    {
      mmsi: '352001000',
      imo: '9876543',
      name: 'PACIFIC GLORY',
      type: 'Cargo',
      destination: 'YOKOHAMA',
      lat: 48.0,
      lon: -125.0,
      speed: 14.5,
      course: 270.0,
      heading: 271,
      last_position_UTC: '2026-10-19T07:00:00Z',
      last_position_epoch: nowEpoch - 60,
    },
    {
      mmsi: '211000000',
      // All optional fields missing
      lat: 30.0,
      lon: 130.0,
      last_position_epoch: nowEpoch - 1200,
    },
  ];

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({ status: 'live', rows, rawRowCount: 2 }),
    });

    await controller.updateFleetAvailability();

    // Verify UI container revealed
    assert.equal(elements.get('pelagos-vessel-metrics').style.display, 'grid');
    assert.equal(
      elements.get('pelagos-vessel-filter-bar').style.display,
      'flex',
    );
    assert.equal(
      elements.get('pelagos-vessel-list-container').style.display,
      'block',
    );

    const list = elements.get('pelagos-vessel-list');
    assert.equal(list.children.length, 2);

    // Verify fully populated vessel item
    const item1 = list.children.find((c) => c.dataset.mmsi === '352001000');
    assert.ok(item1);
    assert.equal(item1.dataset.mmsi, '352001000');
    const nameEl = item1.querySelectorAll('.vessel-name')[0];
    assert.ok(nameEl);
    assert.equal(nameEl.textContent, 'PACIFIC GLORY');

    // Verify missing fields handling on selection of vessel 2
    controller.selectVessel('211000000', {
      syncGlobe: false,
      flyCamera: false,
    });

    assert.equal(
      elements.get('pelagos-selected-vessel-card').style.display,
      'block',
    );
    assert.equal(
      elements.get('pelagos-selected-vessel-mmsi').textContent,
      '211000000',
    );
    assert.equal(
      elements.get('pelagos-selected-vessel-name').textContent,
      'MMSI 211000000',
    );
    assert.equal(
      elements.get('pelagos-selected-vessel-imo').textContent,
      'Not reported',
    );
    assert.equal(
      elements.get('pelagos-selected-vessel-type').textContent,
      'Not reported',
    );
    assert.equal(
      elements.get('pelagos-selected-vessel-dest').textContent,
      'Not reported',
    );
    assert.equal(
      elements.get('pelagos-selected-vessel-nav').textContent,
      'Not reported',
    );
    assert.equal(
      elements.get('pelagos-selected-vessel-motion').textContent,
      'Not reported · Not reported',
    );
    assert.equal(
      elements.get('pelagos-selected-vessel-heading').textContent,
      'Not reported',
    );
    assert.match(
      elements.get('pelagos-selected-vessel-coords').textContent,
      /30\.0000°N,\s*130\.0000°E/,
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos deduplicates duplicate AIS vessel messages keeping the freshest fix', async () => {
  const { doc, elements } = createMockDom();
  const controller = new PelagosPanelController({ document: doc });
  await controller.init();

  const now = Math.floor(Date.now() / 1000);
  const rows = [
    {
      mmsi: '352001000',
      name: 'SHIP A (OLD)',
      lat: 48.0,
      lon: -125.0,
      speed: 10.0,
      last_position_epoch: now - 500,
    },
    {
      mmsi: '352001000',
      name: 'SHIP A (NEW)',
      lat: 48.2,
      lon: -125.5,
      speed: 15.0,
      last_position_epoch: now - 50,
    },
    {
      mmsi: '400000000',
      name: 'SHIP B',
      lat: 35.0,
      lon: 139.0,
      speed: 12.0,
      last_position_epoch: now - 100,
    },
  ];

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({ status: 'live', rows, rawRowCount: 3 }),
    });

    await controller.updateFleetAvailability();

    const list = elements.get('pelagos-vessel-list');
    assert.equal(list.children.length, 2); // 2 distinct vessels, not 3

    // Verify freshest fix was kept for SHIP A
    const shipA = controller.vesselsNormalized.find(
      (v) => v.mmsi === '352001000',
    );
    assert.ok(shipA);
    assert.equal(shipA.speed, 15.0);
    assert.equal(shipA.name, 'SHIP A (NEW)');
    assert.equal(elements.get('pelagos-tab-all-count').textContent, '2');
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos correctly distinguishes globally tracked vessels from route-relevant vessels and filters by tab', async () => {
  const { doc, elements } = createMockDom();
  const controller = new PelagosPanelController({ document: doc });
  await controller.init();

  // Set an active Seattle -> Yokohama route geometry
  controller.currentRoute = {
    geometrySegments: [
      [
        [-124.75, 48.5],
        [-130.0, 49.0],
        [-150.0, 50.0],
        [-170.0, 49.0],
      ],
      [
        [170.0, 45.0],
        [150.0, 40.0],
        [139.73, 35.15],
      ],
    ],
  };

  const now = Math.floor(Date.now() / 1000);
  const rows = [
    // Vessel 1: 5 NM off the Seattle exit segment -> Near corridor
    {
      mmsi: '111111111',
      name: 'CORRIDOR RUNNER',
      lat: 48.55,
      lon: -124.75,
      speed: 15.0,
      last_position_epoch: now - 30,
    },
    // Vessel 2: Far away in Mediterranean
    {
      mmsi: '222222222',
      name: 'MEDITERRANEAN TRADER',
      lat: 33.0,
      lon: 30.0,
      speed: 12.0,
      last_position_epoch: now - 40,
    },
    // Vessel 3: Far away in Indian Ocean
    {
      mmsi: '333333333',
      name: 'INDIAN OCEAN CLIPPER',
      lat: 5.0,
      lon: 80.0,
      speed: 11.0,
      last_position_epoch: now - 50,
    },
  ];

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({ status: 'live', rows, rawRowCount: 3 }),
    });

    await controller.updateFleetAvailability();

    // Verify global count vs corridor count
    assert.equal(elements.get('pelagos-global-vessel-count').textContent, '3');
    assert.equal(elements.get('pelagos-route-vessel-count').textContent, '1');
    assert.equal(elements.get('pelagos-tab-all-count').textContent, '3');
    assert.equal(elements.get('pelagos-tab-corridor-count').textContent, '1');

    // Default 'all' tab shows all 3 vessels
    assert.equal(elements.get('pelagos-vessel-list').children.length, 3);

    // Switch to 'corridor' tab
    controller.setVesselTab('corridor');
    assert.equal(elements.get('pelagos-vessel-list').children.length, 1);
    assert.equal(
      elements.get('pelagos-vessel-list').children[0].dataset.mmsi,
      '111111111',
    );

    // Clear route -> corridor count becomes '—' and corridor list displays empty notice
    controller.clearRoute();
    assert.equal(elements.get('pelagos-route-vessel-count').textContent, '—');
    assert.equal(elements.get('pelagos-tab-corridor-count').textContent, '—');
    assert.equal(elements.get('pelagos-vessel-empty').style.display, 'block');
    assert.match(
      elements.get('pelagos-vessel-empty').textContent,
      /Calculate a route first/,
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos vessel list and Cesium globe bidirectional selection and camera focusing', async () => {
  const { doc, elements } = createMockDom();

  let selectedGlobeMmsi = null;
  let clearedGlobe = false;
  let worldFocusRecord = null;
  let flewToCartesian = null;

  const prevWindow = globalThis.window;
  const prevCustomEvent = globalThis.CustomEvent;
  const listeners = new Map();
  globalThis.window = {
    addEventListener: (evt, fn) => {
      if (!listeners.has(evt)) listeners.set(evt, []);
      listeners.get(evt).push(fn);
    },
    removeEventListener: (evt, fn) => {
      const list = listeners.get(evt) || [];
      const idx = list.indexOf(fn);
      if (idx >= 0) list.splice(idx, 1);
    },
    dispatchEvent: (evt) => {
      const list = listeners.get(evt.type) || [];
      for (const fn of list) fn(evt);
      return true;
    },
  };
  globalThis.CustomEvent = class CustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  };

  const mockAisLiveLayer = {
    selectById: (mmsi) => {
      selectedGlobeMmsi = String(mmsi);
      return true;
    },
    clearSelection: () => {
      clearedGlobe = true;
      selectedGlobeMmsi = null;
      return true;
    },
  };

  const mockWorldFocus = {
    requestWorldFocus: (spec) => {
      worldFocusRecord = spec;
    },
  };

  const mockViewer = {
    camera: {
      flyTo: (spec) => {
        flewToCartesian = spec.destination;
      },
    },
    entities: {
      add: (s) => s,
      remove: () => {},
    },
  };

  const controller = new PelagosPanelController({
    viewer: mockViewer,
    services: {
      aisLiveVesselsLayer: mockAisLiveLayer,
      worldFocus: mockWorldFocus,
    },
    document: doc,
  });
  await controller.init();

  const rows = [
    {
      mmsi: '352001000',
      name: 'NORTHERN STAR',
      lat: 48.0,
      lon: -125.0,
      speed: 12.0,
      last_position_UTC: '2026-10-19T09:00:00Z',
      last_position_epoch: Math.floor(Date.now() / 1000) - 20,
    },
  ];

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({ status: 'live', rows, rawRowCount: 1 }),
    });

    await controller.updateFleetAvailability();

    // 1. Select vessel from Pelagos list -> triggers globe highlight and camera focus
    controller.selectVessel('352001000', { syncGlobe: true, flyCamera: true });

    assert.equal(controller.selectedMmsi, '352001000');
    assert.equal(selectedGlobeMmsi, '352001000');
    assert.ok(worldFocusRecord);
    assert.equal(worldFocusRecord.id, '352001000');
    assert.ok(flewToCartesian);
    assert.equal(
      elements.get('pelagos-selected-vessel-card').style.display,
      'block',
    );
    assert.equal(
      elements.get('pelagos-selected-vessel-name').textContent,
      'NORTHERN STAR',
    );

    // Verify CSS class toggles on list item
    const vesselListEl = elements.get('pelagos-vessel-list');
    const vesselItem = vesselListEl.children[0];
    assert.ok(vesselItem, 'Vessel item should be rendered in list');
    assert.ok(
      vesselItem.classList.contains('selected'),
      'Vessel item should have selected class',
    );
    assert.ok(
      vesselItem.classList.contains('is-selected'),
      'Vessel item should have is-selected class',
    );

    // Verify inspector timestamp is valid and never contains 'undefined'
    const timeText = elements.get('pelagos-selected-vessel-time').textContent;
    assert.ok(
      timeText.includes('2026-10-19T09:00:00Z'),
      `Expected valid timestamp, got ${timeText}`,
    );
    assert.ok(
      !timeText.includes('undefined'),
      `Timestamp display should never display 'undefined', got ${timeText}`,
    );

    // 2. Clear selected vessel -> clears globe selection and hides inspector
    controller.clearSelectedVessel({ syncGlobe: true });
    assert.equal(controller.selectedMmsi, null);
    assert.equal(clearedGlobe, true);
    assert.equal(
      elements.get('pelagos-selected-vessel-card').style.display,
      'none',
    );
    assert.ok(
      !vesselItem.classList.contains('selected'),
      'Vessel item should not have selected class after clearing',
    );
    assert.ok(
      !vesselItem.classList.contains('is-selected'),
      'Vessel item should not have is-selected class after clearing',
    );

    // 3. Globe to list selection: dispatching gev:entity-selected on window
    const entitySelectedEvent = new globalThis.CustomEvent(
      'gev:entity-selected',
      {
        detail: {
          layerId: 'ais-live-vessels',
          id: 'ais-352001000',
          properties: { mmsi: '352001000' },
        },
      },
    );
    globalThis.window.dispatchEvent(entitySelectedEvent);

    assert.equal(controller.selectedMmsi, '352001000');
    assert.equal(
      elements.get('pelagos-selected-vessel-card').style.display,
      'block',
    );

    // 4. Globe to list deselection: dispatching gev:entity-selection-cleared on window
    const entityClearedEvent = new globalThis.CustomEvent(
      'gev:entity-selection-cleared',
      {
        detail: { layerId: 'ais-live-vessels' },
      },
    );
    globalThis.window.dispatchEvent(entityClearedEvent);

    assert.equal(controller.selectedMmsi, null);
    assert.equal(
      elements.get('pelagos-selected-vessel-card').style.display,
      'none',
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
    globalThis.window = prevWindow;
    globalThis.CustomEvent = prevCustomEvent;
  }
});

test('Pelagos gracefully handles vessel disappearing from live cache without broken state', async () => {
  const { doc, elements } = createMockDom();
  const controller = new PelagosPanelController({ document: doc });
  await controller.init();

  const now = Math.floor(Date.now() / 1000);
  const rowsInitial = [
    {
      mmsi: '352001000',
      name: 'OCEAN PIONEER',
      lat: 48.0,
      lon: -125.0,
      last_position_epoch: now - 10,
    },
  ];

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({ status: 'live', rows: rowsInitial, rawRowCount: 1 }),
    });

    await controller.updateFleetAvailability();
    controller.selectVessel('352001000', { syncGlobe: false });
    assert.equal(controller.selectedMmsi, '352001000');
    assert.equal(
      elements.get('pelagos-selected-vessel-card').style.display,
      'block',
    );

    // Next update arrives: vessel aged out and is not present in cache
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({ status: 'live', rows: [], rawRowCount: 0 }),
    });

    // Does not throw error, clears selection state safely
    await controller.updateFleetAvailability();
    assert.equal(controller.selectedMmsi, null);
    assert.equal(
      elements.get('pelagos-selected-vessel-card').style.display,
      'none',
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos search query filters vessel list and handles empty search results', async () => {
  const { doc, elements } = createMockDom();
  const controller = new PelagosPanelController({ document: doc });
  await controller.init();

  const now = Math.floor(Date.now() / 1000);
  const rows = [
    {
      mmsi: '352001000',
      name: 'EVER GIVEN',
      type: 'Cargo',
      lat: 48.0,
      lon: -125.0,
      last_position_epoch: now - 10,
    },
    {
      mmsi: '211000000',
      name: 'SEASPAN LEADER',
      type: 'Container',
      lat: 35.0,
      lon: 139.0,
      last_position_epoch: now - 20,
    },
  ];

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({ status: 'live', rows, rawRowCount: 2 }),
    });

    await controller.updateFleetAvailability();
    assert.equal(elements.get('pelagos-vessel-list').children.length, 2);

    // Search for 'GIVEN'
    controller.vesselSearchQuery = 'GIVEN';
    controller.refreshVesselData();
    assert.equal(elements.get('pelagos-vessel-list').children.length, 1);
    assert.equal(
      elements.get('pelagos-vessel-list').children[0].dataset.mmsi,
      '352001000',
    );

    // Search for non-matching string
    controller.vesselSearchQuery = 'NONEXISTENT';
    controller.refreshVesselData();
    assert.equal(elements.get('pelagos-vessel-list').children.length, 0);
    assert.equal(elements.get('pelagos-vessel-empty').style.display, 'block');
    assert.match(
      elements.get('pelagos-vessel-empty').textContent,
      /No tracked vessels match "NONEXISTENT"/,
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos Voyage Freight Calculator input validation enforces positive cargo tonnage and handles boundary cases', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'JPYOK';

  // 1. Zero tonnage
  elements.get('pelagos-cargo-tonnes').value = '0';
  const resZero = await controller.calculateVoyageCost();
  assert.equal(resZero, false);
  assert.match(
    elements.get('pelagos-cost-validation-msg').textContent,
    /strictly positive/i,
  );
  assert.equal(
    elements.get('pelagos-cost-status-tag').textContent,
    'INVALID INPUT',
  );

  // 2. Negative tonnage
  elements.get('pelagos-cargo-tonnes').value = '-500';
  const resNeg = await controller.calculateVoyageCost();
  assert.equal(resNeg, false);
  assert.match(
    elements.get('pelagos-cost-validation-msg').textContent,
    /strictly positive/i,
  );

  // 3. Non-numeric tonnage
  elements.get('pelagos-cargo-tonnes').value = 'abc';
  const resNaN = await controller.calculateVoyageCost();
  assert.equal(resNaN, false);
  assert.match(
    elements.get('pelagos-cost-validation-msg').textContent,
    /strictly positive/i,
  );

  // 4. Missing endpoint selection
  elements.get('pelagos-origin-select').value = '';
  elements.get('pelagos-cargo-tonnes').value = '52000';
  const resMissing = await controller.calculateVoyageCost();
  assert.equal(resMissing, false);
  assert.match(
    elements.get('pelagos-cost-validation-msg').textContent,
    /select both an origin and destination/i,
  );

  controller.destroy();
});

test('Pelagos Voyage Freight Calculator renders gross freight cost, currency, formula, rate, and unverified distance provenance for supported corridor', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'JPYOK';
  elements.get('pelagos-cargo-tonnes').value = '52000';

  const mockCostPayload = {
    status: 'calculated',
    cargo_tonnes: 52000.0,
    cargo_unit: 'MT',
    route: {
      route_code: 'PNW-JPN-BULK',
      route_name: 'U.S. Pacific Northwest to Japan Bulk Grain Ocean Corridor',
      origin_region: 'U.S. Pacific Northwest',
      destination_region: 'Japan',
    },
    applicable_rate: {
      rate_value: 37.31,
      currency: 'USD',
      unit: 'USD/MT',
      observation_date: '2026-09-01',
      source_name:
        "USDA Agricultural Marketing Service / O'Neil Commodity Consulting",
      provenance_validated: true,
    },
    distance: {
      distance_nm: 4403.4,
      distance_unit: 'NM',
      distance_source: 'prototype_operational_routing',
    },
    gross_freight_cost: 1940120.0,
    gross_freight_cost_currency: 'USD',
    calculation_formula: 'gross_freight_cost = cargo_tonnes * rate_per_tonne',
  };

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (url) => {
      if (String(url).includes('/calculator/voyage-freight')) {
        return { ok: true, json: async () => mockCostPayload };
      }
      return { ok: true, json: async () => ({}) };
    };

    const success = await controller.calculateVoyageCost();
    assert.equal(success, true);

    // Verify status tag and displays
    assert.equal(
      elements.get('pelagos-cost-status-tag').textContent,
      'ESTIMATED',
    );
    assert.equal(elements.get('pelagos-cost-content').style.display, 'block');
    assert.equal(elements.get('pelagos-cost-empty').style.display, 'none');

    // Arithmetic & currency
    assert.equal(elements.get('pelagos-cost-val').textContent, '1,940,120.00');
    assert.equal(elements.get('pelagos-cost-currency').textContent, 'USD');
    assert.match(
      elements.get('pelagos-cost-formula').textContent,
      /52,000 MT × \$37.31\/MT/,
    );

    // Metadata breakdown
    assert.match(
      elements.get('pelagos-cost-rate').textContent,
      /\$37.31 USD\/MT/,
    );
    assert.match(elements.get('pelagos-cost-cargo').textContent, /52,000 MT/);
    assert.match(elements.get('pelagos-cost-source').textContent, /2026-09-01/);
    assert.match(
      elements.get('pelagos-cost-distance').textContent,
      /4,403.4 NM/,
    );

    // Critical Provenance Requirement: Prototype distance must be labeled unverified for navigation
    assert.match(
      elements.get('pelagos-cost-distance-provenance').textContent,
      /Prototype Benchmark \(Unverified for navigation\)/,
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos Voyage Freight Calculator displays unavailable state and no fabricated cost for unmonitored trade corridor', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  elements.get('pelagos-origin-select').value = 'NLRTM';
  elements.get('pelagos-dest-select').value = 'SGSIN';
  elements.get('pelagos-cargo-tonnes').value = '50000';

  const mockUnavailablePayload = {
    status: 'unavailable',
    message: 'Trade corridor Rotterdam -> Singapore is currently unmonitored.',
    gross_freight_cost: null,
    applicable_rate: null,
  };

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (url) => {
      if (String(url).includes('/calculator/voyage-freight')) {
        return { ok: true, json: async () => mockUnavailablePayload };
      }
      return { ok: true, json: async () => ({}) };
    };

    const success = await controller.calculateVoyageCost();
    assert.equal(success, true);

    assert.equal(
      elements.get('pelagos-cost-status-tag').textContent,
      'UNAVAILABLE',
    );
    assert.equal(elements.get('pelagos-cost-content').style.display, 'none');
    assert.equal(elements.get('pelagos-cost-empty').style.display, 'block');
    assert.match(
      elements.get('pelagos-cost-empty').textContent,
      /unmonitored/i,
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos Voyage Freight Calculator prevents stale asynchronous responses from overwriting newer requests', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  let slowResolve;
  const slowPromise = new Promise((resolve) => {
    slowResolve = resolve;
  });

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      const payload = JSON.parse(options.body);
      if (payload.origin === 'USSEA') {
        // Slow initial request
        await slowPromise;
        return {
          ok: true,
          json: async () => ({
            status: 'calculated',
            gross_freight_cost: 1865500.0,
            gross_freight_cost_currency: 'USD',
            applicable_rate: { rate_value: 37.31, unit: 'USD/MT' },
            cargo_tonnes: 50000.0,
          }),
        };
      }
      // Fast second request (USMSY)
      return {
        ok: true,
        json: async () => ({
          status: 'calculated',
          gross_freight_cost: 4582800.0,
          gross_freight_cost_currency: 'USD',
          applicable_rate: { rate_value: 76.38, unit: 'USD/MT' },
          cargo_tonnes: 60000.0,
        }),
      };
    };

    // 1. Fire slow calculation (Seattle)
    elements.get('pelagos-origin-select').value = 'USSEA';
    elements.get('pelagos-dest-select').value = 'JPYOK';
    elements.get('pelagos-cargo-tonnes').value = '50000';
    const p1 = controller.calculateVoyageCost();

    // 2. Immediately switch to US Gulf and fire second request
    elements.get('pelagos-origin-select').value = 'USMSY';
    elements.get('pelagos-dest-select').value = 'JPYOK';
    elements.get('pelagos-cargo-tonnes').value = '60000';
    const p2 = controller.calculateVoyageCost();

    await p2;
    assert.equal(elements.get('pelagos-cost-val').textContent, '4,582,800.00');

    // 3. Resolve slow request now
    slowResolve();
    await p1;

    // Stale Seattle response MUST NOT overwrite US Gulf result!
    assert.equal(elements.get('pelagos-cost-val').textContent, '4,582,800.00');
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos Voyage Freight Calculator handles network error gracefully without breaking dashboard', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'JPYOK';
  elements.get('pelagos-cargo-tonnes').value = '52000';

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => {
      throw new Error('Connection reset by peer');
    };

    const success = await controller.calculateVoyageCost();
    assert.equal(success, false);

    assert.equal(elements.get('pelagos-cost-status-tag').textContent, 'ERROR');
    assert.equal(elements.get('pelagos-cost-content').style.display, 'none');
    assert.equal(elements.get('pelagos-cost-empty').style.display, 'block');
    assert.match(
      elements.get('pelagos-cost-empty').textContent,
      /Connection reset by peer/i,
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos Candidate Vessel Suitability renders ranked candidate matches with particulars, distance, and unconfirmed laycan notice', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'JPYOK';
  elements.get('pelagos-cargo-tonnes').value = '52000';

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (url) => {
      if (String(url).includes('/api/v1/calculator/vessel-suitability')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'matched',
            corridor: {
              route_code: 'PNW-JPN-BULK',
              route_name:
                'U.S. Pacific Northwest to Japan Bulk Grain Ocean Corridor',
              origin_region: 'U.S. Pacific Northwest',
              destination_region: 'Japan',
              commodity: 'Agricultural Bulk Grain',
              vessel_class: 'Panamax / Supramax',
            },
            loading_port_id: 'USSEA',
            loading_port_name: 'Seattle',
            cargo_tonnes: 52000.0,
            target_vessel_class: 'Panamax / Supramax',
            total_vessels_evaluated: 5,
            potentially_suitable_count: 2,
            candidates: [
              {
                mmsi: '636022875',
                imo: '9745287',
                vessel_name: 'MSC ATHOS',
                vessel_type: 'Cargo - Hazardous A',
                estimated_dimensional_class: 'Post-Panamax / Capesize',
                suitability_status: 'potentially_suitable',
                suitability_reason:
                  'Dimensionally screened candidate. Classified as Post-Panamax / Capesize based on reported dimensions (LOA 300m, Beam 48m, Draft 10.3m). Vessel exceeds standard Panamax grain parcel envelope (52,000 MT); physical dimensional envelope indicates adequate scale, but actual DWT, hold suitability, and commercial cargo capacity are unverified from AIS alone.',
                distance_to_load_port_nm: 139.7,
                distance_qualification:
                  'HISTORICAL_OBSERVATION_ONLY: Distance of 139.7 NM reflects vessel position at historical observation fix (2024-01-01). Current vessel position and proximity to loading port are unknown.',
                length_meters: 300.0,
                width_meters: 48.0,
                draft_meters: 10.3,
                speed_knots: 15.9,
                course_degrees: 180.0,
                is_stale: true,
                laycan_feasibility: 'UNCONFIRMED',
                commercial_availability: 'UNCONFIRMED',
                cargo_capacity_status: 'UNVERIFIED',
              },
              {
                mmsi: '212719000',
                imo: '9300001',
                vessel_name: 'TROODOS SUN',
                vessel_type: 'Cargo',
                estimated_dimensional_class: 'Panamax / Kamsarmax',
                suitability_status: 'potentially_suitable',
                suitability_reason:
                  'Dimensionally screened candidate. Classified as Panamax / Kamsarmax based on reported dimensions (LOA 228m, Beam 35m, Draft 12.5m), which aligns with the physical envelope for 52,000 MT parcel. Verified deadweight tonnage (DWT), hold cleanliness, and commercial cargo capacity are unverified from AIS alone.',
                distance_to_load_port_nm: 855.8,
                distance_qualification:
                  'HISTORICAL_OBSERVATION_ONLY: Distance of 855.8 NM reflects vessel position at historical observation fix (2024-01-01). Current vessel position and proximity to loading port are unknown.',
                length_meters: 228.0,
                width_meters: 35.0,
                draft_meters: 12.5,
                speed_knots: 10.4,
                course_degrees: 180.0,
                is_stale: true,
                laycan_feasibility: 'UNCONFIRMED',
                commercial_availability: 'UNCONFIRMED',
                cargo_capacity_status: 'UNVERIFIED',
              },
            ],
            disclaimers: [
              'Potential vessel suitability is evaluated strictly as a dimensional screening.',
              'Commercial open-tonnage status is NOT reported in AIS broadcasts.',
            ],
          }),
        };
      }
      return { ok: false, status: 404 };
    };

    const success = await controller.matchSuitableVessels();
    assert.equal(success, true);

    // Verify tag and metrics
    assert.equal(
      elements.get('pelagos-suitability-tag').textContent,
      '2 POTENTIALLY SUITABLE',
    );
    assert.equal(
      elements.get('pelagos-suitability-metrics').style.display,
      'grid',
    );
    assert.equal(
      elements.get('pelagos-suitability-total-count').textContent,
      '5',
    );
    assert.equal(
      elements.get('pelagos-suitability-matched-count').textContent,
      '2',
    );
    assert.equal(
      elements.get('pelagos-suitability-incompatible-count').textContent,
      '3',
    );

    // Verify rendered candidate list items
    const listEl = elements.get('pelagos-suitability-list');
    assert.equal(listEl.children.length, 2);

    const firstCand = listEl.children[0];
    assert.equal(firstCand.dataset.mmsi, '636022875');
    assert.match(firstCand.textContent, /MSC ATHOS/);
    assert.match(firstCand.textContent, /Post-Panamax \/ Capesize/);
    assert.match(firstCand.textContent, /POTENTIALLY SUITABLE/);
    assert.match(
      firstCand.textContent,
      /139\.7 NM at fix \(Seattle · Historical\)/,
    );
    assert.match(firstCand.textContent, /300m LOA · 48m Beam · 10\.3m Draft/);
    assert.match(firstCand.textContent, /DWT Capacity: UNVERIFIED/);
    assert.match(firstCand.textContent, /Laycan: UNCONFIRMED/);
    assert.match(firstCand.textContent, /Historical Fix/);
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos Candidate Vessel Suitability handles vessels with missing particulars honestly as INSUFFICIENT DATA', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'JPYOK';

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        status: 'matched',
        loading_port_id: 'USSEA',
        loading_port_name: 'Seattle',
        cargo_tonnes: 52000.0,
        total_vessels_evaluated: 1,
        potentially_suitable_count: 0,
        candidates: [
          {
            mmsi: '367121040',
            vessel_name: 'AMERICAN COURAGE',
            vessel_type: 'Cargo',
            suitability_status: 'insufficient_data',
            suitability_reason:
              'Vessel classified as Cargo, but AIS broadcast lacks verified length and beam particulars; dimensional capacity cannot be confirmed.',
            distance_to_load_port_nm: 1850.5,
            length_meters: 194.0,
            width_meters: null,
            draft_meters: null,
            is_stale: true,
            laycan_feasibility: 'UNCONFIRMED',
          },
        ],
      }),
    });

    const success = await controller.matchSuitableVessels();
    assert.equal(success, true);

    const listEl = elements.get('pelagos-suitability-list');
    assert.equal(listEl.children.length, 1);
    const item = listEl.children[0];
    assert.match(item.textContent, /INSUFFICIENT DATA/);
    assert.match(
      item.textContent,
      /lacks verified length and beam particulars/,
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos Candidate Vessel Suitability displays unmonitored corridor status honestly', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  elements.get('pelagos-origin-select').value = 'NLRTM';
  elements.get('pelagos-dest-select').value = 'SGSIN';

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        status: 'unmonitored_corridor_matched',
        loading_port_id: 'NLRTM',
        loading_port_name: 'Rotterdam',
        cargo_tonnes: 50000.0,
        total_vessels_evaluated: 5,
        potentially_suitable_count: 0,
        candidates: [],
      }),
    });

    await controller.matchSuitableVessels();
    assert.equal(
      elements.get('pelagos-suitability-tag').textContent,
      'UNMONITORED CORRIDOR',
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos Candidate Vessel Suitability prevents stale asynchronous responses from overwriting newer requests', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  const origFetch = globalThis.fetch;
  try {
    let slowResolve;
    const slowPromise = new Promise((resolve) => {
      slowResolve = resolve;
    });

    globalThis.fetch = async (url, options) => {
      const body = JSON.parse(options.body);
      if (body.origin === 'USSEA') {
        await slowPromise;
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'matched',
            loading_port_id: 'USSEA',
            loading_port_name: 'Seattle',
            cargo_tonnes: 52000.0,
            total_vessels_evaluated: 10,
            potentially_suitable_count: 5,
            candidates: [],
          }),
        };
      } else {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'matched',
            loading_port_id: 'USMSY',
            loading_port_name: 'New Orleans',
            cargo_tonnes: 60000.0,
            total_vessels_evaluated: 20,
            potentially_suitable_count: 1,
            candidates: [],
          }),
        };
      }
    };

    // 1. Fire request 1 for Seattle
    elements.get('pelagos-origin-select').value = 'USSEA';
    elements.get('pelagos-dest-select').value = 'JPYOK';
    const p1 = controller.matchSuitableVessels();

    // 2. Immediately switch to US Gulf and fire request 2
    elements.get('pelagos-origin-select').value = 'USMSY';
    elements.get('pelagos-dest-select').value = 'JPYOK';
    const p2 = controller.matchSuitableVessels();

    await p2;
    assert.equal(
      elements.get('pelagos-suitability-matched-count').textContent,
      '1',
    );

    // 3. Resolve slow Seattle request
    slowResolve();
    await p1;

    // Stale Seattle response MUST NOT overwrite US Gulf result!
    assert.equal(
      elements.get('pelagos-suitability-matched-count').textContent,
      '1',
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos Candidate Vessel Suitability handles network error gracefully without crashing dashboard', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'JPYOK';

  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => {
      throw new Error('503 Service Unavailable');
    };

    const success = await controller.matchSuitableVessels();
    assert.equal(success, false);

    assert.equal(elements.get('pelagos-suitability-tag').textContent, 'ERROR');
    assert.equal(
      elements.get('pelagos-suitability-empty').style.display,
      'block',
    );
    assert.match(
      elements.get('pelagos-suitability-empty').textContent,
      /503 Service Unavailable/i,
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

function collectNodeText(node) {
  if (!node) return '';
  const parts = [];
  if (node.textContent) parts.push(node.textContent);
  if (Array.isArray(node.children)) {
    for (const child of node.children) {
      parts.push(collectNodeText(child));
    }
  }
  return parts.join(' ');
}

test('Pelagos D1 Calculator renders dynamic distance provenance, rate provenance, and API disclaimers', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();

  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  controller.renderCostResult({
    status: 'calculated',
    origin_port_id: 'USMSY',
    origin_port_name: 'New Orleans',
    destination_port_id: 'JPYOK',
    destination_port_name: 'Yokohama',
    corridor: {
      route_code: 'USG-JPN-BULK',
      route_name: 'U.S. Gulf to Japan',
    },
    cargo_tonnes: 52000,
    cargo_unit: 'MT',
    gross_freight_cost: 2714400.0,
    gross_freight_cost_currency: 'USD',
    applicable_rate: {
      rate_value: 52.2,
      currency: 'USD',
      unit: 'USD/MT',
      rate_type: 'observed',
      observation_date: '2025-11-01',
      observation_period: 'monthly',
      source_name: 'USDA AMS Grain Transportation Report',
      source_dataset_id: 'USDA-GTR-TABLE-2',
      license_type: 'U.S. Public Domain',
      is_derived: false,
    },
    distance: {
      distance_nm: 9180.5,
      distance_km: 17002.3,
      distance_source: 'marnet_operational_routing',
      provenance: {
        dataset_name: 'Eurostat MARNET 50km',
        authority: 'European Commission / Eurostat',
        licensing: 'Open Maritime Network',
        qualification: 'Unverified for SOLAS navigation',
      },
    },
    disclaimers: [
      'CUSTOM API DISCLAIMER: Gross ocean freight excludes canal tolls and bunker fuel.',
      'Second API caveat for audit visibility.',
    ],
  });

  assert.match(
    elements.get('pelagos-cost-route').textContent,
    /New Orleans → Yokohama · USG-JPN-BULK/,
  );
  assert.match(
    elements.get('pelagos-cost-rate-meta').textContent,
    /USDA-GTR-TABLE-2/,
  );
  assert.match(
    elements.get('pelagos-cost-rate-meta').textContent,
    /U\.S\. Public Domain/,
  );
  assert.match(
    elements.get('pelagos-cost-distance').textContent,
    /9,180\.5 NM/,
  );
  assert.match(
    elements.get('pelagos-cost-distance').textContent,
    /MARNET Routing/,
  );
  assert.match(
    elements.get('pelagos-cost-distance-provenance').textContent,
    /marnet_operational_routing/,
  );
  assert.match(
    elements.get('pelagos-cost-distance-provenance').textContent,
    /Eurostat MARNET 50km/,
  );
  assert.match(
    elements.get('pelagos-cost-distance-provenance').textContent,
    /Unverified for SOLAS navigation/,
  );

  const disclaimerText = collectNodeText(
    elements.get('pelagos-cost-disclaimers'),
  );
  assert.match(disclaimerText, /CUSTOM API DISCLAIMER/);
  assert.match(disclaimerText, /Second API caveat/);

  // Also verify corridor_benchmark_distance without custom provenance resolves dynamically
  controller.renderCostResult({
    status: 'calculated',
    cargo_tonnes: 50000,
    gross_freight_cost: 1500000,
    applicable_rate: {
      rate_value: 30,
      source_name: 'USDA',
      observation_date: '2025-11-01',
    },
    distance: {
      distance_nm: 4300,
      distance_source: 'corridor_benchmark_distance',
      provenance: {},
    },
    disclaimers: [],
  });
  assert.match(elements.get('pelagos-cost-distance').textContent, /Benchmark/);
  assert.match(
    elements.get('pelagos-cost-distance-provenance').textContent,
    /corridor_benchmark_distance/,
  );

  controller.destroy();
});

test('Pelagos D1 canal_policy propagates to routing, freight calculator, suitability requests, and resets cleanly', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();
  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  const recordedBodies = [];
  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (url, init = {}) => {
      if (init.body) {
        recordedBodies.push({ url, body: JSON.parse(init.body) });
      }
      if (url === '/api/v1/routing/calculate') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'calculated',
            geometry_segments: [
              [
                [-89.43, 28.9],
                [-120.0, 20.0],
              ],
              [
                [160.0, 32.0],
                [139.73, 35.15],
              ],
            ],
            waypoints: [],
            summary: {
              origin_id: 'USMSY',
              origin_name: 'New Orleans',
              destination_id: 'JPYOK',
              destination_name: 'Yokohama',
              total_distance_nm: 10500,
              total_distance_km: 19446,
              canals_transited: ['suez'],
              canal_policy_applied: 'avoid_panama',
            },
          }),
        };
      }
      if (String(url).startsWith('/api/v1/freight/route-intelligence')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'monitored',
            corridor: {
              route_code: 'USG-JPN-BULK',
              route_name: 'U.S. Gulf to Japan',
              benchmark_distance_nm: 9180,
            },
            latest_observation: {
              rate_value: 52.0,
              observation_date: '2025-11-01',
              source_name: 'USDA AMS',
              source_dataset_id: 'USDA-GTR-T2',
            },
            baseline_forecast: {
              model_type: 'persistence',
              horizon_months: 1,
              target_date: '2025-12-01',
              predicted_value: 52.0,
            },
            multi_horizon_forecasts: {},
            disclaimers: [],
          }),
        };
      }
      if (url === '/api/v1/calculator/voyage-freight') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'calculated',
            cargo_tonnes: 52000,
            gross_freight_cost: 2704000,
            applicable_rate: { rate_value: 52.0, source_name: 'USDA' },
            distance: {
              distance_nm: 10500,
              distance_source: 'marnet_operational_routing',
              provenance: {},
            },
            disclaimers: [],
          }),
        };
      }
      if (url === '/api/v1/calculator/vessel-suitability') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'matched',
            cargo_tonnes: 52000,
            total_vessels_evaluated: 0,
            potentially_suitable_count: 0,
            candidates: [],
          }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ vessels: [] }) };
    };

    elements.get('pelagos-origin-select').value = 'USMSY';
    elements.get('pelagos-dest-select').value = 'JPYOK';
    elements.get('pelagos-canal-policy').value = 'avoid_panama';

    await controller.calculateRoute();
    assert.ok(controller.currentRoute);
    await new Promise((r) => setTimeout(r, 15));

    const routeReq = recordedBodies.find(
      (r) => r.url === '/api/v1/routing/calculate',
    );
    const costReq = recordedBodies.find(
      (r) => r.url === '/api/v1/calculator/voyage-freight',
    );
    const suitReq = recordedBodies.find(
      (r) => r.url === '/api/v1/calculator/vessel-suitability',
    );

    assert.equal(routeReq.body.canal_policy, 'avoid_panama');
    assert.equal(costReq.body.canal_policy, 'avoid_panama');
    assert.equal(suitReq.body.canal_policy, 'avoid_panama');
    assert.equal(
      elements.get('pelagos-route-canal-policy').textContent,
      'avoid_panama',
    );
    assert.equal(
      elements.get('pelagos-cost-canal-policy').textContent,
      'avoid_panama',
    );
    assert.equal(elements.get('pelagos-corridor-dist').textContent, '9,180 NM');
    assert.equal(
      elements.get('pelagos-rate-dataset-id').textContent,
      'USDA-GTR-T2',
    );

    // Change policy to avoid_all and calculate cost again
    elements.get('pelagos-canal-policy').value = 'avoid_all';
    await controller.calculateVoyageCost();
    const lastCost = recordedBodies
      .filter((r) => r.url === '/api/v1/calculator/voyage-freight')
      .pop();
    assert.equal(lastCost.body.canal_policy, 'avoid_all');
    assert.equal(
      elements.get('pelagos-cost-canal-policy').textContent,
      'avoid_all',
    );

    controller.clearRoute();
    assert.equal(elements.get('pelagos-canal-policy').value, 'allow');
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});

test('Pelagos D1 Multi-Horizon Forecasts render T+1/T+3/T+6, UNAVAILABLE horizons, and Persistence non-directional notice', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();
  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  controller._renderMultiHorizonForecasts(
    {
      baseline_forecast: {
        model_type: 'persistence',
        model_family: 'deterministic_baseline',
        horizon_months: 1,
        target_date: '2026-01-01',
        predicted_value: 34.5,
        unit: 'USD/MT',
        is_experimental: false,
      },
      forecasts: [
        {
          model_type: 'persistence',
          horizon_months: 1,
          target_date: '2026-01-01',
          predicted_value: 34.5,
          unit: 'USD/MT',
        },
        {
          model_type: 'persistence',
          horizon_months: 3,
          target_date: '2026-03-01',
          predicted_value: 34.5,
          unit: 'USD/MT',
        },
      ],
      multi_horizon_forecasts: {
        'T+1': {
          model_type: 'persistence',
          horizon_months: 1,
          target_date: '2026-01-01',
          predicted_value: 34.5,
          unit: 'USD/MT',
        },
        'T+3': {
          model_type: 'persistence',
          horizon_months: 3,
          target_date: '2026-03-01',
          predicted_value: 34.5,
          unit: 'USD/MT',
        },
        // T+6 intentionally missing -> must display UNAVAILABLE, not extrapolate
      },
    },
    true,
  );

  assert.match(
    elements.get('pelagos-forecast-tag').textContent,
    /PERSISTENCE \(ACTIVE PRODUCTION\)/,
  );
  assert.match(
    elements.get('pelagos-forecast-model').textContent,
    /Persistence Baseline \(Active Production\)/,
  );
  assert.match(
    elements.get('pelagos-forecast-note').textContent,
    /NOT promoted to production/i,
  );
  assert.match(
    elements.get('pelagos-forecast-note').textContent,
    /NOT directional market-timing signals/i,
  );

  const horizonRows = elements.get('pelagos-forecast-horizons-list').children;
  assert.equal(horizonRows.length, 3);
  assert.match(collectNodeText(horizonRows[0]), /T\+1.*2026-01-01.*\$34\.50/);
  assert.match(collectNodeText(horizonRows[1]), /T\+3.*2026-03-01.*\$34\.50/);
  assert.match(collectNodeText(horizonRows[2]), /T\+6.*UNAVAILABLE/);

  controller.destroy();
});

test('Pelagos D1 Environmental Conditions render 6 variables, null handling, non-interpolation, and all freshness badges', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();
  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  const mkStation = (id, freshness, waveHeightNull = false) => ({
    station_id: id,
    station_name: `Buoy ${id}`,
    sea_area: 'North Pacific Fairway',
    coordinates: { latitude: 46.8, longitude: -124.5 },
    distance_to_corridor_fairway_nm: 12.4,
    distance_to_origin_port_nm: 45.2,
    observation_time: '2026-11-12T10:00:00Z',
    data_age_hours:
      freshness === 'fresh'
        ? 2.5
        : freshness === 'recent'
          ? 48
          : freshness === 'stale'
            ? 300
            : 1200,
    freshness,
    quality_status: 'valid',
    source_name: 'NOAA NDBC',
    variables: {
      wind_speed_knots: { value: 14.2, unit: 'knots' },
      wind_direction_degrees: { value: 290, unit: 'degrees' },
      wave_height_meters: {
        value: waveHeightNull ? null : 2.1,
        unit: 'meters',
      },
      wave_period_seconds: { value: 9.0, unit: 'seconds' },
      sea_surface_temp_c: { value: 11.4, unit: 'degC' },
      air_pressure_hpa: { value: 1013.2, unit: 'hPa' },
    },
  });

  controller._renderEnvironmentalConditions(
    {
      status: 'partial',
      coverage_status: 'departure_waters_monitored',
      coverage_extent: 'coastal_departure_fairway',
      corridor_reference_point: 'Columbia River Fairway',
      stations_count: 4,
      stations: [
        mkStation('46041', 'fresh', false),
        mkStation('46029', 'recent', true),
        mkStation('46050', 'stale', false),
        mkStation('46089', 'historical', false),
      ],
      aggregation: {
        method: 'point_observations',
        summary: 'No spatial interpolation across ocean basin.',
      },
      spatial_coverage_notes: 'Coastal departure buoys only.',
      disclaimer: 'Do not assume open-ocean uniformity.',
    },
    true,
  );

  assert.equal(elements.get('pelagos-env-tag').textContent, 'PARTIAL');
  const stationsChildren = elements.get('pelagos-env-stations').children;
  assert.equal(stationsChildren.length, 4);

  const badges = elements
    .get('pelagos-env-stations')
    .querySelectorAll('.env-freshness-badge');
  assert.deepEqual(
    badges.map((b) => b.textContent),
    ['FRESH', 'RECENT', 'STALE', 'HISTORICAL'],
  );

  assert.match(collectNodeText(stationsChildren[1]), /— \(NULL\)/);
  assert.match(collectNodeText(stationsChildren[0]), /14\.2 knots/);
  assert.match(collectNodeText(stationsChildren[0]), /1013\.2 hPa/);
  assert.match(
    elements.get('pelagos-env-notes').textContent,
    /No spatial interpolation/,
  );
  assert.match(
    elements.get('pelagos-env-notes').textContent,
    /Do not assume open-ocean uniformity/,
  );

  controller.destroy();
});

test('Pelagos D1 Bunker References render unavailable corridor pricing, non-substituted regional benchmarks, and hub quotes', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();
  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  controller._renderBunkerReferences(
    {
      provider: 'Bunker Fuel Prices (bunkerfuelprices.com)',
      status: 'unavailable',
      reason:
        'Corridor bunkering hubs unquoted; synthesis strictly disallowed.',
      hubs_with_prices_count: 0,
      bunkering_hubs_in_scope: [
        {
          port_id: 'USSEA',
          name: 'Seattle / Puget Sound',
          region: 'US PNW',
          role: 'primary_departure_bunkering_hub',
          status: 'unavailable',
          availability_reason: 'No stem quote published for USSEA',
          quotes: [],
        },
      ],
      latest_quotes: [],
      regional_benchmarks: [
        {
          port_code: 'SGSIN',
          port_name: 'Singapore',
          fuel_grade: 'VLSFO',
          price_usd_per_mt: 585.5,
          currency: 'USD',
          unit: 'USD/MT',
          pricing_type: 'indicative',
          delivery_basis: 'delivered',
          observation_time: '2026-11-11T12:00:00Z',
          retrieval_time_utc: '2026-11-12T06:00:00Z',
          freshness: 'recent',
        },
      ],
      disclaimer: 'Indicative prices only.',
    },
    true,
  );

  assert.equal(elements.get('pelagos-bunker-tag').textContent, 'UNAVAILABLE');
  assert.match(
    elements.get('pelagos-bunker-provider').textContent,
    /0\/1 hubs quoted/,
  );
  const hubsText = collectNodeText(elements.get('pelagos-bunker-hubs'));
  assert.match(hubsText, /Seattle \/ Puget Sound \(USSEA\)/);
  assert.match(hubsText, /No stem quote published for USSEA/);

  const benchText = collectNodeText(elements.get('pelagos-bunker-benchmarks'));
  assert.match(benchText, /NOT SUBSTITUTED FOR CORRIDOR HUBS/);
  assert.match(benchText, /Singapore \(SGSIN\) · VLSFO · \$585\.50 USD\/MT/);
  assert.match(
    benchText,
    /Type: indicative · Basis: delivered · Freshness: RECENT/,
  );
  assert.match(
    elements.get('pelagos-bunker-disclaimer').textContent,
    /Indicative prices only/,
  );
  assert.match(
    elements.get('pelagos-bunker-disclaimer').textContent,
    /fuel expenditure is NOT calculated/i,
  );

  controller.destroy();
});

test('Pelagos D1 Suitability renders verified vs unverified DWT, 3-layer compatibility, missing particulars, registry sync distinction, and unavailable status', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();
  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  controller.renderSuitabilityResult({
    status: 'matched',
    loading_port_id: 'USSEA',
    loading_port_name: 'Seattle',
    cargo_tonnes: 52000,
    target_vessel_class: 'Panamax / Supramax',
    data_mode: 'historical',
    data_mode_notice: 'Archived January 1, 2024 AIS fixes.',
    ais_vs_commercial_distinction:
      'Vessel.commercial_status indicates registry sync only, NOT spot charter open tonnage.',
    route_compatibility_summary: {
      route_navigable: true,
      routing_status: 'calculated',
      route_distance_nm: 4320.5,
      canals_transited: ['panama'],
      origin_documented_draft_limit_meters: 15.2,
      origin_limit_source: 'USACE Project Depth',
      destination_documented_draft_limit_meters: null,
      destination_limit_source: 'No channel limit documented',
    },
    total_vessels_evaluated: 2,
    potentially_suitable_count: 1,
    disclaimers: ['Custom suitability disclaimer from API.'],
    candidates: [
      {
        mmsi: '311000001',
        vessel_name: 'MV VERIFIED BULK',
        estimated_dimensional_class: 'Panamax',
        registry_vessel_class: 'Panamax Bulk Carrier',
        registry_commercial_status: 'active',
        suitability_status: 'potentially_suitable',
        suitability_reason: 'Matches envelope and verified DWT.',
        verified_cargo_capacity_dwt: 76000,
        cargo_capacity_status: 'VERIFIED',
        commercial_availability: 'UNCONFIRMED',
        commercial_availability_note: 'No fixture feed connected.',
        laycan_feasibility: 'UNCONFIRMED',
        is_stale: true,
        observation_time: '2024-01-01T12:00:00Z',
        observation_age_seconds: 86400,
        data_mode: 'historical',
        missing_particulars: ['grain_cleanliness_certificate'],
        particulars_completeness: {
          completeness_status: 'partial',
          missing_particulars: ['grain_cleanliness_certificate'],
        },
        cargo_compatibility: {
          status: 'compatible',
          verified_dwt_tonnes: 76000,
          capacity_check_basis: 'verified_dwt',
          reasons: ['Verified DWT 76,000t satisfies 52,000 MT'],
        },
        route_compatibility: {
          status: 'conditionally_compatible_unverified_limits',
          canal_compatibility_notes: ['Panama locks clearance required'],
          origin_port: { status: 'compatible' },
          destination_port: { status: 'insufficient_data' },
          reasons: ['Discharge draft limit unverified'],
        },
      },
      {
        mmsi: '311000002',
        vessel_name: 'MV AIS ONLY',
        suitability_status: 'insufficient_data',
        suitability_reason: 'Draft missing.',
        verified_cargo_capacity_dwt: null,
        cargo_capacity_status: 'UNVERIFIED',
        commercial_availability: 'UNCONFIRMED',
        laycan_feasibility: 'UNCONFIRMED',
        registry_commercial_status: 'active',
        missing_particulars: ['draft_meters', 'verified_dwt'],
        is_stale: false,
      },
    ],
  });

  const routeCompatText = collectNodeText(
    elements.get('pelagos-suitability-route-compat'),
  );
  assert.match(routeCompatText, /NAVIGABLE/);
  assert.match(routeCompatText, /15\.2m \(USACE Project Depth\)/);
  assert.match(routeCompatText, /Unverified \(No channel limit documented\)/);

  assert.match(
    elements.get('pelagos-suitability-commercial-distinction').textContent,
    /NOT spot charter open tonnage/,
  );
  assert.match(
    collectNodeText(elements.get('pelagos-suitability-disclaimers')),
    /Custom suitability disclaimer from API/,
  );

  const items = elements.get('pelagos-suitability-list').children;
  assert.equal(items.length, 2);
  const firstText = collectNodeText(items[0]);
  assert.match(firstText, /Verified DWT: 76,000 t/);
  assert.match(firstText, /Missing \[grain_cleanliness_certificate\]/);
  assert.match(firstText, /Cargo Layer: compatible/);
  assert.match(
    firstText,
    /Route\/Port Layer: conditionally_compatible_unverified_limits/,
  );
  assert.match(
    firstText,
    /Registry Sync: "active" \(AIS registry sync indicator ONLY, NOT commercial open tonnage\)/,
  );
  assert.match(firstText, /Commercial: UNCONFIRMED · Laycan: UNCONFIRMED/);

  const secondText = collectNodeText(items[1]);
  assert.match(secondText, /DWT Capacity: UNVERIFIED/);
  assert.match(secondText, /Missing \[draft_meters, verified_dwt\]/);

  // Explicit unavailable status handling
  controller.renderSuitabilityResult({
    status: 'unavailable',
    message: 'Route cannot be calculated under current avoid_all canal policy.',
    disclaimers: ['Route blocked caveat.'],
  });
  assert.equal(
    elements.get('pelagos-suitability-tag').textContent,
    'UNAVAILABLE',
  );
  assert.equal(
    elements.get('pelagos-suitability-empty').style.display,
    'block',
  );
  assert.match(
    elements.get('pelagos-suitability-empty').textContent,
    /avoid_all canal policy/,
  );

  controller.destroy();
});

test('Pelagos D1 handles route-intelligence API error and available corridor bunker hub quotes honestly', async () => {
  const { doc, elements } = createMockDom();
  const viewer = createMockViewer();
  const controller = new PelagosPanelController({ viewer, document: doc });
  await controller.init();

  // 1. Available hub quote rendering
  controller._renderBunkerReferences(
    {
      provider: 'Bunker Fuel Prices (bunkerfuelprices.com)',
      status: 'partial',
      reason: '1/2 hubs quoted',
      hubs_with_prices_count: 1,
      bunkering_hubs_in_scope: [
        {
          port_id: 'USSEA',
          name: 'Seattle / Puget Sound',
          region: 'US PNW',
          role: 'primary_departure_bunkering_hub',
          status: 'available',
          quotes: [
            {
              port_code: 'USSEA',
              port_name: 'Seattle',
              fuel_grade: 'VLSFO',
              price_usd_per_mt: 612.25,
              currency: 'USD',
              unit: 'USD/MT',
              pricing_type: 'transacted',
              delivery_basis: 'ex_wharf',
              observation_time: '2026-11-12T08:00:00Z',
              retrieval_time_utc: '2026-11-12T09:00:00Z',
              freshness: 'fresh',
            },
          ],
        },
        {
          port_id: 'JPYOK',
          name: 'Yokohama / Tokyo Bay',
          region: 'Japan',
          role: 'destination_discharging_bunkering_hub',
          status: 'unavailable',
          availability_reason: 'No stem quote available',
          quotes: [],
        },
      ],
      regional_benchmarks: [],
      disclaimer: 'Indicative only.',
    },
    true,
  );

  assert.equal(elements.get('pelagos-bunker-tag').textContent, 'PARTIAL');
  const hubsText = collectNodeText(elements.get('pelagos-bunker-hubs'));
  assert.match(hubsText, /Seattle \(USSEA\) · VLSFO · \$612\.25 USD\/MT/);
  assert.match(
    hubsText,
    /Type: transacted · Basis: ex_wharf · Freshness: FRESH/,
  );
  assert.match(hubsText, /Yokohama \/ Tokyo Bay \(JPYOK\)/);

  // 2. Route intelligence API failure degrades gracefully to empty/unavailable states
  const origFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (url) => {
      if (url === '/api/v1/routing/calculate') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'calculated',
            geometry_segments: [
              [
                [-124.75, 48.5],
                [-150.0, 46.0],
              ],
              [
                [165.0, 42.0],
                [139.73, 35.15],
              ],
            ],
            waypoints: [],
            summary: {
              origin_id: 'USSEA',
              origin_name: 'Seattle',
              destination_id: 'JPYOK',
              destination_name: 'Yokohama',
              total_distance_nm: 4300,
              total_distance_km: 7963,
            },
          }),
        };
      }
      if (String(url).startsWith('/api/v1/freight/route-intelligence')) {
        return {
          ok: false,
          status: 503,
          json: async () => ({ detail: 'Down' }),
        };
      }
      if (url === '/api/v1/calculator/vessel-suitability') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'unavailable',
            message: 'Suitability unavailable without route intelligence.',
            candidates: [],
          }),
        };
      }
      return { ok: true, status: 200, json: async () => ({ vessels: [] }) };
    };

    elements.get('pelagos-origin-select').value = 'USSEA';
    elements.get('pelagos-dest-select').value = 'JPYOK';
    await controller.calculateRoute();

    assert.equal(
      elements.get('pelagos-corridor-status-tag').textContent,
      'UNMONITORED',
    );
    assert.equal(elements.get('pelagos-env-tag').textContent, 'UNAVAILABLE');
    assert.equal(elements.get('pelagos-bunker-tag').textContent, 'UNAVAILABLE');
    assert.equal(
      elements.get('pelagos-forecast-tag').textContent,
      'UNAVAILABLE',
    );
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});
