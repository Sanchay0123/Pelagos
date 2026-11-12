import assert from 'node:assert/strict';
import test from 'node:test';
import {
  PelagosReadinessController,
  READINESS_GATE_ORDER,
  buildGatePresentation,
  classifyReadinessOutcome,
  computeGateSummary,
  mapGateStatusToSemantic,
} from './pelagosReadiness.js';
import { recommendCharterReadiness } from './pelagosApi.js';
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
    const attrs = new Map();
    let _innerHTML = '';
    let _textContent = '';

    const el = {
      id,
      tagName: tag.toUpperCase(),
      value: '',
      disabled: false,
      style: {},
      children,
      dataset: {},
      setAttribute: (k, v) => {
        attrs.set(String(k), String(v));
      },
      getAttribute: (k) => attrs.get(String(k)) ?? null,
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
            btnText = makeElement(`${id}-btn-text`, 'span');
            btnText.className = 'btn-text';
            children.push(btnText);
          }
          return btnText;
        }
        return null;
      },
      addEventListener: (event, fn) => {
        if (!listeners.has(event)) listeners.set(event, []);
        listeners.get(event).push(fn);
      },
      removeEventListener: (event, fn) => {
        if (!listeners.has(event)) return;
        listeners.set(
          event,
          listeners.get(event).filter((f) => f !== fn),
        );
      },
      dispatchEvent: (eventObj) => {
        const list = listeners.get(eventObj.type) || [];
        const evt = {
          target: el,
          preventDefault: () => {},
          ...eventObj,
        };
        for (const fn of list) fn(evt);
      },
    };
    return el;
  }

  const elementSpecs = [
    // D2.2 Readiness Audit IDs
    ['pelagos-readiness-card', 'div'],
    ['pelagos-readiness-status-tag', 'span'],
    ['pelagos-readiness-route-code', 'select'],
    ['pelagos-readiness-voyage-summary', 'div'],
    ['pelagos-readiness-cargo-tonnes', 'input'],
    ['pelagos-readiness-commodity', 'input'],
    ['pelagos-readiness-laycan-start', 'input'],
    ['pelagos-readiness-laycan-end', 'input'],
    ['pelagos-readiness-max-vessels', 'input'],
    ['pelagos-readiness-validation-msg', 'div'],
    ['pelagos-readiness-submit-btn', 'button'],
    ['pelagos-readiness-error', 'div'],
    ['pelagos-readiness-error-text', 'span'],
    ['pelagos-readiness-retry-btn', 'button'],
    ['pelagos-readiness-empty', 'div'],
    ['pelagos-readiness-result', 'div'],
    ['pelagos-readiness-outcome', 'div'],
    ['pelagos-readiness-summary', 'div'],
    ['pelagos-readiness-contracts', 'div'],
    ['pelagos-readiness-toggle-all-btn', 'button'],
    ['pelagos-readiness-gates', 'div'],
    ['pelagos-readiness-missing-inputs', 'div'],
    ['pelagos-readiness-limitations', 'div'],
    ['pelagos-readiness-disclaimers', 'ul'],
    // Shared panel selectors & panel shell
    ['pelagos-panel', 'div'],
    ['pelagos-origin-select', 'select'],
    ['pelagos-dest-select', 'select'],
    ['pelagos-canal-policy', 'select'],
    ['pelagos-origin-filter', 'input'],
    ['pelagos-dest-filter', 'input'],
    ['pelagos-calculate-btn', 'button'],
    ['pelagos-reset-btn', 'button'],
    ['pelagos-swap-btn', 'button'],
    ['pelagos-validation-msg', 'div'],
    ['pelagos-status-banner', 'div'],
    ['pelagos-dashboard', 'div'],
    ['pelagos-cargo-tonnes', 'input'],
    ['pelagos-calculate-cost-btn', 'button'],
  ];

  for (const [id, tag] of elementSpecs) {
    elements.set(id, makeElement(id, tag));
  }

  elements.get('pelagos-readiness-cargo-tonnes').value = '52000';
  elements.get('pelagos-readiness-commodity').value = 'Agricultural Bulk Grain';
  elements.get('pelagos-readiness-max-vessels').value = '20';
  elements.get('pelagos-canal-policy').value = 'allow';
  elements.get('pelagos-cargo-tonnes').value = '52000';

  let counter = 0;
  return {
    elements,
    document: {
      getElementById: (id) => elements.get(id) || null,
      createElement: (tag) => makeElement(`dyn-${++counter}`, tag),
    },
  };
}

/** Genuine PNW-JPN-BULK insufficient_data fixture matching `/api/v1/charter/recommend`. */
function makeInsufficientRecommendResponse(overrides = {}) {
  return {
    status: 'insufficient_data',
    recommendation_issued: false,
    recommended_action: null,
    recommended_contract_structure: null,
    corridor: {
      route_code: 'PNW-JPN-BULK',
      route_name: 'U.S. Pacific Northwest to Japan Bulk Grain Ocean Corridor',
      origin_region: 'U.S. Pacific Northwest',
      destination_region: 'Japan',
      commodity: 'Grain / Dry Bulk',
      vessel_class: 'Panamax / Supramax',
    },
    cargo_tonnes: 52000.0,
    commodity: 'Agricultural Bulk Grain',
    data_readiness_gates: {
      corridor_and_freight_market: {
        domain: 'corridor_and_freight_market',
        status: 'available',
        details:
          'Verified benchmark rate $38.00/MT (2026-10-01); gross freight $1,976,000.00.',
        missing_items: [],
      },
      directional_forecast_edge: {
        domain: 'directional_forecast_edge',
        status: 'insufficient_data',
        details:
          'Active production forecasting baseline is persistence (T+1, T+3, T+6 all equal latest observed rate; zero directional edge for Wait vs. Fix Now timing).',
        missing_items: ['calibrated_directional_forward_rate_edge'],
      },
      operational_route_and_ports: {
        domain: 'operational_route_and_ports',
        status: 'partial',
        details:
          'Navigable sea route evaluated (4403.4 NM); port/canal limits audited.',
        missing_items: [
          'USSEA_verified_berth_draft_and_loa_limits',
          'JPYOK_verified_berth_draft_and_loa_limits',
        ],
      },
      vessel_physical_particulars: {
        domain: 'vessel_physical_particulars',
        status: 'insufficient_data',
        details:
          'Evaluated 120 genuine AIS vessel record(s); 0 verified DWT values, 7 dimensionally screened only.',
        missing_items: [
          'verified_vessel_dwt_tonnage',
          'verified_hold_cleanliness',
          'verified_grain_cubic_capacity',
          'complete_cargo_vessel_draft_particulars',
        ],
      },
      commercial_open_tonnage_and_laycan: {
        domain: 'commercial_open_tonnage_and_laycan',
        status: 'unavailable',
        details:
          'Live AIS and commercial open-tonnage broker lists are unconnected; current DB AIS snapshot is historical (Jan 1, 2024 sample). Laycan reachability and vessel employment are unconfirmed.',
        missing_items: [
          'commercial_open_tonnage_list',
          'live_vessel_ais_positions',
          'laycan_itinerary_confirmation',
        ],
      },
      bunker_and_voyage_economics: {
        domain: 'bunker_and_voyage_economics',
        status: 'unavailable',
        details:
          "Corridor bunker hub status is 'unavailable' (0/3 hubs quoted); fuel-consumption curves, port disbursements, demurrage, and COA/TC rates are unavailable.",
        missing_items: [
          'corridor_hub_bunker_price_quotes',
          'vessel_fuel_consumption_curve',
          'port_disbursements_and_canal_tolls',
          'coa_and_time_charter_quotes',
        ],
      },
    },
    missing_decision_inputs: [
      'calibrated_directional_forward_rate_edge',
      'USSEA_verified_berth_draft_and_loa_limits',
      'JPYOK_verified_berth_draft_and_loa_limits',
      'verified_vessel_dwt_tonnage',
      'verified_hold_cleanliness',
      'verified_grain_cubic_capacity',
      'complete_cargo_vessel_draft_particulars',
      'commercial_open_tonnage_list',
      'live_vessel_ais_positions',
      'laycan_itinerary_confirmation',
      'corridor_hub_bunker_price_quotes',
      'vessel_fuel_consumption_curve',
      'port_disbursements_and_canal_tolls',
      'coa_and_time_charter_quotes',
    ],
    verified_supporting_analytics: {
      gross_freight_cost_usd: 1976000.0,
      applicable_rate: {
        rate_value: 38.0,
        currency: 'USD',
        unit: 'USD/MT',
        rate_type: 'observed',
        observation_date: '2026-10-01',
        observation_period: 'monthly',
        source_name:
          "USDA Agricultural Marketing Service / O'Neil Commodity Consulting",
        source_dataset_id: 'ehs5-yac3',
        license_type: 'U.S. Public Domain',
        is_derived: false,
        provenance_validated: true,
      },
      operational_route: {
        route_navigable: true,
        routing_status: 'calculated',
        routing_mode: 'prototype',
        route_distance_nm: 4403.414,
        canals_transited: [],
        error_message: null,
      },
      persistence_forecasts: {
        'T+1': {
          model_type: 'persistence',
          origin_date: '2026-10-01',
          target_date: '2026-11-01',
          predicted_value: 38.0,
          unit: 'USD/MT',
        },
        'T+3': {
          model_type: 'persistence',
          origin_date: '2026-10-01',
          target_date: '2027-01-01',
          predicted_value: 38.0,
          unit: 'USD/MT',
        },
        'T+6': {
          model_type: 'persistence',
          origin_date: '2026-10-01',
          target_date: '2027-04-01',
          predicted_value: 38.0,
          unit: 'USD/MT',
        },
      },
      environmental_coverage: 'departure_waters_monitored',
      bunker_fuel_status: 'unavailable',
      bunker_hubs_quoted: '0/3',
      vessels_evaluated_count: 120,
      dimensionally_screened_candidates_count: 7,
      data_mode: 'historical',
    },
    contract_comparisons: [],
    ranked_vessel_candidates: [
      {
        mmsi: '212719000',
        imo: '9432100',
        vessel_name: 'TROODOS SUN',
        vessel_type: 'Cargo',
        draft_meters: 12.5,
        verified_cargo_capacity_dwt: null,
        cargo_capacity_status: 'UNVERIFIED',
        observation_time: '2024-01-01T00:00:00Z',
        is_stale: true,
        data_mode: 'historical',
        registry_commercial_status: 'active',
        commercial_availability: 'UNCONFIRMED',
        commercial_availability_note:
          'Historical AIS presence only; no live broker fixture list.',
        laycan_feasibility: 'UNCONFIRMED',
        missing_particulars: [
          'dwt_tonnage',
          'hold_cleanliness',
          'commercial_fixture_status',
        ],
        cargo_compatibility: {
          status: 'potentially_compatible_unverified_capacity',
        },
        route_compatibility: {
          status: 'partial',
          origin_port: {
            port_id: 'USSEA',
            port_name: 'Seattle',
            status: 'insufficient_data',
            documented_draft_limit_meters: null,
          },
          destination_port: {
            port_id: 'JPYOK',
            port_name: 'Yokohama',
            status: 'insufficient_data',
            documented_draft_limit_meters: null,
          },
          canal_compatibility_notes: [],
        },
      },
    ],
    ais_vs_commercial_summary:
      'Historical AIS observations in Pelagos demonstrate dimensional screening only and do NOT establish open-tonnage availability, laycan readiness, or commercial fixture status.',
    disclaimers: [
      'Potential vessel suitability is evaluated strictly as a dimensional screening (AIS vessel type and reported dimensions).',
      'Commercial open-tonnage status, charter party commitments, vessel employment, and fixture lists are NOT reported in AIS broadcasts.',
    ],
    message:
      "Actionable chartering recommendation is withheld ('insufficient_data') for corridor 'PNW-JPN-BULK'. Monitored freight benchmark, persistence forecasts, and historical AIS dimensional screening are available, but commercial open-tonnage fixtures, verified DWT, corridor bunker quotes, voyage economics, and directional forecast edge are missing.",
    ...overrides,
  };
}

/** Valid backend-authorized positive fixture matching `test_charter_intelligence.py` schema. */
function makeRecommendedResponse() {
  return {
    status: 'recommended',
    recommendation_issued: true,
    recommended_action: 'Fix Now',
    recommended_contract_structure: 'Consecutive Voyage Charter (COA)',
    corridor: {
      route_code: 'PNW-JPN-BULK',
      route_name: 'U.S. Pacific Northwest to Japan Bulk Grain Ocean Corridor',
      vessel_class: 'Panamax / Supramax',
    },
    cargo_tonnes: 52000.0,
    commodity: 'Agricultural Bulk Grain',
    data_readiness_gates: {
      corridor_and_freight_market: {
        domain: 'corridor_and_freight_market',
        status: 'available',
        details:
          'Verified benchmark rate $38.00/MT (2026-10-01); gross freight $1,976,000.00.',
        missing_items: [],
      },
      directional_forecast_edge: {
        domain: 'directional_forecast_edge',
        status: 'available',
        details: 'Verified non-flat directional forward rate signal supplied.',
        missing_items: [],
      },
      operational_route_and_ports: {
        domain: 'operational_route_and_ports',
        status: 'available',
        details:
          'Navigable sea route evaluated (4403.4 NM); port/canal limits audited.',
        missing_items: [],
      },
      vessel_physical_particulars: {
        domain: 'vessel_physical_particulars',
        status: 'available',
        details:
          'Verified physical particulars supplied via commercial fixture bundle.',
        missing_items: [],
      },
      commercial_open_tonnage_and_laycan: {
        domain: 'commercial_open_tonnage_and_laycan',
        status: 'available',
        details:
          'Verified open-tonnage fixtures supplied from Unit Test Verified Fixture Feed.',
        missing_items: [],
      },
      bunker_and_voyage_economics: {
        domain: 'bunker_and_voyage_economics',
        status: 'available',
        details:
          'Verified bunker quote ($620.00/MT VLSFO) and voyage parameters supplied.',
        missing_items: [],
      },
    },
    missing_decision_inputs: [],
    verified_supporting_analytics: {
      gross_freight_cost_usd: 1976000.0,
      applicable_rate: {
        rate_value: 38.0,
        unit: 'USD/MT',
        observation_date: '2026-10-01',
        source_name:
          "USDA Agricultural Marketing Service / O'Neil Commodity Consulting",
      },
      operational_route: {
        route_navigable: true,
        routing_status: 'calculated',
        routing_mode: 'prototype',
        route_distance_nm: 4403.4,
        canals_transited: [],
      },
      persistence_forecasts: {
        'T+1': {
          model_type: 'persistence',
          origin_date: '2026-10-01',
          target_date: '2026-11-01',
          predicted_value: 38.0,
          unit: 'USD/MT',
        },
      },
      bunker_fuel_status: 'verified_input',
      bunker_hubs_quoted: '1/1',
      vessels_evaluated_count: 1,
      data_mode: 'verified_fixture',
    },
    contract_comparisons: [
      {
        contract_structure: 'Consecutive Voyage Charter (COA)',
        total_cost_usd: 6120000.0,
        unit_cost_usd_per_mt: 39.23,
        voyages_evaluated: 3,
      },
      {
        contract_structure: 'Spot Single Voyage',
        total_cost_usd: 2150000.0,
        unit_cost_usd_per_mt: 41.35,
        voyages_evaluated: 1,
      },
    ],
    ranked_vessel_candidates: [
      {
        mmsi: '800000004',
        vessel_name: 'COMPLIANT GRAIN STAR',
        draft_meters: 12.1,
        verified_cargo_capacity_dwt: 74000,
        cargo_capacity_status: 'VERIFIED',
        commercial_availability: 'VERIFIED_OPEN',
        laycan_feasibility: 'FEASIBLE',
      },
    ],
    ais_vs_commercial_summary:
      'Recommendation derived strictly from verified commercial fixture bundle.',
    disclaimers: ['Verified fixture test run.'],
    message:
      'Evaluated 1 verified open vessel(s); lowest verified unit cost is Consecutive Voyage Charter (COA) (39.23 USD/MT).',
  };
}

test('1. Correct request payload construction for POST and GET /api/v1/charter/recommend (without MMSI)', async () => {
  const captured = [];
  const fetchMock = async (url, opts = {}) => {
    captured.push({ url: String(url), opts });
    return {
      ok: true,
      status: 200,
      json: async () => makeInsufficientRecommendResponse(),
    };
  };

  await recommendCharterReadiness(
    {
      origin: 'USSEA',
      destination: 'JPYOK',
      cargo_tonnes: 52000,
      commodity: 'Agricultural Bulk Grain',
      canal_policy: 'avoid_suez',
      laycan_start: '2026-11-15',
      laycan_end: '2026-11-25',
      max_vessels: 25,
    },
    { method: 'POST', fetchImpl: fetchMock },
  );

  assert.equal(captured[0].url, '/api/v1/charter/recommend');
  assert.equal(captured[0].opts.method, 'POST');
  const postBody = JSON.parse(captured[0].opts.body);
  assert.deepEqual(postBody, {
    origin: 'USSEA',
    destination: 'JPYOK',
    cargo_tonnes: 52000,
    commodity: 'Agricultural Bulk Grain',
    canal_policy: 'avoid_suez',
    laycan_start: '2026-11-15',
    laycan_end: '2026-11-25',
    max_vessels: 25,
  });
  assert.equal('mmsi' in postBody, false);

  await recommendCharterReadiness(
    {
      route_code: 'USG-JPN-BULK',
      cargo_tonnes: 55000,
      commodity: 'Agricultural Bulk Grain',
      canal_policy: 'allow',
    },
    { method: 'GET', fetchImpl: fetchMock },
  );

  assert.equal(captured[1].opts.method, 'GET');
  assert.match(captured[1].url, /^\/api\/v1\/charter\/recommend\?/);
  assert.match(captured[1].url, /route_code=USG-JPN-BULK/);
  assert.match(captured[1].url, /cargo_tonnes=55000/);

  // Controller payload prefers route_code when selected
  const { document, elements } = createMockDom();
  const controller = new PelagosReadinessController({
    document,
    fetchImpl: fetchMock,
  });
  controller.init();
  elements.get('pelagos-readiness-route-code').value = 'PNW-JPN-BULK';
  elements.get('pelagos-readiness-laycan-start').value = '2026-11-15';
  elements.get('pelagos-readiness-laycan-end').value = '2026-11-20';

  const built = controller.buildReadinessPayload();
  assert.equal(built.valid, true);
  assert.equal(built.payload.route_code, 'PNW-JPN-BULK');
  assert.equal('origin' in built.payload, false);
  assert.equal('mmsi' in built.payload, false);
});

test('2 & 3. Successful readiness assessment renders all six gates with exact backend gate IDs', async () => {
  const { document, elements } = createMockDom();
  const controller = new PelagosReadinessController({
    document,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => makeInsufficientRecommendResponse(),
    }),
  });
  controller.init();
  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'JPYOK';

  const ok = await controller.auditReadiness();
  assert.equal(ok, true);
  assert.equal(elements.get('pelagos-readiness-result').style.display, 'block');

  const gatesEl = elements.get('pelagos-readiness-gates');
  assert.equal(gatesEl.children.length, 6);
  const renderedKeys = gatesEl.children.map((c) => c.dataset.gateKey);
  assert.deepEqual(renderedKeys, [...READINESS_GATE_ORDER]);
});

test('4 & 5. Semantic gate status mapping preserves FAIL vs INSUFFICIENT_DATA vs UNVERIFIED vs UNAVAILABLE distinction', () => {
  assert.equal(mapGateStatusToSemantic('available').semantic, 'PASS');
  assert.equal(mapGateStatusToSemantic('fail').semantic, 'FAIL');
  assert.equal(mapGateStatusToSemantic('incompatible').semantic, 'FAIL');
  assert.equal(
    mapGateStatusToSemantic('insufficient_data').semantic,
    'INSUFFICIENT_DATA',
  );
  assert.equal(mapGateStatusToSemantic('partial').semantic, 'UNVERIFIED');
  assert.equal(mapGateStatusToSemantic('unavailable').semantic, 'UNAVAILABLE');

  const mixedGates = {
    corridor_and_freight_market: { status: 'available', details: 'ok' },
    directional_forecast_edge: { status: 'insufficient_data', details: 'flat' },
    operational_route_and_ports: { status: 'fail', details: 'draft exceeded' },
    vessel_physical_particulars: { status: 'partial', details: 'no dwt' },
    commercial_open_tonnage_and_laycan: {
      status: 'unavailable',
      details: 'unconnected',
    },
    bunker_and_voyage_economics: { status: 'unavailable', details: 'missing' },
  };
  const counts = computeGateSummary(mixedGates);
  assert.equal(counts.passed, 1);
  assert.equal(counts.failed, 1);
  assert.equal(counts.insufficientData, 1);
  assert.equal(counts.unverified, 1);
  assert.equal(counts.unavailable, 2);

  const { document, elements } = createMockDom();
  const controller = new PelagosReadinessController({ document });
  const resp = makeInsufficientRecommendResponse({
    data_readiness_gates: mixedGates,
  });
  controller.renderResult(resp, { origin: 'USSEA', destination: 'JPYOK' });

  const gateCards = elements.get('pelagos-readiness-gates').children;
  const routeCard = gateCards.find(
    (c) => c.dataset.gateKey === 'operational_route_and_ports',
  );
  const forecastCard = gateCards.find(
    (c) => c.dataset.gateKey === 'directional_forecast_edge',
  );
  assert.equal(routeCard.className, 'readiness-gate-card gate-semantic-fail');
  assert.equal(
    forecastCard.className,
    'readiness-gate-card gate-semantic-insufficient_data',
  );
  assert.match(
    elements.get('pelagos-readiness-summary').textContent,
    /Passed: 1.*Failed: 1.*Insufficient Data: 1.*Unverified \/ Partial: 1.*Unavailable: 2/,
  );
});

test('6 & 7. Renders consolidated missing decision inputs and authentic evidence/provenance', () => {
  const { document, elements } = createMockDom();
  const controller = new PelagosReadinessController({ document });
  const resp = makeInsufficientRecommendResponse();
  controller.renderResult(resp, { origin: 'USSEA', destination: 'JPYOK' });

  const missingText = elements.get(
    'pelagos-readiness-missing-inputs',
  ).textContent;
  assert.match(missingText, /MISSING DECISION INPUTS \(14\)/);
  assert.match(missingText, /calibrated_directional_forward_rate_edge/);
  assert.match(missingText, /USSEA_verified_berth_draft_and_loa_limits/);
  assert.match(missingText, /commercial_open_tonnage_list/);

  const gatesText = elements.get('pelagos-readiness-gates').textContent;
  assert.match(gatesText, /\$38\.00 USD\/MT/);
  assert.match(gatesText, /2026-10-01/);
  assert.match(gatesText, /ehs5-yac3/);
  assert.match(gatesText, /MARNET.*50 km sea-lane topology/i);
  assert.match(gatesText, /TROODOS SUN \(MMSI 212719000 · IMO 9432100\)/);
});

test('8 & 9. Recommendation withheld never fabricates actions; valid recommendation renders backend action only when recommendation_issued is true', () => {
  const { document, elements } = createMockDom();
  const controller = new PelagosReadinessController({ document });

  // 8. Withheld
  controller.renderResult(makeInsufficientRecommendResponse(), {
    origin: 'USSEA',
    destination: 'JPYOK',
  });
  const outcomeWithheld = elements.get('pelagos-readiness-outcome').textContent;
  assert.match(outcomeWithheld, /RECOMMENDATION WITHHELD — INSUFFICIENT DATA/);
  assert.equal(
    elements.get('pelagos-readiness-contracts').style.display,
    'none',
  );

  // Defense-in-depth: even if backend returns recommended_action without recommendation_issued: true, withhold it
  const sneaky = classifyReadinessOutcome({
    status: 'recommended',
    recommendation_issued: false,
    recommended_action: 'Fix Now',
  });
  assert.equal(sneaky.recommendationIssued, false);
  assert.equal(sneaky.recommendedAction, null);

  // 9. Valid issued recommendation from verified fixture contract
  const issuedResp = makeRecommendedResponse();
  controller.renderResult(issuedResp, {
    origin: 'USSEA',
    destination: 'JPYOK',
  });
  const outcomeIssued = elements.get('pelagos-readiness-outcome').textContent;
  assert.match(outcomeIssued, /RECOMMENDATION ISSUED/);
  assert.match(
    outcomeIssued,
    /Backend Action: Fix Now · Contract: Consecutive Voyage Charter \(COA\)/,
  );
  assert.equal(
    elements.get('pelagos-readiness-contracts').style.display,
    'block',
  );
  assert.match(
    elements.get('pelagos-readiness-contracts').textContent,
    /\$39\.23\/MT \(3 voyage\(s\)\)/,
  );
});

test('10, 11, 12 & 13. Exposes persistence baseline, unconfirmed commercial/laycan, and missing bunker/voyage economics without fabrication', () => {
  const { document, elements } = createMockDom();
  const corridorCtx = {
    corridor: { route_code: 'PNW-JPN-BULK' },
    bunker_fuel_references: {
      provider: 'Ship & Bunker / Reference Sample',
      bunkering_hubs_in_scope: [
        {
          name: 'Seattle / Tacoma',
          port_id: 'USSEA',
          role: 'origin',
          status: 'unavailable',
          quotes: [],
        },
      ],
      regional_benchmarks: [
        {
          port_name: 'Singapore',
          port_code: 'SGSIN',
          fuel_grade: 'VLSFO',
          price_usd_per_mt: 590.5,
          unit: 'USD/MT',
          observation_time: '2026-10-01T00:00:00Z',
        },
      ],
    },
  };

  const controller = new PelagosReadinessController({
    document,
    getCorridorFreightContext: () => corridorCtx,
  });

  const resp = makeInsufficientRecommendResponse();
  controller.renderResult(resp, { origin: 'USSEA', destination: 'JPYOK' });

  const gatesText = elements.get('pelagos-readiness-gates').textContent;
  // 10. Persistence
  assert.match(gatesText, /Active Production Model: persistence/);
  assert.match(
    gatesText,
    /T\+1: \$38\.00 USD\/MT \(Origin: 2026-10-01 → Target: 2026-11-01\)/,
  );
  assert.match(
    gatesText,
    /C3\.2\/C3\.3 experimental ML candidates are NOT promoted/,
  );

  // 11 & 12. Commercial & Laycan unconfirmed + historical AIS distinction
  assert.match(
    gatesText,
    /Commercial Availability: UNCONFIRMED · Laycan Feasibility: UNCONFIRMED/,
  );
  assert.match(
    gatesText,
    /Registry Sync Flag: "active".*NOT commercially unfixed/i,
  );

  // 13. Bunker & Voyage economics + distinct regional benchmark
  assert.match(
    gatesText,
    /Corridor Bunker Status: UNAVAILABLE \(0\/3 hubs quoted\)/,
  );
  assert.match(
    gatesText,
    /Regional Benchmark ONLY \(NOT corridor hub price\): Singapore \(SGSIN\) · VLSFO · \$590\.50 USD\/MT/,
  );
  assert.match(gatesText, /gross ocean freight is NOT total voyage cost/i);
});

test('14 & 15. Unsupported route (unavailable) and client/backend invalid input handling', () => {
  const { document, elements } = createMockDom();
  const controller = new PelagosReadinessController({ document });
  controller.init();

  // 15. Client validation blocks <=0 cargo tonnage & laycan_end < laycan_start
  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'JPYOK';
  elements.get('pelagos-readiness-cargo-tonnes').value = '0';
  elements.get('pelagos-readiness-laycan-start').value = '2026-11-20';
  elements.get('pelagos-readiness-laycan-end').value = '2026-11-10';
  const badPayload = controller.buildReadinessPayload();
  assert.equal(badPayload.valid, false);
  assert.match(badPayload.error, /positive cargo tonnage/);
  assert.match(badPayload.error, /laycan cancelling date/);

  // 14. Unsupported/unmonitored route short-circuited by backend
  const unavailableResp = {
    status: 'unavailable',
    recommendation_issued: false,
    recommended_action: null,
    recommended_contract_structure: null,
    corridor: null,
    cargo_tonnes: 50000.0,
    commodity: 'Agricultural Bulk Grain',
    data_readiness_gates: {
      corridor_and_freight_market: {
        domain: 'corridor_and_freight_market',
        status: 'unavailable',
        details: 'Route is outside monitored bulk freight corridors.',
        missing_items: [
          'monitored_freight_rate_history',
          'corridor_benchmark_observations',
        ],
      },
    },
    missing_decision_inputs: ['monitored_freight_rate_history'],
    message:
      'Freight market intelligence is unavailable for unmonitored trade corridors.',
  };

  controller.renderResult(unavailableResp, {
    origin: 'NLRTM',
    destination: 'SGSIN',
    cargo_tonnes: 50000,
  });
  assert.match(
    elements.get('pelagos-readiness-outcome').textContent,
    /RECOMMENDATION WITHHELD — UNAVAILABLE/,
  );
  // Still shows all 6 gate slots cleanly, marking unreached downstream gates unavailable
  assert.equal(elements.get('pelagos-readiness-gates').children.length, 6);
  const g2 = elements
    .get('pelagos-readiness-gates')
    .children.find((c) => c.dataset.gateKey === 'directional_forecast_edge');
  assert.match(g2.textContent, /UNAVAILABLE/);
});

test('16 & 17. API HTTP failure + retry recovery and duplicate concurrent submission prevention', async () => {
  const { document, elements } = createMockDom();
  let calls = 0;
  let resolveSlowFetch;

  const controller = new PelagosReadinessController({
    document,
    fetchImpl: async () => {
      calls += 1;
      if (calls === 1) {
        return {
          ok: false,
          status: 503,
          json: async () => ({ detail: 'Readiness backend unavailable' }),
        };
      }
      if (calls === 2) {
        return {
          ok: true,
          status: 200,
          json: async () => makeInsufficientRecommendResponse(),
        };
      }
      return new Promise((resolve) => {
        resolveSlowFetch = () =>
          resolve({
            ok: true,
            status: 200,
            json: async () => makeInsufficientRecommendResponse(),
          });
      });
    },
  });
  controller.init();
  elements.get('pelagos-origin-select').value = 'USSEA';
  elements.get('pelagos-dest-select').value = 'JPYOK';

  // 16. Failure then Retry
  const firstOk = await controller.auditReadiness();
  assert.equal(firstOk, false);
  assert.equal(elements.get('pelagos-readiness-error').style.display, 'block');
  assert.match(
    elements.get('pelagos-readiness-error-text').textContent,
    /Readiness backend unavailable/,
  );

  const retryOk = await controller.retryLastAudit();
  assert.equal(retryOk, true);
  assert.equal(elements.get('pelagos-readiness-error').style.display, 'none');
  assert.equal(elements.get('pelagos-readiness-result').style.display, 'block');

  // 17. Concurrent lock
  const p1 = controller.auditReadiness();
  assert.equal(controller.isAuditing, true);
  assert.equal(elements.get('pelagos-readiness-submit-btn').disabled, true);
  const second = await controller.auditReadiness();
  assert.equal(second, false);
  assert.equal(calls, 3);

  resolveSlowFetch();
  assert.equal(await p1, true);
  assert.equal(controller.isAuditing, false);
  assert.equal(elements.get('pelagos-readiness-submit-btn').disabled, false);
});

test('18. Expandable gate cards and PelagosPanelController synchronization', async () => {
  const { document, elements } = createMockDom();
  const origFetch = globalThis.fetch;

  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes('/api/v1/routing/ports')) {
      return {
        ok: true,
        status: 200,
        json: async () => [...VERIFIED_HYDROGRAPHIC_PORTS],
      };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  };

  try {
    const panel = new PelagosPanelController({ document });
    await panel.init();

    panel.readiness.renderResult(makeInsufficientRecommendResponse(), {
      origin: 'USSEA',
      destination: 'JPYOK',
    });

    const gates = elements.get('pelagos-readiness-gates').children;
    const g1Card = gates[0];
    const g1Body = g1Card.children.find(
      (c) => c.className === 'readiness-gate-body',
    );
    assert.equal(g1Body.style.display, 'block');

    // Toggle individual gate closed
    g1Card._toggleBtn.dispatchEvent({ type: 'click' });
    assert.equal(g1Body.style.display, 'none');
    assert.equal(g1Card._toggleBtn.getAttribute('aria-expanded'), 'false');

    // Toggle-all expands remaining collapsed gate
    elements.get('pelagos-readiness-toggle-all-btn').dispatchEvent({
      type: 'click',
    });
    assert.equal(g1Body.style.display, 'block');
    assert.equal(
      elements.get('pelagos-readiness-toggle-all-btn').textContent,
      'Collapse All Gates',
    );

    // Toggle-all collapses all 6 gates
    elements.get('pelagos-readiness-toggle-all-btn').dispatchEvent({
      type: 'click',
    });
    for (const card of gates) {
      const body = card.children.find(
        (c) => c.className === 'readiness-gate-body',
      );
      assert.equal(body.style.display, 'none');
    }

    // Shared cargo & swap & clear synchronization
    elements.get('pelagos-origin-select').value = 'USSEA';
    elements.get('pelagos-dest-select').value = 'JPYOK';
    panel.swapEndpoints();
    assert.match(
      elements.get('pelagos-readiness-voyage-summary').textContent,
      /Yokohama \(JPYOK\) → Seattle \(USSEA\)/,
    );

    elements.get('pelagos-readiness-cargo-tonnes').value = '61000';
    elements
      .get('pelagos-readiness-cargo-tonnes')
      .dispatchEvent({ type: 'input' });
    assert.equal(elements.get('pelagos-cargo-tonnes').value, '61000');

    panel.clearRoute();
    assert.equal(
      elements.get('pelagos-readiness-result').style.display,
      'none',
    );
    assert.equal(
      elements.get('pelagos-readiness-status-tag').textContent,
      'AWAITING AUDIT',
    );

    panel.destroy();
  } finally {
    globalThis.fetch = origFetch;
  }
});

test('Gate presentation helper builds honest gate-6 fallback without context and gate-3 draft provenance when present', () => {
  const resp = makeInsufficientRecommendResponse();
  resp.ranked_vessel_candidates[0].route_compatibility.origin_port = {
    port_id: 'USKAL',
    port_name: 'Kalama',
    status: 'compatible',
    documented_draft_limit_meters: 13.11,
    limit_source_note: 'USACE 43ft fresh water limit',
  };

  const g3 = buildGatePresentation(
    'operational_route_and_ports',
    resp.data_readiness_gates.operational_route_and_ports,
    resp,
  );
  assert.ok(
    g3.evidence.some((e) =>
      e.includes('13.11m draft limit (USACE 43ft fresh water limit)'),
    ),
  );

  const g6 = buildGatePresentation(
    'bunker_and_voyage_economics',
    resp.data_readiness_gates.bunker_and_voyage_economics,
    resp,
    null,
  );
  assert.ok(
    g6.evidence.some((e) =>
      e.includes('Corridor Bunker Status: UNAVAILABLE (0/3 hubs quoted)'),
    ),
  );
});
