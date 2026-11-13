---
title: Marine Bunker Fuel Intelligence & Data Quality Specification
document_id: SPEC-BUNKER-001
type: specification
status: approved
version: 1.1.0
created: 2026-11-13
updated: 2026-11-13
tags:
  - bunker
  - fuel
  - validation
  - data-quality
  - routing
  - economics
---

# Marine Bunker Fuel Intelligence & Data Quality Specification

## 1. Purpose and Scope

### 1.1 Purpose
This specification establishes the architectural, validation, and data quality standards governing marine bunker fuel price acquisition, normalization, quarantine isolation, and intelligence fusion within the Pelagos maritime analytics platform.

### 1.2 Scope
- **Governed Operations:** Public API consumption from bunker pricing feeds (specifically `bunkerfuelprices.com`), currency verification, unit validation, ISO 8217 fuel grade normalization, port identity mapping, corridor hub availability tracking, observation freshness evaluation, provider withholding accounting, durable quarantine persistence, and idempotent storage.
- **Out of Scope:** Commercial data purchasing, automated currency FX conversion (without audited live rates), synthetic price imputation or interpolation, and physical bunker procurement/trading execution.

---

## 2. Provider Terms, Permitted Use & Licensing Compliance

### 2.1 Authorized Source Feed
Pelagos acquires live indicative spot bunker prices from the publicly accessible API endpoint:
- **Base Provider:** Bunker Fuel Prices (`https://bunkerfuelprices.com/`)
- **Endpoint:** `GET https://bunkerfuelprices.com/api/v1/prices`
- **Payload Schema:** JSON object containing `count` (integer), `withheld` (integer), `withheldReason` (string), and `prices` (array of price observation objects).

### 2.2 Terms of Service and Permitted Use Analysis
During the F1.4.1 discovery and F1.4.2.1 verification audits, the provider's published terms of service and website policies were inspected:
- **Section 05 Restriction:** The terms explicitly state: *"You may not use the platform ... to extract our data in bulk for a competing service."*
- **Automated API Retrieval & Repeated Polling:** The public `/api/v1/prices` endpoint does not require an API key, but automated scheduled bulk extraction or rapid periodic polling without an explicit license agreement is restricted.
- **Retention of Observations:** Upstream data is derived from licensed commercial feeds (e.g., OilPriceAPI `licensed:oilpriceapi`). Retaining proprietary spot indications in permanent operational databases without an explicit commercial license exceeds public evaluation rights.
- **Internal Processing & Display:** Internal research, evaluation, and offline development benchmarking are acceptable; operational redistribution or display in commercial shipping workflows requires provider authorization.
- **Competing Service Consideration:** Pelagos provides maritime freight forecasting and vessel voyage optimization, which overlaps with commercial maritime intelligence platforms.

### 2.3 Integration Governance Status: `PENDING_PROVIDER_PERMISSION` & Offline Fixture Governance
In accordance with Pelagos's truth-in-status principles:
- Authorization is **never inferred** merely because an HTTP endpoint is publicly accessible without authentication.
- The live acquisition integration is formally tagged with `licensing_status: "PENDING_PROVIDER_PERMISSION"`.
- Production and automated workflows must operate under strict offline fixture governance:
  - **No Exemption for Offline Fixtures:** Storing provider-derived records in local files (`genuine_bunker_prices.json`) does not bypass provider restrictions or grant operational usage rights.
  - **Data Governance Role:** Retained offline fixtures serve strictly as `HISTORICAL_AUDIT_EVIDENCE` for pipeline validation, idempotency testing, and forensic audit.
  - **Operational Isolation:** Restricted provider-derived observations are strictly **barred from entering operational bunker-price calculations**. When callers invoke corridor bunker resolvers in operational mode (`operational_mode=True`), all bunkering hubs report `status: "unavailable"`, `operational_availability: "UNAVAILABLE"`, and `operational_use_permitted: False`.
  - **Audit Preservation:** The 5 valid historical observations and 1 quarantined observation in `genuine_bunker_prices.json` are preserved strictly unaltered as historical audit evidence.
- Bypass mechanisms, headless scraping, or header spoofing to circumvent provider rate gates or terms are strictly prohibited.

---

## 3. Explicit Currency Integrity & Ticker Inspection

### 3.1 The Multi-Currency Provider Anomaly
Upstream aggregators ingest quotations from regional feeds. In certain Chinese bonded and domestic bunker terminals (e.g., Zhoushan `CNZOS` and Shanghai `CNSGH`), quotes are reported in local currency (Chinese Yuan / RMB, ticker suffix `_CNY`), yet the payload schema designates the field as `usdPerMt`.
Consuming such records blindly as USD creates a catastrophic ~7.2× valuation error (e.g., reporting 6,028.00 USD/MT instead of 6,028.00 CNY/MT $\approx$ 830 USD/MT).

### 3.2 Ticker & Currency Inspection Rules
1. **Ticker Currency Suffix Inspection:**
   - Any record whose `source` ticker contains `_CNY` or `_RMB` is explicitly identified as denominated in Chinese Yuan.
   - If the stated currency is labeled USD (or field `usdPerMt` is populated) while the source indicates CNY/RMB, the record is immediately quarantined with code `CURRENCY_CONTRADICTION`.
2. **Unsupported Foreign Tickers:**
   - Tickers indicating foreign currencies (`_EUR`, `_SGD`, `_JPY`, `_GBP`, `_AUD`, `_CAD`, `_CHF`, `_KRW`, `_INR`) without explicit, verified automated conversion rates are quarantined (`UNSUPPORTED_TICKER_CURRENCY`).
3. **Missing or Conflicting Currency Metadata:**
   - Observations lacking clear currency attributes or presenting non-USD denominations without FX conversion metadata are quarantined (`MISSING_CURRENCY_DENOMINATION` or `CONFLICTING_CURRENCY_METADATA`).

---

## 4. Physical Units & Price Sanity Checks

### 4.1 Mass-Based Marine Bunker Standard
Marine bunker contracts for ocean-going bulk vessels are traded and consumed on a mass basis: **USD per Metric Ton (USD/MT)**.

1. **Accepted Units:**
   - `USD/MT`, `USD/T`, `$/MT`.
2. **Rejected Volumetric Units:**
   - Volumetric units (`USD/BBL`, `USD/GAL`, `USD/M3`) are strictly rejected (`VOLUMETRIC_UNIT_NOT_SUPPORTED`). Marine fuels vary substantially in density (e.g., VLSFO $\approx 890\text{--}920\text{ kg/m}^3$, HSFO $\approx 960\text{--}991\text{ kg/m}^3$, MGO $\approx 820\text{--}860\text{ kg/m}^3$). Converting barrels or gallons to metric tons requires an exact, certified standard density hydrometer measurement at $15^\circ\text{C}$ (ISO 3675/12185), which is absent in spot indication feeds.

### 4.2 Comprehensive Price Category Delineation
The validation pipeline rigorously distinguishes six price conditions:
1. **Structurally Invalid Prices:** Missing price values, nulls, or non-numeric strings (`"N/A"`, `"NaN"`) trigger immediate quarantine with code `STRUCTURAL_INVALID_PRICE`.
2. **Non-Positive Prices:** Numeric values $\le 0.0$ USD/MT trigger quarantine with code `NON_POSITIVE_PRICE`.
3. **Currency & Unit Contradictions:** Quarantined under `CURRENCY_CONTRADICTION` or `VOLUMETRIC_UNIT_NOT_SUPPORTED`.
4. **Standard Market Prices ($150 to $3,000/MT):** Fully valid; ingested directly into canonical observations without warnings.
5. **Unusual but Potentially Genuine Market Prices ($50–$150/MT and $3,000–$5,000/MT):** Validated and ingested into canonical observations, but flagged with warning code `UNUSUAL_PRICE_LEVEL (ANOMALY_WARNING)` in `provenance_notes`. This accommodates niche certified biofuels (B100), cryogenic LNG, or severe remote supply shortages without losing genuine market observations.
6. **Extreme Prices Requiring Manual Review (> $5,000/MT or < $50/MT):** Quarantined with code `EXTREME_PRICE_REQUIRES_MANUAL_REVIEW`. Rather than irreversible silent dropping, these records are durably persisted in `quarantined_records` with their full raw payload and reason for manual review.

> [!NOTE]
> **Operational Review Ceiling Justification ($5,000/MT):**
> In modern commercial maritime bunkering, conventional compliant marine fuel (HSFO, VLSFO, MGO) trades between $250 and $1,200/MT. Even during historical energy supply crises (2008, 2022), delivered MGO spikes at remote bunkering locations peaked around $1,900/MT. Advanced drop-in biofuels trade at $2,000–$3,500/MT. A quotation exceeding $5,000/MT in conventional spot indication feeds almost invariably represents currency confusion (e.g., domestic JPY or CNY values), volumetric scaling errors, or typographical errors (cents vs dollars). Quarantining under `EXTREME_PRICE_REQUIRES_MANUAL_REVIEW` preserves the source evidence for audit while protecting chartering algorithms against cost explosion.

---

## 5. Fuel Specification Semantics & Regulatory Context Demarcation

### 5.1 Strict Conceptual Separation (Six Disentangled Concepts)
The validation pipeline strictly enforces boundaries between six distinct concepts to prevent false specification inference and invalid compliance assertions:
1. **Source-Reported Sulfur Content (`source_reported_sulfur_pct`):** Laboratory-measured or supplier-warranted sulfur percentage from a Certificate of Quality or Bunker Delivery Note (e.g., `2.45%`). Defaults strictly to `None` if absent in raw payload.
2. **Canonical Fuel Grade Designation (`canonical_fuel_grade`):** High-level commercial category identifier (`VLSFO`, `HSFO`, `MGO`, `MDO`, `ULSFO`, `B24`, `B30`, `LNG`).
3. **ISO 8217 Specification (`nominal_max_sulfur_spec` & `nominal_iso_reference`):** Standard commercial benchmark ceiling under ISO 8217 (e.g., RMG 380 nominal max 3.50% S, RMG/RME nominal max 0.50% S, DMA nominal max 0.10% S).
4. **Global MARPOL Sulfur Limit (`global_marpol_sulfur_limit_pct`):** Statutory ceiling of $0.50\%$ S under MARPOL Annex VI Regulation 14.1 (effective 1 January 2020).
5. **Applicable ECA Sulfur Limit (`applicable_eca_sulfur_limit_pct`):** Statutory ceiling of $0.10\%$ S under MARPOL Annex VI Regulation 14.4 (effective 1 January 2015) in designated Emission Control Areas (Baltic Sea, North Sea, North American ECA, US Caribbean Sea, and Mediterranean Sea SOx-ECA).
6. **Vessel Compliance Arrangements (`compliance_arrangement` & `scrubber_equivalence_applicable`):** Operational equivalence mechanisms, such as an approved Exhaust Gas Cleaning System (EGCS / scrubber) certified under MARPOL Annex VI Regulation 4.

### 5.2 Prohibition of Universal Regulatory Limits on Fuel Grades
Assigning a single universal regulatory limit solely from a canonical fuel grade is domain-invalid:
- **VLSFO ($0.50\%$ S):** Complies with the global MARPOL cap, but is **strictly non-compliant** within ECAs unless the vessel operates an approved EGCS.
- **HSFO ($3.50\%$ S):** Exceeds the post-2020 global cap, but is **fully lawful** on the high seas and within ECAs if the vessel is fitted with an approved, operational EGCS (scrubber equivalence under MARPOL Annex VI Regulation 4).
- **Context-Dependent Regulatory Assessment:** Where voyage route location, operating date, or vessel scrubber status is unavailable, the system returns `status: "CONTEXT_DEPENDENT"` rather than falsely asserting universal compliance.

### 5.3 Controlled Grade Normalization Map

| Input Raw Grade | Canonical Grade | Fuel Category | Nominal Max Sulfur Spec | Nominal ISO Reference |
|---|---|---|---|---|
| `VLSFO`, `VLSFO 0.5%` | `VLSFO` | `RESIDUAL_BLEND` | 0.50% | ISO 8217 RMG/RME 0.5% S |
| `HSFO`, `IFO 380`, `IFO 180` | `HSFO` | `RESIDUAL` | 3.50% | ISO 8217 RMG 380 |
| `MGO`, `LSMGO`, `LSMGO 0.1%` | `MGO` | `DISTILLATE` | 0.10% | ISO 8217 DMA/DMZ |
| `MDO`, `DMB` | `MDO` | `DISTILLATE_HEAVY` | 1.50% | ISO 8217 DMB |
| `ULSFO`, `ULSFO 0.1%` | `ULSFO` | `RESIDUAL_LIGHT` | 0.10% | ISO 8217 RMD 80 (0.1% S) |
| `B24`, `BIO-VLSFO B24` | `B24` | `BIO_BLEND` | 0.50% | Bio-Bunker B24 |
| `B30`, `BIO-VLSFO B30` | `B30` | `BIO_BLEND` | 0.50% | Bio-Bunker B30 |
| `LNG`, `LNG BUNKER` | `LNG` | `CRYOGENIC_GAS` | 0.00% | ISO 23306 LNG |

### 5.4 Strict Separation of MGO and MDO
MGO and MDO are never merged:
- **MGO (DMA/DMZ):** A 100% pure distillate fuel with maximum sulfur $0.10\%$, water content $< 0.3\%$, and viscosity $2.0\text{--}6.0\text{ cSt}$ at $40^\circ\text{C}$. Used in Emission Control Areas (ECAs) and auxiliary engines.
- **MDO (DMB):** A blend of distillate and small fractions of residual fuel oil, with permissible sulfur up to $1.50\%$ and viscosity up to $11.0\text{ cSt}$.

---

## 6. Port Identity & Strict Anti-Substitution Policy

### 6.1 Canonical Port Resolution
Prices are resolved to UN/LOCODE keys using authoritative port dictionaries (`KNOWN_CANONICAL_PORTS`):
- `ARBUE`: Buenos Aires, Argentina
- `DKSKA`: Skagen, Denmark
- `USSEA`: Seattle, United States
- `USPDX`: Portland (OR), United States
- `USHOU`: Houston, United States
- `USMSY`: New Orleans, United States
- `JPYOK`: Yokohama, Japan
- `SGSIN`: Singapore, Singapore
- `NLRTM`: Rotterdam, Netherlands
- `CNZOS`: Zhoushan, China
- `CNSGH`: Shanghai, China

### 6.2 Strict Anti-Substitution Invariant
Pelagos strictly enforces an **anti-substitution policy**:
- If a monitored corridor hub (e.g., `USSEA` Seattle) lacks a genuine quote from the provider, its status remains `UNAVAILABLE`.
- Under **no circumstances** does the system substitute a nearby port's quote (e.g., using Portland `USPDX` or Vancouver `CAVAN` for Seattle, or using Houston `USHOU` for New Orleans `USMSY`).
- Maritime bunkering prices reflect highly localized barge delivery fees, local refinery logistics, port tariffs, and regional availability. Fabricating or proxying prices across ports introduces severe financial distortion into voyage chartering calculations.

---

## 7. Observation Freshness & Provenance Preservation

### 7.1 Freshness Evaluation Tiers
Observation timestamps (`observedAt` or `observed_at`) are classified into 4 standardized freshness tiers relative to current UTC time $T$:
1. **Fresh ($\le 24\text{ hours}$):** Quote was updated within the last day; suitable for immediate tactical voyage planning.
2. **Recent ($24\text{ to }168\text{ hours}$):** Quote is between 1 and 7 days old; acceptable for indicative forecasting with freshness notation.
3. **Stale ($> 168\text{ hours}$ / $> 7\text{ days}$):** Quote is older than one week; flagged as stale and highlighted with caution in voyage calculations.
4. **Unknown:** Missing or malformed timestamp; treated with conservative fallback.

### 7.2 Full Provenance Preservation
The system stores full raw provider metadata in the `provenance_notes` attribute:
- Stated delivery basis (`delivered`, `ex-wharf`).
- Price nature (`indicative`, `firm`).
- Exact provider source ticker (e.g., `licensed:oilpriceapi:IFO_380_INDICATION_DKSKA_USD`).
- Original provider provenance statement.

---

## 8. Provider Withholding & Coverage Visibility

### 8.1 Transparency in API Statistics
The upstream provider intentionally withholds records when unverified or when commercial license tiers apply. The Pelagos ingestion engine surfaces these statistics explicitly:
- `published_count`: Total active records delivered by the API.
- `withheld_by_provider`: Number of records withheld by upstream provider logic (e.g., 155 records).
- `withheld_reason`: Upstream provider's declared withholding reason (e.g., *"Generated or unverified figures are never served here."*).
- `coverage_by_port`: Dictionary mapping port names to lists of available fuel grades.
- `unavailable_priority_hubs`: Explicit list of monitored corridor hubs currently lacking quotes.

---

## 9. Durable Quarantine Storage & Restart Persistence

### 9.1 Durable SQL Storage (`quarantined_records`)
Quarantined records are durably persisted to SQL storage using the existing `QuarantinedRecord` model:
- `source_name`: Provider identifier (`"Bunker Fuel Prices (bunkerfuelprices.com)"`).
- `source_partition`: Feed partition (e.g., `"bunker_price_observations"`).
- `raw_record_id`: Composite identifier `f"{port_code}_{grade}_{observed_at}"`.
- `error_code`: Machine-readable rejection code (`CURRENCY_CONTRADICTION`, `EXTREME_PRICE_REQUIRES_MANUAL_REVIEW`, `VOLUMETRIC_UNIT_NOT_SUPPORTED`, `UNSUPPORTED_FUEL_GRADE`, `NON_POSITIVE_PRICE`, `STRUCTURAL_INVALID_PRICE`).
- `error_details`: Full diagnostic explanation string.
- `raw_payload`: Bit-for-bit JSON serialization of the exact item payload.
- `ingestion_time`: UTC timestamp of acquisition run.

### 9.2 Key Invariants
1. **Restart Persistence:** Because quarantined records are committed to the SQL `quarantined_records` table, rejection reasons and raw evidence survive complete process restarts and database reconnections.
2. **Canonical Table Isolation:** Quarantined records are committed exclusively to `quarantined_records` and **never** enter `bunker_price_observations`.
3. **Evidence Recovery:** The complete original payload is stored in `raw_payload` and fully recoverable via `json.loads(record.raw_payload)`.
4. **Idempotency:** Re-running ingestion checks for existing `(source_name, raw_record_id, error_code)` entries, preventing duplicate quarantine rows.

---

## 10. Historical Data Protection & Baseline Guarantees

### 10.1 Immutability of Authentic Datasets
- Pre-existing verified observations stored in `backend/pelagos/data/genuine_bunker_prices.json` (5 valid observations across Buenos Aires and Skagen, plus 1 quarantined Zhoushan record) are strictly protected against modification or deletion.
- Ingestion regression tests continuously assert the exact count, values, and provenance of these records.

---

## 11. Traceability & Testing

### 11.1 Test Suite Verification
Validation rules, governance invariants, and quarantine semantics are verified by 50 automated tests in `tests/test_bunker_provider.py`:
- `TestBunkerCurrencyIntegrity`: Currency suffix inspection, contradiction quarantine, missing currency detection.
- `TestBunkerUnitsAndPricingValidation`: Mass unit enforcement, volumetric rejection, non-positive price rejection, price warning vs review thresholds.
- `TestBunkerFuelGradeNormalization`: ISO 8217 normalization, MGO vs MDO demarcation, generic road diesel rejection.
- `TestBunkerPortResolutionAndAntiSubstitution`: UN/LOCODE matching, anti-substitution invariant, unquoted priority hub tracking.
- `TestBunkerFreshnessAndProvenanceIntegrity`: Freshness classification tiers, raw provenance metadata retention.
- `TestBunkerIngestionAndQuarantineHardening`: Withholding transparency, partial batch quarantine isolation.
- `TestBunkerHistoricalProtection`: Immutability of verified historical seed fixtures.
- `TestBunkerQuarantinePersistence`: Restart persistence test, durable SQL storage, raw payload recovery, canonical table isolation, idempotency.
- `TestBunkerFuelSpecificationSemantics`: Demarcation of generic labels from measured specs, preservation of explicit source specs, nominal ISO specification vs context-dependent statutory MARPOL limits.
- `TestBunkerPriceThresholdReview`: Separation of structural errors, non-positive prices, currency errors, volumetric errors, unusual prices, and extreme price reviews.
- `TestBunkerProviderTermsAndPermittedUse`: Verification of `PENDING_PROVIDER_PERMISSION` and permitted use tracking.
- `TestBunkerOfflineFixtureGovernance`: Verification that offline fixtures serve as historical audit evidence, barring restricted provider observations from operational calculations (`operational_mode=True`), and preserving original fixture observations unaltered.
