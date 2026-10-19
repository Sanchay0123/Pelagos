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
    const el = {
      id,
      tagName: tag.toUpperCase(),
      value: '',
      textContent: '',
      innerHTML: '',
      style: {},
      disabled: false,
      children,
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
  ];

  for (const id of ids) {
    makeElement(id);
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
      [[-124.75, 48.5], [-170.0, 52.0], [-180.0, 52.5]],
      [[180.0, 52.5], [160.0, 48.0], [139.73, 35.15]],
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
    origin_port: { port_id: 'USSEA', name: 'Seattle', region: 'U.S. Pacific Northwest' },
    destination_port: { port_id: 'JPYOK', name: 'Yokohama', region: 'Japan' },
    latest_observation: {
      observation_date: '2026-09-01',
      rate_value: 37.31,
      currency: 'USD',
      unit: 'USD/MT',
      source_name: "USDA Agricultural Marketing Service / O'Neil Commodity Consulting",
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
    assert.equal(elements.get('pelagos-corridor-status-tag').textContent, 'MONITORED');
    assert.equal(elements.get('pelagos-corridor-code').textContent, 'PNW-JPN-BULK');
    assert.equal(elements.get('pelagos-corridor-cargo').textContent, 'Grain / Dry Bulk');

    // Observed Rate
    assert.equal(elements.get('pelagos-rate-val').textContent, '37.31');
    assert.equal(elements.get('pelagos-rate-unit').textContent, 'USD/MT');
    assert.equal(elements.get('pelagos-rate-date').textContent, '2026-09-01');
    assert.match(elements.get('pelagos-rate-source').textContent, /USDA/);
    assert.match(elements.get('pelagos-rate-type').textContent, /Not Derived/i);

    // Forecast
    assert.equal(elements.get('pelagos-forecast-val').textContent, '37.31');
    assert.equal(elements.get('pelagos-forecast-period').textContent, '2026-10-01');
    assert.match(elements.get('pelagos-forecast-model').textContent, /Persistence/i);

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
    geometry: [[3.85, 52.0], [10.0, 40.0], [32.35, 31.4], [103.65, 1.22]],
    geometry_segments: [
      [[3.85, 52.0], [10.0, 40.0], [32.35, 31.4], [103.65, 1.22]],
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
      canals_transited: ['dover', 'gibraltar', 'suez', 'babelmandeb', 'malacca'],
    },
  };

  const mockFreightPayload = {
    status: 'unmonitored',
    corridor: null,
    origin_port: { port_id: 'NLRTM', name: 'Rotterdam', region: 'Northern Europe' },
    destination_port: { port_id: 'SGSIN', name: 'Singapore', region: 'Southeast Asia' },
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
    assert.equal(elements.get('pelagos-corridor-status-tag').textContent, 'UNMONITORED');
    assert.equal(elements.get('pelagos-corridor-content').style.display, 'none');
    assert.equal(elements.get('pelagos-unmonitored-notice').style.display, 'flex');
    assert.match(elements.get('pelagos-unmonitored-desc').textContent, /unmonitored/i);

    // No fabricated rate or forecast!
    assert.equal(elements.get('pelagos-observed-content').style.display, 'none');
    assert.equal(elements.get('pelagos-rate-empty').style.display, 'block');
    assert.match(elements.get('pelagos-rate-empty').textContent, /no freight rate observations/i);

    assert.equal(elements.get('pelagos-forecast-content').style.display, 'none');
    assert.equal(elements.get('pelagos-forecast-empty').style.display, 'block');
    assert.match(elements.get('pelagos-forecast-empty').textContent, /no predictive forecast model/i);
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
      geometrySegments: [[[-124.75, 48.5], [139.73, 35.15]]],
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

    await controller.updateFleetAvailability({ vessel_availability: { status: 'unavailable' } });
    assert.equal(elements.get('pelagos-fleet-tag').textContent, 'UNCONFIGURED');
    assert.match(elements.get('pelagos-fleet-text').textContent, /AISSTREAM_API_KEY is not configured/);

    // 2. Test live connected state with valid messages
    globalThis.fetch = async () => ({
      ok: true,
      json: async () => ({
        status: 'live',
        rows: [{ mmsi: '123456789', lat: 35.0, lon: 139.0 }],
        rawRowCount: 42,
      }),
    });

    await controller.updateFleetAvailability({ vessel_availability: { status: 'unavailable' } });
    assert.equal(elements.get('pelagos-fleet-tag').textContent, 'TELEMETRY LIVE');
    assert.match(elements.get('pelagos-fleet-text').textContent, /Active — Live AIS Telemetry Connected/);
    assert.match(elements.get('pelagos-fleet-desc').textContent, /AIS telemetry alone does not establish commercial vessel charter availability/);
  } finally {
    globalThis.fetch = origFetch;
    controller.destroy();
  }
});
