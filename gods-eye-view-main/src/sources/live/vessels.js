import { coordinates, epoch, finite, LiveSourceError } from './contract.js';

/** AIS positions are sea-surface observations; heading and course are distinct. */
export function normalizeVesselObservation(row, reference = null) {
  const id = String(row?.mmsi || row?.input_identifier || '').trim();
  const latitude = finite(row?.lat),
    longitude = finite(row?.lon);
  if (!id || !coordinates(latitude, longitude)) return null;
  const speedKts = finite(row.speed);
  return {
    id,
    reference: reference ?? id,
    latitude,
    longitude,
    name: String(row.name || row.input_name || id),
    imo: String(row.imo || ''),
    type: String(row.type_specific || row.type || ''),
    destination: String(row.destination || ''),
    speedMps: speedKts == null ? null : speedKts * 0.514444,
    courseDeg: finite(row.course),
    headingDeg: finite(row.heading),
    observedAtMs:
      epoch(row.last_position_epoch, 1000) ??
      epoch(Date.parse(row.last_position_UTC)),
    altitudeDatum: 'sea-surface',
    historical: Boolean(row.historical),
  };
}

export function vesselSnapshot(
  payload,
  {
    source = 'AISStream',
    coverage = 'received AIS positions',
    referenceFor = (row) => String(row.mmsi || row.input_identifier || ''),
  } = {},
) {
  if (!Array.isArray(payload?.rows))
    throw new LiveSourceError('malformed', 'Malformed vessel response');
  const rows = payload.rows;
  const isHistorical =
    payload?.data_mode === 'historical' ||
    payload?.mode === 'historical' ||
    payload?.status === 'historical' ||
    payload?.status === 'replay' ||
    payload?.is_live === false ||
    payload?.freshness === 'historical';
  const records = [],
    ids = new Set();
  for (const row of rows) {
    const record = normalizeVesselObservation(row);
    if (record) {
      record.reference = referenceFor(row);
      if (isHistorical) record.historical = true;
    }
    if (record && !ids.has(record.id)) {
      records.push(record);
      ids.add(record.id);
    }
  }
  const observedAtMs =
    epoch(payload?.newestPositionAt) ??
    epoch(Date.parse(payload?.newestPositionAt)) ??
    records.reduce(
      (newest, row) => Math.max(newest || 0, row.observedAtMs || 0) || null,
      null,
    );
  const freshness = payload?.freshness
    ? payload.freshness
    : isHistorical
      ? 'historical'
      : payload?.refreshing
        ? 'stale'
        : observedAtMs == null
          ? 'unknown'
          : 'current';
  return {
    records,
    source: payload?.source || source,
    coverage: payload?.coverage || coverage,
    complete: records.length === rows.length && !payload?.refreshing,
    rejectedCount: rows.length - records.length,
    observedAtMs,
    freshness,
    stale: Boolean(payload?.refreshing),
    dataMode: isHistorical ? 'historical' : 'live',
    isLive: !isHistorical,
    observationDate:
      payload?.observation_date ||
      (observedAtMs ? new Date(observedAtMs).toISOString().slice(0, 10) : null),
    // Connection state is distinct from receipt of a usable position.
    transportStatus:
      typeof payload?.status === 'string'
        ? payload.status
        : isHistorical
          ? 'historical'
          : null,
    lastMessageAt: payload?.lastMessageAt ?? null,
    nextAttemptAt: finite(payload?.nextAttemptAt),
    silentForMs: finite(payload?.silentForMs),
    reconnectAttempt: finite(payload?.reconnectAttempt),
    rawRowCount: rows.length,
  };
}

export function normalizeVesselTrack(samples) {
  if (!Array.isArray(samples)) return [];
  return samples.flatMap((sample) => {
    const latitude = finite(sample?.lat),
      longitude = finite(sample?.lon);
    if (!coordinates(latitude, longitude)) return [];
    return [
      {
        latitude,
        longitude,
        observedAtMs: epoch(sample?.t, 1000),
        altitudeDatum: 'sea-surface',
      },
    ];
  });
}
