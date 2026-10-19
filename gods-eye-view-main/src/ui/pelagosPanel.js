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
      waypointCount: d.getElementById('pelagos-waypoint-count'),

      // Corridor
      corridorCard: d.getElementById('pelagos-corridor-card'),
      corridorStatusTag: d.getElementById('pelagos-corridor-status-tag'),
      corridorContent: d.getElementById('pelagos-corridor-content'),
      corridorName: d.getElementById('pelagos-corridor-name'),
      corridorCode: d.getElementById('pelagos-corridor-code'),
      corridorCargo: d.getElementById('pelagos-corridor-cargo'),
      corridorVesselClass: d.getElementById('pelagos-corridor-vessel-class'),
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
      rateEmpty: d.getElementById('pelagos-rate-empty'),

      // Forecast
      forecastCard: d.getElementById('pelagos-forecast-card'),
      forecastContent: d.getElementById('pelagos-forecast-content'),
      forecastVal: d.getElementById('pelagos-forecast-val'),
      forecastUnit: d.getElementById('pelagos-forecast-unit'),
      forecastPeriod: d.getElementById('pelagos-forecast-period'),
      forecastModel: d.getElementById('pelagos-forecast-model'),
      forecastHorizon: d.getElementById('pelagos-forecast-horizon'),
      forecastEmpty: d.getElementById('pelagos-forecast-empty'),

      // Fleet
      fleetCard: d.getElementById('pelagos-fleet-card'),
      fleetTag: d.getElementById('pelagos-fleet-tag'),
      fleetDot: d.getElementById('pelagos-fleet-dot'),
      fleetText: d.getElementById('pelagos-fleet-text'),
      fleetDesc: d.getElementById('pelagos-fleet-desc'),

      // Limitations
      limitationsList: d.getElementById('pelagos-limitations-list'),
    };
  }

  /** Initialize event listeners and fetch port definitions. */
  async init() {
    this._resolveElements();
    if (!this.elements.panel) return;

    this._bindEvents();
    await this.loadPorts();
  }

  _bind(element, event, handler) {
    if (!element) return;
    element.addEventListener(event, handler);
    this._boundListeners.push({ element, event, handler });
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
  }

  validateEndpoints() {
    const el = this.elements;
    const origin = el.originSelect?.value;
    const dest = el.destSelect?.value;

    if (!origin || !dest) {
      this.showValidationMessage('Please select both an origin and destination port.');
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

      const payload = {
        origin: originId,
        destination: destId,
        routing_mode: 'marnet',
        canal_policy: 'allow',
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

      if (el.rateVal) el.rateVal.textContent = Number(obs.rate_value).toFixed(2);
      if (el.rateUnit)
        el.rateUnit.textContent = `${obs.currency || 'USD'}/${obs.unit?.split('/')[1] || 'MT'}`;
      if (el.rateDate) el.rateDate.textContent = obs.observation_date || '—';
      if (el.rateSource)
        el.rateSource.textContent =
          obs.source_name ||
          "USDA AMS / O'Neil Commodity Consulting";
      if (el.rateType) {
        el.rateType.textContent = obs.is_derived
          ? 'Derived Analytical Estimate'
          : 'Verified Published Benchmark (Not Derived)';
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
    }

    // 4. Freight Forecast
    const fc = isMonitored ? freight.baseline_forecast : null;
    if (fc && Number.isFinite(Number(fc.predicted_value))) {
      if (el.forecastContent) el.forecastContent.style.display = 'block';
      if (el.forecastEmpty) el.forecastEmpty.style.display = 'none';

      if (el.forecastVal)
        el.forecastVal.textContent = Number(fc.predicted_value).toFixed(2);
      if (el.forecastUnit) el.forecastUnit.textContent = fc.unit || 'USD/MT';
      if (el.forecastPeriod)
        el.forecastPeriod.textContent = fc.target_date || 'October 2026';
      const modelName = fc.model_type
        ? fc.model_type.charAt(0).toUpperCase() + fc.model_type.slice(1)
        : 'Persistence';
      if (el.forecastModel)
        el.forecastModel.textContent = `${modelName} Baseline`;
      if (el.forecastHorizon)
        el.forecastHorizon.textContent = `${fc.horizon_months || 1} Month Forward`;
    } else {
      if (el.forecastContent) el.forecastContent.style.display = 'none';
      if (el.forecastEmpty) {
        el.forecastEmpty.style.display = 'block';
        el.forecastEmpty.textContent = isMonitored
          ? 'No forecast generated for this period.'
          : 'No predictive forecast model active for this trade lane.';
      }
    }

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
   * Dynamically query live AIS stream status and update the Vessel Availability card.
   * Accurately distinguishes live sea telemetry from commercial open-tonnage availability.
   */
  async updateFleetAvailability(freight) {
    const el = this.elements;
    if (!el.fleetCard) return;

    try {
      const resp = await fetch('/api/ais-live?maxRows=1', { cache: 'no-store' });
      if (resp.ok) {
        const liveData = await resp.json();
        const status = liveData?.status;
        const count = Array.isArray(liveData?.rows) ? liveData.rows.length : 0;
        const totalLive = liveData?.rawRowCount || count;

        if (status === 'live' && count > 0) {
          if (el.fleetTag) {
            el.fleetTag.className = 'pelagos-tag tag-ready';
            el.fleetTag.textContent = 'TELEMETRY LIVE';
          }
          if (el.fleetDot) el.fleetDot.className = 'status-indicator-dot dot-active';
          if (el.fleetText) el.fleetText.textContent = `Active — Live AIS Telemetry Connected (${totalLive.toLocaleString()} vessels tracked)`;
          if (el.fleetDesc) {
            el.fleetDesc.textContent =
              'Live AIS position stream is connected. Real-time fleet tracking is active across monitored sea lanes. Notice: Commercial open-tonnage fixture lists remain unconnected (AIS telemetry alone does not establish commercial vessel charter availability).';
          }
          return;
        }

        if (status === 'connecting' || status === 'idle') {
          if (el.fleetTag) {
            el.fleetTag.className = 'pelagos-tag tag-loading';
            el.fleetTag.textContent = 'CONNECTING';
          }
          if (el.fleetDot) el.fleetDot.className = 'status-indicator-dot dot-pending';
          if (el.fleetText) el.fleetText.textContent = 'Connecting — Establishing AISStream WebSocket…';
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
          if (el.fleetDot) el.fleetDot.className = 'status-indicator-dot dot-inactive';
          if (el.fleetText) el.fleetText.textContent = 'Unavailable — AISSTREAM_API_KEY is not configured';
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
          if (el.fleetDot) el.fleetDot.className = 'status-indicator-dot dot-inactive';
          if (el.fleetText) el.fleetText.textContent = 'Unavailable — AISStream API key rejected';
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

    if (el.fleetTag) {
      el.fleetTag.className = 'pelagos-tag tag-offline';
      el.fleetTag.textContent = 'UNCONNECTED';
    }
    if (el.fleetDot) el.fleetDot.className = 'status-indicator-dot dot-inactive';
    if (el.fleetText) el.fleetText.textContent = 'Unavailable — live vessel availability data is not connected';
    if (el.fleetDesc) {
      el.fleetDesc.textContent =
        freight?.vessel_availability?.reason ||
        'Live AIS fleet telemetry and commercial open tonnage lists are not connected (pelagos live_ais_connected: false). Static sample only covers coastal U.S. waters and is not interpreted as active fleet availability.';
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

    if (this.elements.dashboard) {
      this.elements.dashboard.style.display = 'none';
    }
    this.clearStatusBanner();
    this.clearValidationMessage();

    if (this.elements.originSelect) this.elements.originSelect.value = '';
    if (this.elements.destSelect) this.elements.destSelect.value = '';
    if (this.elements.originFilter) this.elements.originFilter.value = '';
    if (this.elements.destFilter) this.elements.destFilter.value = '';

    this.filterPortOptions(this.elements.originSelect, '');
    this.filterPortOptions(this.elements.destSelect, '');
  }

  /** Teardown all event listeners, graphics, and abort pending requests. */
  destroy() {
    this._abortController?.abort();
    this.clearGlobeGraphics();

    for (const { element, event, handler } of this._boundListeners) {
      try {
        element.removeEventListener(event, handler);
      } catch {
        /* listener cleanup is best effort */
      }
    }
    this._boundListeners = [];
  }
}
