# Independent Code Review — Pelagos AIS Vessel Details

## Overall Verdict
**Approve with non-blocking suggestions.**
The implementation accurately delivers the scoped functionality with robust fallbacks, truth-in-telemetry guards, and proper commercial context disclaimers.

## Positive Observations
- **Data Integrity & Fallbacks:** `normalizeLiveVesselItem` correctly identifies missing data and avoids hallucinating names or locations. The handling of epoch-based caching vs string-based timestamps is defensively written.
- **Cesium Selection Lifecycle:** The bidirectional selection synchronization is clean, and the `PelagosPanelController` safely cleans up event listeners in `destroy()`.
- **Robustness in Tests:** Tests correctly specify expected behavior over implementation details (e.g., verifying `lastPositionUtc` is presented consistently without emitting `undefined` literals).

## Medium- and Low-Priority Findings

1. **Equirectangular Projection Scalability (src/ui/pelagosVessels.js, lines ~210-250)**
   - **Scenario:** `calculateDistanceToRouteNm` relies on `cosMidLat` to implement an equirectangular approximation for distances to the route corridor.
   - **Impact:** While acceptable for 50 NM corridors, the distortion grows at extremely high latitudes.
   - **Correction/Suggestion:** No immediate change is required given the accuracy constraints, but consider replacing this with a rigorous spherical distance algorithm (like Vincenty or Haversine cross-track) if the corridor buffer is expanded significantly in the future.

2. **Deduplication Sort Determinism (src/ui/pelagosVessels.js, lines ~315-322)**
   - **Scenario:** When two vessels have identical `distanceToRouteNm` or `displayName`, the sort logic relies on `localeCompare`.
   - **Impact:** While largely deterministic, falling back to MMSI or epoch sorting could ensure identical orderings on subsequent renders.
   - **Correction/Suggestion:** Append a final fallback in `allVessels.sort`: `return a.mmsi.localeCompare(b.mmsi);`.

3. **Event Listener Removal Context (src/ui/pelagosPanel.js, lines ~1529-1537)**
   - **Scenario:** The `destroy()` method iteratively removes listeners appended to `window`.
   - **Impact:** The implementation is safe due to the `try/catch` and existence checks, but using `AbortController` signals for `window.addEventListener` might provide an even cleaner tear-down.

## Missing Tests and Manual Browser Verification
- **Test Coverage:** All major paths (MMSI deduplication, missing fields, antimeridian wrapping) are explicitly tested. No brittle mocks were found, and regressions are covered.
- **Browser GUI Check:** Need to manually verify the UI layout dynamically responds to vessel selection when interacting with the globe, ensuring the responsive grid classes align gracefully inside Cesium widget containers.

## Commercial Interpretation Check
Confirmed: The implementation explicitly displays disclaimers (`pelagos-fleet-desc` and `vessel-commercial-callout`) preventing the UI from establishing open-tonnage statuses falsely.
