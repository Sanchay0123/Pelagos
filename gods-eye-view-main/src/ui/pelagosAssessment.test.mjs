import assert from 'node:assert/strict';
import test from 'node:test';
import {
  PelagosAssessmentController,
  buildGroupedMissingInfo,
  classifyAssessmentOutcome,
} from './pelagosAssessment.js';
import { assessVesselCharter, fetchRegisteredVessels } from './pelagosApi.js';
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
      disabled: false,
      style: {},
      children,
      dataset: {},
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
    // Assessment UI IDs
    ['pelagos-assess-card', 'div'],
    ['pelagos-assess-status-tag', 'span'],
    ['pelagos-assess-vessel-filter', 'input'],
    ['pelagos-assess-vessel-select', 'select'],
    ['pelagos-assess-vessel-registry-status', 'div'],
    ['pelagos-assess-reload-vessels-btn', 'button'],
    ['pelagos-assess-vessel-preview', 'div'],
    ['pelagos-assess-voyage-summary', 'div'],
    ['pelagos-assess-cargo-tonnes', 'input'],
    ['pelagos-assess-commodity', 'input'],
    ['pelagos-assess-submit-btn', 'button'],
    ['pelagos-assess-validation-msg', 'div'],
    ['pelagos-assess-error', 'div'],
    ['pelagos-assess-error-text', 'span'],
    ['pelagos-assess-retry-btn', 'button'],
    ['pelagos-assess-empty', 'div'],
    ['pelagos-assess-result', 'div'],
    ['pelagos-assess-outcome', 'div'],
    ['pelagos-assess-vessel-section', 'div'],
    ['pelagos-assess-cargo-section', 'div'],
    ['pelagos-assess-route-section', 'div'],
    ['pelagos-assess-ais-commercial-section', 'div'],
    ['pelagos-assess-missing-section', 'div'],
    ['pelagos-assess-disclaimers', 'ul'],
    // Shared panel selectors
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

  elements.get('pelagos-assess-cargo-tonnes').value = '52000';
  elements.get('pelagos-assess-commodity').value = 'Agricultural Bulk Grain';
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

function makeCandidateBase(overrides = {}) {
  return {
    mmsi: '212719000',
    vessel_name: 'MV AEGEAN TRADER',
    imo: '9432100',
    vessel_type: 'Cargo',
    vessel_type_code: 70,
    estimated_dimensional_class: 'Supramax',
    registry_vessel_class: 'Supramax',
    dwt_tonnage: 58000,
    verified_cargo_capacity_dwt: 58000,
    cargo_capacity_status: 'VERIFIED',
    length_meters: 190.0,
    width_meters: 32.2,
    draft_meters: 12.1,
    particulars_completeness: {
      completeness_status: 'complete',
      verified_particulars: [
        'dwt_tonnage',
        'length_meters',
        'width_meters',
        'draft_meters',
      ],
      missing_particulars: [
        'hold_cleanliness_status',
        'commercial_fixture_status',
      ],
      data_sources: ['registered_vessels', 'ais_observations'],
      registry_vessel_class: 'Supramax',
    },
    missing_particulars: [
      'hold_cleanliness_status',
      'commercial_fixture_status',
    ],
    screening_stage: 'dimensional_and_dwt_screened',
    suitability_status: 'compatible_physical_and_route',
    suitability_reason:
      'Vessel physical dimensions and verified DWT match cargo and corridor constraints.',
    cargo_compatibility: {
      status: 'compatible',
      cargo_tonnes: 52000,
      commodity: 'Agricultural Bulk Grain',
      verified_dwt_tonnes: 58000,
      capacity_check_basis: 'verified_dwt_tonnage',
      reasons: [
        'Verified DWT (58,000 MT) accommodates 52,000 MT grain parcel with 10.3% margin.',
      ],
      missing_requirements: ['hold_cleanliness_status'],
    },
    route_compatibility: {
      status: 'compatible',
      route_navigable: true,
      routing_status: 'success',
      routing_mode: 'marnet',
      route_distance_nm: 6240.5,
      canals_transited: ['Panama Canal'],
      origin_port: {
        port_id: 'USMSY',
        port_name: 'New Orleans',
        status: 'compatible',
        documented_draft_limit_meters: 13.7,
        limit_source_note: 'Port authority Mississippi River channel limit',
        vessel_draft_meters: 12.1,
        access_limitations: null,
        reasons: ['Vessel draft 12.1m clears documented 13.7m channel draft.'],
        missing_requirements: [],
      },
      destination_port: {
        port_id: 'NLRTM',
        port_name: 'Rotterdam',
        status: 'compatible',
        documented_draft_limit_meters: 16.5,
        limit_source_note: 'Port of Rotterdam Maasvlakte terminal limit',
        vessel_draft_meters: 12.1,
        access_limitations: null,
        reasons: ['Vessel draft 12.1m clears documented 16.5m berth depth.'],
        missing_requirements: [],
      },
      canal_compatibility_notes: [
        'Panama Canal Neopanamax beam (<=49.0m) cleared (32.2m beam).',
      ],
      reasons: ['MARNET corridor navigable under allow canal policy.'],
      missing_requirements: [],
    },
    observation_time: '2026-11-12T10:15:00Z',
    observation_age_seconds: 185000,
    is_stale: true,
    data_mode: 'historical',
    latitude: 29.1,
    longitude: -89.2,
    distance_to_load_port_nm: 45.3,
    distance_qualification: 'HISTORICAL_OBSERVATION_ONLY',
    registry_commercial_status: 'active',
    commercial_availability: 'UNCONFIRMED',
    commercial_availability_note:
      'No live broker open-tonnage feed integrated.',
    laycan_feasibility: 'UNCONFIRMED',
    laycan_feasibility_note:
      'Commercial laycan window commitments are unverified.',
    ...overrides,
  };
}

function makeAssessResponse(overrides = {}) {
  const candidate =
    'candidate' in overrides ? overrides.candidate : makeCandidateBase();
  return {
    status: 'assessed',
    overall_suitability: 'potentially_suitable',
    corridor: {
      route_code: 'USG-ROT',
      route_name: 'US Gulf to Rotterdam',
      vessel_class: 'Panamax',
    },
    ais_observation_status: {
      observation_time: candidate?.observation_time ?? null,
      observation_age_seconds: candidate?.observation_age_seconds ?? null,
      is_stale: candidate?.is_stale ?? true,
      data_mode: candidate?.data_mode ?? 'historical',
      latitude: candidate?.latitude ?? null,
      longitude: candidate?.longitude ?? null,
      distance_qualification:
        candidate?.distance_qualification ?? 'HISTORICAL_OBSERVATION_ONLY',
    },
    commercial_availability_status: {
      registry_commercial_status:
        candidate?.registry_commercial_status ?? 'active',
      registry_status_meaning:
        'Registry commercial_status ("active") means historical registry synchronization only — NOT that vessel tonnage is open or commercially unfixed.',
      commercial_availability: 'UNCONFIRMED',
      commercial_availability_note: 'Broker open-tonnage feed not integrated.',
      laycan_feasibility: 'UNCONFIRMED',
    },
    disclaimers: [
      'Physical & dimensional screening only — does NOT constitute commercial fixture confirmation.',
      'AIS observations older than 30 minutes represent historical positions only.',
    ],
    message:
      'Vessel physically screened for US Gulf to Rotterdam (commercial fixture & laycan UNCONFIRMED).',
    ...overrides,
    candidate,
  };
}

test('1. Registered vessel pagination, filtering, selection, and empty/error registry state (no hardcoded vessels)', async () => {
  const { document, elements } = createMockDom();
  const seenUrls = [];

  const fetchMock = async (url) => {
    seenUrls.push(String(url));
    if (String(url).includes('page=1')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'ok',
          domain: 'vessels',
          page: 1,
          page_size: 100,
          total_count: 2,
          total_pages: 2,
          items: [
            {
              mmsi: '212719000',
              imo: '9432100',
              vessel_name: 'MV AEGEAN TRADER',
              vessel_type: 'Cargo',
              vessel_class: 'Supramax',
              commercial_status: 'active',
              dwt_tonnage: 58000,
              length_meters: 190,
              width_meters: 32.2,
              draft_max_meters: 12.1,
              missing_particulars: [],
            },
          ],
        }),
      };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({
        status: 'ok',
        domain: 'vessels',
        page: 2,
        page_size: 100,
        total_count: 2,
        total_pages: 2,
        items: [
          {
            mmsi: '355100200',
            imo: null,
            vessel_name: 'PACIFIC HORIZON',
            vessel_type: 'Tanker',
            vessel_class: null,
            commercial_status: 'active',
            dwt_tonnage: null,
            length_meters: 228,
            width_meters: 32.3,
            draft_max_meters: null,
            missing_particulars: ['dwt_tonnage', 'draft_meters'],
          },
        ],
      }),
    };
  };

  const controller = new PelagosAssessmentController({
    document,
    fetchImpl: fetchMock,
    getPorts: () => VERIFIED_HYDROGRAPHIC_PORTS,
  });

  await controller.init();

  assert.equal(seenUrls.length, 2);
  assert.match(
    seenUrls[0],
    /\/api\/v1\/platform-data\/explorer\/vessels\?.*page_size=100/,
  );
  assert.equal(controller.registeredVessels.length, 2);
  assert.match(
    elements.get('pelagos-assess-vessel-registry-status').textContent,
    /2 registered vessels loaded/i,
  );

  // Filter by MMSI narrows and auto-selects when single match >= 3 chars
  elements.get('pelagos-assess-vessel-filter').value = '355100';
  elements.get('pelagos-assess-vessel-filter').dispatchEvent({ type: 'input' });
  assert.equal(controller.filteredVessels.length, 1);
  assert.equal(elements.get('pelagos-assess-vessel-select').value, '355100200');
  assert.match(
    elements.get('pelagos-assess-vessel-preview').textContent,
    /DWT: UNVERIFIED.*Registry Missing: \[dwt_tonnage, draft_meters\]/,
  );

  // Failure response shows explicit unavailable state + reload button (no hardcoded list)
  const failController = new PelagosAssessmentController({
    document: createMockDom().document,
    fetchImpl: async () => ({ ok: false, status: 503, json: async () => ({}) }),
  });
  await failController.init();
  assert.equal(failController.registeredVessels.length, 0);
  assert.match(
    failController.elements.registryStatus.textContent,
    /Unavailable: Unable to load registered vessels/,
  );
  assert.equal(
    failController.elements.reloadVesselsBtn.style.display,
    'inline-block',
  );
});

test('2. Request payload construction for POST and GET /api/v1/charter/assess and validation', async () => {
  const { document, elements } = createMockDom();
  const captured = [];

  const apiFetch = async (url, opts = {}) => {
    captured.push({ url: String(url), opts });
    return {
      ok: true,
      status: 200,
      json: async () => makeAssessResponse(),
    };
  };

  // Direct API client check for POST and GET
  await assessVesselCharter(
    {
      mmsi: '212719000',
      cargo_tonnes: 52000,
      commodity: 'Wheat',
      origin: 'USMSY',
      destination: 'NLRTM',
      canal_policy: 'avoid_suez',
    },
    { method: 'POST', fetchImpl: apiFetch },
  );
  assert.equal(captured[0].url, '/api/v1/charter/assess');
  assert.equal(captured[0].opts.method, 'POST');
  assert.deepEqual(JSON.parse(captured[0].opts.body), {
    mmsi: '212719000',
    cargo_tonnes: 52000,
    commodity: 'Wheat',
    origin: 'USMSY',
    destination: 'NLRTM',
    canal_policy: 'avoid_suez',
  });

  await assessVesselCharter(
    {
      mmsi: '212719000',
      cargo_tonnes: 52000,
      commodity: 'Wheat',
      origin: 'USMSY',
      destination: 'NLRTM',
      canal_policy: 'avoid_suez',
    },
    { method: 'GET', fetchImpl: apiFetch },
  );
  assert.equal(captured[1].opts.method, 'GET');
  assert.match(captured[1].url, /\/api\/v1\/charter\/assess\?.*mmsi=212719000/);
  assert.match(captured[1].url, /cargo_tonnes=52000/);
  assert.match(captured[1].url, /canal_policy=avoid_suez/);

  // Controller validation blocks missing/invalid inputs before fetch
  const controller = new PelagosAssessmentController({
    document,
    fetchImpl: async (url) => {
      if (String(url).includes('/explorer/vessels')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            page: 1,
            total_pages: 1,
            total_count: 1,
            items: [{ mmsi: '212719000', vessel_name: 'MV AEGEAN TRADER' }],
          }),
        };
      }
      return apiFetch(url);
    },
  });
  await controller.init();

  const missingCheck = await controller.assess();
  assert.equal(missingCheck, false);
  assert.match(
    elements.get('pelagos-assess-validation-msg').textContent,
    /a registered vessel \(MMSI\), an origin port, a destination port/,
  );

  // Same origin and destination blocked
  elements.get('pelagos-assess-vessel-select').value = '212719000';
  elements.get('pelagos-origin-select').value = 'USMSY';
  elements.get('pelagos-dest-select').value = 'USMSY';
  const samePortCheck = await controller.assess();
  assert.equal(samePortCheck, false);
  assert.match(
    elements.get('pelagos-assess-validation-msg').textContent,
    /Origin and destination ports must be different/,
  );
});

test('3. Verified DWT vs Unverified DWT rendering and 4. Missing particulars completeness', () => {
  const { document, elements } = createMockDom();
  const controller = new PelagosAssessmentController({ document });

  // Verified DWT
  controller.renderResult(makeAssessResponse(), { canal_policy: 'allow' });
  assert.match(
    elements.get('pelagos-assess-vessel-section').textContent,
    /58,000 MT \(VERIFIED\)/,
  );
  assert.equal(
    elements.get('pelagos-assess-status-tag').textContent,
    'COMPATIBLE (PHYSICAL & ROUTE ONLY)',
  );

  // Unverified DWT & partial/insufficient particulars
  const unverifiedResp = makeAssessResponse({
    overall_suitability: 'potentially_suitable',
    candidate: makeCandidateBase({
      verified_cargo_capacity_dwt: null,
      dwt_tonnage: null,
      cargo_capacity_status: 'UNVERIFIED',
      draft_meters: null,
      suitability_status: 'potentially_compatible_physical_dimensions_only',
      screening_stage: 'dimensional_only_unverified_dwt',
      particulars_completeness: {
        completeness_status: 'partial',
        verified_particulars: ['length_meters', 'width_meters'],
        missing_particulars: [
          'dwt_tonnage',
          'draft_meters',
          'hold_cleanliness_status',
        ],
        data_sources: ['ais_observations'],
      },
      cargo_compatibility: {
        status: 'potentially_compatible_unverified_capacity',
        cargo_tonnes: 52000,
        commodity: 'Agricultural Bulk Grain',
        verified_dwt_tonnes: null,
        capacity_check_basis: 'unverified_ais_dimensions_only',
        reasons: [
          'Verified DWT unavailable; cannot certify 52,000 MT cargo deadweight capacity.',
        ],
        missing_requirements: ['dwt_tonnage', 'hold_cleanliness_status'],
      },
    }),
  });

  controller.renderResult(unverifiedResp, { canal_policy: 'allow' });
  assert.equal(
    elements.get('pelagos-assess-status-tag').textContent,
    'PARTIALLY ASSESSED (PHYSICAL SCREENING ONLY)',
  );
  assert.match(
    elements.get('pelagos-assess-vessel-section').textContent,
    /Deadweight \(DWT\): UNVERIFIED \(UNVERIFIED\)/,
  );
  assert.match(
    elements.get('pelagos-assess-vessel-section').textContent,
    /Particulars Completeness: PARTIAL · Sources: ais_observations/,
  );
  assert.match(
    elements.get('pelagos-assess-vessel-section').textContent,
    /Missing \/ Unverified: dwt_tonnage, draft_meters, hold_cleanliness_status/,
  );
});

test('5. Cargo compatibility states and 6. Route/port incompatibility & canal policy details', () => {
  const { document, elements } = createMockDom();
  const controller = new PelagosAssessmentController({ document });

  const incompatibleResp = makeAssessResponse({
    overall_suitability: 'dimension_mismatch',
    candidate: makeCandidateBase({
      suitability_status: 'dimension_mismatch',
      cargo_compatibility: {
        status: 'incompatible',
        cargo_tonnes: 95000,
        commodity: 'Agricultural Bulk Grain',
        verified_dwt_tonnes: 58000,
        capacity_check_basis: 'verified_dwt_tonnage',
        reasons: ['Requested parcel 95,000 MT exceeds verified DWT 58,000 MT.'],
        missing_requirements: ['hold_cleanliness_status'],
      },
      route_compatibility: {
        status: 'incompatible',
        route_navigable: false,
        routing_status: 'blocked_draft_and_canal',
        routing_mode: 'marnet',
        route_distance_nm: 11200.4,
        canals_transited: [],
        origin_port: {
          port_id: 'USMSY',
          port_name: 'New Orleans',
          status: 'incompatible',
          documented_draft_limit_meters: 13.7,
          limit_source_note: 'USACE Southwest Pass 45ft limit',
          vessel_draft_meters: 15.2,
          access_limitations: 'Max air draft 41m',
          reasons: ['Vessel draft 15.2m exceeds 13.7m documented draft limit.'],
          missing_requirements: [],
        },
        destination_port: {
          port_id: 'NLRTM',
          port_name: 'Rotterdam',
          status: 'compatible',
          documented_draft_limit_meters: 16.5,
          limit_source_note: 'Rotterdam port handbook',
          vessel_draft_meters: 15.2,
          reasons: [],
          missing_requirements: [],
        },
        canal_compatibility_notes: [
          'Suez Canal avoided per canal_policy=avoid_suez.',
        ],
        reasons: [
          'Origin port draft limit exceeded; rerouted via Cape of Good Hope.',
        ],
        missing_requirements: ['under_keel_clearance_margin'],
      },
    }),
  });

  controller.renderResult(incompatibleResp, { canal_policy: 'avoid_suez' });

  assert.equal(
    elements.get('pelagos-assess-status-tag').textContent,
    'INCOMPATIBLE',
  );
  assert.match(
    elements.get('pelagos-assess-cargo-section').textContent,
    /Cargo Status: INCOMPATIBLE/,
  );
  assert.match(
    elements.get('pelagos-assess-cargo-section').textContent,
    /exceeds verified DWT 58,000 MT/,
  );
  assert.match(
    elements.get('pelagos-assess-cargo-section').textContent,
    /Missing Cargo Verification Items: hold_cleanliness_status/,
  );
  const routeText = elements.get('pelagos-assess-route-section').textContent;
  assert.match(routeText, /Canal Policy Applied: avoid_suez/);
  assert.match(
    routeText,
    /Origin Port \(New Orleans\): INCOMPATIBLE · Limit 13\.7m \(USACE Southwest Pass 45ft limit\)/,
  );
  assert.match(routeText, /Access note: Max air draft 41m/);
  assert.match(routeText, /Suez Canal avoided per canal_policy=avoid_suez/);
});

test('7. Historical AIS & stale observations, 8. Commercial availability UNCONFIRMED & active registry meaning, 9. Laycan UNCONFIRMED', () => {
  const { document, elements } = createMockDom();
  const controller = new PelagosAssessmentController({ document });

  controller.renderResult(makeAssessResponse(), { canal_policy: 'allow' });

  const aisCommText = elements.get(
    'pelagos-assess-ais-commercial-section',
  ).textContent;
  assert.match(aisCommText, /STALE \/ HISTORICAL · Mode: historical/);
  assert.match(
    aisCommText,
    /Distance Qualification: HISTORICAL_OBSERVATION_ONLY/,
  );
  assert.match(
    aisCommText,
    /Registry Sync Status: active — .*historical registry synchronization only — NOT that vessel tonnage is open/i,
  );
  assert.match(aisCommText, /Commercial Availability: UNCONFIRMED/);
  assert.match(aisCommText, /Laycan Feasibility: UNCONFIRMED/);

  // Never marks an assessed vessel as commercially open even when overall_suitability is potentially_suitable
  assert.match(
    elements.get('pelagos-assess-outcome').textContent,
    /commercial availability is UNCONFIRMED and laycan feasibility is UNCONFIRMED\. Physical compatibility does NOT establish open-tonnage availability or charter readiness/i,
  );
});

test('10. Insufficient-data response and 4 grouped missing-information sections', () => {
  const { document, elements } = createMockDom();
  const controller = new PelagosAssessmentController({ document });

  const insufficientResp = makeAssessResponse({
    overall_suitability: 'insufficient_data',
    candidate: makeCandidateBase({
      suitability_status: 'insufficient_data',
      suitability_reason:
        'Draft and verified DWT missing for port and cargo screening.',
      verified_cargo_capacity_dwt: null,
      cargo_capacity_status: 'UNVERIFIED',
      draft_meters: null,
      particulars_completeness: {
        completeness_status: 'insufficient_data',
        verified_particulars: [],
        missing_particulars: ['dwt_tonnage', 'draft_meters'],
        data_sources: ['registered_vessels'],
      },
      cargo_compatibility: {
        status: 'insufficient_data',
        cargo_tonnes: 52000,
        commodity: 'Wheat',
        verified_dwt_tonnes: null,
        reasons: ['Insufficient data: DWT unverified.'],
        missing_requirements: ['dwt_tonnage', 'hold_cleanliness_status'],
      },
      route_compatibility: {
        status: 'insufficient_data',
        route_navigable: true,
        reasons: ['Draft unknown for port channel screening.'],
        missing_requirements: ['draft_meters'],
        origin_port: {
          port_id: 'USMSY',
          status: 'insufficient_data',
          missing_requirements: ['vessel_draft_meters'],
        },
        destination_port: {
          port_id: 'NLRTM',
          status: 'insufficient_data',
          missing_requirements: ['berth_restriction_confirmation'],
        },
      },
    }),
  });

  const outcome = classifyAssessmentOutcome(insufficientResp);
  assert.equal(outcome.key, 'insufficient_data');
  assert.equal(outcome.label, 'INSUFFICIENT DATA');

  const grouped = buildGroupedMissingInfo(insufficientResp);
  assert.deepEqual(grouped.vesselParticulars.items, [
    'dwt_tonnage',
    'draft_meters',
  ]);
  assert.deepEqual(grouped.cargoCompatibility.items, [
    'dwt_tonnage',
    'hold_cleanliness_status',
  ]);
  assert.ok(grouped.routeConstraints.items.includes('draft_meters'));
  assert.ok(grouped.routeConstraints.items.includes('vessel_draft_meters'));
  assert.ok(
    grouped.commercialInfo.items.includes(
      'commercial_availability_unconfirmed',
    ),
  );
  assert.ok(
    grouped.commercialInfo.items.includes('laycan_feasibility_unconfirmed'),
  );

  controller.renderResult(insufficientResp, { canal_policy: 'allow' });
  const missingText = elements.get(
    'pelagos-assess-missing-section',
  ).textContent;
  assert.match(
    missingText,
    /1\. Vessel Particulars \(dwt_tonnage, draft_meters\)/,
  );
  assert.match(
    missingText,
    /2\. Cargo Compatibility \(dwt_tonnage, hold_cleanliness_status\)/,
  );
  assert.match(missingText, /3\. Route \/ Operational Constraints/);
  assert.match(missingText, /4\. Commercial Information/);
});

test('11. Unavailable vessel (vessel_not_found) or unavailable route', () => {
  const { document, elements } = createMockDom();
  const controller = new PelagosAssessmentController({ document });

  const notFoundResp = {
    status: 'vessel_not_found',
    overall_suitability: null,
    corridor: null,
    candidate: null,
    ais_observation_status: {},
    commercial_availability_status: {
      commercial_availability: 'UNCONFIRMED',
      laycan_feasibility: 'UNCONFIRMED',
    },
    disclaimers: ['No AIS or registered vessel match for MMSI 999999999.'],
    message: 'Vessel with MMSI 999999999 was not found.',
  };

  controller.renderResult(notFoundResp, { canal_policy: 'allow' });
  assert.equal(
    elements.get('pelagos-assess-status-tag').textContent,
    'UNAVAILABLE · VESSEL NOT FOUND',
  );
  assert.match(
    elements.get('pelagos-assess-vessel-section').textContent,
    /Vessel with MMSI 999999999 was not found/,
  );

  const unavailableRouteOutcome = classifyAssessmentOutcome({
    status: 'unavailable',
    message: 'MARNET graph unavailable between ports.',
  });
  assert.equal(unavailableRouteOutcome.key, 'unavailable');
  assert.equal(unavailableRouteOutcome.label, 'UNAVAILABLE');
});

test('12. API HTTP/network error state & retry action succeeds cleanly', async () => {
  const { document, elements } = createMockDom();
  let attempt = 0;

  const fetchMock = async (url) => {
    if (String(url).includes('/explorer/vessels')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          page: 1,
          total_pages: 1,
          total_count: 1,
          items: [{ mmsi: '212719000', vessel_name: 'MV AEGEAN TRADER' }],
        }),
      };
    }
    attempt += 1;
    if (attempt === 1) {
      return {
        ok: false,
        status: 502,
        json: async () => ({ detail: 'Upstream assessment failure' }),
      };
    }
    return {
      ok: true,
      status: 200,
      json: async () => makeAssessResponse(),
    };
  };

  const controller = new PelagosAssessmentController({
    document,
    fetchImpl: fetchMock,
  });
  await controller.init();

  elements.get('pelagos-assess-vessel-select').value = '212719000';
  elements.get('pelagos-origin-select').value = 'USMSY';
  elements.get('pelagos-dest-select').value = 'NLRTM';

  const firstOk = await controller.assess();
  assert.equal(firstOk, false);
  assert.equal(elements.get('pelagos-assess-error').style.display, 'block');
  assert.match(
    elements.get('pelagos-assess-error-text').textContent,
    /Upstream assessment failure/,
  );
  assert.equal(elements.get('pelagos-assess-status-tag').textContent, 'ERROR');

  // Click Retry
  const retryOk = await controller.retryLastAssessment();
  assert.equal(retryOk, true);
  assert.equal(elements.get('pelagos-assess-error').style.display, 'none');
  assert.equal(elements.get('pelagos-assess-result').style.display, 'block');
  assert.equal(
    elements.get('pelagos-assess-status-tag').textContent,
    'COMPATIBLE (PHYSICAL & ROUTE ONLY)',
  );
});

test('13. Prevents duplicate concurrent submissions while assessment request is in flight', async () => {
  const { document, elements } = createMockDom();
  let resolveAssessFetch;
  let assessFetchCalls = 0;

  const fetchMock = async (url) => {
    if (String(url).includes('/explorer/vessels')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          page: 1,
          total_pages: 1,
          total_count: 1,
          items: [{ mmsi: '212719000', vessel_name: 'MV AEGEAN TRADER' }],
        }),
      };
    }
    assessFetchCalls += 1;
    return new Promise((resolve) => {
      resolveAssessFetch = () =>
        resolve({
          ok: true,
          status: 200,
          json: async () => makeAssessResponse(),
        });
    });
  };

  const controller = new PelagosAssessmentController({
    document,
    fetchImpl: fetchMock,
  });
  await controller.init();

  elements.get('pelagos-assess-vessel-select').value = '212719000';
  elements.get('pelagos-origin-select').value = 'USMSY';
  elements.get('pelagos-dest-select').value = 'NLRTM';

  const firstPromise = controller.assess();
  assert.equal(controller.isAssessing, true);
  assert.equal(elements.get('pelagos-assess-submit-btn').disabled, true);

  // Second concurrent call while first is still in flight must be ignored
  const secondResult = await controller.assess();
  assert.equal(secondResult, false);
  assert.equal(assessFetchCalls, 1);

  resolveAssessFetch();
  const firstResult = await firstPromise;
  assert.equal(firstResult, true);
  assert.equal(controller.isAssessing, false);
  assert.equal(elements.get('pelagos-assess-submit-btn').disabled, false);
});

test('PelagosPanelController synchronizes ports, swapEndpoints, cargo tonnage, clearRoute, and destroy with PelagosAssessmentController', async () => {
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
    if (u.includes('/api/v1/platform-data/explorer/vessels')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          page: 1,
          total_pages: 1,
          total_count: 1,
          items: [
            {
              mmsi: '212719000',
              vessel_name: 'MV AEGEAN TRADER',
              vessel_type: 'Cargo',
            },
          ],
        }),
      };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  };

  try {
    const panel = new PelagosPanelController({ document });
    await panel.init();

    assert.equal(panel.assessment.registeredVessels.length, 1);

    elements.get('pelagos-origin-select').value = 'USMSY';
    elements.get('pelagos-dest-select').value = 'NLRTM';
    panel.swapEndpoints();

    assert.match(
      elements.get('pelagos-assess-voyage-summary').textContent,
      /Voyage: Rotterdam \(NLRTM\) → New Orleans \(USMSY\)/,
    );

    // Assessment cargo input updates shared panel cargo input
    elements.get('pelagos-assess-cargo-tonnes').value = '64000';
    elements
      .get('pelagos-assess-cargo-tonnes')
      .dispatchEvent({ type: 'input' });
    assert.equal(elements.get('pelagos-cargo-tonnes').value, '64000');

    // Clear resets assessment output cleanly
    panel.assessment.renderResult(makeAssessResponse(), {
      canal_policy: 'allow',
    });
    assert.equal(elements.get('pelagos-assess-result').style.display, 'block');
    panel.clearRoute();
    assert.equal(elements.get('pelagos-assess-result').style.display, 'none');
    assert.equal(
      elements.get('pelagos-assess-status-tag').textContent,
      'AWAITING INPUT',
    );

    panel.destroy();
  } finally {
    globalThis.fetch = origFetch;
  }
});
