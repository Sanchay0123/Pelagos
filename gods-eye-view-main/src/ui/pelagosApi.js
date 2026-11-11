/**
 * @module ui/pelagosApi
 * @description Read-only API client for Pelagos Stage E1 overview and explorer endpoints.
 */

function serializeQueryParams(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue;
    search.set(key, String(value));
  }
  return search.toString();
}

async function readJsonSafely(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function buildHttpErrorMessage(status, payload, fallback) {
  if (payload && typeof payload.detail === 'string' && payload.detail.trim()) {
    return payload.detail.trim();
  }
  if (payload && typeof payload.reason === 'string' && payload.reason.trim()) {
    return payload.reason.trim();
  }
  return `${fallback} (HTTP ${status})`;
}

/**
 * Fetch platform-wide live overview summary from `/api/v1/platform-data/overview`.
 * @param {object} [options]
 * @param {AbortSignal} [options.signal]
 * @param {boolean} [options.useCache=true]
 * @param {Function} [options.fetchImpl]
 */
export async function fetchPlatformOverview({
  signal,
  useCache = true,
  fetchImpl = globalThis.fetch,
} = {}) {
  if (typeof fetchImpl !== 'function') {
    return {
      ok: false,
      statusCode: 0,
      data: null,
      error: 'Fetch API unavailable',
    };
  }
  const qs = serializeQueryParams({ use_cache: useCache ? 'true' : 'false' });
  const url = `/api/v1/platform-data/overview${qs ? `?${qs}` : ''}`;
  try {
    const response = await fetchImpl(url, { signal, cache: 'no-store' });
    const payload = await readJsonSafely(response);
    if (!response.ok) {
      return {
        ok: false,
        statusCode: response.status,
        data: payload,
        error: buildHttpErrorMessage(
          response.status,
          payload,
          'Failed to load platform data overview',
        ),
      };
    }
    return {
      ok: true,
      statusCode: response.status,
      data: payload,
      error: null,
    };
  } catch (err) {
    if (err?.name === 'AbortError') {
      return {
        ok: false,
        aborted: true,
        statusCode: 0,
        data: null,
        error: null,
      };
    }
    return {
      ok: false,
      statusCode: 0,
      data: null,
      error:
        err?.message || 'Network error while connecting to Pelagos backend',
    };
  }
}

/**
 * Fetch paginated, filtered, sorted records from `/api/v1/platform-data/explorer/{domain}`.
 * @param {string} domain
 * @param {object} [query]
 * @param {object} [options]
 * @param {AbortSignal} [options.signal]
 * @param {Function} [options.fetchImpl]
 */
export async function fetchExplorerDomain(
  domain,
  query = {},
  { signal, fetchImpl = globalThis.fetch } = {},
) {
  if (typeof fetchImpl !== 'function') {
    return {
      ok: false,
      statusCode: 0,
      data: null,
      error: 'Fetch API unavailable',
    };
  }
  const cleanDomain = encodeURIComponent(
    String(domain || '')
      .trim()
      .toLowerCase(),
  );
  const qs = serializeQueryParams(query);
  const url = `/api/v1/platform-data/explorer/${cleanDomain}${qs ? `?${qs}` : ''}`;
  try {
    const response = await fetchImpl(url, { signal, cache: 'no-store' });
    const payload = await readJsonSafely(response);
    if (!response.ok) {
      return {
        ok: false,
        statusCode: response.status,
        data: payload,
        error: buildHttpErrorMessage(
          response.status,
          payload,
          `Failed to load explorer domain "${domain}"`,
        ),
      };
    }
    return {
      ok: true,
      statusCode: response.status,
      data: payload,
      error: null,
    };
  } catch (err) {
    if (err?.name === 'AbortError') {
      return {
        ok: false,
        aborted: true,
        statusCode: 0,
        data: null,
        error: null,
      };
    }
    return {
      ok: false,
      statusCode: 0,
      data: null,
      error: err?.message || 'Network error while querying explorer endpoint',
    };
  }
}
