/**
 * @module ui/pelagosExplorer
 * @description Full-view Detailed Data Explorer (#/explorer) controller backed by server-side pagination APIs.
 */

import { fetchExplorerDomain } from './pelagosApi.js';
import {
  DEFAULT_EXPLORER_DOMAIN,
  buildExplorerHash,
  parseExplorerHash,
} from './explorerRouter.js';
import {
  DOMAIN_CONFIGS,
  buildDomainFilterDescriptors,
  escapeHtml,
  formatNumberCell,
} from './pelagosExplorerDomains.js';

export class PelagosExplorerController {
  /**
   * @param {object} [options]
   * @param {Document} [options.document]
   * @param {Function} [options.onReturnToGlobe]
   * @param {Function} [options.onDomainChange]
   * @param {Function} [options.fetchImpl]
   */
  constructor({
    document = globalThis.document,
    onReturnToGlobe = null,
    onDomainChange = null,
    fetchImpl = globalThis.fetch,
  } = {}) {
    this.doc = document;
    this.onReturnToGlobe = onReturnToGlobe;
    this.onDomainChange = onDomainChange;
    this.fetchImpl = fetchImpl;

    this.active = false;
    this.domain = DEFAULT_EXPLORER_DOMAIN;
    this.isValidDomain = true;
    this.page = 1;
    this.pageSize = 25;
    this.sortBy = DOMAIN_CONFIGS[DEFAULT_EXPLORER_DOMAIN].defaultSortBy;
    this.sortOrder = DOMAIN_CONFIGS[DEFAULT_EXPLORER_DOMAIN].defaultSortOrder;
    this.search = '';
    this.filters = {
      ...DOMAIN_CONFIGS[DEFAULT_EXPLORER_DOMAIN].defaultFilters,
    };

    this.lastPayload = null;
    this.selectedRow = null;
    this.selectedBenchmarkRoute = 'PNW-JPN-BULK';
    this.selectedBenchmarkHorizon = 1;
    this.showBenchmarkStatsDetails = false;
    this._abortController = null;
    this._reqSeq = 0;
    this._searchTimer = null;
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
      root: d.getElementById('pelagos-explorer-view'),
      returnBtn: d.getElementById('pelagos-explorer-return-btn'),
      summaryBadge: d.getElementById('pelagos-explorer-summary-badge'),
      kicker: d.getElementById('pelagos-explorer-kicker'),
      title: d.getElementById('pelagos-explorer-title'),
      tabList: d.getElementById('explorer-domain-tabs'),
      kpiBar: d.getElementById('explorer-kpi-bar'),
      statusBanner: d.getElementById('explorer-status-banner'),
      searchInput: d.getElementById('explorer-search-input'),
      dynamicFilters: d.getElementById('explorer-dynamic-filters'),
      resetBtn: d.getElementById('explorer-reset-filters-btn'),
      exportCsvBtn: d.getElementById('explorer-export-csv-btn'),
      tableHead: d.getElementById('explorer-table-head'),
      tableBody: d.getElementById('explorer-table-body'),
      loadingOverlay: d.getElementById('explorer-loading-spinner'),
      emptyState: d.getElementById('explorer-empty-state'),
      pageStart: d.getElementById('explorer-page-start'),
      pageEnd: d.getElementById('explorer-page-end'),
      totalCount: d.getElementById('explorer-total-count'),
      prevPageBtn: d.getElementById('explorer-prev-page-btn'),
      nextPageBtn: d.getElementById('explorer-next-page-btn'),
      currentPage: d.getElementById('explorer-current-page'),
      totalPages: d.getElementById('explorer-total-pages'),
      pageSizeSelect: d.getElementById('explorer-page-size-select'),
      provenanceContent: d.getElementById('explorer-provenance-content'),
      tablePane: d.querySelector?.('.explorer-table-pane') || null,
      benchmarkWorkspace: d.getElementById('explorer-benchmark-workspace'),
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

    this._bind(el.returnBtn, 'click', () => {
      if (typeof this.onReturnToGlobe === 'function') {
        this.onReturnToGlobe();
      } else if (typeof window !== 'undefined' && window.location) {
        window.location.hash = '';
      }
    });

    if (el.tabList?.querySelectorAll) {
      el.tabList.querySelectorAll('.domain-tab').forEach((btn) => {
        this._bind(btn, 'click', () => {
          const dom = btn.dataset?.domain;
          if (dom) {
            if (typeof this.onDomainChange === 'function') {
              this.onDomainChange(dom);
            } else {
              this.setDomain(dom);
            }
          }
        });
      });
    }

    this._bind(el.searchInput, 'input', (e) => {
      this.search = e?.target?.value || '';
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => {
        this.page = 1;
        void this.loadDomainData();
      }, 220);
    });

    this._bind(el.resetBtn, 'click', () => {
      this.resetCurrentFilters();
    });

    this._bind(el.exportCsvBtn, 'click', () => {
      this.exportCurrentPageCsv();
    });

    this._bind(el.prevPageBtn, 'click', () => {
      if (this.page > 1) {
        this.page -= 1;
        void this.loadDomainData();
      }
    });

    this._bind(el.nextPageBtn, 'click', () => {
      const maxPages = this.lastPayload?.total_pages || 1;
      if (this.page < maxPages) {
        this.page += 1;
        void this.loadDomainData();
      }
    });

    this._bind(el.pageSizeSelect, 'change', (e) => {
      const next = Number(e?.target?.value);
      if (Number.isFinite(next) && next >= 1 && next <= 100) {
        this.pageSize = next;
        this.page = 1;
        void this.loadDomainData();
      }
    });
  }

  /** Synchronize explorer visibility and domain state from a URL hash string. */
  syncFromHash(hash) {
    const parsed = parseExplorerHash(hash);
    if (!parsed.active) {
      this.close();
      return parsed;
    }
    this.open({
      domain: parsed.domain,
      isValidDomain: parsed.isValidDomain,
    });
    return parsed;
  }

  /** Update summary pill counts from Overview API payload when available. */
  updateOverviewCounts(overview) {
    const el = this.elements;
    if (!overview) return;
    const gs = overview.global_summary;
    const bd = gs?.breakdown || {};
    const domains = overview.domains || {};
    if (el.summaryBadge && gs) {
      const spreadObs = Number(bd.freight_derived_spread || 0);
      const rawTotal = Number(bd.raw_observations_total || 0);
      const sourceObs =
        bd.source_observations_excluding_derived ??
        bd.raw_observations_excluding_derived_spread ??
        Math.max(0, rawTotal - spreadObs);
      const entities = Number(bd.registered_physical_entities || 0);
      const withData =
        gs.domains_with_records_count ?? gs.domains_populated_count ?? 0;
      const totalDom = gs.total_domains || 8;
      const partialCnt =
        gs.domains_partial_count ??
        Object.values(domains).filter((d) => d?.status === 'partial').length;
      const partialSuffix = partialCnt > 0 ? ` · ${partialCnt} PARTIAL` : '';
      el.summaryBadge.textContent =
        rawTotal > 0 || entities > 0
          ? `${sourceObs.toLocaleString('en-US')} SOURCE OBS + ${spreadObs.toLocaleString('en-US')} SPREAD + ${entities.toLocaleString('en-US')} ENTITIES · ${withData}/${totalDom} W/ DATA${partialSuffix}`
          : `${Number(gs.total_genuine_records || 0).toLocaleString('en-US')} DB RECORDS · ${withData}/${totalDom} W/ DATA${partialSuffix}`;
    }
    if (el.tabList?.querySelectorAll) {
      el.tabList.querySelectorAll('.domain-tab').forEach((btn) => {
        const id = btn.dataset?.domain;
        const pill = btn.querySelector('.tab-pill');
        if (!pill || !id || !domains[id]) return;
        const d = domains[id];
        const count =
          d.total_observations ??
          d.position_observations ??
          d.total_ports ??
          d.total_dataset_rows ??
          d.total_ingestion_runs ??
          0;
        pill.textContent = Number(count).toLocaleString('en-US');
      });
    }
  }

  open({ domain = DEFAULT_EXPLORER_DOMAIN, isValidDomain = true } = {}) {
    const domainChanged = domain !== this.domain || !this.active;
    this.active = true;
    this.isValidDomain = isValidDomain;

    if (this.elements.root) {
      this.elements.root.hidden = false;
    }
    if (this.doc?.body?.classList) {
      this.doc.body.classList.add('pelagos-explorer-active');
    }

    if (domainChanged || !this.lastPayload) {
      this.setDomain(domain, { isValidDomain });
    }
  }

  close() {
    this.active = false;
    this._abortController?.abort();
    clearTimeout(this._searchTimer);
    if (this.elements.root) {
      this.elements.root.hidden = true;
    }
    if (this.doc?.body?.classList) {
      this.doc.body.classList.remove('pelagos-explorer-active');
    }
  }

  setDomain(domain, { isValidDomain = true } = {}) {
    const clean = String(domain || DEFAULT_EXPLORER_DOMAIN)
      .trim()
      .toLowerCase();
    this.domain = clean;
    this.isValidDomain = isValidDomain && Boolean(DOMAIN_CONFIGS[clean]);
    const cfg = DOMAIN_CONFIGS[clean];

    this.page = 1;
    this.search = '';
    if (this.elements.searchInput) this.elements.searchInput.value = '';
    this.selectedRow = null;

    if (cfg) {
      this.sortBy = cfg.defaultSortBy;
      this.sortOrder = cfg.defaultSortOrder;
      this.filters = { ...(cfg.defaultFilters || {}) };
    } else {
      this.sortBy = 'id';
      this.sortOrder = 'desc';
      this.filters = {};
    }

    this._syncDomainTabs();
    if (this.domain !== 'datasets') {
      this._hideBenchmarkWorkspace();
    }
    void this.loadDomainData();
  }

  resetCurrentFilters() {
    const cfg = DOMAIN_CONFIGS[this.domain];
    this.page = 1;
    this.search = '';
    if (this.elements.searchInput) this.elements.searchInput.value = '';
    this.selectedRow = null;
    if (cfg) {
      this.sortBy = cfg.defaultSortBy;
      this.sortOrder = cfg.defaultSortOrder;
      this.filters = { ...(cfg.defaultFilters || {}) };
    } else {
      this.filters = {};
    }
    void this.loadDomainData();
  }

  _syncDomainTabs() {
    const el = this.elements;
    const cfg = DOMAIN_CONFIGS[this.domain];
    if (el.title) {
      el.title.textContent = cfg
        ? `DETAILED DATA EXPLORER · ${cfg.title.toUpperCase()}`
        : 'DETAILED DATA EXPLORER';
    }
    if (el.kicker) {
      el.kicker.textContent = cfg
        ? cfg.kicker
        : 'PELAGOS MARITIME INTELLIGENCE · UNKNOWN DOMAIN';
    }
    if (el.tabList?.querySelectorAll) {
      el.tabList.querySelectorAll('.domain-tab').forEach((btn) => {
        const isCurrent = btn.dataset?.domain === this.domain;
        btn.classList?.toggle('active', isCurrent);
        btn.setAttribute?.('aria-selected', isCurrent ? 'true' : 'false');
      });
    }
  }

  async loadDomainData() {
    const el = this.elements;
    this._reqSeq += 1;
    const seq = this._reqSeq;

    this._abortController?.abort();
    const controller = new AbortController();
    this._abortController = controller;

    if (el.loadingOverlay) el.loadingOverlay.style.display = 'flex';
    if (el.emptyState) el.emptyState.style.display = 'none';
    this._renderBanner(null, null);

    if (!this.isValidDomain && !DOMAIN_CONFIGS[this.domain]) {
      if (el.loadingOverlay) el.loadingOverlay.style.display = 'none';
      this.renderErrorState(
        `Unknown explorer domain "${this.domain}". Select one of the eight canonical Pelagos domains above.`,
      );
      return null;
    }

    const queryParams = {
      page: this.page,
      page_size: this.pageSize,
      sort_by: this.sortBy,
      sort_order: this.sortOrder,
      search: this.search.trim() || undefined,
      ...this.filters,
    };

    const result = await fetchExplorerDomain(this.domain, queryParams, {
      signal: controller.signal,
      fetchImpl: this.fetchImpl,
    });

    if (seq !== this._reqSeq || result.aborted) return null;
    if (el.loadingOverlay) el.loadingOverlay.style.display = 'none';

    if (!result.ok || !result.data) {
      this.renderErrorState(
        result.error ||
          `Failed to query backend endpoint for "${this.domain}".`,
      );
      return null;
    }

    this.lastPayload = result.data;
    this.renderPayload(result.data);
    return result.data;
  }

  _renderBanner(variant, message) {
    const b = this.elements.statusBanner;
    if (!b) return;
    if (!message) {
      b.style.display = 'none';
      b.textContent = '';
      b.className = 'explorer-banner';
      return;
    }
    b.style.display = 'block';
    b.className = `explorer-banner banner-${variant}`;
    b.textContent = message;
  }

  renderErrorState(message) {
    const el = this.elements;
    this._renderBanner('error', message);
    if (el.tableHead) el.tableHead.innerHTML = '';
    if (el.tableBody) el.tableBody.innerHTML = '';
    if (el.kpiBar) el.kpiBar.innerHTML = '';
    if (el.emptyState) {
      el.emptyState.style.display = 'flex';
      el.emptyState.textContent = message;
    }
    this._updatePaginationControls({
      page: 1,
      page_size: this.pageSize,
      total_count: 0,
      total_pages: 0,
      items: [],
    });
    if (el.provenanceContent) {
      el.provenanceContent.innerHTML = `<div class="prov-notice notice-error">${escapeHtml(message)}</div>`;
    }
    this._hideBenchmarkWorkspace();
  }

  renderPayload(payload) {
    this._renderKpis(payload);
    this._renderDynamicFilters(payload);
    this._renderTable(payload);
    this._updatePaginationControls(payload);
    this._renderProvenanceDrawer(payload);
    this._renderBenchmarkWorkspace(payload);

    if (payload.status === 'unavailable') {
      this._renderBanner(
        'warning',
        `UNAVAILABLE: ${payload.reason || 'Underlying dataset or capability unavailable.'}`,
      );
    } else if (payload.availability_status === 'partial') {
      this._renderBanner(
        'warning',
        `PARTIAL COVERAGE: ${payload.integrity_notices?.[0] || 'Coverage incomplete for primary corridors.'}`,
      );
    } else if (
      this.domain === 'vessels' ||
      this.domain === 'ports' ||
      this.domain === 'datasets'
    ) {
      this._renderBanner('info', payload.integrity_notices?.[0] || '');
    } else if (payload.status === 'empty' && payload.reason) {
      this._renderBanner('info', payload.reason);
    }
  }

  _renderKpis(payload) {
    const bar = this.elements.kpiBar;
    if (!bar) return;
    const summary = payload.summary || {};
    const entries = [
      {
        label: 'MATCHING RECORDS',
        val: Number(payload.total_count || 0).toLocaleString('en-US'),
      },
      {
        label: 'AVAILABILITY',
        val: String(
          payload.availability_status || payload.status || 'unknown',
        ).toUpperCase(),
      },
    ];

    for (const [key, val] of Object.entries(summary)) {
      if (entries.length >= 5) break;
      if (val === null || val === undefined || typeof val === 'object')
        continue;
      entries.push({
        label: key.replaceAll('_', ' ').toUpperCase(),
        val:
          typeof val === 'number' ? val.toLocaleString('en-US') : String(val),
      });
    }

    bar.innerHTML = entries
      .map(
        (k) => `
      <div class="explorer-kpi-card">
        <span class="kpi-label">${escapeHtml(k.label)}</span>
        <span class="kpi-value">${escapeHtml(k.val)}</span>
      </div>`,
      )
      .join('');
  }

  _renderDynamicFilters(payload) {
    const container = this.elements.dynamicFilters;
    if (!container) return;
    container.innerHTML = '';

    const descriptors = buildDomainFilterDescriptors(
      this.domain,
      payload.filter_options || {},
      this.filters,
    );

    for (const desc of descriptors) {
      const wrap = this.doc.createElement('label');
      wrap.className = 'toolbar-filter-field';

      const lbl = this.doc.createElement('span');
      lbl.className = 'filter-field-label';
      lbl.textContent = desc.label;
      wrap.appendChild(lbl);

      let control;
      if (desc.type === 'select') {
        control = this.doc.createElement('select');
        control.className = 'pelagos-select select-compact';
        control.setAttribute?.('aria-label', desc.label);
        for (const optDef of desc.options || []) {
          const opt = this.doc.createElement('option');
          opt.value = optDef.value;
          opt.textContent = optDef.label;
          control.appendChild(opt);
        }
        control.value = String(desc.value ?? '');
      } else {
        control = this.doc.createElement('input');
        control.type = desc.type || 'text';
        control.className = 'pelagos-filter-input input-compact';
        control.setAttribute?.('aria-label', desc.label);
        control.value = String(desc.value ?? '');
      }

      control.addEventListener('change', (e) => {
        const nextVal = e?.target?.value ?? '';
        if (nextVal === '') {
          delete this.filters[desc.key];
        } else {
          this.filters[desc.key] = nextVal;
        }
        this.page = 1;
        this.selectedRow = null;
        void this.loadDomainData();
      });

      wrap.appendChild(control);
      container.appendChild(wrap);
    }
  }

  _renderTable(payload) {
    const el = this.elements;
    const cfg = DOMAIN_CONFIGS[this.domain];
    const columns = cfg?.columns || [];
    const allowedSort = new Set(payload.allowed_sort_fields || []);

    if (el.tableHead) {
      el.tableHead.innerHTML = '';
      const tr = this.doc.createElement('tr');
      for (const col of columns) {
        const th = this.doc.createElement('th');
        if (col.align === 'right') th.className = 'text-right';
        const sortable = col.sortBy && allowedSort.has(col.sortBy);
        if (sortable) {
          const btn = this.doc.createElement('button');
          btn.type = 'button';
          btn.className = `table-sort-btn${this.sortBy === col.sortBy ? ' active' : ''}`;
          const arrow =
            this.sortBy === col.sortBy
              ? this.sortOrder === 'asc'
                ? ' ▲'
                : ' ▼'
              : '';
          btn.textContent = `${col.label}${arrow}`;
          btn.addEventListener('click', () => {
            if (this.sortBy === col.sortBy) {
              this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
            } else {
              this.sortBy = col.sortBy;
              this.sortOrder = 'desc';
            }
            this.page = 1;
            void this.loadDomainData();
          });
          th.appendChild(btn);
        } else {
          th.textContent = col.label;
        }
        tr.appendChild(th);
      }
      el.tableHead.appendChild(tr);
    }

    if (el.tableBody) {
      el.tableBody.innerHTML = '';
      const items = Array.isArray(payload.items) ? payload.items : [];
      if (items.length === 0) {
        if (el.emptyState) {
          el.emptyState.style.display = 'flex';
          el.emptyState.textContent =
            payload.reason ||
            'No records found in this domain for the current filters.';
        }
        return;
      }
      if (el.emptyState) el.emptyState.style.display = 'none';

      items.forEach((row, idx) => {
        const tr = this.doc.createElement('tr');
        tr.className = 'explorer-data-row';
        tr.tabIndex = 0;
        if (this.selectedRow === row) tr.classList.add('is-selected');

        for (const col of columns) {
          const td = this.doc.createElement('td');
          if (col.align === 'right') td.className = 'text-right font-mono';
          const formatted = col.format ? col.format(row) : row[col.key];
          td.textContent =
            formatted === null || formatted === undefined || formatted === ''
              ? 'NULL'
              : String(formatted);
          tr.appendChild(td);
        }

        const selectThisRow = () => {
          this.selectedRow = row;
          if (el.tableBody?.querySelectorAll) {
            el.tableBody
              .querySelectorAll('.explorer-data-row')
              .forEach((rEl, rIdx) => {
                rEl.classList?.toggle('is-selected', rIdx === idx);
              });
          }
          this._renderProvenanceDrawer(payload);
        };

        tr.addEventListener('click', selectThisRow);
        tr.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault?.();
            selectThisRow();
          }
        });

        el.tableBody.appendChild(tr);
      });
    }
  }

  _updatePaginationControls(payload) {
    const el = this.elements;
    const total = Number(payload.total_count || 0);
    const page = Number(payload.page || 1);
    const pageSize = Number(payload.page_size || this.pageSize);
    const totalPages = Number(payload.total_pages || 0);
    const countOnPage = Array.isArray(payload.items) ? payload.items.length : 0;
    const start =
      total === 0 || countOnPage === 0 ? 0 : (page - 1) * pageSize + 1;
    const end = total === 0 || countOnPage === 0 ? 0 : start + countOnPage - 1;

    if (el.pageStart) el.pageStart.textContent = String(start);
    if (el.pageEnd) el.pageEnd.textContent = String(end);
    if (el.totalCount)
      el.totalCount.textContent = total.toLocaleString('en-US');
    if (el.currentPage)
      el.currentPage.textContent = String(total === 0 ? 0 : page);
    if (el.totalPages) el.totalPages.textContent = String(totalPages);
    if (el.prevPageBtn) el.prevPageBtn.disabled = page <= 1 || total === 0;
    if (el.nextPageBtn)
      el.nextPageBtn.disabled = page >= totalPages || total === 0;
    if (el.pageSizeSelect) el.pageSizeSelect.value = String(pageSize);
  }

  _renderProvenanceDrawer(payload) {
    const container = this.elements.provenanceContent;
    if (!container) return;

    const sections = [];

    // 1. Integrity rules & domain notices
    const notices = Array.isArray(payload.integrity_notices)
      ? payload.integrity_notices
      : [];
    if (notices.length > 0) {
      sections.push(`
        <div class="drawer-section">
          <div class="drawer-section-title">DOMAIN INTEGRITY & LIMITATIONS</div>
          <ul class="drawer-notices-list">
            ${notices.map((n) => `<li>${escapeHtml(n)}</li>`).join('')}
          </ul>
        </div>`);
    }

    // 2. Domain-specific secondary panels
    const sec = payload.secondary_panels || {};
    if (Array.isArray(sec.corridor_hub_coverage)) {
      sections.push(`
        <div class="drawer-section">
          <div class="drawer-section-title">CORRIDOR BUNKER HUB COVERAGE</div>
          <div class="drawer-kv-list">
            ${sec.corridor_hub_coverage
              .map(
                (h) => `
              <div class="drawer-kv-item">
                <span class="kv-k">${escapeHtml(h.corridor_code)} · ${escapeHtml(h.port_code || h.port_name)}</span>
                <span class="kv-v status-${escapeHtml(h.status)}">${h.status === 'available' ? `${h.observation_count} quotes` : 'UNAVAILABLE'}</span>
              </div>`,
              )
              .join('')}
          </div>
        </div>`);
    }

    if (sec.routing_topology) {
      const rt = sec.routing_topology;
      sections.push(`
        <div class="drawer-section">
          <div class="drawer-section-title">MARNET TOPOLOGY QUALIFICATION</div>
          <p class="drawer-prose">${escapeHtml(rt.topology_warning)}</p>
          <div class="drawer-kv-list">
            <div class="drawer-kv-item"><span class="kv-k">Default Mode</span><span class="kv-v">${escapeHtml(rt.default_routing_mode)}</span></div>
            <div class="drawer-kv-item"><span class="kv-k">MARNET Cache</span><span class="kv-v">${rt.marnet_cache_present ? 'Present' : 'Not Cached'}</span></div>
            <div class="drawer-kv-item"><span class="kv-k">Certification</span><span class="kv-v">Non-Certified</span></div>
          </div>
        </div>`);
    }

    if (sec.benchmarks) {
      const bm = sec.benchmarks;
      const rows = (bm.c32_highlights || []).slice(0, 6);
      const anyArtifacts = Boolean(bm.c32_available || bm.c33_available);
      sections.push(`
        <div class="drawer-section">
          <div class="drawer-section-title">BASELINE VS EXPERIMENTAL BENCHMARKS</div>
          <div class="drawer-badge-baseline">PRODUCTION BASELINE: ${escapeHtml(String(bm.production_baseline || 'persistence').toUpperCase())}</div>
          <p class="drawer-prose">${escapeHtml(bm.production_note || 'No experimental model has been promoted to production.')}</p>
          ${
            !anyArtifacts
              ? '<div class="prov-notice notice-warning">Benchmark artifacts are currently unavailable (c32_available=false, c33_available=false).</div>'
              : ''
          }
          ${
            rows.length > 0
              ? `<div class="drawer-kv-list">
                  ${rows
                    .map(
                      (r) => `
                    <div class="drawer-kv-item">
                      <span class="kv-k">${escapeHtml(r.route_code)} T+${escapeHtml(r.horizon_months)} (N=${escapeHtml(r.common_sample_size)})</span>
                      <span class="kv-v">Pers ${escapeHtml(this._formatBenchmarkNumber(r.persistence_mae))} · Exp (${escapeHtml(r.best_experimental_candidate)}): ${escapeHtml(this._formatBenchmarkNumber(r.best_experimental_mae))}</span>
                    </div>`,
                    )
                    .join('')}
                </div>
                <p class="drawer-prose">Full C3.2/C3.3 statistical evidence (DM/HLN, Holm-Bonferroni, 95% CI, LightGBM unavailability, regime audit) is displayed in the Datasets benchmark workspace.</p>`
              : ''
          }
        </div>`);
    }

    if (Array.isArray(sec.feature_inventory)) {
      sections.push(`
        <div class="drawer-section">
          <div class="drawer-section-title">7-DOMAIN FEATURE COVERAGE AUDIT</div>
          <div class="drawer-kv-list">
            ${sec.feature_inventory
              .map(
                (f) => `
              <div class="drawer-kv-item">
                <span class="kv-k">${escapeHtml(f.domain_name)}</span>
                <span class="kv-v">${f.missingness_rate_pct}% miss · ${escapeHtml(f.usability_status)}</span>
              </div>`,
              )
              .join('')}
          </div>
        </div>`);
    }

    // 3. Selected record inspection
    if (this.selectedRow) {
      const entries = Object.entries(this.selectedRow).map(([k, v]) => {
        const displayVal =
          v === null || v === undefined
            ? 'NULL (Missing)'
            : typeof v === 'object'
              ? JSON.stringify(v)
              : String(v);
        return `
          <div class="drawer-kv-item record-field">
            <span class="kv-k">${escapeHtml(k)}</span>
            <span class="kv-v${v === null || v === undefined ? ' val-null' : ''}">${escapeHtml(displayVal)}</span>
          </div>`;
      });
      sections.push(`
        <div class="drawer-section">
          <div class="drawer-section-title">SELECTED RECORD PROVENANCE</div>
          <div class="drawer-kv-list">${entries.join('')}</div>
        </div>`);
    } else {
      sections.push(`
        <div class="drawer-section">
          <div class="drawer-section-title">RECORD INSPECTOR</div>
          <p class="drawer-prose">Select any table row to inspect full record-level provenance, nulls, and timestamps.</p>
        </div>`);
    }

    container.innerHTML = sections.join('');
  }

  _formatBenchmarkNumber(value, suffix = '') {
    if (value === null || value === undefined || value === '') {
      return '— (NULL)';
    }
    if (typeof value === 'boolean') {
      return value ? 'true' : 'false';
    }
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) return '— (NULL)';
      return `${String(value)}${suffix}`;
    }
    return `${String(value)}${suffix}`;
  }

  _classificationBadgeClass(classification, isBaseline = false) {
    if (isBaseline || classification === 'PRODUCTION_BASELINE') {
      return 'bench-badge-baseline';
    }
    if (classification === 'NOT_BETTER_THAN_BASELINE') {
      return 'bench-badge-not-better';
    }
    if (classification === 'INCONCLUSIVE') {
      return 'bench-badge-inconclusive';
    }
    return 'bench-badge-unavailable';
  }

  _hideBenchmarkWorkspace() {
    const el = this.elements;
    if (el.benchmarkWorkspace) {
      el.benchmarkWorkspace.hidden = true;
      el.benchmarkWorkspace.innerHTML = '';
    }
    el.tablePane?.classList?.remove('has-benchmarks');
  }

  _findBenchmarkExperiment(experiments, routeCode, horizonMonths) {
    if (!Array.isArray(experiments)) return null;
    return (
      experiments.find(
        (exp) =>
          exp?.route_code === routeCode &&
          Number(exp?.horizon_months) === Number(horizonMonths),
      ) || null
    );
  }

  _collectRouteAndHorizonOptions(benchmarks) {
    const allExps = [
      ...(benchmarks?.c32_experiments || []),
      ...(benchmarks?.c33_experiments || []),
    ];
    const routes = [];
    const horizons = [];
    for (const exp of allExps) {
      if (exp?.route_code && !routes.includes(exp.route_code)) {
        routes.push(exp.route_code);
      }
      const h = Number(exp?.horizon_months);
      if (Number.isFinite(h) && !horizons.includes(h)) {
        horizons.push(h);
      }
    }
    horizons.sort((a, b) => a - b);
    return { routes, horizons };
  }

  _renderBenchmarkControls(container, routes, horizons) {
    const controlsBar = this.doc.createElement('div');
    controlsBar.className = 'benchmark-controls-bar';

    const routeGroup = this.doc.createElement('div');
    routeGroup.className = 'benchmark-selector-group';
    const routeLabel = this.doc.createElement('span');
    routeLabel.className = 'benchmark-selector-label';
    routeLabel.textContent = 'CORRIDOR:';
    routeGroup.appendChild(routeLabel);

    for (const route of routes) {
      const btn = this.doc.createElement('button');
      const isSelected = route === this.selectedBenchmarkRoute;
      btn.type = 'button';
      btn.className = `benchmark-selector-btn benchmark-route-btn${isSelected ? ' active' : ''}`;
      btn.dataset.route = route;
      btn.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
      btn.textContent = route;
      btn.addEventListener('click', () => {
        this.selectedBenchmarkRoute = route;
        if (this.lastPayload) this._renderBenchmarkWorkspace(this.lastPayload);
      });
      routeGroup.appendChild(btn);
    }
    controlsBar.appendChild(routeGroup);

    const horizonGroup = this.doc.createElement('div');
    horizonGroup.className = 'benchmark-selector-group';
    const horizonLabel = this.doc.createElement('span');
    horizonLabel.className = 'benchmark-selector-label';
    horizonLabel.textContent = 'HORIZON:';
    horizonGroup.appendChild(horizonLabel);

    for (const h of horizons) {
      const btn = this.doc.createElement('button');
      const isSelected = Number(h) === Number(this.selectedBenchmarkHorizon);
      btn.type = 'button';
      btn.className = `benchmark-selector-btn benchmark-horizon-btn${isSelected ? ' active' : ''}`;
      btn.dataset.horizon = String(h);
      btn.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
      btn.textContent = `T+${h}`;
      btn.addEventListener('click', () => {
        this.selectedBenchmarkHorizon = Number(h);
        if (this.lastPayload) this._renderBenchmarkWorkspace(this.lastPayload);
      });
      horizonGroup.appendChild(btn);
    }
    controlsBar.appendChild(horizonGroup);

    const toggleBtn = this.doc.createElement('button');
    toggleBtn.type = 'button';
    toggleBtn.className = `benchmark-stats-toggle${this.showBenchmarkStatsDetails ? ' active' : ''}`;
    toggleBtn.setAttribute(
      'aria-pressed',
      this.showBenchmarkStatsDetails ? 'true' : 'false',
    );
    toggleBtn.textContent = this.showBenchmarkStatsDetails
      ? 'Hide Statistical Details & Regime Breakdown'
      : 'Expand Statistical Details & Regime Breakdown';
    toggleBtn.addEventListener('click', () => {
      this.showBenchmarkStatsDetails = !this.showBenchmarkStatsDetails;
      if (this.lastPayload) this._renderBenchmarkWorkspace(this.lastPayload);
    });
    controlsBar.appendChild(toggleBtn);

    container.appendChild(controlsBar);
  }

  _buildStatsDetailTableHtml(models, title) {
    const rows = Array.isArray(models)
      ? models.filter((m) => m.is_available !== false)
      : [];
    return `
      <div class="benchmark-subsection">
        <div class="benchmark-subsection-title">${escapeHtml(title)}</div>
        <p class="benchmark-method-note">
          DM/HLN two-sided hypothesis tests and Holm-Bonferroni family-wise error correction compare candidate errors against Persistence. An insignificant or INCONCLUSIVE result (p &gt;= 0.05 or bootstrap CI containing 0) does NOT prove statistical equivalence or candidate superiority.
        </p>
        <div class="benchmark-table-scroll">
          <table class="benchmark-table benchmark-table-compact">
            <thead>
              <tr>
                <th>Model</th>
                <th>DM Stat</th>
                <th>HLN Stat</th>
                <th>Raw p-value</th>
                <th>Holm p-value</th>
                <th>Holm Sig</th>
                <th>Favors Cand.</th>
                <th>Favors Base.</th>
                <th>95% Bootstrap CI</th>
                <th>Block / Boot</th>
                <th>Classification</th>
              </tr>
            </thead>
            <tbody>
              ${rows
                .map((m) => {
                  const ciText =
                    m.bootstrap_ci_lower === null &&
                    m.bootstrap_ci_upper === null
                      ? '— (NULL)'
                      : `[${this._formatBenchmarkNumber(m.bootstrap_ci_lower)}, ${this._formatBenchmarkNumber(m.bootstrap_ci_upper)}] (contains_zero=${this._formatBenchmarkNumber(m.ci_contains_zero)})`;
                  const blockBoot =
                    m.block_length === null && m.bootstrap_iterations === null
                      ? '— (NULL)'
                      : `b=${this._formatBenchmarkNumber(m.block_length)} / n=${this._formatBenchmarkNumber(m.bootstrap_iterations)}`;
                  const clsBadge = this._classificationBadgeClass(
                    m.research_classification,
                    m.is_baseline,
                  );
                  return `
                    <tr class="${m.is_baseline ? 'is-baseline-row' : ''}">
                      <td><strong>${escapeHtml(m.display_name || m.model_name)}</strong></td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.dm_statistic))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.hln_statistic))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.p_value))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.holm_adjusted_p_value))}</td>
                      <td>${escapeHtml(this._formatBenchmarkNumber(m.holm_statistically_significant))}</td>
                      <td>${escapeHtml(this._formatBenchmarkNumber(m.favors_candidate))}</td>
                      <td>${escapeHtml(this._formatBenchmarkNumber(m.favors_baseline))}</td>
                      <td class="num-cell">${escapeHtml(ciText)}</td>
                      <td class="num-cell">${escapeHtml(blockBoot)}</td>
                      <td><span class="bench-badge ${clsBadge}">${escapeHtml(m.research_classification || m.status || '—')}</span></td>
                    </tr>`;
                })
                .join('')}
            </tbody>
          </table>
        </div>
      </div>`;
  }

  _buildRegimeBreakdownHtml(breakdownList, title) {
    if (!Array.isArray(breakdownList) || breakdownList.length === 0) {
      return '';
    }
    return `
      <div class="benchmark-subsection">
        <div class="benchmark-subsection-title">${escapeHtml(title)}</div>
        <div class="benchmark-regime-grid">
          ${breakdownList
            .map(
              (reg) => `
            <div class="benchmark-regime-card">
              <div class="benchmark-regime-header">
                <strong>${escapeHtml(reg.display_name || reg.regime_id)}</strong>
                <span>N=${escapeHtml(this._formatBenchmarkNumber(reg.sample_size))} · ${escapeHtml(reg.start_date || '—')} to ${escapeHtml(reg.end_date || '—')}</span>
              </div>
              <div class="benchmark-table-scroll">
                <table class="benchmark-table benchmark-table-compact">
                  <thead>
                    <tr>
                      <th>Model</th>
                      <th>MAE</th>
                      <th>RMSE</th>
                      <th>MAPE %</th>
                      <th>MASE</th>
                      <th>Rel. MAE Δ%</th>
                      <th>Dir. Acc.</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${(reg.models || [])
                      .map(
                        (rm) => `
                      <tr class="${rm.model_name === 'persistence' ? 'is-baseline-row' : ''}">
                        <td>${escapeHtml(rm.model_name)}</td>
                        <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(rm.mae))}</td>
                        <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(rm.rmse))}</td>
                        <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(rm.mape))}</td>
                        <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(rm.mase))}</td>
                        <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(rm.relative_mae_percent_change))}</td>
                        <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(rm.directional_accuracy_non_flat))}</td>
                      </tr>`,
                      )
                      .join('')}
                  </tbody>
                </table>
              </div>
            </div>`,
            )
            .join('')}
        </div>
      </div>`;
  }

  _buildC32SectionHtml(benchmarks) {
    if (!benchmarks.c32_available) {
      return `
        <div class="benchmark-section">
          <div class="benchmark-section-header">
            <h3>C3.2 EXPERIMENTAL CANDIDATE ML BENCHMARK</h3>
            <span class="bench-badge bench-badge-unavailable">ARTIFACT UNAVAILABLE</span>
          </div>
          <p class="benchmark-empty-note">C3.2 benchmark artifact is unavailable; Persistence remains the production baseline.</p>
        </div>`;
    }

    const exp = this._findBenchmarkExperiment(
      benchmarks.c32_experiments,
      this.selectedBenchmarkRoute,
      this.selectedBenchmarkHorizon,
    );
    if (!exp) {
      return `
        <div class="benchmark-section">
          <div class="benchmark-section-header">
            <h3>C3.2 EXPERIMENTAL CANDIDATE ML BENCHMARK</h3>
          </div>
          <p class="benchmark-empty-note">No C3.2 experiment record found for ${escapeHtml(this.selectedBenchmarkRoute)} T+${escapeHtml(this.selectedBenchmarkHorizon)}.</p>
        </div>`;
    }

    const unavailableList = exp.unavailable_models || [];
    const evaluatedModels = (exp.models || []).filter(
      (m) => m.is_available !== false,
    );
    return `
      <section class="benchmark-section" aria-label="C3.2 Experimental ML Benchmarks">
        <div class="benchmark-section-header">
          <div>
            <h3>C3.2 EXPERIMENTAL ML BENCHMARK — ${escapeHtml(exp.route_code)} · T+${escapeHtml(exp.horizon_months)}</h3>
            <p class="benchmark-section-subtitle">
              Strict-forward expanding-window out-of-sample evaluation across common sample N=${escapeHtml(this._formatBenchmarkNumber(exp.common_sample_size))} (labeled rows: ${escapeHtml(this._formatBenchmarkNumber(exp.total_labeled_rows))}, min train: ${escapeHtml(this._formatBenchmarkNumber(exp.min_training_samples))}, window: ${escapeHtml(exp.common_start_date || '—')} to ${escapeHtml(exp.common_end_date || '—')}).
            </p>
          </div>
          <span class="bench-badge bench-badge-experimental">EXPERIMENTAL COMPARISON</span>
        </div>

        <div class="benchmark-table-scroll">
          <table class="benchmark-table">
            <thead>
              <tr>
                <th>Model</th>
                <th>N</th>
                <th>MAE</th>
                <th>RMSE</th>
                <th>MAPE</th>
                <th>MASE</th>
                <th>Rel. MAE Δ%</th>
                <th>Dir. Acc.</th>
                <th>Holm p-value</th>
                <th>Classification</th>
              </tr>
            </thead>
            <tbody>
              ${evaluatedModels
                .map((m) => {
                  const clsBadge = this._classificationBadgeClass(
                    m.research_classification,
                    m.is_baseline,
                  );
                  return `
                    <tr class="${m.is_baseline ? 'is-baseline-row' : ''}">
                      <td>
                        <div class="bench-model-name">${escapeHtml(m.display_name || m.model_name)}</div>
                        <div class="bench-model-meta">${escapeHtml(m.model_name)} · ${escapeHtml(m.feature_subset_name || '—')} (${escapeHtml(this._formatBenchmarkNumber(m.feature_count))} features)</div>
                      </td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.sample_size))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.mae))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.rmse))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.mape))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.mase))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.relative_mae_percent_change))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.directional_accuracy_non_flat))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.holm_adjusted_p_value))}</td>
                      <td>
                        <span class="bench-badge ${clsBadge}">
                          ${escapeHtml(m.is_baseline ? 'PRODUCTION BASELINE' : m.research_classification || '—')}
                        </span>
                      </td>
                    </tr>`;
                })
                .join('')}
            </tbody>
          </table>
        </div>

        ${
          unavailableList.length > 0
            ? `<div class="benchmark-unavailable-panel">
                <div class="benchmark-unavailable-title">UNAVAILABLE CANDIDATE MODELS (NOT SUBSTITUTED WITH ZERO)</div>
                ${unavailableList
                  .map(
                    (um) => `
                  <div class="benchmark-unavailable-row">
                    <span class="bench-badge bench-badge-unavailable">UNAVAILABLE</span>
                    <strong>${escapeHtml(um.display_name || um.model_name)} (${escapeHtml(um.model_name)})</strong>
                    <span class="benchmark-unavailable-reason">Optional dependency lightgbm not installed · ${escapeHtml(um.unavailability_reason || um.status || 'UNAVAILABLE')}</span>
                  </div>`,
                  )
                  .join('')}
              </div>`
            : ''
        }

        ${
          this.showBenchmarkStatsDetails
            ? `${this._buildStatsDetailTableHtml(exp.models, 'C3.2 STATISTICAL HYPOTHESIS TESTS & BOOTSTRAP CIs (VS PERSISTENCE)')}${this._buildRegimeBreakdownHtml(exp.regime_breakdown, 'C3.2 REGIME BREAKDOWN (PER MACRO REGIME)')}`
            : ''
        }
      </section>`;
  }

  _buildC33SectionHtml(benchmarks) {
    if (!benchmarks.c33_available) {
      return `
        <div class="benchmark-section">
          <div class="benchmark-section-header">
            <h3>C3.3 EXPERIMENTAL REGIME-AWARE INVESTIGATION</h3>
            <span class="bench-badge bench-badge-unavailable">ARTIFACT UNAVAILABLE</span>
          </div>
          <p class="benchmark-empty-note">C3.3 regime-aware investigation artifact is unavailable.</p>
        </div>`;
    }

    const exp = this._findBenchmarkExperiment(
      benchmarks.c33_experiments,
      this.selectedBenchmarkRoute,
      this.selectedBenchmarkHorizon,
    );
    if (!exp) {
      return `
        <div class="benchmark-section">
          <div class="benchmark-section-header">
            <h3>C3.3 EXPERIMENTAL REGIME-AWARE INVESTIGATION</h3>
          </div>
          <p class="benchmark-empty-note">No C3.3 experiment record found for ${escapeHtml(this.selectedBenchmarkRoute)} T+${escapeHtml(this.selectedBenchmarkHorizon)}.</p>
        </div>`;
    }

    const audit = exp.regime_sample_audit || {};
    const originCounts = audit.origin_regime_counts || {};
    const maxTrainPairs = audit.max_eligible_same_origin_train_pairs || {};
    const trainableSteps = audit.trainable_steps_at_threshold || {};
    const insufficientList = audit.completely_insufficient_regimes || [];
    const regimeKeys = Object.keys(originCounts);
    const partitioned = exp.regime_partitioned_analysis || null;
    const evaluatedModels = (exp.models || []).filter(
      (m) => m.is_available !== false,
    );

    return `
      <section class="benchmark-section benchmark-section-c33" aria-label="C3.3 Experimental Regime-Aware Investigation">
        <div class="benchmark-section-header">
          <div>
            <h3>C3.3 EXPERIMENTAL REGIME-AWARE INVESTIGATION — ${escapeHtml(exp.route_code)} · T+${escapeHtml(exp.horizon_months)}</h3>
            <p class="benchmark-section-subtitle">
              Experimental only — C3.3 regime-aware and cross-corridor models do not outperform Persistence overall. Common sample N=${escapeHtml(this._formatBenchmarkNumber(exp.common_sample_size))} (${escapeHtml(exp.common_start_date || '—')} to ${escapeHtml(exp.common_end_date || '—')}, min regime selection samples: ${escapeHtml(this._formatBenchmarkNumber(exp.min_regime_selection_samples))}).
            </p>
          </div>
          <span class="bench-badge bench-badge-experimental">EXPERIMENTAL REGIME STUDY</span>
        </div>

        <div class="benchmark-table-scroll">
          <table class="benchmark-table">
            <thead>
              <tr>
                <th>Model</th>
                <th>Origin Regime</th>
                <th>N</th>
                <th>MAE</th>
                <th>RMSE</th>
                <th>MAPE</th>
                <th>MASE</th>
                <th>Rel. MAE Δ%</th>
                <th>Holm p-value</th>
                <th>Classification</th>
              </tr>
            </thead>
            <tbody>
              ${evaluatedModels
                .map((m) => {
                  const clsBadge = this._classificationBadgeClass(
                    m.research_classification,
                    m.is_baseline,
                  );
                  return `
                    <tr class="${m.is_baseline ? 'is-baseline-row' : ''}">
                      <td>
                        <div class="bench-model-name">${escapeHtml(m.display_name || m.model_name)}</div>
                        <div class="bench-model-meta">${escapeHtml(m.model_name)} · ${escapeHtml(m.feature_subset_name || '—')}</div>
                      </td>
                      <td>${escapeHtml(m.uses_origin_regime ? 'Decision-time regime' : 'No')}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.sample_size))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.mae))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.rmse))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.mape))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.mase))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.relative_mae_percent_change))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(m.holm_adjusted_p_value))}</td>
                      <td>
                        <span class="bench-badge ${clsBadge}">
                          ${escapeHtml(m.is_baseline ? 'PRODUCTION BASELINE' : m.research_classification || '—')}
                        </span>
                      </td>
                    </tr>`;
                })
                .join('')}
            </tbody>
          </table>
        </div>

        <div class="benchmark-c33-audit-grid">
          <div class="benchmark-subsection benchmark-audit-card">
            <div class="benchmark-subsection-title">REGIME SAMPLE SUFFICIENCY AUDIT</div>
            ${
              insufficientList.length > 0
                ? `<div class="benchmark-insufficient-flag">Insufficient regime training samples: ${escapeHtml(insufficientList.join(', '))} (trainable_steps_at_threshold=0 at threshold ${escapeHtml(this._formatBenchmarkNumber(audit.min_regime_training_samples))}).</div>`
                : ''
            }
            <div class="benchmark-table-scroll">
              <table class="benchmark-table benchmark-table-compact">
                <thead>
                  <tr>
                    <th>Regime</th>
                    <th>Origin Rows</th>
                    <th>Max Train Pairs</th>
                    <th>Trainable Steps</th>
                    <th>Sufficient (&gt;=${escapeHtml(this._formatBenchmarkNumber(audit.min_regime_training_samples))})?</th>
                  </tr>
                </thead>
                <tbody>
                  ${regimeKeys
                    .map((regName) => {
                      const steps = trainableSteps[regName];
                      const isSufficient =
                        typeof steps === 'number' ? steps > 0 : null;
                      return `
                    <tr>
                      <td><strong>${escapeHtml(regName)}</strong></td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(originCounts[regName]))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(maxTrainPairs[regName]))}</td>
                      <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(steps))}</td>
                      <td>${escapeHtml(this._formatBenchmarkNumber(isSufficient))}</td>
                    </tr>`;
                    })
                    .join('')}
                </tbody>
              </table>
            </div>
          </div>

          ${
            partitioned
              ? `<div class="benchmark-subsection benchmark-audit-card">
                  <div class="benchmark-subsection-title">MATCHED REGIME-PARTITIONED SUBSET (N=${escapeHtml(this._formatBenchmarkNumber(partitioned.matched_subset_sample_size))} vs FULL N=${escapeHtml(this._formatBenchmarkNumber(exp.common_sample_size))})</div>
                  <p class="benchmark-section-subtitle">
                    Matched window: ${escapeHtml(partitioned.common_start_date || '—')} to ${escapeHtml(partitioned.common_end_date || '—')} · Skipped steps: ${escapeHtml(this._formatBenchmarkNumber(partitioned.skipped_steps_count))} (${escapeHtml(Object.keys(partitioned.skipped_reasons || {}).join(', ') || 'none')}).
                  </p>
                  <div class="benchmark-table-scroll">
                    <table class="benchmark-table benchmark-table-compact">
                      <thead>
                        <tr>
                          <th>Model</th>
                          <th>N</th>
                          <th>MAE</th>
                          <th>RMSE</th>
                          <th>Rel. MAE Δ%</th>
                          <th>Holm p-value</th>
                          <th>Classification</th>
                        </tr>
                      </thead>
                      <tbody>
                        ${(partitioned.models || [])
                          .map(
                            (pm) => `
                          <tr class="${pm.is_baseline ? 'is-baseline-row' : ''}">
                            <td>${escapeHtml(pm.display_name || pm.model_name)}</td>
                            <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(pm.sample_size))}</td>
                            <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(pm.mae))}</td>
                            <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(pm.rmse))}</td>
                            <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(pm.relative_mae_percent_change))}</td>
                            <td class="num-cell">${escapeHtml(this._formatBenchmarkNumber(pm.holm_adjusted_p_value))}</td>
                            <td><span class="bench-badge ${this._classificationBadgeClass(pm.research_classification, pm.is_baseline)}">${escapeHtml(pm.is_baseline ? 'PRODUCTION BASELINE' : pm.research_classification || '—')}</span></td>
                          </tr>`,
                          )
                          .join('')}
                      </tbody>
                    </table>
                  </div>
                </div>`
              : ''
          }
        </div>

        ${
          this.showBenchmarkStatsDetails
            ? `${this._buildStatsDetailTableHtml(exp.models, 'C3.3 STATISTICAL HYPOTHESIS TESTS & BOOTSTRAP CIs (VS PERSISTENCE)')}${this._buildRegimeBreakdownHtml(exp.regime_breakdown, 'C3.3 REGIME BREAKDOWN (PER MACRO REGIME)')}`
            : ''
        }
      </section>`;
  }

  _renderBenchmarkWorkspace(payload) {
    const ws = this.elements.benchmarkWorkspace;
    if (!ws) return;

    if (this.domain !== 'datasets') {
      this._hideBenchmarkWorkspace();
      return;
    }

    ws.hidden = false;
    ws.innerHTML = '';
    this.elements.tablePane?.classList?.add('has-benchmarks');

    const benchmarks = payload?.secondary_panels?.benchmarks;
    const baselineName = String(
      benchmarks?.production_baseline || 'persistence',
    ).toUpperCase();
    const baselineNote =
      benchmarks?.production_note ||
      'Persistence remains the active production forecasting baseline; no experimental candidate outperformed it with statistical significance.';

    const banner = this.doc.createElement('div');
    banner.className = 'benchmark-production-banner';
    banner.innerHTML = `
      <div class="benchmark-production-headline">
        <span class="bench-badge bench-badge-baseline">ACTIVE PRODUCTION MODEL: ${escapeHtml(baselineName)}</span>
        <strong>No experimental model has been promoted to production.</strong>
      </div>
      <div class="benchmark-production-note">${escapeHtml(baselineNote)}</div>
    `;
    ws.appendChild(banner);

    if (
      !benchmarks ||
      (!benchmarks.c32_available && !benchmarks.c33_available)
    ) {
      const missingNotice = this.doc.createElement('div');
      missingNotice.className = 'benchmark-unavailable-banner';
      missingNotice.textContent =
        'Experimental C3.2/C3.3 benchmark artifacts are currently unavailable; Persistence remains the production baseline and no synthetic metrics are generated.';
      ws.appendChild(missingNotice);
      return;
    }

    const { routes, horizons } =
      this._collectRouteAndHorizonOptions(benchmarks);
    if (routes.length > 0 && !routes.includes(this.selectedBenchmarkRoute)) {
      this.selectedBenchmarkRoute = routes[0];
    }
    if (
      horizons.length > 0 &&
      !horizons.includes(Number(this.selectedBenchmarkHorizon))
    ) {
      this.selectedBenchmarkHorizon = horizons[0];
    }

    if (routes.length > 0 && horizons.length > 0) {
      this._renderBenchmarkControls(ws, routes, horizons);
    }

    const sectionsBody = this.doc.createElement('div');
    sectionsBody.className = 'benchmark-sections-body';
    sectionsBody.innerHTML =
      this._buildC32SectionHtml(benchmarks) +
      this._buildC33SectionHtml(benchmarks);
    ws.appendChild(sectionsBody);
  }

  exportCurrentPageCsv() {
    const items = this.lastPayload?.items;
    if (!Array.isArray(items) || items.length === 0) return;
    const keys = Object.keys(items[0]);
    const csvEscape = (v) => {
      const s =
        v === null || v === undefined
          ? ''
          : typeof v === 'object'
            ? JSON.stringify(v)
            : String(v);
      return `"${s.replaceAll('"', '""')}"`;
    };
    const lines = [
      keys.map(csvEscape).join(','),
      ...items.map((row) => keys.map((k) => csvEscape(row[k])).join(',')),
    ];
    const csvContent = lines.join('\n');
    if (
      typeof Blob === 'undefined' ||
      typeof URL === 'undefined' ||
      !URL.createObjectURL
    ) {
      return csvContent;
    }
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = this.doc.createElement('a');
    link.href = url;
    link.download = `pelagos-${this.domain}-page-${this.page}.csv`;
    link.click?.();
    setTimeout(() => URL.revokeObjectURL?.(url), 1000);
    return csvContent;
  }

  destroy() {
    this._abortController?.abort();
    clearTimeout(this._searchTimer);
    for (const { el, event, handler } of this._boundListeners) {
      try {
        el.removeEventListener(event, handler);
      } catch {
        /* best effort */
      }
    }
    this._boundListeners = [];
    this.close();
  }
}

export { buildExplorerHash };
