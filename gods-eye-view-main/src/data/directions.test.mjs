import test from 'node:test';
import assert from 'node:assert/strict';
import * as Cesium from 'cesium';
import {
  DEFAULT_DIRECTIONS_MODE,
  DIRECTIONS_MODES,
  DIRECTIONS_POINTER_OWNER,
  DIRECTIONS_ROUTE_COLOR,
  FREIGHT_INTELLIGENCE_ENDPOINT,
  MARITIME_ROUTING_ENDPOINT,
  buildMaritimeRoutingPayload,
  createDirectionsStepOverlayEntry,
  directionsRequestUrl,
  directionsRowControls,
  directionsStats,
  directionsStepCopy,
  directionsStepList,
  normalizeDirectionsParams,
  normalizeRoutePayload,
  stepAnchorDelayMs,
  stepIndexAtDistance,
  stepMarkerHeightM,
  stepMarkerIndices,
  pointerBlockedMessage,
  POINTER_TOOL_EXITS,
  STEP_ANCHOR_DEADLINE_MS,
  STEP_ANCHOR_RETRY_MS,
  STEP_ANCHOR_SLOW_RETRY_MS,
  STEP_ANCHOR_FAST_ATTEMPTS,
  createApplicationDirectionsLayer,
  createDirectionsLayer,
  directionsServices,
} from './directions.js';
import { GROUND_FLOOR_LIFT_M } from './groundFloor.js';
import {
  claimPointer,
  isPointerFree,
  pointerOwner,
  releasePointer,
  resetPointerOwnership,
} from './inputOwnership.js';

/** Take the pointer as some other tool, and hand back its release. */
function claimAs(owner) {
  const lease = claimPointer(owner);
  assert.ok(lease, `${owner} should have taken the pointer`);
  return () => releasePointer(lease);
}
import { LAYER_STATE_REGISTRY } from './layerState.js';
import { SPRITE_LAYER_ORDER } from './spriteOrder.js';

/** The instance under test; each ownership fixture builds a fresh one. */
let directionsLayer = createApplicationDirectionsLayer();

const idle = {
  enabled: true,
  mode: 'car',
  armed: null,
  a: null,
  b: null,
  status: 'idle',
  error: null,
  route: null,
  lastUpdate: null,
};
const A = { lat: 60.1699, lon: 24.9384 };
const B = { lat: 60.2055, lon: 24.6559 };
const route = {
  distanceM: 21479,
  durationS: 1478,
  geometry: [
    [24.9384, 60.1699],
    [24.6559, 60.2055],
  ],
  steps: [
    {
      index: 0,
      instruction: 'Head out on Kaivokatu',
      distanceM: 8,
      durationS: 1,
      lat: 60.1699,
      lon: 24.9384,
    },
    {
      index: 1,
      instruction: 'Turn right onto Kansakoulukatu',
      distanceM: 236,
      durationS: 39,
      lat: 60.1683,
      lon: 24.9349,
    },
    {
      index: 2,
      instruction: 'Arrive at your destination',
      distanceM: 0,
      durationS: 0,
      lat: 60.2055,
      lon: 24.6559,
    },
  ],
  mode: 'car',
};

test('layer module declares the manager contract, params, and row controls', () => {
  assert.equal(directionsLayer.id, 'directions');
  assert.equal(directionsLayer.updateInterval, 0);
  for (const method of [
    'init',
    'enable',
    'disable',
    'update',
    'getStats',
    'destroy',
    'setParams',
    'getParams',
    'getRowControls',
    'setRowControlsListener',
    'attachDataManager',
  ]) {
    assert.equal(
      typeof directionsLayer[method],
      'function',
      `${method} is implemented`,
    );
  }
  assert.deepEqual(directionsLayer.getParams(), {
    mode: DEFAULT_DIRECTIONS_MODE,
  });
  const entry = LAYER_STATE_REGISTRY.find((row) => row.id === 'directions');
  assert.ok(entry, 'directions has a share-link token');
  assert.equal(
    LAYER_STATE_REGISTRY.filter((row) => row.token === entry.token).length,
    1,
  );
  assert.ok(SPRITE_LAYER_ORDER.includes('directions'));
});

test('params: unknown modes are rejected, commands and arm state are normalized', () => {
  assert.deepEqual(normalizeDirectionsParams({ mode: 'FOOT' }), {
    mode: 'foot',
  });
  assert.equal(normalizeDirectionsParams({ mode: 'rocket' }), null);
  assert.deepEqual(normalizeDirectionsParams({ arm: 'a' }), { arm: 'a' });
  assert.deepEqual(normalizeDirectionsParams({ arm: 'zzz' }), { arm: null });
  assert.deepEqual(
    normalizeDirectionsParams({ swap: true, fly: true, clear: true }),
    { swap: true, fly: true, clear: true },
  );
  assert.deepEqual(normalizeDirectionsParams({ swap: 'yes' }), {});
  assert.deepEqual(normalizeDirectionsParams(), {});
  // The live module refuses an unknown mode without touching state.
  assert.equal(directionsLayer.setParams({ mode: 'rocket' }), false);
  assert.deepEqual(directionsLayer.getParams(), {
    mode: DEFAULT_DIRECTIONS_MODE,
  });
});

test('row chips: modes, arming, and command availability follow the state', () => {
  const { chips } = directionsRowControls(idle);
  const ids = chips.map((c) => c.id);
  assert.deepEqual(ids, [
    'mode-car',
    'mode-foot',
    'mode-bike',
    'mode-sea',
    'set-a',
    'set-b',
    'swap',
    'fly',
    'clear',
  ]);
  assert.equal(chips.find((c) => c.id === 'mode-car').active, true);
  assert.equal(chips.find((c) => c.id === 'set-a').label, 'SET A');
  assert.deepEqual(chips.find((c) => c.id === 'set-a').params, { arm: 'a' });
  assert.equal(chips.find((c) => c.id === 'fly').disabled, true);
  assert.equal(chips.find((c) => c.id === 'swap').disabled, true);
  assert.equal(chips.find((c) => c.id === 'clear').disabled, true);

  const armed = directionsRowControls({ ...idle, armed: 'a' }).chips;
  assert.equal(armed.find((c) => c.id === 'set-a').label, 'CLICK MAP');
  assert.deepEqual(
    armed.find((c) => c.id === 'set-a').params,
    { arm: null },
    'clicking again cancels',
  );

  const ready = directionsRowControls({
    ...idle,
    a: A,
    b: B,
    status: 'ready',
    route,
  }).chips;
  assert.equal(ready.find((c) => c.id === 'set-a').label, 'A ✓');
  assert.equal(ready.find((c) => c.id === 'fly').disabled, false);
  assert.equal(ready.find((c) => c.id === 'swap').disabled, false);
  assert.equal(ready.find((c) => c.id === 'clear').disabled, false);

  const routing = directionsRowControls({
    ...idle,
    a: A,
    b: B,
    status: 'routing',
  }).chips;
  assert.equal(routing.find((c) => c.id === 'fly').busy, true);
  for (const mode of Object.keys(DIRECTIONS_MODES))
    assert.ok(ids.includes(`mode-${mode}`));
});

test('stats guide the user, show progress, and summarize a route honestly', () => {
  assert.equal(directionsStats(idle).coverage, 'SET A, then click the globe');
  assert.equal(
    directionsStats({ ...idle, armed: 'b' }).coverage,
    'Click the globe to place B',
  );
  assert.equal(
    directionsStats({ ...idle, a: A }).coverage,
    'SET B, then click the globe',
  );
  const routing = directionsStats({ ...idle, a: A, b: B, status: 'routing' });
  assert.equal(routing.loading, true);
  const ready = directionsStats({
    ...idle,
    a: A,
    b: B,
    status: 'ready',
    route,
    lastUpdate: 5,
  });
  assert.equal(ready.count, 3);
  assert.equal(ready.coverage, '21 km · 25 min · Drive');
  assert.equal(
    ready.loadingLabel,
    '21 km · 25 min · Drive',
    'the summary is the row detail line',
  );
  assert.equal(ready.loading, undefined);
  assert.equal(
    directionsStats(idle).loadingLabel,
    'SET A, then click the globe',
  );
  const failed = directionsStats({
    ...idle,
    a: A,
    b: B,
    status: 'error',
    error: 'No route found between A and B',
  });
  assert.equal(failed.error, 'No route found between A and B');
  assert.equal(failed.count, 0);
  assert.equal(directionsLayer.getStats().count, 0);
});

test('step cards name the maneuver, its leg, and what comes next', () => {
  const copy = directionsStepCopy(route.steps, 1);
  assert.equal(copy.title, 'Turn right onto Kansakoulukatu');
  assert.deepEqual(copy.details, [
    'Step 2 of 3 · then 240 m · 39 s',
    'Then: Arrive at your destination',
  ]);
  const last = directionsStepCopy(route.steps, 2);
  assert.deepEqual(last.details, ['Step 3 of 3']);
  const position = Cesium.Cartesian3.fromDegrees(24.9349, 60.1683, 2);
  const card = createDirectionsStepOverlayEntry(1, position, copy);
  assert.equal(card.accent, DIRECTIONS_ROUTE_COLOR);
  assert.equal(card.id, 'directions-step-1');
  assert.equal(createDirectionsStepOverlayEntry(null, position, copy), null);
});

test('the proxy request carries both endpoints, the profile, and asks for steps', () => {
  const url = directionsRequestUrl('foot', A, B);
  assert.match(
    url,
    /^\/api\/route\?profile=foot&coords=24\.938400%2C60\.169900%3B24\.655900%2C60\.205500&steps=1$/,
  );
});

test('route payloads are validated; a failed route never becomes a straight line', () => {
  const ok = normalizeRoutePayload(
    {
      ok: true,
      distanceM: 21479,
      durationS: 1478,
      geometry: route.geometry,
      steps: route.steps,
    },
    'car',
  );
  assert.equal(ok.distanceM, 21479);
  assert.equal(ok.steps.length, 3);
  assert.equal(ok.mode, 'car');
  assert.equal(
    normalizeRoutePayload({ ok: false, error: 'no route found' }, 'car'),
    null,
  );
  assert.equal(
    normalizeRoutePayload({ ok: true, geometry: [[1, 1]] }, 'car'),
    null,
  );
  assert.equal(
    normalizeRoutePayload(
      {
        ok: true,
        geometry: [
          [999, 1],
          [1, 1],
        ],
      },
      'car',
    ),
    null,
  );
  const noSteps = normalizeRoutePayload(
    { ok: true, geometry: route.geometry },
    'bike',
  );
  assert.deepEqual(noSteps.steps, []);
  assert.equal(normalizeRoutePayload(null, 'car'), null);
});

test('maneuver dots skip departure and arrival and ride the shared ground floor', () => {
  assert.deepEqual(stepMarkerIndices(route.steps), [1]);
  assert.deepEqual(stepMarkerIndices([]), []);
  assert.deepEqual(stepMarkerIndices(null), []);
  assert.deepEqual(
    stepMarkerIndices([{}, {}, {}, {}, {}]),
    [1, 2, 3],
    'every decision between the endpoints gets a dot',
  );
  // Denver: ground is ~1.6 km ellipsoidal. A dot fixed near zero would sit a
  // kilometre and a half under the junction it marks.
  const lift = GROUND_FLOOR_LIFT_M;
  assert.equal(stepMarkerHeightM(1609.3, lift), 1609.3 + lift);
  assert.equal(stepMarkerHeightM(0, lift), lift);
  assert.equal(
    stepMarkerHeightM(null, lift),
    null,
    'a cold cell has no honest height',
  );
  assert.equal(stepMarkerHeightM(undefined, lift), null);
  assert.equal(stepMarkerHeightM(Number.NaN, lift), null);
});

test('the turn-by-turn list is ordered, labelled, and highlights the flown step', () => {
  assert.equal(directionsStepList({ route: null }), null);
  assert.equal(directionsStepList({ route: { steps: [] } }), null);
  const list = directionsStepList({
    route,
    selectedStep: null,
    flightStep: null,
  });
  assert.deepEqual(
    list.items.map((item) => item.ordinal),
    [1, 2, 3],
    'items are in route order',
  );
  assert.deepEqual(
    list.items.map((item) => item.text),
    route.steps.map((step) => step.instruction),
  );
  assert.deepEqual(
    list.items.map((item) => item.lead),
    ['8 m', '240 m', '0 m'],
  );
  assert.deepEqual(
    list.items.map((item) => item.params),
    [{ step: 0 }, { step: 1 }, { step: 2 }],
  );
  assert.equal(
    list.items.some((item) => item.active),
    false,
  );

  const selected = directionsStepList({
    route,
    selectedStep: 2,
    flightStep: null,
  });
  assert.deepEqual(
    selected.items.map((item) => item.active),
    [false, false, true],
  );
  assert.deepEqual(
    selected.items.map((item) => item.current),
    [false, false, false],
  );

  // A running FLY owns the highlight; the clicked step yields to it.
  const flying = directionsStepList({ route, selectedStep: 2, flightStep: 1 });
  assert.deepEqual(
    flying.items.map((item) => item.active),
    [false, true, false],
  );
  assert.deepEqual(
    flying.items.map((item) => item.current),
    [false, true, false],
  );

  const rowList = directionsRowControls({
    ...idle,
    a: A,
    b: B,
    status: 'ready',
    route,
  }).list;
  assert.equal(rowList.items.length, 3);
  assert.equal(rowList.ariaLabel, 'Turn-by-turn directions');
  assert.equal(directionsRowControls(idle).list, null, 'no route, no list');
});

test('the flown step follows distance travelled, never running off either end', () => {
  const steps = [{ distanceM: 100 }, { distanceM: 250 }, { distanceM: 0 }];
  assert.equal(stepIndexAtDistance(steps, 0), 0);
  assert.equal(stepIndexAtDistance(steps, 99), 0);
  assert.equal(stepIndexAtDistance(steps, 100), 1);
  assert.equal(stepIndexAtDistance(steps, 349), 1);
  assert.equal(stepIndexAtDistance(steps, 350), 2, 'the arrival step is last');
  assert.equal(stepIndexAtDistance(steps, 99999), 2);
  assert.equal(stepIndexAtDistance(steps, -5), 0);
  assert.equal(stepIndexAtDistance(steps, Number.NaN), 0);
  assert.equal(stepIndexAtDistance([], 10), null);
  assert.equal(stepIndexAtDistance(null, 10), null);
});

test('the FLY chip reports a flight in progress', () => {
  const flying = directionsRowControls({
    ...idle,
    a: A,
    b: B,
    status: 'ready',
    route,
    flightStep: 1,
  }).chips.find((chip) => chip.id === 'fly');
  assert.equal(flying.label, 'FLYING');
  assert.equal(flying.busy, true);
  assert.equal(flying.active, true);
});

test('a blocked pointer is reported instead of silently doing nothing', () => {
  const blocked = directionsStats({ ...idle, pointerBlocked: true });
  assert.match(blocked.error, /Another map tool is using clicks/);
  assert.equal(blocked.count, 0);
});

test('a step selection can be requested and cleared through params', () => {
  assert.deepEqual(normalizeDirectionsParams({ step: 3 }), { step: 3 });
  assert.deepEqual(normalizeDirectionsParams({ step: 0 }), { step: 0 });
  assert.deepEqual(normalizeDirectionsParams({ step: null }), { step: null });
  assert.deepEqual(normalizeDirectionsParams({ step: -1 }), { step: null });
  assert.deepEqual(normalizeDirectionsParams({ step: 'two' }), { step: null });
});

/**
 * Drive the live layer module with the overlay host stubbed out. No viewer is
 * needed for the arming paths: they are pure state plus the pointer claim.
 */
function ownershipFixture(t) {
  directionsLayer = createApplicationDirectionsLayer();
  directionsLayer._setOverlayHostForTest({
    setEntries() {},
    setVisible() {},
    clearSource() {},
  });
  resetPointerOwnership();
  t.after(() => {
    directionsLayer.setParams({ clear: true });
    directionsLayer._setOverlayHostForTest(null);
    resetPointerOwnership();
  });
}

test('placing an endpoint takes the pointer, and every exit path gives it back', (t) => {
  ownershipFixture(t);

  assert.equal(isPointerFree(), true);
  assert.equal(directionsLayer.setParams({ arm: 'a' }), true);
  assert.equal(
    pointerOwner(),
    DIRECTIONS_POINTER_OWNER,
    'arming claims the pointer',
  );
  assert.equal(
    directionsLayer.getRowControls().chips.find((c) => c.id === 'set-a').active,
    true,
  );

  // Re-arming for the other endpoint keeps the same claim; it never stacks.
  directionsLayer.setParams({ arm: 'b' });
  assert.equal(pointerOwner(), DIRECTIONS_POINTER_OWNER);

  // Cancel (clicking the armed chip again).
  directionsLayer.setParams({ arm: null });
  assert.equal(isPointerFree(), true, 'cancelling releases');
  assert.equal(
    directionsLayer.getRowControls().chips.find((c) => c.id === 'set-b').active,
    false,
  );

  // CLEAR while armed.
  directionsLayer.setParams({ arm: 'a' });
  directionsLayer.setParams({ clear: true });
  assert.equal(isPointerFree(), true, 'CLEAR releases');

  // disable() while armed.
  directionsLayer.setParams({ arm: 'a' });
  directionsLayer.disable(null);
  assert.equal(isPointerFree(), true, 'disabling the layer releases');

  // destroy() while armed, on a layer that was never enabled.
  directionsLayer.setParams({ arm: 'b' });
  assert.equal(pointerOwner(), DIRECTIONS_POINTER_OWNER);
  directionsLayer.destroy({ scene: { primitives: { remove() {} } } });
  assert.equal(isPointerFree(), true, 'destroy releases');
});

test('arming is refused, and said so, while another tool holds the pointer', (t) => {
  ownershipFixture(t);

  const releaseDraw = claimAs('draw');
  assert.equal(
    directionsLayer.setParams({ arm: 'a' }),
    true,
    'the write is accepted',
  );
  assert.equal(
    pointerOwner(),
    'draw',
    "the other tool's claim is never stolen",
  );
  const chips = directionsLayer.getRowControls().chips;
  assert.equal(
    chips.find((chip) => chip.id === 'set-a').active,
    false,
    'nothing is armed',
  );
  assert.match(
    directionsLayer.getStats().error,
    /Another map tool is using clicks/,
    'the row says why the click did nothing',
  );

  // Once the other tool lets go, arming works and the complaint clears.
  assert.equal(releaseDraw(), true);
  directionsLayer.setParams({ arm: 'a' });
  assert.equal(pointerOwner(), DIRECTIONS_POINTER_OWNER);
  assert.equal(directionsLayer.getStats().error, null);
});

test('a release names this layer, so it cannot free a successor tool claim', (t) => {
  ownershipFixture(t);

  directionsLayer.setParams({ arm: 'a' });
  assert.equal(pointerOwner(), DIRECTIONS_POINTER_OWNER);
  // A superseding teardown: the layer is disabled, then another tool claims.
  directionsLayer.disable(null);
  const releaseDraw = claimAs('draw');
  // A late second teardown of ours must not free the draw tool's claim.
  directionsLayer.disable(null);
  directionsLayer.setParams({ clear: true });
  assert.equal(pointerOwner(), 'draw');
  releaseDraw();
});

test('the pointer claim outlives the click that consumed it', async (t) => {
  ownershipFixture(t);
  // Every layer binds its own handler to the same canvas and they all run
  // inside ONE browser event. Releasing the claim the moment the endpoint is
  // placed hands the rest of that same click to every ambient handler
  // registered after this one — which is how placing A on top of a bikeshare
  // station also deselected the station.
  directionsLayer.setParams({ arm: 'a' });
  assert.equal(pointerOwner(), DIRECTIONS_POINTER_OWNER);

  assert.equal(
    directionsLayer.placeEndpoint('a', { lat: 37.7955, lon: -122.3937 }),
    true,
  );
  // Still inside the dispatch: an ambient handler that runs after this one
  // asks the same question the real ones ask, and must be told to stand down.
  assert.equal(
    isPointerFree(),
    false,
    'a later handler in the same click yields',
  );
  assert.equal(pointerOwner(), DIRECTIONS_POINTER_OWNER);
  // A microtask checkpoint runs BETWEEN two DOM listeners, so the release must
  // not be on one.
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(
    isPointerFree(),
    false,
    'a microtask is not the end of the click',
  );

  // The click is over; the pointer goes back.
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(
    isPointerFree(),
    true,
    'the claim is returned once the click ends',
  );

  // A bad placement request changes nothing.
  directionsLayer.setParams({ arm: 'b' });
  assert.equal(directionsLayer.placeEndpoint('c', { lat: 1, lon: 1 }), false);
  assert.equal(
    directionsLayer.placeEndpoint('b', { lat: Number.NaN, lon: 1 }),
    false,
  );
  assert.equal(pointerOwner(), DIRECTIONS_POINTER_OWNER, 'still armed');
});

test('a teardown during the pending release still frees the pointer at once', async (t) => {
  ownershipFixture(t);
  directionsLayer.setParams({ arm: 'a' });
  directionsLayer.placeEndpoint('a', { lat: 37.7955, lon: -122.3937 });
  assert.equal(isPointerFree(), false);
  // Disabling mid-click must not wait for a timer that may never be reached.
  directionsLayer.disable(null);
  assert.equal(isPointerFree(), true);
  // And the pending timer cannot then release someone else's later claim.
  const releaseDraw = claimAs('draw');
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(pointerOwner(), 'draw');
  releaseDraw();
});

test('a cold ground cell is re-read for as long as the terrain proxy can take', () => {
  // The proxy behind the shared floor can take tens of seconds on a cold
  // cache. Twelve quick retries covered four seconds of that.
  assert.ok(
    STEP_ANCHOR_DEADLINE_MS >= 60_000,
    `the budget must outlast a slow terrain round trip, is ${STEP_ANCHOR_DEADLINE_MS} ms`,
  );
  assert.equal(stepAnchorDelayMs(0, 0), STEP_ANCHOR_RETRY_MS);
  assert.equal(
    stepAnchorDelayMs(STEP_ANCHOR_FAST_ATTEMPTS - 1, 1000),
    STEP_ANCHOR_RETRY_MS,
  );
  assert.equal(
    stepAnchorDelayMs(STEP_ANCHOR_FAST_ATTEMPTS, 2000),
    STEP_ANCHOR_SLOW_RETRY_MS,
    'the re-reads back off once the quick ones have not settled it',
  );
  // The last wait never overshoots the budget, and past it there is no wait.
  assert.equal(stepAnchorDelayMs(50, STEP_ANCHOR_DEADLINE_MS - 100), 100);
  assert.equal(stepAnchorDelayMs(50, STEP_ANCHOR_DEADLINE_MS), null);
  assert.equal(stepAnchorDelayMs(50, STEP_ANCHOR_DEADLINE_MS + 1), null);
  assert.equal(stepAnchorDelayMs(0, Number.NaN), null);
});

test('a route cut off at the step cap says so instead of ending at a random turn', () => {
  const cut = { ...route, stepsTruncated: true };
  const list = directionsStepList({ route: cut });
  assert.equal(list.items.length, route.steps.length + 1);
  const last = list.items.at(-1);
  assert.match(last.text, /Only the first 3 turns of this route are shown/);
  assert.equal(last.disabled, true, 'the note is not an action');
  assert.equal(last.params, undefined, 'and carries no command');
  assert.match(
    directionsStats({ ...idle, a: A, b: B, status: 'ready', route: cut })
      .coverage,
    /first 3 turns$/,
  );
  // A complete route says nothing extra.
  assert.equal(directionsStepList({ route }).items.length, route.steps.length);
  assert.equal(
    normalizeRoutePayload(
      { ok: true, geometry: route.geometry, steps: route.steps },
      'car',
    ).stepsTruncated,
    false,
  );
  assert.equal(
    normalizeRoutePayload(
      {
        ok: true,
        geometry: route.geometry,
        steps: route.steps,
        stepsTruncated: true,
      },
      'car',
    ).stepsTruncated,
    true,
  );
});

test('re-arming reuses the lease this layer already holds', (t) => {
  ownershipFixture(t);
  // A claim never stacks, not even under the same name — two live instances of
  // one tool are two owners. So arming B after arming A must not ask for a
  // second claim it cannot get.
  assert.equal(directionsLayer.setParams({ arm: 'a' }), true);
  assert.equal(pointerOwner(), DIRECTIONS_POINTER_OWNER);
  assert.equal(directionsLayer.setParams({ arm: 'b' }), true);
  assert.equal(
    pointerOwner(),
    DIRECTIONS_POINTER_OWNER,
    'the layer still holds the pointer after re-arming',
  );
  const chips = directionsLayer.getRowControls().chips;
  assert.equal(chips.find((chip) => chip.id === 'set-b').active, true);
  assert.equal(
    directionsLayer.getStats().error,
    null,
    'and nothing is refused',
  );
});

test('a stale lease cannot free the claim a later arming holds', async (t) => {
  ownershipFixture(t);
  directionsLayer.setParams({ arm: 'a' });
  directionsLayer.placeEndpoint('a', { lat: 37.7955, lon: -122.3937 });
  // The release for that click is pending. Arming again before it fires must
  // keep the pointer, not lose it to the timer a moment later.
  directionsLayer.setParams({ arm: 'b' });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(
    pointerOwner(),
    DIRECTIONS_POINTER_OWNER,
    'the pending release must not disarm the new arming',
  );
});

test('a refused arming names the tool holding the pointer and how to leave it', () => {
  // Owner field test: he pressed SET A while Draw was open, nothing happened,
  // and he concluded Directions was broken. Silence was the bug.
  assert.equal(
    pointerBlockedMessage('draw', 'a'),
    'Draw is active — press Escape twice to leave Draw, then set A',
  );
  assert.equal(
    pointerBlockedMessage('draw', 'b'),
    'Draw is active — press Escape twice to leave Draw, then set B',
  );
  // A tool nobody has written an exit for still gets a usable sentence.
  assert.equal(
    pointerBlockedMessage('measure', 'a'),
    'measure is active — turn measure off, then set A',
  );
  // Nothing to say when nothing is in the way, or when we are the holder.
  assert.equal(pointerBlockedMessage(null, 'a'), null);
  assert.equal(pointerBlockedMessage('', 'a'), null);
  assert.equal(pointerBlockedMessage('   ', 'a'), null);
  assert.equal(pointerBlockedMessage(DIRECTIONS_POINTER_OWNER, 'a'), null);
  // Every exit the table knows names its tool and says how to leave it.
  for (const [id, exit] of Object.entries(POINTER_TOOL_EXITS)) {
    assert.match(
      pointerBlockedMessage(id, 'a'),
      new RegExp(`^${exit.name} is active — `),
    );
    assert.match(pointerBlockedMessage(id, 'a'), /, then set A$/);
  }
});

test('the refusal reaches the app toast, and a successful arming says nothing', (t) => {
  ownershipFixture(t);
  const toasts = [];
  directionsLayer.attachShellServices({
    showToast: (text) => toasts.push(text),
  });
  t.after(() => directionsLayer.attachShellServices(null));

  const releaseDraw = claimAs('draw');
  directionsLayer.setParams({ arm: 'a' });
  assert.deepEqual(toasts, [
    'Draw is active — press Escape twice to leave Draw, then set A',
  ]);
  // The row status the operator can also read is still set.
  assert.match(
    directionsLayer.getStats().error,
    /Another map tool is using clicks/,
  );

  // Once Draw lets go, arming works and says nothing at all.
  releaseDraw();
  directionsLayer.setParams({ arm: 'a' });
  assert.equal(toasts.length, 1, 'a successful arming is silent');
  assert.equal(pointerOwner(), DIRECTIONS_POINTER_OWNER);
});

test('maritime request construction: builds correct POST URL and body payload', () => {
  const url = directionsRequestUrl('sea', A, B);
  assert.equal(url, MARITIME_ROUTING_ENDPOINT);
  assert.equal(url, '/api/v1/routing/calculate');

  const payload = buildMaritimeRoutingPayload(A, B);
  assert.deepEqual(payload, {
    origin: { coordinates: [24.9384, 60.1699] },
    destination: { coordinates: [24.6559, 60.2055] },
    canal_policy: 'allow',
    routing_mode: 'marnet',
    supports_segments: true,
  });

  const custom = buildMaritimeRoutingPayload(A, B, {
    canal_policy: 'avoid_suez',
    routing_mode: 'prototype',
  });
  assert.equal(custom.canal_policy, 'avoid_suez');
  assert.equal(custom.routing_mode, 'prototype');
  assert.equal(custom.supports_segments, true);
});

test('maritime payload normalization: single continuous route and transpacific disjoint segments', () => {
  // 1. Continuous route (e.g. Rotterdam -> Singapore)
  const continuousPayload = {
    status: 'calculated',
    route_method: 'marnet_50km_generalized_graph',
    routing_mode: 'marnet',
    summary: {
      total_distance_nm: 8399.3,
      distance_nm: 8399.3,
      total_distance_km: 15555.5,
      canals_transited: ['suez', 'malacca'],
      canal_policy_applied: 'allow',
      origin_snap_distance_nm: 2.1,
      destination_snap_distance_nm: 1.5,
    },
    geometry: [
      [4.4, 51.9],
      [103.8, 1.3],
    ],
    geometry_segments: [
      [
        [4.4, 51.9],
        [103.8, 1.3],
      ],
    ],
    waypoints: [
      {
        node_id: 'NLRTM',
        name: 'Rotterdam',
        longitude: 4.4,
        latitude: 51.9,
        waypoint_type: 'fairway_gate',
        cumulative_distance_nm: 0.0,
      },
      {
        node_id: 'SGSIN',
        name: 'Singapore',
        longitude: 103.8,
        latitude: 1.3,
        waypoint_type: 'fairway_gate',
        cumulative_distance_nm: 8399.3,
      },
    ],
  };

  const normContinuous = normalizeRoutePayload(continuousPayload, 'sea');
  assert.ok(normContinuous);
  assert.equal(normContinuous.isMaritime, true);
  assert.equal(normContinuous.mode, 'sea');
  assert.equal(normContinuous.distanceNm, 8399.3);
  assert.equal(normContinuous.geometrySegments.length, 1);
  assert.deepEqual(normContinuous.canalsTransited, ['suez', 'malacca']);
  assert.equal(normContinuous.originSnapDistanceNm, 2.1);
  assert.equal(normContinuous.steps.length, 2);

  // 2. Transpacific disjoint route (e.g. Seattle -> Yokohama across 180 deg)
  const transpacificPayload = {
    status: 'calculated',
    route_method: 'marnet_50km_generalized_graph',
    routing_mode: 'marnet',
    summary: {
      total_distance_nm: 4512.3,
      distance_nm: 4512.3,
      total_distance_km: 8356.8,
      canals_transited: [],
      canal_policy_applied: 'allow',
    },
    geometry: [], // Flat continuous geometry suppressed by backend for antimeridian
    geometry_segments: [
      [
        [-122.3, 47.6],
        [-180.0, 50.0],
      ],
      [
        [180.0, 50.0],
        [139.6, 35.4],
      ],
    ],
    waypoints: [
      {
        node_id: 'USSEA',
        name: 'Seattle',
        longitude: -122.3,
        latitude: 47.6,
        cumulative_distance_nm: 0.0,
      },
      {
        node_id: 'JPYOK',
        name: 'Yokohama',
        longitude: 139.6,
        latitude: 35.4,
        cumulative_distance_nm: 4512.3,
      },
    ],
  };

  const normPacific = normalizeRoutePayload(transpacificPayload, 'sea');
  assert.ok(normPacific);
  assert.equal(normPacific.isMaritime, true);
  assert.deepEqual(normPacific.geometry, []);
  assert.equal(normPacific.geometrySegments.length, 2);
  assert.equal(normPacific.geometrySegments[0][1][0], -180.0);
  assert.equal(normPacific.geometrySegments[1][0][0], 180.0);
  assert.equal(normPacific.distanceNm, 4512.3);
});

test('maritime failure handling: non-calculated statuses and malformed payloads return null', () => {
  // Domain failure statuses return null
  assert.equal(
    normalizeRoutePayload(
      {
        status: 'unsupported_route',
        error_message: 'Route traverses polar waters (|latitude| >= 66.5)',
        geometry: [],
        geometry_segments: [],
      },
      'sea',
    ),
    null,
  );

  assert.equal(
    normalizeRoutePayload(
      {
        status: 'invalid_input',
        error_message: 'Coordinates out of range',
        geometry: [],
        geometry_segments: [],
      },
      'sea',
    ),
    null,
  );

  assert.equal(
    normalizeRoutePayload(
      {
        status: 'configuration_error',
        error_message: 'MARNET cache not found',
        geometry: [],
        geometry_segments: [],
      },
      'sea',
    ),
    null,
  );

  // Missing or invalid geometry segments return null
  assert.equal(
    normalizeRoutePayload(
      {
        status: 'calculated',
        geometry: [],
        geometry_segments: [],
      },
      'sea',
    ),
    null,
  );

  assert.equal(normalizeRoutePayload(null, 'sea'), null);
  assert.equal(normalizeRoutePayload({}, 'sea'), null);
});

test('maritime stats summary: formats nautical miles, transited canals, and snap distance', () => {
  const maritimeRoute = {
    isMaritime: true,
    mode: 'sea',
    distanceNm: 8399.3,
    distanceKm: 15555.5,
    canalsTransited: ['suez', 'malacca'],
    originSnapDistanceNm: 2.1,
    destinationSnapDistanceNm: 1.5,
    steps: [{ lat: 1, lon: 1 }, { lat: 2, lon: 2 }],
  };

  const stats = directionsStats({
    ...idle,
    mode: 'sea',
    a: A,
    b: B,
    status: 'ready',
    route: maritimeRoute,
  });

  assert.match(stats.coverage, /8,399\.3 NM/);
  assert.match(stats.coverage, /15,556 km/);
  assert.match(stats.coverage, /Sea \(MARNET\)/);
  assert.match(stats.coverage, /Suez, Malacca/);
  assert.match(stats.coverage, /snap ~3\.6 NM/);
  assert.equal(stats.count, 2);

  // Failure stats
  const failedStats = directionsStats({
    ...idle,
    mode: 'sea',
    a: A,
    b: B,
    status: 'error',
    error: 'Route traverses polar waters (|latitude| >= 66.5)',
  });
  assert.equal(failedStats.error, 'Route traverses polar waters (|latitude| >= 66.5)');
  assert.equal(failedStats.status, 'empty');
});

test('entity lifecycle: disjoint maritime segments create distinct Cesium entities and clear cleanly', () => {
  const addedEntities = new Map();
  const removedEntities = new Set();

  const mockViewer = {
    isDestroyed: () => false,
    entities: {
      add: (ent) => {
        addedEntities.set(ent.id, ent);
        return ent;
      },
      remove: (ent) => {
        const id = ent.id || ent;
        removedEntities.add(id);
        if (ent.id) addedEntities.delete(ent.id);
        return true;
      },
    },
    scene: {
      primitives: {
        add: () => {},
        remove: () => {},
        raiseToTop: () => {},
      },
    },
  };

  const testServices = {
    ...directionsServices,
    annotations: {
      ensureFlowFabricRegistered: () => {},
      FlowMaterialProperty: class MockFlowMaterialProperty {},
    },
  };
  const layer = createDirectionsLayer({ services: testServices });
  layer.init(mockViewer);

  const multiSegmentRoute = {
    isMaritime: true,
    mode: 'sea',
    geometrySegments: [
      [
        [139.7, 35.6],
        [179.9, 36.0],
      ],
      [
        [-179.9, 36.0],
        [-122.4, 37.8],
      ],
    ],
    steps: [],
  };

  layer._drawRouteForTest(multiSegmentRoute);

  const entities = layer._getRouteEntitiesForTest();
  assert.equal(entities.length, 2);
  assert.equal(entities[0].id, 'directions:route:segment_0');
  assert.equal(entities[1].id, 'directions:route:segment_1');
  assert.equal(addedEntities.size, 2);
  assert.ok(addedEntities.has('directions:route:segment_0'));
  assert.ok(addedEntities.has('directions:route:segment_1'));

  // Continuous route draws single entity 'directions:route'
  const singleSegmentRoute = {
    isMaritime: true,
    mode: 'sea',
    geometrySegments: [
      [
        [4.4, 51.9],
        [3.0, 51.5],
      ],
    ],
    steps: [],
  };

  layer._drawRouteForTest(singleSegmentRoute);
  const singleEntities = layer._getRouteEntitiesForTest();
  assert.equal(singleEntities.length, 1);
  assert.equal(singleEntities[0].id, 'directions:route');
  assert.equal(addedEntities.size, 1);
  assert.ok(addedEntities.has('directions:route'));
  // Previous segments should have been removed
  assert.ok(removedEntities.has('directions:route:segment_0'));
  assert.ok(removedEntities.has('directions:route:segment_1'));

  // Clear graphics cleans up everything
  layer._clearRouteGraphicsForTest();

  assert.equal(layer._getRouteEntitiesForTest().length, 0);
  assert.equal(addedEntities.size, 0);
  assert.ok(removedEntities.has('directions:route'));

  layer.destroy(mockViewer);
});

test('antimeridian camera flight: avoids continuous flight and does not pass false connecting leg to flyRoute', () => {
  const flyRouteCalls = [];
  const flyToCalls = [];

  const mockViewer = {
    isDestroyed: () => false,
    camera: {
      flyTo: (opts) => {
        flyToCalls.push(opts);
      },
    },
    entities: {
      add: (ent) => ent,
      remove: () => true,
    },
    scene: {
      primitives: {
        add: () => {},
        remove: () => {},
        raiseToTop: () => {},
      },
    },
  };

  const testServices = {
    ...directionsServices,
    camera: {
      ...directionsServices.camera,
      initCameraVerbs: () => {},
      interruptCameraMotionIfActive: () => ({ wasActive: false }),
      flyRoute: (...args) => {
        flyRouteCalls.push(args);
        return { ok: true, motionId: 1 };
      },
    },
    annotations: {
      ensureFlowFabricRegistered: () => {},
      FlowMaterialProperty: class MockFlowMaterialProperty {},
    },
  };

  const layer = createDirectionsLayer({ services: testServices });
  layer.init(mockViewer);

  // Antimeridian route with 2 disjoint segments across 180°
  const antimeridianRoute = {
    isMaritime: true,
    mode: 'sea',
    geometry: [],
    geometrySegments: [
      [
        [-122.3, 47.6],
        [-180.0, 50.0],
      ],
      [
        [180.0, 50.0],
        [139.6, 35.4],
      ],
    ],
    steps: [
      { lat: 47.6, lon: -122.3, instruction: 'Seattle' },
      { lat: 35.4, lon: 139.6, instruction: 'Yokohama' },
    ],
  };

  layer._setRouteForTest(antimeridianRoute);
  const flyResult = layer._flyCurrentRouteForTest();

  // Must refuse continuous flight across disjoint segments
  assert.equal(flyResult, false);

  // flyRoute must NEVER be called with a flattened path joining [-180, 50] to [180, 50]
  assert.equal(flyRouteCalls.length, 0);

  // Safe camera strategy frames the route destination endpoint
  assert.equal(flyToCalls.length, 1);
  assert.ok(flyToCalls[0].destination);

  // Verify that single-segment routes continue to use flyRoute normally (land routing preservation)
  const singleSegmentRoute = {
    isMaritime: false,
    mode: 'car',
    geometry: [
      [24.9384, 60.1699],
      [24.6559, 60.2055],
    ],
    geometrySegments: [
      [
        [24.9384, 60.1699],
        [24.6559, 60.2055],
      ],
    ],
    steps: [
      { lat: 60.1699, lon: 24.9384 },
      { lat: 60.2055, lon: 24.6559 },
    ],
  };

  layer._setRouteForTest(singleSegmentRoute);
  const singleResult = layer._flyCurrentRouteForTest();

  assert.equal(singleResult, true);
  assert.equal(flyRouteCalls.length, 1);
  // The single segment path is passed directly
  const flownPath = flyRouteCalls[0][0][0].path;
  assert.equal(flownPath.length, 2);
  assert.equal(flownPath[0].lon, 24.9384);
  assert.equal(flownPath[1].lon, 24.6559);

  layer.destroy(mockViewer);
});

test('antimeridian geometry sanitization: malformed backend segments with seam duplicates are sanitized and do not crash Cesium', () => {
  const addedEntities = new Map();

  const mockViewer = {
    isDestroyed: () => false,
    entities: {
      add: (ent) => {
        addedEntities.set(ent.id, ent);
        return ent;
      },
      remove: (ent) => {
        const id = ent.id || ent;
        if (ent.id) addedEntities.delete(ent.id);
        return true;
      },
    },
    scene: {
      primitives: {
        add: () => {},
        remove: () => {},
        raiseToTop: () => {},
      },
    },
  };

  const testServices = {
    ...directionsServices,
    annotations: {
      ensureFlowFabricRegistered: () => {},
      FlowMaterialProperty: class MockFlowMaterialProperty {},
    },
  };
  const layer = createDirectionsLayer({ services: testServices });
  layer.init(mockViewer);

  // Payload mimicking the Seattle -> Yokohama backend orientation defect:
  // - Seg 0 has trailing [180.0, 40.0] after [-180.0, 40.0] (360° jump across seam)
  // - Seg 1 has leading [-180.0, 40.0] before [180.0, 40.0] (360° jump across seam)
  const malformedPayload = {
    status: 'calculated',
    summary: { total_distance_nm: 4450.0 },
    geometry_segments: [
      [
        [-122.3, 47.6],
        [-173.0, 47.2],
        [-180.0, 40.0],
        [180.0, 40.0],
      ],
      [
        [-180.0, 40.0],
        [180.0, 40.0],
        [170.0, 40.0],
        [139.6, 35.4],
      ],
    ],
    waypoints: [
      { name: 'Seattle', longitude: -122.3, latitude: 47.6 },
      { name: 'Yokohama', longitude: 139.6, latitude: 35.4 },
    ],
  };

  const norm = normalizeRoutePayload(malformedPayload, 'sea');
  assert.ok(norm);
  assert.equal(norm.geometrySegments.length, 2);

  // Assert Seg 0 is sanitized: ends cleanly at -180.0 and does NOT contain +180.0
  const seg0 = norm.geometrySegments[0];
  assert.equal(seg0[seg0.length - 1][0], -180.0);
  assert.ok(seg0.every(([lon]) => lon <= 0));

  // Assert Seg 1 is sanitized: starts cleanly at +180.0 and does NOT contain -180.0
  const seg1 = norm.geometrySegments[1];
  assert.equal(seg1[0][0], 180.0);
  assert.ok(seg1.every(([lon]) => lon >= 0));

  // Assert no segment contains intra-segment antimeridian discontinuity (|lon[i+1] - lon[i]| > 180)
  for (const seg of norm.geometrySegments) {
    for (let i = 0; i < seg.length - 1; i++) {
      const dLon = Math.abs(seg[i + 1][0] - seg[i][0]);
      assert.ok(dLon <= 180.0, `Discontinuity detected: ${seg[i]} -> ${seg[i + 1]}`);
    }
  }

  // Draw sanitized route on Cesium viewer
  layer._drawRouteForTest(norm);

  const entities = layer._getRouteEntitiesForTest();
  assert.equal(entities.length, 2);
  assert.equal(entities[0].id, 'directions:route:segment_0');
  assert.equal(entities[1].id, 'directions:route:segment_1');

  // Verify positions of both entities are valid Cartesian3 without 0-distance seam jumps
  const pos0 = entities[0].polyline.positions;
  const pos1 = entities[1].polyline.positions;
  assert.equal(pos0.length, 3); // [-122.3, 47.6], [-173.0, 47.2], [-180.0, 40.0]
  assert.equal(pos1.length, 3); // [180.0, 40.0], [170.0, 40.0], [139.6, 35.4]

  layer.destroy(mockViewer);
});

test('maritime stats: freight intelligence formatting for supported, unmonitored, and loading states', () => {
  const baseMaritimeRoute = {
    isMaritime: true,
    distanceNm: 4520.0,
    distanceKm: 8371.0,
    canalsTransited: [],
    originSnapDistanceNm: 0,
    destinationSnapDistanceNm: 0,
    steps: [{ instruction: 'Seattle' }, { instruction: 'Yokohama' }],
    routeMethod: 'Pelagos MARNET',
  };

  // Case 1: Loading state
  const loadingStats = directionsStats({
    mode: 'sea',
    route: baseMaritimeRoute,
    status: 'ready',
    freightStatus: 'loading',
    freightIntelligence: null,
  });
  assert.ok(loadingStats.loadingLabel.includes('Freight loading'));
  assert.ok(loadingStats.loadingLabel.includes('4,520 NM'));

  // Case 2: Monitored corridor with observed rate
  const monitoredStats = directionsStats({
    mode: 'sea',
    route: baseMaritimeRoute,
    status: 'ready',
    freightStatus: 'ready',
    freightIntelligence: {
      status: 'monitored',
      corridor: { route_code: 'PNW-JPN-BULK', route_name: 'U.S. Pacific Northwest to Japan Bulk Grain' },
      latest_observation: { rate_value: 37.31, unit: 'USD/MT' },
    },
  });
  assert.ok(monitoredStats.loadingLabel.includes('Grain: $37.31/MT'));
  assert.ok(monitoredStats.loadingLabel.includes('4,520 NM'));

  // Case 3: Unmonitored corridor (Rotterdam -> Singapore)
  const unmonitoredStats = directionsStats({
    mode: 'sea',
    route: {
      ...baseMaritimeRoute,
      distanceNm: 8288.5,
      distanceKm: 15350.0,
      canalsTransited: ['suez'],
    },
    status: 'ready',
    freightStatus: 'ready',
    freightIntelligence: {
      status: 'unmonitored',
      message: 'Unmonitored corridor',
    },
  });
  assert.ok(unmonitoredStats.loadingLabel.includes('Freight: Unmonitored'));
  assert.ok(unmonitoredStats.loadingLabel.includes('Suez'));
  assert.ok(unmonitoredStats.loadingLabel.includes('8,288.5 NM'));
});

test('maritime step list: prepends structured freight intelligence cards for supported and unmonitored routes', () => {
  const maritimeRoute = {
    isMaritime: true,
    steps: [
      { instruction: 'Departure from Seattle', distanceM: 0, lat: 48.5, lon: -124.75 },
      { instruction: 'MARNET Mid-Pacific Fairway', distanceM: 4000000, lat: 45.0, lon: -175.0 },
      { instruction: 'Arrival at Yokohama', distanceM: 4000000, lat: 35.15, lon: 139.73 },
    ],
  };

  // 1. Monitored corridor (Seattle -> Yokohama)
  const monitoredResult = directionsStepList({
    route: maritimeRoute,
    freightStatus: 'ready',
    freightIntelligence: {
      status: 'monitored',
      corridor: {
        route_code: 'PNW-JPN-BULK',
        route_name: 'U.S. Pacific Northwest to Japan Bulk Grain',
        vessel_class: 'Panamax / Supramax',
        cargo_type: 'Agricultural Bulk Grain',
      },
      latest_observation: {
        rate_value: 37.31,
        unit: 'USD/MT',
        observation_date: '2026-09-01',
        source_name: 'USDA AMS',
      },
      baseline_forecast: {
        predicted_value: 37.31,
        unit: 'USD/MT',
        target_date: '2026-10-01',
        model_type: 'persistence',
      },
      vessel_availability: {
        status: 'unavailable',
        reason: 'no live AIS feed',
      },
    },
  });

  assert.ok(monitoredResult);
  const items = monitoredResult.items;
  // 5 freight cards + 3 waypoints = 8 items
  assert.equal(items.length, 8);
  assert.equal(items[0].id, 'freight-corridor');
  assert.equal(items[0].lead, 'CORRIDOR');
  assert.ok(items[0].text.includes('PNW-JPN-BULK'));
  assert.ok(items[0].text.includes('Panamax / Supramax'));
  assert.equal(items[0].disabled, true);

  assert.equal(items[1].id, 'freight-rate');
  assert.equal(items[1].lead, 'OBSERVED');
  assert.ok(items[1].text.includes('$37.31 USD/MT'));
  assert.ok(items[1].text.includes('2026-09-01'));
  assert.ok(items[1].text.includes('USDA AMS'));

  assert.equal(items[2].id, 'freight-forecast');
  assert.equal(items[2].lead, 'FORECAST');
  assert.ok(items[2].text.includes('$37.31 USD/MT'));
  assert.ok(items[2].text.includes('2026-10-01'));
  assert.ok(items[2].text.includes('persistence'));

  assert.equal(items[3].id, 'freight-vessels');
  assert.equal(items[3].lead, 'FLEET');
  assert.ok(items[3].text.includes('Unavailable'));

  assert.equal(items[4].id, 'freight-scope');
  assert.equal(items[4].lead, 'SCOPE');
  assert.ok(items[4].text.includes('bulk grain only'));

  // Navigational waypoints follow
  assert.equal(items[5].id, 'step-0');
  assert.equal(items[5].text, 'Departure from Seattle');
  assert.equal(items[7].id, 'step-2');
  assert.equal(items[7].text, 'Arrival at Yokohama');

  // 2. Unmonitored corridor (Rotterdam -> Singapore)
  const unmonitoredResult = directionsStepList({
    route: maritimeRoute,
    freightStatus: 'ready',
    freightIntelligence: {
      status: 'unmonitored',
      message: 'Trade lane Rotterdam -> Singapore is unmonitored in Pelagos.',
      vessel_availability: {
        status: 'unavailable',
        reason: 'no live AIS feed',
      },
    },
  });

  assert.ok(unmonitoredResult);
  const unmonItems = unmonitoredResult.items;
  // 2 freight cards + 3 waypoints = 5 items
  assert.equal(unmonItems.length, 5);
  assert.equal(unmonItems[0].id, 'freight-unmonitored');
  assert.equal(unmonItems[0].lead, 'FREIGHT');
  assert.ok(unmonItems[0].text.includes('unmonitored'));
  assert.equal(unmonItems[1].id, 'freight-vessels');
  assert.equal(unmonItems[1].lead, 'FLEET');
  assert.ok(unmonItems[1].text.includes('Unavailable'));

  // 3. Land routing (car, foot, bike) does NOT add freight cards
  const landRoute = {
    isMaritime: false,
    steps: [
      { instruction: 'Start on Market St', distanceM: 100 },
      { instruction: 'Arrive at destination', distanceM: 0 },
    ],
  };
  const landResult = directionsStepList({
    route: landRoute,
    freightStatus: 'ready',
    freightIntelligence: { status: 'monitored' },
  });
  assert.equal(landResult.items.length, 2);
  assert.equal(landResult.items[0].id, 'step-0');
});

test('layer freight lifecycle: test seam, row notifications, and clearance', () => {
  const mockViewer = {
    isDestroyed: () => false,
    entities: { add: () => ({ id: 'mock' }), remove: () => true },
    scene: {
      primitives: {
        add: () => {},
        remove: () => {},
        raiseToTop: () => {},
      },
      canvas: {},
    },
  };

  const layer = createDirectionsLayer({ services: directionsServices });
  layer.init(mockViewer);

  let notified = 0;
  layer.setRowControlsListener(() => {
    notified += 1;
  });

  const testIntel = {
    status: 'monitored',
    corridor: { route_code: 'PNW-JPN-BULK' },
    latest_observation: { rate_value: 37.31, unit: 'USD/MT' },
  };

  // Set freight intelligence via test seam
  layer._setFreightIntelligenceForTest(testIntel, 'ready', null);
  assert.equal(notified, 1);

  const state1 = layer._getFreightIntelligenceForTest();
  assert.equal(state1.status, 'ready');
  assert.equal(state1.intelligence.corridor.route_code, 'PNW-JPN-BULK');

  // Clear graphics cleans up freight intelligence
  layer._clearRouteGraphicsForTest();
  const state2 = layer._getFreightIntelligenceForTest();
  assert.equal(state2.status, 'idle');
  assert.equal(state2.intelligence, null);
  assert.equal(state2.error, null);

  // Set again, then test clearAll()
  layer._setFreightIntelligenceForTest(testIntel, 'ready', null);
  assert.equal(layer._getFreightIntelligenceForTest().status, 'ready');

  layer.setParams({ clear: true });
  const state3 = layer._getFreightIntelligenceForTest();
  assert.equal(state3.status, 'idle');
  assert.equal(state3.intelligence, null);

  layer.destroy(mockViewer);
});
