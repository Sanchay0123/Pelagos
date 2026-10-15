import test from 'node:test';
import assert from 'node:assert/strict';

import { vesselSnapshot } from '../../sources/live/vessels.js';
import { layerFeedState } from '../../data/feedState.js';
import { LayerPanel } from '../../ui/layerPanel.js';
import { createCards } from './cards.js';

test('vesselSnapshot distinguishes historical observations from live AIS telemetry', () => {
  const historicalPayload = {
    rows: [
      {
        mmsi: '338075892',
        name: 'PILOT BOAT SPRING PT',
        type_specific: 'Other',
        lat: 43.65322,
        lon: -70.25298,
        speed: 0.0,
        course: 358.8,
        last_position_UTC: '2024-01-01T00:00:03Z',
        last_position_epoch: 1704067203,
      },
    ],
    source: 'NOAA MarineCadastre / USCG NAIS',
    license: 'CC0 1.0 Universal',
    coverage: 'US Coastal and Territorial Waters (Verified Genuine Observations)',
    status: 'historical',
    data_mode: 'historical',
    is_live: false,
    live_ais_connected: false,
    backend_connected: true,
    freshness: 'historical',
    newestPositionAt: '2024-01-01T00:00:03Z',
    observation_date: '2024-01-01',
    rawRowCount: 1,
    total_records: 1,
  };

  const snapshot = vesselSnapshot(historicalPayload);
  assert.equal(snapshot.dataMode, 'historical');
  assert.equal(snapshot.isLive, false);
  assert.equal(snapshot.freshness, 'historical');
  assert.equal(snapshot.transportStatus, 'historical');
  assert.equal(snapshot.observationDate, '2024-01-01');
  assert.equal(snapshot.records.length, 1);
  assert.equal(snapshot.records[0].historical, true);
  assert.equal(snapshot.source, 'NOAA MarineCadastre / USCG NAIS');
});

test('layerFeedState and LayerPanel distinguish historical status from live AIS', () => {
  const classes = new Map();
  const attrs = new Map();
  const button = {
    classList: { toggle: (key, value) => classes.set(key, value) },
    dataset: {},
    setAttribute: (key, value) => attrs.set(key, value),
  };

  const layer = {
    id: 'ais-live-vessels',
    name: 'Live Vessels',
    source: 'NOAA MarineCadastre / USCG NAIS',
    enabled: true,
    stats: {
      count: 133,
      acceptedRowCount: 133,
      rawRowCount: 133,
      lastUpdate: Date.now(),
      status: 'historical',
      mode: 'historical',
      dataMode: 'historical',
      isLive: false,
      freshness: 'historical',
      observationDate: '2024-01-01',
      coverage: 'US Coastal and Territorial Waters',
      source: 'NOAA MarineCadastre / USCG NAIS',
    },
  };

  const feedState = layerFeedState(layer.stats);
  assert.equal(feedState, 'historical');

  const panel = LayerPanel.prototype;
  panel._syncToggleButton(button, layer);

  assert.equal(button.textContent, 'HISTORICAL');
  assert.equal(button.dataset.feedState, 'historical');
  assert.equal(classes.get('feed-historical'), true);

  const metaText = panel._buildMetaText(layer);
  assert.match(metaText, /^HISTORICAL · NOAA MarineCadastre \/ USCG NAIS · observed 2024-01-01/);
});

test('formatPositionTime renders explicit observation date and HISTORICAL badge on historical records', () => {
  const cards = createCards({
    vesselState: { state: { feed: {} } },
    services: {},
    parts: { rendering: {} },
  });

  const historicalRecord = {
    mmsi: '338075892',
    name: 'PILOT BOAT SPRING PT',
    lastPositionUtc: '2024-01-01T00:00:03Z',
    historical: true,
  };
  const historicalTimeStr = cards.formatPositionTime(historicalRecord);
  assert.equal(historicalTimeStr, 'POS: 2024-01-01 00:00:03Z (HISTORICAL)');

  const liveRecord = {
    mmsi: '367123456',
    name: 'LIVE RUNNER',
    lastPositionUtc: '2026-10-15T12:00:00Z',
    historical: false,
  };
  const liveTimeStr = cards.formatPositionTime(liveRecord);
  assert.equal(liveTimeStr, 'POS: 12:00:00Z');
});

test('backend connection does not imply current AIS connectivity when provider is unavailable', () => {
  // Successful backend HTTP response, but serving historical fallback
  const fallbackStats = {
    count: 50,
    acceptedRowCount: 50,
    rawRowCount: 50,
    lastUpdate: Date.now(),
    status: 'historical',
    mode: 'historical',
    isLive: false,
    freshness: 'historical',
    observationDate: '2024-01-01',
  };

  const state = layerFeedState(fallbackStats);
  assert.notEqual(state, 'nominal', 'Must not report nominal ON for historical fallback');
  assert.equal(state, 'historical');
});
