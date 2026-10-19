import assert from 'node:assert/strict';
import http from 'node:http';
import test from 'node:test';
import { writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import { build, createServer, preview } from 'vite';
import { localProviderPlugins } from '../../server/providers/local.js';
import { apiNotFoundPlugin } from '../../server/standalone/api-not-found.js';
import { makeFixtureRoot } from './fixtureRoot.mjs';

test('data providers have both hooks; credential editing stays development-only', () => {
  for (const plugin of localProviderPlugins()) {
    if (plugin.name === 'gev-key-setup') {
      assert.equal(plugin.configurePreviewServer, undefined);
      assert.equal(
        plugin.apply({}, { command: 'serve', isPreview: true }),
        false,
      );
      assert.equal(
        plugin.apply({}, { command: 'serve', isPreview: false }),
        true,
      );
      continue;
    }
    assert.equal(typeof plugin.configureServer, 'function', plugin.name);
    assert.equal(typeof plugin.configurePreviewServer, 'function', plugin.name);
  }
});

test('real dev and built-preview servers serve provider JSON and terminate unknown APIs', async (t) => {
  // Physical path: Vite's root and the files written under it must agree on one
  // spelling, and macOS reaches the temp directory through a symlink.
  const root = await makeFixtureRoot('gev-preview-');
  t.after(() => rm(root, { recursive: true, force: true }));
  await writeFile(
    path.join(root, 'index.html'),
    '<!doctype html><title>Preview fixture</title><p>Built application</p>',
  );
  for (const [name, value] of Object.entries({
    AISSTREAM_API_KEY: '',
    FIRMS_MAP_KEY: '',
    FIRMS_API_KEY: '',
    TOMTOM_API_KEY: '',
    GOOGLE_MAPS_API_KEY: '',
    GOOGLE_MAPS_SERVER_API_KEY: '',
    OPENAI_API_KEY: '',
    OPENSKY_AUTH_MODE: 'anon',
    OPENSKY_CLIENT_ID: '',
    OPENSKY_CLIENT_SECRET: '',
    CCTV_FORCE_AUSTIN: '0',
    CCTV_SOURCES_FILE: path.join(root, 'absent.json'),
    CCTV_SOURCES_JSON: JSON.stringify([
      { id: 'fixture', lat: 30.27, lon: -97.74 },
    ]),
  })) {
    const before = process.env[name];
    process.env[name] = value;
    t.after(() => {
      if (before === undefined) delete process.env[name];
      else process.env[name] = before;
    });
  }
  const nativeFetch = globalThis.fetch;
  t.mock.method(globalThis, 'fetch', async (raw, options) => {
    const url = new URL(raw);
    if (url.hostname === '127.0.0.1') return nativeFetch(raw, options);
    assert.ok(
      ['opensky-network.org', 'api.adsb.lol'].includes(url.hostname),
      url.origin,
    );
    return Response.json({
      time: Math.floor(Date.now() / 1000),
      states: [],
      ac: [],
    });
  });
  const base = {
    root,
    configFile: false,
    envFile: false,
    publicDir: false,
    logLevel: 'silent',
  };
  await build(base);
  for (const isPreview of [false, true]) {
    const config = {
      ...base,
      plugins: [...localProviderPlugins(), apiNotFoundPlugin()],
      server: { host: '127.0.0.1', port: 0, hmr: false },
      preview: { host: '127.0.0.1', port: 0 },
    };
    const server = isPreview
      ? await preview(config)
      : await createServer(config);
    if (!isPreview) await server.listen();
    const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
    try {
      for (const [route, status] of [
        ['/api/opensky', 200],
        ['/api/adsblol/mil', 200],
        ['/api/adsbdb/type/invalid', 400],
        ['/api/firms/status', 200],
        ['/api/terrain/heights?points=invalid', 400],
        ['/api/overpass', 405],
        ['/api/cctv/sources', 200],
        ['/api/gbfs/', 400],
        ['/api/tomtom/status', 200],
        ['/api/radio/unknown', 404],
        ['/api/setup/status', isPreview ? 404 : 200],
        ['/api/setup/update', 404],
        ['/api/does-not-exist', 404],
        ['/api', 404],
      ]) {
        const response = await fetch(origin + route);
        assert.equal(
          response.status,
          status,
          `${isPreview ? 'preview' : 'dev'} ${route}`,
        );
        assert.match(
          response.headers.get('content-type'),
          /application\/json/,
          route,
        );
        const body = await response.json();
        if (route === '/api/cctv/sources')
          assert.equal(body.sources[0].id, 'fixture');
        if (
          route === '/api/does-not-exist' ||
          (isPreview && route.startsWith('/api/setup'))
        ) {
          assert.deepEqual(body, { error: 'Unknown API route' });
          assert.equal(response.headers.get('cache-control'), 'no-store');
        }
      }
      const tle = await fetch(origin + '/api/celestrak/invalid!');
      assert.equal(tle.status, 400);
      assert.equal(await tle.text(), 'invalid group');
      if (isPreview) {
        const write = await fetch(origin + '/api/setup/keys', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ GOOGLE_MAPS_API_KEY: 'must-not-save' }),
        });
        assert.equal(write.status, 404);
        assert.deepEqual(await write.json(), { error: 'Unknown API route' });
      }
      for (const route of ['/', '/application-route', '/apiary']) {
        const response = await fetch(origin + route);
        assert.equal(response.status, 200);
        assert.match(response.headers.get('content-type'), /text\/html/);
        assert.match(await response.text(), /Built application/);
      }
    } finally {
      await server.close();
    }
  }
});

test('apiNotFoundPlugin allows /api/v1 requests to reach proxy in dev and downstream middleware in preview', async (t) => {
  const backend = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ proxied: true, url: req.url }));
  });
  await new Promise((resolve) => backend.listen(0, '127.0.0.1', resolve));
  const backendPort = backend.address().port;
  const backendOrigin = `http://127.0.0.1:${backendPort}`;

  const root = await makeFixtureRoot('gev-v1-pass-');
  t.after(async () => {
    backend.close();
    await rm(root, { recursive: true, force: true });
  });

  await writeFile(
    path.join(root, 'index.html'),
    '<!doctype html><title>V1 Test</title><p>Built application</p>',
  );
  const base = {
    root,
    configFile: false,
    envFile: false,
    publicDir: false,
    logLevel: 'silent',
  };
  await build(base);

  // 1. Development mode: proxy configured for /api/v1 must receive the request
  const devServer = await createServer({
    ...base,
    plugins: [apiNotFoundPlugin()],
    server: {
      host: '127.0.0.1',
      port: 0,
      hmr: false,
      proxy: {
        '/api/v1': {
          target: backendOrigin,
          changeOrigin: true,
        },
      },
    },
  });
  await devServer.listen();
  const devOrigin = `http://127.0.0.1:${devServer.httpServer.address().port}`;

  try {
    const v1Res = await fetch(`${devOrigin}/api/v1/routing/calculate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ origin: [0, 0], destination: [1, 1] }),
    });
    assert.equal(v1Res.status, 200, 'dev /api/v1 should reach backend proxy');
    assert.deepEqual(await v1Res.json(), {
      proxied: true,
      url: '/api/v1/routing/calculate',
    });

    const unknownRes = await fetch(`${devOrigin}/api/does-not-exist`);
    assert.equal(unknownRes.status, 404, 'unknown non-v1 API should return 404');
    assert.deepEqual(await unknownRes.json(), { error: 'Unknown API route' });

    const nonV1Res = await fetch(`${devOrigin}/api/v2/routing/calculate`);
    assert.equal(nonV1Res.status, 404, 'non-v1 API should return 404');
    assert.deepEqual(await nonV1Res.json(), { error: 'Unknown API route' });
  } finally {
    await devServer.close();
  }

  // 2. Preview mode: downstream middleware receives /api/v1, while unknown API returns 404
  const previewServer = await preview({
    ...base,
    plugins: [
      apiNotFoundPlugin(),
      {
        name: 'downstream-preview-v1',
        configurePreviewServer(server) {
          server.middlewares.use('/api/v1', (req, res) => {
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ previewProxied: true, url: req.url }));
          });
        },
      },
    ],
    preview: { host: '127.0.0.1', port: 0 },
  });
  const previewOrigin = `http://127.0.0.1:${previewServer.httpServer.address().port}`;

  try {
    const prevV1Res = await fetch(`${previewOrigin}/api/v1/routing/calculate`, {
      method: 'POST',
    });
    assert.equal(prevV1Res.status, 200, 'preview /api/v1 should pass through');
    assert.deepEqual(await prevV1Res.json(), {
      previewProxied: true,
      url: '/routing/calculate',
    });

    const prevUnknownRes = await fetch(`${previewOrigin}/api/does-not-exist`);
    assert.equal(prevUnknownRes.status, 404, 'preview unknown API should return 404');
    assert.deepEqual(await prevUnknownRes.json(), { error: 'Unknown API route' });
  } finally {
    await previewServer.close();
  }
});
