---
title: Pelagos Freight Index Specification and Forecasting Feasibility Assessment
document_id: ML-001
type: ml_spec
status: approved
version: 1.3.0
created: 2026-10-16
updated: 2026-10-17
tags:
  - freight
  - index
  - forecasting
  - machine-learning
  - specification
  - feasibility
  - baselines
---

# Pelagos Freight Index Specification & Forecasting Feasibility Assessment

## 1. Purpose

This document provides the mathematical specification, empirical feasibility assessment, and technical architecture for:
1. An experimental, reproducible freight corridor index built on genuine U.S.–East Asia dry bulk vessel rate observations.
2. A comparative evaluation of candidate aggregation formulas (Simple Arithmetic Mean, Normalized Two-Route Index, and Distance-Weighted Composite), recommending the simplest defensible formulation.
3. A leakage-safe baseline forecasting evaluation (`pelagos.forecasting`) implementing persistence, rolling average, and trend extrapolation baselines.
4. An empirical walk-forward evaluation protocol establishing preliminary baseline benchmarks in accordance with [REQ-FR-FRF-001](../requirements/functional-requirements.md).

This specification fulfills Milestone D8.1 and establishes the boundaries for experimental freight index calculation without claiming unverified industry benchmark authority.

---

## 2. Scope

### In Scope
- Mathematical formulation and comparative analysis of candidate corridor indices using verified repository freight observations.
- Explicit operational and statistical evaluation of aggregation assumptions (unweighted arithmetic, relative normalized index, and distance weighting).
- Rigorous definition of the U.S. Gulf vs. Pacific Northwest spread as a derived rate difference ($R_{USG} - R_{PNW}$).
- Monthly forecasting baseline models (Persistence, Rolling Moving Average, and Linear Trend Extrapolation) evaluated at 1-month ($T+1$), 3-month ($T+3$), and 6-month ($T+6$) horizons.
- Chronological walk-forward backtesting protocol with preliminary Mean Absolute Error (MAE), Root Mean Squared Error (RMSE), and Mean Absolute Percentage Error (MAPE) metrics on the committed 24-month fixture.
- Technical architecture and domain schemas for the `backend/pelagos/forecasting/` module reusing existing D6/D7 dual-tier SQLite/Parquet/DuckDB storage.

### Out of Scope & Explicit Boundaries
- Designation of this index as an official Baltic exchange equivalent, commercial settlement index, or "Panamax Agricultural Index (PAIP)".
- Implementation of runtime forecasting or index-calculation code was executed in D8.2–D8.5 following approval of this specification.
- Hard production acceptance gating based solely on small-sample (12-step) backtest results.
- Synthetic data generation or interpolation of unobserved historical months ([REQ-NFR-DIP-001](../requirements/nonfunctional-requirements.md)).
- Modifications to existing D6/D7 ingestion pipelines, CLI workflows, or storage catalogs.

---

## 3. Background and Context

Pelagos completed Milestone D7 by establishing a verified freight market data vertical slice:
- Ingestion adapter for USDA Agricultural Marketing Service (AMS) AgTransport Ocean Bulk Vessel Rates ([REQ-FR-DII-005](../requirements/functional-requirements.md)).
- Route registry encompassing standard dry bulk corridors: `USG-JPN-BULK`, `PNW-JPN-BULK`, and `SPREAD-USG-PNW-JPN`.
- Dual-tier storage foundation with database-enforced atomic Compare-and-Swap (CAS) Parquet publication and DuckDB analytical query support ([ADR-0002](../architecture/adr/adr-0002-dual-tier-storage-strategy.md)).

While D7 provided storage and access to discrete route rates, commercial decision-makers require:
1. A consolidated, transparent barometer of grain freight rate movements between North America and East Asia.
2. Quantitative baseline forecast benchmarks to evaluate whether charter rates are projected to increase or decrease over 1- to 6-month horizons ([REQ-FR-FRF-001](../requirements/functional-requirements.md)).

---

## 4. Requirements and Constraints Traceability

| Requirement ID | Canonical Requirement | Specification Section | Compliance Strategy |
|---|---|---|---|
| `REQ-FR-DII-003` | Mandatory Provenance Metadata | Section 5.1 | All index observations retain immutable links to underlying source datasets and timestamps. |
| `REQ-FR-DII-005` | Historical Market Freight Ingestion | Section 5.1 | Consumes verified monthly USD/MT rates from `FreightObservation` and published Parquet partitions. |
| `REQ-FR-FRF-001` | Route-Conditioned Freight Prediction | Section 5.3 | Implements persistence and moving average baselines; mandates empirical measurement before claiming accuracy. |
| `REQ-NFR-DIP-001` | Zero Fabricated / Synthetic Data | Section 5.2, 5.3 | Missing periods trigger explicit `INSUFFICIENT_DATA` status; interpolation and synthetic backfilling are prohibited. |
| `REQ-NFR-DIP-003` | Multi-Dimensional UTC Tracking | Section 5.4 | Discrete tracking of `observation_date`, `calculated_at`, and `published_at`. |
| `REQ-NFR-DCT-001` | Core Technology Independence | Section 5.4 | Pure mathematical algorithms implemented in Python/NumPy/DuckDB; zero external SaaS or proprietary API dependencies. |

---

## 5. Design and Specification

### 5.1 Repository and Data Audit

An independent retrieval and audit of the committed repository fixture (`backend/pelagos/ingestion/fixtures/usda_vessel_rates_sample.json`), the SQLite operational store (`pelagos.db`), and ingestion code establishes the following empirical data profile:

```
+---------------------------------------------------------------------------------------------------------+
|                                        DATA PROFILE & AUDIT SUMMARY                                      |
+----------------------+--------------------+---------------------+--------------------+------------------+
| Route Code           | Route Class        | Directly Observed?  | Benchmark Dist.    | Ingested Range   |
+----------------------+--------------------+---------------------+--------------------+------------------+
| USG-JPN-BULK         | Ocean Bulk Corridor| Yes (is_derived=0)  | 9,150.0 nm         | 2024-10 to 2026-09|
| PNW-JPN-BULK         | Ocean Bulk Corridor| Yes (is_derived=0)  | 4,520.0 nm         | 2024-10 to 2026-09|
| SPREAD-USG-PNW-JPN   | Rate Difference    | No  (is_derived=1)  | N/A (0.0 nm)       | 2024-10 to 2026-09|
+----------------------+--------------------+---------------------+--------------------+------------------+
```

#### 1. Sampling Cadence and Discrete Units
- **Frequency:** Strictly monthly. Observations are recorded with `observation_date` anchored to the first day of each calendar month (`YYYY-MM-01`).
- **Absence of Daily Series:** Daily observations do not exist in the source dataset. Analytical windows and forecast horizons must strictly reflect monthly increments ($T+1, T+3, T+6$ months) rather than 7-day or 30-day daily steps.
- **Units:** U.S. Dollars per Metric Ton (`USD/MT`), currency `USD`.

#### 2. Reconciliation of Verified Source Values
The committed fixture contains exactly 24 consecutive calendar months with zero missing periods:
- **Earliest Record (Base Period $t_0$):** `2024-10-01`
  - `gulf_to_japan`: **53.65 USD/MT**
  - `pnw_to_japan`: **30.05 USD/MT**
  - `gulf_pnw_spread`: **23.60 USD/MT**
  - *Verification:* $53.65 - 30.05 = 23.60$ USD/MT. Confirmed identical across `usda_vessel_rates_sample.json` and `pelagos.db`.
- **Latest Record:** `2026-09-01`
  - `gulf_to_japan`: **76.38 USD/MT**
  - `pnw_to_japan`: **37.31 USD/MT**
  - `gulf_pnw_spread`: **39.07 USD/MT**
  - *Verification:* $76.38 - 37.31 = 39.07$ USD/MT.
- **Fixture Summary Statistics (24 Months, 2024-10 to 2026-09):**
  - `USG-JPN-BULK`: Min 45.55 USD/MT (`2025-01-01`), Max 76.38 USD/MT (`2026-09-01`), Mean 56.25 USD/MT.
  - `PNW-JPN-BULK`: Min 26.15 USD/MT (`2025-01-01`), Max 37.31 USD/MT (`2026-09-01`), Mean 30.62 USD/MT.
  - `SPREAD-USG-PNW-JPN`: Min 18.50 USD/MT (`2024-12-01`), Max 39.07 USD/MT (`2026-09-01`), Mean 25.64 USD/MT.

#### 3. Directly Observed vs. Derived Fields
- `gulf_to_japan` and `pnw_to_japan` are directly observed survey market rates compiled from maritime broker fixture reports.
- `gulf_pnw_spread` is the derived arithmetic difference $R_{USG} - R_{PNW}$ ($| \text{diff} | = 0.000$ across all 24 records).

#### 4. Source Limitations & Licensing Rights
- **Publisher Attribution:** "USDA Agricultural Marketing Service / O'Neil Commodity Consulting".
- **Repository Documentation:** [freight_usda.py:19-20](file:///c:/Users/Lenovo/Desktop/random%20codes/SIH26/Pelagos/backend/pelagos/ingestion/freight_usda.py#L19-L20) tags the data as `U.S. Public Domain`.
- **Licensing Nuance:** Works created solely by officers or employees of the U.S. Federal Government are in the public domain under 17 U.S.C. § 105. However, because the underlying rate assessments are co-produced with a commercial private consultancy ("O'Neil Commodity Consulting"), the repository lacks an explicit downstream redistribution agreement or commercial license grant from O'Neil Commodity Consulting.
- **Operational Boundary:** The dataset is valid for internal analytical processing, experimental index formulation, and academic/research demonstration. Any public commercial redistribution must include the mandatory USDA attribution notice and verify that third-party consulting terms are not infringed.

---

### 5.2 Experimental Freight Index Specification

#### 1. Identity and Naming
- **Canonical Name:** **Pelagos Experimental Grain Index (U.S.–Japan)**
- **Identifier:** `PEGI-USJPN`
- **Dual-Metric Distinction (Absolute Rate vs. Normalized Index):**
  - **Absolute Freight Rate (`pegi_usd_mt`, USD/MT):** The physical nominal freight cost per metric ton calculated as the simple arithmetic mean of both corridors: $(R_{USG} + R_{PNW}) / 2$. Expressed in dollars per ton, this measures physical transportation expenditure.
  - **Normalized Relative Index (`pegi_points`, Index Points):** A unitless relative price barometer normalized to 100.00 at October 2024 ($t_0$) using approved **Formula A**: $0.5 \times ((R_{USG}/53.65) + (R_{PNW}/30.05)) \times 100.0$. Expressed in index points, this measures pure percentage rate inflation/deflation, according equal 50/50 economic weight to percentage moves on both coasts while eliminating nominal dollar scale distortion.
- **Explicit Limitations:** This is a corridor-specific dry bulk freight barometer covering agricultural grain exports from North America to East Asia. It is **not** a global dry bulk index (such as the Baltic Dry Index), nor an official industry benchmark. It must not be marketed as an authoritative settlement instrument for freight derivatives.

#### 2. Comparative Evaluation of Candidate Index Formulas

To avoid unevidenced assumptions, we evaluate three candidate formulas:

```
+---------------------------------------------------------------------------------------------------------+
|                                    CANDIDATE FORMULA COMPARISON                                          |
+--------------------------+--------------------------------+---------------------------------------------+
| Candidate                | Mathematical Formulation       | What It Measures & Core Assumptions         |
+--------------------------+--------------------------------+---------------------------------------------+
| 1. Simple Arithmetic     | I_arith(t) =                   | Average freight cost per ton across both    |
|    Mean (USD/MT)         | (R_USG(t) + R_PNW(t)) / 2      | export outlets. Assumes equal coastal       |
|    (Recommended Primary) |                                | optionality. Zero exogenous parameters.     |
+--------------------------+--------------------------------+---------------------------------------------+
| 2. Normalized Two-Route  | I_norm(t) = 0.5 *              | Average relative rate inflation/deflation   |
|    Index (Base 100)      | (R_USG(t)/R_USG(t0) +          | relative to base period t0. Eliminates      |
|    (Recommended Relative)|  R_PNW(t)/R_PNW(t0)) * 100.0   | nominal price bias between coasts.          |
+--------------------------+--------------------------------+---------------------------------------------+
| 3. Distance-Weighted     | I_dist(t) =                    | Distance-weighted rate proxying capacity    |
|    Composite             | w_USG * R_USG(t) +             | commitment. Assumes fixed routing (9,150 nm |
|    (Analytical Scenario) | w_PNW * R_PNW(t)               | vs 4,520 nm) and equal cargo volume.        |
+--------------------------+--------------------------------+---------------------------------------------+
```

##### Candidate 1: Simple Arithmetic Mean (Recommended Primary Absolute Rate Metric)
$$I_{arith}(t) = \text{pegi\_usd\_mt}(t) = \frac{R_{USG}(t) + R_{PNW}(t)}{2}$$
- **What It Measures:** The unweighted average freight cost in USD/MT to ship grain from the U.S. to Japan across both primary Pacific and Gulf loading ranges.
- **Assumptions Required:** Assumes an unweighted commercial benchmark for grain charterers evaluating aggregate U.S. export freight levels.
- **Strengths:**
  - Zero exogenous parameters: does not require estimating trade volumes or assuming static voyage distances.
  - Maximum transparency: directly verifiable in USD/MT without subjective weighting.
  - Fully reproducible across all systems.

##### Candidate 2: Normalized Two-Route Relative Index (Approved Primary Index Points - Formula A)
$$\text{pegi\_points}(t) = \frac{1}{2} \left( \frac{R_{USG}(t)}{R_{USG}(t_0)} + \frac{R_{PNW}(t)}{R_{PNW}(t_0)} \right) \times 100.0$$
where $R_{USG}(t_0) = 53.65 \text{ USD/MT}$ and $R_{PNW}(t_0) = 30.05 \text{ USD/MT}$ for base month $t_0 = \text{2024-10-01}$.
- **What It Measures:** Pure percentage movement of U.S.–Japan grain freight rates relative to the benchmark reference period $t_0$, giving equal 50/50 economic weight to relative price movements on each coast.
- **Assumptions Required:** Requires designating an explicit base month ($t_0 = \text{2024-10-01}$).
- **Strengths:** Eliminates nominal dollar bias where higher-cost Gulf rates naturally show larger absolute dollar swings than PNW rates. A 10% rate increase on either coast carries the exact same index weight (+5.0 points).

*Unselected Alternative (Normalized Arithmetic Mean - Formula B):*
Normalizing the aggregate arithmetic rate directly ($I_{pts}(t) = (I_{arith}(t) / I_{arith}(t_0)) \times 100.0 = (\text{pegi\_usd\_mt}(t) / 41.8500) \times 100.0$) was evaluated but unselected as the primary relative index. Because Gulf rates are nominally much higher (~1.8x PNW rates), directly indexing the dollar sum implicitly weights the Gulf route at ~64.1% and the PNW route at ~35.9%, allowing nominal dollar swings on the Gulf to dominate the index. In contrast, the approved Formula A gives equal 50/50 economic weight to percentage price changes on both coasts. For example, in January 2025 ($R_{USG} = 45.55$, $R_{PNW} = 26.15$), approved Formula A yields $\mathbf{85.96}$ points ($0.5 \times (45.55/53.65 + 26.15/30.05) \times 100$), whereas unselected Formula B yields $\mathbf{85.66}$ points ($(35.8500 / 41.8500) \times 100$); in September 2026, Formula A yields $\mathbf{133.26}$ points versus $\mathbf{135.83}$ points under Formula B.

##### Candidate 3: Distance-Weighted Composite (Analytical Scenario Only)
$$w_{USG} = \frac{9,150}{9,150 + 4,520} \approx 0.669349 \quad (66.93\%)$$
$$w_{PNW} = \frac{4,520}{9,150 + 4,520} \approx 0.330651 \quad (33.07\%)$$
$$I_{dist}(t) = 0.669349 \cdot R_{USG}(t) + 0.330651 \cdot R_{PNW}(t)$$
- **Why Distance Weighting Is NOT Assumed Superior:**
  - Route distance alone is **not** equivalent to ton-mile freight absorption without genuine cargo export volume data ($Ton\text{-}Miles = Tons \times Distance$).
  - If actual monthly grain export tonnages are distributed 60% PNW and 40% Gulf, distance weighting arbitrarily overweights the Gulf (67%) simply because the voyage is longer, producing an unevidenced distortion.
  - Furthermore, Gulf-to-Japan transit distance varies dynamically depending on whether vessels transit the Panama Canal (~9,150 nm) or divert via the Cape of Good Hope (~14,000+ nm).
  - Therefore, distance weighting cannot be justified as an authoritative primary index without empirical cargo volume statistics.

##### Justified Recommendation:
1. **Primary Absolute Rate Metric:** **Simple Arithmetic Mean ($/MT)** (`pegi_usd_mt`), representing the straightforward average nominal freight rate across both loading ranges: $\text{pegi\_usd\_mt}(t) = \frac{R_{USG}(t) + R_{PNW}(t)}{2}$.
2. **Primary Normalized Relative Index:** **Route-Normalized Base-100 Index Points** (`pegi_points`), tracking equal-weighted relative price evolution from October 2024 using approved Formula A: $\text{pegi\_points}(t) = 0.5 \times \left( \frac{R_{USG}(t)}{53.65} + \frac{R_{PNW}(t)}{30.05} \right) \times 100.0$.
3. **Scenario Metric:** Distance-weighted composite retained only as an exploratory secondary metric (`pegi_distance_weighted_usd_mt`).

#### 3. Correct Treatment of the US Gulf–PNW Spread
- `SPREAD-USG-PNW-JPN` $= R_{USG}(t) - R_{PNW}(t)$ in USD/MT.
- **Empirical Definition:** The spread measures the **observed spot rate premium of Gulf loading over Pacific Northwest loading** for deliveries to Japan.
- **Unsupported Claims Excluded:** The spread must **not** be speculatively attributed to specific unmeasured causal factors (such as canal toll hikes, bunker fuel fluctuations, or waiting times) without independent data feeds confirming those specific cost components.
- **Collinearity Rule:** The spread **must never be added into the composite rate**. Because $R_{USG} - R_{PNW}$ is a linear combination of the two base legs, including it in a composite index would introduce mathematical double-counting ($R_{USG} + R_{PNW} + (R_{USG} - R_{PNW}) = 2 R_{USG}$).
- **Role:** Maintained as an independent companion metric (`spread_usd_mt`).

#### 4. Period Alignment, Missing-Data & Eligibility Rules
- **Period Alignment:** Both route observations must share the exact same calendar month date (`YYYY-MM-01`).
- **Zero Imputation Policy:** If either `USG-JPN-BULK` or `PNW-JPN-BULK` is missing for month $t$, the composite index **must not** be calculated using a single leg or filled with forward-projected values ([REQ-NFR-DIP-001](../requirements/nonfunctional-requirements.md)).
- **Eligibility Statuses:**
  - `STATUS_CALCULATED`: Both underlying legs observed; index successfully computed.
  - `STATUS_INSUFFICIENT_DATA`: One or both underlying legs missing for month $t$.
  - `STATUS_OUT_OF_RANGE`: Requested date precedes dataset history or extends into future unobserved periods.

#### 5. Reconciled Worked Examples Directly from Verified Records

```
+---------------------------------------------------------------------------------------------------------+
|                                    RECONCILED WORKED INDEX EXAMPLES                                     |
+------------+------------+------------+------------+--------------------+----------------+---------------+
| Date       | USG ($/MT) | PNW ($/MT) | Spread ($) | Arith. Rate ($/MT) | PEGI Points    | Dist. ($/MT)  |
|            |            |            | (USG-PNW)  | (pegi_usd_mt)      | (Formula A)    | (Scenario)    |
+------------+------------+------------+------------+--------------------+----------------+---------------+
| 2024-10-01 | 53.65      | 30.05      | 23.60      | 41.8500 (Base)     | 100.00 (Base)  | 45.8466       |
| 2025-01-01 | 45.55      | 26.15      | 19.40      | 35.8500            |  85.96         | 39.1354       |
| 2025-09-01 | 57.13      | 29.63      | 27.50      | 43.3800            | 102.54         | 48.0371       |
| 2026-07-01 | 68.95      | 35.95      | 33.00      | 52.4500            | 124.08         | 58.0385       |
| 2026-08-01 | 72.94      | 36.75      | 36.19      | 54.8450            | 129.13         | 60.9737       |
| 2026-09-01 | 76.38      | 37.31      | 39.07      | 56.8450            | 133.26         | 63.4615       |
+------------+------------+------------+------------+--------------------+----------------+---------------+
```

*Direct Formula Verification for 2026-09-01:*
- Primary Arithmetic Rate (`pegi_usd_mt`, Absolute Rate): $\frac{76.38 + 37.31}{2} = \frac{113.69}{2} = \mathbf{56.8450} \text{ USD/MT}$
- Primary Normalized Index (`pegi_points`, Formula A Points): $0.5 \times \left( \frac{76.38}{53.65} + \frac{37.31}{30.05} \right) \times 100.0 = 0.5 \times (1.423672 + 1.241597) \times 100.0 = \mathbf{133.26} \text{ points}$
- Distance-Weighted Scenario: $(0.669349 \times 76.38) + (0.330651 \times 37.31) = 51.1248 + 12.3366 = \mathbf{63.4615} \text{ USD/MT}$
- Derived Spread Indicator: $76.38 - 37.31 = \mathbf{39.07} \text{ USD/MT}$


---

### 5.3 Forecasting Feasibility & Evaluation Protocol

#### 1. Forecasting Horizons & Cadence Alignment
Because the source dataset updates monthly, forward predictions must strictly operate on monthly discrete horizons:
- $h = 1$ month ahead ($T+1$ month)
- $h = 3$ months ahead ($T+3$ months)
- $h = 6$ months ahead ($T+6$ months)

Arbitrary daily or 30-day forecast requests are rejected at the interface boundary.

#### 2. Baseline Model Formulations

##### Model 1: Persistence Baseline (Naive Last Observation)
$$\hat{y}_{t+h|t} = y_t \quad \forall h \ge 1$$
- **Minimum History Required:** 1 valid observation.
- **Operational Role:** Mandatory benchmark. In commodity freight markets, spot rates exhibit strong random-walk characteristics. Any statistical or ML model must outperform persistence to justify operational complexity.

##### Model 2: Rolling Window Moving Average
$$\hat{y}_{t+h|t} = \frac{1}{k} \sum_{i=0}^{k-1} y_{t-i}$$
- **Evaluated Windows:** $k=3$ (quarterly) and $k=6$ (semi-annual).
- **Minimum History Required:** Exactly $k$ consecutive preceding monthly observations.
- **Missing Data Rule:** If any observation within $[t-k+1, t]$ is missing, the model returns `STATUS_INSUFFICIENT_HISTORY`.

##### Model 3: Historical Linear Trend Extrapolation
$$\hat{y}_{t+h|t} = \hat{\alpha}_t + \hat{\beta}_t \cdot (w + h)$$
where $\hat{\alpha}_t$ and $\hat{\beta}_t$ are estimated via Ordinary Least Squares (OLS) over trailing $w=6$ months.
- **Physical Boundary Constraint:** Freight rates cannot be negative. If extrapolated $\hat{y} \le 0$, it is clamped to $\epsilon = 0.10$ USD/MT with flag `FLAG_TREND_NEGATIVE_CLAMP`.

#### 3. Chronological Walk-Forward Backtesting Protocol
1. **Zero Lookahead:** To predict period $t+h$, the model observes strictly data where `observation_date` $\le t$.
2. **Backtest Structure on Committed Fixture:**
   - Total sample: 24 consecutive months (`2024-10-01` to `2026-09-01`).
   - Burn-in training history: Months 1 to 12 (`2024-10-01` to `2025-09-01`).
   - Out-of-sample evaluation window: Months 13 to 24 (`2025-10-01` to `2026-09-01`), yielding exactly **12 one-month-ahead ($h=1$) evaluation steps**.
3. **Error Metrics:**
   $$\text{MAE} = \frac{1}{N} \sum_{i=1}^{N} | y_i - \hat{y}_i |$$
   $$\text{RMSE} = \sqrt{\frac{1}{N} \sum_{i=1}^{N} (y_i - \hat{y}_i)^2}$$
   $$\text{MAPE} = \frac{1}{N} \sum_{i=1}^{N} \left| \frac{y_i - \hat{y}_i}{y_i} \right| \times 100\%$$

#### 4. Preliminary Empirical Results & Explicit Qualifications

```
+---------------------------------------------------------------------------------------------------------+
|                        PRELIMINARY BASELINE EVALUATION (h = 1 Month, 12 Test Steps)                     |
+----------------------+--------------------+-----------------+--------------------+----------------------+
| Route Code           | Model              | MAE (USD/MT)    | RMSE (USD/MT)      | MAPE (%)             |
+----------------------+--------------------+-----------------+--------------------+----------------------+
| USG-JPN-BULK         | Persistence (M1)   | 3.14            | 3.81               | 5.03%                |
| USG-JPN-BULK         | Rolling MA-3 (M2)  | 4.39            | 5.73               | 6.80%                |
| USG-JPN-BULK         | Rolling MA-6 (M2)  | 6.85            | 7.73               | 10.45%               |
| USG-JPN-BULK         | Linear Trend-6 (M3)| 5.05            | 5.99               | 8.19%                |
+----------------------+--------------------+-----------------+--------------------+----------------------+
| PNW-JPN-BULK         | Persistence (M1)   | 1.12            | 1.50               | 3.39%                |
| PNW-JPN-BULK         | Rolling MA-3 (M2)  | 1.68            | 2.32               | 4.97%                |
| PNW-JPN-BULK         | Rolling MA-6 (M2)  | 2.61            | 3.19               | 7.55%                |
| PNW-JPN-BULK         | Linear Trend-6 (M3)| 1.98            | 2.33               | 5.91%                |
+----------------------+--------------------+-----------------+--------------------+----------------------+
```

##### Essential Methodological Qualifications:
1. **Preliminary Evidence Only:** These results represent initial exploratory evidence from a single 12-month test window (October 2025 to September 2026).
2. **Limited Statistical Generalizability:** Twelve evaluation steps over a single seasonal grain cycle cannot establish macro regime validity. During this period, freight rates trended strongly upward, penalizing lagging moving averages.
3. **No Hard Production Acceptance Gates:** These numbers **must not** be used as hard numerical production pass/fail thresholds for future models. A model evaluated across a flat or declining rate regime might exhibit different error profiles.
4. **Value of the Remote Historical Archive:** The full 1996–2026 archive (~360 months) contains multiple macroeconomic cycles (2008 financial crisis, commodity super-cycle, COVID disruption, El Niño weather cycles). Backtesting across that larger archive under multi-fold rolling origin cross-validation will be required in a future task to establish statistically robust benchmark thresholds.

---

### 5.4 Proposed Technical Design for `backend/pelagos/forecasting/`

The forecasting subsystem will be implemented as an encapsulated domain package within the Pelagos modular monolith ([ADR-0001](../architecture/adr/adr-0001-modular-monolith-architecture.md)).

```
backend/pelagos/forecasting/
├── __init__.py
├── index_engine.py       # Arithmetic & normalized index calculations
├── baselines.py          # Pure mathematical baseline models (Persistence, MA, Trend)
├── evaluation.py         # Chronological walk-forward backtest runner & metrics
├── schemas.py            # Pydantic schemas for API inputs/outputs
└── service.py            # High-level query service integrating DuckDB analytics
```

#### 1. Data Contracts & Output Schemas

```python
class FreightIndexRecord(BaseModel):
    index_code: str = "PEGI-USJPN"
    observation_date: date
    pegi_usd_mt: float          # Simple arithmetic mean
    pegi_points: float          # Base-100 normalized points
    spread_usd_mt: float        # USG - PNW difference
    pegi_distance_weighted: Optional[float] = None  # Scenario metric
    status: str = "calculated"
    provenance_hash: str

class BaselineForecastResult(BaseModel):
    route_code: str
    origin_date: date
    target_date: date
    horizon_months: int
    model_type: str  # "persistence" | "rolling_ma_3" | "rolling_ma_6" | "linear_trend_6"
    predicted_value: Optional[float]
    unit_of_measure: str = "USD/MT"
    status: str = "forecast_generated"  # or "insufficient_history"
    flags: List[str] = []
    history_count_observed: int = 0
    notes: Optional[str] = None
```

#### 2. Reusing Storage Infrastructure (DuckDB & Parquet)
The forecasting service will query the D7 Parquet analytical store via DuckDB, avoiding redundant SQL data copies:
```sql
SELECT
    observation_date,
    MAX(CASE WHEN route_code = 'USG-JPN-BULK' THEN rate_value END) AS usg_rate,
    MAX(CASE WHEN route_code = 'PNW-JPN-BULK' THEN rate_value END) AS pnw_rate,
    MAX(CASE WHEN route_code = 'SPREAD-USG-PNW-JPN' THEN rate_value END) AS spread_rate
FROM parquet_scan('parquet_data/freight/year=*/*.parquet')
GROUP BY observation_date
ORDER BY observation_date ASC;
```

#### 3. API Integration Endpoints
Implemented under `/api/v1/freight/` in `backend/pelagos/api/routes/freight.py`:
- `GET /api/v1/freight/index`: Returns calculated historical index series (`pegi_usd_mt`, `pegi_points`, `spread_usd_mt`, and distance-weighted scenario). Optional query parameters: `start_date`, `end_date`.
- `GET /api/v1/freight/forecast` (alias: `GET /api/v1/freight/forecast/baseline`): Generates next-horizon point forecast for a directly observed route (`USG-JPN-BULK` or `PNW-JPN-BULK`). Parameters: `route_code` (required), `model_type` (default `persistence`; options: `persistence`, `rolling_ma_3`, `rolling_ma_6`, `linear_trend_6`), `origin_date` (default latest observed month), `horizon_months` (discrete horizon 1–6, default 1).
- `GET /api/v1/freight/forecast/evaluate`: Returns empirical chronological walk-forward out-of-sample evaluation metrics (MAE, RMSE, MAPE, relative ratio vs. persistence) across baseline models for a route with mandatory preliminary qualification disclaimer. Parameters: `route_code` (required), `burn_in_months` (default 12), `include_steps` (default false).

#### 4. Companion CLI Commands
Implemented in `backend/pelagos/cli.py`:
- `pelagos freight-index [--since YYYY-MM-DD] [--limit N] [--format table|json] [--storage-dir PATH] [--db-url URL]`: Computes and displays the Pelagos Experimental Grain Index (PEGI-USJPN) series.
- `pelagos freight-forecast-baseline --route-code ROUTE_CODE [--model MODEL] [--origin-date YYYY-MM-DD] [--horizon-months N] [--format table|json] [--storage-dir PATH] [--db-url URL]`: Generates and displays experimental baseline point forecasts in table or JSON format.

---

## 6. Dependencies and Interfaces

- **Upstream:** `pelagos.models.FreightObservation`, `pelagos.models.FreightPublication`, and D7 Parquet publication directory.
- **Execution Engine:** Python 3.12+, DuckDB 1.1+, NumPy 1.26+.
- **Downstream:** Commercial chartering recommendation module (`pelagos.commercial`) and 3D geospatial dashboard HUD deck.

---

## 7. Failure Modes and Limitations

1. **Short Historical Series in Offline Fixture:** The 24-month sample supports 12 walk-forward evaluation steps for $h=1$, but only 7 steps for $h=6$. Longer-term macro regime evaluation requires accessing the full historical remote archive (1996–2026).
2. **Corridor Generalization Limit:** The index reflects U.S. to Japan bulk grain movements. It must not be extrapolated to Iron Ore (Capesize Brazil–China), Crude Tankers (VLCC Gulf–Asia), or Containerized box freight (SCFI / FBX).
3. **Absence of Cargo Volume Weighting:** The index reflects average quoted transport rates, not volume-weighted freight expenditures, because reliable monthly export tonnages are unobserved.

---

## 8. Security, Privacy and Licensing

- Zero personal data or vessel AIS identifiers processed in the macro index.
- Strict license attribution notice to USDA AMS and O'Neil Commodity Consulting maintained in all API responses.

---

## 9. Testing and Verification Plan

Implemented and verified across Milestones D8.2–D8.5:
1. **Mathematical Reproducibility:** Exact float matching of index values and arithmetic means against the worked examples in Section 5.2 verified in `tests/test_freight_index.py`.
2. **Missing Leg Fencing:** Attempting to compute an index when one route is missing returns `STATUS_INSUFFICIENT_DATA` without synthetic backfilling.
3. **Zero Lookahead Leakage:** Unit tests in `tests/test_forecasting_baselines.py` verify that walk-forward step $t$ cannot observe records from $t+1$.
4. **Negative Rate Clamping:** Steep negative linear trends clamp at $\epsilon = 0.10$ USD/MT with `FLAG_TREND_NEGATIVE_CLAMP` verified in `tests/test_forecasting_baselines.py`.
5. **Read-Only Invariants:** `tests/test_forecasting_api.py` and `tests/test_cli.py` verify complete database immutability and zero Parquet writes.

---

## 10. Traceability

- **Upstream:** [PROJECT_OVERVIEW.md Section 1, 5](../PROJECT_OVERVIEW.md)
- **Requirements:** [REQ-FR-DII-005](../requirements/functional-requirements.md), [REQ-FR-FRF-001](../requirements/functional-requirements.md), [REQ-NFR-DIP-001](../requirements/nonfunctional-requirements.md), [REQ-NFR-DCT-001](../requirements/nonfunctional-requirements.md)
- **Architecture:** [ARCH-001 Section 5.1](../architecture/system-architecture.md), [ADR-0001](../architecture/adr/adr-0001-modular-monolith-architecture.md), [ADR-0002](../architecture/adr/adr-0002-dual-tier-storage-strategy.md)

---

## 11. Open Questions and Unresolved Decisions

- **OQ-ML-01 (Remote Archive Ingestion Timing):** When should the full 1996–2026 remote archive (~360 records) be ingested to support multi-regime cross-validation without mutating the existing baseline tests?
- **OQ-ML-02 (Third-Party License Clarification):** Confirm whether O'Neil Commodity Consulting terms impose restrictions on downstream commercial packaging outside USDA public domain provisions.

---

## 12. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-10-16 | Project Agent | Initial creation of D8.1 Freight Index Specification and Baseline Forecasting Feasibility Assessment. |
| 1.1.0 | 2026-10-16 | Project Agent | D8.1 corrections: Reconciled base period fixture values (2024-10), reassessed formula to recommend Simple Arithmetic Mean and Normalized Base-100 Points, removed causal claims from spread definition, and qualified forecasting walk-forward sample limitations. |
| 1.2.0 | 2026-10-16 | Project Agent | D8.2 reconciliation: Designated Formula A (Route-Normalized Two-Route Index) as sole primary definition for pegi_points, documented Formula B as unselected alternative, reconciled worked example table points and walkthrough to 133.26 points. |
| 1.3.0 | 2026-10-17 | Project Agent | D8.6 integration audit: Reconciled implemented API routes (/api/v1/freight/index, /api/v1/freight/forecast, /api/v1/freight/forecast/evaluate), CLI commands (freight-index, freight-forecast-baseline), schemas, and verification evidence. Status updated to approved. |
