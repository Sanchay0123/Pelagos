---
title: Pelagos Operational Maritime Routing Readiness Assessment
document_id: ROUT-001
type: operational_routing
status: drafting
version: 1.1.0
created: 2026-10-17
updated: 2026-10-17
tags:
  - routing
  - maritime
  - operational-routing
  - specification
  - readiness
  - navigation
---

# Pelagos Operational Maritime Routing Readiness Assessment

## 1. Purpose

This document establishes the operational readiness assessment, data strategy, architectural boundaries, and minimum viable implementation specification for the Pelagos Operational Maritime Routing (OMR) subsystem.

It defines how the platform will calculate plausible sea-lane voyage routes between commercial ports, derive nautical distances, evaluate canal and chokepoint constraints, and provide voyage duration estimates while maintaining a strict operational distinction between an **analytical commercial route** and a **certified navigation-grade safety route**.

This specification fulfills Milestone D9.1 and directly traces to [REQ-FR-OMR-001](../requirements/functional-requirements.md), [REQ-FR-OMR-002](../requirements/functional-requirements.md), and [REQ-FR-OMR-003](../requirements/functional-requirements.md).

---

## 2. Scope

### In Scope
- Assessment of existing backend models, storage, endpoints, frontend CesiumJS rendering pipelines, and Python geospatial dependencies.
- Precise identification, licensing analysis, and evaluation of candidate maritime sea-lane graph datasets, port catalogs, coastline barrier masks, and canal constraint models.
- Rigorous distinction between administrative port centroids, verified fairway entrance coordinates, and berth terminals.
- Specification of a bounded Minimum Viable Product (MVP) routing engine (`pelagos.routing`).
- Formal API contracts for input coordinates/identifiers and output route geometry, nautical distance, duration estimates, waypoints, and provenance.
- Explicit detection and handling of land crossings, disconnected networks, and unsupported corridors without falling back to naive straight lines.
- Deterministic verification and validation protocol across canonical benchmark corridors, distinguishing macro corridor benchmarks from port-to-port measurements.

### Out of Scope & Explicit Non-Goals
- **Non-Navigation-Grade System:** Pelagos is explicitly NOT an Electronic Chart Display and Information System (ECDIS), an integrated bridge navigation system, or an autopilot actuation system.
- **Coastline Intersections Do Not Establish Navigational Safety:** Ray-casting checks against land boundaries confirm only gross physical separation from dry land; they do not establish safe under-keel clearance, channel depth, or regulatory compliance.
- **No Dynamic Meteorological Routing in MVP:** Complex wave-spectrum drift, surface currents, and cyclone avoidance ([REQ-FR-OMR-004](../requirements/functional-requirements.md)) are deferred to subsequent milestones.
- **No Ingestion of External Datasets during D9.1:** This task produces an architectural readiness assessment and specification only; no external datasets are ingested or runtime engines implemented during D9.1.
- **Preservation of Existing Stores:** Existing operational database (`pelagos.db`), backups, Parquet analytical partitions (`parquet_data/`), and forecasting models (`ML-001`) remain completely untouched.

---

## 3. Background and Context

### 3.1 Existing Repository Audit

An audit of the Pelagos codebase establishes the following capability baseline:

1. **Modular Monolith Backend (`backend/pelagos/`):**
   - FastAPI REST API with structured routers for health, telemetry, freight observations, DuckDB analytical summaries, and baseline forecasting.
   - Dual-tier persistence strategy ([ADR-0002](../architecture/adr/adr-0002-dual-tier-storage-strategy.md)) integrating relational SQLite/PostgreSQL with columnar Parquet storage and DuckDB analytics.
   - SQLAlchemy ORM models (`pelagos.models`):
     - `VesselPosition`: Stores AIS observations (latitude, longitude, speed, course, draft, vessel dimensions). Contains 120 sample records from the Florida Straits.
     - `FreightRoute`: Defines commercial corridors including benchmark distances (`USG-JPN-BULK` = 9,150.0 nm; `PNW-JPN-BULK` = 4,520.0 nm).
     - `FreightObservation` & `FreightPublication`: Store verified rate series and CAS-versioned yearly Parquet catalogs.
   - **Gap Identified:** The backend currently possesses zero maritime routing engines, graph data structures, port coordinate registries, or bathymetric depth tables.

2. **Frontend 3D Geospatial Engine (`gods-eye-view-main/`):**
   - Built on CesiumJS (1.121+), vanilla JS, and Vite.
   - Implements a terrestrial Directions layer (`src/layers/directions/index.js`) allowing users to click two points ("SET A", "SET B") on the 3D globe.
   - **Terrestrial Routing Proxy (`server/providers/places/routes.js`):** Proxies to OpenStreetMap / FOSSGIS OSRM (`routing.openstreetmap.de`) with profiles: `car`, `foot`, `bike`.
   - **Gap Identified:** OSRM is strictly an overland road network router. When queried across ocean bodies, it fails or reports routing busy/unavailable.
   - **Reusable Visualization Pipeline:** The frontend `drawRoute` function already accepts a JSON payload containing an array of `[longitude, latitude]` pairs and renders a clamped polyline on Cesium using:
     ```javascript
     const positions = Cesium.Cartesian3.fromDegreesArray(route.geometry.flat());
     _routeEntity = _viewer.entities.add({
       id: 'directions:route',
       polyline: {
         positions,
         width: 9,
         material: new services.annotations.FlowMaterialProperty(DIRECTIONS_ROUTE_COLOR),
         clampToGround: true,
         classificationType: Cesium.ClassificationType.BOTH,
       },
     });
     ```
     This Cesium polyline rendering and maneuver waypoint card overlay can be directly reused for maritime routes without re-architecting the 3D viewer.

3. **Geospatial Python Environment:**
   - **Installed:** `networkx` (3.3), `pyproj` (3.6.1), `pyshp` (2.3.1), `numpy` (2.4.2), `scipy` (1.14.1), `duckdb` (1.5.6).
   - **Not Installed:** `shapely`, `geopandas`, `geopy`, `searoute`, `geojson`.
   - **Policy Alignment ([ADR-0004](../architecture/adr/adr-0004-core-technology-independence.md)):** Core technology independence prohibits proprietary external routing APIs (e.g. Google Directions API, commercial routing SaaS). The routing engine must execute locally using deterministic, open data structures.

---

## 4. Requirements and Constraints Traceability

| Requirement ID | Canonical Requirement | Specification Section | Compliance Strategy |
|---|---|---|---|
| `REQ-FR-OMR-001` | Evidence-Conditioned Navigational Feasibility | Section 5.1, 5.2 | Evaluates routes on verified sea-lane graph with draft and canal gate constraints; outputs `unsupported-route` on missing data. |
| `REQ-FR-OMR-002` | Prohibition of Geometric Shortest Paths | Section 5.3 | Strictly prohibits straight-line or great-circle sea paths when graph routing fails; displays explicit error without fallback lines. |
| `REQ-FR-OMR-003` | Dynamic Chokepoint & Canal Constraints | Section 5.1, 5.4 | Incorporates canal transit attributes (Panama, Suez) with configurable bypass policies (Cape of Good Hope). |
| `REQ-NFR-DCT-001` | Core Technology Independence | Section 5.1 | Relies on local NetworkX topological graph and open data; zero proprietary external routing SaaS dependencies. |
| `REQ-NFR-DIP-001` | Zero Fabricated / Synthetic Data | Section 5.2, 7 | Missing connections return explicit `unsupported_route`; synthetic coordinates are strictly prohibited in production. |

---

## 5. Design and Specification

### 5.1 Precise Evaluation of Candidate Maritime Data Sources

Commercial vessels do not navigate randomly in open polygonal water; they adhere to defined deep-water fairways, Traffic Separation Schemes (TSS, COLREGS Rule 10), ocean currents, and international canals.

```
+-----------------------------------------------------------------------------------------------------------------------------------------+
|                                              CANDIDATE MARITIME DATA SOURCE AUDIT                                                       |
+--------------------------+-----------------------+-----------------------------+---------------------+----------------------------------+
| Component                | Candidate Source &    | Maintainer & Repository     | Format & Coverage   | License & Legal Status           |
|                          | Release / Version     | Download Location           |                     |                                  |
+--------------------------+-----------------------+-----------------------------+---------------------+----------------------------------+
| 1. Sea-Lane Graph        | Eurostat SeaRoute     | Eurostat / Julien Gaffuri   | Shapefile & GeoJSON | EUPL-1.2 (Reciprocal copyleft;   |
|    (Primary Candidate)   | MARNET Module         | github.com/eurostat/searoute| Global sea lanes    | requires review before embedding |
|                          | (v1.0 release)        |                             | (5km-100km grids)   | in MIT/Apache codebases).        |
+--------------------------+-----------------------+-----------------------------+---------------------+----------------------------------+
| 2. Sea-Lane Graph        | searoute-py           | Gent Halili                 | GeoJSON network     | License Discrepancy: Package is  |
|    (Derived Package)     | (PyPI searoute v1.4.1)| github.com/genthalili/      | (~30,000 nautical   | Apache-2.0, but embedded network |
|                          |                       | searoute-py                 | corridor segments)  | derives from Eurostat EUPL data. |
+--------------------------+-----------------------+-----------------------------+---------------------+----------------------------------+
| 3. Sea-Lane Graph        | ORNL CTA Global       | Oak Ridge National Lab /    | ESRI Shapefile      | U.S. Federal Government work     |
|    (Public Domain Legacy)| Shipping Lane Network | U.S. Dept. of Energy        | Global sea lanes    | (Public Domain, 17 U.S.C. § 105).|
|                          | (Legacy release)      | cta.ornl.gov                | (~1990s baseline)   | Outdated TSS & canal lock state. |
+--------------------------+-----------------------+-----------------------------+---------------------+----------------------------------+
| 4. Port Identifiers      | UN/LOCODE             | UNECE / UN/CEFACT           | CSV / Data Tables   | Standard UN Terms (Disclaims     |
|    (Trade Codes)         | (2024-1 release)      | unece.org/cefact/locode     | ~100,000 global     | accuracy; CC BY 4.0 for standard,|
|                          |                       |                             | location codes      | but contains approximate coords).|
+--------------------------+-----------------------+-----------------------------+---------------------+----------------------------------+
| 5. Port Information      | NGA World Port Index  | U.S. National Geospatial-   | Shapefile / CSV     | U.S. Federal Government work     |
|    (Directory Only)      | (Pub. 150, 2024 ed.)  | Intelligence Agency (NGA)   | ~3,700 commercial   | (Public Domain, 17 U.S.C. § 105).|
|                          |                       | msi.nga.mil                 | port directories    | Directory data only; NOT routes. |
+--------------------------+-----------------------+-----------------------------+---------------------+----------------------------------+
| 6. Coastline Barrier     | Natural Earth Land    | Natural Earth / NACIS       | Shapefile / GeoJSON | Public Domain (Free for all      |
|    (Plausibility Mask)   | (v5.1.2, 1:110m/1:50m)| naturalearthdata.com        | Global continental  | uses; barrier mask only; cannot  |
|                          |                       |                             | & island polygons   | synthesize navigable routes).    |
+--------------------------+-----------------------+-----------------------------+---------------------+----------------------------------+
```

#### Detailed Source & Licensing Analysis

1. **Sea-Lane Graph Licensing Discrepancy & Unresolved D9.2 Prerequisite:**
   - **Eurostat MARNET:** The most complete open-access topological sea-lane graph is Eurostat's MARNET, authored by Julien Gaffuri. However, it is published under the **European Union Public License (EUPL-1.2)**, which includes copyleft terms (Article 5) requiring downstream derivative packages to remain under EUPL-compatible terms.
   - **`searoute-py`:** The popular Python port by Gent Halili claims Apache-2.0 in its repository metadata, but its embedded GeoJSON dataset (`marnet_densified.geojson`) is a direct extraction and densification of Eurostat's EUPL-1.2 MARNET data. Therefore, the Apache-2.0 license may legally cover only the Python wrapper logic, not the underlying maritime network geometry.
   - **Resolution Policy:** **Selecting, verifying, and legally vetting the production sea-lane graph data asset is explicitly designated as an open D9.2 prerequisite.** Ingesting or redistributing this dataset will not occur until licensing compatibility (EUPL-1.2 vs. Apache-2.0 / MIT) is confirmed by project maintainers.

2. **Port Coordinates: Distinguishing Identifiers from Navigational Approach Gates:**
   - **UN/LOCODE is an Administrative Trade Code, NOT Navigational Data:** UN/LOCODE (managed by UNECE / UN/CEFACT under Recommendation No. 16) provides canonical 5-letter location identifiers (e.g. `USMSY`, `JPYOK`). However, UNECE explicitly disclaims coordinate accuracy. UN/LOCODE coordinates are frequently municipal city-center centroids, customs offices, or approximate harbor midpoints submitted by member states. In many cases, these coordinates lie miles inland or behind shallow non-navigable channels. Furthermore, while UN/CEFACT standards are open under CC BY 4.0, UN website distribution policies reserve rights against unauthorized commercial redistribution of raw databases.
   - **NGA World Port Index (Pub. 150) is an Information Directory, NOT Routing Data:** NGA Pub. 150 is a public domain U.S. Federal Government publication (17 U.S.C. § 105) detailing physical harbor attributes (maximum draft, entrance depth, tide, pilotage, tugs). While authoritative for port facilities, **it contains zero fairway approach waypoints, TSS corridors, or route networks**, and cannot independently calculate routes.
   - **Three-Tier Port Coordinate Hierarchy for Pelagos:**
     - **Level 1 (Administrative Centroid):** UN/LOCODE trade identifier and nominal geographic coordinate (used for search and user display).
     - **Level 2 (Navigable Fairway Gate / Pilot Station):** Verified deep-water sea buoy or pilot boarding station at the harbor entrance (e.g., Mississippi River Southwest Pass Sea Buoy: $28^\circ 54' N, 89^\circ 26' W$ for New Orleans access). This is the terminal connection point for sea-lane graph pathfinding.
     - **Level 3 (Berth / Terminal Coordinate):** Specific commercial pier or grain elevator terminal within a harbor basin (requiring local harbor pilotage, tug assistance, and air-draft clearance under bridges).
   - **Implementation Rule:** The Pelagos routing engine connects between Level 2 Navigable Fairway Gates on the sea-lane graph, explicitly identifying inner-harbor approaches as distinct local pilotage segments.

3. **Why Coastline Geometry Alone is Insufficient:**
   - Coastline geometry (such as Natural Earth land polygons) only defines dry land boundaries. It contains no bathymetric depth, no navigational markers, and no traffic rules.
   - Ray-casting checks against land boundaries confirm only gross physical separation from dry land; **they do NOT establish that a path is navigationally safe, deep enough for vessel draft, or compliant with international maritime regulations.**

---

### 5.2 Minimum Viable Routing Architecture (MVP)

The routing subsystem will be encapsulated within `backend/pelagos/routing/` following the modular monolith pattern ([ADR-0001](../architecture/adr/adr-0001-modular-monolith-architecture.md)):

```
backend/pelagos/routing/
├── __init__.py
├── graph.py          # NetworkX sea-lane graph loader and shortest-path solver
├── ports.py          # Curated port catalog, UN/LOCODE lookup, and fairway gate mapping
├── geometry.py       # Geodesic distance (Haversine/Vincenty on WGS 84) and land intersection checks
├── schemas.py         # Pydantic input/output contracts
└── service.py        # Routing service coordinating port resolution, graph solving, and ETA
```

#### Algorithmic Execution Workflow:
1. **Endpoint Resolution:** Origin and destination are supplied as either a recognized UN/LOCODE (e.g. `"USMSY"`, `"JPYOK"`) or explicit WGS 84 coordinates (`lat`, `lon`).
2. **Coordinate & Marine Plausibility Validation:**
   - Latitudes must satisfy $-90.0 \le \phi \le 90.0$; longitudes must satisfy $-180.0 \le \lambda \le 180.0$.
   - Coordinates positioned deeply inland beyond coastal tolerance are rejected immediately (`STATUS_INVALID_INPUT`).
3. **Terminal Sea-Lane Snapping:**
   - Origin and destination are mapped to their respective verified Fairway Entrance Gates or snapped to the nearest accessible water nodes in the sea-lane graph.
   - The connector line is checked against the Natural Earth land boundary mask. If a direct line intersects a landmass, secondary gateway nodes are evaluated.
4. **Graph Pathfinding:**
   - Dijkstra or $A^*$ algorithm executed on the NetworkX graph with edge weights defined by geodesic nautical miles ($NM$).
   - Canals carry policy tags. If the user specifies `canal_policy="avoid_panama"`, Panama Canal edges are masked with infinite weight, forcing the solver to find the Cape of Good Hope or Cape Horn bypass.
5. **Path Synthesis:**
   - Complete waypoint sequence assembled: $[P_{origin}] \rightarrow [W_1, W_2, \dots, W_k] \rightarrow [P_{dest}]$.
   - Individual leg distances summed; total nautical miles and kilometers calculated.

---

### 5.3 Proposed API Data Contracts

#### Input Contract (`RoutingRequest`)

```python
class PortIdentifierOrCoords(BaseModel):
    unlocode: Optional[str] = None  # e.g. "USMSY", "JPYOK", "USSEA"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    port_name: Optional[str] = None

class RoutingRequest(BaseModel):
    origin: PortIdentifierOrCoords
    destination: PortIdentifierOrCoords
    vessel_speed_knots: Optional[float] = 13.0   # Default commercial bulk steaming speed
    canal_policy: Optional[str] = "allow"        # "allow" | "avoid_panama" | "avoid_suez" | "avoid_all"
    max_draft_m: Optional[float] = None          # Optional draft constraint check
    departure_time: Optional[datetime] = None    # Optional departure for ETA calculation
```

#### Output Contract (`RoutingResponse`)

```python
class RouteWaypoint(BaseModel):
    name: str
    latitude: float
    longitude: float
    waypoint_type: str  # "fairway_gate" | "sea_lane" | "canal_entrance" | "strait"
    cumulative_distance_nm: float

class RouteSummary(BaseModel):
    origin_name: str
    origin_coords: List[float]  # [lon, lat]
    destination_name: str
    destination_coords: List[float]
    total_distance_nm: float
    total_distance_km: float
    estimated_voyage_hours: float
    estimated_voyage_days: float
    eta_utc: Optional[datetime] = None
    canals_transited: List[str]
    canal_policy_applied: str

class RoutingResponse(BaseModel):
    route_id: str                      # Deterministic SHA-256 hash of query parameters
    status: str                        # "calculated" | "unsupported_route" | "invalid_input"
    route_method: str = "maritime_sea_lane_graph"
    summary: Optional[RouteSummary]
    geometry: List[List[float]]        # [[lon, lat], ...] WGS 84 for Cesium polyline rendering
    waypoints: List[RouteWaypoint]
    limitations: List[str]             # Explicit disclaimers
    provenance_hash: str
```

#### Enforcement of `REQ-FR-OMR-002` (Prohibition of Geometric Paths):
If pathfinding fails (e.g. disconnected graph component, landlocked coordinate, or canal bypass blocked by depth restriction):
- The response returns `status: "unsupported_route"`.
- `geometry` is returned as an empty array `[]` (or `null`).
- An explicit error explanation is returned: `"Operational maritime route unavailable for specified corridor and constraints; straight-line geometry suppressed."`

---

## 6. Distance and ETA Semantics

### 6.1 Distance Metrics and Standard Units

1. **Standard Marine Unit:** **Nautical Miles (NM)**.
   - Defined by international standard: $1 \text{ NM} = 1,852 \text{ meters} = 1.852 \text{ km}$.
2. **Leg Distance Calculation:**
   - Geodesic distance along the WGS 84 ellipsoid computed using the Vincenty or great-circle Haversine formula via `pyproj` or standard spherical trigonometry:
     $$d = 2 R \arcsin \left( \sqrt{\sin^2\left(\frac{\Delta \phi}{2}\right) + \cos(\phi_1) \cos(\phi_2) \sin^2\left(\frac{\Delta \lambda}{2}\right)} \right)$$
3. **Cumulative Total Distance:**
   $$D_{total\_nm} = \sum_{i=1}^{N-1} d(W_i, W_{i+1})$$
   $$D_{total\_km} = D_{total\_nm} \times 1.852$$

### 6.2 Voyage Duration and ETA Calculation

Voyage duration is modeled as the sum of open-water steaming time and nominal canal transit/lockage delays:

$$T_{steaming\_hours} = \frac{D_{open\_water\_nm}}{V_{speed\_knots}}$$
$$T_{total\_hours} = T_{steaming\_hours} + \sum T_{canal\_delay}$$

#### Canal Delays as Configurable Operational Assumptions:
Canal transit durations are **NOT universal constants**; they vary dynamically with convoy scheduling, seasonal water levels, and booking queue congestion. The following values are **provisional, configurable baseline assumptions** with documented operational provenance:
- **Panama Canal:** Configurable baseline assumption: **24.0 hours** (assumes scheduled booking slot, Gatun Lake lockages, and Gaillard Cut transit without extraordinary queue congestion). Provenance: Panama Canal Authority (ACP) published average transit times for booked vessels.
- **Suez Canal:** Configurable baseline assumption: **14.0 hours** (assumes synchronized convoy slot transit from Port Said to Port Tewfik). Provenance: Suez Canal Authority (SCA) convoy navigation rules.
- **Kiel Canal:** Configurable baseline assumption: **8.0 hours**.

#### ETA Derivation:
- If `departure_time` ($t_{dep}$) is supplied: $\text{ETA} = t_{dep} + T_{total\_hours}$.
- If omitted: Returns `estimated_voyage_hours` and `estimated_voyage_days`, leaving absolute calendar ETA unset.

#### Explicit Limitations:
- Calculated voyage duration reflects **nominal calm-water conditions**.
- It does **not** model dynamic sea-state resistance, tropical storm avoidance rerouting, tidal current drifts, or real-time canal congestion queue delays.
- It is an **analytical commercial estimate**, NOT an ECDIS passage plan.

---

## 7. Deterministic Validation Plan & Grounding Evidence

### 7.1 Distinguishing Macro Corridor Benchmarks from Port-to-Port Measurements
Existing Pelagos freight records (`pelagos.models.FreightRoute`) carry macro corridor benchmark distances derived from USDA AMS AgTransport fixture metadata:
- `USG-JPN-BULK`: **9,150.0 nm** (U.S. Gulf loading range to Japan discharge range via Panama Canal).
- `PNW-JPN-BULK`: **4,520.0 nm** (Pacific Northwest loading range to Japan discharge range direct).

These USDA figures represent broad commercial grain trading corridor averages. They are **not** point-to-point measurements between specific terminal piers. Point-to-point distances between specific harbor fairway gates (e.g., New Orleans Southwest Pass to Yokohama Bay) will exhibit minor variations depending on exact fairway buoy locations and TSS lane choices. All target distance ranges in the validation test matrix are therefore explicitly designated as **provisional benchmarks**.

### 7.2 Required Evidence for Validating Computed Routes
To rigorously validate a computed maritime route's distance and geometry in Milestone D9.2+, the implementation must verify against:
1. **Authoritative Hydrographic Distance Tables:** Official distances published by national hydrographic agencies, specifically **NGA Pub. 151 (Distance Between Ports)** or **NOAA Distance Between U.S. Ports**, for the exact harbor fairway coordinates.
2. **Empirical Historical AIS Voyage Tracks:** Verified GPS trajectories from real commercial vessels traversing the corridor (e.g. Capesize or Panamax fixtures from MarineCadastre or global AIS records), confirming that the computed path adheres to observed navigational corridors.
3. **Ellipsoidal Geodesic Integrity:** Verification that every leg distance matches WGS 84 geodesic calculations via `pyproj` and that coordinates do not intersect land polygons.

### 7.3 Deterministic Validation Test Matrix

```
+-----------------------------------------------------------------------------------------------------------------------------------------+
|                                              DETERMINISTIC VALIDATION TEST MATRIX                                                       |
+----+--------------------------+-----------------------+-------------------+-----------------------------+-------------------------------+
| ID | Origin / Destination     | Scenario / Policy     | Expected Status   | Provisional Distance Range  | Grounding Reference Evidence  |
|    | (Fairway Gates)          |                       |                   | (Nautical Miles)            |                               |
+----+--------------------------+-----------------------+-------------------+-----------------------------+-------------------------------+
| T1 | USMSY (New Orleans) ->   | Standard Canal Route  | "calculated"      | 9,100 - 9,350 NM            | Provisional range consistent  |
|    | JPYOK (Yokohama)         | canal_policy="allow"  |                   | (via Panama Canal)          | with USDA corridor benchmark  |
|    |                          |                       |                   |                             | (9,150 NM) and NGA Pub. 151.  |
+----+--------------------------+-----------------------+-------------------+-----------------------------+-------------------------------+
| T2 | USSEA (Seattle) ->       | North Pacific Direct  | "calculated"      | 4,400 - 4,650 NM            | Provisional range consistent  |
|    | JPYOK (Yokohama)         | canal_policy="allow"  |                   | (Great Circle sea lane)     | with USDA corridor benchmark  |
|    |                          |                       |                   |                             | (4,520 NM) and NGA Pub. 151.  |
+----+--------------------------+-----------------------+-------------------+-----------------------------+-------------------------------+
| T3 | USMSY (New Orleans) ->   | Canal Bypass          | "calculated"      | 14,000 - 15,500 NM          | Provisional range modeling    |
|    | JPYOK (Yokohama)         | canal_policy=         |                   | (via Cape of Good Hope      | Panama Canal closure; traces  |
|    |                          | "avoid_panama"        |                   | and Sunda/Malacca Straits)  | to REQ-FR-OMR-003.            |
+----+--------------------------+-----------------------+-------------------+-----------------------------+-------------------------------+
| T4 | Landlocked Coordinate    | Inland Origin Test    | "invalid_input"   | N/A (Null / Suppressed)     | Verifies rejection of inland  |
|    | (Denver: 39.73, -104.99) |                       |                   |                             | coordinates; suppresses path. |
+----+--------------------------+-----------------------+-------------------+-----------------------------+-------------------------------+
| T5 | Disconnected Water       | Unnavigable Route     | "unsupported_     | N/A (Null / Suppressed)     | Verifies REQ-FR-OMR-002: zero |
|    | (Caspian Sea to Pacific) |                       | route"            |                             | straight-line fallback.       |
+----+--------------------------+-----------------------+-------------------+-----------------------------+-------------------------------+
| T6 | Deterministic Parity     | Repeated Query Test   | "calculated"      | Bit-for-bit identical       | Verifies 10 repeated queries  |
|    | (USMSY -> JPYOK)         |                       |                   | distance & SHA-256 hash     | produce identical output.     |
+----+--------------------------+-----------------------+-------------------+-----------------------------+-------------------------------+
```

---

## 8. Dependencies and Interfaces

- **Upstream:** `pelagos.models.FreightRoute` benchmark metadata, curated port catalog with fairway entrance coordinates, topological sea-lane graph.
- **Runtime Environment:** Python 3.12+, `networkx` 3.3, `pyproj` 3.6.1, `numpy` 2.4.2.
- **Downstream:**
  - `pelagos.api.routes.routing`: Read-only REST endpoint serving GeoJSON coordinates.
  - `pelagos.commercial`: Voyage chartering optimization engine consuming nautical distance and voyage days.
  - `gods-eye-view-main`: CesiumJS 3D command deck draped polyline rendering.

---

## 9. Failure Modes and Limitations

1. **Topological Graph Resolution Limit:** The sea-lane graph represents deep-water macro shipping lanes. It does not provide harbor-tug berthing maneuvers or berth-level navigation.
2. **Dynamic Chokepoint Queue Fluctuations:** Canal transit times are modeled using configurable baseline assumptions, not real-time booking slot auctions or emergency draft restrictions.
3. **Draft Sounding Limitations:** Coastal harbor approaches require local pilotage. The system flags draft limits when known, but does not guarantee safe under-keel clearance.

---

## 10. Security, Privacy and Licensing Analysis

1. **Sea-Lane Network Graph Licensing:** Eurostat MARNET carries the EUPL-1.2 license, while `searoute-py` declares Apache-2.0 despite embedding MARNET data. The legal compatibility of redistributing this data asset within the Pelagos modular monolith must be formally established during D9.2 before repository commitment.
2. **Port Data Licensing:** UN/LOCODE terms disclaim coordinate accuracy and reserve commercial redistribution rights. NGA Pub. 150 is public domain under 17 U.S.C. § 105 but serves only as a directory of port characteristics, not navigational coordinates. A curated, self-contained reference fixture mapping UN/LOCODE keys to public-domain fairway coordinates is the compliant path.
3. **Zero Credentials or SaaS Dependencies:** All pathfinding algorithms run locally via NetworkX; zero user queries or coordinates are transmitted to external cloud services.

---

## 11. Testing and Verification Strategy

Implemented and validated in Milestone D9.2+:
1. Unit tests verifying Dijkstra/A* path convergence on NetworkX graph.
2. Verification of Panama Canal bypass distance expansion ($+5,000+\text{ NM}$).
3. Verification that invalid coordinates or disconnected networks strictly return `unsupported_route` with zero polyline geometry.
4. Parity testing verifying exact mathematical relationship $1\text{ NM} = 1.852\text{ km}$.

---

## 12. Open Questions and D9.2 Prerequisites

- **OQ-ROUT-01 (Sea-Lane Graph Licensing & Selection - D9.2 Prerequisite):** Formally resolve whether Eurostat MARNET (EUPL-1.2), the `searoute-py` data extraction, or an updated derivative of Oak Ridge National Laboratory (ORNL) CTA public domain data will serve as the committed production routing graph.
- **OQ-ROUT-02 (Curated Fairway Gate Fixture Scope):** Define the initial fixture size for port fairway entrance gates. *Recommendation: Author a curated fixture of ~50–100 major bulk/container ports with verified deep-water sea buoy coordinates, rather than attempting to ingest the entire 100k UN/LOCODE database.*
- **OQ-ROUT-03 (Canal Fee Model Placement):** Maintain financial toll fee modeling inside the commercial chartering engine (`D10`), keeping the routing engine focused on transit feasibility, delay hours, and distance.

---

## 13. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-17 | Project Agent | Initial creation of D9.1 Operational Maritime Routing Readiness Assessment. Formulated data strategy, NetworkX graph architecture, API contracts, canal bypass policies, and deterministic validation matrix. |
| 1.1.0 | 2026-10-17 | Project Agent | D9.1 evidence corrections: Precisely evaluated sea-lane graph candidates (Eurostat MARNET EUPL-1.2 vs. searoute-py Apache-2.0 vs. ORNL CTA public domain); marked graph source selection as unresolved D9.2 prerequisite; corrected port-coordinate claims to distinguish UN/LOCODE centroids from fairway gates and NGA Pub. 150 directory data; separated Pelagos corridor benchmarks from port-to-port distances; labeled validation ranges provisional and defined required hydrographic validation evidence; labeled canal delays as configurable operational assumptions with ACP/SCA provenance. |
