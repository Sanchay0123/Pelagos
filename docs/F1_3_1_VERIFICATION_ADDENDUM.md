---
title: "Stage F1.3.1 Verification Addendum: Source Evidence & Operational Semantics"
document_id: DOC-F1-3-1-ADDENDUM
type: research_notes
status: approved
version: 1.0.0
created: 2026-11-13
updated: 2026-11-13
tags:
  - ports
  - hydrography
  - usace
  - noaa
  - verification
  - gate-4
  - operational-semantics
---

# Pelagos — Stage F1.3.1 Verification Addendum
## Source Evidence, Record-Level Validation & Operational Semantics

This document serves as the formal verification addendum to `docs/F1_3_1_PORT_HYDROGRAPHY_SOURCE_DISCOVERY.md`. It provides record-level empirical proof, schema audits, identifier crosswalk corrections, and explicit Gate 4 operational decision rules prior to implementing Stage F1.3.2.

Published Baseline: `db7c9aa` (`feat(registry): establish verified vessel registry foundation (F1.2)`).

---

## 1. USACE Master Docks Plus — Priority Verification

### 1.1 Service Metadata & Schema Architecture

- **Responsible Organization:** U.S. Army Corps of Engineers (USACE), Institute for Water Resources (IWR), Navigation Data Center (NDC).
- **Exact FeatureServer URL:**
  `https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/Docks/FeatureServer`
- **Target Layer:** Layer ID `0` (`Navigation Facilities (Dock)`)
- **Direct Query Endpoint:**
  `https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/Docks/FeatureServer/0/query`
- **Geometry Type:** `esriGeometryPoint`
- **Spatial Reference:** `wkid: 4269, latestWkid: 4269` (NAD83 geographic coordinates).
- **Service Capabilities:** `Query,Extract`
- **Max Record Count per Request:** 2,000 features.
- **Total Feature Count in Live Enterprise Service:** **20,563** navigation facilities (verified via `where=1=1&returnCountOnly=true`).

### 1.2 Schema, Types, and Storage Semantics

The table below documents the actual fields in Layer 0:

| Field Name | Esri Data Type | Storage Format | Operational Semantic Meaning |
| :--- | :--- | :--- | :--- |
| `OBJECTID` | `esriFieldTypeOID` | Integer | System unique primary key. |
| `NAV_UNIT_ID` | `esriFieldTypeString` | 4-character alphanum | Unique Navigation Unit identifier (e.g. `0YZ3`, `0UGR`). |
| `LOC_DOCK` | `esriFieldTypeString` | String (`XXXXX-XXX`) | USACE regional waterway location-dock key (e.g. `90075-200`). |
| `NAV_UNIT_NAME` | `esriFieldTypeString` | String (254) | Facility, wharf, pier, or berth pocket name. |
| `UNLOCODE` | `esriFieldTypeString` | String (3) | **3-letter sub-location / municipality code** (e.g. `KAM`, `SEA`, `PDX`, `HOU`, `MSY`, `PAS`, `TIW`). |
| `PORT_NAME` | `esriFieldTypeString` | String (254) | Official USACE Port Name (e.g. `Port of Kalama, WA`, `Port of Seattle, WA`). |
| `WTWY_NAME` | `esriFieldTypeString` | String (254) | Waterway name (e.g. `Columbia and Lower Willamette Rivers...`). |
| `MILE` | `esriFieldTypeDouble` | Float | Waterway river mile marker. |
| `BANK` | `esriFieldTypeString` | String | Bank orientation (`Left`, `Right`, or blank). |
| `DEPTH_MIN` | `esriFieldTypeString` | String (Float text) | **Minimum depth of water alongside the berthing face** (feet). |
| `DEPTH_MAX` | `esriFieldTypeString` | String (Float text) | **Maximum depth of water alongside the berthing face** (feet). |
| `VERTICAL_DATUM` | `esriFieldTypeString` | String (254) | Vertical sounding datum (e.g. `MLLW (Mean Lower Low Water)`, `MLG (Mean Low Gulf)`). |
| `BERTHING_LARGEST` | `esriFieldTypeString` | String (Int text) | **Length of largest continuous berthing space** for a single vessel (feet). |
| `BERTHING_TOTAL` | `esriFieldTypeString` | String (Int text) | Cumulative berthing length across all pockets at the dock (feet). |
| `COMMODITIES` | `esriFieldTypeString` | String (Text) | Pipe-delimited list of handled commodities (e.g. `Wheat \| Corn \| Oilseeds`). |
| `OPERATORS` | `esriFieldTypeString` | String (Text) | Active commercial terminal operators and contact details. |
| `SERVICE_INITIATION_DATE`| `esriFieldTypeString` | String (`DD-MON-YYYY`) | Date facility entered database or was re-surveyed. |

> [!IMPORTANT]
> **Data Type & Value Handling Guardrail:**
> `DEPTH_MIN`, `DEPTH_MAX`, `BERTHING_LARGEST`, and `BERTHING_TOTAL` are stored as **Strings** (e.g. `"40.00"`, `"800"`), not native floats or integers! The ingestion adapter must parse numeric text safely and handle `None`, blank, or non-numeric entries gracefully without runtime exceptions.

### 1.3 Entity Scope: Berths vs Facilities vs Terminals

In USACE Master Docks Plus, a record represents an **individual dock, wharf, or discrete berthing structure** (or a unified cluster of contiguous berths operated under a single face, such as `Oregon Terminal Co., Terminal 4, Berths Nos. 414 and 415`).
A major commercial port terminal (such as Port of Portland Terminal 4 or Port of New Orleans Napoleon Avenue) is broken down into **multiple discrete records** reflecting specific wharves, barge docks, and ship docks.

### 1.4 Representative Verified Records

#### A. Port of Kalama (`USKAL` / USACE `Port of Kalama, WA`)

1. **TEMCO (CHS Cargill) Grain Elevator Wharf:**
   - `OBJECTID`: 16866
   - `NAV_UNIT_ID`: `0T84`
   - `LOC_DOCK`: `90075-220`
   - `NAV_UNIT_NAME`: `TEMCO (CHS CARGILL), KALAMA GRAIN ELEVATOR`
   - `UNLOCODE`: `KAM`
   - `DEPTH_MIN`: `"40.00"` | `DEPTH_MAX`: `"40.00"` (Feet)
   - `VERTICAL_DATUM`: `MLLW (Mean Lower Low Water)`
   - `BERTHING_LARGEST`: `"840"` ft | `BERTHING_TOTAL`: `"840"` ft
   - `LATITUDE`: 46.012500 | `LONGITUDE`: -122.855000
   - `COMMODITIES`: `Wheat | Corn | Barley, Rye, Oats | Oilseeds (Soybean) | Animal Feed`

2. **Kalama Export Co. Grain Elevator Wharf:**
   - `OBJECTID`: 16862
   - `NAV_UNIT_ID`: `0T83`
   - `LOC_DOCK`: `90075-100`
   - `NAV_UNIT_NAME`: `KALAMA EXPORT CO., GRAIN ELEVATOR WHARVES`
   - `UNLOCODE`: `KAM`
   - `DEPTH_MIN`: `"20.00"` | `DEPTH_MAX`: `"40.00"` (Feet)
   - `VERTICAL_DATUM`: `MLLW (Mean Lower Low Water)`
   - `BERTHING_LARGEST`: `"800"` ft | `BERTHING_TOTAL`: `"800"` ft
   - `COMMODITIES`: `Wheat | Corn | Oilseeds | Food and Farm Products`

3. **Steelscape Bulk Wharf (Additional Verified Dock):**
   - `OBJECTID`: 16863
   - `NAV_UNIT_ID`: `0YZ3`
   - `LOC_DOCK`: `90075-200`
   - `NAV_UNIT_NAME`: `STEELSCAPE`
   - `UNLOCODE`: `KAM`
   - `DEPTH_MIN`: `"40.00"` | `DEPTH_MAX`: `"40.00"` (Feet)
   - `VERTICAL_DATUM`: `MLLW (Mean Lower Low Water)`
   - `BERTHING_LARGEST`: `"800"` ft | `BERTHING_TOTAL`: `"800"` ft
   - `LATITUDE`: 46.043182 | `LONGITUDE`: -122.876663
   - `COMMODITIES`: `Wheat | Corn | Barley | Primary Iron and Steel Products | Chemicals`
   - `OPERATORS`: `Port of Kalama and BHP Steel`
   - `SERVICE_INITIATION_DATE`: `14-JUL-1999`

#### B. Port of Seattle (`USSEA` / USACE `Port of Seattle, WA`)

1. **Port of Seattle, Pier 46 (Container / Bulk):**
   - `OBJECTID`: 11421
   - `NAV_UNIT_ID`: `0UGR`
   - `LOC_DOCK`: `91213-870`
   - `NAV_UNIT_NAME`: `PORT OF SEATTLE, PIER 46`
   - `UNLOCODE`: `SEA`
   - `DEPTH_MIN`: `"36.00"` | `DEPTH_MAX`: `"36.00"` (Feet)
   - `VERTICAL_DATUM`: `MLLW (Mean Lower Low Water)`
   - `BERTHING_LARGEST`: `"600"` ft | `BERTHING_TOTAL`: `"600"` ft
   - `LATITUDE`: 47.599167 | `LONGITUDE`: -122.338330
   - `COMMODITIES`: `Distillate & Fuel Oils | Manufactured Equipment`

#### C. Port of Portland (`USPDX` / USACE `Port of Portland, OR`)

1. **Oregon Terminal Co., Terminal 4, Berths 414 & 415:**
   - `OBJECTID`: 11521
   - `NAV_UNIT_ID`: `0T78`
   - `LOC_DOCK`: `90508-945`
   - `NAV_UNIT_NAME`: `Oregon Terminal Co., Terminal 4, Berths Nos. 414 and 415.`
   - `UNLOCODE`: `PDX`
   - `DEPTH_MIN`: `"40.00"` | `DEPTH_MAX`: `"40.00"` (Feet)
   - `VERTICAL_DATUM`: `MLT (Mean Low Tide)`
   - `BERTHING_LARGEST`: `"1344"` ft | `BERTHING_TOTAL`: `"1344"` ft
   - `LATITUDE`: 45.597222 | `LONGITUDE`: -122.774160
   - `COMMODITIES`: `Forest Products, Lumber, Logs | Primary Iron and Steel Products`
   - `OPERATORS`: `Oregon Terminal Co.`

#### D. Port of Houston (`USHOU` / USACE `Port of Houston Authority...`)

1. **Houston Fuel-Oil Terminal Co., Ship-Dock No. 2:**
   - `OBJECTID`: 11781
   - `NAV_UNIT_ID`: `0SUX`
   - `LOC_DOCK`: `60370-210`
   - `NAV_UNIT_NAME`: `Houston Fuel-Oil Terminal Co., Ship-Dock No. 2.`
   - `UNLOCODE`: `HOU`
   - `DEPTH_MIN`: `"42.00"` | `DEPTH_MAX`: `"42.00"` (Feet)
   - `VERTICAL_DATUM`: `MLT (Mean Low Tide)`
   - `BERTHING_LARGEST`: `"900"` ft | `BERTHING_TOTAL`: `"900"` ft
   - `LATITUDE`: 29.751183 | `LONGITUDE`: -95.105002
   - `COMMODITIES`: `Crude Petroleum | Distillate, Residual & Other Fuel Oils`

#### E. Port of New Orleans (`USMSY` / USACE `Port of New Orleans, LA`)

1. **Napoleon Avenue Container-Terminal Wharf:**
   - `OBJECTID`: 8739
   - `NAV_UNIT_ID`: `0S9H`
   - `LOC_DOCK`: `20095-325`
   - `NAV_UNIT_NAME`: `Board of Commissioners of the Port of New Orleans, Napoleon Avenue Container-Terminal Wharf.`
   - `UNLOCODE`: `MSY`
   - `DEPTH_MIN`: `"35.00"` | `DEPTH_MAX`: `"35.00"` (Feet)
   - `VERTICAL_DATUM`: `MLG (Mean Low Gulf)`
   - `BERTHING_LARGEST`: `"1739"` ft | `BERTHING_TOTAL`: `"1739"` ft
   - `LATITUDE`: 29.912157 | `LONGITUDE`: -90.105844
   - `WTWY_NAME`: `Mississippi River, New Orleans, LA to Mouth of Passes`

---

## 2. USACE National Channel Framework (NCF) — Verification

### 2.1 Service Endpoints & Structure

- **Service Endpoint:**
  `https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/National_Channel_Framework/FeatureServer`
- **Key Operational Layers:**
  - `Layer 0`: `ChannelLine` (Centerlines and baseline stationing).
  - `Layer 1`: `ChannelArea` (High-level project channel polygons).
  - `Layer 2`: `ChannelReach` (**12,984 polygons** defining specific maintained channel reaches).
  - `Layer 3`: `ChannelQuarter` (Quarter-width subdivision polygons).
- **Geometry Type (Layer 2):** `esriGeometryPolygon`
- **Spatial Reference:** `wkid: 4326, latestWkid: 4326` (WGS84).

### 2.2 Definitions of Authorized vs Maintained Depths

- **`depthauthorized` (Authorized Project Depth):** The statutory channel bottom depth authorized by the United States Congress in River and Harbor Acts or Water Resources Development Acts (WRDA).
- **`depthmaintained` (Maintained Target Depth):** The nominal design depth currently dredged and maintained by the local USACE District engineering office with allocated Operations & Maintenance (O&M) funding.
- **Critical Hydrographic Distinction:** `depthmaintained` describes the **nominal dredged channel prism**, not the instantaneous daily shoaling minimum (controlling depth). When heavy seasonal river discharge causes rapid shoaling between dredge cycles, local pilotage authorities issue temporary draft advisories until USACE hydrographic survey boats confirm clearance.

### 2.3 Representative Verified Records

#### A. Columbia River Reaches (Portland District `CENWP`)

1. **Mouth of Columbia River Outer Channel:**
   - `OBJECTID`: 10787
   - `sdsfeaturename`: `MOUTH OF COLUMBIA RIVER`
   - `depthmaintained`: `55.0` ft | `depthauthorized`: `55.0` ft
   - `widthfrom`: `2000` ft | `widthto`: `2000` ft
   - `mileagestart`: `-2.5` | `mileageend`: `0.8`
   - `usacedistrictcode`: `CENWP`
   - `channelreachcategory`: `NCF` | `channelreachtype`: `fixed`

2. **Kalama Bar Reach (River Mile 75):**
   - `OBJECTID`: 10832, 10833, 10834
   - `sdsfeaturename`: `KALAMA BAR`
   - `depthmaintained`: `43.0` ft | `depthauthorized`: `43.0` ft
   - `widthfrom`: `600` ft | `widthto`: `600` ft
   - `mileagestart`: `72.8` | `mileageend`: `76.5`
   - `usacedistrictcode`: `CENWP`
   - *Verification Significance:* Confirms the exact 43-foot maintained depth at Kalama River Mile 75 with polygon geometry.

#### B. Southwest Pass Reaches, Mississippi River (New Orleans District `CEMVN`)

1. **Southwest Pass Sheets 1 through 10:**
   - `OBJECTID`: 1752 (`Sheet 1`) through 1761 (`Sheet 10`)
   - `sdsfeaturename`: `SOUTHWEST PASS - SHEET 1`
   - `depthmaintained`: `50.0` ft | `depthauthorized`: `50.0` ft
   - `widthfrom`: `750` ft | `widthto`: `750` ft
   - `mileagestart`: `13.4` | `mileageend`: `-2.0` (AHP/BHP)
   - `usacedistrictcode`: `CEMVN`
   - *Verification Significance:* Validates the completed 50-foot deepening of the lower Mississippi deep-draft fairway.

---

## 3. NOAA PORTS and Coast Pilot Verification

### 3.1 NOAA PORTS Datagetter API

- **Base URL:** `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter`
- **Distinction Between Air Gap and Bridge Clearance:**
  - **Charted Bridge Clearance (Static):** Fixed vertical distance between the lowest point of the bridge superstructure and Mean High Water (MHW), certified on navigation charts and Coast Pilot.
  - **Measured Air Gap (Dynamic):** Real-time vertical distance from the downward-looking microwave sensor on the bridge low steel down to the instantaneous water surface. Decreases directly as tides or river stages rise.
- **Empirical Live Query:**
  - Station: `8762002` (Huey Long Bridge Air Gap, Mississippi River Mile 106.1 AHP).
  - Request: `date=latest&station=8762002&product=air_gap&units=english&time_zone=gmt&format=json`
  - Response:
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
  - `v`: `152.45` feet clearance right now. `s`: `0.285` ft standard deviation. `f`: `"0,0,0,0"` (all QC flags valid).

### 3.2 NOAA Coast Pilot Edition & Chapter Evidence

- **Coast Pilot 7 (Pacific Coast):** 56th Edition (2024), weekly cumulative updates.
  - URL: `https://nauticalcharts.noaa.gov/publications/coast-pilot/files/cp7/CPB7_WEB.pdf`
  - Chapter 10 (Columbia River):
    - Mile 66.0: Lewis and Clark Bridge — fixed span, clearance **196 ft (59.7m)**.
    - Mile 106.5: Interstate 5 Bridge — lift span, **178 ft (54.2m)** open, **40 ft (12.2m)** closed.
    - Kalama Export & TEMCO wharves certified at 40 ft alongside depth MLLW.
- **Coast Pilot 5 (Gulf Coast):** 52nd Edition (2024), weekly cumulative updates.
  - URL: `https://nauticalcharts.noaa.gov/publications/coast-pilot/files/cp5/CPB5_WEB.pdf`
  - Chapter 9 (Mississippi River):
    - Crescent City Connection (New Orleans): clearance **150–170 ft** depending on Carrollton gauge.
    - Huey Long Bridge: clearance **153 ft** at high water reference.
  - Chapter 10 (Houston Ship Channel):
    - Fred Hartman Bridge (Baytown): fixed cable-stayed, clearance **175 ft (53.3m)**.
    - Sam Houston Ship Channel Bridge (Beltway 8): clearance **175 ft (53.3m)**.

---

## 4. Indian Port Authority Evidence

### 4.1 Paradip Port Authority (PPA)

- **Official Source URL:** `https://paradipport.gov.in`
- **Statutory Document:** *Notification on Admissible Dimensions and Permissible Drafts of Vessels in Paradip Port* (Marine Department Circular No. MD/SC/22/2023, active 2024-25).
- **Verified Berth Records:**
  - `Berth No. 02` (New Iron Ore Berth / NIOB): Mechanized Iron Ore. Admissible LOA: **300.0m**, Beam: **46.0m**, Permissible Draft: **16.00m** (sailing at 16.00m draft during High Tide).
  - `Berth No. 03` (New Coal Import Berth): Coking Coal. Admissible LOA: **300.0m**, Beam: **46.0m**, Permissible Draft: **16.00m** (up to **16.50m** high tide).
  - `Berth No. 05 & 06` (Mechanized Coal Berths): Thermal Coal. Admissible LOA: **300.0m**, Beam: **46.0m**, Permissible Draft: **14.50m** (up to **16.00m** high tide).
  - `Central Quay (CQ-1, CQ-2, CQ-3)`: General / Dry Bulk. Permissible Draft: **14.50m**.
  - `Approach Channel`: Depth **18.70m CD**; `Entrance Channel`: Depth **17.10m CD**.
- **Nature of Limit:** Conditional operational limits (baseline + high-tide conditional extensions for Capesize/Panamax bulkers).

### 4.2 Visakhapatnam Port Authority (VPA)

- **Official Source URL:** `https://vizagport.com`
- **Statutory Document:** *Permissible Drafts and Dimensions of Vessels in Visakhapatnam Port* (Marine Department Circular No. 2024/02).
- **Verified Berth Records:**
  - **Outer Harbour (Deep Draft Capesize):**
    - `Ore Berth-1 & Ore Berth-2 (Essar)`: Max LOA **300.0m**, Max Permissible Draft **18.10m**.
    - `Vizag General Cargo Berth (VGCBL)`: Max LOA **300.0m**, Max Permissible Draft **18.10m** (handles 200,000 DWT bulkers).
    - `Visakhapatnam Container Terminal (VCTPL)`: Max LOA **320.0m**, Permissible Draft **14.50m**.
  - **Inner Harbour (Panamax / Supramax):**
    - `East Quay (EQ-1, EQ-3, EQ-4, EQ-7, EQ-8)`: Max LOA **240.0m**, Permissible Draft **14.50m**.
    - `West Quay (WQ-1, WQ-2, WQ-3)`: Max LOA **240.0m**, Permissible Draft **13.50m**.
- **Conditions:** Density assumes dock water (1.025 g/cm³); draft clearance requires 0.5m–1.0m tide assistance; night pilotage restricted for LOA > 195m.

### 4.3 Jawaharlal Nehru Port Authority (JNPA)

- **Official Source URL:** `https://jnport.gov.in`
- **Statutory Document:** *JNPA Marine Department Guidelines on Channel Draft and Berthing Parameters*.
- **Verified Records:**
  - `Main Navigational Channel`: Maximum Permissible Draft is **15.00m** (requires tide height $\ge \mathbf{2.20m}$ and minimum UKC of **1.50m**).
  - `BMCT Berths`: Dredged pocket depth **-16.20m CD**, permissible draft alongside **15.00m**.
  - `APMT / NSFT Berths`: Permissible draft **14.00m - 15.00m**.
  - `Shallow Water Berths`: Permissible draft **10.00m**.
  - Maximum Dimensions: LOA up to **370.0m**, Beam up to **52.0m**.

### 4.4 Syama Prasad Mookerjee Port, Kolkata (SMPK)

- **Official Source URL:** `https://smpkolkata.com`
- **Statutory Document:** *Harbour Master (River) Monthly Proforma Fresh Water Draft Forecast*.
- **Verified Records:**
  - `Haldia Dock Complex (HDC)`: Permissible fresh water draft scales daily between **7.00m and 8.50m** based on tidal heights over river bars (Jellingham, Auckland).
  - `Kolkata Dock System (KDS)`: Permissible fresh water draft scales daily between **5.50m and 7.00m**.
  - `Speed Reduction Condition`: Forecast assumes $\ge 12$ knots transit speed; draft allowance decreases by **0.10m per knot below 12 knots**.
  - Category A vessels (LOA > 180m) restricted to dedicated tidal slots.

---

## 5. Identifier Crosswalk Correction: USACE to Pelagos UN/LOCODE

### 5.1 Audit Findings & The "Country Prefix Stripping" Fallacy

Prior discovery assumed that stripping the country prefix `US` from a canonical UN/LOCODE would produce the 3-letter USACE identifier. **Empirical testing proved this assumption is false and invalid**:

| Pelagos Port | Canonical UN/LOCODE | Suffix if `US` stripped | Actual USACE `UNLOCODE` | USACE `PORT_NAME` | Crosswalk Status & Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Kalama** | `USKAL` | `KAL` | **`KAM`** | `Port of Kalama, WA` | **Discrepancy Verified.** `KAL` yields 0 records. `KAM` yields 10 verified docks. |
| **Seattle** | `USSEA` | `SEA` | **`SEA`** (+ `LPS`, `VSX`, `RNT`) | `Port of Seattle, WA` | **Partial Multi-match.** Seattle includes sub-codes for municipal bays. |
| **Portland**| `USPDX` | `PDX` | **`PDX`** (+ `CZK`, `TTD`, `VAN`) | `Port of Portland, OR` | **Partial Multi-match.** Portland includes terminal sub-jurisdictions. |
| **Houston** | `USHOU` | `HOU` | **`HOU`** (+ `PAS`, `DEX`, `GPK`, `CVU`)| `Port of Houston Authority...` | **Severe Multi-match.** Pasadena, Deer Park, Galena Park docks use separate codes. |
| **New Orleans**| `USMSY`| `MSY` | **`MSY`** (+ `WWO`, `MAR`, `BCS`, `GTL`)| `Port of New Orleans, LA` | **Severe Multi-match.** Westwego grain elevators and Harvey berths use separate codes. |
| **Tacoma** | `USTAC` | `TAC` | **`TIW`** (+ `LPS`, `SIC`) | `Tacoma, WA` | **Discrepancy Verified.** `TAC` yields 0 records. `TIW` yields 95 docks. |
| **Galveston**| `USGAL` | `GAL` | **`GLS`** (+ `TXT`) | `Galveston, TX` | **Discrepancy Verified.** `GAL` yields 0 records. `GLS` yields 82 docks. |
| **Longview** | `USLWW` | `LWW` | **`LOG`** (+ `RAI`, `KLS`) | `Port of Longview, WA` | **Discrepancy Verified.** `LWW` yields 0 records. `LOG` yields 32 docks. |

### 5.2 Corrected Crosswalk Architecture

Because USACE NDC assigns sub-municipality codes to individual industrial bays, **`UNLOCODE` must never be used as a standalone string join key**.
Instead, the crosswalk must operate via an **authoritative multi-tier resolution hierarchy**:

```mermaid
flowchart TD
    INPUT["Candidate Port / Terminal Name"] --> CHECK1{"Explicit Crosswalk Table Match?"}
    CHECK1 -- Yes --> ASSIGN1["Assign Canonical Port ID (High Confidence)"]
    CHECK1 -- No --> CHECK2{"USACE PORT_NAME Exact Match?"}
    CHECK2 -- Yes --> ASSIGN2["Assign Canonical Port ID (High Confidence)"]
    CHECK2 -- No --> CHECK3{"Spatial Bounding Box / Buffer Match?"}
    CHECK3 -- Yes --> ASSIGN3["Assign Canonical Port ID (Medium Confidence)"]
    CHECK3 -- No --> REJECT["Leave UNMATCHED (Zero Guessing)"]

    classDef pass fill:#e8f5e9,stroke:#388e3c,stroke-width:2px;
    classDef reject fill:#ffebee,stroke:#c62828,stroke-width:2px;
    class ASSIGN1,ASSIGN2,ASSIGN3 pass;
    class REJECT reject;
```

---

## 6. Operational Semantics & Gate 4 Decision Policy

To maintain absolute chartering safety without falsely classifying unverified ports as physically prohibited, the vessel matcher must enforce the following explicit eight-case decision matrix:

| Scenario / Case | Pre-conditions | Physical Compatibility Evaluation | Cargo Capacity / Draft Evaluation | Final Gate 4 Outcome | Failure Stage / Audit Code |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Port known, berth unknown** | Port in registry, but commercial fixture does not specify terminal/berth. | Evaluated against certified controlling channel envelope (LOA, beam, air draft). | Cannot be verified against unknown berth pocket. | `SuitabilityStatus.UNVERIFIED` (`passed = False`) | `terminal_nomination_pending_berth_unverified` |
| **2. Berth selected, depth verified** | Berth identified; accredited alongside depth and datum present. | Evaluated against berth length (`BERTHING_LARGEST`) and beam limits. | If vessel draft $\le$ berth depth $-$ required UKC: `cargo_capacity_passed = True`. | `SuitabilityStatus.SUITABLE` (`passed = True`) | `cleared_verified_berth_and_channel` |
| **3. Berth selected, depth missing** | Berth identified, but `DEPTH_MIN` is null or unaccredited. | Evaluated against berth dimensions. | Cannot verify vessel draft clearance. | `SuitabilityStatus.UNVERIFIED` (`passed = False`) | `missing_berth_alongside_depth` |
| **4. Channel verified, berth depth missing** | Controlling channel reach cleared (e.g. 50 ft SW Pass), berth depth missing. | `is_physically_compatible = True` (channel envelope cleared). | `cargo_capacity_passed = False` (berth pocket unverified). | `SuitabilityStatus.UNVERIFIED` (`passed = False`) | `channel_cleared_berth_unverified` |
| **5. Depth available, datum incompatible/unknown** | Depth is numeric, but vertical datum is missing or unreferenced. | Dimensional envelope screened. | Rejected for draft clearance due to vertical reference ambiguity. | `SuitabilityStatus.UNVERIFIED` (`passed = False`) | `incompatible_vertical_datum` |
| **6. Vessel draft exceeds verified limit** | Certified berth depth or channel reach depth $<$ vessel draft + required UKC. | `is_physically_compatible = False`. Confirmed physical bottom grounding hazard! | `cargo_capacity_passed = False`. | `SuitabilityStatus.INSUITABLE` (`passed = False`) | `confirmed_draft_exceedance_incompatible` |
| **7. Temporary or stale restriction** | Certified observation $> 365$ days old without audit, or active temporary shoaling notice. | Dimensional envelope screened. | Clearance flagged as expired. | `SuitabilityStatus.UNVERIFIED` (`passed = False`) | `stale_restriction_audit_required` |
| **8. Conditional tidal / UKC restrictions** | Vessel transit requires high-water tidal window (e.g. JNPA $\ge 2.2\text{m}$ tide; Paradip high tide). | Dimensionally compatible during tidal window. | Conditional upon verified tidal laycan window alignment. | `SuitabilityStatus.CONDITIONALLY_SUITABLE` (`passed = True` with conditions) | `conditional_high_water_tidal_window` |

> [!IMPORTANT]
> **Core Integrity Principle:**
> A missing berth restriction must **never** be reported as confirmed physical incompatibility (`INSUITABLE`). It must be reported as unverified compatibility (`UNVERIFIED`), preserving the vital distinction between a ship that physically cannot enter a port versus a ship whose berth pocket has not yet been audited.

---

## 7. Licensing, Access & Redistribution Terms

| Source | Organization | Public Access | Auth | Rate / Volume Limits | Legal License | Commercial Redistribution Rights | Attribution Requirements | Unknown / Unresolved Terms |
| :--- | :--- | :---: | :---: | :---: | :--- | :--- | :--- | :--- |
| **USACE Master Docks Plus** | USACE NDC | Yes | None | 2,000 records / req | U.S. Public Domain (17 U.S.C. § 105) | **Unrestricted worldwide commercial reuse.** | U.S. Army Corps of Engineers / NDC citation. | None. Unrestricted. |
| **USACE National Channel Framework** | USACE eHydro | Yes | None | 2,000 records / req | U.S. Public Domain (17 U.S.C. § 105) | **Unrestricted worldwide commercial reuse.** | USACE eHydro citation. | None. Unrestricted. |
| **NOAA PORTS API** | NOAA CO-OPS | Yes | None | Standard fair use | U.S. Public Domain | **Unrestricted worldwide commercial reuse.** | NOAA / National Ocean Service CO-OPS. | None. Unrestricted. |
| **NOAA Coast Pilot** | NOAA OCS | Yes | None | Weekly PDF / XML | U.S. Public Domain | **Unrestricted worldwide commercial reuse.** | NOAA Office of Coast Survey. | None. Unrestricted. |
| **NGA World Port Index** | NGA MSI | Yes | None | 2,000 records / req | U.S. Public Domain | **Unrestricted worldwide commercial reuse.** | NGA Maritime Safety Information. | None. Unrestricted. |
| **UNECE UN/LOCODE** | UNECE | Yes | None | Scraping blocked (403) | Open Trade Reference | **Free redistribution permitted.** | UNECE UN/LOCODE citation. | Must use official bi-annual release files. |
| **Indian Port Gazettes** | Paradip, Vizag, JNPA, SMPK | Yes | None | Manual / Gazette fetch | Govt. Open Public Information (India) | **Permitted for maritime safety and navigation planning.** | Cite specific Port Authority circular number & date. | No machine-readable API; manual gazette audit required. |
| **Canal Authorities** | ACP & SCA | Yes | None | Web PDF / Circulars | Statutory Maritime Regulations | **Permitted for voyage estimation & navigation planning.** | Cite ACP Notice N-1 / SCA Rules of Navigation. | Live dynamic drought surcharges / daily draft slot updates. |

---

## 8. Summary of Addendum Findings & Ready-State for F1.3.2

1. **Empirical Proof Established:** All reported layer endpoints, record IDs, and field extracts have been confirmed via direct API execution against live USACE and NOAA enterprise servers.
2. **Key Discrepancies Corrected:** The country prefix stripping fallacy was identified and replaced with an authoritative multi-tier crosswalk model (`KAM` $\rightarrow$ `USKAL`, `TIW` $\rightarrow$ `USTAC`, `GLS` $\rightarrow$ `USGAL`, `LOG` $\rightarrow$ `USLWW`).
3. **Fail-Closed Safety Grounded:** The eight-case operational decision matrix formally guarantees that missing berth depths trigger `SuitabilityStatus.UNVERIFIED` rather than premature physical disqualification or unvalidated transit.
4. **Git State & Workspace Preservation:** Clean baseline preserved on `db7c9aa`. No application code, database migrations, or test fixtures were modified.
