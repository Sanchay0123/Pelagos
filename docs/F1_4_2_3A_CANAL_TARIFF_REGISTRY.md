---
title: "Pelagos Stage F1.4.2.3a — Canal Tariff Registry & Deterministic Rule Foundation"
document_id: "DOC-F1423A-001"
type: "architecture_spec"
status: "approved"
version: "1.0.0"
created: "2026-11-15"
updated: "2026-11-15"
author: "DeepMind Antigravity Engineering Agent"
target_stage: "Stage F1.4.2.3a (Canal Tariff Foundation)"
baseline_git_commit: "2929e61c5ddb8061ebfa9ad6c905b38b69335ef6"
tags:
  - canal_tariffs
  - panama_canal
  - suez_canal
  - currency_conversion
  - tariff_registry
  - data_governance
---

# Pelagos Stage F1.4.2.3a — Canal Tariff Registry & Deterministic Rule Foundation

## 1. Executive Summary & Purpose

Stage **F1.4.2.3a** implements the authoritative canal tariff registry, deterministic static tariff calculation engines, and currency conversion architecture for the Panama and Suez Canals, establishing the technical foundation for canal disbursement intelligence in Pelagos.

### Explicit Scope Boundaries
- **Authorized Foundation:**
  - Panama Canal tariff registry and deterministic static tariff rules (ACP Item 1010 fixed and DWT capacity fees, Item 1065 fixed freshwater surcharge, Advisory A-42-2023 variable freshwater surcharge formula).
  - Suez Canal tariff registry and deterministic static tariff rules (SCA progressive 7 SCNT brackets, Circular No. 7/2023 dry bulk surcharge, Circular No. 1/2024 mooring fee, Rules of Navigation Article 58 escort tug requirements).
  - Currency conversion architecture and data model (`CurrencyExchangeRate` and `CurrencyConversionService`) supporting statutory IMF SDR fixings, Central Bank of Egypt rates, and strict fail-closed missing date handling.
  - Effective-date, historical schedule gating, and source-governance infrastructure.
  - Comprehensive unit and integration test suite (`tests/test_canal_tariffs.py`).
- **Strictly Prohibited & Blocked:**
  - Production-ready operational canal toll calculations without certified statutory tonnage certificates (PC/UMS and SCNT).
  - Synthetic tonnage generation or mathematical proxy derivation (e.g. SCNT/GT or PC/UMS/GT ratios).
  - Complete port disbursement account (PDA) estimates.
  - Automatic integration into voyage Time Charter Equivalent (TCE) or net freight optimization engines.

---

## 2. Mandatory Architectural Invariants & Governance Rules

Stage F1.4.2.3a incorporates and strictly enforces all governance invariants mandated by the accepted F1.4.2.3 Discovery Closure (`CLOSURE-F1423-001-REV2`):

```
+---------------------------------------------------------------------------------------------------+
|                                STAGE F1.4.2.3a GOVERNANCE INVARIANTS                              |
+---------------------------------------------------------------------------------------------------+
| 1. BERGE GALDHOPIGGEN OVERALL VERDICT:                                                            |
|    - DWT (38,852 MT), LOA (179.90 m), Beam (30.00 m) remain SOURCE_REPORTED.                      |
|    - Only Gross Tonnage (25,569 GT) via Isle of Man Ship Registry is DIRECTLY_VERIFIED.           |
|    - Overall reconciliation verdict: UPHELD_SOURCE_REPORTED.                                      |
| 2. SUEZ 7 BASE BRACKETS PRESERVATION:                                                             |
|    - Preserved as PARTIALLY_VERIFIED (online matches without direct SCA register inspection).    |
|    - Individual published ballast rates (8.62, 6.58, 5.21, 1.89, 1.68, 1.58, 1.50) preserved    |
|      exactly without blanket 85% multiplier smoothing.                                            |
| 3. SUEZ SURCHARGE DECOUPLING:                                                                     |
|    - Circular No. 7/2023 (+5% dry bulk surcharge) modeled as a distinct, decoupled rule with its   |
|      own effective date (2024-01-15) and statutory citation.                                      |
| 4. STRICT PROHIBITION OF SYNTHETIC TONNAGE:                                                       |
|    - PC/UMS and SCNT are UNAVAILABLE for the seed bulkers.                                        |
|    - Zero mathematical proxy regression from GT or DWT is allowed in any context.                 |
| 5. HISTORICAL VERSION GATING:                                                                     |
|    - 2024 and 2025 tariff schedules are historical versions; 2026+ queries fail closed to         |
|      HISTORICAL_VERSION_EXPIRED.                                                                  |
| 6. PANAMA BALLAST FACTOR:                                                                         |
|    - 85% applies to BOTH the fixed fee and the capacity fee under ACP Item 1010.                   |
| 7. PANAMA VARIABLE FRESHWATER SURCHARGE (FWS):                                                    |
|    - Enforces sigmoidal formula: f(x) = 0.10 / (1 + exp(0.6 * (x - 79.0)))                       |
|    - Requires explicit 1200 hrs previous-day Gatun Lake level (x, in feet). Fails closed if missing.|
| 8. SUEZ ESCORT TUG MODELING:                                                                      |
|    - Rules of Navigation Article 58 modeled conditionally based on beam, draft, and condition,    |
|      not as arbitrary fixed financial add-ons.                                                    |
| 9. THREE-TIERED READINESS DECISION:                                                               |
|    - Level 1 (Source Registry): APPROVED.                                                         |
|    - Level 2 (Static Rules Foundation): CONDITIONALLY APPROVED.                                   |
|    - Level 3 (Operational Canal Tolls): BLOCKED / FAIL-CLOSED.                                    |
+---------------------------------------------------------------------------------------------------+
```

---

## 3. Data Model & Currency Conversion Architecture

### 3.1 Database Schemas

The relational persistence foundation is integrated into `backend/pelagos/models.py` and managed via SQLite schema migrations in `backend/pelagos/database.py`:

#### CurrencyExchangeRate Model (`currency_exchange_rates`)
| Field | Type | Description | Governance & Constraints |
|---|---|---|---|
| `id` | INTEGER | Primary Key | Auto-incrementing |
| `base_currency` | VARCHAR(3) | Base currency code | e.g. `SDR`, `USD`, `EUR` |
| `quote_currency` | VARCHAR(3) | Quote currency code | e.g. `USD`, `EGP` |
| `rate` | FLOAT | Exchange rate ($1 \text{ base} = \text{rate quote}$) | Strictly positive ($> 0$) |
| `effective_date` | DATE | Fixing / effective date | Index key |
| `rate_provider` | VARCHAR(64) | Authoritative publisher | `IMF`, `CENTRAL_BANK_OF_EGYPT`, `ECB` |
| `retrieval_timestamp`| TIMESTAMP | System ingestion timestamp | ISO-8601 UTC |
| `source_reference` | VARCHAR(255) | Official bulletin reference | Primary bulletin or statutory circular |
| `evidence_status` | VARCHAR(32) | Data evidence classification | `DIRECTLY_VERIFIED` or `PARTIALLY_VERIFIED` |

#### CanalTariffRecord Model (`canal_tariff_records`)
| Field | Type | Description | Governance & Constraints |
|---|---|---|---|
| `id` | INTEGER | Primary Key | Auto-incrementing |
| `canal_authority` | VARCHAR(16) | Canal operating authority | `ACP` (Panama), `SCA` (Suez) |
| `jurisdiction` | VARCHAR(64) | Governing sovereign jurisdiction | `PANAMA`, `EGYPT` |
| `tariff_identifier` | VARCHAR(64) | Official statutory identifier | e.g. `ACP-ITEM-1010-DS01`, `SCA-BRACKET-T1` |
| `charge_category` | VARCHAR(64) | Category of disbursement | `TOLL_CAPACITY`, `TOLL_FIXED`, `SURCHARGE`, `MOORING` |
| `rate_value` | FLOAT | Statutory rate value | Numeric rate value |
| `currency` | VARCHAR(3) | Billing currency | `USD` (Panama), `SDR` or `USD` (Suez) |
| `billing_unit` | VARCHAR(32) | Unit of assessment | `PER_DWT`, `FIXED_VESSEL`, `PER_SCNT`, `PERCENTAGE` |
| `calculation_basis` | VARCHAR(64) | Physical basis of assessment | `SUMMER_DWT`, `VESSEL_TIER`, `SCNT_PROGRESSIVE_BRACKET` |
| `loading_condition` | VARCHAR(16) | Applicable loading state | `LADEN`, `BALLAST`, `UNIVERSAL` |
| `effective_from` | DATE | Statutory validity start | Required date |
| `effective_to` | DATE | Statutory validity expiration | Required date or null for open |
| `source_url` | TEXT | Official direct document URL | Inspectable authority URL |
| `document_identifier`| VARCHAR(128) | Official statutory document | e.g. `ACP Tariff 2024`, `SCA Circular No. 7/2023` |
| `precise_source_reference` | VARCHAR(128) | Document table / page / section | Exact statutory clause |
| `evidence_classification` | VARCHAR(32) | Evidentiary rating | `DIRECTLY_VERIFIED`, `PARTIALLY_VERIFIED` |
| `permission_status` | VARCHAR(32) | Legal redistribution status | `STATUTORY_PUBLIC_LAW`, `FAIR_USE_RESEARCH_ONLY` |
| `supersession_status` | VARCHAR(32) | Current regulatory status | `CURRENT_LEGAL_FORCE`, `HISTORICAL_SUPERSEDED` |

### 3.2 Currency Conversion Service (`CurrencyConversionService`)

The Suez Canal Authority assesses transit dues strictly in International Monetary Fund (IMF) Special Drawing Rights (SDR). Under SCA Rules of Navigation Article 105, transit dues are converted to US Dollars or other convertible currencies based on official daily SDR fixings.

The `CurrencyConversionService` (`backend/pelagos/currency.py`) implements:
1. **Direct Rate Lookup:** Looks up historical fixing dates for statutory currency pairs (`SDR/USD`, `USD/EGP`, `EUR/USD`).
2. **Reciprocal Rate Derivation:** Automatically derives reciprocal conversions ($1 / \text{rate}$) when the inverse pair is registered.
3. **Identity Short-Circuit:** Returns unity ($1.0$) with zero conversion error when base equals quote.
4. **Historical Statutory Seed Data:** Pre-seeds official IMF SDR fixings for 2024 and 2025:
   - 2024-01-02: $1 \text{ SDR} = 1.3345 \text{ USD}$
   - 2024-05-01: $1 \text{ SDR} = 1.3180 \text{ USD}$
   - 2024-10-01: $1 \text{ SDR} = 1.3410 \text{ USD}$
   - 2025-01-02: $1 \text{ SDR} = 1.3050 \text{ USD}$
   - 2025-06-01: $1 \text{ SDR} = 1.3200 \text{ USD}$
5. **Strict Fail-Closed Behavior:** If no exchange rate exists for the requested transit date, the service strictly returns `is_successful=False` and `status_code="MISSING_EXCHANGE_RATE_FOR_DATE"`. It never guesses, interpolates, or defaults to $1.0$.

---

## 4. Panama Canal Deterministic Rule Foundation

The Panama Canal deterministic toll engine models the official ACP Maritime Services Tariff for non-containerized dry bulk vessels transiting either the Panamax or Neopanamax locks.

### 4.1 Vessel Tier Classification
Classification is determined by extreme beam in accordance with ACP regulations:
- **Regular (Panamax):** $\text{Beam} \le 91.0\text{ ft}$ ($27.74\text{ m}$)
- **Super (Panamax):** $91.0\text{ ft} < \text{Beam} \le 107.0\text{ ft}$ ($32.61\text{ m}$)
- **Neopanamax:** $\text{Beam} > 107.0\text{ ft}$ ($32.61\text{ m}$)

### 4.2 ACP Item 1010 Tariff Schedules (Fixed & Capacity Fees)
Item 1010 assesses a two-part tariff consisting of a fixed transit fee ($F_{\text{fixed}}$) and a deadweight capacity fee ($R_{\text{cap}} \times \text{DWT}$):

| Tier | Tariff Item (Fixed) | Tariff Item (Cap) | Fixed Fee ($) | 2024 DWT Rate ($/MT) | 2025 DWT Rate ($/MT) | Evidence Status |
|---|---|---|---|---|---|---|
| **Regular** | 1010.FR03 | 1010.DR01 | $60,000 | $1.25 | $1.65 | `DIRECTLY_VERIFIED` |
| **Super** | 1010.FS01 | 1010.DS01 | $100,000 | $1.15 | $1.50 | `DIRECTLY_VERIFIED` |
| **Neopanamax**| 1010.FN02 | 1010.DN01 | $300,000 | $0.50 | $0.80 | `DIRECTLY_VERIFIED` |

### 4.3 Ballast Treatment (ACP Article 25 & Item 1010)
For non-container dry bulk carriers transiting in ballast, the statutory **85% factor** applies to **both** components:
$$\text{Item 1010 Base Dues}_{\text{ballast}} = 0.85 \times \left( F_{\text{fixed}} + R_{\text{cap}} \times \text{DWT} \right)$$
*(Note: If calculated as individual line items, Fixed Fee is reduced by 15% and Capacity Fee is reduced by 15%, yielding an identical result).*

### 4.4 ACP Item 1065 Fixed Freshwater Surcharge
Item 1065 assesses a non-discretionary fixed fee based on Length Overall (LOA):
- $\text{LOA} \le 125.0\text{ ft}$ ($38.10\text{ m}$): **$0.00**
- $125.0\text{ ft} < \text{LOA} \le 300.0\text{ ft}$ ($91.44\text{ m}$): **$4,000.00 USD**
- $\text{LOA} > 300.0\text{ ft}$ ($91.44\text{ m}$): **$10,000.00 USD**

### 4.5 Variable Freshwater Surcharge (ACP Advisory A-42-2023)
The variable freshwater surcharge depends dynamically on the official water elevation of Gatun Lake:
- **Measurement Standard:** Certified Gatun Lake water level (in feet) measured at **1200 hours on the day prior to transit**.
- **Statutory Sigmoidal Transfer Function:**
  $$f(x) = \frac{0.10}{1 + e^{0.6 \times (x - 79.0)}}$$
  where $x$ is Gatun Lake elevation in feet above sea level.
- **Surcharge Amount:**
  $$\text{FWS}_{\text{variable}} = f(x) \times \text{Item 1010 Total Toll}$$
- **Fail-Closed Governance:** If the previous-day 1200 hrs lake level is not provided, the variable FWS cannot be computed. The model records `GATUN_LAKE_LEVEL_NOT_PROVIDED`, reports the component as `UNAVAILABLE`, and fails the overall calculation as incomplete.

### 4.6 Historical Schedule Expiration
The tariff registry contains schedules for 2024 and 2025. When queried for a transit date on or after 2026-01-01, the Panama engine refuses to project unverified tariffs and fails closed with `HISTORICAL_VERSION_EXPIRED`.

---

## 5. Suez Canal Deterministic Rule Foundation

The Suez Canal deterministic toll engine models the Suez Canal Authority (SCA) Rules of Navigation and statutory circular amendments for bulk carriers.

### 5.1 SCA 7 Progressive SCNT Tiers (Base Transit Dues)
Base transit dues are calculated on a progressive bracket structure assessed on certified Suez Canal Net Tonnage (SCNT):

| Tier | SCNT Bracket | Laden Rate (SDR/ton) | Ballast Rate (SDR/ton) | Evidence Classification |
|---|---|---|---|---|
| **Tier 1** | First 5,000 SCNT | 10.13 | 8.62 | `PARTIALLY_VERIFIED` |
| **Tier 2** | Next 5,000 SCNT (5,001 – 10,000) | 7.74 | 6.58 | `PARTIALLY_VERIFIED` |
| **Tier 3** | Next 10,000 SCNT (10,001 – 20,000) | 6.12 | 5.21 | `PARTIALLY_VERIFIED` |
| **Tier 4** | Next 20,000 SCNT (20,001 – 40,000) | 2.24 | 1.89 | `PARTIALLY_VERIFIED` |
| **Tier 5** | Next 30,000 SCNT (40,001 – 70,000) | 1.97 | 1.68 | `PARTIALLY_VERIFIED` |
| **Tier 6** | Next 50,000 SCNT (70,001 – 120,000)| 1.85 | 1.58 | `PARTIALLY_VERIFIED` |
| **Tier 7** | All tonnage over 120,000 SCNT | 1.77 | 1.50 | `PARTIALLY_VERIFIED` |

**Ballast Preservation Invariant:** The individual published ballast rates (e.g. 8.62, 6.58, 5.21) are preserved directly. Pelagos strictly prohibits applying a blanket 85% multiplication to the laden table.

### 5.2 Decoupled Circular No. 7/2023 (+5% Dry Bulk Surcharge)
SCA Circular No. 7/2023 enacted a permanent **+5% surcharge** on base transit dues for dry bulk vessels transiting the Suez Canal in either direction:
- Effective Date: **2024-01-15 onwards**.
- Calculation: $0.05 \times \text{Base Dues}_{\text{SDR}}$.
- Architectural Decoupling: Modeled as a separate rule component rather than hardcoded into the base bracket rates.

### 5.3 Decoupled Circular No. 1/2024 Mooring Fee
SCA Circular No. 1/2024 established revised charges for mooring and light dues:
- Fixed Assessment: **$3,500.00 USD** per transit.
- Modeled as a direct US Dollar charge added to the converted toll.

### 5.4 Rules of Navigation Article 58 (Escort Tug Requirements)
Article 58 specifies mandatory escort tug requirements based on vessel dimensions, draft, and loading condition:
- **Laden Condition:**
  - $\text{Beam} > 245.0\text{ ft}$ ($74.68\text{ m}$): Compulsory **2 escort tugs**.
  - $\text{Beam} > 218.0\text{ ft}$ ($66.45\text{ m}$): Compulsory **1 escort tug**.
  - $\text{SCNT} \in [70,000, 90,000]$: Compulsory **1 escort tug**.
  - $\text{Draft} > 47.0\text{ ft}$ ($14.33\text{ m}$): Compulsory **1 escort tug**.
- **Ballast Condition:**
  - $\text{Beam} > 233.0\text{ ft}$ ($71.02\text{ m}$): Compulsory **2 escort tugs**.
  - $\text{Beam} > 218.0\text{ ft}$ ($66.45\text{ m}$): Compulsory **1 escort tug**.
  - $\text{SCNT} > 130,000$: Compulsory **1 escort tug**.
- **Operational Implementation:** The deterministic rule engine evaluates vessel parameters and returns the exact number of required escort tugs and the statutory rule triggered. It does not synthesize arbitrary commercial tug tariff figures.

---

## 6. Statutory Tonnage & Governance Invariants

### 6.1 PC/UMS and SCNT Unavailability
Neither the Panama Canal Universal Measurement System (PC/UMS) net tonnage certificate nor the Suez Canal Net Tonnage (SCNT) certificate is public or inspectable for the Pelagos seed bulkers:
- `NAVIOS VICTORY` (IMO 9699311): `PC_UMS_UNAVAILABLE`, `SCNT_UNAVAILABLE`
- `IRIS BLISS` (IMO 9825489): `PC_UMS_UNAVAILABLE`, `SCNT_UNAVAILABLE`
- `BERGE GALDHOPIGGEN` (IMO 9736107): `PC_UMS_UNAVAILABLE`, `SCNT_UNAVAILABLE`
- `HADEEL` (IMO 9198642): `PC_UMS_UNAVAILABLE`, `SCNT_UNAVAILABLE`
- `BULK UNITY` (IMO 9324021): `PC_UMS_UNAVAILABLE`, `SCNT_UNAVAILABLE`
- `PACIFIC CORAL` (IMO 9528641): `PC_UMS_UNAVAILABLE`, `SCNT_UNAVAILABLE`

### 6.2 Strict Prohibition of Synthetic Tonnage
Under the governing data standard:
1. Pelagos strictly prohibits estimating statutory tonnages using proxy formulas, empirical ratios (e.g. $\text{SCNT} \approx 0.95 \times \text{GT}$ or $\text{PC/UMS} \approx 0.85 \times \text{GT}$), or regression models.
2. If SCNT is missing, the Suez Canal base bracket evaluation cannot execute. The engine returns `MISSING_CERTIFIED_SCNT`, records `SCNT_REQUIRED_FOR_SUEZ_BASE_TOLL`, and marks `is_complete=False`.
3. In operational mode (`operational_mode=True`), the engine strictly raises an exception or returns `OPERATIONAL_CALCULATION_BLOCKED`.

### 6.3 Three-Tiered Readiness Status
| Readiness Tier | Scope | Status | Enforcement Action |
|---|---|---|---|
| **Level 1** | Canal Tariff Registry Foundation | **APPROVED** | Ingest, persist, and serve authoritative static tariff records. |
| **Level 2** | Deterministic Static Tariff Rules | **CONDITIONALLY APPROVED** | Execute deterministic rule engines in research/calibration environments with explicit missing-field governance. |
| **Level 3** | Operational Canal Toll Execution | **BLOCKED / FAIL-CLOSED** | Strictly blocked from voyage TCE optimization until certified statutory certificates are acquired. |

---

## 7. Testing & Verification

A dedicated automated test suite (`tests/test_canal_tariffs.py`) validates all rules, edge cases, and safety invariants.

### 7.1 Test Coverage Summary (17 Tests)
- **Panama Canal Rules:**
  - `test_panama_tier_classification`: Boundary tests for Regular, Super, and Neopanamax beam cutoffs.
  - `test_panama_2024_rates`: Exact validation of 2024 fixed fees and DWT capacity rates across all tiers.
  - `test_panama_2025_rates`: Exact validation of 2025 fixed fees and DWT capacity rates across all tiers.
  - `test_panama_ballast_both_components`: Verifies that the 85% ballast multiplier is applied to both fixed and capacity fees.
  - `test_panama_item_1065_loa_tiers`: Verifies LOA cutoff tiers ($0, $4k, $10k).
  - `test_panama_fws_sigmoidal_formula`: Verifies formula evaluations across Gatun Lake elevations ($75\text{ ft}$, $79\text{ ft}$, $85\text{ ft}$, $90\text{ ft}$).
  - `test_panama_fws_missing_lake_level_fails_closed`: Verifies fail-closed behavior when Gatun Lake level is omitted.
  - `test_panama_historical_schedule_expiration`: Verifies that queries for 2026 fail closed.
- **Suez Canal Rules:**
  - `test_suez_progressive_bracket_boundaries`: Verifies progressive ton accumulation across all 7 SCNT brackets.
  - `test_suez_individual_ballast_rates_preserved`: Verifies exact adherence to individual published ballast bracket rates.
  - `test_suez_surcharge_and_mooring_separation`: Verifies decoupled execution of Circular No. 7/2023 (+5%) and Circular No. 1/2024 ($3,500).
  - `test_suez_article_58_escort_tugs`: Verifies escort tug triggers for beam, draft, and SCNT thresholds.
  - `test_suez_missing_scnt_fails_closed`: Verifies that omitting SCNT blocks base due calculation.
  - `test_suez_missing_currency_rate_fails_closed`: Verifies fail-closed behavior on unseeded exchange rate dates.
  - `test_suez_partially_verified_evidence_propagation`: Verifies that base bracket calculations retain `PARTIALLY_VERIFIED` classification.
- **System Governance:**
  - `test_operational_mode_blocks_incomplete_calculation`: Verifies that `operational_mode=True` blocks incomplete or missing statutory certificate runs.
  - `test_database_persistence_and_schema_compatibility`: Verifies SQLite table creation and round-trip persistence of `CanalTariffRecord` and `CurrencyExchangeRate`.

### 7.2 Test Execution Results
- `tests/test_canal_tariffs.py`: **17 passed** in 1.60s.
- `tests/test_vessel_fuel_model.py`: **19 passed** in 24.11s.
- `tests/test_engine_sfoc.py`: **9 passed** in 2.15s.
- **Combined Test Status:** **45 passed, 0 failed, 0 regressions**.

---

## 8. Traceability & Regulatory Reference Matrix

| Authority | Document / Circular | Statutory Reference | Pelagos Implementation Component | Evidence Status |
|---|---|---|---|---|
| **ACP** | Maritime Services Tariff (2024/2025) | Item 1010.FR03 / DR01 | `CanalTariffRegistry._calc_panama_item_1010` (Regular) | `DIRECTLY_VERIFIED` |
| **ACP** | Maritime Services Tariff (2024/2025) | Item 1010.FS01 / DS01 | `CanalTariffRegistry._calc_panama_item_1010` (Super) | `DIRECTLY_VERIFIED` |
| **ACP** | Maritime Services Tariff (2024/2025) | Item 1010.FN02 / DN01 | `CanalTariffRegistry._calc_panama_item_1010` (Neopanamax)| `DIRECTLY_VERIFIED` |
| **ACP** | Maritime Services Tariff Regulations | Article 25 | `CanalTariffRegistry.calculate_panama_toll` (85% Ballast)| `DIRECTLY_VERIFIED` |
| **ACP** | Maritime Services Tariff (2024/2025) | Item 1065.0001 | `CanalTariffRegistry._calc_panama_item_1065` | `DIRECTLY_VERIFIED` |
| **ACP** | Advisory to Shipping No. A-42-2023 | Section 1 & 2 | `CanalTariffRegistry.evaluate_panama_variable_fws_rate` | `DIRECTLY_VERIFIED` |
| **SCA** | Rules of Navigation & Tariff Schedule | Dry Bulk 7 Tiers Table | `CanalTariffRegistry.calculate_suez_toll` (Progressive) | `PARTIALLY_VERIFIED`|
| **SCA** | Circular No. 7/2023 | Surcharge for Dry Bulk Vessels | `CanalTariffRegistry.calculate_suez_toll` (+5% Surcharge)| `DIRECTLY_VERIFIED` |
| **SCA** | Circular No. 1/2024 | Mooring and Light Dues | `CanalTariffRegistry.calculate_suez_toll` ($3,500 Mooring)| `DIRECTLY_VERIFIED` |
| **SCA** | Rules of Navigation (Dec 2020) | Chapter 3, Sec III, Art 58| `CanalTariffRegistry.evaluate_suez_escort_tug_requirement` | `DIRECTLY_VERIFIED` |
| **SCA** | Rules of Navigation (Dec 2020) | Chapter 5, Art 105 | `CurrencyConversionService` (SDR/USD Conversion) | `DIRECTLY_VERIFIED` |

---

## 9. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-11-15 | DeepMind Antigravity Engineering Agent | Initial foundation release for Stage F1.4.2.3a canal tariff registry, deterministic rules, currency conversion architecture, and statutory data governance. |

