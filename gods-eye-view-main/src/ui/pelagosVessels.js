/**
 * @module ui/pelagosVessels
 * @description Vessel data normalization, corridor proximity calculation, and filtering
 * for the Pelagos Maritime Intelligence workspace.
 */

/** Standard maritime trade corridor proximity buffer in nautical miles. */
export const CORRIDOR_PROXIMITY_NM = 50.0;

/**
 * Format elapsed time into human-readable compact string.
 * @param {number|null} epochSec - Unix epoch timestamp in seconds.
 * @param {number} [nowEpochSec] - Current epoch seconds reference.
 * @returns {string}
 */
export function formatRelativeAge(
  epochSec,
  nowEpochSec = Math.floor(Date.now() / 1000),
) {
  if (!Number.isFinite(epochSec) || epochSec <= 0) return 'Not reported';
  const diffSec = Math.max(0, nowEpochSec - epochSec);
  if (diffSec < 60) return '< 1m ago';
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400)
    return `${Math.floor(diffSec / 3600)}h ${Math.floor((diffSec % 3600) / 60)}m ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

/**
 * Format latitude and longitude coordinates into navigation degrees notation.
 * @param {number} lat
 * @param {number} lon
 * @returns {string}
 */
export function formatVesselCoords(lat, lon) {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return 'Not reported';
  const latDir = lat >= 0 ? 'N' : 'S';
  const lonDir = lon >= 0 ? 'E' : 'W';
  return `${Math.abs(lat).toFixed(4)}°${latDir}, ${Math.abs(lon).toFixed(4)}°${lonDir}`;
}

/**
 * Normalize an incoming raw AIS row from the /api/ais-live feed into a clean,
 * verifiable vessel data object.
 *
 * Requirements:
 * - Shows "Unknown" or "Not reported" for missing fields.
 * - Never invents vessel names, destinations, or coordinates.
 * - Accurately notes whether data is historical sample vs live telemetry.
 *
 * @param {Object} row - Raw AIS record from feed.
 * @param {number} [nowEpochSec] - Current epoch seconds reference.
 * @returns {Object|null} Normalized vessel object, or null if coordinates/MMSI are unusable.
 */
export function normalizeLiveVesselItem(
  row,
  nowEpochSec = Math.floor(Date.now() / 1000),
) {
  if (!row || typeof row !== 'object') return null;

  const lat = Number(row.lat);
  const lon = Number(row.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;

  const mmsi = String(row.mmsi || '').trim();
  if (!mmsi) return null;

  const rawName = String(row.name || '').trim();
  const isFallbackName = !rawName || /^MMSI\s*\d+$/i.test(rawName);
  const displayName = isFallbackName ? `MMSI ${mmsi}` : rawName;

  const imo =
    row.imo && String(row.imo).trim() ? String(row.imo).trim() : 'Not reported';
  const type =
    row.type && String(row.type).trim()
      ? String(row.type).trim()
      : 'Not reported';
  const destination =
    row.destination && String(row.destination).trim()
      ? String(row.destination).trim()
      : 'Not reported';

  const speedVal = Number(row.speed);
  const speed = Number.isFinite(speedVal) && speedVal >= 0 ? speedVal : null;
  const speedFormatted =
    speed !== null ? `${speed.toFixed(1)} kn` : 'Not reported';

  const courseVal = Number(row.course);
  const course =
    Number.isFinite(courseVal) && courseVal >= 0 && courseVal < 360
      ? courseVal
      : null;
  const courseFormatted =
    course !== null ? `${Math.round(course)}°` : 'Not reported';

  const headingVal = Number(row.heading);
  const heading =
    Number.isFinite(headingVal) &&
    headingVal >= 0 &&
    headingVal < 360 &&
    headingVal !== 511
      ? headingVal
      : null;
  const headingFormatted =
    heading !== null ? `${Math.round(heading)}°` : 'Not reported';

  const lastPositionUtc =
    row.last_position_UTC || row.lastPositionUtc || 'Not reported';
  const epochVal = Number(row.last_position_epoch ?? row.lastPositionEpoch);
  let lastEpoch = null;
  if (Number.isFinite(epochVal) && epochVal > 0) {
    lastEpoch = epochVal;
  } else if (lastPositionUtc !== 'Not reported') {
    const parsed = Date.parse(lastPositionUtc);
    if (Number.isFinite(parsed)) lastEpoch = Math.floor(parsed / 1000);
  }

  const ageFormatted = formatRelativeAge(lastEpoch, nowEpochSec);
  const isStale = lastEpoch !== null ? nowEpochSec - lastEpoch > 1800 : false;
  const isHistorical = Boolean(row.historical);
  const navStatus = row.navigational_status || row.navStatus || 'Not reported';

  return {
    mmsi,
    displayName,
    name: displayName,
    hasReportedName: !isFallbackName,
    imo,
    type,
    displayType: type !== 'Not reported' ? type : 'Not reported',
    destination,
    lat,
    lon,
    speed,
    speedFormatted,
    course,
    courseFormatted,
    heading,
    headingFormatted,
    lastPositionUtc,
    lastUtc: lastPositionUtc,
    lastEpoch,
    ageFormatted,
    isStale,
    isHistorical,
    navStatus,
  };
}

/**
 * Calculate the minimum distance in nautical miles from a vessel coordinate to any
 * segment of the currently active MARNET maritime route.
 *
 * Algorithm:
 * - Iterates over all consecutive waypoint pairs along all route geometry segments.
 * - Handles antimeridian crossing longitude wrapping (180° meridian).
 * - Computes localized equirectangular projection onto the segment centerline.
 * - Returns minimum nautical mile distance, or null if no route is active.
 *
 * Limitations:
 * - Proximity measures physical geometric distance (≤50 NM) to the generalized sea lane.
 * - It does NOT imply the vessel is traveling on that specific route or toward the destination port.
 * - It does NOT establish commercial charter availability or open-tonnage status.
 * - Local equirectangular projection is computationally efficient for interactive globe
 *   filtering, with minor distance distortion at high polar latitudes (>75°).
 *
 * @param {number} lat - Vessel latitude.
 * @param {number} lon - Vessel longitude.
 * @param {Object|null} activeRoute - Normalized route object containing geometrySegments.
 * @returns {number|null} Minimum distance in NM, or null if route is unavailable.
 */
export function calculateDistanceToRouteNm(lat, lon, activeRoute) {
  if (!activeRoute || !Number.isFinite(lat) || !Number.isFinite(lon))
    return null;

  const segments =
    Array.isArray(activeRoute.geometrySegments) &&
    activeRoute.geometrySegments.length > 0
      ? activeRoute.geometrySegments
      : Array.isArray(activeRoute.geometry) && activeRoute.geometry.length >= 2
        ? [activeRoute.geometry]
        : [];

  if (segments.length === 0) return null;

  let minDistanceNm = Number.POSITIVE_INFINITY;

  for (const seg of segments) {
    if (!Array.isArray(seg) || seg.length < 2) continue;

    for (let i = 0; i < seg.length - 1; i++) {
      const p1 = seg[i];
      const p2 = seg[i + 1];
      if (!Array.isArray(p1) || !Array.isArray(p2)) continue;

      let lon1 = p1[0];
      let lat1 = p1[1];
      let lon2 = p2[0];
      let lat2 = p2[1];
      let vLon = lon;
      let vLat = lat;

      if (
        !Number.isFinite(lon1) ||
        !Number.isFinite(lat1) ||
        !Number.isFinite(lon2) ||
        !Number.isFinite(lat2)
      ) {
        continue;
      }

      // Handle antimeridian wrapping between segment points
      if (lon2 - lon1 > 180) lon2 -= 360;
      else if (lon1 - lon2 > 180) lon2 += 360;

      // Wrap vessel longitude relative to lon1
      if (vLon - lon1 > 180) vLon -= 360;
      else if (lon1 - vLon > 180) vLon += 360;

      // Segment vector in degrees scaled by local cos(lat)
      const midLatRad = (((lat1 + lat2) / 2) * Math.PI) / 180;
      const cosMidLat = Math.cos(midLatRad);

      const dx = (lon2 - lon1) * cosMidLat;
      const dy = lat2 - lat1;
      const segLenSq = dx * dx + dy * dy;

      let distDeg;
      if (segLenSq < 1e-12) {
        const px = (vLon - lon1) * cosMidLat;
        const py = vLat - lat1;
        distDeg = Math.hypot(px, py);
      } else {
        const px = (vLon - lon1) * cosMidLat;
        const py = vLat - lat1;
        const t = Math.max(0, Math.min(1, (px * dx + py * dy) / segLenSq));
        const projX = t * dx;
        const projY = t * dy;
        distDeg = Math.hypot(px - projX, py - projY);
      }

      const distNm = distDeg * 60.0;
      if (distNm < minDistanceNm) {
        minDistanceNm = distNm;
      }
    }
  }

  return Number.isFinite(minDistanceNm) ? minDistanceNm : null;
}

/**
 * Deduplicate raw vessel rows by MMSI, annotate route proximity, and filter
 * according to user query and corridor tabs.
 *
 * @param {Array<Object>} rawRows - Array of raw rows from AIS feed.
 * @param {Object} [options]
 * @param {string} [options.query=''] - Search filter text.
 * @param {'all'|'corridor'} [options.filterTab='all'] - Active tab filter.
 * @param {Object|null} [options.activeRoute=null] - Currently calculated maritime route.
 * @param {number} [options.maxDistanceNm=CORRIDOR_PROXIMITY_NM] - Corridor distance threshold in NM.
 * @param {number} [options.nowEpochSec] - Current epoch seconds reference.
 * @returns {{ vessels: Array<Object>, totalCount: number, routeRelevantCount: number }}
 */
export function deduplicateAndFilterVessels(rawRows, options = {}) {
  const {
    query = '',
    searchQuery = '',
    filterTab = 'all',
    tab = 'all',
    activeRoute = null,
    maxDistanceNm = CORRIDOR_PROXIMITY_NM,
    nowEpochSec = Math.floor(Date.now() / 1000),
  } = options;

  const effectiveQuery = String(query || searchQuery || '')
    .trim()
    .toLowerCase();
  const effectiveTab = filterTab !== 'all' ? filterTab : tab;

  if (!Array.isArray(rawRows) || rawRows.length === 0) {
    return {
      vessels: [],
      totalCount: 0,
      routeRelevantCount: 0,
      corridorCount: 0,
    };
  }

  // 1. Deduplicate by MMSI (keep newest fix if multiple)
  const byMmsi = new Map();
  for (const row of rawRows) {
    const item = normalizeLiveVesselItem(row, nowEpochSec);
    if (!item) continue;

    const existing = byMmsi.get(item.mmsi);
    if (!existing || (item.lastEpoch || 0) > (existing.lastEpoch || 0)) {
      byMmsi.set(item.mmsi, item);
    }
  }

  const allVessels = Array.from(byMmsi.values());
  let routeRelevantCount = 0;

  // 2. Annotate route proximity
  for (const v of allVessels) {
    const distNm = calculateDistanceToRouteNm(v.lat, v.lon, activeRoute);
    v.distanceToRouteNm = distNm !== null ? Math.round(distNm * 10) / 10 : null;
    v.isNearRoute = distNm !== null && distNm <= maxDistanceNm;
    v.isNearCorridor = v.isNearRoute;
    if (v.isNearRoute) {
      routeRelevantCount += 1;
    }
  }

  // 3. Sort: near-corridor vessels first, then by distance, then by name, then deterministic tie-breaker by MMSI
  allVessels.sort((a, b) => {
    if (a.isNearRoute && !b.isNearRoute) return -1;
    if (!a.isNearRoute && b.isNearRoute) return 1;

    if (a.distanceToRouteNm !== null && b.distanceToRouteNm !== null) {
      const distDiff = a.distanceToRouteNm - b.distanceToRouteNm;
      if (Math.abs(distDiff) > 1e-6) {
        return distDiff;
      }
    }

    const nameDiff = String(a.displayName || '').localeCompare(
      String(b.displayName || ''),
    );
    if (nameDiff !== 0) {
      return nameDiff;
    }

    return String(a.mmsi || '').localeCompare(String(b.mmsi || ''));
  });

  // 4. Filter by tab
  let filtered = allVessels;
  if (effectiveTab === 'corridor') {
    filtered = filtered.filter((v) => v.isNearRoute);
  }

  // 5. Filter by text search query
  if (effectiveQuery) {
    filtered = filtered.filter((v) => {
      return (
        v.displayName.toLowerCase().includes(effectiveQuery) ||
        v.mmsi.toLowerCase().includes(effectiveQuery) ||
        v.type.toLowerCase().includes(effectiveQuery) ||
        v.destination.toLowerCase().includes(effectiveQuery) ||
        v.imo.toLowerCase().includes(effectiveQuery)
      );
    });
  }

  return {
    vessels: filtered,
    totalCount: allVessels.length,
    routeRelevantCount,
    corridorCount: routeRelevantCount,
  };
}
