/**
 * @module ui/pelagosExplorerDomains
 * @description Domain schemas, filter controls, table columns, and formatting helpers for Detailed Data Explorer.
 */

export function escapeHtml(str) {
  return String(str ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function formatUtcCell(val, dateOnly = false) {
  if (!val) return 'NULL';
  const s = String(val);
  if (dateOnly) return s.slice(0, 10);
  return s.replace('T', ' ').replace('+00:00', 'Z').slice(0, 19);
}

export function formatNumberCell(val, decimals = 2) {
  if (val === null || val === undefined || !Number.isFinite(Number(val))) {
    return 'NULL';
  }
  return Number(val).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export const DOMAIN_CONFIGS = Object.freeze({
  freight: {
    title: 'Freight Market',
    kicker:
      'USDA AMS SODA ehs5-yac3 · Monitored Grain Corridors & Derived Spread',
    defaultSortBy: 'observation_date',
    defaultSortOrder: 'desc',
    defaultFilters: { series_type: 'all' },
    columns: [
      {
        key: 'observation_date',
        label: 'Obs Date',
        sortBy: 'observation_date',
        format: (r) => formatUtcCell(r.observation_date, true),
      },
      { key: 'route_code', label: 'Corridor / Series', sortBy: 'route_code' },
      {
        key: 'series_classification',
        label: 'Classification',
        sortBy: 'is_derived',
        format: (r) =>
          r.is_derived ? 'Derived Spread' : 'Underlying Corridor',
      },
      {
        key: 'rate_value',
        label: 'Rate',
        sortBy: 'rate_value',
        align: 'right',
        format: (r) =>
          `${formatNumberCell(r.rate_value, 2)} ${r.unit_of_measure || 'USD/MT'}`,
      },
      {
        key: 'is_reconstructed',
        label: 'Reconstructed',
        sortBy: 'is_reconstructed',
        format: (r) =>
          r.is_reconstructed
            ? `YES (Orig: ${r.original_rate_value !== null ? formatNumberCell(r.original_rate_value, 2) : 'NULL'})`
            : 'No',
      },
      {
        key: 'source_name',
        label: 'Source / Provenance',
        format: (r) => r.source_name || '—',
      },
    ],
  },
  trade: {
    title: 'Bilateral Trade Data',
    kicker:
      'USDA FAS GATS & Indian DGCI&S · Commodity Trade Value != Freight Rate',
    defaultSortBy: 'period_date',
    defaultSortOrder: 'desc',
    defaultFilters: {},
    columns: [
      {
        key: 'period_date',
        label: 'Period',
        sortBy: 'period_date',
        format: (r) => formatUtcCell(r.period_date, true),
      },
      {
        key: 'corridor_pair',
        label: 'Reporter → Partner',
        sortBy: 'reporter_country',
        format: (r) =>
          `${r.reporter_country} (${r.reporter_iso2}) → ${r.partner_country} (${r.partner_iso2})`,
      },
      {
        key: 'commodity_name',
        label: 'Commodity (HS)',
        sortBy: 'commodity_name',
        format: (r) => `${r.commodity_name} [${r.commodity_code}]`,
      },
      {
        key: 'volume_metric_tons',
        label: 'Volume (MT)',
        sortBy: 'volume_metric_tons',
        align: 'right',
        format: (r) => formatNumberCell(r.volume_metric_tons, 0),
      },
      {
        key: 'trade_value_usd',
        label: 'Customs Value (USD)',
        sortBy: 'trade_value_usd',
        align: 'right',
        format: (r) =>
          r.trade_value_usd !== null
            ? `$${formatNumberCell(r.trade_value_usd, 0)}`
            : 'NULL',
      },
      {
        key: 'unit_price_usd_per_mt',
        label: 'Commodity Unit Value (Not Freight)',
        sortBy: 'unit_price_usd_per_mt',
        align: 'right',
        format: (r) =>
          r.unit_price_usd_per_mt !== null
            ? `$${formatNumberCell(r.unit_price_usd_per_mt, 2)}/MT`
            : 'NULL',
      },
      { key: 'source_name', label: 'Source', sortBy: 'source_name' },
    ],
  },
  environmental: {
    title: 'Environmental Observations',
    kicker:
      'NOAA NDBC & INCOIS Moored Buoys · No Interpolation of Missing Sensors',
    defaultSortBy: 'observation_time',
    defaultSortOrder: 'desc',
    defaultFilters: {},
    columns: [
      {
        key: 'observation_time',
        label: 'Obs Time (UTC)',
        sortBy: 'observation_time',
        format: (r) => formatUtcCell(r.observation_time),
      },
      {
        key: 'station_id',
        label: 'Station',
        sortBy: 'station_id',
        format: (r) => `${r.station_id} · ${r.station_name}`,
      },
      {
        key: 'coords',
        label: 'Coordinates',
        format: (r) =>
          `${Number(r.latitude).toFixed(3)}, ${Number(r.longitude).toFixed(3)}`,
      },
      {
        key: 'wind_speed_knots',
        label: 'Wind (kn)',
        sortBy: 'wind_speed_knots',
        align: 'right',
        format: (r) => formatNumberCell(r.wind_speed_knots, 1),
      },
      {
        key: 'wave_height_meters',
        label: 'Wave (m)',
        sortBy: 'wave_height_meters',
        align: 'right',
        format: (r) => formatNumberCell(r.wave_height_meters, 2),
      },
      {
        key: 'air_pressure_hpa',
        label: 'Press (hPa)',
        sortBy: 'air_pressure_hpa',
        align: 'right',
        format: (r) => formatNumberCell(r.air_pressure_hpa, 1),
      },
      {
        key: 'sea_surface_temp_c',
        label: 'SST (°C)',
        sortBy: 'sea_surface_temp_c',
        align: 'right',
        format: (r) => formatNumberCell(r.sea_surface_temp_c, 1),
      },
      {
        key: 'freshness_classification',
        label: 'Freshness / Source',
        format: (r) =>
          `${String(r.freshness_classification || '').toUpperCase()} · ${r.source_name}`,
      },
    ],
  },
  bunker: {
    title: 'Bunker Fuel',
    kicker:
      'bunkerfuelprices.com · Indicative Spot Only · Core Corridor Hubs Unavailable',
    defaultSortBy: 'observation_time',
    defaultSortOrder: 'desc',
    defaultFilters: {},
    columns: [
      {
        key: 'observation_time',
        label: 'Obs Time (UTC)',
        sortBy: 'observation_time',
        format: (r) => formatUtcCell(r.observation_time),
      },
      {
        key: 'port_code',
        label: 'Port',
        sortBy: 'port_code',
        format: (r) => `${r.port_name} (${r.port_code})`,
      },
      { key: 'fuel_grade', label: 'Grade', sortBy: 'fuel_grade' },
      {
        key: 'price_value',
        label: 'Indicative Price',
        sortBy: 'price_value',
        align: 'right',
        format: (r) =>
          `${formatNumberCell(r.price_value, 2)} ${r.unit || r.currency}`,
      },
      {
        key: 'pricing_type',
        label: 'Qualification',
        format: (r) =>
          `${String(r.pricing_type || '').toUpperCase()} (${r.delivery_basis || 'n/a'})`,
      },
      {
        key: 'quarantine_status',
        label: 'Quarantine',
        format: (r) => String(r.quarantine_status || 'accepted').toUpperCase(),
      },
      { key: 'source_name', label: 'Provider' },
    ],
  },
  vessels: {
    title: 'Vessel & AIS',
    kicker:
      'NOAA MarineCadastre / USCG NAIS · Historical Telemetry Only (NOT Commercial Open Tonnage)',
    defaultSortBy: 'vessel_name',
    defaultSortOrder: 'asc',
    defaultFilters: {},
    columns: [
      { key: 'vessel_name', label: 'Canonical Vessel', sortBy: 'vessel_name' },
      {
        key: 'mmsi',
        label: 'MMSI / IMO',
        sortBy: 'mmsi',
        format: (r) =>
          `${r.mmsi}${r.imo ? ` · IMO ${r.imo}` : ' · IMO Missing'}`,
      },
      {
        key: 'vessel_type',
        label: 'Category / Class',
        sortBy: 'vessel_type',
        format: (r) =>
          `${r.vessel_type}${r.vessel_class ? ` (${r.vessel_class})` : ''}`,
      },
      {
        key: 'coords',
        label: 'Historical Position',
        format: (r) =>
          r.latitude !== null && r.longitude !== null
            ? `${Number(r.latitude).toFixed(3)}, ${Number(r.longitude).toFixed(3)}`
            : 'NULL',
      },
      {
        key: 'observation_time',
        label: 'AIS Time (Historical)',
        sortBy: 'observation_time',
        format: (r) => `${formatUtcCell(r.observation_time)} [HIST]`,
      },
      {
        key: 'length_meters',
        label: 'LOA / Draft / DWT',
        sortBy: 'length_meters',
        format: (r) =>
          `${r.length_meters ? `${r.length_meters}m` : 'NULL'} / ${r.draft_max_meters ? `${r.draft_max_meters}m` : 'NULL'} / DWT:${r.dwt_tonnage ?? 'NULL'}`,
      },
      {
        key: 'source_name',
        label: 'Source & Mode',
        format: (r) => `${r.source_name} (Historical)`,
      },
    ],
  },
  ports: {
    title: 'Ports & Routes',
    kicker:
      'UNECE UN/LOCODE Directory & Generalized MARNET 50km Topology (Non-Certified Navigation)',
    defaultSortBy: 'name',
    defaultSortOrder: 'asc',
    defaultFilters: {},
    columns: [
      { key: 'unlocode', label: 'UN/LOCODE', sortBy: 'unlocode' },
      {
        key: 'name',
        label: 'Port / Aliases',
        sortBy: 'name',
        format: (r) => `${r.name} (${r.aliases_count || 0} aliases)`,
      },
      {
        key: 'country_code',
        label: 'Country / Region',
        sortBy: 'country_code',
        format: (r) =>
          `${r.country} (${r.country_code}) · ${r.port_region || '—'}`,
      },
      {
        key: 'coords',
        label: 'Coordinates',
        sortBy: 'latitude',
        format: (r) =>
          `${Number(r.latitude).toFixed(3)}, ${Number(r.longitude).toFixed(3)}`,
      },
      {
        key: 'corridor_coverage',
        label: 'Corridor Coverage',
        sortBy: 'is_monitored_corridor_eligible',
      },
      {
        key: 'nearest_graph_node',
        label: 'MARNET Node (Non-Certified)',
        sortBy: 'nearest_graph_node',
        format: (r) => r.nearest_graph_node || 'Directory Only',
      },
    ],
  },
  datasets: {
    title: 'Forecasting Datasets',
    kicker:
      'Strict-Forward Dataset Snapshots & C3.2/C3.3 Benchmarks (Persistence Baseline Active)',
    defaultSortBy: 'observation_date',
    defaultSortOrder: 'desc',
    defaultFilters: { route_code: 'PNW-JPN-BULK', horizon: '1' },
    columns: [
      {
        key: 'observation_date',
        label: 'Obs Date',
        sortBy: 'observation_date',
      },
      {
        key: 'decision_date',
        label: 'Decision Cutoff',
        sortBy: 'decision_date',
      },
      { key: 'target_date', label: 'Target Period', sortBy: 'target_date' },
      {
        key: 'route_code',
        label: 'Corridor',
        sortBy: 'route_code',
        format: (r) => `${r.route_code} (T+${r.horizon_months})`,
      },
      { key: 'regime_id', label: 'Regime', sortBy: 'regime_id' },
      {
        key: 'rate_lag_0',
        label: 'Origin Rate',
        align: 'right',
        format: (r) =>
          r.rate_lag_0 !== null
            ? `$${formatNumberCell(r.rate_lag_0, 2)}`
            : 'NULL',
      },
      {
        key: 'target_value',
        label: 'Target Value',
        sortBy: 'target_value',
        align: 'right',
        format: (r) =>
          r.target_value !== null
            ? `$${formatNumberCell(r.target_value, 2)}`
            : 'UNLABELED',
      },
      {
        key: 'is_labeled',
        label: 'Role / Mode',
        sortBy: 'is_labeled',
        format: (r) =>
          `${r.is_labeled ? 'LABELED' : 'INFERENCE'} (${r.horizon_mode})`,
      },
    ],
  },
  sources: {
    title: 'Source Operations & Provenance',
    kicker:
      'Ingestion Run Audit Ledger & Quarantined Records (Credentials Strictly Redacted)',
    defaultSortBy: 'completed_at',
    defaultSortOrder: 'desc',
    defaultFilters: { view: 'runs' },
    columns: [
      {
        key: 'completed_at',
        label: 'Timestamp (UTC)',
        sortBy: 'completed_at',
        format: (r) => formatUtcCell(r.completed_at || r.ingestion_time),
      },
      { key: 'source_name', label: 'Source / Provider', sortBy: 'source_name' },
      {
        key: 'partition_or_err',
        label: 'Partition / Code',
        format: (r) => r.source_partition || r.error_code || '—',
      },
      {
        key: 'status',
        label: 'Status',
        sortBy: 'status',
        format: (r) => String(r.status || r.error_code || '—').toUpperCase(),
      },
      {
        key: 'counts',
        label: 'Accepted / Dup / Quar / Fail',
        sortBy: 'accepted_count',
        format: (r) =>
          r.accepted_count !== undefined
            ? `${r.accepted_count} / ${r.duplicate_count} / ${r.quarantined_count} / ${r.failed_count}`
            : `MMSI ${r.mmsi || '—'}`,
      },
      {
        key: 'error_details',
        label: 'Details',
        format: (r) => r.error_details || 'None',
      },
    ],
  },
});

export function buildDomainFilterDescriptors(
  domain,
  filterOptions = {},
  currentFilters = {},
) {
  switch (domain) {
    case 'freight':
      return [
        {
          key: 'route_code',
          label: 'Corridor',
          type: 'select',
          options: [
            { value: '', label: 'All Corridors' },
            ...(filterOptions.route_codes || []).map((c) => ({
              value: c,
              label: c,
            })),
          ],
          value: currentFilters.route_code || '',
        },
        {
          key: 'series_type',
          label: 'Series Type',
          type: 'select',
          options: [
            { value: 'all', label: 'All Series' },
            { value: 'underlying', label: 'Underlying Corridors Only' },
            { value: 'spread', label: 'Derived Spread Only' },
          ],
          value: currentFilters.series_type || 'all',
        },
        {
          key: 'reconstructed_only',
          label: 'Reconstructed',
          type: 'select',
          options: [
            { value: '', label: 'All Observations' },
            { value: 'true', label: 'Reconstructed Only' },
          ],
          value: currentFilters.reconstructed_only ? 'true' : '',
        },
        {
          key: 'date_from',
          label: 'From',
          type: 'date',
          value: currentFilters.date_from || '',
        },
        {
          key: 'date_to',
          label: 'To',
          type: 'date',
          value: currentFilters.date_to || '',
        },
      ];
    case 'trade':
      return [
        {
          key: 'reporter_iso2',
          label: 'Reporter',
          type: 'select',
          options: [
            { value: '', label: 'All Reporters' },
            ...(filterOptions.reporters || []).map((c) => ({
              value: c,
              label: c,
            })),
          ],
          value: currentFilters.reporter_iso2 || '',
        },
        {
          key: 'partner_iso2',
          label: 'Partner',
          type: 'select',
          options: [
            { value: '', label: 'All Partners' },
            ...(filterOptions.partners || []).map((c) => ({
              value: c,
              label: c,
            })),
          ],
          value: currentFilters.partner_iso2 || '',
        },
        {
          key: 'commodity_code',
          label: 'Commodity',
          type: 'select',
          options: [
            { value: '', label: 'All Commodities' },
            ...(filterOptions.commodities || []).map((c) => ({
              value: c.code,
              label: `${c.code} · ${c.name}`,
            })),
          ],
          value: currentFilters.commodity_code || '',
        },
      ];
    case 'environmental':
      return [
        {
          key: 'station_id',
          label: 'Station',
          type: 'select',
          options: [
            { value: '', label: 'All Stations' },
            ...(filterOptions.stations || []).map((s) => ({
              value: s.station_id,
              label: `${s.station_id} — ${s.station_name}`,
            })),
          ],
          value: currentFilters.station_id || '',
        },
        {
          key: 'sea_area',
          label: 'Sea Area',
          type: 'select',
          options: [
            { value: '', label: 'All Sea Areas' },
            ...(filterOptions.sea_areas || []).map((a) => ({
              value: a,
              label: a,
            })),
          ],
          value: currentFilters.sea_area || '',
        },
      ];
    case 'bunker':
      return [
        {
          key: 'port_code',
          label: 'Port',
          type: 'select',
          options: [
            { value: '', label: 'All Quoted Ports' },
            ...(filterOptions.ports || []).map((p) => ({
              value: p.port_code,
              label: `${p.port_name} (${p.port_code})`,
            })),
          ],
          value: currentFilters.port_code || '',
        },
        {
          key: 'fuel_grade',
          label: 'Grade',
          type: 'select',
          options: [
            { value: '', label: 'All Grades' },
            ...(filterOptions.fuel_grades || []).map((g) => ({
              value: g,
              label: g,
            })),
          ],
          value: currentFilters.fuel_grade || '',
        },
      ];
    case 'vessels':
      return [
        {
          key: 'vessel_type',
          label: 'Type',
          type: 'select',
          options: [
            { value: '', label: 'All Types' },
            ...(filterOptions.vessel_types || []).map((t) => ({
              value: t,
              label: t,
            })),
          ],
          value: currentFilters.vessel_type || '',
        },
        {
          key: 'has_imo',
          label: 'IMO Status',
          type: 'select',
          options: [
            { value: '', label: 'All Vessels' },
            { value: 'true', label: 'IMO Present' },
            { value: 'false', label: 'IMO Missing' },
          ],
          value:
            currentFilters.has_imo === true || currentFilters.has_imo === 'true'
              ? 'true'
              : currentFilters.has_imo === false ||
                  currentFilters.has_imo === 'false'
                ? 'false'
                : '',
        },
      ];
    case 'ports':
      return [
        {
          key: 'country_code',
          label: 'Country',
          type: 'select',
          options: [
            { value: '', label: 'All Countries' },
            ...(filterOptions.countries || []).map((c) => ({
              value: c.country_code,
              label: `${c.country} (${c.country_code})`,
            })),
          ],
          value: currentFilters.country_code || '',
        },
        {
          key: 'corridor_eligible',
          label: 'Corridor Status',
          type: 'select',
          options: [
            { value: 'all', label: 'All Ports' },
            { value: 'true', label: 'Corridor-Eligible Only' },
            { value: 'false', label: 'Unmonitored / Non-Corridor' },
          ],
          value: currentFilters.corridor_eligible || 'all',
        },
        {
          key: 'graph_connected',
          label: 'MARNET Snap',
          type: 'select',
          options: [
            { value: 'all', label: 'All Connectivity' },
            { value: 'true', label: 'MARNET Graph Snapped' },
            { value: 'false', label: 'Directory Only' },
          ],
          value: currentFilters.graph_connected || 'all',
        },
      ];
    case 'datasets':
      return [
        {
          key: 'route_code',
          label: 'Corridor',
          type: 'select',
          options: [
            { value: '', label: 'All Monitored Corridors' },
            ...(filterOptions.routes || ['PNW-JPN-BULK', 'USG-JPN-BULK']).map(
              (r) => ({
                value: r,
                label: r,
              }),
            ),
          ],
          value: currentFilters.route_code ?? 'PNW-JPN-BULK',
        },
        {
          key: 'horizon',
          label: 'Horizon',
          type: 'select',
          options: [
            { value: '', label: 'All Horizons (T+1, T+3, T+6)' },
            { value: '1', label: 'T+1 Strict Forward' },
            { value: '3', label: 'T+3 Strict Forward' },
            { value: '6', label: 'T+6 Strict Forward' },
          ],
          value: currentFilters.horizon ?? '1',
        },
        {
          key: 'regime_id',
          label: 'Regime',
          type: 'select',
          options: [
            { value: '', label: 'All Regimes' },
            ...(filterOptions.regimes || []).map((reg) => ({
              value: reg,
              label: reg,
            })),
          ],
          value: currentFilters.regime_id || '',
        },
        {
          key: 'is_labeled',
          label: 'Split',
          type: 'select',
          options: [
            { value: 'all', label: 'All Rows' },
            { value: 'true', label: 'Labeled Training' },
            { value: 'false', label: 'Unlabeled Inference' },
          ],
          value: currentFilters.is_labeled || 'all',
        },
      ];
    case 'sources':
      return [
        {
          key: 'view',
          label: 'Ledger View',
          type: 'select',
          options: [
            { value: 'runs', label: 'Ingestion Runs Ledger' },
            { value: 'quarantine', label: 'Quarantined Records' },
          ],
          value: currentFilters.view || 'runs',
        },
        {
          key: 'source_name',
          label: 'Provider',
          type: 'select',
          options: [
            { value: '', label: 'All Sources' },
            ...(filterOptions.sources || []).map((s) => ({
              value: s,
              label: s,
            })),
          ],
          value: currentFilters.source_name || '',
        },
      ];
    default:
      return [];
  }
}
