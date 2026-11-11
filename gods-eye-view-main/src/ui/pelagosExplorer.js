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
  }

  renderPayload(payload) {
    this._renderKpis(payload);
    this._renderDynamicFilters(payload);
    this._renderTable(payload);
    this._updatePaginationControls(payload);
    this._renderProvenanceDrawer(payload);

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
      sections.push(`
        <div class="drawer-section">
          <div class="drawer-section-title">BASELINE VS EXPERIMENTAL BENCHMARKS</div>
          <div class="drawer-badge-baseline">PRODUCTION BASELINE: ${escapeHtml(String(bm.production_baseline || 'persistence').toUpperCase())}</div>
          <p class="drawer-prose">${escapeHtml(bm.production_note)}</p>
          ${
            rows.length > 0
              ? `<div class="drawer-kv-list">
                  ${rows
                    .map(
                      (r) => `
                    <div class="drawer-kv-item">
                      <span class="kv-k">${escapeHtml(r.route_code)} T+${escapeHtml(r.horizon_months)} (N=${escapeHtml(r.common_sample_size)})</span>
                      <span class="kv-v">Pers ${formatNumberCell(r.persistence_mae, 2)} · Exp (${escapeHtml(r.best_experimental_candidate)}): ${formatNumberCell(r.best_experimental_mae, 2)}</span>
                    </div>`,
                    )
                    .join('')}
                </div>`
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
