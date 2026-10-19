/** Finish unmatched API requests before Vite's HTML fallback. Install last. */
export function apiNotFoundPlugin() {
  const install = (server) => {
    server.middlewares.use('/api', (req, res, next) => {
      // Allow /api/v1 and /api/v1/* requests to continue to Vite's proxy middleware.
      if (
        req.url === '/v1' ||
        req.url.startsWith('/v1/') ||
        req.url.startsWith('/v1?') ||
        req.originalUrl === '/api/v1' ||
        req.originalUrl?.startsWith('/api/v1/') ||
        req.originalUrl?.startsWith('/api/v1?')
      ) {
        return next();
      }
      res.writeHead(404, {
        'Content-Type': 'application/json',
        'Cache-Control': 'no-store',
      });
      res.end(JSON.stringify({ error: 'Unknown API route' }));
    });
  };
  return {
    name: 'api-not-found',
    configureServer: install,
    configurePreviewServer: install,
  };
}
