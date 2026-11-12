/**
 * @module ui/pelagosAssessment
 * @description Pelagos D2.1 Single-Vessel Charter Assessment controller and renderer.
 * Integrates GET/POST `/api/v1/charter/assess` and `/api/v1/platform-data/explorer/vessels`.
 */

import { assessVesselCharter, fetchRegisteredVessels } from './pelagosApi.js';

function formatNum(val, digits = 1) {
  if (val === null || val === undefined || !Number.isFinite(Number(val))) {
    return '—';
  }
  return Number(val).toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function formatAgeSeconds(ageSec) {
  if (
    ageSec === null ||
    ageSec === undefined ||
    !Number.isFinite(Number(ageSec))
  ) {
    return 'Age unknown';
  }
  const secs = Math.max(0, Number(ageSec));
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400 * 2) return `${(secs / 3600).toFixed(1)}h ago`;
  return `${Math.round(secs / 86400).toLocaleString('en-US')}d ago`;
}

/**
 * Derive an explicit, honest D2.1 assessment outcome classification from the
 * backend `VesselCompatibilityResult` payload without inventing any score or
 * claiming commercial readiness from physical screening.
 */
export function classifyAssessmentOutcome(data) {
  if (!data || typeof data !== 'object') {
    return {
      key: 'unavailable',
      label: 'UNAVAILABLE',
      tagClass: 'pelagos-tag tag-unmonitored',
      commercialNotice:
        'Assessment payload is unavailable. Physical and commercial readiness cannot be evaluated.',
    };
  }

  if (data.status === 'vessel_not_found') {
    return {
      key: 'vessel_not_found',
      label: 'UNAVAILABLE · VESSEL NOT FOUND',
      tagClass: 'pelagos-tag tag-unmonitored',
      commercialNotice:
        data.message ||
        'Selected vessel MMSI was not found in registered vessel records or AIS observations.',
    };
  }

  if (data.status === 'unavailable') {
    return {
      key: 'unavailable',
      label: 'UNAVAILABLE',
      tagClass: 'pelagos-tag tag-unmonitored',
      commercialNotice:
        data.message ||
        'Assessment could not be completed for the selected vessel or voyage.',
    };
  }

  if (data.status === 'invalid_input') {
    return {
      key: 'invalid_input',
      label: 'INVALID INPUT',
      tagClass: 'pelagos-tag tag-offline',
      commercialNotice:
        data.message || 'Invalid vessel, cargo, or route parameters.',
    };
  }

  const cand = data.candidate || {};
  const overall = data.overall_suitability || cand.suitability_status || null;
  const cargoStatus = cand.cargo_compatibility?.status || null;
  const routeStatus = cand.route_compatibility?.status || null;
  const commStatus =
    data.commercial_availability_status?.commercial_availability ||
    cand.commercial_availability ||
    'UNCONFIRMED';
  const laycanStatus =
    data.commercial_availability_status?.laycan_feasibility ||
    cand.laycan_feasibility ||
    'UNCONFIRMED';

  if (overall === 'unsuitable_type' || overall === 'dimension_mismatch') {
    return {
      key: 'incompatible',
      label: 'INCOMPATIBLE',
      tagClass: 'pelagos-tag tag-offline',
      commercialNotice:
        'Physical or operational incompatibility detected for the proposed cargo or route. Commercial fixture checks cannot override physical incompatibility.',
    };
  }

  if (overall === 'insufficient_data') {
    return {
      key: 'insufficient_data',
      label: 'INSUFFICIENT DATA',
      tagClass: 'pelagos-tag tag-warning',
      commercialNotice:
        'Required vessel particulars or operational limits are missing; unknown or unverified attributes are never treated as compatible.',
    };
  }

  if (overall === 'potentially_suitable') {
    const fullyVerifiedPhysical =
      cargoStatus === 'compatible' && routeStatus === 'compatible';
    if (fullyVerifiedPhysical) {
      return {
        key: 'compatible',
        label: 'COMPATIBLE (PHYSICAL & ROUTE ONLY)',
        tagClass: 'pelagos-tag tag-verified',
        commercialNotice: `Physical cargo and route checks passed, but commercial availability is ${commStatus} and laycan feasibility is ${laycanStatus}. Physical compatibility does NOT establish open-tonnage availability or charter readiness.`,
      };
    }

    return {
      key: 'partially_assessed',
      label: 'PARTIALLY ASSESSED (PHYSICAL SCREENING ONLY)',
      tagClass: 'pelagos-tag tag-warning',
      commercialNotice: `Screened on partial particulars (${cargoStatus || 'unverified cargo capacity'} / ${routeStatus || 'unverified route limits'}). Commercial availability is ${commStatus} and laycan feasibility is ${laycanStatus} — vessel is NOT ready to charter.`,
    };
  }

  return {
    key: 'unavailable',
    label: String(overall || data.status || 'UNAVAILABLE').toUpperCase(),
    tagClass: 'pelagos-tag tag-unmonitored',
    commercialNotice:
      'Commercial availability and charter readiness remain UNCONFIRMED without integrated broker fixtures.',
  };
}

/**
 * Group missing information returned by `/api/v1/charter/assess` into the
 * four D2.1 audit categories using only backend reasons and notices.
 */
export function buildGroupedMissingInfo(data) {
  const cand = data?.candidate || {};
  const partMissing = Array.isArray(
    cand.particulars_completeness?.missing_particulars,
  )
    ? cand.particulars_completeness.missing_particulars
    : Array.isArray(cand.missing_particulars)
      ? cand.missing_particulars
      : [];

  const cargoMissing = Array.isArray(
    cand.cargo_compatibility?.missing_requirements,
  )
    ? [...cand.cargo_compatibility.missing_requirements]
    : [];
  if (
    cand.cargo_capacity_status === 'UNVERIFIED' &&
    !cargoMissing.includes('dwt_tonnage')
  ) {
    cargoMissing.unshift('dwt_tonnage');
  }

  const routeMissing = [];
  const pushUnique = (target, list) => {
    if (!Array.isArray(list)) return;
    for (const item of list) {
      if (item && !target.includes(item)) target.push(item);
    }
  };
  pushUnique(routeMissing, cand.route_compatibility?.missing_requirements);
  pushUnique(
    routeMissing,
    cand.route_compatibility?.origin_port?.missing_requirements,
  );
  pushUnique(
    routeMissing,
    cand.route_compatibility?.destination_port?.missing_requirements,
  );

  const comm = data?.commercial_availability_status || {};
  const commercialMissing = ['commercial_fixture_status'];
  if (
    (comm.commercial_availability ||
      cand.commercial_availability ||
      'UNCONFIRMED') === 'UNCONFIRMED'
  ) {
    commercialMissing.push('commercial_availability_unconfirmed');
  }
  if (
    (comm.laycan_feasibility || cand.laycan_feasibility || 'UNCONFIRMED') ===
    'UNCONFIRMED'
  ) {
    commercialMissing.push('laycan_feasibility_unconfirmed');
  }

  const vesselItems = partMissing.filter(
    (m) => m !== 'commercial_fixture_status',
  );

  const vesselWhy = [
    cand.verified_cargo_capacity_dwt == null
      ? 'Verified DWT is absent from AIS broadcasts; cargo capacity cannot be confirmed from dimensional screening alone.'
      : null,
    cand.draft_meters == null
      ? 'Vessel draft is missing and is required for documented port channel clearance.'
      : null,
    cand.suitability_status === 'insufficient_data'
      ? cand.suitability_reason
      : null,
  ]
    .filter(Boolean)
    .join(' ');

  const cargoWhy = Array.isArray(cand.cargo_compatibility?.reasons)
    ? cand.cargo_compatibility.reasons.join(' ')
    : '';

  const routeWhyParts = [
    ...(Array.isArray(cand.route_compatibility?.reasons)
      ? cand.route_compatibility.reasons
      : []),
    ...(Array.isArray(cand.route_compatibility?.canal_compatibility_notes)
      ? cand.route_compatibility.canal_compatibility_notes
      : []),
    ...(Array.isArray(cand.route_compatibility?.origin_port?.reasons)
      ? cand.route_compatibility.origin_port.reasons
      : []),
    ...(Array.isArray(cand.route_compatibility?.destination_port?.reasons)
      ? cand.route_compatibility.destination_port.reasons
      : []),
  ];

  const commercialWhy = [
    comm.commercial_availability_note || cand.commercial_availability_note,
    cand.laycan_feasibility_note,
    comm.registry_status_meaning,
  ]
    .filter(Boolean)
    .join(' ');

  return {
    vesselParticulars: {
      title: '1. Vessel Particulars',
      items: vesselItems,
      explanation:
        vesselWhy ||
        'All tracked physical particulars present for screening, or see missing items above.',
    },
    cargoCompatibility: {
      title: '2. Cargo Compatibility',
      items: cargoMissing,
      explanation:
        cargoWhy ||
        'Cargo compatibility depends on verified DWT and clean dry-bulk hold verification.',
    },
    routeConstraints: {
      title: '3. Route / Operational Constraints',
      items: routeMissing,
      explanation:
        routeWhyParts.join(' ') ||
        'Port berth draft/LOA/beam limits or canal transit rules could not be fully verified from integrated data.',
    },
    commercialInfo: {
      title: '4. Commercial Information',
      items: commercialMissing,
      explanation:
        commercialWhy ||
        'Commercial broker fixtures and laycan readiness feeds are not integrated in Pelagos.',
    },
  };
}

export class PelagosAssessmentController {
  /**
   * @param {object} [options]
   * @param {Document} [options.document]
   * @param {Function} [options.fetchImpl]
   * @param {Function} [options.getPorts]
   * @param {Function} [options.onCargoChange]
   */
  constructor({
    document = globalThis.document,
    fetchImpl = globalThis.fetch,
    getPorts = null,
    onCargoChange = null,
  } = {}) {
    this.doc = document;
    this.fetchImpl = fetchImpl;
    this.getPorts = getPorts;
    this.onCargoChange = onCargoChange;

    this.registeredVessels = [];
    this.filteredVessels = [];
    this.vesselsLoaded = false;
    this.vesselsLoadError = null;
    this.selectedVesselMmsi = '';
    this.isLoadingVessels = false;

    this.isAssessing = false;
    this.currentAssessment = null;
    this.lastSubmittedPayload = null;

    this._vesselsAbortController = null;
    this._assessAbortController = null;
    this._boundListeners = [];
    this._resolveElements();
  }

  _resolveElements() {
    const d = this.doc;
    if (!d) {
      this.elements = {};
      return;
    }
    this.elements = {
      card: d.getElementById('pelagos-assess-card'),
      statusTag: d.getElementById('pelagos-assess-status-tag'),
      vesselFilter: d.getElementById('pelagos-assess-vessel-filter'),
      vesselSelect: d.getElementById('pelagos-assess-vessel-select'),
      registryStatus: d.getElementById('pelagos-assess-vessel-registry-status'),
      reloadVesselsBtn: d.getElementById('pelagos-assess-reload-vessels-btn'),
      vesselPreview: d.getElementById('pelagos-assess-vessel-preview'),
      voyageSummary: d.getElementById('pelagos-assess-voyage-summary'),
      cargoInput: d.getElementById('pelagos-assess-cargo-tonnes'),
      commodityInput: d.getElementById('pelagos-assess-commodity'),
      submitBtn: d.getElementById('pelagos-assess-submit-btn'),
      validationMsg: d.getElementById('pelagos-assess-validation-msg'),
      errorBox: d.getElementById('pelagos-assess-error'),
      errorText: d.getElementById('pelagos-assess-error-text'),
      retryBtn: d.getElementById('pelagos-assess-retry-btn'),
      emptyState: d.getElementById('pelagos-assess-empty'),
      resultBox: d.getElementById('pelagos-assess-result'),
      outcomeBox: d.getElementById('pelagos-assess-outcome'),
      vesselSection: d.getElementById('pelagos-assess-vessel-section'),
      cargoSection: d.getElementById('pelagos-assess-cargo-section'),
      routeSection: d.getElementById('pelagos-assess-route-section'),
      aisCommercialSection: d.getElementById(
        'pelagos-assess-ais-commercial-section',
      ),
      missingSection: d.getElementById('pelagos-assess-missing-section'),
      disclaimersList: d.getElementById('pelagos-assess-disclaimers'),

      // Shared selectors in pelagos-panel
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

  async init() {
    this._resolveElements();
    const el = this.elements;
    if (!el.card) return;

    this._bind(el.vesselFilter, 'input', (e) => {
      this.filterRegisteredVessels(e?.target?.value ?? '');
    });

    this._bind(el.vesselSelect, 'change', (e) => {
      this.selectedVesselMmsi = String(e?.target?.value ?? '').trim();
      this.clearValidationMessage();
      this._renderSelectedVesselPreview();
    });

    this._bind(el.reloadVesselsBtn, 'click', () => {
      void this.loadRegisteredVessels();
    });

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

    this._bind(el.commodityInput, 'input', () => {
      this.clearValidationMessage();
    });

    this._bind(el.originSelect, 'change', () => this.syncVoyageSummary());
    this._bind(el.destSelect, 'change', () => this.syncVoyageSummary());
    this._bind(el.canalPolicySelect, 'change', () => this.syncVoyageSummary());

    this._bind(el.submitBtn, 'click', () => {
      void this.assess();
    });

    this._bind(el.retryBtn, 'click', () => {
      void this.retryLastAssessment();
    });

    this.syncVoyageSummary();
    await this.loadRegisteredVessels();
  }

  syncVoyageSummary() {
    const el = this.elements;
    if (!el.voyageSummary) return;

    const originId = String(el.originSelect?.value || '').trim();
    const destId = String(el.destSelect?.value || '').trim();
    const policy = String(el.canalPolicySelect?.value || 'allow').trim();

    if (!originId || !destId) {
      el.voyageSummary.textContent =
        'Voyage Ports: Select Origin and Destination ports in Route Endpoints above.';
      return;
    }

    if (originId === destId) {
      el.voyageSummary.textContent = `Voyage Ports: Origin and Destination cannot both be ${originId}.`;
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
    el.voyageSummary.textContent = `Voyage: ${origLabel} → ${destLabel} · Canal Policy: ${policy}`;
  }

  async loadRegisteredVessels() {
    const el = this.elements;
    if (this.isLoadingVessels) return false;
    this.isLoadingVessels = true;
    this._vesselsAbortController?.abort();
    const controller = new AbortController();
    this._vesselsAbortController = controller;

    if (el.registryStatus) {
      el.registryStatus.textContent =
        'Loading registered vessels from Pelagos registry…';
    }
    if (el.reloadVesselsBtn) el.reloadVesselsBtn.style.display = 'none';

    try {
      const res = await fetchRegisteredVessels({
        signal: controller.signal,
        fetchImpl: this.fetchImpl,
      });

      if (res.aborted) return false;

      if (!res.ok) {
        this.vesselsLoaded = false;
        this.vesselsLoadError = res.error;
        this.registeredVessels = [];
        this.filteredVessels = [];
        this._populateVesselSelect([]);
        if (el.registryStatus) {
          el.registryStatus.textContent = `Unavailable: Unable to load registered vessels (${res.error}).`;
        }
        if (el.reloadVesselsBtn)
          el.reloadVesselsBtn.style.display = 'inline-block';
        return false;
      }

      this.registeredVessels = res.items || [];
      this.vesselsLoaded = true;
      this.vesselsLoadError = null;
      this.filterRegisteredVessels(el.vesselFilter?.value || '');

      if (el.registryStatus) {
        el.registryStatus.textContent =
          this.registeredVessels.length > 0
            ? `${this.registeredVessels.length} registered vessels loaded (historical AIS registry; commercial status not open-tonnage).`
            : 'No registered vessel records found in database.';
      }
      return true;
    } finally {
      this.isLoadingVessels = false;
    }
  }

  filterRegisteredVessels(query = '') {
    const term = String(query || '')
      .trim()
      .toLowerCase();
    const list = this.registeredVessels.filter((v) => {
      if (!term) return true;
      return (
        String(v.vessel_name || '')
          .toLowerCase()
          .includes(term) ||
        String(v.mmsi || '')
          .toLowerCase()
          .includes(term) ||
        String(v.imo || '')
          .toLowerCase()
          .includes(term) ||
        String(v.vessel_type || '')
          .toLowerCase()
          .includes(term) ||
        String(v.vessel_class || '')
          .toLowerCase()
          .includes(term)
      );
    });
    this.filteredVessels = list;
    this._populateVesselSelect(list);
  }

  _populateVesselSelect(vessels) {
    const select = this.elements.vesselSelect;
    if (!select) return;

    const prev = this.selectedVesselMmsi || String(select.value || '').trim();
    select.innerHTML = '';

    const defaultOpt = this.doc.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent =
      vessels.length > 0
        ? 'Select registered vessel (MMSI / Name)…'
        : 'No registered vessels available';
    select.appendChild(defaultOpt);

    for (const v of vessels) {
      const opt = this.doc.createElement('option');
      opt.value = String(v.mmsi);
      const cls = v.vessel_class || v.vessel_type || 'Unknown type';
      const dwt =
        v.dwt_tonnage !== null && v.dwt_tonnage !== undefined
          ? `${Number(v.dwt_tonnage).toLocaleString('en-US')} DWT`
          : 'DWT UNVERIFIED';
      opt.textContent = `${v.vessel_name || `MMSI ${v.mmsi}`} (${v.mmsi} · ${cls} · ${dwt})`;
      select.appendChild(opt);
    }

    if (prev && vessels.some((v) => String(v.mmsi) === prev)) {
      select.value = prev;
      this.selectedVesselMmsi = prev;
    } else if (
      vessels.length === 1 &&
      (this.elements.vesselFilter?.value || '').trim().length >= 3
    ) {
      select.value = String(vessels[0].mmsi);
      this.selectedVesselMmsi = String(vessels[0].mmsi);
    } else {
      select.value = '';
      this.selectedVesselMmsi = '';
    }

    this._renderSelectedVesselPreview();
  }

  _renderSelectedVesselPreview() {
    const el = this.elements;
    if (!el.vesselPreview) return;

    const mmsi =
      this.selectedVesselMmsi || String(el.vesselSelect?.value || '').trim();
    if (!mmsi) {
      el.vesselPreview.style.display = 'none';
      el.vesselPreview.textContent = '';
      return;
    }

    const v = this.registeredVessels.find((r) => String(r.mmsi) === mmsi);
    if (!v) {
      el.vesselPreview.style.display = 'block';
      el.vesselPreview.textContent = `Selected MMSI: ${mmsi} (particulars will be queried from backend on submission)`;
      return;
    }

    const dwt =
      v.dwt_tonnage !== null && v.dwt_tonnage !== undefined
        ? `${formatNum(v.dwt_tonnage, 0)} MT (Verified)`
        : 'UNVERIFIED';
    const dims = `${v.length_meters ? `${Math.round(v.length_meters)}m LOA` : 'LOA —'} · ${v.width_meters ? `${Math.round(v.width_meters)}m Beam` : 'Beam —'} · ${v.draft_max_meters ? `${Number(v.draft_max_meters).toFixed(1)}m Draft` : 'Draft —'}`;
    const missing =
      Array.isArray(v.missing_particulars) && v.missing_particulars.length > 0
        ? ` · Registry Missing: [${v.missing_particulars.join(', ')}]`
        : '';

    el.vesselPreview.style.display = 'block';
    el.vesselPreview.textContent = `${v.vessel_name || mmsi} · MMSI: ${v.mmsi}${v.imo ? ` · IMO: ${v.imo}` : ''} · Type: ${v.vessel_type || '—'}${v.vessel_class ? ` (${v.vessel_class})` : ''} · DWT: ${dwt} · ${dims} · Registry Sync: "${v.commercial_status || 'unknown'}" (sync only)${missing}`;
  }

  /** Build & validate request payload from actual DOM inputs without fabricating defaults. */
  buildAssessmentPayload() {
    const el = this.elements;
    const mmsi = String(
      el.vesselSelect?.value || this.selectedVesselMmsi || '',
    ).trim();
    const origin = String(el.originSelect?.value || '').trim();
    const destination = String(el.destSelect?.value || '').trim();
    const canalPolicy = String(el.canalPolicySelect?.value || 'allow').trim();
    const cargoRaw = String(el.cargoInput?.value ?? '').trim();
    const cargoTonnes = Number(cargoRaw);
    const commodity = String(el.commodityInput?.value ?? '').trim();

    const missing = [];
    if (!mmsi) missing.push('a registered vessel (MMSI)');
    if (!origin) missing.push('an origin port');
    if (!destination) missing.push('a destination port');
    if (!cargoRaw || !Number.isFinite(cargoTonnes) || cargoTonnes <= 0) {
      missing.push('a positive cargo tonnage (> 0 MT)');
    }
    if (!commodity) missing.push('a cargo commodity type');

    if (missing.length > 0) {
      return {
        valid: false,
        error: `Missing required assessment inputs: please specify ${missing.join(', ')}.`,
      };
    }

    if (origin === destination) {
      return {
        valid: false,
        error:
          'Origin and destination ports must be different to assess voyage compatibility.',
      };
    }

    return {
      valid: true,
      payload: {
        mmsi,
        cargo_tonnes: cargoTonnes,
        commodity,
        origin,
        destination,
        canal_policy: canalPolicy || 'allow',
      },
    };
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

  setAssessingState(loading) {
    this.isAssessing = loading;
    const el = this.elements;
    if (el.submitBtn) {
      el.submitBtn.disabled = loading;
      el.submitBtn.classList?.toggle?.('is-loading', loading);
      const textSpan = el.submitBtn.querySelector?.('.btn-text');
      if (textSpan) {
        textSpan.textContent = loading
          ? 'ASSESSING…'
          : 'ASSESS VESSEL FOR VOYAGE';
      }
    }
    if (el.statusTag && loading) {
      el.statusTag.className = 'pelagos-tag tag-loading';
      el.statusTag.textContent = 'ASSESSING…';
    }
  }

  async assess({ method = 'POST' } = {}) {
    // Prevent accidental duplicate / re-entrant submissions while request is in flight
    if (this.isAssessing) return false;

    this.clearValidationMessage();
    const built = this.buildAssessmentPayload();
    if (!built.valid) {
      this.showValidationMessage(built.error);
      return false;
    }

    this.lastSubmittedPayload = built.payload;
    return this._executeAssessment(built.payload, { method });
  }

  async retryLastAssessment({ method = 'POST' } = {}) {
    if (this.isAssessing) return false;
    if (!this.lastSubmittedPayload) {
      return this.assess({ method });
    }
    return this._executeAssessment(this.lastSubmittedPayload, { method });
  }

  async _executeAssessment(payload, { method = 'POST' } = {}) {
    if (this.isAssessing) return false;

    this.setAssessingState(true);
    this._assessAbortController?.abort();
    const controller = new AbortController();
    this._assessAbortController = controller;

    if (this.elements.errorBox) this.elements.errorBox.style.display = 'none';

    try {
      const res = await assessVesselCharter(payload, {
        method,
        signal: controller.signal,
        fetchImpl: this.fetchImpl,
      });

      if (res.aborted) return false;

      if (!res.ok) {
        this.renderError(res.error || `HTTP ${res.statusCode || 'Error'}`);
        return false;
      }

      this.currentAssessment = res.data;
      this.renderResult(res.data, payload);
      return true;
    } finally {
      this.setAssessingState(false);
    }
  }

  renderError(errorMsg) {
    const el = this.elements;
    if (el.statusTag) {
      el.statusTag.className = 'pelagos-tag tag-offline';
      el.statusTag.textContent = 'ERROR';
    }
    if (el.resultBox) el.resultBox.style.display = 'none';
    if (el.emptyState) el.emptyState.style.display = 'none';
    if (el.errorBox) el.errorBox.style.display = 'block';
    if (el.errorText) {
      el.errorText.textContent = `Single-vessel charter assessment error: ${errorMsg}`;
    }
  }

  _appendSectionRow(container, label, value, valueClass = '') {
    const row = this.doc.createElement('div');
    row.className = 'assess-kv-row';

    const k = this.doc.createElement('span');
    k.className = 'assess-k';
    k.textContent = `${label}:`;
    row.appendChild(k);

    const v = this.doc.createElement('span');
    v.className = valueClass ? `assess-v ${valueClass}` : 'assess-v';
    v.textContent = value;
    row.appendChild(v);

    container.appendChild(row);
  }

  _appendSectionHeading(container, title) {
    const h = this.doc.createElement('div');
    h.className = 'assess-section-title';
    h.textContent = title;
    container.appendChild(h);
  }

  _appendReasonList(container, title, list) {
    if (!Array.isArray(list) || list.length === 0) return;
    const label = this.doc.createElement('div');
    label.className = 'assess-sublabel';
    label.textContent = title;
    container.appendChild(label);

    const ul = this.doc.createElement('ul');
    ul.className = 'assess-bullets';
    for (const item of list) {
      const li = this.doc.createElement('li');
      li.textContent = String(item);
      ul.appendChild(li);
    }
    container.appendChild(ul);
  }

  renderResult(data, requestPayload = this.lastSubmittedPayload) {
    const el = this.elements;
    if (!el.card || !el.resultBox) return;

    if (el.errorBox) el.errorBox.style.display = 'none';
    if (el.emptyState) el.emptyState.style.display = 'none';
    el.resultBox.style.display = 'block';

    const outcome = classifyAssessmentOutcome(data);
    if (el.statusTag) {
      el.statusTag.className = outcome.tagClass;
      el.statusTag.textContent = outcome.label;
    }

    const cand = data?.candidate || null;
    const aisStatus = data?.ais_observation_status || {};
    const commStatus = data?.commercial_availability_status || {};

    // 1. Outcome Banner
    if (el.outcomeBox) {
      el.outcomeBox.innerHTML = '';
      el.outcomeBox.className = `pelagos-assess-outcome outcome-${outcome.key}`;

      const head = this.doc.createElement('div');
      head.className = 'assess-outcome-headline';
      head.textContent = `${outcome.label}${data?.overall_suitability ? ` (${data.overall_suitability})` : ''}`;
      el.outcomeBox.appendChild(head);

      const reason = this.doc.createElement('div');
      reason.className = 'assess-outcome-reason';
      reason.textContent =
        data?.message ||
        cand?.suitability_reason ||
        'No backend summary message.';
      el.outcomeBox.appendChild(reason);

      const corridorText = data?.corridor
        ? `Corridor: ${data.corridor.route_name || data.corridor.route_code} (${data.corridor.route_code}) · Target Class: ${data.corridor.vessel_class || '—'}`
        : 'Corridor: Unmonitored trade corridor (no benchmark corridor rate attached)';
      const corrDiv = this.doc.createElement('div');
      corrDiv.className = 'assess-outcome-meta';
      corrDiv.textContent = corridorText;
      el.outcomeBox.appendChild(corrDiv);

      const commBanner = this.doc.createElement('div');
      commBanner.className = 'assess-outcome-boundary';
      commBanner.textContent = outcome.commercialNotice;
      el.outcomeBox.appendChild(commBanner);
    }

    // 2. Vessel Identity & Particulars
    if (el.vesselSection) {
      el.vesselSection.innerHTML = '';
      this._appendSectionHeading(
        el.vesselSection,
        'VESSEL IDENTITY & PARTICULARS',
      );
      if (!cand) {
        this._appendSectionRow(
          el.vesselSection,
          'Vessel Record',
          data?.message || 'Vessel record unavailable or not found.',
          'is-warning',
        );
      } else {
        const pc = cand.particulars_completeness || {};
        this._appendSectionRow(
          el.vesselSection,
          'Vessel & IDs',
          `${cand.vessel_name || '—'} · MMSI ${cand.mmsi || '—'} · IMO ${cand.imo || 'UNREPORTED'}`,
        );
        this._appendSectionRow(
          el.vesselSection,
          'Type & Class',
          `AIS Type: ${cand.vessel_type || '—'}${cand.vessel_type_code != null ? ` (code ${cand.vessel_type_code})` : ''} · AIS Class: ${cand.estimated_dimensional_class || '—'} · Registry Class: ${cand.registry_vessel_class || pc.registry_vessel_class || 'UNCLASSIFIED'}`,
        );
        const hasDwt =
          cand.verified_cargo_capacity_dwt !== null &&
          cand.verified_cargo_capacity_dwt !== undefined &&
          cand.cargo_capacity_status === 'VERIFIED';
        this._appendSectionRow(
          el.vesselSection,
          'Deadweight (DWT)',
          hasDwt
            ? `${formatNum(cand.verified_cargo_capacity_dwt, 1)} MT (VERIFIED)`
            : `UNVERIFIED (${cand.cargo_capacity_status || 'UNVERIFIED'})`,
          hasDwt ? 'is-ok' : 'is-warning',
        );
        this._appendSectionRow(
          el.vesselSection,
          'Dimensions',
          `LOA: ${cand.length_meters != null ? `${formatNum(cand.length_meters, 1)}m` : 'UNAVAILABLE'} · Beam: ${cand.width_meters != null ? `${formatNum(cand.width_meters, 1)}m` : 'UNAVAILABLE'} · Draft: ${cand.draft_meters != null ? `${formatNum(cand.draft_meters, 1)}m` : 'UNAVAILABLE'}`,
        );
        this._appendSectionRow(
          el.vesselSection,
          'Particulars Completeness',
          `${String(pc.completeness_status || cand.screening_stage || 'partial').toUpperCase()}${Array.isArray(pc.data_sources) && pc.data_sources.length ? ` · Sources: ${pc.data_sources.join(', ')}` : ''}`,
        );
        this._appendSectionRow(
          el.vesselSection,
          'Verified Particulars',
          Array.isArray(pc.verified_particulars) &&
            pc.verified_particulars.length
            ? pc.verified_particulars.join(', ')
            : 'None',
        );
        const missingList = Array.isArray(pc.missing_particulars)
          ? pc.missing_particulars
          : cand.missing_particulars || [];
        this._appendSectionRow(
          el.vesselSection,
          'Missing / Unverified',
          missingList.length > 0 ? missingList.join(', ') : 'None',
          missingList.length > 0 ? 'is-warning' : '',
        );
      }
    }

    // 3. Cargo Compatibility
    if (el.cargoSection) {
      el.cargoSection.innerHTML = '';
      this._appendSectionHeading(el.cargoSection, 'CARGO COMPATIBILITY');
      const cc = cand?.cargo_compatibility;
      if (!cc) {
        this._appendSectionRow(
          el.cargoSection,
          'Status',
          'Unavailable without vessel record.',
          'is-warning',
        );
      } else {
        this._appendSectionRow(
          el.cargoSection,
          'Cargo Status',
          String(cc.status || 'unknown').toUpperCase(),
          cc.status === 'compatible'
            ? 'is-ok'
            : cc.status === 'incompatible'
              ? 'is-error'
              : 'is-warning',
        );
        const capStr =
          cc.verified_dwt_tonnes != null
            ? `${formatNum(cc.verified_dwt_tonnes, 1)} DWT verified`
            : 'UNVERIFIED DWT';
        this._appendSectionRow(
          el.cargoSection,
          'Parcel vs Capacity',
          `${formatNum(cc.cargo_tonnes, 0)} MT (${cc.commodity || requestPayload?.commodity || '—'}) vs ${capStr} · Basis: ${cc.capacity_check_basis || '—'}`,
        );
        this._appendReasonList(
          el.cargoSection,
          'Evaluation Reasons:',
          cc.reasons,
        );
        this._appendReasonList(
          el.cargoSection,
          'Missing Cargo Verification Items:',
          cc.missing_requirements,
        );
      }
    }

    // 4. Route & Operational Compatibility
    if (el.routeSection) {
      el.routeSection.innerHTML = '';
      this._appendSectionHeading(
        el.routeSection,
        'ROUTE & OPERATIONAL COMPATIBILITY',
      );
      const rc = cand?.route_compatibility;
      const appliedPolicy =
        requestPayload?.canal_policy ||
        this.elements.canalPolicySelect?.value ||
        'allow';
      this._appendSectionRow(
        el.routeSection,
        'Canal Policy Applied',
        appliedPolicy,
      );

      if (!rc) {
        this._appendSectionRow(
          el.routeSection,
          'Route Compatibility',
          data?.message ||
            'Route operational compatibility could not be evaluated.',
          'is-warning',
        );
      } else {
        this._appendSectionRow(
          el.routeSection,
          'Route Status',
          `${String(rc.status || 'unknown').toUpperCase()} · Navigable: ${rc.route_navigable === true ? 'YES' : rc.route_navigable === false ? 'NO' : 'UNKNOWN'} (${rc.routing_status || '—'})`,
          rc.status === 'compatible'
            ? 'is-ok'
            : rc.status === 'incompatible'
              ? 'is-error'
              : 'is-warning',
        );
        this._appendSectionRow(
          el.routeSection,
          'Routing Mode & Dist',
          `${rc.routing_mode || '—'} · ${rc.route_distance_nm != null ? `${formatNum(rc.route_distance_nm, 1)} NM` : 'Distance unavailable'} · Canals: ${Array.isArray(rc.canals_transited) && rc.canals_transited.length ? rc.canals_transited.join(', ') : 'None'}`,
        );

        const formatPort = (pe, label) => {
          if (!pe) {
            this._appendSectionRow(el.routeSection, label, 'Not evaluated');
            return;
          }
          const limitStr =
            pe.documented_draft_limit_meters != null
              ? `Limit ${formatNum(pe.documented_draft_limit_meters, 2)}m (${pe.limit_source_note || 'documented'})`
              : 'Draft limit UNVERIFIED';
          const vDraft =
            pe.vessel_draft_meters != null
              ? `Vessel Draft ${formatNum(pe.vessel_draft_meters, 1)}m`
              : 'Vessel Draft MISSING';
          this._appendSectionRow(
            el.routeSection,
            `${label} (${pe.port_name || pe.port_id || '—'})`,
            `${String(pe.status || '—').toUpperCase()} · ${limitStr} · ${vDraft}${pe.access_limitations ? ` · Access note: ${pe.access_limitations}` : ''}`,
            pe.status === 'compatible'
              ? 'is-ok'
              : pe.status === 'incompatible'
                ? 'is-error'
                : 'is-warning',
          );
          this._appendReasonList(
            el.routeSection,
            `${pe.port_id || label} Details:`,
            pe.reasons,
          );
        };

        formatPort(rc.origin_port, 'Origin Port');
        formatPort(rc.destination_port, 'Destination Port');

        this._appendReasonList(
          el.routeSection,
          'Canal Constraints & Notes:',
          rc.canal_compatibility_notes,
        );
        this._appendReasonList(el.routeSection, 'Route Reasons:', rc.reasons);
        this._appendReasonList(
          el.routeSection,
          'Missing Operational Requirements:',
          rc.missing_requirements,
        );
      }
    }

    // 5. AIS & Commercial Status
    if (el.aisCommercialSection) {
      el.aisCommercialSection.innerHTML = '';
      this._appendSectionHeading(
        el.aisCommercialSection,
        'AIS TELEMETRY vs COMMERCIAL STATUS',
      );
      const obsTime =
        aisStatus.observation_time ?? cand?.observation_time ?? null;
      const obsAge =
        aisStatus.observation_age_seconds ??
        cand?.observation_age_seconds ??
        null;
      const isStale = aisStatus.is_stale ?? cand?.is_stale ?? true;
      const dataMode = aisStatus.data_mode ?? cand?.data_mode ?? 'historical';
      const lat = aisStatus.latitude ?? cand?.latitude;
      const lon = aisStatus.longitude ?? cand?.longitude;
      const distQual =
        aisStatus.distance_qualification ?? cand?.distance_qualification;

      this._appendSectionRow(
        el.aisCommercialSection,
        'Last AIS Fix',
        obsTime
          ? `${obsTime} (${formatAgeSeconds(obsAge)} · ${isStale ? 'STALE / HISTORICAL' : 'LIVE <30m'} · Mode: ${dataMode})`
          : `No AIS observation recorded (Mode: ${dataMode})`,
        isStale ? 'is-warning' : 'is-ok',
      );
      this._appendSectionRow(
        el.aisCommercialSection,
        'AIS Coordinates',
        lat != null && lon != null
          ? `${Number(lat).toFixed(4)}°, ${Number(lon).toFixed(4)}°${cand?.distance_to_load_port_nm != null ? ` (${formatNum(cand.distance_to_load_port_nm, 1)} NM Great-Circle at fix)` : ''}`
          : 'Unavailable',
      );
      if (distQual) {
        this._appendSectionRow(
          el.aisCommercialSection,
          'Distance Qualification',
          distQual,
        );
      }

      const regSync =
        commStatus.registry_commercial_status ??
        cand?.registry_commercial_status ??
        'untracked';
      const regMeaning =
        commStatus.registry_status_meaning ||
        `Registry commercial_status ("${regSync}") indicates historical AIS registry sync ONLY — NOT open-tonnage availability.`;
      this._appendSectionRow(
        el.aisCommercialSection,
        'Registry Sync Status',
        `${regSync} — ${regMeaning}`,
      );

      const commercialAvail =
        commStatus.commercial_availability ??
        cand?.commercial_availability ??
        'UNCONFIRMED';
      const laycanFeas =
        commStatus.laycan_feasibility ??
        cand?.laycan_feasibility ??
        'UNCONFIRMED';
      this._appendSectionRow(
        el.aisCommercialSection,
        'Commercial Availability',
        `${commercialAvail} (${commStatus.commercial_availability_note || cand?.commercial_availability_note || 'Broker open-tonnage feed not connected'})`,
        'is-warning',
      );
      this._appendSectionRow(
        el.aisCommercialSection,
        'Laycan Feasibility',
        `${laycanFeas} (${cand?.laycan_feasibility_note || 'Commercial fixture commitments unverified'})`,
        'is-warning',
      );
    }

    // 6. Grouped Missing Information & Explanations
    if (el.missingSection) {
      el.missingSection.innerHTML = '';
      this._appendSectionHeading(
        el.missingSection,
        'MISSING INFORMATION & IMPACT',
      );
      const groups = buildGroupedMissingInfo(data);
      for (const grp of [
        groups.vesselParticulars,
        groups.cargoCompatibility,
        groups.routeConstraints,
        groups.commercialInfo,
      ]) {
        const box = this.doc.createElement('div');
        box.className = 'assess-missing-group';

        const title = this.doc.createElement('div');
        title.className = 'assess-missing-title';
        title.textContent = `${grp.title} (${grp.items.length > 0 ? grp.items.join(', ') : 'None flagged'})`;
        box.appendChild(title);

        const exp = this.doc.createElement('div');
        exp.className = 'assess-missing-why';
        exp.textContent = grp.explanation;
        box.appendChild(exp);

        el.missingSection.appendChild(box);
      }
    }

    // 7. Authoritative Disclaimers
    if (el.disclaimersList) {
      el.disclaimersList.innerHTML = '';
      const disclaimers = Array.isArray(data?.disclaimers)
        ? data.disclaimers
        : [];
      for (const d of disclaimers) {
        const li = this.doc.createElement('li');
        li.textContent = d;
        el.disclaimersList.appendChild(li);
      }
    }
  }

  clearResult() {
    this._assessAbortController?.abort();
    this.isAssessing = false;
    this.currentAssessment = null;
    this.lastSubmittedPayload = null;
    this.clearValidationMessage();

    const el = this.elements;
    if (el.submitBtn) {
      el.submitBtn.disabled = false;
      el.submitBtn.classList?.remove?.('is-loading');
      const textSpan = el.submitBtn.querySelector?.('.btn-text');
      if (textSpan) textSpan.textContent = 'ASSESS VESSEL FOR VOYAGE';
    }
    if (el.statusTag) {
      el.statusTag.className = 'pelagos-tag tag-unmonitored';
      el.statusTag.textContent = 'AWAITING INPUT';
    }
    if (el.errorBox) el.errorBox.style.display = 'none';
    if (el.resultBox) el.resultBox.style.display = 'none';
    if (el.emptyState) el.emptyState.style.display = 'block';
    this.syncVoyageSummary();
  }

  destroy() {
    this._vesselsAbortController?.abort();
    this._assessAbortController?.abort();
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
