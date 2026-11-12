/**
 * @module ui/pelagosPanel
 * @description Dedicated Pelagos Maritime Intelligence panel and Cesium route visualization.
 *
 * Provides searchable origin and destination port selectors populated from repository-backed
 * hydrographic port data, executes MARNET sea routing with antimeridian seam sanitization,
 * renders interactive flowing polyline sea lanes on the Cesium globe, and presents a
 * structured freight intelligence dashboard (corridors, observed rates, forecasts, fleet
 * availability, and data scope limitations).
 */

import * as Cesium from 'cesium';
import {
  FlowMaterialProperty,
  ensureFlowFabricRegistered,
} from '../annotations/worldAnnotationRenderer.js';
import {
  DIRECTIONS_ROUTE_COLOR,
  normalizeRoutePayload,
} from '../layers/directions/index.js';
import {
  CORRIDOR_PROXIMITY_NM,
  normalizeLiveVesselItem,
  calculateDistanceToRouteNm,
  deduplicateAndFilterVessels,
  formatVesselCoords,
  formatRelativeAge,
} from './pelagosVessels.js';
import { PelagosOverviewController } from './pelagosOverview.js';
import { PelagosAssessmentController } from './pelagosAssessment.js';
import { PelagosReadinessController } from './pelagosReadiness.js';

/** Verified hydrographic ports fallback dataset from backend/pelagos/routing/fixtures/ports.json */
export const VERIFIED_HYDROGRAPHIC_PORTS = Object.freeze([
  {
    port_id: 'USSEA',
    name: 'Seattle',
    country: 'United States',
    country_code: 'US',
    coordinates: [-124.75, 48.5],
  },
  {
    port_id: 'JPYOK',
    name: 'Yokohama',
    country: 'Japan',
    country_code: 'JP',
    coordinates: [139.73, 35.15],
  },
  {
    port_id: 'USMSY',
    name: 'New Orleans',
    country: 'United States',
    country_code: 'US',
    coordinates: [-89.43, 28.9],
  },
  {
    port_id: 'NLRTM',
    name: 'Rotterdam',
    country: 'Netherlands',
    country_code: 'NL',
    coordinates: [3.85, 52.0],
  },
  {
    port_id: 'SGSIN',
    name: 'Singapore',
    country: 'Singapore',
    country_code: 'SG',
    coordinates: [103.65, 1.22],
  },
  {
    port_id: 'PAPAC',
    name: 'Balboa (Panama Pacific)',
    country: 'Panama',
    country_code: 'PA',
    coordinates: [-79.5167, 8.85],
  },
  {
    port_id: 'EGPSD',
    name: 'Port Said (Suez North)',
    country: 'Egypt',
    country_code: 'EG',
    coordinates: [32.35, 31.4],
  },
  {
    port_id: 'CASP_TEST',
    name: 'Baku',
    country: 'Azerbaijan',
    country_code: 'AZ',
    coordinates: [49.8333, 40.3667],
  },
]);

export class PelagosPanelController {
  /**
   * @param {object} options
   * @param {Cesium.Viewer} [options.viewer]
   * @param {object} [options.services]
   * @param {object} [options.actions]
   * @param {Document} [options.document]
   */
  constructor({
    viewer = null,
    services = {},
    actions = {},
    document = globalThis.document,
  } = {}) {
    this.viewer = viewer;
    this.services = services;
    this.actions = actions;
    this.doc = document;

    this.ports = [...VERIFIED_HYDROGRAPHIC_PORTS];
    this.currentRoute = null;
    this.currentFreight = null;
    this.isCalculating = false;
    this.routeEntities = [];

    this._routeSeq = 0;
    this._abortController = null;
    this._boundListeners = [];

    // Voyage Freight Calculator state (OPT-001)
    this.currentCargoTonnes = 52000;
    this.currentCostResult = null;
    this.isCalculatingCost = false;
    this._costSeq = 0;
    this._costAbortController = null;

    // Candidate Vessel Suitability state (OPT-002)
    this.currentSuitabilityResult = null;
    this.isMatchingSuitability = false;
    this._suitabilitySeq = 0;
    this._suitabilityAbortController = null;

    // AIS Vessel Intelligence state
    this.vesselsRaw = [];
    this.vesselsNormalized = [];
    this.selectedMmsi = null;
    this.vesselFilterTab = 'all'; // 'all' | 'corridor'
    this.vesselSearchQuery = '';
    this._windowListeners = [];

    this.overview = new PelagosOverviewController({
      document: this.doc,
      onNavigateExplorer: (domain) =>
        this.actions?.navigateToExplorer?.(domain),
    });

    this.assessment = new PelagosAssessmentController({
      document: this.doc,
      getPorts: () => this.ports,
      onCargoChange: (val) => {
        this.currentCargoTonnes = val;
        if (this.elements.cargoTonnesInput) {
          this.elements.cargoTonnesInput.value = String(val);
        }
        if (this.readiness?.elements?.cargoInput) {
          this.readiness.elements.cargoInput.value = String(val);
        }
      },
    });

    this.readiness = new PelagosReadinessController({
      document: this.doc,
      getPorts: () => this.ports,
      getCorridorFreightContext: () => this.currentFreight,
      onCargoChange: (val) => {
        this.currentCargoTonnes = val;
        if (this.elements.cargoTonnesInput) {
          this.elements.cargoTonnesInput.value = String(val);
        }
        if (this.assessment?.elements?.cargoInput) {
          this.assessment.elements.cargoInput.value = String(val);
        }
      },
    });

    this._resolveElements();
  }

  _resolveElements() {
    const d = this.doc;
    if (!d) return;

    this.elements = {
      panel: d.getElementById('pelagos-panel'),
      originSelect: d.getElementById('pelagos-origin-select'),
      destSelect: d.getElementById('pelagos-dest-select'),
      originFilter: d.getElementById('pelagos-origin-filter'),
      destFilter: d.getElementById('pelagos-dest-filter'),
      canalPolicySelect: d.getElementById('pelagos-canal-policy'),
      swapBtn: d.getElementById('pelagos-swap-btn'),
      calculateBtn: d.getElementById('pelagos-calculate-btn'),
      resetBtn: d.getElementById('pelagos-reset-btn'),
      validationMsg: d.getElementById('pelagos-validation-msg'),
      statusBanner: d.getElementById('pelagos-status-banner'),
      dashboard: d.getElementById('pelagos-dashboard'),

      // Route overview
      summaryOrigin: d.getElementById('pelagos-summary-origin'),
      summaryDest: d.getElementById('pelagos-summary-dest'),
      distNm: d.getElementById('pelagos-dist-nm'),
      distKm: d.getElementById('pelagos-dist-km'),
      routeMethod: d.getElementById('pelagos-route-method'),
      canalsVal: d.getElementById('pelagos-canals-val'),
      routeCanalPolicy: d.getElementById('pelagos-route-canal-policy'),
      waypointCount: d.getElementById('pelagos-waypoint-count'),

      // Corridor
      corridorCard: d.getElementById('pelagos-corridor-card'),
      corridorStatusTag: d.getElementById('pelagos-corridor-status-tag'),
      corridorContent: d.getElementById('pelagos-corridor-content'),
      corridorName: d.getElementById('pelagos-corridor-name'),
      corridorCode: d.getElementById('pelagos-corridor-code'),
      corridorCargo: d.getElementById('pelagos-corridor-cargo'),
      corridorVesselClass: d.getElementById('pelagos-corridor-vessel-class'),
      corridorDist: d.getElementById('pelagos-corridor-dist'),
      unmonitoredNotice: d.getElementById('pelagos-unmonitored-notice'),
      unmonitoredDesc: d.getElementById('pelagos-unmonitored-desc'),

      // Observed
      observedCard: d.getElementById('pelagos-observed-card'),
      observedTag: d.getElementById('pelagos-observed-tag'),
      observedContent: d.getElementById('pelagos-observed-content'),
      rateVal: d.getElementById('pelagos-rate-val'),
      rateUnit: d.getElementById('pelagos-rate-unit'),
      rateDate: d.getElementById('pelagos-rate-date'),
      rateSource: d.getElementById('pelagos-rate-source'),
      rateType: d.getElementById('pelagos-rate-type'),
      rateDatasetId: d.getElementById('pelagos-rate-dataset-id'),
      rateEmpty: d.getElementById('pelagos-rate-empty'),

      // Voyage Freight Calculator (OPT-001)
      costCard: d.getElementById('pelagos-cost-card'),
      costStatusTag: d.getElementById('pelagos-cost-status-tag'),
      cargoTonnesInput: d.getElementById('pelagos-cargo-tonnes'),
      calculateCostBtn: d.getElementById('pelagos-calculate-cost-btn'),
      costValidationMsg: d.getElementById('pelagos-cost-validation-msg'),
      costContent: d.getElementById('pelagos-cost-content'),
      costVal: d.getElementById('pelagos-cost-val'),
      costCurrency: d.getElementById('pelagos-cost-currency'),
      costFormula: d.getElementById('pelagos-cost-formula'),
      costRoute: d.getElementById('pelagos-cost-route'),
      costRate: d.getElementById('pelagos-cost-rate'),
      costCargo: d.getElementById('pelagos-cost-cargo'),
      costSource: d.getElementById('pelagos-cost-source'),
      costRateMeta: d.getElementById('pelagos-cost-rate-meta'),
      costCanalPolicy: d.getElementById('pelagos-cost-canal-policy'),
      costDistance: d.getElementById('pelagos-cost-distance'),
      costDistProvenance: d.getElementById('pelagos-cost-distance-provenance'),
      costDisclaimerCallout: d.getElementById(
        'pelagos-cost-disclaimer-callout',
      ),
      costDisclaimers: d.getElementById('pelagos-cost-disclaimers'),
      costEmpty: d.getElementById('pelagos-cost-empty'),

      // Candidate Vessel Suitability (OPT-002)
      suitabilityCard: d.getElementById('pelagos-suitability-card'),
      suitabilityTag: d.getElementById('pelagos-suitability-tag'),
      suitabilityContext: d.getElementById('pelagos-suitability-context'),
      suitabilityHistoricalBanner: d.getElementById(
        'pelagos-suitability-historical-banner',
      ),
      suitabilityRouteCompat: d.getElementById(
        'pelagos-suitability-route-compat',
      ),
      suitabilityMetrics: d.getElementById('pelagos-suitability-metrics'),
      suitabilityTotalCount: d.getElementById(
        'pelagos-suitability-total-count',
      ),
      suitabilityMatchedCount: d.getElementById(
        'pelagos-suitability-matched-count',
      ),
      suitabilityIncompatibleCount: d.getElementById(
        'pelagos-suitability-incompatible-count',
      ),
      suitabilityListContainer: d.getElementById(
        'pelagos-suitability-list-container',
      ),
      suitabilityList: d.getElementById('pelagos-suitability-list'),
      suitabilityEmpty: d.getElementById('pelagos-suitability-empty'),
      suitabilityCommercialDistinction: d.getElementById(
        'pelagos-suitability-commercial-distinction',
      ),
      suitabilityDisclaimers: d.getElementById(
        'pelagos-suitability-disclaimers',
      ),

      // Forecast
      forecastCard: d.getElementById('pelagos-forecast-card'),
      forecastTag: d.getElementById('pelagos-forecast-tag'),
      forecastContent: d.getElementById('pelagos-forecast-content'),
      forecastVal: d.getElementById('pelagos-forecast-val'),
      forecastUnit: d.getElementById('pelagos-forecast-unit'),
      forecastPeriod: d.getElementById('pelagos-forecast-period'),
      forecastModel: d.getElementById('pelagos-forecast-model'),
      forecastHorizon: d.getElementById('pelagos-forecast-horizon'),
      forecastHorizonsList: d.getElementById('pelagos-forecast-horizons-list'),
      forecastNote: d.getElementById('pelagos-forecast-note'),
      forecastEmpty: d.getElementById('pelagos-forecast-empty'),

      // Environmental Conditions (Buoy Stations)
      envCard: d.getElementById('pelagos-env-card'),
      envTag: d.getElementById('pelagos-env-tag'),
      envContent: d.getElementById('pelagos-env-content'),
      envMeta: d.getElementById('pelagos-env-meta'),
      envStations: d.getElementById('pelagos-env-stations'),
      envNotes: d.getElementById('pelagos-env-notes'),
      envEmpty: d.getElementById('pelagos-env-empty'),

      // Bunker Fuel References
      bunkerCard: d.getElementById('pelagos-bunker-card'),
      bunkerTag: d.getElementById('pelagos-bunker-tag'),
      bunkerContent: d.getElementById('pelagos-bunker-content'),
      bunkerProvider: d.getElementById('pelagos-bunker-provider'),
      bunkerHubs: d.getElementById('pelagos-bunker-hubs'),
      bunkerBenchmarks: d.getElementById('pelagos-bunker-benchmarks'),
      bunkerDisclaimer: d.getElementById('pelagos-bunker-disclaimer'),
      bunkerEmpty: d.getElementById('pelagos-bunker-empty'),

      // Fleet
      fleetCard: d.getElementById('pelagos-fleet-card'),
      fleetTag: d.getElementById('pelagos-fleet-tag'),
      fleetDot: d.getElementById('pelagos-fleet-dot'),
      fleetText: d.getElementById('pelagos-fleet-text'),
      fleetDesc: d.getElementById('pelagos-fleet-desc'),

      // Vessel Intelligence Controls & Inspector
      vesselMetrics: d.getElementById('pelagos-vessel-metrics'),
      globalVesselCount: d.getElementById('pelagos-global-vessel-count'),
      routeVesselCount: d.getElementById('pelagos-route-vessel-count'),
      vesselFilterBar: d.getElementById('pelagos-vessel-filter-bar'),
      vesselSearch: d.getElementById('pelagos-vessel-search'),
      tabAllBtn: d.getElementById('pelagos-tab-all'),
      tabAllCount: d.getElementById('pelagos-tab-all-count'),
      tabCorridorBtn: d.getElementById('pelagos-tab-corridor'),
      tabCorridorCount: d.getElementById('pelagos-tab-corridor-count'),
      selectedVesselCard: d.getElementById('pelagos-selected-vessel-card'),
      selectedVesselName: d.getElementById('pelagos-selected-vessel-name'),
      selectedVesselClose: d.getElementById('pelagos-selected-vessel-close'),
      selectedVesselMmsi: d.getElementById('pelagos-selected-vessel-mmsi'),
      selectedVesselImo: d.getElementById('pelagos-selected-vessel-imo'),
      selectedVesselType: d.getElementById('pelagos-selected-vessel-type'),
      selectedVesselDest: d.getElementById('pelagos-selected-vessel-dest'),
      selectedVesselCoords: d.getElementById('pelagos-selected-vessel-coords'),
      selectedVesselMotion: d.getElementById('pelagos-selected-vessel-motion'),
      selectedVesselHeading: d.getElementById(
        'pelagos-selected-vessel-heading',
      ),
      selectedVesselNav: d.getElementById('pelagos-selected-vessel-nav'),
      selectedVesselTime: d.getElementById('pelagos-selected-vessel-time'),
      selectedCorridorRow: d.getElementById('pelagos-selected-corridor-row'),
      selectedVesselCorridor: d.getElementById(
        'pelagos-selected-vessel-corridor',
      ),
      selectedVesselFocus: d.getElementById('pelagos-selected-vessel-focus'),
      vesselListContainer: d.getElementById('pelagos-vessel-list-container'),
      vesselList: d.getElementById('pelagos-vessel-list'),
      vesselEmpty: d.getElementById('pelagos-vessel-empty'),

      // Limitations
      limitationsList: d.getElementById('pelagos-limitations-list'),
    };
  }

  /** Initialize event listeners, live data overview, and port definitions. */
  async init() {
    this._resolveElements();
    if (!this.elements.panel) return;

    this._bindEvents();
    this._initGlobeEventListeners();
    const overviewPromise = this.overview?.init?.();
    const assessmentPromise = this.assessment?.init?.();
    this.readiness?.init?.();
    await this.loadPorts();
    if (overviewPromise) await overviewPromise;
    if (assessmentPromise) await assessmentPromise;
  }

  _bind(element, event, handler) {
    if (!element) return;
    element.addEventListener(event, handler);
    this._boundListeners.push({ element, event, handler });
  }

  _initGlobeEventListeners() {
    if (typeof window === 'undefined' || !window.addEventListener) return;

    const onEntitySelected = (e) => {
      const record = e?.detail;
      if (record?.layerId === 'ais-live-vessels') {
        const mmsi =
          record?.properties?.mmsi ??
          record?.entity?.mmsi ??
          (typeof record?.id === 'string' && record.id.startsWith('ais-')
            ? record.id.slice(4)
            : null);
        if (mmsi) {
          this.selectVessel(mmsi, { syncGlobe: false, flyCamera: false });
        }
      }
    };

    const onEntityCleared = (e) => {
      if (!e?.detail?.layerId || e.detail.layerId === 'ais-live-vessels') {
        this.clearSelectedVessel({ syncGlobe: false });
      }
    };

    window.addEventListener('gev:entity-selected', onEntitySelected);
    window.addEventListener('gev:entity-selection-cleared', onEntityCleared);

    this._windowListeners.push(
      { event: 'gev:entity-selected', handler: onEntitySelected },
      { event: 'gev:entity-selection-cleared', handler: onEntityCleared },
    );
  }

  _bindEvents() {
    const el = this.elements;

    this._bind(el.calculateBtn, 'click', () => {
      void this.calculateRoute();
    });

    this._bind(el.resetBtn, 'click', () => {
      this.clearRoute();
    });

    this._bind(el.swapBtn, 'click', () => {
      this.swapEndpoints();
    });

    this._bind(el.originSelect, 'change', () => {
      this.clearValidationMessage();
      if (el.originFilter && el.originSelect.value) {
        const port = this.findPort(el.originSelect.value);
        if (port) el.originFilter.value = `${port.name} (${port.port_id})`;
      }
    });

    this._bind(el.destSelect, 'change', () => {
      this.clearValidationMessage();
      if (el.destFilter && el.destSelect.value) {
        const port = this.findPort(el.destSelect.value);
        if (port) el.destFilter.value = `${port.name} (${port.port_id})`;
      }
    });

    this._bind(el.originFilter, 'input', (e) => {
      this.filterPortOptions(el.originSelect, e.target.value);
    });

    this._bind(el.destFilter, 'input', (e) => {
      this.filterPortOptions(el.destSelect, e.target.value);
    });

    if (el.vesselSearch) {
      this._bind(el.vesselSearch, 'input', (e) => {
        this.vesselSearchQuery = e.target.value || '';
        this.refreshVesselData();
      });
    }

    if (el.tabAllBtn) {
      this._bind(el.tabAllBtn, 'click', () => {
        this.setVesselTab('all');
      });
    }

    if (el.tabCorridorBtn) {
      this._bind(el.tabCorridorBtn, 'click', () => {
        this.setVesselTab('corridor');
      });
    }

    if (el.selectedVesselClose) {
      this._bind(el.selectedVesselClose, 'click', () => {
        this.clearSelectedVessel({ syncGlobe: true });
      });
    }

    if (el.selectedVesselFocus) {
      this._bind(el.selectedVesselFocus, 'click', () => {
        if (this.selectedMmsi) {
          this.focusVesselOnGlobe(this.selectedMmsi);
        }
      });
    }

    if (el.calculateCostBtn) {
      this._bind(el.calculateCostBtn, 'click', () => {
        void this.calculateVoyageCost();
      });
    }

    if (el.cargoTonnesInput) {
      this._bind(el.cargoTonnesInput, 'input', () => {
        this.clearCostValidationMessage();
        if (this.assessment?.elements?.cargoInput) {
          this.assessment.elements.cargoInput.value = el.cargoTonnesInput.value;
        }
        if (this.readiness?.elements?.cargoInput) {
          this.readiness.elements.cargoInput.value = el.cargoTonnesInput.value;
        }
      });
      this._bind(el.cargoTonnesInput, 'keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          void this.calculateVoyageCost();
        }
      });
    }
  }

  /** Retrieve registered port records from the backend API, falling back to fixture data. */
  async loadPorts() {
    try {
      const response = await fetch('/api/v1/routing/ports');
      if (response.ok) {
        const data = await response.json();
        if (Array.isArray(data) && data.length > 0) {
          this.ports = data;
        }
      }
    } catch {
      // Retain verified hydrographic fixture ports on fetch failure
    }

    this.ports.sort((a, b) => a.name.localeCompare(b.name));
    this.populateSelects();
    this.assessment?.syncVoyageSummary?.();
    this.readiness?.syncVoyageSummary?.();
  }

  findPort(identifier) {
    if (!identifier) return null;
    const clean = String(identifier).trim().toUpperCase();
    return this.ports.find((p) => p.port_id.toUpperCase() === clean) || null;
  }

  populateSelects() {
    this.populateSelect(this.elements.originSelect, 'Select origin port…');
    this.populateSelect(this.elements.destSelect, 'Select destination port…');
  }

  populateSelect(selectElement, placeholderText) {
    if (!selectElement) return;
    const prevVal = selectElement.value;
    selectElement.innerHTML = '';

    const defaultOpt = this.doc.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = placeholderText;
    selectElement.appendChild(defaultOpt);

    for (const port of this.ports) {
      const opt = this.doc.createElement('option');
      opt.value = port.port_id;
      opt.textContent = `${port.name}, ${port.country} (${port.port_id})`;
      selectElement.appendChild(opt);
    }

    if (prevVal && this.findPort(prevVal)) {
      selectElement.value = prevVal;
    }
  }

  filterPortOptions(selectElement, filterText) {
    if (!selectElement) return;
    const term = (filterText || '').trim().toLowerCase();
    const currentVal = selectElement.value;

    selectElement.innerHTML = '';
    const defaultOpt = this.doc.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = selectElement.id.includes('origin')
      ? 'Select origin port…'
      : 'Select destination port…';
    selectElement.appendChild(defaultOpt);

    const matches = this.ports.filter((port) => {
      if (!term) return true;
      return (
        port.name.toLowerCase().includes(term) ||
        port.country.toLowerCase().includes(term) ||
        port.port_id.toLowerCase().includes(term) ||
        (port.country_code && port.country_code.toLowerCase().includes(term))
      );
    });

    for (const port of matches) {
      const opt = this.doc.createElement('option');
      opt.value = port.port_id;
      opt.textContent = `${port.name}, ${port.country} (${port.port_id})`;
      selectElement.appendChild(opt);
    }

    if (currentVal && matches.some((p) => p.port_id === currentVal)) {
      selectElement.value = currentVal;
    } else if (matches.length === 1 && term.length >= 3) {
      selectElement.value = matches[0].port_id;
    }
  }

  swapEndpoints() {
    const el = this.elements;
    if (!el.originSelect || !el.destSelect) return;

    const tempVal = el.originSelect.value;
    el.originSelect.value = el.destSelect.value;
    el.destSelect.value = tempVal;

    if (el.originFilter) {
      const p = this.findPort(el.originSelect.value);
      el.originFilter.value = p ? `${p.name} (${p.port_id})` : '';
    }
    if (el.destFilter) {
      const p = this.findPort(el.destSelect.value);
      el.destFilter.value = p ? `${p.name} (${p.port_id})` : '';
    }

    this.clearValidationMessage();
    this.assessment?.syncVoyageSummary?.();
    this.readiness?.syncVoyageSummary?.();
  }

  validateEndpoints() {
    const el = this.elements;
    const origin = el.originSelect?.value;
    const dest = el.destSelect?.value;

    if (!origin || !dest) {
      this.showValidationMessage(
        'Please select both an origin and destination port.',
      );
      return false;
    }

    if (origin.toUpperCase() === dest.toUpperCase()) {
      const port = this.findPort(origin);
      const name = port ? port.name : origin;
      this.showValidationMessage(
        `Origin and destination cannot be the same location (${name}). Please select different endpoints.`,
      );
      return false;
    }

    this.clearValidationMessage();
    return true;
  }

  showValidationMessage(message) {
    const el = this.elements.validationMsg;
    if (!el) return;
    el.textContent = message;
    el.classList.add('visible');
  }

  clearValidationMessage() {
    const el = this.elements.validationMsg;
    if (!el) return;
    el.textContent = '';
    el.classList.remove('visible');
  }

  showCostValidationMessage(message) {
    const el = this.elements.costValidationMsg;
    if (!el) return;
    el.textContent = message;
    el.style.display = 'block';
  }

  clearCostValidationMessage() {
    const el = this.elements.costValidationMsg;
    if (!el) return;
    el.textContent = '';
    el.style.display = 'none';
  }

  setCostCalculatingState(calculating) {
    this.isCalculatingCost = calculating;
    const btn = this.elements.calculateCostBtn;
    if (!btn) return;
    btn.disabled = calculating;
    btn.classList.toggle('is-loading', calculating);
    const textSpan = btn.querySelector('.btn-text');
    if (textSpan) {
      textSpan.textContent = calculating
        ? 'CALCULATING…'
        : 'CALCULATE VOYAGE COST';
    }
  }

  showStatusBanner(variant, message) {
    const el = this.elements.statusBanner;
    if (!el) return;
    el.textContent = message;
    el.className = `pelagos-status-banner visible status-${variant}`;
  }

  clearStatusBanner() {
    const el = this.elements.statusBanner;
    if (!el) return;
    el.textContent = '';
    el.className = 'pelagos-status-banner';
  }

  setCalculatingState(calculating) {
    this.isCalculating = calculating;
    const btn = this.elements.calculateBtn;
    if (!btn) return;
    btn.disabled = calculating;
    btn.classList.toggle('is-loading', calculating);
    const textSpan = btn.querySelector('.btn-text');
    if (textSpan) {
      textSpan.textContent = calculating ? 'CALCULATING…' : 'CALCULATE ROUTE';
    }
  }

  /** Compute operational maritime route and retrieve freight intelligence. */
  async calculateRoute() {
    if (!this.validateEndpoints()) return;

    const originId = this.elements.originSelect.value;
    const destId = this.elements.destSelect.value;

    this.setCalculatingState(true);
    this.clearStatusBanner();

    this._routeSeq += 1;
    const seq = this._routeSeq;

    this._abortController?.abort();
    const controller = new AbortController();
    this._abortController = controller;

    this.showStatusBanner(
      'loading',
      'Calculating MARNET sea route and retrieving freight intelligence…',
    );

    try {
      const originPort = this.findPort(originId);
      const destPort = this.findPort(destId);

      const canalPolicy = this.elements.canalPolicySelect?.value || 'allow';
      const payload = {
        origin: originId,
        destination: destId,
        routing_mode: 'marnet',
        canal_policy: canalPolicy,
        supports_segments: true,
      };

      const routePromise = fetch('/api/v1/routing/calculate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      }).then(async (res) => {
        const data = await res.json();
        if (!res.ok) {
          throw new Error(
            data?.error_message || data?.error || `HTTP ${res.status}`,
          );
        }
        return data;
      });

      const freightPromise = fetch(
        `/api/v1/freight/route-intelligence?origin=${encodeURIComponent(originId)}&destination=${encodeURIComponent(destId)}`,
        {
          headers: { Accept: 'application/json' },
          signal: controller.signal,
        },
      )
        .then(async (res) => {
          if (!res.ok) return null;
          return await res.json();
        })
        .catch(() => null);

      const [routeData, freightData] = await Promise.all([
        routePromise,
        freightPromise,
      ]);

      if (seq !== this._routeSeq) return; // Stale request discarded

      const normalizedRoute = normalizeRoutePayload(routeData, 'sea');
      if (!normalizedRoute) {
        throw new Error(
          routeData?.error_message ||
            'Could not calculate a navigable maritime route.',
        );
      }

      this.currentRoute = normalizedRoute;
      this.currentFreight = freightData;

      // Render graphics on Cesium globe
      this.drawRouteOnGlobe(normalizedRoute, originPort, destPort);

      // Render Freight Intelligence Dashboard
      this.renderDashboard(normalizedRoute, freightData, originPort, destPort);

      this.showStatusBanner('ready', 'Maritime route calculated successfully.');
      setTimeout(() => {
        if (
          seq === this._routeSeq &&
          this.elements.statusBanner?.classList.contains('status-ready')
        ) {
          this.clearStatusBanner();
        }
      }, 3000);
    } catch (err) {
      if (seq !== this._routeSeq) return;
      if (err.name === 'AbortError') return;

      this.clearGlobeGraphics();
      if (this.elements.dashboard) {
        this.elements.dashboard.style.display = 'none';
      }
      this.showStatusBanner(
        'error',
        `Routing failed: ${err.message || 'Service unavailable'}`,
      );
    } finally {
      if (seq === this._routeSeq) {
        this.setCalculatingState(false);
      }
    }
  }

  /** Render route polylines, endpoint markers, and waypoint dots on Cesium globe. */
  drawRouteOnGlobe(route, originPort, destPort) {
    this.clearGlobeGraphics();
    if (!this.viewer) return;

    try {
      ensureFlowFabricRegistered();
    } catch {
      /* fabric registration fallback */
    }

    const allPositions = [];

    // 1. Draw Polyline segments (antimeridian safe)
    const segments =
      Array.isArray(route.geometrySegments) && route.geometrySegments.length > 0
        ? route.geometrySegments
        : Array.isArray(route.geometry) && route.geometry.length >= 2
          ? [route.geometry]
          : [];

    segments.forEach((seg, idx) => {
      if (!Array.isArray(seg) || seg.length < 2) return;
      const flatCoords = seg.flat();
      const positions = Cesium.Cartesian3.fromDegreesArray(flatCoords);
      positions.forEach((p) => allPositions.push(p));

      let material;
      try {
        material = new FlowMaterialProperty(DIRECTIONS_ROUTE_COLOR);
      } catch {
        material = Cesium.Color.fromCssColorString(
          DIRECTIONS_ROUTE_COLOR,
        ).withAlpha(0.9);
      }

      const entity = this.viewer.entities.add({
        id: `pelagos:route:segment_${idx}`,
        polyline: {
          positions,
          width: 9,
          material,
          clampToGround: true,
          classificationType: Cesium.ClassificationType.BOTH,
        },
      });
      this.routeEntities.push(entity);
    });

    // 2. Origin Marker A
    const origCoords =
      originPort?.coordinates || route.geometrySegments?.[0]?.[0];
    if (origCoords && Array.isArray(origCoords) && origCoords.length >= 2) {
      const origPos = Cesium.Cartesian3.fromDegrees(
        origCoords[0],
        origCoords[1],
        0,
      );
      allPositions.push(origPos);
      const markerA = this.viewer.entities.add({
        id: 'pelagos:marker:origin',
        position: origPos,
        point: {
          pixelSize: 14,
          color: Cesium.Color.fromCssColorString('#5dff9f'),
          outlineColor: Cesium.Color.fromCssColorString('#00d4ff'),
          outlineWidth: 2,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        label: {
          text: `A: ${originPort?.name || 'Origin'}`,
          font: '12px "JetBrains Mono", monospace',
          fillColor: Cesium.Color.WHITE,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 3,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -14),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
      this.routeEntities.push(markerA);
    }

    // 3. Destination Marker B
    const lastSeg = route.geometrySegments?.[route.geometrySegments.length - 1];
    const destCoords =
      destPort?.coordinates || (lastSeg && lastSeg[lastSeg.length - 1]);
    if (destCoords && Array.isArray(destCoords) && destCoords.length >= 2) {
      const destPos = Cesium.Cartesian3.fromDegrees(
        destCoords[0],
        destCoords[1],
        0,
      );
      allPositions.push(destPos);
      const markerB = this.viewer.entities.add({
        id: 'pelagos:marker:dest',
        position: destPos,
        point: {
          pixelSize: 14,
          color: Cesium.Color.fromCssColorString('#ff6b6b'),
          outlineColor: Cesium.Color.fromCssColorString('#00d4ff'),
          outlineWidth: 2,
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
        label: {
          text: `B: ${destPort?.name || 'Destination'}`,
          font: '12px "JetBrains Mono", monospace',
          fillColor: Cesium.Color.WHITE,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 3,
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -14),
          disableDepthTestDistance: Number.POSITIVE_INFINITY,
        },
      });
      this.routeEntities.push(markerB);
    }

    // 4. Waypoint dots
    if (Array.isArray(route.steps)) {
      route.steps.forEach((step, sIdx) => {
        if (sIdx === 0 || sIdx === route.steps.length - 1) return;
        if (!Number.isFinite(step.lon) || !Number.isFinite(step.lat)) return;
        const wpPos = Cesium.Cartesian3.fromDegrees(step.lon, step.lat, 0);
        const wpEntity = this.viewer.entities.add({
          id: `pelagos:waypoint:${sIdx}`,
          position: wpPos,
          point: {
            pixelSize: 7,
            color: Cesium.Color.WHITE.withAlpha(0.9),
            outlineColor: Cesium.Color.fromCssColorString('#39d0ff'),
            outlineWidth: 2,
            disableDepthTestDistance: Number.POSITIVE_INFINITY,
          },
        });
        this.routeEntities.push(wpEntity);
      });
    }

    // Frame camera on calculated passage
    if (this.viewer.camera && allPositions.length >= 2) {
      try {
        const sphere = Cesium.BoundingSphere.fromPoints(allPositions);
        this.viewer.camera.flyToBoundingSphere(sphere, {
          duration: 1.8,
          offset: new Cesium.HeadingPitchRange(
            0,
            Cesium.Math.toRadians(-50),
            Math.max(sphere.radius * 2.3, 2000000),
          ),
        });
      } catch {
        /* best-effort camera framing */
      }
    }

    this.actions?.holdContinuousRender?.('pelagos-route');
    this.actions?.governorRequestRender?.('pelagos-route');
  }

  /** Render the Freight Intelligence Dashboard cards with API and route metrics. */
  renderDashboard(route, freight, originPort, destPort) {
    const el = this.elements;
    if (!el.dashboard) return;
    el.dashboard.style.display = 'flex';

    // 1. Route Overview
    const origName = originPort?.name || route.originName || 'Origin';
    const origCountry = originPort?.country ? `, ${originPort.country}` : '';
    const destName = destPort?.name || route.destinationName || 'Destination';
    const destCountry = destPort?.country ? `, ${destPort.country}` : '';

    if (el.summaryOrigin) {
      el.summaryOrigin.textContent = `${origName}${origCountry} (${originPort?.port_id || route.originId || ''})`;
    }
    if (el.summaryDest) {
      el.summaryDest.textContent = `${destName}${destCountry} (${destPort?.port_id || route.destinationId || ''})`;
    }

    const distNm = route.distanceNm ?? route.distanceM / 1852;
    const distKm = route.distanceKm ?? route.distanceM / 1000;
    if (el.distNm) {
      el.distNm.textContent = Number(distNm).toLocaleString(undefined, {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      });
    }
    if (el.distKm) {
      el.distKm.textContent = Number(distKm).toLocaleString(undefined, {
        minimumFractionDigits: 1,
        maximumFractionDigits: 1,
      });
    }

    if (el.routeMethod) {
      el.routeMethod.textContent =
        route.routeMethod || 'MARNET 50km generalized';
    }

    if (el.canalsVal) {
      if (
        Array.isArray(route.canalsTransited) &&
        route.canalsTransited.length > 0
      ) {
        el.canalsVal.textContent = route.canalsTransited
          .map((c) => c.charAt(0).toUpperCase() + c.slice(1))
          .join(', ');
      } else {
        el.canalsVal.textContent = 'None (Open Ocean Passage)';
      }
    }

    if (el.routeCanalPolicy) {
      el.routeCanalPolicy.textContent =
        route.canalPolicyApplied || el.canalPolicySelect?.value || 'allow';
    }

    if (el.waypointCount) {
      el.waypointCount.textContent = `${route.steps?.length || 0} sea-lane waypoints`;
    }

    // 2. Freight Corridor
    const isMonitored =
      freight && freight.status === 'monitored' && freight.corridor;
    if (isMonitored) {
      const c = freight.corridor;
      if (el.corridorStatusTag) {
        el.corridorStatusTag.className = 'pelagos-tag tag-ready';
        el.corridorStatusTag.textContent = 'MONITORED';
      }
      if (el.corridorContent) el.corridorContent.style.display = 'block';
      if (el.unmonitoredNotice) el.unmonitoredNotice.style.display = 'none';

      if (el.corridorName)
        el.corridorName.textContent = c.route_name || c.route_code;
      if (el.corridorCode) el.corridorCode.textContent = c.route_code || '—';
      if (el.corridorCargo)
        el.corridorCargo.textContent = c.cargo_type || 'Bulk Grain';
      if (el.corridorVesselClass)
        el.corridorVesselClass.textContent =
          c.vessel_class || 'Panamax / Supramax';
      if (el.corridorDist) {
        el.corridorDist.textContent =
          c.benchmark_distance_nm !== null &&
          c.benchmark_distance_nm !== undefined
            ? `${Number(c.benchmark_distance_nm).toLocaleString('en-US', {
                maximumFractionDigits: 1,
              })} NM`
            : '—';
      }
    } else {
      if (el.corridorStatusTag) {
        el.corridorStatusTag.className = 'pelagos-tag tag-unmonitored';
        el.corridorStatusTag.textContent = 'UNMONITORED';
      }
      if (el.corridorContent) el.corridorContent.style.display = 'none';
      if (el.unmonitoredNotice) el.unmonitoredNotice.style.display = 'flex';
      if (el.unmonitoredDesc) {
        el.unmonitoredDesc.textContent =
          freight?.message ||
          `Trade lane ${origName} → ${destName} is currently unmonitored. Pelagos freight observations and forecasting models are limited to U.S.-Japan bulk grain corridors.`;
      }
    }

    // 3. Observed Freight Rate
    const obs = isMonitored ? freight.latest_observation : null;
    if (obs && Number.isFinite(Number(obs.rate_value))) {
      if (el.observedContent) el.observedContent.style.display = 'block';
      if (el.rateEmpty) el.rateEmpty.style.display = 'none';
      if (el.observedTag) el.observedTag.style.display = 'inline-block';

      if (el.rateVal)
        el.rateVal.textContent = Number(obs.rate_value).toFixed(2);
      if (el.rateUnit)
        el.rateUnit.textContent = `${obs.currency || 'USD'}/${obs.unit?.split('/')[1] || 'MT'}`;
      if (el.rateDate) el.rateDate.textContent = obs.observation_date || '—';
      if (el.rateSource)
        el.rateSource.textContent =
          obs.source_name || "USDA AMS / O'Neil Commodity Consulting";
      if (el.rateType) {
        el.rateType.textContent = obs.is_derived
          ? 'Derived Analytical Estimate'
          : 'Verified Published Benchmark (Not Derived)';
      }
      if (el.rateDatasetId) {
        el.rateDatasetId.textContent = obs.source_dataset_id || '—';
      }
    } else {
      if (el.observedContent) el.observedContent.style.display = 'none';
      if (el.rateEmpty) {
        el.rateEmpty.style.display = 'block';
        el.rateEmpty.textContent = isMonitored
          ? 'No observed benchmark rate available for this corridor.'
          : 'No freight rate observations available for this unmonitored route.';
      }
      if (el.observedTag) el.observedTag.style.display = 'none';
      if (el.rateDatasetId) el.rateDatasetId.textContent = '—';
    }

    // 3b. Voyage Freight Calculator Card (OPT-001)
    if (isMonitored) {
      if (el.costCard) el.costCard.style.display = 'block';
      void this.calculateVoyageCost({
        triggerOrigin: originPort?.port_id || route.originId,
        triggerDest: destPort?.port_id || route.destinationId,
      });
      void this.matchSuitableVessels({
        triggerOrigin: originPort?.port_id || route.originId,
        triggerDest: destPort?.port_id || route.destinationId,
        cargoTonnes: this.currentCargoTonnes,
      });
    } else {
      if (el.costCard) el.costCard.style.display = 'block';
      if (el.costStatusTag) {
        el.costStatusTag.className = 'pelagos-tag tag-unmonitored';
        el.costStatusTag.textContent = 'UNAVAILABLE';
      }
      if (el.costContent) el.costContent.style.display = 'none';
      if (el.costDisclaimers) el.costDisclaimers.innerHTML = '';
      if (el.costEmpty) {
        el.costEmpty.style.display = 'block';
        el.costEmpty.textContent =
          freight?.message ||
          'Voyage freight cost calculation is unavailable for unmonitored trade routes. Genuine rate benchmarks are currently limited to U.S.-Japan dry bulk grain corridors.';
      }
      void this.matchSuitableVessels({
        triggerOrigin: originPort?.port_id || route.originId,
        triggerDest: destPort?.port_id || route.destinationId,
        cargoTonnes: this.currentCargoTonnes,
      });
    }

    // 4. Multi-Horizon Production Freight Forecasts (T+1, T+3, T+6)
    this._renderMultiHorizonForecasts(freight, isMonitored);

    // 4b. Environmental Conditions (Buoy Stations)
    this._renderEnvironmentalConditions(
      freight?.environmental_conditions,
      isMonitored,
    );

    // 4c. Bunker Fuel References
    this._renderBunkerReferences(
      freight?.bunker_fuel_references || freight?.bunker_fuel,
      isMonitored,
    );

    // 5. Data Coverage & Limitations
    if (el.limitationsList) {
      el.limitationsList.innerHTML = '';
      const disclaimers =
        freight?.disclaimers && freight.disclaimers.length > 0
          ? freight.disclaimers
          : [
              'Benchmark freight rates apply specifically to Agricultural Bulk Grain in Panamax / Supramax bulk carriers.',
              'Does NOT represent general container (TEU), tanker (VLCC), or spot vessel charter agreements.',
              'MARNET routes are hydrographic sea-lane approximations; not certified passage plans.',
            ];
      disclaimers.forEach((disc) => {
        const li = this.doc.createElement('li');
        li.textContent = disc;
        el.limitationsList.appendChild(li);
      });
    }

    // 6. Vessel Availability / Fleet Telemetry Status
    void this.updateFleetAvailability(freight);
  }

  /**
   * Render multi-horizon production forecasts (T+1, T+3, T+6) without client-side extrapolation.
   */
  _renderMultiHorizonForecasts(freight, isMonitored) {
    const el = this.elements;
    const forecastsList = Array.isArray(freight?.forecasts)
      ? freight.forecasts
      : [];
    const multiMap =
      freight?.multi_horizon_forecasts &&
      typeof freight.multi_horizon_forecasts === 'object'
        ? freight.multi_horizon_forecasts
        : {};
    const fc = isMonitored
      ? freight?.baseline_forecast ||
        forecastsList[0] ||
        multiMap['T+1'] ||
        null
      : null;

    if (el.forecastHorizonsList) {
      el.forecastHorizonsList.innerHTML = '';
    }

    if (fc && Number.isFinite(Number(fc.predicted_value))) {
      if (el.forecastContent) el.forecastContent.style.display = 'block';
      if (el.forecastEmpty) el.forecastEmpty.style.display = 'none';
      if (el.forecastTag) {
        const isExp =
          Boolean(fc.is_experimental) || fc.model_family === 'experimental_ml';
        el.forecastTag.className = isExp
          ? 'pelagos-tag tag-warning'
          : 'pelagos-tag tag-model';
        el.forecastTag.textContent = isExp
          ? 'EXPERIMENTAL MODEL'
          : 'PERSISTENCE (ACTIVE PRODUCTION)';
      }

      if (el.forecastVal)
        el.forecastVal.textContent = Number(fc.predicted_value).toFixed(2);
      if (el.forecastUnit) el.forecastUnit.textContent = fc.unit || 'USD/MT';
      if (el.forecastPeriod)
        el.forecastPeriod.textContent = fc.target_date || 'Unavailable';

      const modelRaw = fc.model_type || 'persistence';
      const modelName = modelRaw.charAt(0).toUpperCase() + modelRaw.slice(1);
      const familyLabel =
        fc.model_family === 'experimental_ml' || fc.is_experimental
          ? 'Experimental'
          : 'Active Production';
      if (el.forecastModel)
        el.forecastModel.textContent = `${modelName} Baseline (${familyLabel})`;
      if (el.forecastHorizon)
        el.forecastHorizon.textContent = `T+${fc.horizon_months || 1} (${fc.horizon_months || 1} Month Forward)`;

      if (el.forecastHorizonsList) {
        [1, 3, 6].forEach((h) => {
          const item =
            multiMap[`T+${h}`] ||
            forecastsList.find((f) => Number(f?.horizon_months) === h) ||
            null;
          const row = this.doc.createElement('div');
          row.className = 'pelagos-forecast-horizon-row';

          const hBadge = this.doc.createElement('span');
          hBadge.className = 'fh-horizon';
          hBadge.textContent = `T+${h}`;
          row.appendChild(hBadge);

          const targetSpan = this.doc.createElement('span');
          targetSpan.className = 'fh-target';
          targetSpan.textContent = item?.target_date || 'Target unavailable';
          row.appendChild(targetSpan);

          const valSpan = this.doc.createElement('span');
          if (item && Number.isFinite(Number(item.predicted_value))) {
            valSpan.className = 'fh-value';
            const mType = item.model_type || 'persistence';
            valSpan.textContent = `$${Number(item.predicted_value).toFixed(2)} ${item.unit || 'USD/MT'} (${mType})`;
          } else {
            valSpan.className = 'fh-value is-unavailable';
            valSpan.textContent = 'UNAVAILABLE';
          }
          row.appendChild(valSpan);

          el.forecastHorizonsList.appendChild(row);
        });
      }

      if (el.forecastNote) {
        const extraNote = fc.warning || fc.notes || '';
        el.forecastNote.textContent = `${extraNote ? `${extraNote} ` : ''}Active Production Baseline: Persistence (C3.2/C3.3 experimental ML models are NOT promoted to production). Flat multi-horizon persistence values are naive benchmarks, NOT directional market-timing signals.`;
      }
    } else {
      if (el.forecastTag) {
        el.forecastTag.className = 'pelagos-tag tag-unmonitored';
        el.forecastTag.textContent = 'UNAVAILABLE';
      }
      if (el.forecastContent) el.forecastContent.style.display = 'none';
      if (el.forecastEmpty) {
        el.forecastEmpty.style.display = 'block';
        el.forecastEmpty.textContent = isMonitored
          ? freight?.baseline_forecast?.notes ||
            'Forecast horizons unavailable for this period.'
          : 'No predictive forecast model active for this trade lane.';
      }
    }
  }

  /**
   * Render coastal buoy environmental observations with explicit freshness and null handling.
   */
  _renderEnvironmentalConditions(env, isMonitored) {
    const el = this.elements;
    if (!el.envCard) return;

    if (el.envStations) el.envStations.innerHTML = '';
    if (el.envMeta) el.envMeta.textContent = '';
    if (el.envNotes) el.envNotes.textContent = '';

    if (!env) {
      if (el.envTag) {
        el.envTag.className = 'pelagos-tag tag-unmonitored';
        el.envTag.textContent = 'UNAVAILABLE';
      }
      if (el.envContent) el.envContent.style.display = 'none';
      if (el.envEmpty) {
        el.envEmpty.style.display = 'block';
        el.envEmpty.textContent = isMonitored
          ? 'No environmental buoy station data returned for this corridor.'
          : 'Environmental buoy observations are surfaced for monitored corridors only.';
      }
      return;
    }

    const statusUpper = String(env.status || 'unavailable').toUpperCase();
    if (el.envTag) {
      el.envTag.className =
        env.status === 'available'
          ? 'pelagos-tag tag-verified'
          : env.status === 'partial'
            ? 'pelagos-tag tag-warning'
            : 'pelagos-tag tag-unmonitored';
      el.envTag.textContent = statusUpper;
    }

    const stations = Array.isArray(env.stations) ? env.stations : [];
    if (el.envContent) el.envContent.style.display = 'block';
    if (el.envEmpty) el.envEmpty.style.display = 'none';

    if (el.envMeta) {
      const fairPt = env.corridor_reference_point || 'Departure fairway';
      const covStatus = env.coverage_status || 'unknown';
      const covExtent = env.coverage_extent || 'coastal_departure_fairway';
      const count = env.stations_count ?? stations.length;
      el.envMeta.textContent = `Coverage: ${covStatus} · Scope: ${covExtent} · Reference: ${fairPt} · Stations: ${count}`;
    }

    if (el.envStations) {
      if (stations.length === 0) {
        const emptyNote = this.doc.createElement('div');
        emptyNote.className = 'pelagos-empty-state';
        emptyNote.textContent =
          'No active moored buoy observations recorded for this corridor.';
        el.envStations.appendChild(emptyNote);
      } else {
        const varDefs = [
          ['wind_speed_knots', 'Wind Speed'],
          ['wind_direction_degrees', 'Wind Dir'],
          ['wave_height_meters', 'Wave Height'],
          ['wave_period_seconds', 'Wave Period'],
          ['sea_surface_temp_c', 'Sea Temp'],
          ['air_pressure_hpa', 'Air Pressure'],
        ];

        stations.forEach((st) => {
          const card = this.doc.createElement('div');
          card.className = 'pelagos-env-station-item';

          const top = this.doc.createElement('div');
          top.className = 'env-station-top';

          const titleWrap = this.doc.createElement('div');
          titleWrap.className = 'env-station-title';
          titleWrap.textContent = `${st.station_name || st.station_id} (${st.station_id || '—'})`;
          top.appendChild(titleWrap);

          const freshness = String(st.freshness || 'unknown').toLowerCase();
          const freshBadge = this.doc.createElement('span');
          freshBadge.className = `env-freshness-badge freshness-${freshness}`;
          freshBadge.textContent = freshness.toUpperCase();
          top.appendChild(freshBadge);
          card.appendChild(top);

          const meta = this.doc.createElement('div');
          meta.className = 'env-station-meta';
          const coords =
            st.coordinates &&
            Number.isFinite(Number(st.coordinates.latitude)) &&
            Number.isFinite(Number(st.coordinates.longitude))
              ? `${Number(st.coordinates.latitude).toFixed(2)}°, ${Number(st.coordinates.longitude).toFixed(2)}°`
              : 'Coords: —';
          const fairDist =
            st.distance_to_corridor_fairway_nm !== null &&
            st.distance_to_corridor_fairway_nm !== undefined
              ? `${Number(st.distance_to_corridor_fairway_nm).toFixed(1)} NM to fairway`
              : 'Fairway dist: —';
          const origDist =
            st.distance_to_origin_port_nm !== null &&
            st.distance_to_origin_port_nm !== undefined
              ? ` · ${Number(st.distance_to_origin_port_nm).toFixed(1)} NM to origin`
              : '';
          const ageStr =
            st.data_age_hours !== null && st.data_age_hours !== undefined
              ? `${Number(st.data_age_hours).toFixed(1)}h age`
              : 'Age: —';
          meta.textContent = `${st.sea_area || 'Offshore'} · ${coords} · ${fairDist}${origDist} · Quality: ${st.quality_status || '—'} · Obs: ${st.observation_time || '—'} (${ageStr}) · ${st.source_name || 'NOAA NDBC'}`;
          card.appendChild(meta);

          const grid = this.doc.createElement('div');
          grid.className = 'env-vars-grid';
          varDefs.forEach(([key, label]) => {
            const cell = this.doc.createElement('div');
            cell.className = 'env-var-cell';

            const kSpan = this.doc.createElement('span');
            kSpan.className = 'env-var-k';
            kSpan.textContent = label;
            cell.appendChild(kSpan);

            const vInfo = st.variables?.[key];
            const vSpan = this.doc.createElement('span');
            if (vInfo && vInfo.value !== null && vInfo.value !== undefined) {
              vSpan.className = 'env-var-v';
              vSpan.textContent = `${vInfo.value} ${vInfo.unit || ''}`.trim();
            } else {
              vSpan.className = 'env-var-v is-null';
              vSpan.textContent = '— (NULL)';
            }
            cell.appendChild(vSpan);
            grid.appendChild(cell);
          });
          card.appendChild(grid);
          el.envStations.appendChild(card);
        });
      }
    }

    if (el.envNotes) {
      const parts = [
        env.aggregation?.summary,
        env.spatial_coverage_notes,
        env.disclaimer,
      ].filter(Boolean);
      el.envNotes.textContent =
        parts.join(' · ') ||
        'Point observations at coastal departure buoys only; not interpolated across open ocean.';
    }
  }

  /**
   * Render bunker fuel reference hubs and non-substituted regional benchmarks.
   */
  _renderBunkerReferences(bunker, isMonitored) {
    const el = this.elements;
    if (!el.bunkerCard) return;

    if (el.bunkerProvider) el.bunkerProvider.textContent = '';
    if (el.bunkerHubs) el.bunkerHubs.innerHTML = '';
    if (el.bunkerBenchmarks) el.bunkerBenchmarks.innerHTML = '';
    if (el.bunkerDisclaimer) el.bunkerDisclaimer.textContent = '';

    if (!bunker) {
      if (el.bunkerTag) {
        el.bunkerTag.className = 'pelagos-tag tag-unmonitored';
        el.bunkerTag.textContent = 'UNAVAILABLE';
      }
      if (el.bunkerContent) el.bunkerContent.style.display = 'none';
      if (el.bunkerEmpty) {
        el.bunkerEmpty.style.display = 'block';
        el.bunkerEmpty.textContent = isMonitored
          ? 'No bunker fuel reference summary returned.'
          : 'Bunker fuel references are surfaced for monitored corridors only.';
      }
      return;
    }

    const status = String(bunker.status || 'unavailable').toLowerCase();
    if (el.bunkerTag) {
      el.bunkerTag.className =
        status === 'available'
          ? 'pelagos-tag tag-verified'
          : status === 'partial'
            ? 'pelagos-tag tag-warning'
            : 'pelagos-tag tag-unmonitored';
      el.bunkerTag.textContent = status.toUpperCase();
    }

    if (el.bunkerContent) el.bunkerContent.style.display = 'block';
    if (el.bunkerEmpty) el.bunkerEmpty.style.display = 'none';

    const hubs = Array.isArray(bunker.bunkering_hubs_in_scope)
      ? bunker.bunkering_hubs_in_scope
      : [];
    const latestQuotes = Array.isArray(bunker.latest_quotes)
      ? bunker.latest_quotes
      : [];
    const benchmarks = Array.isArray(bunker.regional_benchmarks)
      ? bunker.regional_benchmarks
      : [];

    if (el.bunkerProvider) {
      const quotedCount = bunker.hubs_with_prices_count ?? 0;
      el.bunkerProvider.textContent = `Provider: ${bunker.provider || '—'} · Corridor Quotes: ${status.toUpperCase()} (${quotedCount}/${hubs.length} hubs quoted) · ${bunker.reason || ''}`;
    }

    const createQuoteRow = (q, isBenchmark = false) => {
      const row = this.doc.createElement('div');
      row.className = isBenchmark
        ? 'pelagos-bunker-quote-row is-benchmark'
        : 'pelagos-bunker-quote-row';

      const main = this.doc.createElement('div');
      main.className = 'bunker-quote-main';
      const priceText = Number.isFinite(Number(q.price_usd_per_mt))
        ? `$${Number(q.price_usd_per_mt).toFixed(2)} ${q.unit || q.currency || 'USD/MT'}`
        : '—';
      main.textContent = `${q.port_name || '—'} (${q.port_code || '—'}) · ${q.fuel_grade || '—'} · ${priceText}`;
      row.appendChild(main);

      const sub = this.doc.createElement('div');
      sub.className = 'bunker-quote-sub';
      const basis = q.delivery_basis || '—';
      const pType = q.pricing_type || 'indicative';
      const fresh = String(q.freshness || 'stale').toUpperCase();
      sub.textContent = `${isBenchmark ? 'External Regional Benchmark (NOT corridor hub pricing) · ' : ''}Type: ${pType} · Basis: ${basis} · Freshness: ${fresh} · Obs: ${q.observation_time || '—'} · Retrieved: ${q.retrieval_time_utc || '—'}`;
      row.appendChild(sub);

      return row;
    };

    if (el.bunkerHubs) {
      const heading = this.doc.createElement('div');
      heading.className = 'bunker-section-heading';
      heading.textContent = 'CORRIDOR BUNKERING HUBS IN SCOPE';
      el.bunkerHubs.appendChild(heading);

      if (hubs.length === 0 && latestQuotes.length === 0) {
        const none = this.doc.createElement('div');
        none.className = 'pelagos-empty-state';
        none.textContent = 'No corridor bunkering hubs configured.';
        el.bunkerHubs.appendChild(none);
      } else {
        hubs.forEach((hub) => {
          const item = this.doc.createElement('div');
          item.className = 'pelagos-bunker-hub-item';

          const hTop = this.doc.createElement('div');
          hTop.className = 'bunker-hub-top';
          const hName = this.doc.createElement('span');
          hName.textContent = `${hub.name || '—'} (${hub.port_id || '—'}) · ${hub.region || '—'}`;
          hTop.appendChild(hName);

          const hStat = this.doc.createElement('span');
          const hubAvailable = hub.status === 'available';
          hStat.className = hubAvailable
            ? 'pelagos-tag tag-verified'
            : 'pelagos-tag tag-unmonitored';
          hStat.textContent = String(hub.status || 'unavailable').toUpperCase();
          hTop.appendChild(hStat);
          item.appendChild(hTop);

          const hRole = this.doc.createElement('div');
          hRole.className = 'bunker-hub-meta';
          hRole.textContent = `Role: ${hub.role || '—'}${hub.availability_reason ? ` · ${hub.availability_reason}` : hubAvailable ? '' : ' · Corridor hub pricing unavailable (no substitution)'}`;
          item.appendChild(hRole);

          if (Array.isArray(hub.quotes) && hub.quotes.length > 0) {
            hub.quotes.forEach((q) => {
              item.appendChild(createQuoteRow(q, false));
            });
          }

          el.bunkerHubs.appendChild(item);
        });

        if (
          latestQuotes.length > 0 &&
          !hubs.some((h) => Array.isArray(h.quotes) && h.quotes.length > 0)
        ) {
          latestQuotes.forEach((q) => {
            el.bunkerHubs.appendChild(createQuoteRow(q, false));
          });
        }
      }
    }

    if (el.bunkerBenchmarks) {
      const bHead = this.doc.createElement('div');
      bHead.className = 'bunker-section-heading';
      bHead.textContent =
        'EXTERNAL REGIONAL BENCHMARKS (NOT SUBSTITUTED FOR CORRIDOR HUBS)';
      el.bunkerBenchmarks.appendChild(bHead);

      if (benchmarks.length === 0) {
        const none = this.doc.createElement('div');
        none.className = 'bunker-hub-meta';
        none.textContent =
          'No external regional benchmark quotes returned. Corridor bunkering hub pricing remains unavailable and is never synthesized.';
        el.bunkerBenchmarks.appendChild(none);
      } else {
        benchmarks.forEach((q) => {
          el.bunkerBenchmarks.appendChild(createQuoteRow(q, true));
        });
      }
    }

    if (el.bunkerDisclaimer) {
      el.bunkerDisclaimer.textContent = `${bunker.disclaimer || 'Indicative bunker quotes only.'} Voyage fuel expenditure is NOT calculated without verified vessel fuel consumption and corridor hub stem quotes.`;
    }
  }

  /**
   * Dynamically query live AIS stream status and update the Vessel Availability card.
   * Accurately distinguishes live sea telemetry from commercial open-tonnage availability.
   */
  async updateFleetAvailability(freight) {
    const el = this.elements;
    if (!el.fleetCard) return;

    try {
      const resp = await fetch('/api/ais-live?maxRows=200', {
        cache: 'no-store',
      });
      if (resp.ok) {
        const liveData = await resp.json();
        const status = liveData?.status;
        const rows = Array.isArray(liveData?.rows) ? liveData.rows : [];
        const count = rows.length;
        const totalLive = liveData?.rawRowCount || count;
        this.totalLiveCount = totalLive;

        if (
          (status === 'live' || status === 'sample' || rows.length > 0) &&
          count > 0
        ) {
          this.vesselsRaw = rows;
          const isSample =
            status === 'sample' || rows.some((r) => r.historical);

          if (isSample) {
            if (el.fleetTag) {
              el.fleetTag.className = 'pelagos-tag tag-warning';
              el.fleetTag.textContent = 'SAMPLE FEED';
            }
            if (el.fleetDot)
              el.fleetDot.className = 'status-indicator-dot dot-warning';
            if (el.fleetText) {
              el.fleetText.textContent = `Historical Sample — ${totalLive.toLocaleString()} Coastal Vessel Tracks (${count} loaded)`;
            }
            if (el.fleetDesc) {
              el.fleetDesc.textContent =
                'Displaying historical coastal vessel sample. Notice: This is not live telemetry and does not reflect current sea positions or commercial vessel charter availability.';
            }
          } else {
            if (el.fleetTag) {
              el.fleetTag.className = 'pelagos-tag tag-ready';
              el.fleetTag.textContent = 'TELEMETRY LIVE';
            }
            if (el.fleetDot)
              el.fleetDot.className = 'status-indicator-dot dot-active';
            if (el.fleetText) {
              el.fleetText.textContent = `Active — Live AIS Telemetry Connected (${totalLive.toLocaleString()} vessels tracked)`;
            }
            if (el.fleetDesc) {
              el.fleetDesc.textContent =
                'Live AIS position stream is connected. Real-time fleet tracking is active across monitored sea lanes. Notice: Commercial open-tonnage fixture lists remain unconnected (AIS telemetry alone does not establish commercial vessel charter availability).';
            }
          }

          // Reveal vessel intelligence UI sections
          if (el.vesselMetrics) el.vesselMetrics.style.display = 'grid';
          if (el.vesselFilterBar) el.vesselFilterBar.style.display = 'flex';
          if (el.vesselListContainer)
            el.vesselListContainer.style.display = 'block';

          this.refreshVesselData();
          return;
        }

        // Handle offline / non-active statuses
        this._hideVesselControls();

        if (status === 'connecting' || status === 'idle') {
          if (el.fleetTag) {
            el.fleetTag.className = 'pelagos-tag tag-loading';
            el.fleetTag.textContent = 'CONNECTING';
          }
          if (el.fleetDot)
            el.fleetDot.className = 'status-indicator-dot dot-pending';
          if (el.fleetText)
            el.fleetText.textContent =
              'Connecting — Establishing AISStream WebSocket…';
          if (el.fleetDesc) {
            el.fleetDesc.textContent =
              'WebSocket connection to stream.aisstream.io is in progress. Awaiting initial valid AIS envelope reception.';
          }
          return;
        }

        if (status === 'missing-key') {
          if (el.fleetTag) {
            el.fleetTag.className = 'pelagos-tag tag-offline';
            el.fleetTag.textContent = 'UNCONFIGURED';
          }
          if (el.fleetDot)
            el.fleetDot.className = 'status-indicator-dot dot-inactive';
          if (el.fleetText)
            el.fleetText.textContent =
              'Unavailable — AISSTREAM_API_KEY is not configured';
          if (el.fleetDesc) {
            el.fleetDesc.textContent =
              'No AISStream key configured. Add AISSTREAM_API_KEY to Provider Settings (bottom right POWER UP panel) or .env to enable live fleet telemetry.';
          }
          return;
        }

        if (status === 'auth-failed') {
          if (el.fleetTag) {
            el.fleetTag.className = 'pelagos-tag tag-offline';
            el.fleetTag.textContent = 'AUTH FAILED';
          }
          if (el.fleetDot)
            el.fleetDot.className = 'status-indicator-dot dot-inactive';
          if (el.fleetText)
            el.fleetText.textContent =
              'Unavailable — AISStream API key rejected';
          if (el.fleetDesc) {
            el.fleetDesc.textContent =
              'The configured AISStream credential was rejected by the upstream gateway. Please verify your AISSTREAM_API_KEY.';
          }
          return;
        }
      }
    } catch {
      /* Fallback to freight endpoint data or default offline state */
    }

    this._hideVesselControls();

    if (el.fleetTag) {
      el.fleetTag.className = 'pelagos-tag tag-offline';
      el.fleetTag.textContent = 'UNCONNECTED';
    }
    if (el.fleetDot)
      el.fleetDot.className = 'status-indicator-dot dot-inactive';
    if (el.fleetText)
      el.fleetText.textContent =
        'Unavailable — live vessel availability data is not connected';
    if (el.fleetDesc) {
      el.fleetDesc.textContent =
        freight?.vessel_availability?.reason ||
        'Live AIS fleet telemetry and commercial open tonnage lists are not connected (pelagos live_ais_connected: false). Static sample only covers coastal U.S. waters and is not interpreted as active fleet availability.';
    }
  }

  _hideVesselControls() {
    this.vesselsRaw = [];
    this.vesselsNormalized = [];
    this.clearSelectedVessel({ syncGlobe: false });
    if (this.elements.vesselMetrics)
      this.elements.vesselMetrics.style.display = 'none';
    if (this.elements.vesselFilterBar)
      this.elements.vesselFilterBar.style.display = 'none';
    if (this.elements.vesselListContainer)
      this.elements.vesselListContainer.style.display = 'none';
    if (this.elements.selectedVesselCard)
      this.elements.selectedVesselCard.style.display = 'none';
  }

  /** Compute deterministic voyage gross freight cost via backend API. */
  async calculateVoyageCost({ triggerOrigin, triggerDest } = {}) {
    const el = this.elements;
    const rawVal = el.cargoTonnesInput
      ? el.cargoTonnesInput.value
      : this.currentCargoTonnes;
    const tonnes = Number(rawVal);

    if (!Number.isFinite(tonnes) || tonnes <= 0) {
      this.showCostValidationMessage(
        'Cargo tonnage must be strictly positive (> 0 MT).',
      );
      if (el.costStatusTag) {
        el.costStatusTag.className = 'pelagos-tag tag-offline';
        el.costStatusTag.textContent = 'INVALID INPUT';
      }
      return false;
    }

    this.clearCostValidationMessage();
    this.currentCargoTonnes = tonnes;

    const originId = triggerOrigin || el.originSelect?.value;
    const destId = triggerDest || el.destSelect?.value;

    if (!originId || !destId) {
      this.showCostValidationMessage(
        'Please select both an origin and destination port first.',
      );
      return false;
    }

    this.setCostCalculatingState(true);
    this._costSeq += 1;
    const seq = this._costSeq;

    this._costAbortController?.abort();
    const controller = new AbortController();
    this._costAbortController = controller;

    try {
      const canalPolicy = el.canalPolicySelect?.value || 'allow';
      const payload = {
        origin: originId,
        destination: destId,
        cargo_tonnes: tonnes,
        canal_policy: canalPolicy,
      };

      const res = await fetch('/api/v1/calculator/voyage-freight', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });

      if (seq !== this._costSeq) return false; // Stale request discarded

      const data = await res.json();
      if (!res.ok) {
        throw new Error(
          data?.message || data?.error_message || `HTTP ${res.status}`,
        );
      }

      this.currentCostResult = data;
      this.renderCostResult(data);
      void this.matchSuitableVessels({
        triggerOrigin: originId,
        triggerDest: destId,
        cargoTonnes: tonnes,
      });
      return true;
    } catch (err) {
      if (seq !== this._costSeq) return false;
      if (err.name === 'AbortError') return false;

      this.renderCostError(
        err.message || 'Cost calculation service unavailable',
      );
      return false;
    } finally {
      if (seq === this._costSeq) {
        this.setCostCalculatingState(false);
      }
    }
  }

  /** Render structured voyage gross freight calculation outcome. */
  renderCostResult(data) {
    const el = this.elements;
    if (!el.costCard) return;

    if (el.costDisclaimers) {
      el.costDisclaimers.innerHTML = '';
    }

    if (
      data.status === 'calculated' &&
      Number.isFinite(Number(data.gross_freight_cost))
    ) {
      if (el.costStatusTag) {
        el.costStatusTag.className = 'pelagos-tag tag-verified';
        el.costStatusTag.textContent = 'ESTIMATED';
      }
      if (el.costContent) el.costContent.style.display = 'block';
      if (el.costEmpty) el.costEmpty.style.display = 'none';

      if (el.costVal) {
        el.costVal.textContent = Number(data.gross_freight_cost).toLocaleString(
          'en-US',
          {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          },
        );
      }
      if (el.costCurrency) {
        el.costCurrency.textContent = data.gross_freight_cost_currency || 'USD';
      }
      if (el.costFormula) {
        const rateVal = data.applicable_rate?.rate_value ?? 0;
        const cargoT = data.cargo_tonnes ?? this.currentCargoTonnes;
        el.costFormula.textContent = `Gross Ocean Freight: ${Number(cargoT).toLocaleString('en-US')} MT × $${Number(rateVal).toFixed(2)}/MT`;
      }
      if (el.costRoute) {
        const origLabel =
          data.origin_port_name || data.origin_port_id || 'Origin';
        const destLabel =
          data.destination_port_name ||
          data.destination_port_id ||
          'Destination';
        const corridorCode = data.corridor?.route_code
          ? ` · ${data.corridor.route_code}`
          : '';
        el.costRoute.textContent = `${origLabel} → ${destLabel}${corridorCode}`;
      }
      if (el.costRate && data.applicable_rate) {
        el.costRate.textContent = `$${Number(data.applicable_rate.rate_value).toFixed(2)} ${data.applicable_rate.unit || 'USD/MT'} (${data.applicable_rate.currency || 'USD'})`;
      }
      if (el.costCargo) {
        el.costCargo.textContent = `${Number(data.cargo_tonnes).toLocaleString('en-US')} ${data.cargo_unit || 'MT'}`;
      }
      if (el.costSource && data.applicable_rate) {
        el.costSource.textContent = `${data.applicable_rate.observation_date || '—'} · ${data.applicable_rate.source_name || 'USDA Benchmark'}`;
      }
      if (el.costRateMeta && data.applicable_rate) {
        const rateNature = data.applicable_rate.is_derived
          ? 'derived'
          : data.applicable_rate.rate_type || 'observed benchmark';
        const period = data.applicable_rate.observation_period || 'monthly';
        const dsId = data.applicable_rate.source_dataset_id || '—';
        const license = data.applicable_rate.license_type || '—';
        el.costRateMeta.textContent = `${rateNature} (${period}) · Dataset: ${dsId} · License: ${license}`;
      }
      if (el.costCanalPolicy) {
        el.costCanalPolicy.textContent =
          el.canalPolicySelect?.value ||
          this.currentRoute?.canalPolicyApplied ||
          'allow';
      }
      if (el.costDistance) {
        const distNm = data.distance?.distance_nm;
        const distKm = data.distance?.distance_km;
        const distSrc = data.distance?.distance_source || '';
        const srcBadge =
          distSrc === 'corridor_benchmark_distance'
            ? 'Benchmark'
            : distSrc === 'marnet_operational_routing' ||
                distSrc === 'eurostat_marnet_network'
              ? 'MARNET Routing'
              : 'Route';
        if (distNm !== undefined && distNm !== null) {
          const kmPart =
            distKm !== undefined && distKm !== null
              ? ` / ${Number(distKm).toLocaleString('en-US', { maximumFractionDigits: 0 })} km`
              : '';
          el.costDistance.textContent = `${Number(distNm).toLocaleString('en-US', { maximumFractionDigits: 1 })} NM${kmPart} (${srcBadge})`;
        } else {
          el.costDistance.textContent = '—';
        }
      }
      if (el.costDistProvenance) {
        const prov = data.distance?.provenance || {};
        const dSrc = data.distance?.distance_source || '';
        const parts = [
          dSrc || null,
          prov.dataset_name || null,
          prov.authority || null,
          prov.licensing || null,
          prov.qualification || null,
        ].filter(Boolean);
        if (
          prov.qualification ||
          prov.dataset_name ||
          prov.authority ||
          prov.licensing
        ) {
          el.costDistProvenance.textContent = parts.join(' · ');
        } else if (dSrc === 'corridor_benchmark_distance') {
          el.costDistProvenance.textContent =
            'corridor_benchmark_distance · Official Corridor Benchmark Reference';
        } else if (
          dSrc === 'marnet_operational_routing' ||
          dSrc === 'eurostat_marnet_network'
        ) {
          el.costDistProvenance.textContent = `${dSrc} · MARNET Sea-Lane Routing Network`;
        } else {
          el.costDistProvenance.textContent =
            'Prototype Benchmark (Unverified for navigation)';
        }
      }
      if (el.costDisclaimers) {
        const disclaimers =
          Array.isArray(data.disclaimers) && data.disclaimers.length > 0
            ? data.disclaimers
            : [
                'GROSS OCEAN FREIGHT ONLY: Calculated exclusively as (cargo tonnes × published freight rate per MT).',
                'Excludes bunker fuel surcharges, canal tolls, port disbursements, stevedoring, demurrage/despatch, and insurance.',
              ];
        disclaimers.forEach((disc) => {
          const li = this.doc.createElement('li');
          li.textContent = disc;
          el.costDisclaimers.appendChild(li);
        });
      }
    } else if (data.status === 'unavailable') {
      if (el.costStatusTag) {
        el.costStatusTag.className = 'pelagos-tag tag-unmonitored';
        el.costStatusTag.textContent = 'UNAVAILABLE';
      }
      if (el.costContent) el.costContent.style.display = 'none';
      if (el.costEmpty) {
        el.costEmpty.style.display = 'block';
        el.costEmpty.textContent =
          data.message ||
          'Voyage freight benchmark is unavailable for this unmonitored trade corridor.';
      }
    } else if (data.status === 'invalid_input') {
      if (el.costStatusTag) {
        el.costStatusTag.className = 'pelagos-tag tag-offline';
        el.costStatusTag.textContent = 'INVALID INPUT';
      }
      if (el.costContent) el.costContent.style.display = 'none';
      this.showCostValidationMessage(
        data.message || 'Invalid cargo tonnage or route parameters.',
      );
    }
  }

  /** Render cost calculation error state. */
  renderCostError(errorMsg) {
    const el = this.elements;
    if (el.costStatusTag) {
      el.costStatusTag.className = 'pelagos-tag tag-offline';
      el.costStatusTag.textContent = 'ERROR';
    }
    if (el.costContent) el.costContent.style.display = 'none';
    if (el.costDisclaimers) el.costDisclaimers.innerHTML = '';
    if (el.costEmpty) {
      el.costEmpty.style.display = 'block';
      el.costEmpty.textContent = `Cost calculation service error: ${errorMsg}`;
    }
  }

  /** Set candidate vessel suitability loading state */
  setSuitabilityLoadingState(loading) {
    this.isMatchingSuitability = loading;
    const el = this.elements;
    if (el.suitabilityTag && loading) {
      el.suitabilityTag.className = 'pelagos-tag tag-loading';
      el.suitabilityTag.textContent = 'MATCHING…';
    }
  }

  /**
   * Evaluate candidate vessel suitability for corridor and cargo tonnage via API (OPT-002).
   */
  async matchSuitableVessels({ triggerOrigin, triggerDest, cargoTonnes } = {}) {
    const el = this.elements;
    if (!el.suitabilityCard) return false;

    const tonnes = Number(
      cargoTonnes ||
        (el.cargoTonnesInput
          ? el.cargoTonnesInput.value
          : this.currentCargoTonnes),
    );
    if (!Number.isFinite(tonnes) || tonnes <= 0) {
      return false;
    }

    const originId = triggerOrigin || el.originSelect?.value;
    const destId = triggerDest || el.destSelect?.value;

    if (!originId || !destId) {
      if (el.suitabilityTag) {
        el.suitabilityTag.className = 'pelagos-tag tag-unmonitored';
        el.suitabilityTag.textContent = 'AWAITING ROUTE';
      }
      if (el.suitabilityContext) {
        el.suitabilityContext.textContent =
          'Select a freight corridor and cargo tonnage to evaluate physical vessel suitability.';
      }
      if (el.suitabilityMetrics) el.suitabilityMetrics.style.display = 'none';
      if (el.suitabilityListContainer)
        el.suitabilityListContainer.style.display = 'none';
      if (el.suitabilityEmpty) {
        el.suitabilityEmpty.style.display = 'block';
        el.suitabilityEmpty.textContent =
          'Calculate a corridor route above to match candidate vessels.';
      }
      return false;
    }

    this.setSuitabilityLoadingState(true);
    this._suitabilitySeq += 1;
    const seq = this._suitabilitySeq;

    this._suitabilityAbortController?.abort();
    const controller = new AbortController();
    this._suitabilityAbortController = controller;

    try {
      const canalPolicy = el.canalPolicySelect?.value || 'allow';
      const payload = {
        origin: originId,
        destination: destId,
        cargo_tonnes: tonnes,
        canal_policy: canalPolicy,
        max_vessels: 50,
      };

      const res = await fetch('/api/v1/calculator/vessel-suitability', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });

      if (seq !== this._suitabilitySeq) return false;

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || data?.message || `HTTP ${res.status}`);
      }

      this.currentSuitabilityResult = data;
      this.renderSuitabilityResult(data);
      return true;
    } catch (err) {
      if (seq !== this._suitabilitySeq) return false;
      if (err.name === 'AbortError') return false;

      this.renderSuitabilityError(
        err.message || 'Vessel suitability service unavailable',
      );
      return false;
    } finally {
      if (seq === this._suitabilitySeq) {
        this.setSuitabilityLoadingState(false);
      }
    }
  }

  /** Render structured candidate vessel suitability matches. */
  renderSuitabilityResult(data) {
    const el = this.elements;
    if (!el.suitabilityCard) return;

    if (el.suitabilityDisclaimers) {
      el.suitabilityDisclaimers.innerHTML = '';
      if (Array.isArray(data.disclaimers) && data.disclaimers.length > 0) {
        data.disclaimers.forEach((disc) => {
          const li = this.doc.createElement('li');
          li.textContent = disc;
          el.suitabilityDisclaimers.appendChild(li);
        });
      }
    }

    if (el.suitabilityHistoricalBanner && data.data_mode_notice) {
      const modeLabel =
        String(data.data_mode || 'historical').toLowerCase() === 'live'
          ? 'Live AIS Stream Notice:'
          : 'Historical Dataset Notice:';
      el.suitabilityHistoricalBanner.textContent = `ℹ ${modeLabel} ${data.data_mode_notice}`;
    }

    if (
      el.suitabilityCommercialDistinction &&
      data.ais_vs_commercial_distinction
    ) {
      el.suitabilityCommercialDistinction.textContent = `ℹ Commercial Scope & Registry Distinction: ${data.ais_vs_commercial_distinction}`;
    }

    if (el.suitabilityRouteCompat) {
      el.suitabilityRouteCompat.innerHTML = '';
      const rcs = data.route_compatibility_summary;
      if (rcs && typeof rcs === 'object') {
        el.suitabilityRouteCompat.style.display = 'block';
        const title = this.doc.createElement('div');
        title.className = 'route-compat-title';
        title.textContent = 'CORRIDOR & PORT COMPATIBILITY AUDIT';
        el.suitabilityRouteCompat.appendChild(title);

        const navRow = this.doc.createElement('div');
        navRow.className = 'route-compat-row';
        const navState =
          rcs.route_navigable === true
            ? 'NAVIGABLE'
            : rcs.route_navigable === false
              ? 'BLOCKED / NOT NAVIGABLE'
              : 'UNVERIFIED';
        const canalsText =
          Array.isArray(rcs.canals_transited) && rcs.canals_transited.length > 0
            ? rcs.canals_transited.join(', ')
            : 'None';
        const distPart =
          rcs.route_distance_nm !== null && rcs.route_distance_nm !== undefined
            ? ` · ${Number(rcs.route_distance_nm).toLocaleString('en-US', { maximumFractionDigits: 1 })} NM`
            : '';
        navRow.textContent = `Routing: ${navState} (${rcs.routing_status || '—'})${distPart} · Canals: ${canalsText}`;
        el.suitabilityRouteCompat.appendChild(navRow);

        const origDraft =
          rcs.origin_documented_draft_limit_meters !== null &&
          rcs.origin_documented_draft_limit_meters !== undefined
            ? `${Number(rcs.origin_documented_draft_limit_meters).toFixed(1)}m (${rcs.origin_limit_source || 'documented'})`
            : `Unverified (${rcs.origin_limit_source || 'no port draft limit documented'})`;
        const destDraft =
          rcs.destination_documented_draft_limit_meters !== null &&
          rcs.destination_documented_draft_limit_meters !== undefined
            ? `${Number(rcs.destination_documented_draft_limit_meters).toFixed(1)}m (${rcs.destination_limit_source || 'documented'})`
            : `Unverified (${rcs.destination_limit_source || 'no port draft limit documented'})`;

        const draftRow = this.doc.createElement('div');
        draftRow.className = 'route-compat-row';
        draftRow.textContent = `Origin Port Draft Limit: ${origDraft} · Destination Port Draft Limit: ${destDraft}`;
        el.suitabilityRouteCompat.appendChild(draftRow);
      } else {
        el.suitabilityRouteCompat.style.display = 'none';
      }
    }

    if (
      data.status === 'matched' ||
      data.status === 'unmonitored_corridor_matched'
    ) {
      const isMonitored = data.status === 'matched';
      const suitableCount = Number(data.potentially_suitable_count || 0);
      const totalCount = Number(data.total_vessels_evaluated || 0);
      const incompatibleCount = Math.max(0, totalCount - suitableCount);

      if (el.suitabilityTag) {
        el.suitabilityTag.className = isMonitored
          ? suitableCount > 0
            ? 'pelagos-tag tag-verified'
            : 'pelagos-tag tag-warning'
          : 'pelagos-tag tag-unmonitored';
        el.suitabilityTag.textContent = isMonitored
          ? `${suitableCount} POTENTIALLY SUITABLE`
          : 'UNMONITORED CORRIDOR';
      }

      if (el.suitabilityContext) {
        const loadName =
          data.loading_port_name || data.loading_port_id || 'Origin';
        const corridorTitle = data.corridor?.route_name || 'Selected Sea Route';
        const vesselClassStr = data.target_vessel_class
          ? ` (${data.target_vessel_class})`
          : '';
        const modeTag = data.data_mode ? ` · Mode: ${data.data_mode}` : '';
        el.suitabilityContext.textContent = `${corridorTitle} · Loading: ${loadName} · Target: ${Number(data.cargo_tonnes).toLocaleString('en-US')} MT${vesselClassStr}${modeTag}`;
      }

      if (el.suitabilityMetrics) el.suitabilityMetrics.style.display = 'grid';
      if (el.suitabilityTotalCount)
        el.suitabilityTotalCount.textContent =
          totalCount.toLocaleString('en-US');
      if (el.suitabilityMatchedCount)
        el.suitabilityMatchedCount.textContent =
          suitableCount.toLocaleString('en-US');
      if (el.suitabilityIncompatibleCount)
        el.suitabilityIncompatibleCount.textContent =
          incompatibleCount.toLocaleString('en-US');

      const candidates = Array.isArray(data.candidates) ? data.candidates : [];
      if (el.suitabilityList) {
        el.suitabilityList.innerHTML = '';

        if (candidates.length === 0) {
          if (el.suitabilityListContainer)
            el.suitabilityListContainer.style.display = 'none';
          if (el.suitabilityEmpty) {
            el.suitabilityEmpty.style.display = 'block';
            el.suitabilityEmpty.textContent =
              data.message ||
              'No vessel telemetry records evaluated for this route.';
          }
        } else {
          if (el.suitabilityListContainer)
            el.suitabilityListContainer.style.display = 'block';
          if (el.suitabilityEmpty) el.suitabilityEmpty.style.display = 'none';

          candidates.forEach((cand) => {
            const item = this.doc.createElement('div');
            item.className = 'pelagos-candidate-item';
            if (
              this.selectedMmsi &&
              String(cand.mmsi) === String(this.selectedMmsi)
            ) {
              item.classList.add('is-selected');
            }
            if (!item.dataset) item.dataset = {};
            item.dataset.mmsi = String(cand.mmsi);

            // Top row: name, MMSI, and status badge
            const topRow = this.doc.createElement('div');
            topRow.className = 'candidate-item-top';

            const nameGroup = this.doc.createElement('div');
            nameGroup.className = 'candidate-name-group';

            const nameEl = this.doc.createElement('span');
            nameEl.className = 'candidate-name';
            nameEl.textContent = cand.vessel_name || `MMSI ${cand.mmsi}`;
            nameGroup.appendChild(nameEl);

            const idMeta = this.doc.createElement('span');
            idMeta.className = 'candidate-id-meta';
            const imoStr = cand.imo ? ` · IMO: ${cand.imo}` : '';
            const classStr = cand.estimated_dimensional_class
              ? ` · ${cand.estimated_dimensional_class}`
              : '';
            const regClassStr = cand.registry_vessel_class
              ? ` (Registry: ${cand.registry_vessel_class})`
              : '';
            idMeta.textContent = `MMSI: ${cand.mmsi}${imoStr}${classStr}${regClassStr}`;
            nameGroup.appendChild(idMeta);
            topRow.appendChild(nameGroup);

            const badge = this.doc.createElement('span');
            if (cand.suitability_status === 'potentially_suitable') {
              badge.className = 'pelagos-tag tag-verified';
              badge.textContent = 'POTENTIALLY SUITABLE';
            } else if (cand.suitability_status === 'insufficient_data') {
              badge.className = 'pelagos-tag tag-warning';
              badge.textContent = 'INSUFFICIENT DATA';
            } else if (cand.suitability_status === 'dimension_mismatch') {
              badge.className = 'pelagos-tag tag-unmonitored';
              badge.textContent = 'DIMENSION MISMATCH';
            } else {
              badge.className = 'pelagos-tag tag-offline';
              badge.textContent = 'UNSUITABLE TYPE';
            }
            topRow.appendChild(badge);
            item.appendChild(topRow);

            // Metrics row: Dimensions, Distance to Loading Port, Speed, Age
            const metricsRow = this.doc.createElement('div');
            metricsRow.className = 'candidate-metrics-row';

            if (
              cand.distance_to_load_port_nm !== null &&
              cand.distance_to_load_port_nm !== undefined
            ) {
              const distEl = this.doc.createElement('span');
              const portLabel =
                data.loading_port_name || data.loading_port_id || 'load port';
              const distFormatted = Number(
                cand.distance_to_load_port_nm,
              ).toLocaleString('en-US', {
                minimumFractionDigits: 1,
                maximumFractionDigits: 1,
              });
              if (cand.is_stale) {
                distEl.className = 'candidate-distance distance-historical';
                distEl.textContent = `⚓ ${distFormatted} NM at fix (${portLabel} · Historical)`;
                distEl.title =
                  cand.distance_qualification ||
                  'Historical observation distance; current proximity unknown.';
              } else {
                distEl.className = 'candidate-distance';
                distEl.textContent = `⚓ ${distFormatted} NM to ${portLabel} (Live fix)`;
              }
              metricsRow.appendChild(distEl);
            }

            const dimEl = this.doc.createElement('span');
            const loaStr = cand.length_meters
              ? `${Math.round(cand.length_meters)}m LOA`
              : 'LOA: —';
            const beamStr = cand.width_meters
              ? `${Math.round(cand.width_meters)}m Beam`
              : 'Beam: —';
            const draftStr = cand.draft_meters
              ? `${Number(cand.draft_meters).toFixed(1)}m Draft`
              : 'Draft: —';
            dimEl.textContent = `${loaStr} · ${beamStr} · ${draftStr}`;
            metricsRow.appendChild(dimEl);

            const speedEl = this.doc.createElement('span');
            const spd =
              cand.speed_knots !== null && cand.speed_knots !== undefined
                ? `${Number(cand.speed_knots).toFixed(1)} kn`
                : 'Speed: —';
            const crs =
              cand.course_degrees !== null && cand.course_degrees !== undefined
                ? `${Math.round(cand.course_degrees)}°`
                : '—';
            speedEl.textContent = `${spd} · ${crs}`;
            metricsRow.appendChild(speedEl);

            const ageEl = this.doc.createElement('span');
            const ageSecsStr =
              cand.observation_age_seconds !== null &&
              cand.observation_age_seconds !== undefined
                ? ` · ${Math.round(cand.observation_age_seconds / 3600)}h age`
                : '';
            if (cand.is_stale) {
              ageEl.className = 'vessel-age stale';
              const dateStr = cand.observation_time
                ? cand.observation_time.split('T')[0]
                : 'Historical';
              ageEl.textContent = `Historical Fix (${dateStr} · Stale${ageSecsStr})`;
            } else {
              ageEl.className = 'vessel-age live';
              ageEl.textContent = `Live Fix (<30m)${ageSecsStr}`;
            }
            metricsRow.appendChild(ageEl);

            item.appendChild(metricsRow);

            // Reason explanation box
            const reasonBox = this.doc.createElement('div');
            reasonBox.className = `candidate-reason-box ${cand.suitability_status === 'potentially_suitable' ? 'suitable' : ''}`;
            const prefix =
              cand.suitability_status === 'potentially_suitable'
                ? '✓ '
                : cand.suitability_status === 'insufficient_data'
                  ? '⚠ '
                  : '✕ ';
            reasonBox.textContent = `${prefix}${cand.suitability_reason}`;
            item.appendChild(reasonBox);

            // Sub-badges row: DWT Capacity & Commercial / Laycan Boundaries
            const metaBadgesRow = this.doc.createElement('div');
            metaBadgesRow.className = 'candidate-meta-badges';

            const hasVerifiedDwt =
              cand.verified_cargo_capacity_dwt !== null &&
              cand.verified_cargo_capacity_dwt !== undefined &&
              cand.cargo_capacity_status !== 'UNVERIFIED';
            const capacityPill = this.doc.createElement('span');
            capacityPill.className = hasVerifiedDwt
              ? 'candidate-sub-pill capacity-verified'
              : 'candidate-sub-pill capacity-unverified';
            capacityPill.textContent = hasVerifiedDwt
              ? `Verified DWT: ${Number(cand.verified_cargo_capacity_dwt).toLocaleString('en-US')} t`
              : 'DWT Capacity: UNVERIFIED (AIS Broadcast Only)';
            metaBadgesRow.appendChild(capacityPill);

            const laycanPill = this.doc.createElement('span');
            laycanPill.className = 'candidate-sub-pill laycan-unconfirmed';
            laycanPill.textContent = `Commercial: ${cand.commercial_availability || 'UNCONFIRMED'} · Laycan: ${cand.laycan_feasibility || 'UNCONFIRMED'}`;
            metaBadgesRow.appendChild(laycanPill);

            item.appendChild(metaBadgesRow);

            // Three-Layer Compatibility & Particulars Completeness Audit Box
            const layersBox = this.doc.createElement('div');
            layersBox.className = 'candidate-layers-box';

            const compStatus =
              cand.particulars_completeness?.completeness_status ||
              cand.screening_stage ||
              'partial';
            const missingParts =
              Array.isArray(cand.missing_particulars) &&
              cand.missing_particulars.length > 0
                ? cand.missing_particulars
                : Array.isArray(
                      cand.particulars_completeness?.missing_particulars,
                    )
                  ? cand.particulars_completeness.missing_particulars
                  : [];

            const partLine = this.doc.createElement('div');
            partLine.className = 'candidate-layer-row';
            partLine.textContent = `Particulars (${compStatus}): Missing [${missingParts.length > 0 ? missingParts.join(', ') : 'None'}]`;
            layersBox.appendChild(partLine);

            if (cand.cargo_compatibility) {
              const cc = cand.cargo_compatibility;
              const cargoLine = this.doc.createElement('div');
              cargoLine.className = 'candidate-layer-row';
              const dwtStr =
                cc.verified_dwt_tonnes !== null &&
                cc.verified_dwt_tonnes !== undefined
                  ? `${Number(cc.verified_dwt_tonnes).toLocaleString('en-US')} t`
                  : 'UNVERIFIED';
              const ccReasons =
                Array.isArray(cc.reasons) && cc.reasons.length > 0
                  ? ` — ${cc.reasons.join('; ')}`
                  : '';
              cargoLine.textContent = `Cargo Layer: ${cc.status} (Basis: ${cc.capacity_check_basis || '—'}, DWT: ${dwtStr})${ccReasons}`;
              layersBox.appendChild(cargoLine);
            }

            if (cand.route_compatibility) {
              const rc = cand.route_compatibility;
              const routeLine = this.doc.createElement('div');
              routeLine.className = 'candidate-layer-row';
              const oStat = rc.origin_port?.status || '—';
              const dStat = rc.destination_port?.status || '—';
              const canalNotes =
                Array.isArray(rc.canal_compatibility_notes) &&
                rc.canal_compatibility_notes.length > 0
                  ? ` · ${rc.canal_compatibility_notes.join('; ')}`
                  : '';
              const rcReasons =
                Array.isArray(rc.reasons) && rc.reasons.length > 0
                  ? ` — ${rc.reasons.join('; ')}`
                  : '';
              routeLine.textContent = `Route/Port Layer: ${rc.status} (Load: ${oStat}, Discharge: ${dStat})${canalNotes}${rcReasons}`;
              layersBox.appendChild(routeLine);
            }

            const regStat = cand.registry_commercial_status || 'untracked';
            const commLine = this.doc.createElement('div');
            commLine.className = 'candidate-layer-row';
            commLine.textContent = `Registry Sync: "${regStat}" (AIS registry sync indicator ONLY, NOT commercial open tonnage) · Data Mode: ${cand.data_mode || data.data_mode || 'historical'}`;
            layersBox.appendChild(commLine);

            if (
              cand.commercial_availability_note ||
              cand.laycan_feasibility_note
            ) {
              const noteLine = this.doc.createElement('div');
              noteLine.className = 'candidate-layer-row is-muted';
              noteLine.textContent =
                cand.commercial_availability_note ||
                cand.laycan_feasibility_note;
              layersBox.appendChild(noteLine);
            }

            item.appendChild(layersBox);

            // Selection & camera interaction
            item.addEventListener('click', () => {
              this.selectVessel(cand.mmsi, {
                syncGlobe: true,
                flyCamera: true,
              });
            });

            el.suitabilityList.appendChild(item);
          });
        }
      }
    } else if (
      data.status === 'unavailable' ||
      data.status === 'invalid_input'
    ) {
      const isUnavailable = data.status === 'unavailable';
      if (el.suitabilityTag) {
        el.suitabilityTag.className = isUnavailable
          ? 'pelagos-tag tag-unmonitored'
          : 'pelagos-tag tag-offline';
        el.suitabilityTag.textContent = isUnavailable
          ? 'UNAVAILABLE'
          : 'INVALID INPUT';
      }
      if (el.suitabilityContext) {
        el.suitabilityContext.textContent =
          data.message ||
          (isUnavailable
            ? 'Candidate vessel suitability matching is unavailable for this route.'
            : 'Invalid parameters for candidate vessel suitability matching.');
      }
      if (el.suitabilityMetrics) el.suitabilityMetrics.style.display = 'none';
      if (el.suitabilityListContainer)
        el.suitabilityListContainer.style.display = 'none';
      if (el.suitabilityEmpty) {
        el.suitabilityEmpty.style.display = 'block';
        el.suitabilityEmpty.textContent =
          data.message ||
          (isUnavailable
            ? 'Candidate vessel suitability matching is unavailable for this route.'
            : 'Invalid parameters for candidate vessel suitability matching.');
      }
    }
  }

  /** Render candidate vessel suitability error state. */
  renderSuitabilityError(errorMsg) {
    const el = this.elements;
    if (el.suitabilityTag) {
      el.suitabilityTag.className = 'pelagos-tag tag-offline';
      el.suitabilityTag.textContent = 'ERROR';
    }
    if (el.suitabilityRouteCompat)
      el.suitabilityRouteCompat.style.display = 'none';
    if (el.suitabilityMetrics) el.suitabilityMetrics.style.display = 'none';
    if (el.suitabilityListContainer)
      el.suitabilityListContainer.style.display = 'none';
    if (el.suitabilityDisclaimers) el.suitabilityDisclaimers.innerHTML = '';
    if (el.suitabilityEmpty) {
      el.suitabilityEmpty.style.display = 'block';
      el.suitabilityEmpty.textContent = `Candidate suitability service error: ${errorMsg}`;
    }
  }

  /** Switch between All Tracked and Near Corridor filter tabs */
  setVesselTab(tab) {
    if (this.vesselFilterTab === tab) return;
    this.vesselFilterTab = tab;
    if (this.elements.tabAllBtn) {
      this.elements.tabAllBtn.classList.toggle('active', tab === 'all');
    }
    if (this.elements.tabCorridorBtn) {
      this.elements.tabCorridorBtn.classList.toggle(
        'active',
        tab === 'corridor',
      );
    }
    this.refreshVesselData();
  }

  /** Refresh vessel deduplication, route proximity, metrics, and list rendering */
  refreshVesselData() {
    if (!this.vesselsRaw || this.vesselsRaw.length === 0) return;

    const { vessels, totalCount, corridorCount } = deduplicateAndFilterVessels(
      this.vesselsRaw,
      {
        activeRoute: this.currentRoute,
        searchQuery: this.vesselSearchQuery,
        tab: this.vesselFilterTab,
      },
    );
    this.vesselsNormalized = vessels;

    if (this.elements.globalVesselCount) {
      this.elements.globalVesselCount.textContent = (
        this.totalLiveCount ||
        totalCount ||
        this.vesselsRaw.length
      ).toLocaleString();
    }
    if (this.elements.routeVesselCount) {
      this.elements.routeVesselCount.textContent = this.currentRoute
        ? corridorCount.toLocaleString()
        : '—';
    }
    if (this.elements.tabAllCount) {
      this.elements.tabAllCount.textContent = String(totalCount);
    }
    if (this.elements.tabCorridorCount) {
      this.elements.tabCorridorCount.textContent = this.currentRoute
        ? String(corridorCount)
        : '—';
    }

    this.renderVesselList();

    if (this.selectedMmsi) {
      const stillExists = this.vesselsNormalized.some(
        (v) => String(v.mmsi) === String(this.selectedMmsi),
      );
      if (stillExists) {
        this.renderSelectedVesselCard();
      } else {
        const allVessels = deduplicateAndFilterVessels(this.vesselsRaw, {
          activeRoute: this.currentRoute,
        }).vessels;
        const existsInRaw = allVessels.find(
          (v) => String(v.mmsi) === String(this.selectedMmsi),
        );
        if (existsInRaw) {
          this.renderSelectedVesselCard(existsInRaw);
        } else {
          // Vessel aged out or disappeared from live cache
          this.clearSelectedVessel({ syncGlobe: false });
        }
      }
    }
  }

  /** Render the scrollable vessel list with rich telemetry items */
  renderVesselList() {
    const el = this.elements;
    if (!el.vesselList) return;
    el.vesselList.innerHTML = '';

    const list = this.vesselsNormalized || [];
    if (list.length === 0) {
      if (el.vesselEmpty) {
        el.vesselEmpty.style.display = 'block';
        if (this.vesselFilterTab === 'corridor' && !this.currentRoute) {
          el.vesselEmpty.textContent =
            'Calculate a route first to identify vessels operating near the corridor.';
        } else if (this.vesselFilterTab === 'corridor') {
          el.vesselEmpty.textContent =
            'No live vessels detected within 50 NM of the active route corridor.';
        } else if (this.vesselSearchQuery) {
          el.vesselEmpty.textContent = `No tracked vessels match "${this.vesselSearchQuery}".`;
        } else {
          el.vesselEmpty.textContent =
            'No tracked vessels currently available.';
        }
      }
      return;
    }

    if (el.vesselEmpty) el.vesselEmpty.style.display = 'none';

    list.forEach((v) => {
      const item = this.doc.createElement('div');
      item.className = 'pelagos-vessel-item';
      if (this.selectedMmsi && String(v.mmsi) === String(this.selectedMmsi)) {
        item.classList.add('selected');
        item.classList.add('is-selected');
      }
      if (!item.dataset) item.dataset = {};
      item.dataset.mmsi = String(v.mmsi);

      // Top row: name and type tag
      const topRow = this.doc.createElement('div');
      topRow.className = 'vessel-item-top';

      const nameEl = this.doc.createElement('span');
      nameEl.className = 'vessel-name';
      nameEl.textContent = v.displayName;
      topRow.appendChild(nameEl);

      const typeTag = this.doc.createElement('span');
      typeTag.className = 'vessel-tag';
      typeTag.textContent = v.displayType;
      topRow.appendChild(typeTag);
      item.appendChild(topRow);

      // Sub row: MMSI and relative age
      const subRow = this.doc.createElement('div');
      subRow.className = 'vessel-item-sub';

      const mmsiEl = this.doc.createElement('span');
      mmsiEl.className = 'vessel-mmsi';
      mmsiEl.textContent = `MMSI: ${v.mmsi}`;
      subRow.appendChild(mmsiEl);

      const ageEl = this.doc.createElement('span');
      ageEl.className = `vessel-age ${v.isStale ? 'stale' : ''}`;
      ageEl.textContent = v.ageFormatted;
      subRow.appendChild(ageEl);
      item.appendChild(subRow);

      // Detail row: Motion & Proximity
      const detailRow = this.doc.createElement('div');
      detailRow.className = 'vessel-item-detail';

      const motionEl = this.doc.createElement('span');
      motionEl.className = 'vessel-motion';
      motionEl.textContent = `${v.speedFormatted} · ${v.courseFormatted}`;
      detailRow.appendChild(motionEl);

      if (v.isNearCorridor && v.distanceToRouteNm !== null) {
        const proxEl = this.doc.createElement('span');
        proxEl.className = 'vessel-proximity';
        proxEl.textContent = `⟵ ${v.distanceToRouteNm.toFixed(1)} NM`;
        detailRow.appendChild(proxEl);
      }
      item.appendChild(detailRow);

      item.addEventListener('click', () => {
        this.selectVessel(v.mmsi, { syncGlobe: true, flyCamera: true });
      });

      el.vesselList.appendChild(item);
    });
  }

  /** Select a vessel by MMSI, update the inspector card, and sync with Cesium globe */
  selectVessel(mmsi, { syncGlobe = true, flyCamera = true } = {}) {
    if (!mmsi) return;
    this.selectedMmsi = String(mmsi);

    let vessel = this.vesselsNormalized.find(
      (v) => String(v.mmsi) === this.selectedMmsi,
    );
    if (!vessel && this.vesselsRaw?.length > 0) {
      const all = deduplicateAndFilterVessels(this.vesselsRaw, {
        activeRoute: this.currentRoute,
      }).vessels;
      vessel = all.find((v) => String(v.mmsi) === this.selectedMmsi);
    }

    if (!vessel) return;

    if (this.elements.vesselList) {
      const items = this.elements.vesselList.querySelectorAll
        ? this.elements.vesselList.querySelectorAll('.pelagos-vessel-item')
        : [];
      for (const item of items) {
        const isSelected = item.dataset?.mmsi === this.selectedMmsi;
        if (item.classList?.toggle) {
          item.classList.toggle('selected', isSelected);
          item.classList.toggle('is-selected', isSelected);
        }
      }
    }

    this.renderSelectedVesselCard(vessel);

    if (syncGlobe) {
      this.services.aisLiveVesselsLayer?.selectById?.(vessel.mmsi);
      if (flyCamera) {
        this.focusVesselOnGlobe(vessel.mmsi);
      }
    }
  }

  /** Clear selected vessel state and deselect on Cesium layer */
  clearSelectedVessel({ syncGlobe = true } = {}) {
    this.selectedMmsi = null;

    if (this.elements.selectedVesselCard) {
      this.elements.selectedVesselCard.style.display = 'none';
    }

    if (this.elements.vesselList) {
      const items = this.elements.vesselList.querySelectorAll
        ? this.elements.vesselList.querySelectorAll('.pelagos-vessel-item')
        : [];
      for (const item of items) {
        if (item.classList?.remove) {
          item.classList.remove('selected');
          item.classList.remove('is-selected');
        }
      }
    }

    if (syncGlobe) {
      this.services.aisLiveVesselsLayer?.clearSelection?.();
    }
  }

  /** Focus the Cesium globe camera onto a tracked vessel */
  focusVesselOnGlobe(mmsi) {
    if (!mmsi) return;
    const targetMmsi = String(mmsi);
    let vessel = this.vesselsNormalized.find(
      (v) => String(v.mmsi) === targetMmsi,
    );
    if (!vessel && this.vesselsRaw?.length > 0) {
      const all = deduplicateAndFilterVessels(this.vesselsRaw, {
        activeRoute: this.currentRoute,
      }).vessels;
      vessel = all.find((v) => String(v.mmsi) === targetMmsi);
    }
    if (!vessel) return;

    if (this.services.worldFocus?.requestWorldFocus) {
      this.services.worldFocus.requestWorldFocus({
        kind: 'vessel',
        id: vessel.mmsi,
        label: vessel.displayName,
        position: {
          latitude: vessel.lat,
          longitude: vessel.lon,
          altitude: 25000,
        },
      });
    }

    if (
      this.viewer?.camera?.flyTo &&
      Number.isFinite(vessel.lat) &&
      Number.isFinite(vessel.lon)
    ) {
      try {
        this.viewer.camera.flyTo({
          destination: Cesium.Cartesian3.fromDegrees(
            vessel.lon,
            vessel.lat,
            25000,
          ),
          duration: 1.5,
        });
      } catch {
        /* best effort */
      }
    }
  }

  /** Render the Selected Vessel inspector card with complete telemetry fields */
  renderSelectedVesselCard(vesselRecord = null) {
    const el = this.elements;
    if (!el.selectedVesselCard) return;

    const v =
      vesselRecord ||
      this.vesselsNormalized.find(
        (item) => String(item.mmsi) === String(this.selectedMmsi),
      );

    if (!v) {
      el.selectedVesselCard.style.display = 'none';
      return;
    }

    el.selectedVesselCard.style.display = 'block';

    if (el.selectedVesselName)
      el.selectedVesselName.textContent = v.displayName;
    if (el.selectedVesselMmsi) el.selectedVesselMmsi.textContent = v.mmsi;
    if (el.selectedVesselImo) el.selectedVesselImo.textContent = v.imo;
    if (el.selectedVesselType)
      el.selectedVesselType.textContent = v.type || 'Not reported';
    if (el.selectedVesselDest)
      el.selectedVesselDest.textContent = v.destination;
    if (el.selectedVesselCoords)
      el.selectedVesselCoords.textContent = formatVesselCoords(v.lat, v.lon);
    if (el.selectedVesselMotion)
      el.selectedVesselMotion.textContent = `${v.speedFormatted} · ${v.courseFormatted}`;
    if (el.selectedVesselHeading)
      el.selectedVesselHeading.textContent = v.headingFormatted;
    if (el.selectedVesselNav) el.selectedVesselNav.textContent = v.navStatus;
    if (el.selectedVesselTime) {
      if (v.lastPositionUtc && v.lastPositionUtc !== 'Not reported') {
        el.selectedVesselTime.textContent = `${v.lastPositionUtc} (${v.ageFormatted})`;
      } else if (v.ageFormatted && v.ageFormatted !== 'Not reported') {
        el.selectedVesselTime.textContent = v.ageFormatted;
      } else {
        el.selectedVesselTime.textContent = 'Not reported';
      }
    }

    if (el.selectedCorridorRow && el.selectedVesselCorridor) {
      if (v.distanceToRouteNm !== null && this.currentRoute) {
        el.selectedCorridorRow.style.display = 'flex';
        el.selectedVesselCorridor.textContent = `${v.distanceToRouteNm.toFixed(1)} NM ${
          v.isNearCorridor ? '(Within 50 NM Corridor)' : '(Outside Corridor)'
        }`;
      } else {
        el.selectedCorridorRow.style.display = 'none';
      }
    }
  }

  /** Remove route entities from Cesium globe. */
  clearGlobeGraphics() {
    if (this.viewer?.entities) {
      for (const entity of this.routeEntities) {
        try {
          this.viewer.entities.remove(entity);
        } catch {
          /* removal is best-effort */
        }
      }
    }
    this.routeEntities = [];
    this.actions?.releaseContinuousRender?.('pelagos-route');
    this.actions?.governorRequestRender?.('pelagos-route');
  }

  /** Reset all selections, globe graphics, and dashboard cards. */
  clearRoute() {
    this.clearGlobeGraphics();
    this.currentRoute = null;
    this.currentFreight = null;
    this.currentCostResult = null;
    this.currentSuitabilityResult = null;
    this._costAbortController?.abort();
    this._suitabilityAbortController?.abort();

    if (this.elements.suitabilityRouteCompat) {
      this.elements.suitabilityRouteCompat.style.display = 'none';
      this.elements.suitabilityRouteCompat.innerHTML = '';
    }
    if (this.elements.suitabilityDisclaimers) {
      this.elements.suitabilityDisclaimers.innerHTML = '';
    }
    if (this.elements.suitabilityMetrics) {
      this.elements.suitabilityMetrics.style.display = 'none';
    }
    if (this.elements.suitabilityListContainer) {
      this.elements.suitabilityListContainer.style.display = 'none';
    }
    if (this.elements.suitabilityEmpty) {
      this.elements.suitabilityEmpty.style.display = 'block';
      this.elements.suitabilityEmpty.textContent =
        'Calculate a corridor route above to match candidate vessels.';
    }
    if (this.elements.suitabilityTag) {
      this.elements.suitabilityTag.className = 'pelagos-tag tag-unmonitored';
      this.elements.suitabilityTag.textContent = 'AWAITING ROUTE';
    }

    if (this.elements.forecastHorizonsList) {
      this.elements.forecastHorizonsList.innerHTML = '';
    }
    if (this.elements.envStations) this.elements.envStations.innerHTML = '';
    if (this.elements.envContent)
      this.elements.envContent.style.display = 'none';
    if (this.elements.envEmpty) {
      this.elements.envEmpty.style.display = 'block';
      this.elements.envEmpty.textContent =
        'No buoy environmental observations loaded.';
    }
    if (this.elements.envTag) {
      this.elements.envTag.className = 'pelagos-tag tag-unmonitored';
      this.elements.envTag.textContent = 'UNAVAILABLE';
    }
    if (this.elements.bunkerHubs) this.elements.bunkerHubs.innerHTML = '';
    if (this.elements.bunkerBenchmarks)
      this.elements.bunkerBenchmarks.innerHTML = '';
    if (this.elements.bunkerContent)
      this.elements.bunkerContent.style.display = 'none';
    if (this.elements.bunkerEmpty) {
      this.elements.bunkerEmpty.style.display = 'block';
      this.elements.bunkerEmpty.textContent =
        'No bunker fuel reference quotes loaded.';
    }
    if (this.elements.bunkerTag) {
      this.elements.bunkerTag.className = 'pelagos-tag tag-unmonitored';
      this.elements.bunkerTag.textContent = 'UNAVAILABLE';
    }

    if (this.elements.costDisclaimers) {
      this.elements.costDisclaimers.innerHTML = '';
    }
    if (this.elements.costContent) {
      this.elements.costContent.style.display = 'none';
    }
    if (this.elements.costEmpty) {
      this.elements.costEmpty.style.display = 'none';
    }
    if (this.elements.costStatusTag) {
      this.elements.costStatusTag.className = 'pelagos-tag tag-model';
      this.elements.costStatusTag.textContent = 'CALCULATOR';
    }
    this.clearCostValidationMessage();

    if (this.elements.dashboard) {
      this.elements.dashboard.style.display = 'none';
    }
    this.clearStatusBanner();
    this.clearValidationMessage();

    if (this.elements.originSelect) this.elements.originSelect.value = '';
    if (this.elements.destSelect) this.elements.destSelect.value = '';
    if (this.elements.originFilter) this.elements.originFilter.value = '';
    if (this.elements.destFilter) this.elements.destFilter.value = '';
    if (this.elements.canalPolicySelect)
      this.elements.canalPolicySelect.value = 'allow';

    this.filterPortOptions(this.elements.originSelect, '');
    this.filterPortOptions(this.elements.destSelect, '');
    this.assessment?.clearResult?.();
    this.readiness?.clearResult?.();

    // Refresh vessel corridor proximity calculations without active route
    if (this.vesselsRaw && this.vesselsRaw.length > 0) {
      this.refreshVesselData();
    }
  }

  /** Teardown all event listeners, graphics, and abort pending requests. */
  destroy() {
    this.overview?.destroy();
    this.assessment?.destroy();
    this.readiness?.destroy();
    this._abortController?.abort();
    this._costAbortController?.abort();
    this._suitabilityAbortController?.abort();
    this.clearGlobeGraphics();

    for (const { element, event, handler } of this._boundListeners) {
      try {
        element.removeEventListener(event, handler);
      } catch {
        /* listener cleanup is best effort */
      }
    }
    this._boundListeners = [];

    for (const { event, handler } of this._windowListeners) {
      try {
        if (typeof window !== 'undefined' && window.removeEventListener) {
          window.removeEventListener(event, handler);
        }
      } catch {
        /* listener cleanup is best effort */
      }
    }
    this._windowListeners = [];

    this.clearSelectedVessel({ syncGlobe: true });
    this.vesselsRaw = [];
    this.vesselsNormalized = [];
  }
}
