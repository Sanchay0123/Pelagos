/**
 * @module ui/pelagosOverview
 * @description Page 1 Pelagos Live Data Overview controller integrated inside #pelagos-panel.
 */

import { fetchPlatformOverview } from './pelagosApi.js';
import { EXPLORER_DOMAIN_DEFS, buildExplorerHash } from './explorerRouter.js';

function formatCompactUtc(iso) {
  if (!iso) return 'None recorded';
  const cleaned = String(iso).replace('T', ' ').replace('+00:00', 'Z');
  return cleaned.length > 19 ? `${cleaned.slice(0, 19)} UTC` : cleaned;
}

function formatNum(value) {
  if (
    value === null ||
    value === undefined ||
    !Number.isFinite(Number(value))
  ) {
    return '—';
  }
  return Number(value).toLocaleString('en-US');
}

export class PelagosOverviewController {
  /**
   * @param {object} [options]
   * @param {Document} [options.document]
   * @param {Function} [options.onNavigateExplorer]
   * @param {Function} [options.fetchImpl]
   */
  constructor({
    document = globalThis.document,
    onNavigateExplorer = null,
    fetchImpl = globalThis.fetch,
  } = {}) {
    this.doc = document;
    this.onNavigateExplorer = onNavigateExplorer;
    this.fetchImpl = fetchImpl;
    this.overviewData = null;
    this.isLoading = false;
    this._abortController = null;
    this._reqSeq = 0;
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
      card: d.getElementById('pelagos-data-overview-card'),
      statusTag: d.getElementById('pelagos-overview-status-tag'),
      totalRecords: d.getElementById('pelagos-ov-total-records'),
      sourcesCount: d.getElementById('pelagos-ov-sources-count'),
      domainsCount: d.getElementById('pelagos-ov-domains-count'),
      latestSync: d.getElementById('pelagos-ov-latest-sync'),
      methodology: d.getElementById('pelagos-ov-methodology'),
      domainGrid: d.getElementById('pelagos-ov-domain-grid'),
      freightSummary: d.getElementById('pelagos-ov-freight-summary'),
      indicatorsList: d.getElementById('pelagos-ov-indicators-list'),
      noticeBanner: d.getElementById('pelagos-ov-notice'),
      refreshBtn: d.getElementById('pelagos-overview-refresh-btn'),
      openExplorerBtn: d.getElementById('pelagos-open-explorer-btn'),
      headerExplorerBtn: d.getElementById('pelagos-header-explorer-btn'),
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

    this._bind(el.refreshBtn, 'click', () => {
      void this.load({ useCache: false });
    });

    const navigateDefault = () => this._navigate('freight');
    this._bind(el.openExplorerBtn, 'click', navigateDefault);
    this._bind(el.headerExplorerBtn, 'click', (event) => {
      event?.stopPropagation?.();
      navigateDefault();
    });

    if (el.card) {
      return this.load({ useCache: true });
    }
    return Promise.resolve(null);
  }

  _navigate(domain) {
    if (typeof this.onNavigateExplorer === 'function') {
      this.onNavigateExplorer(domain);
    } else if (typeof window !== 'undefined' && window.location) {
      window.location.hash = buildExplorerHash(domain);
    }
  }

  _setStatusBadge(className, text) {
    const tag = this.elements.statusTag;
    if (!tag) return;
    tag.className = className;
    tag.textContent = text;
  }

  _showNotice(variant, message) {
    const banner = this.elements.noticeBanner;
    if (!banner) return;
    if (!message) {
      banner.style.display = 'none';
      banner.textContent = '';
      banner.className = 'pelagos-ov-notice';
      return;
    }
    banner.style.display = 'block';
    banner.className = `pelagos-ov-notice notice-${variant}`;
    banner.textContent = message;
  }

  async load({ useCache = true } = {}) {
    const el = this.elements;
    if (!el.card && !el.totalRecords) return null;

    this._reqSeq += 1;
    const seq = this._reqSeq;
    this._abortController?.abort();
    const controller = new AbortController();
    this._abortController = controller;

    this.isLoading = true;
    this._setStatusBadge('pelagos-tag tag-loading', 'SYNCING');
    if (el.refreshBtn) el.refreshBtn.disabled = true;

    const result = await fetchPlatformOverview({
      signal: controller.signal,
      useCache,
      fetchImpl: this.fetchImpl,
    });

    if (seq !== this._reqSeq || result.aborted) {
      return null;
    }

    this.isLoading = false;
    if (el.refreshBtn) el.refreshBtn.disabled = false;

    if (!result.ok || !result.data) {
      this.renderError(result.error || 'Overview service unavailable.');
      return null;
    }

    if (result.data.status === 'unavailable') {
      this.renderUnavailable(
        result.data.reason || 'Database tables unavailable.',
      );
      return result.data;
    }

    this.overviewData = result.data;
    this.render(result.data);
    return result.data;
  }

  renderError(message) {
    this._setStatusBadge('pelagos-tag tag-offline', 'OFFLINE');
    this._showNotice('error', `Backend overview unavailable: ${message}`);
    const el = this.elements;
    if (el.totalRecords) el.totalRecords.textContent = '—';
    if (el.sourcesCount) el.sourcesCount.textContent = '—';
    if (el.domainsCount) el.domainsCount.textContent = '—';
    if (el.latestSync) el.latestSync.textContent = 'Unavailable';
    if (el.freightSummary) {
      el.freightSummary.textContent =
        'Live freight coverage could not be verified while offline.';
    }
    if (el.indicatorsList) {
      el.indicatorsList.innerHTML = '';
    }
  }

  renderUnavailable(reason) {
    this._setStatusBadge('pelagos-tag tag-unmonitored', 'UNAVAILABLE');
    this._showNotice('warning', reason);
  }

  render(data) {
    const el = this.elements;
    const gs = data.global_summary || {};
    const domains = data.domains || {};
    const bd = gs.breakdown || {};

    const envDomain = domains.environmental || {};
    const isStaleEnv = envDomain.freshness === 'stale';
    this._setStatusBadge(
      isStaleEnv ? 'pelagos-tag tag-warning' : 'pelagos-tag tag-verified',
      isStaleEnv ? 'LIVE · PARTIAL/STALE' : 'LIVE DB',
    );

    if (el.totalRecords) {
      el.totalRecords.textContent = formatNum(gs.total_genuine_records);
    }
    if (el.sourcesCount) {
      el.sourcesCount.textContent = `${formatNum(gs.connected_operational_sources)}/${formatNum(gs.total_registered_source_feeds)}`;
    }
    const domainsWithRecords =
      gs.domains_with_records_count ?? gs.domains_populated_count ?? 0;
    const totalDomains = gs.total_domains ?? EXPLORER_DOMAIN_DEFS.length;
    const partialCount =
      gs.domains_partial_count ??
      Object.values(domains).filter((d) => d?.status === 'partial').length;
    if (el.domainsCount) {
      el.domainsCount.textContent =
        partialCount > 0
          ? `${formatNum(domainsWithRecords)}/${formatNum(totalDomains)} (${formatNum(partialCount)} PARTIAL)`
          : `${formatNum(domainsWithRecords)}/${formatNum(totalDomains)}`;
    }
    if (el.latestSync) {
      el.latestSync.textContent = formatCompactUtc(
        gs.latest_successful_ingestion_timestamp ||
          gs.latest_data_retrieval_timestamp,
      );
    }
    if (el.methodology) {
      const sourceObs =
        bd.source_observations_excluding_derived ??
        bd.raw_observations_excluding_derived_spread ??
        Math.max(
          0,
          Number(bd.raw_observations_total || 0) -
            Number(bd.freight_derived_spread || 0),
        );
      const freightPubs =
        bd.freight_publications_count ??
        domains.freight?.freight_publications_count ??
        0;
      el.methodology.textContent =
        `${formatNum(gs.total_genuine_records)} DB records (NOT independent raw obs) = ` +
        `${formatNum(sourceObs)} source obs (` +
        `${formatNum(bd.freight_underlying_corridor)} corridor freight + ` +
        `${formatNum(bd.trade_observations)} trade + ` +
        `${formatNum(bd.environmental_observations)} env + ` +
        `${formatNum(bd.bunker_observations)} bunker + ` +
        `${formatNum(bd.ais_position_observations)} AIS) + ` +
        `${formatNum(bd.freight_derived_spread)} derived spread + ` +
        `${formatNum(bd.registered_physical_entities)} physical entities (` +
        `${formatNum(bd.canonical_ports)} ports + ${formatNum(bd.canonical_vessels)} vessels). ` +
        `Excludes ${formatNum(bd.identifier_mappings_port_aliases)} aliases, ` +
        `${formatNum(freightPubs)} Parquet pubs, ${formatNum(bd.ingestion_audit_runs)} runs & ` +
        `${formatNum(bd.derived_dataset_rows)} dataset rows.`;
    }

    // 8 Domain Coverage Strip
    if (el.domainGrid) {
      el.domainGrid.innerHTML = '';
      for (const def of EXPLORER_DOMAIN_DEFS) {
        const d = domains[def.id] || {};
        const count = this._extractDomainMetricCount(def.id, d);
        const status = d.status || 'unavailable';

        const btn = this.doc.createElement('button');
        btn.type = 'button';
        btn.className = `pelagos-ov-domain-tile status-${status}`;
        if (!btn.dataset) btn.dataset = {};
        btn.dataset.domain = def.id;
        btn.title = `Open ${def.label} in Detailed Data Explorer`;

        const nameSpan = this.doc.createElement('span');
        nameSpan.className = 'ov-domain-name';
        nameSpan.textContent = def.shortLabel;

        const valSpan = this.doc.createElement('span');
        valSpan.className = 'ov-domain-val';
        valSpan.textContent =
          count !== null ? formatNum(count) : status.toUpperCase();

        const badgeSpan = this.doc.createElement('span');
        badgeSpan.className = 'ov-domain-status';
        badgeSpan.textContent = this._shortDomainStatus(def.id, d);

        btn.appendChild(nameSpan);
        btn.appendChild(valSpan);
        btn.appendChild(badgeSpan);

        btn.addEventListener('click', () => this._navigate(def.id));
        el.domainGrid.appendChild(btn);
      }
    }

    // Freight coverage summary
    const fr = domains.freight || {};
    if (el.freightSummary) {
      const corridors = Array.isArray(fr.monitored_corridors)
        ? fr.monitored_corridors
        : [];
      const range = fr.historical_range || {};
      const lines = corridors.map((c) => {
        const rate =
          c.latest_rate_value !== null && c.latest_rate_value !== undefined
            ? `$${Number(c.latest_rate_value).toFixed(2)} ${c.unit || 'USD/MT'}`
            : 'Unavailable';
        return `${c.route_code}: ${formatNum(c.observation_count)} obs (${rate} latest)`;
      });
      const horizons =
        (fr.available_forecast_horizons || []).join(', ') || 'None';
      const defaultBaseline = 'Persistence';
      const baselineLabel = fr.active_production_baseline || defaultBaseline;
      const rangeStr =
        range.start && range.end
          ? `${String(range.start).slice(0, 10)} → ${String(range.end).slice(0, 10)}`
          : 'No observations';
      el.freightSummary.textContent =
        lines.length > 0
          ? `${lines.join(' · ')} | Range: ${rangeStr} | Horizons: ${horizons} (${baselineLabel} baseline)`
          : 'No freight corridor observations stored in database.';
    }

    // Other domain indicators
    if (el.indicatorsList) {
      el.indicatorsList.innerHTML = '';
      const bk = domains.bunker || {};
      const vs = domains.vessels || {};
      const pt = domains.ports || {};
      const ds = domains.datasets || {};

      const bunkerPorts =
        (bk.ports_covered || []).map((p) => p.port_code).join(', ') || 'None';
      const items = [
        `Metocean Buoys: ${formatNum(envDomain.stations_count)} stations (${formatNum(envDomain.total_observations)} obs) · Freshness: ${String(envDomain.freshness || 'unavailable').toUpperCase()}`,
        `Bunker Fuel: ${formatNum(bk.total_observations)} indicative quotes (${bunkerPorts}); Corridor hubs: ${bk.corridor_hubs_covered_count > 0 ? `${bk.corridor_hubs_covered_count} covered` : 'UNAVAILABLE (USSEA/USHOU/JPYOK)'}`,
        `Vessel & AIS: ${formatNum(vs.position_observations)} historical positions (${formatNum(vs.canonical_vessels)} vessels) · Historical AIS (NOT open tonnage)`,
        `Ports & Routes: ${formatNum(pt.total_ports)} ports (${formatNum(pt.corridor_eligible_ports)} corridor-eligible), ${formatNum(pt.total_aliases)} aliases · Generalized MARNET`,
        `Forecast Datasets: ${formatNum(ds.total_dataset_rows)} strict-forward rows (${formatNum(ds.usable_features_count)} features; ${(ds.horizon_labels || []).join('/')}) · Persistence baseline`,
      ];

      for (const text of items) {
        const li = this.doc.createElement('li');
        li.textContent = text;
        el.indicatorsList.appendChild(li);
      }
    }

    // Honest staleness / partial coverage notice
    if (gs.total_genuine_records === 0) {
      this._showNotice(
        'warning',
        'Database currently contains zero observations across all monitored domains.',
      );
    } else if (
      isStaleEnv ||
      domains.bunker?.corridor_hubs_covered_count === 0
    ) {
      this._showNotice(
        'info',
        'Active limitations: corridor bunker hubs have zero ingested quotes, AIS telemetry is historical, and experimental ML candidates are unpromoted.',
      );
    } else {
      this._showNotice(null, null);
    }
  }

  _extractDomainMetricCount(domainId, domainData) {
    switch (domainId) {
      case 'freight':
      case 'trade':
      case 'environmental':
      case 'bunker':
        return domainData.total_observations ?? 0;
      case 'vessels':
        return (
          domainData.position_observations ?? domainData.canonical_vessels ?? 0
        );
      case 'ports':
        return domainData.total_ports ?? 0;
      case 'datasets':
        return domainData.total_dataset_rows ?? 0;
      case 'sources':
        return domainData.total_ingestion_runs ?? 0;
      default:
        return null;
    }
  }

  _shortDomainStatus(domainId, domainData) {
    if (domainId === 'bunker' && domainData.corridor_hubs_covered_count === 0) {
      return domainData.total_observations > 0 ? 'NON-CORRIDOR' : 'EMPTY';
    }
    if (domainId === 'vessels') {
      return 'HISTORICAL';
    }
    if (domainId === 'environmental' && domainData.freshness) {
      return String(domainData.freshness).toUpperCase();
    }
    if (domainId === 'datasets') {
      return domainData.status === 'populated'
        ? 'READY'
        : String(domainData.status || 'UNAVAIL').toUpperCase();
    }
    return String(domainData.status || 'UNKNOWN').toUpperCase();
  }

  destroy() {
    this._abortController?.abort();
    for (const { el, event, handler } of this._boundListeners) {
      try {
        el.removeEventListener(event, handler);
      } catch {
        /* best effort */
      }
    }
    this._boundListeners = [];
  }
}
