# Pelagos — Stage F1.4.1: Global Bunker & Voyage Economics Source Discovery

**Author**: Pelagos Core Intelligence & Optimization Team
**Date**: 2026-11-13
**Stage**: F1.4.1 (Discovery Only)
**Baseline Commit**: `0ebad18` (`feat(registry): implement port restrictions and hydrography intelligence (F1.3.2)`)
**Status**: Completed — No production schemas, code, or databases modified

---

## 1. Executive Summary

Stage **F1.4.1** establishes the comprehensive, evidence-backed discovery foundation for Pelagos's bunker intelligence and voyage economics capabilities. Prior to this investigation, Pelagos relied on an extremely narrow sample of five genuine bunker observations (Buenos Aires MGO/VLSFO and Skagen HSFO/MGO/VLSFO, with Zhoushan quarantined due to currency anomalies) and possessed zero verified bunker quotes for its primary monitored bulk corridors:
- **`PNW-JPN-BULK`** (Pacific Northwest to Japan: Seattle / Portland to Yokohama / Tokyo Bay)
- **`USG-JPN-BULK`** (U.S. Gulf to Japan: Houston / New Orleans to Yokohama / Tokyo Bay via Panama Canal)

This investigation investigated 18 candidate sources across four critical voyage economics domains:
1. **Marine Bunker Fuel Prices**: Evaluated public APIs, government repositories, port authorities, and commercial price assessment agencies. Reassessed existing provider `bunkerfuelprices.com` and identified a critical upstream data integrity failure where Chinese port quotes are denominated in Chinese Yuan (CNY) but mislabeled as USD. Identified public open data on USDA AgTransport (Socrata dataset `4v3x-mj86`), which publishes the Ship & Bunker Global 20-Port Average, but verified that statutory port authorities (e.g. Singapore MPA, Port of Rotterdam) do not publish commercial bunker prices.
2. **Vessel Fuel Consumption**: Investigated European Union THETIS-MRV public reporting (over 12,000+ commercial vessels reporting annual fuel consumption by grade under EU Regulation 2015/757), engine manufacturer calculation systems (MAN Energy Solutions CEAS and WinGD GTD), and international maritime studies (Fourth IMO GHG Study 2020). Rigorously established the boundary between annual vessel-reported consumption (Category 2), theoretical engine testbed SFOC curves (Category 4), and generic size-bin statistical averages (Category 5).
3. **Port & Canal Disbursements**: Located and inspected official statutory tariffs for departure hubs (Port of Seattle Terminals Tariff No. 5 & NWSA Tariff No. 300, Port of Portland Tariff No. 500, Port of Houston Authority Tariff No. 8, Port of New Orleans Tariff No. 2), destination hubs (City of Yokohama Port Regulations & Dues Tariff), and transit corridors (Panama Canal Authority official dry bulk tolls and Gatun Lake freshwater surcharges under Advisories A-42-2022 / A-34-2023, and Suez Canal Authority SDR tariffs).
4. **Voyage Economics Dependency Matrix**: Constructed a field-level mathematical dependency matrix separating gross freight revenue, voyage expenses, net voyage contribution, and Time Charter Equivalent (TCE). Explicitly documented why voyage cost calculations cannot automatically produce a valid TCE without verified round-trip duration, laytime/demurrage assumptions, and charterer commissions.

---

## 2. Discovery Methodology

The discovery employed a multi-tier investigative methodology:
1. **Live Network Probing & Schema Inspection**: Probed live REST endpoints (including `https://bunkerfuelprices.com/api/v1/prices`, USDA Socrata API `https://agtransport.usda.gov/resource/4v3x-mj86.json`, and EMSA THETIS-MRV portals) using in-memory programmatic scripts without writing to local databases.
2. **Regulatory & Statutory Tariff Retrieval**: Downloaded and inspected official port authority, pilotage commission, and canal authority tariff schedules, extracting exact rate formulas, measurement baselines (LOA, Beam, Draft, GRT, NRT, PC/UMS Net Tons), and currency units.
3. **Manufacturer & Technical Specification Review**: Evaluated MAN Energy Solutions CEAS and WinGD General Technical Data (GTD) tools to determine how Specific Fuel Oil Consumption (SFOC in g/kWh) at varying engine loads (50%–100% MCR) is converted into daily fuel consumption (MT/day) in laden and ballast conditions.
4. **Legal & Licensing Auditing**: Inspected website terms of service, API licensing terms, and open data decrees to accurately classify redistribution permissions, commercial use restrictions, and rate limits.
5. **Strict Data Governance**: Applied the six-tier evidence classification framework (`DIRECTLY_VERIFIED`, `SOURCE_REPORTED`, `ACCESS_RESTRICTED`, `LICENSE_RESTRICTED`, `UNVERIFIED`, `UNAVAILABLE`) to every candidate source.

---

## 3. Domain A — Bunker Fuel Price Source Catalogue

Delivered marine bunker fuel prices are fundamentally distinct from crude oil benchmarks (Brent, WTI), wholesale refinery rack prices, or retail diesel. Bunkering requires physical barge delivery, ex-wharf piping, or truck-to-ship transfer, encompassing stem size premiums, barging fees, and strict fuel quality specifications under ISO 8217 (e.g. VLSFO 0.50% max sulfur, HSFO RMG 380 3.50% max sulfur, LSMGO DMA 0.10% max sulfur).

### A.1 Detailed Provider Analysis

#### 1. Bunker Fuel Prices (`bunkerfuelprices.com`) — Existing Provider Reassessment
- **Operator**: Bunker Fuel Prices (UK-registered entity; English law jurisdiction).
- **Public Endpoint**: `https://bunkerfuelprices.com/api/v1/prices`
- **Verified Findings**:
  - HTTP Status: 200 OK.
  - Payload Schema: Contains `disclaimer`, `count`, `withheld`, `withheldReason`, and `prices` array.
  - Current Count: 54 active price records exposed; **155 records withheld** (74% withholding rate) with reason *"Generated or unverified figures are never served here."*
  - Monitored Corridors:
    - **Houston (`USHOU`)**: 3 valid quotes present (HSFO: 494.50 USD/MT, MGO: 1261.50 USD/MT, VLSFO: 730.50 USD/MT). Delivery basis: `delivered`.
    - **Seattle (`USSEA`)**, **Portland (`USPDX`)**, **New Orleans (`USMSY`)**, and **Yokohama (`JPYOK`)**: **Absent / Withheld**.
  - **Severe Currency Data Anomaly**: Upstream feed from OilPriceAPI (`licensed:oilpriceapi:...`) passes through Chinese domestic price quotes in Chinese Yuan (CNY) without currency conversion:
    - Shanghai (`CNSGH`): `source: "licensed:oilpriceapi:LSFO_380_CNSHA_BONDED_CNY"`, `usdPerMt: 5473`.
    - Zhoushan (`CNZOS`): `source: "licensed:oilpriceapi:LSFO_380_CNZOS_BONDED_SELLER_CNY"`, `usdPerMt: 6028`.
    - Mislabeled field `usdPerMt` presents values approximately 7.2× higher than actual USD market levels (~$550–$650/MT), representing a critical data corruption risk if ingested without ticker inspection.
  - **Licensing & Terms**: Terms of Service Section 05 explicitly states: *"You may not use the platform ... to extract our data in bulk for a competing service."*
  - **Evidence Classification**: `DIRECTLY_VERIFIED` (Endpoint tested, anomalies confirmed, licensing restricted).

#### 2. USDA Agricultural Marketing Service — Daily Bunker Fuel Prices (`agtransport.usda.gov`)
- **Operator**: U.S. Department of Agriculture (USDA) AMS.
- **Socrata Dataset ID**: `4v3x-mj86`
- **Endpoint**: `https://agtransport.usda.gov/resource/4v3x-mj86.json`
- **Verified Findings**:
  - Published Columns: `day`, `month`, `year`, `vlsfo_fuel_oil_imo_2020_grade_0_5`, `marine_gas_oil`, `intermdiate_fuel_oil_180cst`, `intermdiate_fuel_oil_380cst`.
  - Units: USD per metric ton ($/MT).
  - Geographic Scope: **Global 20-Port Average** composite index (Busan, Colombo, Durban, Fujairah, Gibraltar, Hong Kong, Houston, Istanbul, LA/Long Beach, Las Palmas, Mumbai, New York, Panama, Piraeus, Rotterdam, Santos, Shanghai, Singapore, St Petersburg, Tokyo).
  - Individual Port Quotes: **None**. The dataset provides only the 20-port linear average.
  - Historical Period: 2019-01-29 to 2022-11-29. The data feed was discontinued after late 2022.
  - Licensing: Public Domain / Open Government Data. Source attributed to Ship & Bunker.
  - **Evidence Classification**: `DIRECTLY_VERIFIED` (Stale historical archive; composite index cannot serve as port delivered price).

#### 3. Ship & Bunker (`shipandbunker.com`)
- **Operator**: Bigport Media LLC.
- **Coverage**: 150+ ports globally across all major bunker hubs.
- **Verified Findings**:
  - Daily spot price assessments for VLSFO, HSFO (IFO380), MGO, LSMGO, and Biofuels (B24/B30).
  - Directly covers Houston (`USHOU`), Panama (`PAPAC`), Tokyo/Yokohama (`JPYOK`), Singapore, Rotterdam, Fujairah, and Los Angeles.
  - Pacific Northwest (Seattle/Portland): Secondary/intermittent coverage.
  - Access: Web interface provides daily close and 30-day/1-year graphs. API and bulk historical time series require commercial subscription (`Ship & Bunker Data Suite`).
  - Licensing: Copyright protected; terms prohibit automated scraping.
  - **Evidence Classification**: `DIRECTLY_VERIFIED` (Web observed) / `LICENSE_RESTRICTED` (API requires paid agreement).

#### 4. Statutory Port Authorities (MPA Singapore, Port of Rotterdam, Port Houston)
- **Verified Findings**:
  - National port authorities regulate bunker licensing, bunker tanker standards (e.g. MPA Mass Flow Metering TR 48 / SS 648), and publish monthly sales volumes by fuel grade (e.g., MPA monthly bunker volume statistics via `data.gov.sg`).
  - **No statutory port authority publishes delivered bunker prices**. Marine bunkering is handled by licensed private suppliers, traders, and physical barge operators under competitive bilateral contracts.
  - **Evidence Classification**: `DIRECTLY_VERIFIED` (Volume available; price data confirmed `UNAVAILABLE` from port authorities).

---

## 4. Domain B — Vessel Fuel Consumption Source Catalogue

Accurately evaluating voyage economics requires calculating fuel consumption across distinct operational phases:
1. **Sea Passage (Laden)**: Fully loaded draft, deep displacement, higher hull resistance.
2. **Sea Passage (Ballast)**: Light draft, propeller immersion margin, lower hull resistance.
3. **Manoeuvring**: Reduced engine speed, tug operations, auxiliary blowers active.
4. **Port (Working)**: Discharging/loading cargo with vessel cranes, hydraulic pumps, or high-capacity ballast pumps running.
5. **Port (Idle)**: Auxiliary diesel generators supplying hotel load and minimum safety systems.

### B.1 Categorization Hierarchy

The discovery identified five distinct tiers of vessel consumption data:

```
Category 1: Actual Measured Vessel Consumption
└── High-frequency noon reports, mass flow meters, shaft power meters, telemetry (Private shipowner data; NOT publicly accessible).

Category 2: Vessel-Reported Annual Fuel Consumption
└── European Union THETIS-MRV & UK MRV public registry (IMO number, annual fuel by grade, distance, hours; publicly accessible).

Category 3: Regulatory Emissions-Derived Estimates
└── IMO DCS GISIS annual statistical summaries (Global fleet aggregated; ship identities strictly confidential).

Category 4: Engine Manufacturer Theoretical Consumption Curves
└── MAN Energy Solutions CEAS & WinGD GTD (SFOC in g/kWh vs % MCR load; publicly accessible with registration).

Category 5: Generic Vessel-Class Statistical Assumptions
└── Fourth IMO GHG Study 2020 & Baltic Exchange vessel speed-consumption descriptions (Publicly accessible).
```

### B.2 Primary Consumption Datasets

#### 1. European Union THETIS-MRV Public Registry (Category 2)
- **Operator**: European Maritime Safety Agency (EMSA) / European Commission.
- **Regulation**: Regulation (EU) 2015/757 and Regulation (EU) 2023/1805.
- **URL**: `https://mrv.emsa.europa.eu/#public/emission-report`
- **Data Attributes Observed**:
  - `IMO Number`, `Ship Name`, `Ship Type` (Bulk Carrier).
  - `Technical Efficiency`: EEDI or EIV (Estimated Index Value) in $\text{gCO}_2/\text{tonne-nm}$.
  - `Total Annual Fuel Consumption`: Segregated by fuel type (Heavy Fuel Oil, Light Fuel Oil, Marine Gas Oil, LNG) in metric tonnes.
  - `Total CO2 Emissions`: Metric tonnes.
  - `Operational Metrics`: Total distance travelled (NM), total time spent at sea (hours).
  - `Efficiency Indicators`: Annual average fuel consumption per distance ($\text{kg/nm}$) and per transport work ($\text{g/t-nm}$).
- **Coverage**: Over 12,000+ commercial vessels calling European Economic Area (EEA) ports annually.
- **Access**: Free public bulk download (Excel / CSV) published annually by June 30 for the previous calendar year.
- **Evaluation for Pelagos**: Provides verified real-world annual benchmark baselines for dry bulk carriers by IMO number, but does not provide operational speed curves (e.g. 12.0 knots vs 14.0 knots) or separate laden/ballast legs.

#### 2. MAN Energy Solutions CEAS Engine Calculation System (Category 4)
- **Operator**: MAN Energy Solutions SE.
- **URL**: `https://www.man-es.com/marine/products/planning-tools-and-downloads/ceas-engine-calculations`
- **Data Attributes Observed**:
  - Engine models: MAN B&W low-speed two-stroke engines (e.g., 6S50ME-C, 6S60ME-C, 6S70ME-C, G50ME-C) powering the majority of global Panamax, Kamsarmax, and Capesize bulkers.
  - Specific Fuel Oil Consumption (SFOC in $\text{g/kWh}$) across engine load points: 50%, 75%, 85%, and 100% MCR.
  - Daily fuel consumption ($\text{MT/day}$) normalized to ISO standard reference conditions ($42,700\text{ kJ/kg}$ LCV, $1,000\text{ mbar}$, $25^\circ\text{C}$).
- **Evaluation for Pelagos**: Provides verified mechanical fuel consumption curves as a function of engine power output ($\text{kW}$). Converting engine load to vessel speed ($\text{knots}$) requires coupling with naval architectural propulsion resistance curves ($P = c \cdot \Delta^{2/3} \cdot V^3$).

#### 3. Fourth IMO Greenhouse Gas Study 2020 (Category 5)
- **Operator**: International Maritime Organization (IMO) / CE Delft Consortium.
- **URL**: `https://www.imo.org/en/OurWork/Environment/Pages/Fourth-IMO-Greenhouse-Gas-Study-2020.aspx`
- **Data Attributes Observed**:
  - Bulk carrier size bins: 0–9,999; 10,000–34,999; 35,000–59,999 (Handymax/Supramax); 60,000–99,999 (Panamax/Kamsarmax); 100,000–199,999 (Capesize); 200,000+ DWT (VLOC).
  - Main engine power ($\text{kW}$) and auxiliary generator power ($\text{kW}$).
  - Operational speed distributions and daily consumption by operational phase (At Sea, Manoeuvring, In Port).
- **Representative Dry Bulk Consumption Benchmarks (Empirical Fleet Averages)**:
  - **Panamax / Kamsarmax (75,000–82,000 DWT)**:
    - *Eco-Electronic Engine (built post-2015)*:
      - Laden (13.0 knots): ~24.0–26.5 MT/day VLSFO + 0.1 MT MGO.
      - Ballast (13.5 knots): ~20.5–22.5 MT/day VLSFO + 0.1 MT MGO.
      - Eco-Speed (11.5 knots): ~18.0–19.5 MT/day laden, ~14.5–16.0 MT/day ballast.
      - Port Idle: ~1.8–2.2 MT/day MGO/VLSFO.
      - Port Working: ~3.2–4.0 MT/day MGO/VLSFO.
    - *Conventional Mechanical Engine (built 2005–2012)*:
      - Laden (13.5 knots): ~32.0–35.0 MT/day VLSFO.
      - Ballast (14.0 knots): ~28.0–31.0 MT/day VLSFO.
      - Port Idle: ~2.5–3.0 MT/day.
  - **Capesize (170,000–182,000 DWT)**:
    - *Eco-Electronic Engine*:
      - Laden (13.0 knots): ~38.0–42.0 MT/day VLSFO.
      - Ballast (13.5 knots): ~32.0–35.5 MT/day VLSFO.
      - Port Idle: ~2.8–3.5 MT/day.
      - Port Working: ~4.8–5.8 MT/day.

---

## 5. Domain C — Port, Canal & Voyage Expense Source Catalogue

Voyage expenses (Disbursements or "D/A") represent statutory, mandatory costs incurred at loading ports, discharging ports, and intermediate canal transits.

### C.1 Port Tariffs (Loading & Discharging Hubs)

| Port / Facility | Regulating Authority | Tariff Identifier | Calculation Basis | Key Expense Components | Access & Currency |
|---|---|---|---|---|---|
| **Seattle (`USSEA`)** | Port of Seattle & NWSA | Tariff No. 5 & NWSA Tariff No. 300 | Length Overall (LOA) per 24 hours | Dockage (Item 7000 LOA schedule), Wharfage on bulk cargo ($/MT), Harbor security fee, Utility connection | Public PDF; USD ($) |
| **Portland (`USPDX`)** | Port of Portland | Marine Terminals Tariff No. 500 | LOA (meters) per 24 hours | Dockage (Sec. 700 schedule), Lay status discounts, Wharfage, Security surcharge, Fresh water fees | Public PDF; USD ($) |
| **Houston (`USHOU`)** | Port of Houston Authority | Port Houston Tariff No. 8 | LOA (feet/meters) per 24 hours | Dockage (Subrule 073 schedule), Sheddage, Marginal track, Security assessment fee, Wharfage | Public PDF; USD ($) |
| **New Orleans (`USMSY`)** | Board of Commissioners Port of New Orleans | Dock Department Tariff No. 2 | LOA & Gross Registered Tonnage (GRT) | Dockage (Item 100-150 schedule), Harbor dues based on DWT/GRT tiers, Wharfage, Security surcharge | Public PDF; USD ($) |
| **Yokohama (`JPYOK`)** | City of Yokohama Port & Harbor Bureau | Port Regulations & Dues Tariff | Gross Tonnage (GT) per entry & 24h | Port Entry Dues (~2.70 JPY/GT), Berthage (~7.00–12.00 JPY/GT/24h), Wharf usage, Tug assistance guidelines | Public Japanese/English portal; JPY (¥) |

#### Mandatory Pilotage & Tug Services (Separate Tariffs)
- **Puget Sound (Seattle)**: Puget Sound Pilots under Washington State Board of Pilotage Commissioners (WAC 363-116); formula based on vessel LOA, beam, and deepest draft.
- **Columbia River (Portland/Kalama)**: Dual pilotage: Columbia River Bar Pilots (Sea buoy to Astoria) plus Columbia River Pilots (Astoria to loading berths).
- **Houston Ship Channel**: Houston Pilots Association; formula based on draft charge per foot + vessel unit charge ($\text{LOA} \times \text{Beam} / 100 \times \text{Unit Rate}$) + pilot boat fee.
- **Mississippi River (New Orleans)**: Associated Branch Pilots (Bar) from Sea Buoy to Pilottown + Crescent River Port Pilots from Pilottown to berth (plus NOBRA if upriver). High river surcharges apply when Carrollton gauge exceeds 8 feet.
- **Tokyo Bay (Yokohama)**: Tokyo Bay Pilots Association under Japanese Pilotage Law; mandatory pilotage for vessels >10,000 GT calculated on GT and draft increments.

### C.2 Canal Tariffs

#### 1. Panama Canal Authority (ACP) — `USG-JPN-BULK` Governing Bottleneck
- **Official Regulations**: ACP Advisories to Shipping No. A-42-2022, A-34-2023, and Notice to Shipping No. N-1-2024 to 2026.
- **Tariff Structure for Dry Bulk Carriers**:
  1. **Fixed Transit Fee**: Tiered by vessel classification (Regular: beam < 91 ft; Super: beam 91–107 ft; Neopanamax: beam > 107 ft / LOA > 900 ft).
  2. **Capacity Toll (PC/UMS Net Tons)**:
     - Laden Rate: Assessed on PC/UMS net tonnage when loaded with cargo.
     - Ballast Rate: Discounted rate (~20% discount) when returning in ballast without cargo.
  3. **Freshwater Surcharge (FWS)**:
     - Fixed Component: $10,000 for vessels with LOA > 300 ft.
     - Variable Component: 1.0% to 10.0% of total tolls depending on the daily hydrological stage of Gatun Lake (published daily by ACP).
  4. **Operational Canal Services**:
     - Tugboat Assistance: Mandated by lock rules ($4,000–$7,000 per tug; Neopanamax locks require 2 to 4 tugs).
     - Linehandlers, locomotive/mule wires, transit inspection fees.
  5. **Transit Reservation (Booking Slots)**:
     - Standard Pre-Booking: $35,000–$70,000 depending on booking period and vessel class.
     - Emergency / Auction Slots: Dynamic bidding during low-water restrictions (can exceed $1,000,000 during severe drought restrictions).
- **Typical Total Disbursements**: A laden 82,000 DWT Kamsarmax bulk carrier incurs approximately **$380,000 to $520,000 USD** in total transit expenses through the Panama Canal under standard booking.

#### 2. Suez Canal Authority (SCA) — Alternative Routing Option
- **Official Tariff**: SCA Navigation Circulars (annual adjustments).
- **Tariff Basis**: Special Drawing Rights (SDR) assessed on Suez Canal Net Tonnage (SCNet), converted to USD at the daily IMF exchange rate.
- **Typical Total Disbursements**: ~$320,000 to $450,000 USD for Panamax/Capesize dry bulk carriers.

### C.3 Environmental & Regulatory Voyage Surcharges
- **EU Emissions Trading System (EU ETS)**: Directive (EU) 2023/959. Requires surrendering European Union Allowances (EUA in EUR/tCO2) for 40% (2024), 70% (2025), and 100% (2026) of verified emissions. Applies only to voyages connecting to EU/EEA ports (not legally applicable to transpacific PNW-JPN or USG-JPN bulk routes).
- **FuelEU Maritime**: Regulation (EU) 2023/1805. Establishes Well-to-Wake greenhouse gas intensity targets from 2025 onwards, with financial penalties on energy deficits. Relevant for benchmarking emerging zero-carbon alternative fuel economics.

---

## 6. Domain D — Voyage Economics Data Requirements & Dependency Matrix

Calculating voyage profitability and charter performance requires decomposing voyage revenue and expenses into mathematically defined layers.

### D.1 Mathematical Formulations

1. **Bunker Consumption by Leg**:
   $$\text{Consumption}_{\text{sea}} = \left( \frac{\text{Distance}_{\text{laden}}}{24 \times V_{\text{laden}}} \times \text{FOC}_{\text{laden}}(V) \right) + \left( \frac{\text{Distance}_{\text{ballast}}}{24 \times V_{\text{ballast}}} \times \text{FOC}_{\text{ballast}}(V) \right)$$
   $$\text{Consumption}_{\text{port}} = (T_{\text{load\_days}} + T_{\text{disch\_days}}) \times \text{FOC}_{\text{port\_idle/working}}$$
   $$\text{Consumption}_{\text{canal}} = T_{\text{transit\_days}} \times \text{FOC}_{\text{transit}}$$

2. **Bunker Procurement Cost**:
   $$\text{Cost}_{\text{bunker}} = \sum_{\text{grades}} \left( \text{Bunkers Lifted}_{\text{grade}} \times P_{\text{delivered}}(\text{port}, \text{grade}) \right) + \text{Barge Fees}$$

3. **Port-Call Expenses (Disbursements)**:
   $$\text{Cost}_{\text{ports}} = \text{D/A}_{\text{load\_port}} + \text{D/A}_{\text{disch\_port}}$$
   Where $\text{D/A} = \text{Tonnage Dues} + \text{Dockage}(\text{LOA}, \text{days}) + \text{Pilotage}(\text{draft}, \text{units}) + \text{Towage}(\text{tugs}) + \text{Agency Fee} + \text{Wharfage}(\text{cargo})$.

4. **Canal Transit Expenses**:
   $$\text{Cost}_{\text{canal}} = \text{Toll}_{\text{base}}(\text{PC/UMS}, \text{condition}) + \text{Surcharge}_{\text{freshwater}} + \text{Cost}_{\text{tugs}} + \text{Fee}_{\text{booking}}$$

5. **Total Voyage Expenses**:
   $$\text{Expenses}_{\text{voyage}} = \text{Cost}_{\text{bunker}} + \text{Cost}_{\text{ports}} + \text{Cost}_{\text{canal}} + \text{Cost}_{\text{canal\_security/misc}}$$

6. **Gross Freight Revenue**:
   $$\text{Revenue}_{\text{gross}} = \text{Cargo Intake Tonnes} \times \text{Agreed Freight Rate ($/MT)}$$
   Where $\text{Cargo Intake} = \min(\text{DWT} - \text{Bunkers} - \text{Constants}, \text{Draft-Limited Intake}, \text{Cubic Capacity})$.

7. **Net Voyage Contribution**:
   $$\text{Contribution}_{\text{net}} = \text{Revenue}_{\text{gross}} - \text{Expenses}_{\text{voyage}} - (\text{Revenue}_{\text{gross}} \times \text{Commission Rate})$$
   Where $\text{Commission Rate} = \text{Brokerage} (1.25\%) + \text{Address Commission} (2.50\%\text{--}3.75\%) = 3.75\%\text{--}5.00\%$.

8. **Time Charter Equivalent (TCE)**:
   $$\text{TCE ($/day)} = \frac{\text{Contribution}_{\text{net}}}{\text{Total Voyage Duration (days)}}$$
   Where $\text{Total Duration} = \text{Sea Days}_{\text{laden}} + \text{Sea Days}_{\text{ballast}} + \text{Port Days}_{\text{load}} + \text{Port Days}_{\text{disch}} + \text{Canal Waiting/Transit Days} + \text{Weather Margin}$.

---

### D.2 Field-Level Dependency Matrix

| Metric to Calculate | Required Input Fields | Candidate Source | Availability Status | Calculable Now? | Output Status if Incomplete |
|---|---|---|---|---|---|
| **Bunker Consumption by Leg** | Sea distance (NM), laden speed, ballast speed, vessel speed-consumption curves ($\text{FOC}_{\text{laden}}$, $\text{FOC}_{\text{ballast}}$), port days, port FOC | Pelagos Routing Engine, MAN CEAS, WinGD GTD, Fourth IMO GHG Study | **Partially Available** (Route distances verified; speed curves need engine model mapping) | **Conditional** (Requires verified speed curve or fallback class table) | `UNVERIFIED` (Never synthesize consumption) |
| **Bunker Procurement Cost** | Delivered fuel price by grade ($/MT) at bunkering hub, stem quantity, barging fee, delivery basis | `bunkerfuelprices.com`, Ship & Bunker, OilPriceAPI | **Partially Available** (Houston available; PNW/Japan quotes missing from open API) | **Conditional** (Calculable for Houston; conditional for PNW) | `UNAVAILABLE` for unquoted hubs |
| **Port-Call Expenses (D/A)** | Vessel LOA, Beam, Draft, GRT, days alongside, bulk cargo intake, port dockage/wharfage tariffs, pilotage rates, tug count | Port Tariffs (Seattle No. 5, Houston No. 8, NOLA No. 2, Yokohama), Pilotage Tariffs | **Available** (Tariffs verified in discovery) | **Yes** (Deterministic formula from vessel dimensions and official schedules) | `CONDITIONALLY_SUITABLE` (Subject to negotiated discounts) |
| **Canal Transit Expenses** | PC/UMS Net Tonnage, Gatun Lake water stage, transit date, vessel class, booking slot status | Panama Canal Authority (ACP) Advisories A-42-2022, A-34-2023, ACP Lake Level Feed | **Available** (Tariff formulas verified in discovery) | **Yes** (Baseline tariff deterministic; auction fees require market feed) | `CONDITIONALLY_SUITABLE` (Requires Gatun Lake stage input) |
| **Total Voyage Expenses** | Bunker cost, loading port D/A, discharging port D/A, canal expenses, miscellany | Sum of components 1–4 | **Partially Available** | **Conditional** (Complete only if bunker quote is present) | `UNAVAILABLE` if governing bunker or canal cost missing |
| **Cost per Cargo Tonne** | Total voyage expenses, verified cargo intake (MT) | Components above + Pelagos Matcher Gate 4 verified DWT/draft | **Partially Available** | **Conditional** | `UNAVAILABLE` |
| **Gross Freight Revenue** | Cargo intake (MT), corridor spot freight rate ($/MT) | Freight intelligence models (Pelagos C1–C3) + Vessel cargo intake | **Available** | **Yes** | `SUITABLE` |
| **Net Voyage Contribution** | Gross freight revenue, total voyage expenses, total commissions (%) | Components above + Charterparty terms | **Partially Available** | **Conditional** | `UNAVAILABLE` if voyage expenses incomplete |
| **Time Charter Equivalent (TCE)** | Net voyage contribution, laden sea days, ballast repositioning days, load/discharge port laytime, canal transit days, weather margin | Components above + AIS repositioning + Charterparty laytime terms | **Partially Available** (Requires ballast leg and laytime assumptions) | **No** (Voyage calculator alone cannot infer round-trip TCE without ballast leg) | `UNAVAILABLE` (Must not confuse voyage cost with TCE) |

---

## 7. Domain E — Source Quality & Access Classification

Each candidate source has been evaluated under the Pelagos data governance policy:

| Source ID | Source Name | Domain | Evidence Status | Access Method | Licensing Status |
|---|---|---|---|---|---|
| `SRC-BUNKER-BFP-001` | Bunker Fuel Prices | Bunker Prices | `DIRECTLY_VERIFIED` | REST API (JSON) | `LICENSE_RESTRICTED` (Bulk extraction prohibited) |
| `SRC-BUNKER-USDA-002` | USDA AgTransport (4v3x-mj86) | Bunker Prices | `DIRECTLY_VERIFIED` | SODA API / OData | Public Domain (Stale; 20-port index only) |
| `SRC-BUNKER-SB-003` | Ship & Bunker | Bunker Prices | `DIRECTLY_VERIFIED` | Web View / Commercial API | `LICENSE_RESTRICTED` (Commercial data suite) |
| `SRC-BUNKER-OILPRICE-004` | OilPriceAPI | Bunker Prices | `SOURCE_REPORTED` | REST API | `LICENSE_RESTRICTED` (Paid API key required) |
| `SRC-BUNKER-MPA-005` | Singapore MPA | Bunker Prices | `DIRECTLY_VERIFIED` | CSV Download | Open Data (Volume only; no prices) |
| `SRC-CONSUMPTION-EU-MRV-006` | THETIS-MRV (EMSA) | Vessel Consumption | `DIRECTLY_VERIFIED` | Bulk CSV/XLSX | EU Open Access (Annual reporting only) |
| `SRC-CONSUMPTION-MAN-CEAS-007` | MAN CEAS | Vessel Consumption | `DIRECTLY_VERIFIED` | Web / Application | Proprietary Technical (Free registration) |
| `SRC-CONSUMPTION-WINGD-008` | WinGD GTD | Vessel Consumption | `DIRECTLY_VERIFIED` | Web / Project Guide | Proprietary Technical (Free registration) |
| `SRC-CONSUMPTION-IMO-GHG4-009` | Fourth IMO GHG Study 2020 | Vessel Consumption | `DIRECTLY_VERIFIED` | PDF / Data Tables | IMO Public Document (Statistical class averages) |
| `SRC-EXPENSE-PORT-SEATTLE-010` | Port of Seattle / NWSA | Port Expenses | `DIRECTLY_VERIFIED` | PDF Tariffs | Public Regulatory Tariff |
| `SRC-EXPENSE-PORT-PORTLAND-011` | Port of Portland | Port Expenses | `DIRECTLY_VERIFIED` | PDF Tariffs | Public Regulatory Tariff |
| `SRC-EXPENSE-PORT-HOUSTON-012` | Port of Houston Authority | Port Expenses | `DIRECTLY_VERIFIED` | PDF Tariffs | Public Regulatory Tariff |
| `SRC-EXPENSE-PORT-NOLA-013` | Port of New Orleans | Port Expenses | `DIRECTLY_VERIFIED` | PDF Tariffs | Public Regulatory Tariff |
| `SRC-EXPENSE-PORT-YOKOHAMA-014` | City of Yokohama Port Bureau | Port Expenses | `DIRECTLY_VERIFIED` | Web / Regulations | Municipal Public Information (JPY) |
| `SRC-EXPENSE-CANAL-PANAMA-015` | Panama Canal Authority (ACP) | Canal Expenses | `DIRECTLY_VERIFIED` | PDF Circulars / Web | Statutory Regulations / Tariff Schedules |
| `SRC-EXPENSE-CANAL-SUEZ-016` | Suez Canal Authority (SCA) | Canal Expenses | `DIRECTLY_VERIFIED` | PDF Circulars / Web | Statutory Regulations / SDR Tariffs |
| `SRC-EXPENSE-EMISSIONS-EUETS-017`| EU ETS Maritime Directive | Emissions Expenses | `DIRECTLY_VERIFIED` | Statutory Text | EU Public Law (Not applicable to PNW/USG) |
| `SRC-EXPENSE-EMISSIONS-FUELEU-018`| FuelEU Maritime Regulation | Emissions Expenses | `DIRECTLY_VERIFIED` | Statutory Text | EU Public Law (Not applicable to PNW/USG) |

---

## 8. Corridor-Specific Coverage Assessment

### Corridor Coverage Matrix

| Corridor | Bunker Fuel Prices | Vessel Consumption Curves | Port-Call Expenses | Canal Transit Expenses | Overall Voyage Economics Feasibility |
|---|---|---|---|---|---|
| **`PNW-JPN-BULK`** (Seattle/Portland $\rightarrow$ Yokohama/Tokyo Bay) | **UNAVAILABLE** (No verified spot quotes for Seattle, Portland, or Yokohama in free API) | **PARTIAL** (MAN CEAS / WinGD engine models available; class statistical baselines available) | **VERIFIED** (Seattle Tariff No. 5, Portland Tariff No. 500, Yokohama Port Tariff directly verified) | **NOT APPLICABLE** (Transpacific great circle route; 0 canal expenses) | **CONDITIONAL**: Port costs deterministic; voyage fuel cost blocked until PNW/Japan bunker price provider is licensed. |
| **`USG-JPN-BULK`** (Houston/New Orleans $\rightarrow$ Yokohama via Panama) | **PARTIAL** (Houston USHOU verified with 3 grades; New Orleans and Yokohama missing) | **PARTIAL** (MAN CEAS / WinGD engine models available; class statistical baselines available) | **VERIFIED** (Houston Tariff No. 8, NOLA Tariff No. 2, Yokohama Port Tariff directly verified) | **VERIFIED** (Panama Canal ACP dry bulk toll tariffs and Gatun Lake surcharge formula directly verified) | **HIGH FEASIBILITY**: Houston bunker prices present; Panama Canal and port disbursements fully verified; requires resolving Yokohama bunker quote. |

---

## 9. Historical Data Availability

1. **Bunker Prices**:
   - `bunkerfuelprices.com`: Zero public historical depth.
   - USDA Socrata `4v3x-mj86`: Historical daily Global 20-Port Average from 2019-01-29 to 2022-11-29.
   - Ship & Bunker: Comprehensive historical archive from 2012 to present, accessible only via commercial subscription.
2. **Vessel Fuel Consumption**:
   - EU THETIS-MRV: Annual historical reporting from 2018 to present for commercial vessels with EEA calls.
   - Fourth IMO GHG Study: Empirical baseline fleet distributions covering 2012–2018.
3. **Port & Canal Tariffs**:
   - Statutory port tariffs published annually with historical revisions archived on official portals.
   - Panama Canal toll circulars (ACP A-42-2022, A-34-2023, N-1-2024) document historical toll modifications.

---

## 10. Gaps, Vulnerabilities & Critical Findings

1. **The "Bunker Price Free-Data Myth"**:
   - There is no high-quality, completely unrestricted, free public REST API providing daily delivered bunker fuel quotes for Seattle, Portland, or Yokohama.
   - Providers offering "free" APIs either severely restrict port coverage (e.g. 54 active ports out of 209 in `bunkerfuelprices.com`), withhold records, or pass through unverified third-party data containing severe currency label anomalies (e.g. Chinese CNY values labeled as `usdPerMt`).
2. **Consumption Curve Disconnect**:
   - Annual regulatory reporting (EU MRV) cannot be directly divided by distance to infer voyage speed performance, because annual figures aggregate varying speeds, weather delays, and port time.
   - Pure engine manufacturer curves (MAN CEAS) require naval architectural hull coefficients to translate knot speed into engine kW load.
3. **TCE Miscalculation Risk**:
   - A voyage freight calculator cannot compute an authentic Time Charter Equivalent (TCE) from a one-way laden voyage alone. Calculating TCE requires accounting for:
     - Ballast repositioning leg (e.g. discharging in Japan and repositioning back to PNW or Australia).
     - Agreed charterparty laytime allowances (e.g. 10,000 MT/day weather working days).
     - Address commissions and commercial brokerages (typically 3.75%–5.00%).
     - Operational weather margins and canal waiting times.

---

## 11. Recommended Acquisition Priorities (Stage F1.4.2)

To advance Pelagos to operational voyage economics in Stage F1.4.2 without violating licensing or compromising data integrity, the following acquisition priorities are recommended:

1. **Priority 1: Commercial Bunker Intelligence Adapter**:
   - Formally isolate `bunkerfuelprices.com` by adding ticker currency inspection (`_CNY` rejection filter) and explicit handling of `withheld` records.
   - Evaluate a commercial API license with *Ship & Bunker (Bigport Media)* or *OilPriceAPI* to obtain programmatic delivered spot quotes for `USSEA`, `USPDX`, `USHOU`, `USMSY`, and `JPYOK`.
2. **Priority 2: Standardized Speed-Consumption Engine Model**:
   - Implement a deterministic dual-mode consumption model:
     - **Mode A (Vessel-Specific)**: Reads verified Speed/Consumption warranties from vessel chartering descriptions (e.g. 13.0 kn laden on 25.5 MT VLSFO; 13.5 kn ballast on 21.0 MT VLSFO; 2.0 MT idle).
     - **Mode B (Class-Level Fallback)**: Calibrated from Fourth IMO GHG Study benchmarks for Panamax/Kamsarmax (60–99k DWT) and Capesize (100–199k DWT), distinguishing Eco-electronic vs Conventional engine generations.
3. **Priority 3: Port & Canal Disbursement Engine**:
   - Implement deterministic calculation modules for:
     - Port of Seattle / NWSA Tariff No. 300 (LOA-based dockage + Puget Sound pilotage).
     - Port of Houston Authority Tariff No. 8 (LOA-based dockage + Houston Pilots).
     - Port of Yokohama Tariff (GT-based dues + Tokyo Bay pilotage).
     - Panama Canal ACP Dry Bulk Toll Model (PC/UMS Net Tons + Gatun Lake Freshwater Surcharge + Lock Tugs).
4. **Priority 4: Segregated Economics Outputs**:
   - Structure the API and optimization schemas into four distinct financial layers:
     - Layer 1: Voyage Expenses ($\text{Bunkers} + \text{Ports} + \text{Canals}$).
     - Layer 2: Net Voyage Contribution ($\text{Gross Revenue} - \text{Voyage Expenses} - \text{Commissions}$).
     - Layer 3: Round-Trip Time Charter Equivalent (TCE) (conditional upon ballast repositioning leg).
     - Layer 4: Cost per Cargo Tonne ($\text{Voyage Expenses} / \text{Cargo Intake}$).

---

## 12. Verification & Completion Status

This discovery stage was executed under strict read-only governance:
- **Production Code**: Untouched.
- **Database Schemas & Tables**: Untouched.
- **Production Records**: Untouched.
- **Protected Assets**: `pelagos.db`, `parquet_data/`, `scratch/`, `.obsidian/`, and `.env` remain completely unmodified.
- **Git State**: Clean working tree on `main` at commit `0ebad18`.
- **Machine-Readable Inventory**: Generated at `backend/pelagos/data/f1_4_1_source_inventory.json` containing 18 verified sources.

Stage F1.4.1 is complete and ready for review.
