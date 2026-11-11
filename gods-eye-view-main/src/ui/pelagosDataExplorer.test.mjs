import assert from 'node:assert/strict';
import test, { beforeEach } from 'node:test';
import {
  DEFAULT_EXPLORER_DOMAIN,
  EXPLORER_DOMAIN_IDS,
  buildExplorerHash,
  parseExplorerHash,
} from './explorerRouter.js';
import { fetchExplorerDomain, fetchPlatformOverview } from './pelagosApi.js';
import { PelagosOverviewController } from './pelagosOverview.js';
import { PelagosExplorerController } from './pelagosExplorer.js';
import {
  _resetRenderGovernorForTest,
  getRenderGovernorDiagnostics,
  governorRequestRender,
  holdContinuousRender,
  installRenderGovernor,
  isRenderingSuspended,
  resumeRendering,
  suspendRendering,
} from '../renderGovernor.js';
import { ShareLinkManager } from '../sharelink.js';

function createMockElement(id, tag = 'div') {
  const listeners = new Map();
  const children = [];
  const classes = new Set();
  let _textContent = '';
  let _innerHTML = '';

  const el = {
    id,
    tagName: tag.toUpperCase(),
    value: '',
    disabled: false,
    hidden: false,
    tabIndex: 0,
    style: {},
    dataset: {},
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
    set textContent(v) {
      _textContent = String(v ?? '');
      if (v !== '') children.length = 0;
    },
    get innerHTML() {
      return _innerHTML;
    },
    set innerHTML(v) {
      _innerHTML = String(v ?? '');
      if (_innerHTML === '') {
        children.length = 0;
        _textContent = '';
      }
    },
    appendChild(child) {
      children.push(child);
      return child;
    },
    setAttribute(name, val) {
      this[name] = val;
    },
    querySelector: (selector) => {
      if (selector === '.tab-pill') {
        return children.find((c) => c.className === 'tab-pill') || null;
      }
      return null;
    },
    querySelectorAll: (selector) => {
      const results = [];
      const match = (node) => {
        if (selector.startsWith('.')) {
          const cls = selector.slice(1);
          if (
            node.classList?.contains?.(cls) ||
            String(node.className || '').includes(cls)
          ) {
            results.push(node);
          }
        }
        (node.children || []).forEach(match);
      };
      children.forEach(match);
      return results;
    },
    addEventListener(event, handler) {
      if (!listeners.has(event)) listeners.set(event, []);
      listeners.get(event).push(handler);
    },
    removeEventListener(event, handler) {
      const list = listeners.get(event) || [];
      const idx = list.indexOf(handler);
      if (idx >= 0) list.splice(idx, 1);
    },
    dispatchEvent(event) {
      const list = listeners.get(event.type) || [];
      for (const h of list) h(event);
    },
  };
  return el;
}

function createStageE1MockDom() {
  const elements = new Map();
  const ids = [
    'pelagos-data-overview-card',
    'pelagos-overview-status-tag',
    'pelagos-ov-total-records',
    'pelagos-ov-sources-count',
    'pelagos-ov-domains-count',
    'pelagos-ov-latest-sync',
    'pelagos-ov-methodology',
    'pelagos-ov-domain-grid',
    'pelagos-ov-freight-summary',
    'pelagos-ov-indicators-list',
    'pelagos-ov-notice',
    'pelagos-overview-refresh-btn',
    'pelagos-open-explorer-btn',
    'pelagos-header-explorer-btn',
    'pelagos-explorer-view',
    'pelagos-explorer-return-btn',
    'pelagos-explorer-summary-badge',
    'pelagos-explorer-kicker',
    'pelagos-explorer-title',
    'explorer-domain-tabs',
    'explorer-kpi-bar',
    'explorer-status-banner',
    'explorer-search-input',
    'explorer-dynamic-filters',
    'explorer-reset-filters-btn',
    'explorer-export-csv-btn',
    'explorer-table-head',
    'explorer-table-body',
    'explorer-loading-spinner',
    'explorer-empty-state',
    'explorer-page-start',
    'explorer-page-end',
    'explorer-total-count',
    'explorer-prev-page-btn',
    'explorer-next-page-btn',
    'explorer-current-page',
    'explorer-total-pages',
    'explorer-page-size-select',
    'explorer-provenance-content',
  ];
  for (const id of ids) {
    elements.set(id, createMockElement(id));
  }

  const tabsEl = elements.get('explorer-domain-tabs');
  for (const dom of EXPLORER_DOMAIN_IDS) {
    const tab = createMockElement(`tab-${dom}`, 'button');
    tab.className = 'domain-tab';
    tab.dataset = { domain: dom };
    const pill = createMockElement(`pill-${dom}`, 'span');
    pill.className = 'tab-pill';
    tab.appendChild(pill);
    tabsEl.appendChild(tab);
  }

  const body = createMockElement('body', 'body');
  return {
    elements,
    doc: {
      body,
      getElementById: (id) => elements.get(id) || null,
      createElement: (tag) => createMockElement(`el_${Math.random()}`, tag),
    },
  };
}

beforeEach(() => {
  _resetRenderGovernorForTest();
});

test('explorerRouter parses and serializes #/explorer and domain routes', () => {
  assert.deepEqual(parseExplorerHash('#/explorer'), {
    active: true,
    domain: DEFAULT_EXPLORER_DOMAIN,
    requestedDomain: null,
    isValidDomain: true,
  });
  assert.deepEqual(parseExplorerHash('#/explorer/bunker'), {
    active: true,
    domain: 'bunker',
    requestedDomain: 'bunker',
    isValidDomain: true,
  });
  assert.equal(parseExplorerHash('#/explorer/bogus').isValidDomain, false);
  assert.equal(parseExplorerHash('#v=2&lat=30&lon=-97').active, false);
  assert.equal(buildExplorerHash('vessels'), '#/explorer/vessels');
});

test('pelagosApi handles HTTP error and network failures without fabricating data', async () => {
  const errResp = await fetchOverviewMock(false, 503, {
    detail: 'Database offline',
  });
  assert.equal(errResp.ok, false);
  assert.equal(errResp.error, 'Database offline');

  const netErr = await fetchExplorerDomain(
    'freight',
    { page: 1 },
    {
      fetchImpl: async () => {
        throw new Error('ECONNREFUSED');
      },
    },
  );
  assert.equal(netErr.ok, false);
  assert.match(netErr.error, /ECONNREFUSED/);
});

async function fetchOverviewMock(ok, status, payload) {
  return fetchPlatformOverview({
    fetchImpl: async () => ({
      ok,
      status,
      json: async () => payload,
    }),
  });
}

test('PelagosOverviewController renders real overview payload, handles offline state, and triggers explorer navigation', async () => {
  const { doc, elements } = createStageE1MockDom();
  const navCalls = [];
  const samplePayload = {
    status: 'ok',
    global_summary: {
      total_genuine_records: 1424,
      connected_operational_sources: 6,
      total_registered_source_feeds: 6,
      domains_populated_count: 8,
      domains_with_records_count: 8,
      domains_fully_populated_count: 7,
      domains_partial_count: 1,
      total_domains: 8,
      latest_successful_ingestion_timestamp: '2026-11-08T16:53:52.282281+00:00',
      breakdown: {
        raw_observations_total: 1261,
        source_observations_excluding_derived: 891,
        freight_underlying_corridor: 740,
        freight_derived_spread: 370,
        freight_publications_count: 31,
        trade_observations: 13,
        environmental_observations: 13,
        bunker_observations: 5,
        ais_position_observations: 120,
        registered_physical_entities: 163,
        canonical_ports: 43,
        canonical_vessels: 120,
        identifier_mappings_port_aliases: 77,
        ingestion_audit_runs: 13,
        derived_dataset_rows: 2148,
      },
    },
    domains: {
      freight: {
        status: 'populated',
        total_observations: 1110,
        monitored_corridors: [
          {
            route_code: 'PNW-JPN-BULK',
            observation_count: 370,
            latest_rate_value: 38.0,
            unit: 'USD/MT',
          },
        ],
        historical_range: { start: '1996-01-01', end: '2026-10-01' },
        available_forecast_horizons: ['T+1', 'T+3', 'T+6'],
        active_production_baseline: 'persistence',
      },
      trade: { status: 'populated', total_observations: 13 },
      environmental: {
        status: 'populated',
        total_observations: 13,
        stations_count: 3,
        freshness: 'stale',
      },
      bunker: {
        status: 'partial',
        total_observations: 5,
        corridor_hubs_covered_count: 0,
        ports_covered: [{ port_code: 'ARBUE' }],
      },
      vessels: {
        status: 'populated',
        position_observations: 120,
        canonical_vessels: 120,
      },
      ports: {
        status: 'populated',
        total_ports: 43,
        corridor_eligible_ports: 18,
        total_aliases: 77,
      },
      datasets: {
        status: 'populated',
        total_dataset_rows: 2148,
        usable_features_count: 34,
        horizon_labels: ['T+1', 'T+3', 'T+6'],
      },
      sources: { status: 'populated', total_ingestion_runs: 13 },
    },
  };

  const controller = new PelagosOverviewController({
    document: doc,
    onNavigateExplorer: (dom) => navCalls.push(dom),
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => samplePayload,
    }),
  });
  await controller.init();

  assert.equal(elements.get('pelagos-ov-total-records').textContent, '1,424');
  assert.equal(elements.get('pelagos-ov-sources-count').textContent, '6/6');
  assert.equal(
    elements.get('pelagos-ov-domains-count').textContent,
    '8/8 (1 PARTIAL)',
  );
  assert.match(
    elements.get('pelagos-ov-methodology').textContent,
    /891 source obs.*370 derived spread.*163 physical entities.*31 Parquet pubs/,
  );
  assert.match(
    elements.get('pelagos-ov-freight-summary').textContent,
    /PNW-JPN-BULK: 370 obs/,
  );

  elements.get('pelagos-open-explorer-btn').dispatchEvent({ type: 'click' });
  assert.deepEqual(navCalls, ['freight']);

  // Click domain tile directly
  const domainTiles = elements.get('pelagos-ov-domain-grid').children;
  assert.equal(domainTiles.length, 8);
  domainTiles[3].dispatchEvent({ type: 'click' }); // bunker tile
  assert.deepEqual(navCalls, ['freight', 'bunker']);

  // Simulate backend failure -> clears counts honestly
  controller.fetchImpl = async () => {
    throw new Error('backend unreachable');
  };
  await controller.load({ useCache: false });
  assert.equal(elements.get('pelagos-ov-total-records').textContent, '—');
  assert.match(
    elements.get('pelagos-ov-notice').textContent,
    /backend unreachable/,
  );
  controller.destroy();
});

test('PelagosExplorerController navigates domains, paginates, sorts, and handles empty/unavailable states', async () => {
  const { doc, elements } = createStageE1MockDom();
  const requestedUrls = [];
  let responseMode = 'ok';

  const explorer = new PelagosExplorerController({
    document: doc,
    onDomainChange: (dom) => explorer.setDomain(dom),
    fetchImpl: async (url) => {
      requestedUrls.push(String(url));
      if (responseMode === 'empty') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'empty',
            domain: 'bunker',
            availability_status: 'empty',
            page: 1,
            page_size: 25,
            total_count: 0,
            total_pages: 0,
            allowed_sort_fields: ['observation_time'],
            filter_options: {},
            summary: { total_accepted_quotes: 0 },
            integrity_notices: ['Core hubs unavailable'],
            items: [],
            reason: 'Zero bunker quotes ingested.',
          }),
        };
      }
      if (responseMode === 'unavailable') {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'unavailable',
            domain: 'datasets',
            availability_status: 'unavailable',
            page: 1,
            page_size: 25,
            total_count: 0,
            total_pages: 0,
            allowed_sort_fields: ['observation_date'],
            filter_options: {},
            summary: {},
            integrity_notices: [],
            items: [],
            reason: 'Insufficient observations <= burn_in.',
          }),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          status: 'ok',
          domain: 'freight',
          availability_status: 'populated',
          page: 1,
          page_size: 25,
          total_count: 740,
          total_pages: 30,
          allowed_sort_fields: ['observation_date', 'rate_value'],
          filter_options: { route_codes: ['PNW-JPN-BULK', 'USG-JPN-BULK'] },
          summary: { total_database_observations: 1110 },
          integrity_notices: ['SPREAD separated'],
          items: [
            {
              id: 1,
              route_code: 'PNW-JPN-BULK',
              observation_date: '2026-10-01',
              rate_value: 38.0,
              unit_of_measure: 'USD/MT',
              is_derived: false,
              is_reconstructed: false,
              source_name: 'USDA AMS',
            },
          ],
        }),
      };
    },
  });
  explorer.init();

  explorer.updateOverviewCounts({
    global_summary: {
      total_genuine_records: 1424,
      domains_with_records_count: 8,
      domains_partial_count: 1,
      total_domains: 8,
      breakdown: {
        raw_observations_total: 1261,
        source_observations_excluding_derived: 891,
        freight_derived_spread: 370,
        registered_physical_entities: 163,
      },
    },
    domains: {},
  });
  assert.equal(
    elements.get('pelagos-explorer-summary-badge').textContent,
    '891 SOURCE OBS + 370 SPREAD + 163 ENTITIES · 8/8 W/ DATA · 1 PARTIAL',
  );

  explorer.syncFromHash('#/explorer/freight');
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(doc.body.classList.contains('pelagos-explorer-active'), true);
  assert.equal(elements.get('pelagos-explorer-view').hidden, false);
  assert.equal(elements.get('explorer-total-count').textContent, '740');
  assert.equal(elements.get('explorer-next-page-btn').disabled, false);

  // Select row -> populates provenance drawer
  elements
    .get('explorer-table-body')
    .children[0].dispatchEvent({ type: 'click' });
  assert.match(
    elements.get('explorer-provenance-content').innerHTML,
    /SELECTED RECORD PROVENANCE/,
  );

  // Next page call updates query
  elements.get('explorer-next-page-btn').dispatchEvent({ type: 'click' });
  await new Promise((r) => setTimeout(r, 10));
  assert.ok(requestedUrls.some((u) => u.includes('page=2')));

  // Empty & Unavailable states
  responseMode = 'empty';
  explorer.syncFromHash('#/explorer/bunker');
  await new Promise((r) => setTimeout(r, 10));
  assert.match(
    elements.get('explorer-empty-state').textContent,
    /Zero bunker quotes ingested/,
  );

  responseMode = 'unavailable';
  explorer.syncFromHash('#/explorer/datasets');
  await new Promise((r) => setTimeout(r, 10));
  assert.match(
    elements.get('explorer-status-banner').textContent,
    /UNAVAILABLE/,
  );

  // Exiting hash route restores clean body state
  explorer.syncFromHash('');
  assert.equal(doc.body.classList.contains('pelagos-explorer-active'), false);
  assert.equal(elements.get('pelagos-explorer-view').hidden, true);
  explorer.destroy();
});

test('renderGovernor suspendRendering preserves active continuous holds across Explorer navigation and ShareLinkManager pauses hash writes', () => {
  const viewer = {
    useDefaultRenderLoop: true,
    camera: {
      positionCartographic: { latitude: 0.5, longitude: -1.5, height: 1000 },
      heading: 0,
      pitch: -0.5,
      roll: 0,
      changed: { addEventListener: () => () => {} },
    },
    scene: {
      requestRenderMode: false,
      maximumRenderTimeChange: 0,
      requestRenderCalls: 0,
      requestRender() {
        this.requestRenderCalls += 1;
      },
    },
  };

  installRenderGovernor(viewer);
  holdContinuousRender('pelagos-route');
  assert.equal(viewer.scene.requestRenderMode, false);
  assert.equal(viewer.useDefaultRenderLoop, true);

  // Opening #/explorer suspends Cesium loop & forces requestRenderMode while preserving holds
  suspendRendering('explorer');
  assert.equal(isRenderingSuspended(), true);
  assert.equal(viewer.useDefaultRenderLoop, false);
  assert.equal(viewer.scene.requestRenderMode, true);
  const suspendedDiag = getRenderGovernorDiagnostics();
  assert.equal(suspendedDiag.mode, 'suspended');
  assert.deepEqual(suspendedDiag.holds, ['pelagos-route']);

  const beforeTick = viewer.scene.requestRenderCalls;
  governorRequestRender('background-tick');
  assert.equal(viewer.scene.requestRenderCalls, beforeTick);

  // Tab hidden + explorer: exiting explorer alone keeps suspended until tab returns
  suspendRendering('document-hidden');
  resumeRendering('explorer');
  assert.equal(viewer.useDefaultRenderLoop, false);
  resumeRendering('document-hidden');
  assert.equal(isRenderingSuspended(), false);
  assert.equal(viewer.useDefaultRenderLoop, true);
  assert.equal(viewer.scene.requestRenderMode, false);

  // ShareLinkManager does not schedule hash writes while explorer hash sync is paused
  const shareMgr = new ShareLinkManager(viewer);
  shareMgr.setHashSyncPaused(true);
  shareMgr.onPanelStateChange();
  assert.equal(shareMgr._debounceTimer, null);
  shareMgr.destroy();
});
