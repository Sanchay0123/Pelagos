/**
 * @module ui/explorerRouter
 * @description Hash-route parser and serializer for the Detailed Data Explorer (#/explorer).
 */

export const EXPLORER_DOMAIN_DEFS = Object.freeze([
  { id: 'freight', label: 'Freight Market', shortLabel: 'Freight' },
  { id: 'trade', label: 'Trade Data', shortLabel: 'Trade' },
  { id: 'environmental', label: 'Environmental', shortLabel: 'Env Buoys' },
  { id: 'bunker', label: 'Bunker Fuel', shortLabel: 'Bunker' },
  { id: 'vessels', label: 'Vessel & AIS', shortLabel: 'Vessels' },
  { id: 'ports', label: 'Ports & Routes', shortLabel: 'Ports' },
  { id: 'datasets', label: 'Forecasting Datasets', shortLabel: 'Datasets' },
  { id: 'sources', label: 'Source Operations', shortLabel: 'Sources' },
]);

export const EXPLORER_DOMAIN_IDS = Object.freeze(
  EXPLORER_DOMAIN_DEFS.map((entry) => entry.id),
);

export const DEFAULT_EXPLORER_DOMAIN = 'freight';

/**
 * Parse a browser location hash into explorer navigation state.
 * Matches `#/explorer`, `#/explorer/`, `#/explorer/<domain>`, plus optional query string.
 * @param {string} [hash]
 * @returns {{active: boolean, domain: string, requestedDomain: string|null, isValidDomain: boolean}}
 */
export function parseExplorerHash(hash = '') {
  const raw = String(hash || '').trim();
  const normalized = raw.startsWith('#') ? raw.slice(1) : raw;
  const pathOnly = normalized.split('?')[0];

  if (pathOnly === '/explorer' || pathOnly === '/explorer/') {
    return {
      active: true,
      domain: DEFAULT_EXPLORER_DOMAIN,
      requestedDomain: null,
      isValidDomain: true,
    };
  }

  if (pathOnly.startsWith('/explorer/')) {
    const segment = pathOnly
      .slice('/explorer/'.length)
      .split('/')[0]
      .trim()
      .toLowerCase();
    if (!segment) {
      return {
        active: true,
        domain: DEFAULT_EXPLORER_DOMAIN,
        requestedDomain: null,
        isValidDomain: true,
      };
    }
    const isValidDomain = EXPLORER_DOMAIN_IDS.includes(segment);
    return {
      active: true,
      domain: isValidDomain ? segment : segment,
      requestedDomain: segment,
      isValidDomain,
    };
  }

  return {
    active: false,
    domain: DEFAULT_EXPLORER_DOMAIN,
    requestedDomain: null,
    isValidDomain: true,
  };
}

/**
 * Build canonical hash path for an explorer domain.
 * @param {string} [domain]
 * @returns {string}
 */
export function buildExplorerHash(domain = DEFAULT_EXPLORER_DOMAIN) {
  const clean = String(domain || DEFAULT_EXPLORER_DOMAIN)
    .trim()
    .toLowerCase();
  if (!EXPLORER_DOMAIN_IDS.includes(clean)) {
    return '#/explorer';
  }
  return `#/explorer/${clean}`;
}
