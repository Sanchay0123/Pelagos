/**
 * @module ui/pelagosApi
 * @description API client helpers for Pelagos Stage E1 overview/explorer and D2.1 charter assessment endpoints.
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

/**
 * Load all available registered vessel records by paginating `/api/v1/platform-data/explorer/vessels`.
 * @param {object} [options]
 * @param {string} [options.search]
 * @param {AbortSignal} [options.signal]
 * @param {Function} [options.fetchImpl]
 */
export async function fetchRegisteredVessels({
  search = '',
  signal,
  fetchImpl = globalThis.fetch,
} = {}) {
  const allItems = [];
  let page = 1;
  let totalCount = 0;
  let summary = null;

  while (true) {
    const res = await fetchExplorerDomain(
      'vessels',
      {
        page,
        page_size: 100,
        sort_by: 'vessel_name',
        sort_order: 'asc',
        search: search || undefined,
      },
      { signal, fetchImpl },
    );

    if (!res.ok) {
      return {
        ok: false,
        aborted: res.aborted || false,
        statusCode: res.statusCode || 0,
        items: allItems,
        totalCount: 0,
        summary: null,
        error: res.error || 'Failed to load registered vessels',
      };
    }

    const payload = res.data || {};
    const pageItems = Array.isArray(payload.items) ? payload.items : [];
    allItems.push(...pageItems);
    totalCount = Number(payload.total_count ?? allItems.length);
    if (!summary && payload.summary) summary = payload.summary;

    const totalPages = Number(payload.total_pages ?? 1);
    const hasNext =
      payload.has_next !== undefined
        ? Boolean(payload.has_next) && page < totalPages
        : page < totalPages;
    if (!hasNext || pageItems.length === 0 || page >= 20) {
      break;
    }
    page += 1;
  }

  return {
    ok: true,
    statusCode: 200,
    items: allItems,
    totalCount,
    summary,
    error: null,
  };
}

/**
 * Internal shared GET/POST JSON client for Pelagos charter intelligence endpoints.
 * @param {string} baseUrl
 * @param {object} request
 * @param {object} [options]
 */
async function requestCharterEndpoint(
  baseUrl,
  request = {},
  {
    method = 'POST',
    signal,
    fetchImpl = globalThis.fetch,
    defaultErrorMessage = 'Charter request failed',
    networkErrorMessage = 'Network error while calling Pelagos charter API',
  } = {},
) {
  if (typeof fetchImpl !== 'function') {
    return {
      ok: false,
      statusCode: 0,
      data: null,
      error: 'Fetch API unavailable',
    };
  }

  const cleanMethod = String(method || 'POST').toUpperCase();
  let url = baseUrl;
  const fetchOpts = {
    method: cleanMethod,
    headers: { Accept: 'application/json' },
    signal,
  };

  if (cleanMethod === 'GET') {
    const qs = serializeQueryParams(request);
    url = `${url}${qs ? `?${qs}` : ''}`;
  } else {
    fetchOpts.headers['Content-Type'] = 'application/json';
    fetchOpts.body = JSON.stringify(request);
  }

  try {
    const response = await fetchImpl(url, fetchOpts);
    const payload = await readJsonSafely(response);
    if (!response.ok) {
      return {
        ok: false,
        statusCode: response.status,
        data: payload,
        error: buildHttpErrorMessage(
          response.status,
          payload,
          defaultErrorMessage,
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
      error: err?.message || networkErrorMessage,
    };
  }
}

/**
 * Assess a single vessel against proposed cargo and route via `/api/v1/charter/assess`.
 * Supports both POST (default JSON body) and GET (query string).
 * @param {object} request
 * @param {object} [options]
 * @param {'POST'|'GET'} [options.method='POST']
 * @param {AbortSignal} [options.signal]
 * @param {Function} [options.fetchImpl]
 */
export async function assessVesselCharter(
  request = {},
  { method = 'POST', signal, fetchImpl = globalThis.fetch } = {},
) {
  return requestCharterEndpoint('/api/v1/charter/assess', request, {
    method,
    signal,
    fetchImpl,
    defaultErrorMessage: 'Charter compatibility assessment request failed',
    networkErrorMessage:
      'Network error while calling single-vessel charter assessment API',
  });
}

/**
 * Evaluate the six charter-readiness gates and recommendation gate via `/api/v1/charter/recommend`.
 * Supports both POST (default JSON body) and GET (query string).
 * @param {object} request
 * @param {object} [options]
 * @param {'POST'|'GET'} [options.method='POST']
 * @param {AbortSignal} [options.signal]
 * @param {Function} [options.fetchImpl]
 */
export async function recommendCharterReadiness(
  request = {},
  { method = 'POST', signal, fetchImpl = globalThis.fetch } = {},
) {
  return requestCharterEndpoint('/api/v1/charter/recommend', request, {
    method,
    signal,
    fetchImpl,
    defaultErrorMessage: 'Charter readiness audit request failed',
    networkErrorMessage:
      'Network error while calling charter readiness recommendation API',
  });
}
