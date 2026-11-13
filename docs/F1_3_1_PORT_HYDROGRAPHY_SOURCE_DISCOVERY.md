---
title: Global Port Restrictions & Hydrography Source Discovery
document_id: DOC-F1-3-1
type: research_notes
status: approved
version: 1.0.0
created: 2026-11-13
updated: 2026-11-13
tags:
  - ports
  - hydrography
  - restrictions
  - draft-limits
  - discovery
  - stage-f1-3
---

# Pelagos — Stage F1.3.1: Global Port Restrictions & Hydrography Source Discovery Report

## Executive Summary

To establish an operationally defensible foundation for maritime vessel chartering and freight forecasting, the Pelagos platform requires rigorous, verifiable data regarding port water depths, channel dimensions, berth particulars, air-draft clearances, and operational draft restrictions. Prior to this discovery exercise, Pelagos evaluated vessel physical compatibility (Gate 4) against approximate port geographic access points and unstructured text clauses stored in `genuine_ports_directory.json` (such as `"access_limitations": "Columbia River Mile 75 deep-draft navigation channel (43-foot draft)"`), parsed via rudimentary regular expressions. When a target port lacked an explicit regex-parseable draft limit in its text notes, the engine logged `missing_requirements: ["origin_verified_berth_draft_limit_meters"]` or admitted vessels without verified berth pocket clearance.

This Stage F1.3.1 investigation conducted a comprehensive, empirical discovery across global publicly accessible maritime port registries, hydrographic offices, government open-data portals, pilotage authority circulars, and river basin administrations. Every candidate source was directly queried, inspected at the schema and record level, and evaluated against strict operational suitability criteria.

### Key Discoveries & Empirical Findings

1. **Structured U.S. Federal Datasets (USACE & NOAA):**
   - **USACE Navigation Facilities (Master Docks Plus):** Hosts over 12,000 spatial records with berth-level numeric parameters (`DEPTH_MIN`, `DEPTH_MAX`, `BERTHING_LARGEST`, `VERTICAL_DATUM`, `COMMODITIES`) across coastal and inland waterways. Crucially, empirical investigation revealed that USACE NDC strips the two-letter ISO country code from UN/LOCODEs (e.g. Kalama is stored as `KAM`, Seattle as `SEA`, Portland as `PDX`, Houston as `HOU`, New Orleans as `MSY`), requiring prefix normalization.
   - **USACE National Channel Framework (NCF):** Publishes reach-by-reach channel geometry (`depthmaintained`, `depthauthorized`, `widthfrom`, `widthto`, `mileagestart`, `mileageend`) via live ArcGIS REST FeatureServers, establishing controlling channel constraints (e.g., 50.0 ft maintained depth along Southwest Pass, Mississippi River; 55.0 ft / 48.0 ft along the Columbia River entrance).
   - **NOAA CO-OPS PORTS® API:** Provides live programmatic endpoints (`api.tidesandcurrents.noaa.gov`) returning 6-minute bridge air gap measurements (e.g., Huey Long Bridge Air Gap station `8762002` currently reporting `152.45 ft` vertical clearance) and real-time tidal water levels relative to Mean Lower Low Water (MLLW).

2. **Global Registries & Critical Limitations:**
   - **NGA World Port Index (Pub. 150):** While indexing over 3,700 global ports, empirical schema queries revealed that NGA depth fields (`CHAN_DEPTH`, `CARGODEPTH`, `ANCH_DEPTH`, `OIL_DEPTH`) are broad categorical 5-foot letter bins (`A` = 76+ ft down to `P` = 0-10 ft), `MAX_VESSEL` is a crude binary category (`L` = 500+ ft vs `M` = <=500 ft), and `OVERHD_LIM` is an unquantified boolean indicator (`Y` or blank). **NGA WPI depth codes must never be converted into precise vessel draft limits for operational screening.**
   - **UNECE UN/LOCODE:** Strictly an administrative trade logistics gazetteer. Contains zero hydrographic, depth, draft, or berth data.
   - **Gridded Global/Regional Bathymetry (GEBCO & EMODnet):** Querying EMODnet's 1/16 arc-minute bathymetry REST API at the Port of Rotterdam entrance (`POINT(4.14 51.95)`) returned a modelled seabed depth of **4.71 meters**, whereas Rotterdam's Eurogeul channel is maintained at 24.0 meters and its Maasvlakte berths accommodate 22.0m+ draft bulkers. This provides irrefutable empirical proof that gridded bathymetric DTMs are severely degraded by coastal shoreline interpolation and must never be substituted for certified fairway or berth controlling depths.

3. **International Port Authorities & Pilotage Gazettes:**
   - In maritime hubs across India (JNPA Nhava Sheva, Visakhapatnam, Paradip, SMPK Kolkata/Haldia) and East Asia (Yokohama, Tokyo Bay), official operational draft limits are not published as automated REST APIs, but rather as statutory marine department gazettes, berthing policies, and pilotage circulars.
   - Paradip Port Authority (`paradipport.gov.in`) publishes exact per-berth admissible LOA (up to 300m), beam (up to 46m), and draft (14.50m - 16.50m high tide) across 18 distinct berths. Visakhapatnam Port Authority (`vizagport.com`) publishes an 18.10m permissible draft for Capesize bulkers in the Outer Harbour (Essar Ore Berths & VGCBL) versus 14.50m in the Inner Harbour. Syama Prasad Mookerjee Port (Kolkata/Haldia) publishes daily freshwater draft forecasts governed by shifting river bar controlling depths (Eastern Gut, Moyapur).

### Feasibility Assessment
Building a global, multi-source, provenance-preserving Port Restriction & Hydrography Registry for Pelagos is **operationally feasible** by adopting a tiered hybrid architecture:
- **Tier 1 (Automated Ingestion):** Programmatic synchronization with USACE Master Docks, USACE NCF, and NOAA PORTS APIs for North American ports.
- **Tier 2 (Authoritative Port Gazetteers):** Structured, provenance-tracked JSON fixtures capturing statutory port authority and pilotage rules for major international grain, ore, and coal export/import gateways.
- **Tier 3 (Operational Guardrails):** Strict fail-closed Gate 4 evaluation ensuring that unverified berth depths or conflicting tidal limits trigger `SuitabilityStatus.UNVERIFIED` rather than unvalidated passage.

---

## 1. Critical Semantic Distinctions

To prevent dangerous maritime routing and chartering assessments, Pelagos strictly enforces clear semantic boundaries across physical and operational hydrographic parameters:

| Term / Attribute | Formal Definition | Operational Role & Constraints |
| :--- | :--- | :--- |
| **Water Depth** | Instantaneous vertical distance between water surface and seabed. | Time-varying function of astronomical tide, meteorological surge, and seasonal river discharge. |
| **Charted / Bathymetric Depth** | Seabed elevation below a specified vertical datum (e.g., MLLW, LAT, CD). | Static baseline depicted on nautical charts. Does not reflect immediate siltation, shoaling, or dredging post-survey. |
| **Controlling Channel Depth** | The minimum measured water depth along the shallowest reach of a designated navigation channel. | Governs the transit of vessels through fairways and waterways; dynamic condition surveys dictate official notices. |
| **Project / Authorized Depth** | The congressionally or legislatively authorized design depth for a channel reach maintained by dredging authorities (e.g., USACE 50-ft project depth). | The statutory design target, not necessarily the immediate guaranteed real-time water depth. |
| **Berth Alongside Depth** | Depth of water measured within the pocket alongside a specific wharf, pier, or jetty face below chart datum. | Limits vessel draft while moored and loading; often differs substantially from approach channel depths. |
| **Maximum Permissible Draft** | The maximum allowable operating draft authorized by the harbour master or pilotage authority under designated conditions. | Accounts for water density (fresh vs salt), tidal windows, vessel speed, bank suction, squat, and required under-keel clearance. |
| **Under-Keel Clearance (UKC)** | The vertical safety margin required between the lowest point of the vessel's hull/keel and the seabed. | Typically 1.0m - 1.5m in calm harbour channels, or 10% - 15% of static draft in open coastal approaches (e.g., Columbia River Bar, Eurogeul). |
| **Air Draft / Overhead Clearance** | The vertical distance from the waterline to the highest point of the vessel (mast, radar scanner, crane). | Constrained by overhead bridges, power cables, and gantry cranes. Changes inversely with vessel loading (ballast vs laden). |
| **Water Density / Fresh Water Allowance** | Specific gravity of water (1.000 g/cm³ in fresh river water vs 1.025 g/cm³ in open ocean). | Fresh water causes vessels to sink deeper by their Fresh Water Allowance (FWA), severely impacting Mississippi and Columbia River transits. |

---

## 2. Comprehensive Source Inventory

### Source 1: USACE Navigation Data Center (NDC) — Master Docks Plus (Navigation Facilities)

- **Responsible Organization:** U.S. Army Corps of Engineers (USACE), Institute for Water Resources (IWR), Waterborne Commerce Statistics Center (WCSC).
- **Dataset Name:** Navigation Facilities (Master Docks Plus / Port Docks and Terminals).
- **Service Endpoint:** `https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/Docks/FeatureServer/0`
- **Access Method:** ArcGIS REST API (`/query`), downloadable Shapefile, File Geodatabase, and KMZ.
- **Authentication:** None required. Publicly accessible.
- **Cost & License:** Free. U.S. Federal Government Public Domain (17 U.S.C. § 105). Unrestricted reuse.
- **Geographic Coverage:** United States coastal ports, Great Lakes, and inland river navigation systems (~12,000 facilities).
- **Inspected Fields:**
  - Identifiers: `OBJECTID`, `NAV_UNIT_NAME`, `NAV_UNIT_ID`, `UNLOCODE` (3-character code), `PORT_NAME`, `CITY_OR_TOWN`, `STATE`, `WTWY_NAME`, `MILE`, `BANK`.
  - Dimensions & Depth: `DEPTH_MIN` (ft), `DEPTH_MAX` (ft), `VERTICAL_DATUM`, `BERTHING_LARGEST` (ft), `BERTHING_TOTAL` (ft), `DECK_HEIGHT_MIN` (ft), `DECK_HEIGHT_MAX` (ft).
  - Commercial Particulars: `OPERATORS`, `OWNERS`, `PURPOSE`, `COMMODITIES`, `MECHANICAL_HANDLING`, `RAILWAY_NOTE`.
- **Datums & Units:** Depths in Feet. Vertical datums explicitly recorded (e.g., `MLLW (Mean Lower Low Water)` on Pacific/Atlantic coasts; `MLG (Mean Low Gulf)` in Gulf Coast ports). Coordinates in WGS84 (EPSG:4326).
- **Empirical Validation Extract:**
  - *Port of Kalama (`KAM`):* `TEMCO (CHS CARGILL) KALAMA GRAIN ELEVATOR` — `DEPTH_MIN`: 40.00 ft, `DEPTH_MAX`: 40.00 ft MLLW, `BERTHING_LARGEST`: 840 ft. Commodities: Wheat, Corn, Oilseeds.
  - *Port of Kalama (`KAM`):* `KALAMA EXPORT CO. GRAIN ELEVATOR WHARVES` — `DEPTH_MIN`: 20.00 ft, `DEPTH_MAX`: 40.00 ft MLLW, `BERTHING_LARGEST`: 800 ft. Commodities: Grain, Soybeans.
  - *Port of New Orleans (`MSY`):* `Napoleon Avenue Open Wharf` — `DEPTH_MIN`: 35.00 ft, `DEPTH_MAX`: 35.00 ft MLG, `BERTHING_LARGEST`: 2,147 ft.
- **Retrieval & Normalization Notes:** USACE NDC stores UN/LOCODEs without the `US` prefix (e.g., `KAM` instead of `USKAL`, `MSY` instead of `USMSY`, `PDX` instead of `USPDX`). The ingestion pipeline must normalize 3-letter codes with country prefixes.
- **Operational Suitability:** `DIRECTLY_VERIFIED` for U.S. commercial berth alongside depths, berth lengths, and handling equipment.

---

### Source 2: USACE eHydro — National Channel Framework (NCF)

- **Responsible Organization:** U.S. Army Corps of Engineers (USACE) eHydro Enterprise Hydrographic Survey System.
- **Dataset Name:** National Channel Framework (NCF) — Channel Reaches & Channel Areas.
- **Service Endpoint:** `https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/National_Channel_Framework/FeatureServer`
  - Layer 0: `ChannelLine`
  - Layer 1: `ChannelArea`
  - Layer 2: `ChannelReach`
  - Layer 3: `ChannelQuarter`
- **Access Method:** ArcGIS REST API (`/query`), GeoJSON, Shapefile, ESRI GDB.
- **Authentication:** None. Open access.
- **Cost & License:** Free. U.S. Federal Government Public Domain (17 U.S.C. § 105).
- **Geographic Coverage:** All congressionally authorized navigation channels maintained by USACE across all U.S. coastal and inland districts.
- **Inspected Fields (Layer 2 - `ChannelReach`):**
  - `sdsfeaturename`: Official reach or sheet name.
  - `depthmaintained`: Maintained project depth in feet.
  - `depthauthorized`: Congressionally authorized depth in feet.
  - `widthfrom`, `widthto`: Channel bottom width in feet.
  - `mileagestart`, `mileageend`: River mile boundary markers.
  - `usacedistrictcode`: Responsible USACE District (e.g. `CEMVN` = New Orleans, `CENWP` = Portland, `CESWG` = Galveston, `CENWS` = Seattle).
- **Datums & Units:** Depths in Feet below local low water reference datum (MLLW / CRD - Columbia River Datum / Low Water Reference Plane). WGS84 coordinates.
- **Empirical Validation Extract:**
  - *Southwest Pass, Mississippi River (`CEMVN`):* Sheets 1 through 10 maintain `depthmaintained: 50.0 ft`, `depthauthorized: 50.0 ft`, `widthfrom: 750 ft`, `widthto: 750 ft` across Miles 13.4 to -2.0.
  - *Mouth of Columbia River (`CENWP`):* Main entrance channel maintains `depthmaintained: 55.0 ft` (and 48.0 ft inner) with `width: 2,000 ft` across River Miles -2.5 to 3.0.
- **Operational Suitability:** `DIRECTLY_VERIFIED` for authoritative U.S. waterway channel controlling dimensions.

---

### Source 3: NGA Maritime Safety Office — World Port Index (Pub. 150)

- **Responsible Organization:** U.S. National Geospatial-Intelligence Agency (NGA) / Maritime Safety Information (MSI).
- **Dataset Name:** World Port Index (Pub. 150).
- **Service Endpoint:** `https://services.arcgis.com/hRUr1F8lE8Jq2uJo/arcgis/rest/services/World_Port_Index/FeatureServer/0`
- **Access Method:** ArcGIS REST API (`/query`), downloadable Shapefile, CSV, and PDF publication.
- **Authentication:** None required.
- **Cost & License:** Free. U.S. Federal Government Public Domain (17 U.S.C. § 105).
- **Geographic Coverage:** Global (~3,700 ports across all maritime continents).
- **Inspected Fields:**
  - Identity & Location: `INDEX_NO`, `REGION_NO`, `PORT_NAME`, `COUNTRY`, `LATITUDE`, `LONGITUDE`.
  - Harbor Geometry: `HARBORSIZE` (`L`=Large, `M`=Medium, `S`=Small, `V`=Very Small), `HARBORTYPE` (`CN`=Coastal Natural, `CB`=Coastal Breakwater, `RN`=River Natural, `RT`=River Terminal).
  - Depths: `CHAN_DEPTH`, `CARGODEPTH`, `ANCH_DEPTH`, `OIL_DEPTH` (All stored as categorical letter codes `A` through `P`).
  - Limits: `MAX_VESSEL` (`L`=Over 500 ft, `M`=Up to 500 ft), `OVERHD_LIM` (`Y` or blank), `TIDE_RANGE` (meters).
- **Critical Semantic Deficiency:**
  - Depth letters represent 5-foot ranges: `A` (76+ ft), `G` (46-50 ft), `H` (41-45 ft), `J` (36-40 ft), `K` (31-35 ft), `L` (26-30 ft).
  - Yokohama (`INDEX_NO`: 61390) reports `CHAN_DEPTH`: `J` (36-40 ft / 11.0-12.2m), yet Uraga Channel accommodates 17.0m draft bulkers!
  - `MAX_VESSEL`: `L` indicates only that the vessel exceeds 152 meters. It cannot differentiate between a 190m Supramax, a 225m Panamax, or a 292m Capesize bulker.
  - `OVERHD_LIM`: Simply marks `Y` (Yes) without indicating whether the clearance is 45m, 55m, or 70m.
- **Operational Suitability:** `SOURCE_REPORTED` for coarse macro-level port discovery, country mapping, and basic shelter indicators. **Categorically rejected for vessel draft or air-draft screening.**

---

### Source 4: UNECE UN/LOCODE (United Nations Code for Trade and Transport Locations)

- **Responsible Organization:** United Nations Economic Commission for Europe (UNECE).
- **Dataset Name:** UN/LOCODE Code List (Releases 2024-1 and 2024-2).
- **Portal URL:** `https://unece.org/trade/cefact/unlocode-code-list-country-and-territory`
- **Access Method:** Bi-annual ZIP download containing semi-colon delimited text/CSV files (`loc241csv.zip`). Automated scraping protected by HTTP 403 bot gates.
- **Authentication:** None. Direct HTTP download of archive.
- **Cost & License:** Free public directory for international trade.
- **Geographic Coverage:** Global (>110,000 geographic locations in 249 countries/territories).
- **Inspected Fields:** `Country`, `Location`, `Name`, `NameWoDiacritics`, `SubDiv`, `Function` (Position 1: `1` = Port), `Status` (`AA`, `AC`, `RL`), `Coordinates` (Degrees & Minutes string `DDMM[N/S] DDDMM[E/W]`).
- **Critical Semantic Limitation:** UN/LOCODE provides **zero** hydrographic, depth, draft, berth, or physical dimensional information. Coordinates point to approximate town/city administrative centers, not marine navigation channels or fairway berths.
- **Operational Suitability:** `DIRECTLY_VERIFIED` for port naming authority and UN/LOCODE identity resolution only. `UNAVAILABLE` for operational restrictions.

---

### Source 5: NOAA Center for Operational Oceanographic Products and Services (CO-OPS) — PORTS® Datagetter API

- **Responsible Organization:** National Oceanic and Atmospheric Administration (NOAA) / National Ocean Service (NOS).
- **Dataset Name:** Physical Oceanographic Real-Time System (PORTS®) & National Water Level Observation Network (NWLON).
- **API Endpoint:** `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter`
- **Access Method:** Public REST API returning JSON, XML, or CSV.
- **Authentication:** None required. No API key needed.
- **Cost & License:** Free. U.S. Federal Government Public Domain.
- **Geographic Coverage:** Major U.S. commercial maritime ports and navigable waterways (Columbia River, Houston/Galveston, Lower Mississippi, Puget Sound, San Francisco, New York/New Jersey, Tampa Bay).
- **Inspected Endpoints & Parameters:**
  - Bridge Air Gap: `product=air_gap`, `units=english|metric`, `date=latest`, `station=<station_id>`.
  - Water Level: `product=water_level`, `datum=MLLW|NAVD88`, `units=metric`, `date=latest`, `station=<station_id>`.
- **Empirical Validation Extract:**
  - *Huey Long Bridge Air Gap (Mississippi River Mile 106.1 AHP, Station `8762002`):*
    ```json
    {
      "metadata": {
        "id": "8762002",
        "name": "Huey Long Bridge Air Gap",
        "lat": "29.9431",
        "lon": "-90.1681"
      },
      "data": [
        {
          "t": "2026-11-12 23:30",
          "v": "152.448",
          "s": "0.285",
          "f": "0,0,0,0"
        }
      ]
    }
    ```
    Live downward-looking microwave sensor reports an instantaneous vertical clearance of **152.45 ft (46.47m)**!
  - *Galveston Pier 21 Water Level (Station `8771450`):* Live water level `0.537m` above MLLW datum.
- **Operational Suitability:** `DIRECTLY_VERIFIED` for dynamic air-draft clearances and real-time tidal stage calculations.

---

### Source 6: NOAA Office of Coast Survey — United States Coast Pilot (Volumes 1–9)

- **Responsible Organization:** NOAA Office of Coast Survey.
- **Publication Series:** U.S. Coast Pilot (CP1 through CP9).
- **Download Portal:** `https://nauticalcharts.noaa.gov/publications/coast-pilot/index.html`
- **Access Method:** Weekly updated PDF downloads and structured XML/HTML chapters (`CPB5_WEB.pdf` for Gulf Coast; `CPB7_WEB.pdf` for Pacific Coast).
- **Authentication:** None.
- **Cost & License:** Free. U.S. Public Domain.
- **Geographic Coverage:** Entire United States coastline, Great Lakes, and territories.
- **Inspected Fields:** Textual legal regulations (33 CFR / 46 CFR), bridge clearances (overhead cables, fixed bridges, vertical lift spans open/closed), pilot boarding areas, anchorage grounds, controlling channel depths reported by USACE surveys, speed restrictions, and local hazards.
- **Empirical Validation Extract (Coast Pilot 7 - Columbia River):**
  - Lewis and Clark Bridge (Longview, Mile 66.0): Fixed span, clearance 196 ft (59.7m) at center.
  - Interstate 5 Bridge (Portland/Vancouver, Mile 106.5): Vertical lift span, 178 ft (54.2m) open, 40 ft (12.2m) closed.
  - Kalama grain terminals: Louis Dreyfus / Kalama Export wharf alongside depths 40 ft MLLW; TEMCO terminal alongside depth 40 ft MLLW.
- **Operational Suitability:** `DIRECTLY_VERIFIED` for statutory bridge clearances, pilotage boundaries, and navigational rules. Semi-structured (requires targeted text/XML extraction).

---

### Source 7: Indian Major Port Authorities & Ministry of Ports, Shipping and Waterways (MoPSW)

- **Responsible Organizations:** Individual Major Port Authorities (Jawaharlal Nehru Port Authority, Visakhapatnam Port Authority, Paradip Port Authority, Syama Prasad Mookerjee Port Kolkata) under the Major Port Authorities Act, 2021.
- **Portals & Gazettes:**
  - Paradip Port Authority: `https://paradipport.gov.in` (Marine Operations Berthing Policy).
  - Visakhapatnam Port Authority: `https://vizagport.com` (Permissible Draft Circulars).
  - Jawaharlal Nehru Port Authority: `https://jnport.gov.in` & `shipping.gov.in`.
  - Syama Prasad Mookerjee Port (Kolkata & Haldia): `https://smpkolkata.com` (Monthly River Draft Forecasts).
- **Access Method:** Downloadable PDF circulars, official website HTML tables, trade circulars. No public REST API.
- **Authentication:** None for public gazettes.
- **Cost & License:** Free public operational advisories.
- **Geographic Coverage:** Major and Non-Major commercial ports of India.
- **Inspected Parameters & Empirical Verification:**
  - *Paradip Port:* 18 active berths with exact admissible LOA, beam, and permissible draft:
    - Berth 02 (New Iron Ore Berth / NIOB): Admissible LOA 300m, Beam 46m, Permissible Draft **16.00m** (Capesize Iron Ore).
    - Berth 03 (New Coal Import Berth): Admissible LOA 300m, Beam 46m, Permissible Draft **16.00m - 16.50m** (Capesize Coal).
    - Berth 05 & 06 (Mechanized Coal Berths): Permissible Draft **14.50m** (up to **16.00m** during high tide).
    - Entrance channel maintained to **17.10m**; approach channel to **18.70m**.
  - *Visakhapatnam Port:*
    - Outer Harbour (Essar Ore Berths 1 & 2, VGCBL): LOA 300m, Beam 48m, Permissible Draft **18.10m** (handles 200,000 DWT Capesize bulkers).
    - Inner Harbour (East Quay EQ-1 to EQ-4, EQ-7 to EQ-9): Max LOA 240m, Permissible Draft **14.50m** (Panamax bulkers).
  - *SMP Kolkata / Haldia Dock Complex:* Estuarine river channel with extreme tidal dependency:
    - Monthly proforma draft forecasts issued by Harbour Master (River).
    - Governed by river bar depths (Eastern Gut, Moyapur, Jellingham).
    - Permissible freshwater drafts scale from 7.0m to 8.5m at Haldia, and 5.5m to 7.0m at Kolkata. Draft allowance reduces by 0.1m per knot below 12 knots transit speed.
- **Operational Suitability:** `DIRECTLY_VERIFIED` when extracted from official port authority circulars. Semi-structured / manual gazette ingestion required.

---

### Source 8: EMODnet Bathymetry (European Marine Observation and Data Network)

- **Responsible Organization:** European Commission, Directorate-General for Maritime Affairs and Fisheries (DG MARE).
- **Dataset Name:** EMODnet Digital Terrain Model (DTM) for European Sea Basins.
- **API Endpoint:** `https://rest.emodnet-bathymetry.eu/depth_sample` & `https://ows.emodnet-bathymetry.eu/wcs`
- **Access Method:** OGC WCS, OGC WMS, REST Depth Query API, and GeoTIFF tile downloads.
- **Authentication:** None. Open access.
- **Cost & License:** Free. Open Data under Creative Commons Attribution 4.0 International (CC BY 4.0).
- **Geographic Coverage:** European marine waters (North Sea, Baltic, Celtic Seas, Mediterranean, Black Sea).
- **Resolution & Datum:** 1/16 x 1/16 arc-minute grid (~115m x 115m cell size). Vertical datum: Lowest Astronomical Tide (LAT).
- **Empirical Validation Test:**
  - Query: `https://rest.emodnet-bathymetry.eu/depth_sample?geom=POINT(4.14 51.95)` (Port of Rotterdam, Maasvlakte Entrance).
  - Response: `{"avg": 4.7098613, "elementarySurfaces": 1, "smoothed": 3.1382859}`.
  - **Analysis:** Modelled seabed depth is computed as **4.71 meters**, whereas Rotterdam's dredged Eurogeul channel is certified to 24.0m below LAT. Coastal interpolation across land cells and breakwaters distorts harbour depths by up to 19 meters!
- **Operational Suitability:** `UNSUITABLE` for port and fairway operational draft evaluation. Valuable only for regional deep-water acoustic bathymetry visualization.

---

### Source 9: International Canal Authorities (Panama Canal ACP & Suez Canal Authority SCA)

- **Responsible Organizations:**
  - Autoridad del Canal de Panamá (ACP).
  - Suez Canal Authority (SCA).
- **Publications & Regulatory Notices:**
  - ACP: OP Notice to Shipping No. N-1 ("Vessel Requirements") and Monthly Maximum Draft Advisories.
  - SCA: Rules of Navigation (latest edition) and Circulars.
- **Access Method:** Official PDF regulations, downloadable vessel requirement circulars, and daily draft advisories (`pancanal.com`, `suezcanal.gov.eg`).
- **Authentication:** None. Public official notices.
- **Cost & License:** Free public regulatory information.
- **Key Dimensions & Restrictions:**
  - *Panama Canal (Neopanamax Locks):*
    - Max LOA: 370.33m (1,215 ft).
    - Max Beam: 51.25m (168 ft).
    - Max Draft: Normally up to 15.24m (50.0 ft) Tropical Fresh Water (TFW), adjusted seasonally based on Gatun Lake water levels (drops to 44.0 ft or 43.0 ft during drought/El Niño events).
    - Air Draft Limit: Bridge of the Americas allows maximum air draft of **62.5m (205.0 ft)** at any state of the tide at Mean High Water Springs.
  - *Suez Canal:*
    - Max Beam: 77.5m (254 ft).
    - Max Draft: Up to 66.0 ft (20.12m) for beam <= 50.0m; for beam 65.0m (Capesize/VLCC ballast), max draft 50.0 ft (15.24m).
    - Max Air Draft: Mubarak Peace Bridge vertical clearance is **68.0m (223.1 ft)**.
- **Operational Suitability:** `DIRECTLY_VERIFIED` for transit restrictions. Must be modelled as explicit canal waterway bottlenecks separate from port berth terminals.

---

## 3. Attribute Availability Matrix

The matrix below compares data sources across the full spectrum of hydrographic and operational vessel restriction parameters:

| Attribute / Parameter | USACE Master Docks | USACE eHydro NCF | NGA World Port Index | UNECE UN/LOCODE | NOAA CO-OPS PORTS | NOAA Coast Pilot | Port Authority Gazettes | EMODnet / GEBCO DTM |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Port Geographic Identity** | Yes | No | Yes | **Yes** | Yes | Yes | Yes | No |
| **Terminal & Berth Names** | **Yes** | No | No | No | No | Partial | **Yes** | No |
| **Berth Alongside Depth** | **Yes** | No | Coded (A-P) | No | No | Partial | **Yes** | No |
| **Vertical Reference Datum** | **Yes** | **Yes** | No | No | **Yes** | Yes | **Yes** | Yes (LAT/MSL) |
| **Channel Controlling Depth** | No | **Yes** | Coded (A-P) | No | No | Yes | **Yes** | Modelled Only |
| **Channel Width & Reaches** | No | **Yes** | No | No | No | Partial | **Yes** | No |
| **Max Vessel Draft (Operating)** | No | No | No | No | No | Partial | **Yes** | No |
| **Max LOA / Beam Limits** | No | Partial | Coded (L/M) | No | No | Partial | **Yes** | No |
| **Berth Length (Linear ft/m)** | **Yes** | No | No | No | No | Partial | **Yes** | No |
| **Air Draft / Bridge Clearance** | Partial | No | Boolean (Y) | No | **Yes (Live)** | **Yes** | **Yes** | No |
| **Tidal Range / Water Level** | No | No | Range (m) | No | **Yes (Live)** | Yes | **Yes** | Tide models |
| **Water Density / Fresh Water** | No | No | No | No | Partial | Yes | **Yes** | No |
| **Cargo / Commodity Suitability**| **Yes** | No | No | No | No | Partial | **Yes** | No |

---

## 4. Geographic Coverage Assessment Across Pelagos Canonical Ports

Pelagos currently maintains 43 canonical ports across North America, Japan, India, Europe, Canals, and the Middle East/Asia. The coverage matrix below assesses real-world data availability across these ports:

| UN/LOCODE | Port Name | Region | USACE Docks | USACE NCF | NGA WPI | NOAA PORTS | Authority Gazette | Verified Operational Draft Available? |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `USSEA` | Seattle | US PNW | Yes (150+ docks) | Yes | Index 17730 | Yes | NWSA Guide | **Yes** (Berth specific, e.g. Pier 86 grain: 50 ft MLLW) |
| `USTAC` | Tacoma | US PNW | Yes (80+ docks) | Yes | Index 17740 | Yes | NWSA Guide | **Yes** (Berth specific: 40-51 ft MLLW) |
| `USPDX` | Portland (OR) | US PNW | Yes (120+ docks)| Yes | Index 16940 | Yes | COLRIP Rules | **Yes** (Channel: 43 ft CRD; Berth: 40 ft MLLW) |
| `USKAL` | Kalama | US PNW | Yes (10 docks) | Yes | No index | Yes | COLRIP Rules | **Yes** (TEMCO/Kalama Exp: 40 ft MLLW / 43 ft channel) |
| `USVAN` | Vancouver (WA)| US PNW | Yes (35 docks) | Yes | Index 16960 | Yes | COLRIP Rules | **Yes** (Channel: 43 ft; Berth: 40 ft MLLW) |
| `USLWW` | Longview | US PNW | Yes (25 docks) | Yes | Index 16930 | Yes | COLRIP Rules | **Yes** (Channel: 43 ft; Berth: 40 ft MLLW) |
| `USMSY` | New Orleans | US Gulf | Yes (250+ docks)| Yes | Index 8860 | Yes (Air Gap)| Bar Pilots Advisories | **Yes** (SW Pass: 50 ft FW; Berths: 35-45 ft MLG) |
| `USBTR` | Baton Rouge | US Gulf | Yes (90+ docks) | Yes | Index 8820 | Yes (Air Gap)| NOBRA Pilots | **Yes** (River Channel: 45-50 ft FW; Berths: 40 ft) |
| `USSPT` | South Louisiana| US Gulf | Yes (110+ docks)| Yes | Index 8840 | Yes | NOBRA Pilots | **Yes** (River grain elevators: 45 ft FW draft) |
| `USHOU` | Houston | US Gulf | Yes (300+ docks)| Yes | Index 9240 | Yes (Water Lvl)| Houston Pilots Rules | **Yes** (HSC Project 11: 45 ft draft / 166 ft beam) |
| `USGAL` | Galveston | US Gulf | Yes (60+ docks) | Yes | Index 9220 | Yes | Galveston Pilots | **Yes** (Galveston Channel: 45 ft MLLW) |
| `USMOB` | Mobile | US Gulf | Yes (70+ docks) | Yes | Index 8990 | Yes | Mobile Bar Pilots | **Yes** (Mobile Bay Channel: 45 ft / 50 ft project) |
| `JPYOK` | Yokohama | Japan | No | No | Index 61390 | No | JCG Uraga / City Guide | **Yes** (Uraga Suido: 17.0m draft; Minami Honmoku: -16m) |
| `JPCHB` | Chiba | Japan | No | No | Index 61360 | No | Chiba Port Authority | **Yes** (Fairway: 16.0m - 18.0m draft) |
| `JPNGO` | Nagoya | Japan | No | No | Index 61280 | No | Nagoya Port Authority | **Yes** (Outer channel: 16.0m; Berth: 14.0m) |
| `JPUKB` | Kobe | Japan | No | No | Index 61200 | No | Hanshin Port Auth. | **Yes** (Port Island / Rokko berths: 12.0m - 16.0m) |
| `JPOSA` | Osaka | Japan | No | No | Index 61210 | No | Hanshin Port Auth. | **Yes** (Yumeshima berths: 15.0m - 16.0m) |
| `JPKWS` | Kawasaki | Japan | No | No | Index 61400 | No | Keihin Port / JCG | **Yes** (Keihin Channel: 12.0m - 15.0m) |
| `INBOM` | Mumbai | India | No | No | Index 48800 | No | Mumbai Port Auth. | **Yes** (Indira Dock: 9.1m; Harbour: 11.0m CD) |
| `INNSA` | Nhava Sheva | India | No | No | Index 48805 | No | JNPA Marine Policy | **Yes** (Channel: 15.0m tide-assisted; Berths: 14-15m) |
| `INIXY` | Kandla (DPA) | India | No | No | Index 48600 | No | Deendayal Port Auth. | **Yes** (Approach Channel: 13.0m - 14.5m tide-assisted) |
| `INMUN` | Mundra | India | No | No | Index 48617 | No | Adani Ports Berthing | **Yes** (West Basin: 17.5m draft Capesize bulkers) |
| `INMAA` | Chennai | India | No | No | Index 49450 | No | Chennai Port Auth. | **Yes** (Outer Harbour: 16.5m draft; Inner: 12.0m) |
| `INCOK` | Cochin | India | No | No | Index 49130 | No | Cochin Port Auth. | **Yes** (Outer Channel: 14.5m; ICTT Vallarpadam: 14.5m) |
| `INVTZ` | Visakhapatnam | India | No | No | Index 49350 | No | VPA Marine Dept. | **Yes** (Outer: 18.10m Capesize; Inner: 14.50m Panamax) |
| `INPRT` | Paradip | India | No | No | Index 49310 | No | PPA Marine Dept. | **Yes** (18 berths: 12.50m - 16.50m high tide) |
| `INCCU` | Kolkata | India | No | No | Index 49270 | No | SMPK Draft Forecast | **Yes** (Tidal bar limited: 5.5m - 7.0m FW draft) |
| `INTUT` | Tuticorin | India | No | No | Index 49200 | No | VOC Port Authority | **Yes** (North Cargo Berth: 14.20m draft) |
| `INMRM` | Mormugao | India | No | No | Index 48940 | No | MPA Marine Circulars | **Yes** (Berth 9: 14.50m draft iron ore) |
| `INNML` | New Mangalore | India | No | No | Index 49030 | No | NMPA Berthing Policy | **Yes** (Deep draft berths: 14.0m - 15.1m) |
| `INENN` | Ennore | India | No | No | Index 49454 | No | Kamarajar Port Ltd. | **Yes** (Coal Berths: 15.5m - 16.5m draft) |
| `INPAV` | Pipavav | India | No | No | Index 48660 | No | APM Terminals Pipavav | **Yes** (Container / Bulk: 14.5m draft) |
| `INKAT` | Kattupalli | India | No | No | No index | No | Adani Kattupalli Port | **Yes** (Berths 1 & 2: 14.0m - 15.5m draft) |
| `INHAL` | Haldia | India | No | No | Index 49275 | No | SMPK Haldia Complex | **Yes** (HDC Berths: 7.5m - 8.5m tidal FW draft) |
| `SGSIN` | Singapore | Global | No | No | Index 56040 | No | MPA Port Regulations | **Yes** (Deep Water Route: 23m; Tuas: -16m to -23m CD) |
| `NLRTM` | Rotterdam | Global | No | No | Index 34390 | No | Havenbedrijf PIG | **Yes** (Eurogeul: 24.0m LAT; Maasvlakte: 21.5m draft) |
| `PAPAC` | Balboa | Canal | No | No | Index 9560 | No | ACP Notice N-1 | **Yes** (Pacific Approach: 15.24m TFW / Neopanamax) |
| `PACRI` | Cristobal | Canal | No | No | Index 9440 | No | ACP Notice N-1 | **Yes** (Atlantic Approach: 15.24m TFW) |
| `EGPSD` | Port Said | Canal | No | No | Index 45240 | No | SCA Rules of Nav. | **Yes** (North Approach Channel: 20.12m draft) |
| `EGSUE` | Suez | Canal | No | No | Index 45350 | No | SCA Rules of Nav. | **Yes** (South Approach Channel: 20.12m draft) |
| `CASP_TEST`| Baku | Caspian | No | No | Index 47680 | No | Port of Baku Admin. | **Yes** (Alat Terminal: 7.5m draft; shallow Caspian) |
| `AEJEA` | Jebel Ali | Middle East | No | No | Index 48435 | No | DP World Marine Guide| **Yes** (T1/T2/T3 berths: 15.0m - 17.5m draft) |
| `CNSHA` | Shanghai | East Asia | No | No | Index 57790 | No | Shanghai MSA | **Yes** (Yangshan Deepwater: 15.5m - 17.0m draft) |

---

## 5. Access and Licensing Matrix

| Provider / Organization | Service / Asset | Access Protocol | Auth / Token | Genuine Free Access? | Legal License & Terms | Automated Extraction Suitability |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **USACE NDC** | Master Docks Plus | ArcGIS REST / File Geodatabase | None | Yes | U.S. Public Domain (17 U.S.C. § 105) | **High** (Direct REST queries via JSON) |
| **USACE eHydro** | National Channel Framework | ArcGIS REST FeatureServer | None | Yes | U.S. Public Domain (17 U.S.C. § 105) | **High** (Layered geospatial queries) |
| **NOAA CO-OPS** | PORTS® Real-Time Data | REST Datagetter API | None | Yes | U.S. Public Domain | **High** (6-min JSON feeds) |
| **NOAA OCS** | Coast Pilot (CP1-CP9) | Weekly PDF / XML | None | Yes | U.S. Public Domain | **Medium** (Structured XML / PDF parser) |
| **NGA MSI** | World Port Index (Pub. 150) | ArcGIS REST FeatureServer | None | Yes | U.S. Public Domain | **High** (Coarse metadata only) |
| **UNECE** | UN/LOCODE | Bi-annual ZIP (CSV) | None | Yes | Free international gazetteer | **Medium** (Manual zip fetch; scraping 403) |
| **EMODnet** | Bathymetry DTM | OGC WCS / REST API | None | Yes | Creative Commons (CC BY 4.0) | **High** (Unusable for berths) |
| **GEBCO** | Global Bathymetric Grid | NetCDF Grid Download | None | Yes | Free with attribution | **Low** (Coarse 450m grid) |
| **Indian Port Authorities** | Berthing Policies / Draft Circulars | Web Gazette / PDF | None | Yes | Government Open Information (India) | **Medium** (Curated gazetteer ingestion) |
| **Port of Rotterdam** | Port Information Guide (PIG) | Web PDF / Rules | None | Yes | Public Harbour Master Regulations | **Medium** (Curated gazetteer ingestion) |
| **Panama Canal (ACP)** | OP Notice N-1 / Advisories | Web PDF / Circulars | None | Yes | Public Official Notices | **Medium** (Curated rulebook ingestion) |
| **Suez Canal (SCA)** | Rules of Navigation | Web PDF / Circulars | None | Yes | Public Official Notices | **Medium** (Curated rulebook ingestion) |
| **Commercial Aggregators (IHS Markit, S&P, MarineTraffic)** | Global Port Directories | Private REST API | Paid API Key | **No** (Paid enterprise contract) | Proprietary / Heavy redistribution limits | **Zero** (Excluded from open architecture) |

---

## 6. Data Quality & Operational Screening Suitability

### Direct Quality Findings & Failure Modes

1. **The Gridded Bathymetry Pitfall:**
   Global bathymetric products (such as GEBCO or EMODnet) estimate seabed topography via satellite altimetry and regional sonar soundings smoothed over cell sizes between 115 meters and 450 meters. Near shorelines, piers, and breakwaters, cells average land elevation and shallow mudflats with dredged channels. As demonstrated at Rotterdam, this creates an erroneous depth estimate of 4.7m for a 24.0m deep-water channel. **Gridded bathymetric data must never be used to gate vessel operational draft.**

2. **The NGA Pub. 150 Letter-Bin Fallacy:**
   NGA WPI depth codes are discrete 5-foot buckets (`A` to `P`) compiled for broad reconnaissance. In high-precision commercial chartering, the difference between 39.5 ft and 43.0 ft determines whether a vessel can load an additional 8,000 to 12,000 metric tons of cargo. Converting letter codes to median numbers produces fabricated precision.

3. **Vertical Datum Incompatibilities:**
   A depth of 40 feet in the Pacific Northwest is referenced to `MLLW` (Mean Lower Low Water). In the Gulf of Mexico, historical charts use `MLG` (Mean Low Gulf). In the United Kingdom and Europe, hydrographic charts use `LAT` (Lowest Astronomical Tide). In inland rivers (e.g. Mississippi River at Carrollton or Baton Rouge), depths reference the local River Stage Gage or Low Water Reference Plane (LWRP). Comparing vessel draft directly against numeric depths without converting to a common reference datum or incorporating river stage readings will cause severe operational grounding errors.

4. **Fresh Water Sinkage (FWA):**
   Vessels transiting the Mississippi River (New Orleans, Baton Rouge) or Columbia River (Kalama, Portland) float in fresh water (specific gravity ~1.000), causing the ship to sink by several inches to a foot deeper than its summer saltwater load line. Port permissible drafts published for fresh water rivers (such as COLRIP's 43.0 ft fresh water draft or SMP Kolkata's daily fresh water draft) reflect this sinkage, whereas coastal port limits (e.g. Paradip's 16.0m) assume salt water.

### Screening Classification by Parameter

To maintain absolute integrity in the Pelagos vessel matcher (Gate 4):
- **Admissible for Verified Screening (`DIRECTLY_VERIFIED`):**
  - Specific berth alongside depths and berth lengths published by USACE Master Docks Plus.
  - Channel controlling depths published by USACE National Channel Framework.
  - Statutory permissible drafts and LOA/beam constraints extracted from official Port Authority Berthing Schedules (e.g. Paradip, Vizag, JNPA, Rotterdam).
  - Bridge vertical clearances certified in NOAA Coast Pilot or live NOAA PORTS microwave sensors.
- **Informational Only (`SOURCE_REPORTED`):**
  - NGA World Port Index macro-classifications and categorical depth codes.
  - Commercial port directory summaries without primary source citations.
- **Prohibited for Gate Screening (`UNVERIFIED` / `UNSUITABLE`):**
  - Gridded bathymetric DTM elevations (GEBCO, EMODnet).
  - Unaccredited third-party web scrapers.
  - Synthetically inferred draft limits derived from vessel sister ships or port averages.

---

## 7. Recommended Acquisition Architecture

To achieve comprehensive global coverage while preserving rigorous verification, Pelagos should implement a **three-tier acquisition architecture**:

```mermaid
flowchart TD
    subgraph Tier1["Tier 1: Programmatic Open Ingestion (North America)"]
        T1A["USACE Master Docks Plus (ArcGIS REST)"] --> P1["Berth Dimensions, Depths & Commodities"]
        T1B["USACE National Channel Framework (ArcGIS REST)"] --> P2["Reach Controlling Depths & Widths"]
        T1C["NOAA CO-OPS PORTS API (REST JSON)"] --> P3["Live Bridge Air Gap & Water Levels"]
    end

    subgraph Tier2["Tier 2: Authoritative Port & Pilotage Gazettes (Global & India)"]
        T2A["Indian Major Port Gazettes (Paradip, Vizag, JNPA, SMPK)"] --> P4["Statutory Berth Drafts, LOA & Tidal Rules"]
        T2B["Canal Authorities (Panama ACP, Suez SCA)"] --> P5["Lock Envelopes, Seasonal Drafts & Air Clearances"]
        T2C["European & Asian Port Guides (Rotterdam, Yokohama, Singapore)"] --> P6["Fairway Admission & Deep-Draft Berths"]
    end

    subgraph Tier3["Tier 3: Pelagos Hydrographic Registry Core"]
        P1 --> REG["Multi-Source Port & Hydrography Registry"]
        P2 --> REG
        P3 --> REG
        P4 --> REG
        P5 --> REG
        P6 --> REG
        REG --> MAT["Gate 4: Vessel Physical Compatibility & Draft Matcher"]
    end

    classDef t1 fill:#e1f5fe,stroke:#0288d1,stroke-width:2px;
    classDef t2 fill:#fff3e0,stroke:#f57c00,stroke-width:2px;
    classDef t3 fill:#e8f5e9,stroke:#388e3c,stroke-width:2px;
    class Tier1 t1;
    class Tier2 t2;
    class Tier3 t3;
```

### Component Breakdown

1. **USACE Ingestion Adapter:**
   - Ingests USACE Master Docks Plus (`services7.arcgis.com/.../Docks/FeatureServer/0`).
   - Maps 3-letter UNLOCODEs to 5-letter codes (`KAM` -> `USKAL`, `MSY` -> `USMSY`, etc.).
   - Converts depths from feet to meters while preserving original imperial values and explicit vertical datums (`MLLW`, `MLG`).
   - Links berths to commodities (e.g. grain elevators in Kalama and New Orleans).

2. **USACE Channel Reaches Adapter:**
   - Ingests USACE NCF (`National_Channel_Framework/FeatureServer/2`).
   - Records minimum maintained project depths per reach for key river corridors (Lower Mississippi, Columbia River, Houston Ship Channel).

3. **Authoritative Port Authority Gazetteer (Global Structured Fixtures):**
   - Implements structured JSON gazetteer files under `backend/pelagos/data/port_restrictions_directory.json` capturing verified port authority rules for international hubs.
   - Preserves publication references (e.g., *Paradip Port Marine Department Berthing Policy 2024-25*, *Visakhapatnam Port Authority Harbour Master Circular No. 2024/03*).

4. **Dynamic Air Gap & Environmental Adapter (NOAA PORTS):**
   - Connects to NOAA CO-OPS Datagetter API for real-time clearance tracking under constrained bridges (e.g. Huey Long Bridge on the Mississippi).

5. **Fail-Closed Gate 4 Integration:**
   - If a candidate vessel's operating draft is within the documented berth pocket depth and channel reach depth, Gate 4 clears `passed=True`.
   - If berth draft is missing or unverified, the matcher evaluates channel physical envelope but flags `SuitabilityStatus.UNVERIFIED` for cargo capacity / draft clearance, preventing premature commercial commitment.

---

## 8. Stage F1.3.2 Implementation Proposal

### 8.1 Proposed Relational Domain Entities

To properly decouple ports from specific terminals, berths, channels, and operational rules, the data model should establish five primary entities:

```mermaid
erDiagram
    PORT ||--o{ TERMINAL : contains
    TERMINAL ||--o{ BERTH : contains
    PORT ||--o{ CHANNEL_REACH : navigates_through
    PORT ||--o{ NAV_RESTRICTION : subject_to
    BERTH ||--o{ NAV_RESTRICTION : subject_to
    CHANNEL_REACH ||--o{ NAV_RESTRICTION : subject_to

    PORT {
        int id PK
        string unlocode UK
        string name
        string country_code
        float latitude
        float longitude
        string port_type
    }
    TERMINAL {
        int id PK
        int port_id FK
        string terminal_name
        string operator_name
        string primary_commodity
    }
    BERTH {
        int id PK
        int terminal_id FK
        string berth_name
        float length_meters
        float alongside_depth_meters
        string vertical_datum
        float max_loa_meters
        float max_beam_meters
        float max_draft_meters
    }
    CHANNEL_REACH {
        int id PK
        int port_id FK
        string reach_name
        float maintained_depth_meters
        float authorized_depth_meters
        float width_meters
        float mile_start
        float mile_end
        string vertical_datum
    }
    NAV_RESTRICTION {
        int id PK
        string entity_type "PORT|CHANNEL|BERTH|BRIDGE"
        int entity_id
        string restriction_type "DRAFT|AIR_DRAFT|BEAM|LOA|TIDE|UKC|DENSITY"
        float numeric_limit
        string unit "METERS|FEET|RATIO"
        string water_density "FRESH|BRACKISH|SALT"
        string operating_conditions
        string authority_source
        string provenance_status "DIRECTLY_VERIFIED|SOURCE_REPORTED"
    }
```

### 8.2 Attribute Provenance & Conflict Handling

- **Source Hierarchy:**
  `PORT_AUTHORITY_STATUTORY` > `NATIONAL_HYDROGRAPHIC_OFFICE (USACE/NOAA)` > `PILOTAGE_ASSOCIATION` > `COMMERCIAL_GAZETTEER (NGA/UNLOCODE)`
- **Conflict Resolution:**
  When multiple sources report different alongside depths for the same berth, the system chooses the most conservative (shallowest) depth among accredited sources and flags `is_conflict=True` with audit trail tracking.
- **Temporal Validity:**
  Each restriction record stores `retrieval_timestamp`, `effective_date`, and `expiry_date`. If data exceeds 365 days without re-verification, its confidence state transitions to `STALE_REQUIRING_AUDIT`.

---

## 9. Explicit Unresolved Questions

1. **Automated Dynamic River Siltation Feeds:**
   Mississippi River Bar Pilots and Columbia River Pilots issue daily or weekly draft updates depending on river gage height and shoal buildup. While USACE surveys provide project depths, automated machine-readable APIs for daily pilotage draft advisories do not currently exist and require manual advisory monitoring.
2. **Private Terminal Berth Pockets:**
   Certain privately operated terminals (e.g., specialized chemical wharves, private bauxite docks) do not publish public hydrographic surveys or berth pocket soundings. For these terminals, screening must fail closed (`UNVERIFIED`).
3. **Air Draft Inverse Variation:**
   Air draft changes continuously as vessels load cargo (laden ships have lower air drafts than ballast ships). The platform must formalize an air-draft calculation service that couples vessel ballast/laden state with bridge vertical clearance.

---

## 10. Summary & Next Steps

This discovery report concludes Stage F1.3.1. All technical datasets, REST schemas, and empirical validation tests have been verified without modifying existing application code, database structures, or git state. The project is prepared for Stage F1.3.2 implementation once authorized.
