import assert from 'node:assert/strict';
import test from 'node:test';
import {
  CORRIDOR_PROXIMITY_NM,
  formatRelativeAge,
  formatVesselCoords,
  normalizeLiveVesselItem,
  calculateDistanceToRouteNm,
  deduplicateAndFilterVessels,
} from './pelagosVessels.js';

test('formatRelativeAge produces compact human-readable elapsed intervals', () => {
  const now = 1700000000;
  assert.equal(formatRelativeAge(null, now), 'Not reported');
  assert.equal(formatRelativeAge(0, now), 'Not reported');
  assert.equal(formatRelativeAge(now - 15, now), '< 1m ago');
  assert.equal(formatRelativeAge(now - 180, now), '3m ago');
  assert.equal(formatRelativeAge(now - 7500, now), '2h 5m ago');
  assert.equal(formatRelativeAge(now - 100000, now), '1d ago');
});

test('formatVesselCoords produces formatted degrees with directional suffixes', () => {
  assert.equal(formatVesselCoords(35.1234, 139.5678), '35.1234°N, 139.5678°E');
  assert.equal(formatVesselCoords(-33.8688, -70.6483), '33.8688°S, 70.6483°W');
  assert.equal(formatVesselCoords(null, 0), 'Not reported');
});

test('normalizeLiveVesselItem normalizes complete and partial AIS messages', () => {
  const now = 1700000000;

  // 1. Full payload
  const full = normalizeLiveVesselItem(
    {
      mmsi: '353136000',
      name: 'PACIFIC BULKER',
      imo: '9456789',
      type: 'Cargo',
      destination: 'YOKOHAMA',
      lat: 48.5,
      lon: -125.0,
      speed: 13.8,
      course: 260.4,
      heading: 261,
      last_position_UTC: '2026-10-19T09:00:00Z',
      last_position_epoch: now - 300,
    },
    now,
  );

  assert.equal(full.mmsi, '353136000');
  assert.equal(full.displayName, 'PACIFIC BULKER');
  assert.equal(full.hasReportedName, true);
  assert.equal(full.imo, '9456789');
  assert.equal(full.type, 'Cargo');
  assert.equal(full.destination, 'YOKOHAMA');
  assert.equal(full.speed, 13.8);
  assert.equal(full.speedFormatted, '13.8 kn');
  assert.equal(full.course, 260.4);
  assert.equal(full.courseFormatted, '260°');
  assert.equal(full.heading, 261);
  assert.equal(full.headingFormatted, '261°');
  assert.equal(full.lastPositionUtc, '2026-10-19T09:00:00Z');
  assert.equal(full.lastUtc, '2026-10-19T09:00:00Z');
  assert.equal(full.ageFormatted, '5m ago');
  assert.equal(full.isStale, false);
  assert.equal(full.isHistorical, false);

  // 2. Minimal payload with missing optional values
  const minimal = normalizeLiveVesselItem(
    {
      mmsi: '987654321',
      lat: 34.0,
      lon: 139.0,
    },
    now,
  );

  assert.equal(minimal.mmsi, '987654321');
  assert.equal(minimal.displayName, 'MMSI 987654321');
  assert.equal(minimal.hasReportedName, false);
  assert.equal(minimal.imo, 'Not reported');
  assert.equal(minimal.type, 'Not reported');
  assert.equal(minimal.destination, 'Not reported');
  assert.equal(minimal.speed, null);
  assert.equal(minimal.speedFormatted, 'Not reported');
  assert.equal(minimal.course, null);
  assert.equal(minimal.courseFormatted, 'Not reported');
  assert.equal(minimal.heading, null);
  assert.equal(minimal.headingFormatted, 'Not reported');
  assert.equal(minimal.lastPositionUtc, 'Not reported');
  assert.equal(minimal.lastUtc, 'Not reported');
  assert.equal(minimal.ageFormatted, 'Not reported');
  assert.equal(minimal.isStale, false);

  // 3. Stale vessel (> 30 min)
  const stale = normalizeLiveVesselItem(
    {
      mmsi: '111222333',
      lat: 30.0,
      lon: 130.0,
      last_position_epoch: now - 3600,
    },
    now,
  );
  assert.equal(stale.isStale, true);
  assert.equal(stale.ageFormatted, '1h 0m ago');

  // 4. Invalid inputs return null
  assert.equal(normalizeLiveVesselItem(null), null);
  assert.equal(normalizeLiveVesselItem({ mmsi: '' }), null);
  assert.equal(
    normalizeLiveVesselItem({ mmsi: '123', lat: 'invalid', lon: 10 }),
    null,
  );
});

test('calculateDistanceToRouteNm computes cross-track distance and antimeridian handling', () => {
  // Simple route: straight along equator from lon 0 to lon 10
  const simpleRoute = {
    geometrySegments: [
      [
        [0.0, 0.0],
        [10.0, 0.0],
      ],
    ],
  };

  // Vessel at (lat 0.5, lon 5.0) -> distance is 0.5 degrees lat = 30 nautical miles
  const d1 = calculateDistanceToRouteNm(0.5, 5.0, simpleRoute);
  assert.ok(Math.abs(d1 - 30.0) < 0.5, `Expected ~30 NM, got ${d1}`);

  // Vessel at (lat 2.0, lon 5.0) -> distance is 2 degrees lat = 120 nautical miles
  const d2 = calculateDistanceToRouteNm(2.0, 5.0, simpleRoute);
  assert.ok(Math.abs(d2 - 120.0) < 1.0, `Expected ~120 NM, got ${d2}`);

  // Null route returns null
  assert.equal(calculateDistanceToRouteNm(10, 10, null), null);
  assert.equal(
    calculateDistanceToRouteNm(10, 10, { geometrySegments: [] }),
    null,
  );
  assert.equal(calculateDistanceToRouteNm(NaN, 10, simpleRoute), null);
});

test('deduplicateAndFilterVessels deduplicates duplicate MMSI entries and calculates corridor relevance', () => {
  const route = {
    geometrySegments: [
      [
        [0.0, 0.0],
        [10.0, 0.0],
      ],
    ],
  };

  const rawRows = [
    // Older fix for Vessel A (in corridor)
    {
      mmsi: '111',
      name: 'SHIP ALPHA',
      lat: 0.2, // ~12 NM
      lon: 5.0,
      type: 'Cargo',
      last_position_epoch: 1000,
    },
    // Newer fix for Vessel A (still in corridor)
    {
      mmsi: '111',
      name: 'SHIP ALPHA',
      lat: 0.3, // ~18 NM
      lon: 5.0,
      type: 'Cargo',
      last_position_epoch: 2000,
    },
    // Vessel B (in corridor)
    {
      mmsi: '222',
      name: 'SHIP BETA',
      lat: 0.4, // ~24 NM
      lon: 2.0,
      type: 'Tanker',
      last_position_epoch: 1500,
    },
    // Vessel C (far away, > 50 NM from corridor)
    {
      mmsi: '333',
      name: 'SHIP GAMMA',
      lat: 5.0, // ~300 NM away
      lon: 5.0,
      type: 'Fishing',
      last_position_epoch: 1800,
    },
  ];

  // 1. All tab: should have 3 deduplicated vessels (A, B, C)
  const resultAll = deduplicateAndFilterVessels(rawRows, {
    filterTab: 'all',
    activeRoute: route,
  });

  assert.equal(resultAll.totalCount, 3, 'Should have 3 unique MMSIs');
  assert.equal(
    resultAll.routeRelevantCount,
    2,
    'Should have 2 vessels within 50 NM corridor',
  );
  assert.equal(resultAll.vessels.length, 3);

  // Newest fix for 111 should be preserved (lat 0.3, epoch 2000)
  const v111 = resultAll.vessels.find((v) => v.mmsi === '111');
  assert.equal(v111.lat, 0.3);
  assert.equal(v111.isNearRoute, true);

  // 2. Corridor tab: should only return 2 vessels
  const resultCorridor = deduplicateAndFilterVessels(rawRows, {
    filterTab: 'corridor',
    activeRoute: route,
  });
  assert.equal(resultCorridor.vessels.length, 2);
  assert.ok(resultCorridor.vessels.every((v) => v.isNearRoute));

  // 3. Search query filter
  const resultQuery = deduplicateAndFilterVessels(rawRows, {
    query: 'tanker',
    activeRoute: route,
  });
  assert.equal(resultQuery.vessels.length, 1);
  assert.equal(resultQuery.vessels[0].mmsi, '222');

  // 4. Empty rows
  const empty = deduplicateAndFilterVessels([], { activeRoute: route });
  assert.equal(empty.totalCount, 0);
  assert.equal(empty.vessels.length, 0);
});

test('deduplicateAndFilterVessels handles equal sort keys deterministically using distance, name, and MMSI tie-breakers', () => {
  const route = {
    geometrySegments: [
      [
        [0.0, 0.0],
        [10.0, 0.0],
      ],
    ],
  };

  // Case 1: Equal distance (~12 NM at lat 0.2), different display names
  // 'ALPHA CARRIER' vs 'ZULU CARRIER'
  const rowsEqualDistance = [
    {
      mmsi: '200',
      name: 'ZULU CARRIER',
      lat: 0.2,
      lon: 5.0,
      last_position_epoch: 1000,
    },
    {
      mmsi: '100',
      name: 'ALPHA CARRIER',
      lat: 0.2,
      lon: 5.0,
      last_position_epoch: 1000,
    },
  ];

  const sortedByName = deduplicateAndFilterVessels(rowsEqualDistance, {
    activeRoute: route,
  }).vessels;
  assert.equal(
    sortedByName[0].mmsi,
    '100',
    'ALPHA should precede ZULU when distances are equal',
  );
  assert.equal(sortedByName[1].mmsi, '200');

  // Case 2: Equal distance AND identical display names -> tie-broken by MMSI
  const rowsIdenticalName = [
    {
      mmsi: '999888777',
      name: 'PACIFIC TRADER',
      lat: 0.2,
      lon: 5.0,
      last_position_epoch: 1000,
    },
    {
      mmsi: '111222333',
      name: 'PACIFIC TRADER',
      lat: 0.2,
      lon: 5.0,
      last_position_epoch: 1000,
    },
  ];

  const sortedByMmsi1 = deduplicateAndFilterVessels(rowsIdenticalName, {
    activeRoute: route,
  }).vessels;
  assert.equal(
    sortedByMmsi1[0].mmsi,
    '111222333',
    'MMSI 111222333 should precede 999888777 as tie-breaker',
  );
  assert.equal(sortedByMmsi1[1].mmsi, '999888777');

  // Verify stability regardless of input ordering
  const sortedByMmsi2 = deduplicateAndFilterVessels(
    [...rowsIdenticalName].reverse(),
    {
      activeRoute: route,
    },
  ).vessels;
  assert.equal(
    sortedByMmsi2[0].mmsi,
    '111222333',
    'Order must be deterministic regardless of input array ordering',
  );
  assert.equal(sortedByMmsi2[1].mmsi, '999888777');
});
