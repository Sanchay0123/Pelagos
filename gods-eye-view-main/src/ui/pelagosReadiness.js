/**
 * @module ui/pelagosReadiness
 * @description Pelagos D2.2 Charter Readiness Audit controller and renderer.
 * Integrates GET/POST `/api/v1/charter/recommend` and transparently renders all
 * six charter-readiness gates, missing decision inputs, supporting analytics,
 * and withheld/issued recommendation explanations without manufacturing actions.
 */

import { recommendCharterReadiness } from './pelagosApi.js';

export const READINESS_GATE_ORDER = Object.freeze([
  'corridor_and_freight_market',
  'directional_forecast_edge',
  'operational_route_and_ports',
  'vessel_physical_particulars',
  'commercial_open_tonnage_and_laycan',
  'bunker_and_voyage_economics',
]);

export const READINESS_GATE_TITLES = Object.freeze({
  corridor_and_freight_market: 'Gate 1 — Freight Market Readiness',
  directional_forecast_edge: 'Gate 2 — Forecast Directional Edge',
  operational_route_and_ports: 'Gate 3 — Operational Route and Ports',
  vessel_physical_particulars: 'Gate 4 — Vessel Physical Particulars',
  commercial_open_tonnage_and_laycan:
    'Gate 5 — Commercial Open Tonnage and Laycan',
  bunker_and_voyage_economics: 'Gate 6 — Bunker and Voyage Economics',
});

const MONITORED_CORRIDOR_CODES = new Set(['PNW-JPN-BULK', 'USG-JPN-BULK']);

function formatNum(val, digits = 2) {
  if (val === null || val === undefined || !Number.isFinite(Number(val))) {
    return '—';
  }
  return Number(val).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatInt(val) {
  if (val === null || val === undefined || !Number.isFinite(Number(val))) {
    return '—';
  }
  return Math.round(Number(val)).toLocaleString('en-US');
}

/**
 * Map actual backend `DataGateStatus.status` strings to explicit semantic categories
 * without collapsing `FAIL`, `INSUFFICIENT_DATA`, `UNVERIFIED`, or `UNAVAILABLE`.
 */
export function mapGateStatusToSemantic(rawStatus) {
  const normalized = String(rawStatus || 'unavailable')
    .trim()
    .toLowerCase();

  if (normalized === 'available' || normalized === 'pass') {
    return {
      rawStatus: normalized,
      semantic: 'PASS',
      cssKey: 'pass',
      tagClass: 'pelagos-tag tag-verified',
      badgeText: `PASS (${normalized})`,
      summaryKey: 'passed',
    };
  }

  if (
    normalized === 'fail' ||
    normalized === 'failed' ||
    normalized === 'incompatible'
  ) {
    return {
      rawStatus: normalized,
      semantic: 'FAIL',
      cssKey: 'fail',
      tagClass: 'pelagos-tag tag-offline',
      badgeText: `FAIL (${normalized})`,
      summaryKey: 'failed',
    };
  }

  if (normalized === 'insufficient_data') {
    return {
      rawStatus: normalized,
      semantic: 'INSUFFICIENT_DATA',
      cssKey: 'insufficient_data',
      tagClass: 'pelagos-tag tag-warning',
      badgeText: 'INSUFFICIENT_DATA',
      summaryKey: 'insufficientData',
    };
  }

  if (normalized === 'partial' || normalized === 'unverified') {
    return {
      rawStatus: normalized,
      semantic: 'UNVERIFIED',
      cssKey: 'unverified',
      tagClass: 'pelagos-tag tag-warning',
      badgeText: `UNVERIFIED (${normalized})`,
      summaryKey: 'unverified',
    };
  }

  return {
    rawStatus: normalized || 'unavailable',
    semantic: 'UNAVAILABLE',
    cssKey: 'unavailable',
    tagClass: 'pelagos-tag tag-unmonitored',
    badgeText: `UNAVAILABLE (${normalized || 'unavailable'})`,
    summaryKey: 'unavailable',
  };
}

/**
 * Classify overall charter readiness outcome strictly from backend `status`,
 * `recommendation_issued`, `recommended_action`, and `recommended_contract_structure`.
 * Never manufactures FIX NOW, WAIT, HEDGE, SPOT, COA, or TC.
 */
export function classifyReadinessOutcome(data) {
  if (!data || typeof data !== 'object') {
    return {
      key: 'unavailable',
      headline: 'RECOMMENDATION WITHHELD — UNAVAILABLE',
      tagClass: 'pelagos-tag tag-unmonitored',
      tagText: 'UNAVAILABLE',
      recommendationIssued: false,
      recommendedAction: null,
      recommendedContractStructure: null,
      reason: 'Readiness audit response is unavailable.',
    };
  }

  const rawStatus = String(data.status || 'unavailable')
    .trim()
    .toLowerCase();
  const issued =
    data.recommendation_issued === true &&
    typeof data.recommended_action === 'string' &&
    data.recommended_action.trim().length > 0;

  if (rawStatus === 'recommended' && issued) {
    return {
      key: 'recommended',
      headline: 'RECOMMENDATION ISSUED',
      tagClass: 'pelagos-tag tag-verified',
      tagText: 'RECOMMENDED',
      recommendationIssued: true,
      recommendedAction: data.recommended_action.trim(),
      recommendedContractStructure: data.recommended_contract_structure || null,
      reason:
        data.message ||
        'All verified commercial and operational readiness gates passed.',
    };
  }

  if (rawStatus === 'insufficient_data') {
    return {
      key: 'insufficient_data',
      headline: 'RECOMMENDATION WITHHELD — INSUFFICIENT DATA',
      tagClass: 'pelagos-tag tag-warning',
      tagText: 'WITHHELD · INSUFFICIENT DATA',
      recommendationIssued: false,
      recommendedAction: null,
      recommendedContractStructure: null,
      reason:
        data.message ||
        'Charter recommendation withheld because mandatory verification gates are incomplete.',
    };
  }

  if (rawStatus === 'unavailable') {
    return {
      key: 'unavailable',
      headline: 'RECOMMENDATION WITHHELD — UNAVAILABLE',
      tagClass: 'pelagos-tag tag-unmonitored',
      tagText: 'UNAVAILABLE',
      recommendationIssued: false,
      recommendedAction: null,
      recommendedContractStructure: null,
      reason:
        data.message ||
        'Charter recommendation cannot be performed for this route or corridor.',
    };
  }

  if (rawStatus === 'invalid_input') {
    return {
      key: 'invalid_input',
      headline: 'INVALID INPUT — ASSESSMENT HALTED',
      tagClass: 'pelagos-tag tag-offline',
      tagText: 'INVALID INPUT',
      recommendationIssued: false,
      recommendedAction: null,
      recommendedContractStructure: null,
      reason:
        data.message || 'Submitted charter readiness request was rejected.',
    };
  }

  return {
    key: 'withheld',
    headline: 'RECOMMENDATION WITHHELD',
    tagClass: 'pelagos-tag tag-warning',
    tagText: 'WITHHELD',
    recommendationIssued: false,
    recommendedAction: null,
    recommendedContractStructure: null,
    reason:
      data.message ||
      'Charter recommendation withheld because backend conditions for issuing an action were not met.',
  };
}

/**
 * Compute exact gate counts solely from the returned `data_readiness_gates` dictionary.
 */
export function computeGateSummary(gatesMap = {}) {
  const counts = {
    returnedCount: 0,
    passed: 0,
    failed: 0,
    insufficientData: 0,
    unverified: 0,
    unavailable: 0,
  };

  if (!gatesMap || typeof gatesMap !== 'object') {
    return counts;
  }

  const orderedSet = new Set(READINESS_GATE_ORDER);
  const allKeys = [
    ...READINESS_GATE_ORDER,
    ...Object.keys(gatesMap).filter((k) => !orderedSet.has(k)),
  ];

  for (const key of allKeys) {
    const rawGate = gatesMap[key];
    if (!rawGate || typeof rawGate !== 'object') continue;
    counts.returnedCount += 1;
    const mapped = mapGateStatusToSemantic(rawGate.status);
    counts[mapped.summaryKey] += 1;
  }

  return counts;
}

/**
 * Build structured, non-fabricated evidence, provenance, missing inputs, and warnings
 * for a single gate from `CharterRecommendationResponse`.
 */
export function buildGatePresentation(
  gateKey,
  rawGate,
  data = {},
  corridorFreightContext = null,
) {
  const title = READINESS_GATE_TITLES[gateKey] || gateKey;
  const analytics = data?.verified_supporting_analytics || {};
  const corridor = data?.corridor || null;
  const candidates = Array.isArray(data?.ranked_vessel_candidates)
    ? data.ranked_vessel_candidates
    : [];

  const presentInResponse = Boolean(rawGate && typeof rawGate === 'object');
  const semanticInfo = mapGateStatusToSemantic(
    presentInResponse ? rawGate.status : 'unavailable',
  );
  const missingInputs = Array.isArray(rawGate?.missing_items)
    ? [...rawGate.missing_items]
    : [];

  const evidence = [];
  const warnings = [];
  let shortExplanation = presentInResponse
    ? rawGate.details || 'No backend explanation returned.'
    : 'Not evaluated by backend because an upstream prerequisite gate (corridor market or navigable route) was unavailable.';

  if (gateKey === 'corridor_and_freight_market') {
    if (corridor) {
      evidence.push(
        `Corridor Status: MONITORED (${corridor.route_code} — ${corridor.route_name || 'Named Corridor'})`,
      );
      evidence.push(
        `Scope: ${corridor.commodity || data?.commodity || '—'} · Target Class: ${corridor.vessel_class || '—'}`,
      );
    } else {
      evidence.push(
        'Corridor Status: UNMONITORED (outside Pelagos monitored bulk grain benchmark corridors)',
      );
    }

    const rate = analytics.applicable_rate;
    if (rate && rate.rate_value != null) {
      evidence.push(
        `Latest Rate: $${formatNum(rate.rate_value, 2)} ${rate.unit || 'USD/MT'} (${rate.rate_type || 'observed'}, ${rate.observation_period || 'periodic'})`,
      );
      evidence.push(
        `Observation Date: ${rate.observation_date || '—'} · Source: ${rate.source_name || '—'}${rate.source_dataset_id ? ` [${rate.source_dataset_id}]` : ''}${rate.license_type ? ` (${rate.license_type})` : ''}`,
      );
      if (analytics.gross_freight_cost_usd != null) {
        evidence.push(
          `Gross Ocean Freight Only: $${formatNum(analytics.gross_freight_cost_usd, 2)} USD (${formatInt(data?.cargo_tonnes)} MT × $${formatNum(rate.rate_value, 2)}/MT)`,
        );
      }
    } else {
      evidence.push(
        'Latest Freight Rate: UNAVAILABLE (no verified corridor freight observation).',
      );
    }

    warnings.push(
      'Market-data boundary: Pelagos benchmark coverage is limited to monitored bulk grain corridors; trade unit values are NEVER substituted for freight rates.',
    );
  } else if (gateKey === 'directional_forecast_edge') {
    const fcMap = analytics.persistence_forecasts || {};
    const horizons = Object.keys(fcMap);
    const firstFc = horizons.length > 0 ? fcMap[horizons[0]] : null;
    const modelName = firstFc?.model_type || 'persistence';

    evidence.push(`Active Production Model: ${modelName}`);
    if (horizons.length > 0) {
      for (const h of horizons) {
        const entry = fcMap[h];
        if (!entry) continue;
        evidence.push(
          `${h}: ${entry.predicted_value != null ? `$${formatNum(entry.predicted_value, 2)} ${entry.unit || 'USD/MT'}` : 'UNAVAILABLE'} (Origin: ${entry.origin_date || '—'} → Target: ${entry.target_date || '—'})`,
        );
      }
    } else {
      evidence.push(
        'Forecast Horizons: No production persistence horizon series returned for this assessment.',
      );
    }

    evidence.push(
      `Directional Timing Edge: ${semanticInfo.semantic === 'PASS' ? 'VERIFIED NON-FLAT SIGNAL' : 'NONE (flat persistence produces zero directional timing advantage)'}`,
    );
    warnings.push(
      'Persistence remains the active production baseline. C3.2/C3.3 experimental ML candidates are NOT promoted to production, and flat persistence values are NOT Fix Now / Wait / Hedge signals.',
    );
  } else if (gateKey === 'operational_route_and_ports') {
    const route = analytics.operational_route;
    if (route) {
      evidence.push(
        `Route Connectivity: ${route.route_navigable === true ? 'NAVIGABLE' : route.route_navigable === false ? 'DISCONNECTED / UNNAVIGABLE' : 'UNKNOWN'} (Status: ${route.routing_status || '—'} · Mode: ${route.routing_mode || 'MARNET generalized'})`,
      );
      if (route.route_distance_nm != null) {
        evidence.push(
          `Estimated Sea Distance: ${formatNum(route.route_distance_nm, 1)} NM`,
        );
      }
      const canals = Array.isArray(route.canals_transited)
        ? route.canals_transited
        : [];
      evidence.push(
        `Canals Transited: ${canals.length > 0 ? canals.join(', ') : 'None flagged on computed path'}`,
      );
      if (route.error_message) {
        evidence.push(`Routing Diagnostic: ${route.error_message}`);
      }
    } else {
      evidence.push(
        'Operational Route Connectivity: Not available for unmonitored or disconnected request.',
      );
    }

    // Surface documented port limits & provenance if attached on returned candidate route_compatibility
    const firstRc = candidates.find(
      (c) => c?.route_compatibility,
    )?.route_compatibility;
    if (firstRc) {
      const addPortNote = (label, pe) => {
        if (!pe) return;
        const limitText =
          pe.documented_draft_limit_meters != null
            ? `${formatNum(pe.documented_draft_limit_meters, 2)}m draft limit (${pe.limit_source_note || 'documented'})`
            : 'Documented berth draft limit unverified';
        evidence.push(
          `${label} (${pe.port_name || pe.port_id || '—'}): ${String(pe.status || 'unknown').toUpperCase()} — ${limitText}`,
        );
      };
      addPortNote('Origin Port', firstRc.origin_port);
      addPortNote('Destination Port', firstRc.destination_port);
      if (
        Array.isArray(firstRc.canal_compatibility_notes) &&
        firstRc.canal_compatibility_notes.length > 0
      ) {
        evidence.push(
          `Canal Notes: ${firstRc.canal_compatibility_notes.join(' · ')}`,
        );
      }
    }

    warnings.push(
      'Routing & Port Boundary: MARNET routes use a generalized ~50 km sea-lane topology — NOT certified ECDIS navigation, passage planning, berth clearance, or live canal authority advisories.',
    );
  } else if (gateKey === 'vessel_physical_particulars') {
    const evaluated = analytics.vessels_evaluated_count;
    const screened = analytics.dimensionally_screened_candidates_count;
    const verifiedDwtCount = candidates.filter(
      (c) =>
        c?.verified_cargo_capacity_dwt !== null &&
        c?.verified_cargo_capacity_dwt !== undefined &&
        c?.cargo_capacity_status === 'VERIFIED',
    ).length;

    if (evaluated != null) {
      evidence.push(
        `Fleet Screening Scope: ${formatInt(evaluated)} record(s) evaluated · ${formatInt(screened ?? 0)} dimensionally screened · ${formatInt(verifiedDwtCount)} candidate(s) with verified DWT in returned sample`,
      );
    }

    if (candidates.length > 0) {
      const topList = candidates.slice(0, 5);
      for (const c of topList) {
        const dwtLabel =
          c.verified_cargo_capacity_dwt != null &&
          c.cargo_capacity_status === 'VERIFIED'
            ? `${formatInt(c.verified_cargo_capacity_dwt)} MT (VERIFIED)`
            : 'DWT UNVERIFIED';
        const draftLabel =
          c.draft_meters != null
            ? `${formatNum(c.draft_meters, 1)}m draft`
            : 'Draft MISSING';
        const cargoLabel = c.cargo_compatibility?.status
          ? String(c.cargo_compatibility.status).toUpperCase()
          : 'UNVERIFIED';
        const routeLabel = c.route_compatibility?.status
          ? String(c.route_compatibility.status).toUpperCase()
          : 'UNVERIFIED';
        const missText =
          Array.isArray(c.missing_particulars) && c.missing_particulars.length
            ? ` · Missing: ${c.missing_particulars.join(', ')}`
            : '';
        evidence.push(
          `${c.vessel_name || 'Unnamed'} (MMSI ${c.mmsi || '—'}${c.imo ? ` · IMO ${c.imo}` : ''}) — ${dwtLabel} · ${draftLabel} · Cargo: ${cargoLabel} · Route: ${routeLabel}${missText}`,
        );
      }
      if (candidates.length > 5) {
        evidence.push(
          `Showing 5 of ${candidates.length} returned vessel candidate(s).`,
        );
      }
    } else {
      evidence.push(
        'Returned Vessel Candidates: 0 candidates returned for physical inspection.',
      );
    }

    warnings.push(
      'Particulars Boundary: Unknown deadweight tonnage (DWT), draft, hold cleanliness, or grain cubic capacity are NEVER assumed or converted to verified capacity.',
    );
  } else if (gateKey === 'commercial_open_tonnage_and_laycan') {
    const firstCand = candidates[0] || null;
    const commAvail = firstCand?.commercial_availability || 'UNCONFIRMED';
    const laycanFeas = firstCand?.laycan_feasibility || 'UNCONFIRMED';
    const dataMode =
      analytics.data_mode || firstCand?.data_mode || 'historical';

    evidence.push(
      `Commercial Availability: ${commAvail} · Laycan Feasibility: ${laycanFeas}`,
    );
    evidence.push(
      `Telemetry Mode: ${dataMode}${firstCand?.observation_time ? ` (Sample fix: ${firstCand.observation_time} · ${firstCand.is_stale ? 'HISTORICAL / STALE' : 'RECENT'})` : ''}`,
    );
    if (firstCand?.registry_commercial_status) {
      evidence.push(
        `Registry Sync Flag: "${firstCand.registry_commercial_status}" — indicates database AIS synchronization ONLY, NOT commercially unfixed open tonnage.`,
      );
    }
    if (data?.ais_vs_commercial_summary) {
      evidence.push(data.ais_vs_commercial_summary);
    }
    if (firstCand?.commercial_availability_note) {
      evidence.push(firstCand.commercial_availability_note);
    }

    warnings.push(
      'Commercial Boundary: Historical AIS positions and vessel registry sync flags NEVER verify broker fixture status, vessel open tonnage, or laycan commitments.',
    );
  } else if (gateKey === 'bunker_and_voyage_economics') {
    const bStatus = analytics.bunker_fuel_status || 'unavailable';
    const bHubs = analytics.bunker_hubs_quoted || '0/0';
    evidence.push(
      `Corridor Bunker Status: ${String(bStatus).toUpperCase()} (${bHubs} hubs quoted)`,
    );

    const bRef = corridorFreightContext?.bunker_fuel_references;
    if (bRef && typeof bRef === 'object') {
      if (bRef.provider) {
        evidence.push(`Reference Provider: ${bRef.provider}`);
      }
      if (Array.isArray(bRef.bunkering_hubs_in_scope)) {
        for (const hub of bRef.bunkering_hubs_in_scope) {
          const quotesText =
            Array.isArray(hub.quotes) && hub.quotes.length > 0
              ? hub.quotes
                  .map(
                    (q) =>
                      `${q.fuel_grade || 'Fuel'} $${formatNum(q.price_usd_per_mt, 2)} ${q.unit || 'USD/MT'} (${q.pricing_type || 'indicative'}, ${q.freshness || 'stale'})`,
                  )
                  .join('; ')
              : 'No live/verified hub quote';
          evidence.push(
            `Hub: ${hub.name || hub.port_id || '—'} (${hub.port_id || '—'}, ${hub.role || 'hub'}) — ${String(hub.status || 'unavailable').toUpperCase()} · ${quotesText}`,
          );
        }
      }
      if (
        Array.isArray(bRef.regional_benchmarks) &&
        bRef.regional_benchmarks.length > 0
      ) {
        for (const bm of bRef.regional_benchmarks) {
          const portStr = `${bm.port_name || '—'}${bm.port_code ? ` (${bm.port_code})` : ''}`;
          evidence.push(
            `Regional Benchmark ONLY (NOT corridor hub price): ${portStr} · ${bm.fuel_grade || '—'} · $${formatNum(bm.price_usd_per_mt, 2)} ${bm.unit || 'USD/MT'} (${bm.observation_time || '—'})`,
          );
        }
      }
    } else {
      evidence.push(
        'Bunker Port & Fuel-Grade Quotes: None attached or available for corridor hubs.',
      );
    }

    const comparisons = Array.isArray(data?.contract_comparisons)
      ? data.contract_comparisons
      : [];
    if (comparisons.length > 0) {
      for (const cmp of comparisons) {
        evidence.push(
          `Verified Comparison (${cmp.contract_structure}): $${formatNum(cmp.total_cost_usd, 2)} total · $${formatNum(cmp.unit_cost_usd_per_mt, 2)}/MT (${cmp.voyages_evaluated || 1} voyage(s))`,
        );
      }
    } else {
      evidence.push(
        'Voyage Economics Comparisons (Spot vs COA vs TC): Unavailable without verified fuel-consumption curves, port disbursements, canal tolls, and bunker quotes.',
      );
    }

    warnings.push(
      'Economics Boundary: Regional bunker benchmarks are distinct from corridor hub quotes. Pelagos NEVER fabricates voyage fuel expenditure, and gross ocean freight is NOT total voyage cost.',
    );
  }

  return {
    key: gateKey,
    title,
    presentInResponse,
    rawStatus: semanticInfo.rawStatus,
    semantic: semanticInfo.semantic,
    cssKey: semanticInfo.cssKey,
    tagClass: semanticInfo.tagClass,
    badgeText: semanticInfo.badgeText,
    shortExplanation,
    evidence,
    missingInputs,
    warnings,
  };
}

export class PelagosReadinessController {
  /**
   * @param {object} [options]
   * @param {Document} [options.document]
   * @param {Function} [options.fetchImpl]
   * @param {Function} [options.getPorts]
   * @param {Function} [options.getCorridorFreightContext]
   * @param {Function} [options.onCargoChange]
   */
  constructor({
    document = globalThis.document,
    fetchImpl = globalThis.fetch,
    getPorts = null,
    getCorridorFreightContext = null,
    onCargoChange = null,
  } = {}) {
    this.doc = document;
    this.fetchImpl = fetchImpl;
    this.getPorts = getPorts;
    this.getCorridorFreightContext = getCorridorFreightContext;
    this.onCargoChange = onCargoChange;

    this.isAuditing = false;
    this.currentAudit = null;
    this.lastSubmittedPayload = null;
    this._abortController = null;
    this._boundListeners = [];
    this._gateExpandedState = {};
    for (const k of READINESS_GATE_ORDER) {
      this._gateExpandedState[k] = true;
    }

    this._resolveElements();
  }

  _resolveElements() {
    const d = this.doc;
    if (!d) {
      this.elements = {};
      return;
    }

    this.elements = {
      card: d.getElementById('pelagos-readiness-card'),
      statusTag: d.getElementById('pelagos-readiness-status-tag'),
      routeCodeSelect: d.getElementById('pelagos-readiness-route-code'),
      voyageSummary: d.getElementById('pelagos-readiness-voyage-summary'),
      cargoInput: d.getElementById('pelagos-readiness-cargo-tonnes'),
      commodityInput: d.getElementById('pelagos-readiness-commodity'),
      laycanStartInput: d.getElementById('pelagos-readiness-laycan-start'),
      laycanEndInput: d.getElementById('pelagos-readiness-laycan-end'),
      maxVesselsInput: d.getElementById('pelagos-readiness-max-vessels'),
      validationMsg: d.getElementById('pelagos-readiness-validation-msg'),
      submitBtn: d.getElementById('pelagos-readiness-submit-btn'),
      errorBox: d.getElementById('pelagos-readiness-error'),
      errorText: d.getElementById('pelagos-readiness-error-text'),
      retryBtn: d.getElementById('pelagos-readiness-retry-btn'),
      emptyState: d.getElementById('pelagos-readiness-empty'),
      resultBox: d.getElementById('pelagos-readiness-result'),
      outcomeBanner: d.getElementById('pelagos-readiness-outcome'),
      summarySection: d.getElementById('pelagos-readiness-summary'),
      contractSection: d.getElementById('pelagos-readiness-contracts'),
      toggleAllBtn: d.getElementById('pelagos-readiness-toggle-all-btn'),
      gatesContainer: d.getElementById('pelagos-readiness-gates'),
      missingSection: d.getElementById('pelagos-readiness-missing-inputs'),
      limitationsSection: d.getElementById('pelagos-readiness-limitations'),
      disclaimersList: d.getElementById('pelagos-readiness-disclaimers'),

      // Shared panel inputs
      originSelect: d.getElementById('pelagos-origin-select'),
      destSelect: d.getElementById('pelagos-dest-select'),
      canalPolicySelect: d.getElementById('pelagos-canal-policy'),
    };
  }

  _bind(el, event, handler) {
    if (!el || !el.addEventListener) return;
    el.addEventListener(event, handler);
    this._boundListeners.push({ el, event, handler });
  }

  init() {
    this._resolveElements();
    const el = this.elements;
    if (!el.card) return;

    this._bind(el.routeCodeSelect, 'change', () => {
      this.clearValidationMessage();
      this.syncVoyageSummary();
    });
    this._bind(el.originSelect, 'change', () => this.syncVoyageSummary());
    this._bind(el.destSelect, 'change', () => this.syncVoyageSummary());
    this._bind(el.canalPolicySelect, 'change', () => this.syncVoyageSummary());

    this._bind(el.cargoInput, 'input', () => {
      this.clearValidationMessage();
      const val = Number(el.cargoInput?.value);
      if (
        Number.isFinite(val) &&
        val > 0 &&
        typeof this.onCargoChange === 'function'
      ) {
        this.onCargoChange(val);
      }
    });

    this._bind(el.commodityInput, 'input', () => this.clearValidationMessage());
    this._bind(el.laycanStartInput, 'change', () =>
      this.clearValidationMessage(),
    );
    this._bind(el.laycanEndInput, 'change', () =>
      this.clearValidationMessage(),
    );
    this._bind(el.maxVesselsInput, 'input', () =>
      this.clearValidationMessage(),
    );

    this._bind(el.submitBtn, 'click', () => {
      void this.auditReadiness();
    });

    this._bind(el.retryBtn, 'click', () => {
      void this.retryLastAudit();
    });

    this._bind(el.toggleAllBtn, 'click', () => {
      const anyCollapsed = READINESS_GATE_ORDER.some(
        (k) => !this._gateExpandedState[k],
      );
      this.setAllGatesExpanded(anyCollapsed);
    });

    this.syncVoyageSummary();
  }

  syncVoyageSummary() {
    const el = this.elements;
    if (!el.voyageSummary) return;

    const routeCode = String(el.routeCodeSelect?.value || '').trim();
    const originId = String(el.originSelect?.value || '').trim();
    const destId = String(el.destSelect?.value || '').trim();
    const policy = String(el.canalPolicySelect?.value || 'allow').trim();

    if (routeCode) {
      el.voyageSummary.textContent = `Audit Scope: Corridor ${routeCode} · Canal Policy: ${policy} · Evaluates 6 corridor/fleet gates (no single vessel MMSI param)`;
      return;
    }

    if (!originId || !destId) {
      el.voyageSummary.textContent =
        'Audit Scope: Select Origin and Destination ports above (or choose a monitored corridor code).';
      return;
    }

    if (originId === destId) {
      el.voyageSummary.textContent = `Audit Scope: Origin and Destination cannot both be ${originId}.`;
      return;
    }

    const ports = typeof this.getPorts === 'function' ? this.getPorts() : [];
    const origPort = Array.isArray(ports)
      ? ports.find((p) => p.port_id === originId)
      : null;
    const destPort = Array.isArray(ports)
      ? ports.find((p) => p.port_id === destId)
      : null;

    const origLabel = origPort
      ? `${origPort.name} (${origPort.port_id})`
      : originId;
    const destLabel = destPort
      ? `${destPort.name} (${destPort.port_id})`
      : destId;
    el.voyageSummary.textContent = `Audit Scope: ${origLabel} → ${destLabel} · Canal Policy: ${policy}`;
  }

  /**
   * Build and validate payload matching `CharterRecommendRequest` schema strictly.
   * Note: `/api/v1/charter/recommend` does NOT take single vessel MMSI.
   */
  buildReadinessPayload() {
    const el = this.elements;
    const routeCode = String(el.routeCodeSelect?.value || '').trim();
    const origin = String(el.originSelect?.value || '').trim();
    const destination = String(el.destSelect?.value || '').trim();
    const canalPolicy = String(el.canalPolicySelect?.value || 'allow').trim();

    const cargoRaw = String(el.cargoInput?.value ?? '').trim();
    const cargoTonnes = Number(cargoRaw);
    const commodity = String(el.commodityInput?.value ?? '').trim();
    const laycanStart = String(el.laycanStartInput?.value ?? '').trim();
    const laycanEnd = String(el.laycanEndInput?.value ?? '').trim();
    const maxVesselsRaw = String(el.maxVesselsInput?.value ?? '20').trim();
    const maxVessels = Number(maxVesselsRaw);

    const errors = [];
    if (!routeCode) {
      if (!origin || !destination) {
        errors.push(
          'select both origin and destination ports (or choose a monitored corridor code)',
        );
      } else if (origin === destination) {
        errors.push('ensure origin and destination ports are different');
      }
    }

    if (!cargoRaw || !Number.isFinite(cargoTonnes) || cargoTonnes <= 0) {
      errors.push('enter a positive cargo tonnage (> 0 MT)');
    }
    if (!commodity) {
      errors.push('specify a commodity type');
    }
    if (!Number.isInteger(maxVessels) || maxVessels < 1 || maxVessels > 200) {
      errors.push(
        'enter a valid max vessel candidates count between 1 and 200',
      );
    }
    if (laycanStart && laycanEnd && laycanEnd < laycanStart) {
      errors.push(
        'ensure laycan cancelling date does not precede laycan start date',
      );
    }

    if (errors.length > 0) {
      return {
        valid: false,
        error: `Please ${errors.join('; ')}.`,
      };
    }

    const payload = {
      cargo_tonnes: cargoTonnes,
      commodity,
      canal_policy: canalPolicy || 'allow',
      max_vessels: maxVessels,
    };

    if (routeCode) {
      payload.route_code = routeCode;
    } else {
      payload.origin = origin;
      payload.destination = destination;
    }

    if (laycanStart) payload.laycan_start = laycanStart;
    if (laycanEnd) payload.laycan_end = laycanEnd;

    return { valid: true, payload };
  }

  showValidationMessage(msg) {
    if (this.elements.validationMsg) {
      this.elements.validationMsg.textContent = msg;
      this.elements.validationMsg.style.display = 'block';
    }
  }

  clearValidationMessage() {
    if (this.elements.validationMsg) {
      this.elements.validationMsg.textContent = '';
      this.elements.validationMsg.style.display = 'none';
    }
  }

  setAuditingState(loading) {
    this.isAuditing = loading;
    const el = this.elements;
    if (el.submitBtn) {
      el.submitBtn.disabled = loading;
      el.submitBtn.classList?.toggle?.('is-loading', loading);
      const textSpan = el.submitBtn.querySelector?.('.btn-text');
      if (textSpan) {
        textSpan.textContent = loading
          ? 'AUDITING READINESS…'
          : 'RUN CHARTER READINESS AUDIT';
      }
    }
    if (loading && el.statusTag) {
      el.statusTag.className = 'pelagos-tag tag-loading';
      el.statusTag.textContent = 'AUDITING…';
    }
  }

  async auditReadiness({ method = 'POST' } = {}) {
    if (this.isAuditing) return false;

    this.clearValidationMessage();
    const built = this.buildReadinessPayload();
    if (!built.valid) {
      this.showValidationMessage(built.error);
      return false;
    }

    this.lastSubmittedPayload = built.payload;
    return this._executeAudit(built.payload, { method });
  }

  async retryLastAudit({ method = 'POST' } = {}) {
    if (this.isAuditing) return false;
    if (!this.lastSubmittedPayload) {
      return this.auditReadiness({ method });
    }
    return this._executeAudit(this.lastSubmittedPayload, { method });
  }

  async _executeAudit(payload, { method = 'POST' } = {}) {
    if (this.isAuditing) return false;

    this.setAuditingState(true);
    this._abortController?.abort();
    const controller = new AbortController();
    this._abortController = controller;

    if (this.elements.errorBox) this.elements.errorBox.style.display = 'none';

    try {
      const res = await recommendCharterReadiness(payload, {
        method,
        signal: controller.signal,
        fetchImpl: this.fetchImpl,
      });

      if (res.aborted) return false;

      if (!res.ok) {
        this.renderError(
          res.error || 'Charter readiness audit request failed.',
        );
        return false;
      }

      this.currentAudit = res.data;
      this.renderResult(res.data, payload);
      return true;
    } finally {
      if (this._abortController === controller) {
        this.setAuditingState(false);
      }
    }
  }

  renderError(message) {
    const el = this.elements;
    if (el.statusTag) {
      el.statusTag.className = 'pelagos-tag tag-offline';
      el.statusTag.textContent = 'ERROR';
    }
    if (el.resultBox) el.resultBox.style.display = 'none';
    if (el.emptyState) el.emptyState.style.display = 'none';
    if (el.errorBox) el.errorBox.style.display = 'block';
    if (el.errorText) {
      el.errorText.textContent =
        message || 'Failed to execute charter readiness audit.';
    }
  }

  setGateExpanded(gateKey, expanded) {
    this._gateExpandedState[gateKey] = Boolean(expanded);
    const gatesContainer = this.elements.gatesContainer;
    if (!gatesContainer) return;

    const cards = Array.isArray(gatesContainer.children)
      ? gatesContainer.children
      : [];
    for (const card of cards) {
      if (card?.dataset?.gateKey !== gateKey) continue;
      const body = card.children?.find?.(
        (c) =>
          typeof c.className === 'string' &&
          c.className.includes('readiness-gate-body'),
      );
      const toggle = card._toggleBtn;
      if (body) body.style.display = expanded ? 'block' : 'none';
      if (toggle) {
        toggle.setAttribute?.('aria-expanded', expanded ? 'true' : 'false');
        toggle.textContent = expanded ? 'Hide Details ▴' : 'Show Details ▾';
      }
    }
  }

  setAllGatesExpanded(expanded) {
    for (const key of READINESS_GATE_ORDER) {
      this.setGateExpanded(key, expanded);
    }
    if (this.elements.toggleAllBtn) {
      this.elements.toggleAllBtn.textContent = expanded
        ? 'Collapse All Gates'
        : 'Expand All Gates';
    }
  }

  _appendKvRow(container, label, value, valueClass = '') {
    const row = this.doc.createElement('div');
    row.className = 'readiness-kv-row';

    const k = this.doc.createElement('span');
    k.className = 'readiness-k';
    k.textContent = `${label}:`;
    row.appendChild(k);

    const v = this.doc.createElement('span');
    v.className = valueClass ? `readiness-v ${valueClass}` : 'readiness-v';
    v.textContent = value;
    row.appendChild(v);

    container.appendChild(row);
  }

  _getMatchingCorridorContext(data) {
    if (typeof this.getCorridorFreightContext !== 'function') return null;
    const ctx = this.getCorridorFreightContext();
    if (!ctx || typeof ctx !== 'object') return null;
    const ctxCode = ctx.corridor?.route_code || ctx.route_code;
    const respCode = data?.corridor?.route_code;
    if (respCode && ctxCode && respCode !== ctxCode) return null;
    return ctx;
  }

  _collectRelevantLimitations(data, gatePresentations) {
    const notes = [];
    const gatesMap = data?.data_readiness_gates || {};
    const analytics = data?.verified_supporting_analytics || {};

    if (
      !gatesMap.directional_forecast_edge ||
      gatesMap.directional_forecast_edge.status !== 'available'
    ) {
      notes.push(
        'Production baseline is Persistence (no calibrated directional timing edge; C3.2/C3.3 experimental candidates are not promoted).',
      );
    }
    if (
      !gatesMap.commercial_open_tonnage_and_laycan ||
      gatesMap.commercial_open_tonnage_and_laycan.status !== 'available'
    ) {
      notes.push(
        'Commercial open tonnage, broker fixture lists, and laycan arrival windows remain UNCONFIRMED (historical AIS & registry sync flags do not represent commercial openness).',
      );
    }
    if (
      !gatesMap.vessel_physical_particulars ||
      gatesMap.vessel_physical_particulars.status !== 'available'
    ) {
      notes.push(
        'Vessel DWT, hold cleanliness, cubic grain capacity, and some cargo-vessel drafts are missing or unverified.',
      );
    }
    if (
      !gatesMap.bunker_and_voyage_economics ||
      gatesMap.bunker_and_voyage_economics.status !== 'available'
    ) {
      notes.push(
        `Corridor bunker quotes (${analytics.bunker_fuel_status || 'unavailable'}), fuel-consumption curves, port disbursements, and canal tolls are missing.`,
      );
    }
    if (
      !gatesMap.operational_route_and_ports ||
      gatesMap.operational_route_and_ports.status !== 'available'
    ) {
      notes.push(
        'Port berth LOA/beam/draft restrictions may be partial; live canal advisories are unintegrated; MARNET 50 km routes are generalized approximations, not certified navigation.',
      );
    } else {
      notes.push(
        'MARNET routing uses a generalized ~50 km topology and is not certified ECDIS navigation.',
      );
    }

    if (
      notes.length === 0 &&
      gatePresentations.some((g) => g.semantic !== 'PASS')
    ) {
      notes.push(
        'One or more supporting datasets remain partial or unverified; inspect individual gates above.',
      );
    }

    return notes;
  }

  renderResult(data, requestPayload = this.lastSubmittedPayload) {
    const el = this.elements;
    if (!el.card || !el.resultBox) return;

    if (el.errorBox) el.errorBox.style.display = 'none';
    if (el.emptyState) el.emptyState.style.display = 'none';
    el.resultBox.style.display = 'block';

    const outcome = classifyReadinessOutcome(data);
    if (el.statusTag) {
      el.statusTag.className = outcome.tagClass;
      el.statusTag.textContent = outcome.tagText;
    }

    const gatesMap = data?.data_readiness_gates || {};
    const summary = computeGateSummary(gatesMap);
    const analytics = data?.verified_supporting_analytics || {};

    // 1. Overall Readiness Outcome Banner
    if (el.outcomeBanner) {
      el.outcomeBanner.innerHTML = '';
      el.outcomeBanner.className = `readiness-outcome outcome-${outcome.key}`;

      const head = this.doc.createElement('div');
      head.className = 'readiness-outcome-headline';
      head.textContent = outcome.headline;
      el.outcomeBanner.appendChild(head);

      const reason = this.doc.createElement('div');
      reason.className = 'readiness-outcome-reason';
      reason.textContent = outcome.reason;
      el.outcomeBanner.appendChild(reason);

      if (outcome.recommendationIssued) {
        const actionRow = this.doc.createElement('div');
        actionRow.className = 'readiness-outcome-action';
        actionRow.textContent = `Backend Action: ${outcome.recommendedAction}${outcome.recommendedContractStructure ? ` · Contract: ${outcome.recommendedContractStructure}` : ''}`;
        el.outcomeBanner.appendChild(actionRow);
      } else {
        const notice = this.doc.createElement('div');
        notice.className = 'readiness-outcome-withheld-note';
        notice.textContent =
          'No Fix Now, Wait, Hedge, Spot, COA, or TC recommendation is issued because prerequisite commercial or operational gates are unmet.';
        el.outcomeBanner.appendChild(notice);
      }
    }

    // 2. Assessment Summary Header
    if (el.summarySection) {
      el.summarySection.innerHTML = '';
      const heading = this.doc.createElement('div');
      heading.className = 'readiness-section-title';
      heading.textContent = 'ASSESSMENT SUMMARY';
      el.summarySection.appendChild(heading);

      const routeIdentity = data?.corridor
        ? `${data.corridor.route_name || data.corridor.route_code} (${data.corridor.route_code})`
        : requestPayload?.route_code
          ? `Corridor ${requestPayload.route_code}`
          : requestPayload?.origin && requestPayload?.destination
            ? `${requestPayload.origin} → ${requestPayload.destination}`
            : 'Requested Voyage';
      this._appendKvRow(el.summarySection, 'Route Identity', routeIdentity);

      const cargoText = `${data?.cargo_tonnes != null ? `${formatInt(data.cargo_tonnes)} MT` : requestPayload?.cargo_tonnes ? `${formatInt(requestPayload.cargo_tonnes)} MT` : '—'} · ${data?.commodity || requestPayload?.commodity || '—'}`;
      this._appendKvRow(el.summarySection, 'Cargo Scope', cargoText);

      const cands = Array.isArray(data?.ranked_vessel_candidates)
        ? data.ranked_vessel_candidates
        : [];
      const vesselScopeText =
        analytics.vessels_evaluated_count != null
          ? `${formatInt(analytics.vessels_evaluated_count)} registry/AIS records audited (${cands.length} candidate(s) returned)`
          : `${cands.length} candidate(s) returned`;
      this._appendKvRow(el.summarySection, 'Fleet Candidates', vesselScopeText);

      this._appendKvRow(
        el.summarySection,
        'Backend Status',
        `${String(data?.status || 'unavailable').toUpperCase()} · Recommendation Issued: ${outcome.recommendationIssued ? 'YES' : 'NO (WITHHELD)'}`,
        outcome.recommendationIssued ? 'is-ok' : 'is-warning',
      );

      const tsOrRef =
        data?.assessment_timestamp ||
        data?.timestamp ||
        data?.assessment_id ||
        analytics?.applicable_rate?.observation_date;
      if (tsOrRef) {
        this._appendKvRow(
          el.summarySection,
          data?.assessment_timestamp || data?.timestamp
            ? 'Assessment Timestamp'
            : 'Rate Reference Date',
          String(tsOrRef),
        );
      }

      const counters = this.doc.createElement('div');
      counters.className = 'readiness-counters-grid';
      const counterDefs = [
        ['Passed', summary.passed, 'pass'],
        ['Failed', summary.failed, 'fail'],
        ['Insufficient Data', summary.insufficientData, 'insufficient_data'],
        ['Unverified / Partial', summary.unverified, 'unverified'],
        ['Unavailable', summary.unavailable, 'unavailable'],
      ];
      for (const [label, count, cls] of counterDefs) {
        const pill = this.doc.createElement('div');
        pill.className = `readiness-counter-pill counter-${cls}`;
        pill.textContent = `${label}: ${count}`;
        counters.appendChild(pill);
      }
      el.summarySection.appendChild(counters);

      const nonCompNote = this.doc.createElement('div');
      nonCompNote.className = 'readiness-sub-note';
      nonCompNote.textContent =
        'Audit Rule: Readiness is strictly fail-closed. Passing gates never compensate for any failed, unavailable, unverified, or insufficient-data gate.';
      el.summarySection.appendChild(nonCompNote);
    }

    // 3. Verified Contract Comparisons (ONLY if genuinely returned by backend with recommendation_issued)
    if (el.contractSection) {
      el.contractSection.innerHTML = '';
      const comparisons = Array.isArray(data?.contract_comparisons)
        ? data.contract_comparisons
        : [];
      if (outcome.recommendationIssued && comparisons.length > 0) {
        el.contractSection.style.display = 'block';
        const title = this.doc.createElement('div');
        title.className = 'readiness-section-title';
        title.textContent = 'VERIFIED CONTRACT COST COMPARISONS';
        el.contractSection.appendChild(title);
        for (const cmp of comparisons) {
          this._appendKvRow(
            el.contractSection,
            cmp.contract_structure || 'Contract',
            `$${formatNum(cmp.total_cost_usd, 2)} total · $${formatNum(cmp.unit_cost_usd_per_mt, 2)}/MT (${cmp.voyages_evaluated || 1} voyage(s))`,
            'is-ok',
          );
        }
      } else {
        el.contractSection.style.display = 'none';
      }
    }

    // 4. All Six Readiness Gates
    const corridorFreightContext = this._getMatchingCorridorContext(data);
    const extraGateKeys = Object.keys(gatesMap).filter(
      (k) => !READINESS_GATE_ORDER.includes(k),
    );
    const gateKeysToRender = [...READINESS_GATE_ORDER, ...extraGateKeys];
    const gatePresentations = gateKeysToRender.map((gateKey) =>
      buildGatePresentation(
        gateKey,
        gatesMap[gateKey],
        data,
        corridorFreightContext,
      ),
    );

    if (el.gatesContainer) {
      el.gatesContainer.innerHTML = '';

      for (const gate of gatePresentations) {
        const card = this.doc.createElement('div');
        card.className = `readiness-gate-card gate-semantic-${gate.cssKey}`;
        card.dataset.gateKey = gate.key;

        const header = this.doc.createElement('div');
        header.className = 'readiness-gate-header';

        const titleWrap = this.doc.createElement('div');
        titleWrap.className = 'readiness-gate-title-wrap';
        const titleEl = this.doc.createElement('div');
        titleEl.className = 'readiness-gate-title';
        titleEl.textContent = gate.title;
        titleWrap.appendChild(titleEl);

        const idEl = this.doc.createElement('div');
        idEl.className = 'readiness-gate-id';
        idEl.textContent = `Backend Gate ID: ${gate.key}`;
        titleWrap.appendChild(idEl);
        header.appendChild(titleWrap);

        const rightControls = this.doc.createElement('div');
        rightControls.className = 'readiness-gate-controls';

        const badge = this.doc.createElement('span');
        badge.className = gate.tagClass;
        badge.textContent = gate.badgeText;
        rightControls.appendChild(badge);

        const expanded = this._gateExpandedState[gate.key] !== false;
        const toggleBtn = this.doc.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.className = 'readiness-gate-toggle';
        toggleBtn.setAttribute?.('aria-expanded', expanded ? 'true' : 'false');
        toggleBtn.textContent = expanded ? 'Hide Details ▴' : 'Show Details ▾';
        toggleBtn.addEventListener('click', () => {
          const next = !(this._gateExpandedState[gate.key] !== false);
          this.setGateExpanded(gate.key, next);
        });
        card._toggleBtn = toggleBtn;
        rightControls.appendChild(toggleBtn);
        header.appendChild(rightControls);
        card.appendChild(header);

        const explanation = this.doc.createElement('div');
        explanation.className = 'readiness-gate-explanation';
        explanation.textContent = gate.shortExplanation;
        card.appendChild(explanation);

        const body = this.doc.createElement('div');
        body.className = 'readiness-gate-body';
        body.style.display = expanded ? 'block' : 'none';

        if (gate.evidence.length > 0) {
          const evHeading = this.doc.createElement('div');
          evHeading.className = 'readiness-sublabel';
          evHeading.textContent = 'Supporting Evidence & Provenance:';
          body.appendChild(evHeading);

          const evUl = this.doc.createElement('ul');
          evUl.className = 'readiness-bullets';
          for (const line of gate.evidence) {
            const li = this.doc.createElement('li');
            li.textContent = line;
            evUl.appendChild(li);
          }
          body.appendChild(evUl);
        }

        const missHeading = this.doc.createElement('div');
        missHeading.className = 'readiness-sublabel';
        missHeading.textContent = 'Missing Inputs:';
        body.appendChild(missHeading);

        if (gate.missingInputs.length > 0) {
          const missUl = this.doc.createElement('ul');
          missUl.className = 'readiness-bullets is-missing';
          for (const item of gate.missingInputs) {
            const li = this.doc.createElement('li');
            li.textContent = item;
            missUl.appendChild(li);
          }
          body.appendChild(missUl);
        } else {
          const noneDiv = this.doc.createElement('div');
          noneDiv.className = 'readiness-sub-note';
          noneDiv.textContent = gate.presentInResponse
            ? 'None reported by this gate.'
            : 'Gate not reached due to unavailable prerequisite.';
          body.appendChild(noneDiv);
        }

        if (gate.warnings.length > 0) {
          for (const w of gate.warnings) {
            const warnDiv = this.doc.createElement('div');
            warnDiv.className = 'readiness-gate-warning';
            warnDiv.textContent = w;
            body.appendChild(warnDiv);
          }
        }

        card.appendChild(body);
        el.gatesContainer.appendChild(card);
      }
    }

    // 5. Consolidated Missing Decision Inputs
    if (el.missingSection) {
      el.missingSection.innerHTML = '';
      const missingAll = Array.isArray(data?.missing_decision_inputs)
        ? data.missing_decision_inputs
        : [];
      const h = this.doc.createElement('div');
      h.className = 'readiness-section-title';
      h.textContent = `MISSING DECISION INPUTS (${missingAll.length})`;
      el.missingSection.appendChild(h);

      if (missingAll.length === 0) {
        const empty = this.doc.createElement('div');
        empty.className = 'readiness-sub-note';
        empty.textContent = 'No missing decision inputs flagged.';
        el.missingSection.appendChild(empty);
      } else {
        const ul = this.doc.createElement('ul');
        ul.className = 'readiness-bullets is-missing';
        for (const item of missingAll) {
          const li = this.doc.createElement('li');
          li.textContent = String(item);
          ul.appendChild(li);
        }
        el.missingSection.appendChild(ul);
      }
    }

    // 6. Relevant Pelagos System Limitations
    if (el.limitationsSection) {
      el.limitationsSection.innerHTML = '';
      const h = this.doc.createElement('div');
      h.className = 'readiness-section-title';
      h.textContent = 'ACTIVE SYSTEM & DATA LIMITATIONS';
      el.limitationsSection.appendChild(h);

      const limNotes = this._collectRelevantLimitations(
        data,
        gatePresentations,
      );
      const ul = this.doc.createElement('ul');
      ul.className = 'readiness-bullets';
      for (const note of limNotes) {
        const li = this.doc.createElement('li');
        li.textContent = note;
        ul.appendChild(li);
      }
      el.limitationsSection.appendChild(ul);
    }

    // 7. Backend Authoritative Disclaimers
    if (el.disclaimersList) {
      el.disclaimersList.innerHTML = '';
      const disclaimers = Array.isArray(data?.disclaimers)
        ? data.disclaimers
        : [];
      for (const d of disclaimers) {
        const li = this.doc.createElement('li');
        li.textContent = String(d);
        el.disclaimersList.appendChild(li);
      }
    }
  }

  clearResult() {
    this._abortController?.abort();
    this.isAuditing = false;
    this.currentAudit = null;
    this.lastSubmittedPayload = null;
    this.clearValidationMessage();

    const el = this.elements;
    if (el.submitBtn) {
      el.submitBtn.disabled = false;
      el.submitBtn.classList?.remove?.('is-loading');
      const textSpan = el.submitBtn.querySelector?.('.btn-text');
      if (textSpan) textSpan.textContent = 'RUN CHARTER READINESS AUDIT';
    }
    if (el.statusTag) {
      el.statusTag.className = 'pelagos-tag tag-unmonitored';
      el.statusTag.textContent = 'AWAITING AUDIT';
    }
    if (el.errorBox) el.errorBox.style.display = 'none';
    if (el.resultBox) el.resultBox.style.display = 'none';
    if (el.emptyState) el.emptyState.style.display = 'block';
    this.syncVoyageSummary();
  }

  destroy() {
    this._abortController?.abort();
    for (const { el, event, handler } of this._boundListeners) {
      try {
        el.removeEventListener(event, handler);
      } catch {
        // Ignore teardown errors
      }
    }
    this._boundListeners = [];
  }
}

export { MONITORED_CORRIDOR_CODES };
