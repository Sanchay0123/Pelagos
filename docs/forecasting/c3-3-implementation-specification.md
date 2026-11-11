---
title: "Stage C3.3 Implementation Specification: Regime-Aware & Controlled Multivariate Forecasting Investigation"
document_id: SPEC-C33-REGIME-001
type: research_notes
status: approved
version: 1.0.0
created: 2026-11-11
updated: 2026-11-11
tags:
  - forecasting
  - regime-aware
  - multivariate
  - specification
  - stage-c3-3
---

# Pelagos — Stage C3.3 Implementation Specification: Regime-Aware & Controlled Multivariate Forecasting Investigation

> [!NOTE]
> **Research Specification Boundary (`DOC-001` Section 6):** This specification defines an empirical research investigation (`pelagos.forecasting.regime_experiments`). Its execution does **not** alter [`service.py`](../../backend/pelagos/forecasting/service.py) or `DEFAULT_BASELINE_MODEL` (`"persistence"`).

**Document ID:** `SPEC-C33-REGIME-001`  
**Baseline Reference:** Stage C3.2 ([`REPORT-C32-ML-BENCHMARK-001`](./c3-2-candidate-ml-benchmarking-report.md), baseline commit `7c0137fe8b60cb3951bd31dd7350513b524a67e0` + C3.1.2/C3.2 working tree)  
**Production Benchmark (Frozen):** `persistence` (`DEFAULT_BASELINE_MODEL = "persistence"` in [`schemas.py`](../../backend/pelagos/forecasting/schemas.py) and [`service.py`](../../backend/pelagos/forecasting/service.py))  
**Corridors & Horizons:** `PNW-JPN-BULK`, `USG-JPN-BULK` across $T+1$, $T+3$, and $T+6$ (`horizon_alignment = "strict_forward"`)

---

## 1. Scope & Methodological Guardrails

Stage C3.3 evaluates whether **decision-time market-regime awareness** and **controlled multivariate combinations of genuine historically covered predictors** improve out-of-sample grain freight forecasting relative to the active **Persistence (M1)** baseline and the best Stage C3.2 candidate (**Ridge Autoregressive**, `ridge_autoregressive`, 6 own-route freight lags).

The investigation operates under unchanged C1/C3.1/C3.2 constraints:
1. **Horizon Semantics:** Strict-forward (`target_period_start_utc > decision_timestamp`; target offsets $+2, +4, +7$ months from observation month $M$).
2. **Temporal Availability Policy:** Fixed `14`-calendar-day post-month-end cutoff with provisional `12:00 PM America/New_York` instant (`conservative_availability_cutoff`) and `23:59:59 UTC` normalized `decision_timestamp`, strictly treated as a **Pelagos conservative operational assumption** (`observed_publication_time = None`).
3. **Zero Target-Regime Leakage:** C1.3.2 macroeconomic regimes (`PRE_SUPERCYCLE`, `SUPERCYCLE_PEAK_CRASH`, `POST_SUPERCYCLE`, `PANDEMIC_DISRUPTION`) are evaluated by `target_date` for outcome stratification, **never** used as predictors at decision time. Every C3.3 predictor or model selector assigns the active regime strictly from decision-time known dates (`origin_date <= decision_date`).
4. **Statistical Framework:** Expanding walk-forward evaluation (`min_training_samples = 36`, `seed = 42`), train-only `StandardScaler` fitting, physical lower clamping ($\ge 0.10$ USD/MT), identical target-date common-sample alignment, Diebold–Mariano tests with Harvey–Leybourne–Newbold (HLN) small-sample adjustment, Moving Block Bootstrap 95% CIs (`n_boot = 1000`, `block_length = max(horizon, 3)`), and Holm–Bonferroni FWER adjustment.
5. **Non-Promotion Boundary:** Persistence remains the sole production default regardless of experimental outcomes; no changes to `service.py`, `parquet_data/`, or `pelagos.db`.

---

## 2. Multivariate Feature Feasibility Audit (Pre-Experiment Gate)

Live read-only inspection of `pelagos.db` establishes which data domains can support genuine historical walk-forward modeling (370 continuous months, Jan 1996 – Oct 2026):

| Data Domain / Candidate Group | Source & Provenance | DB Count & Span | Availability Timing | Missingness | Decision-Time Usable? | Stage C3.3 Disposition |
|:---|:---|:---:|:---:|:---:|:---:|:---|
| **Own-Route Freight Lags** (`rate_lag_0..12`, 6 features) | USDA AMS GTR Figure 20 / O'Neil (`ehs5-yac3`) | `370`/route (`1996-01`–`2026-10`) | Day 14 assumption (`< dec_dt`) | `0.0%` | **Yes** | **Retained (`ridge_autoregressive` baseline)** |
| **Cross-Corridor & Spread** (`competitor_rate_lag_0`, `corridor_spread_lag_0`, `corridor_spread_mean_3`) | Competitor USDA AMS corridor (`USG` $\leftrightarrow$ `PNW`) | `370`/route (`1996-01`–`2026-10`) | Strictly gated `cutoff < dec_dt` | `0.0%` | **Yes** | **Evaluated in isolation (`multivariate_ar_spread`)** |
| **Volatility & Mean-Reversion + Seasonality** (`rolling_std_6/12`, `rolling_ratio_12`, `origin_sin/cos_month`) | Deterministic own-route dispersion & calendar harmonics | `370` months | Realized at `origin_date` | `0.0%` | **Yes** | **Evaluated in isolation (`multivariate_ar_vol_seasonal`)** |
| **Decision-Time Regime Flags & Interactions** (`origin_date` regime flags $\times$ `rate_diff_1`) | C1.3.2 historical bounds anchored at `origin_date` | `370` months | Known at `origin_date` | `0.0%` | **Yes** | **Evaluated (`regime_interaction_ridge_ar`)** |
| **Route Structural Distance** (`route_benchmark_distance_nm`) | Freight Routes Catalog (`4520` / `9150` NM) | Static (`1` value/route) | Static | `0.0%` | Single-route $\sigma^2=0$ | Excluded from single-corridor linear regressions |
| **Bilateral Maritime Trade** (`trade_volume_grain_us_jp`) | USDA FAS GATS (`8`) + Indian DGCI&S (`5`) | **`13` rows** (`2024-10`–`2024-12`) | Monthly post-period | **`99.1%`** | **No** | **Excluded — Future Data Requirement** |
| **Oceanographic Buoys** (`wave_height`, `wind_speed`) | NOAA NDBC (`46041`, `42001`) + INCOIS (`AD01`) | **`13` snapshots** (`2024-10`, `2026-10`) | Snapshot telemetry | **`99.9%`** | **No** | **Excluded — Future Data Requirement** |
| **Commercial Bunker Fuel** (`bunker_price_vlsfo_delivered`) | Bunker Fuel Prices (`ARBUE`: `2`, `DKSKA`: `3`; core corridors `0`) | **`5` spot rows** (`2026-11-06`) | Spot indication | **`100.0%`** pre-Nov 2026 | **No** | **Excluded — Future Data Requirement** |
| **Vessel AIS Positions** (`ais_fleet_active_density`) | MarineCadastre.gov (`vessel_positions`) | **`120` rows** (`2024-01-01` 10s snapshot) | Snapshot telemetry | **`>99.7%`** | **No** | **Excluded — Future Data Requirement** |

---

## 3. Regime Sample-Size & Insufficient-History Constraints

Inspection of chronological strict-forward labeled rows establishes:
- **Origin vs. Target Regime Transitions:** Under strict-forward target offsets, `origin_regime != target_regime` on `6` rows ($T+1$), `12` rows ($T+3$), and `21` rows ($T+6$). Predictor models must never read `target_date` regimes.
- **Same-Regime Training Scarcity:** Maximum eligible historical pairs with `origin_regime_j == origin_regime_i` and realized `target_date_j <= obs_date_i`:
  - `PRE_SUPERCYCLE`: max `118` ($T+1$), `116` ($T+3$), `113` ($T+6$)
  - **`SUPERCYCLE_PEAK_CRASH` (`36` months total):** max **`34`** ($T+1$), **`32`** ($T+3$), **`29`** ($T+6$) $\implies$ **strictly $< 36$ at every origin (`0` trainable steps at `min_training_samples = 36`)**
  - `POST_SUPERCYCLE`: max `118 / 116 / 113` (first `37 / 39 / 42` steps insufficient)
  - `PANDEMIC_DISRUPTION`: max `78 / 74 / 68` (first `37 / 39 / 42` steps insufficient)

Accordingly, standalone regime-partitioned training is evaluated **without silent fallback** (`insufficient_regime_training_history (<36)` explicitly recorded) and compared against Persistence strictly on the **exact identical valid target-date subset** (`N_sub`) separately from the full `N = 319 / 315 / 309` common-sample comparison.

---

## 4. Candidate Experiments Registry

1. **`persistence`** *(Frozen Production Baseline)*: $y_{t+h|t} = y_t$.
2. **`ridge_autoregressive`** *(C3.2 Best Candidate Comparison)*: `Ridge(alpha=1.0, random_state=42)` on 6 AR lags (`rate_lag_0, 1, 2, 3, 6, 12`).
3. **`regime_conditioned_selector`** *(Regime-Conditioned Walk-Forward Model Selection)*: At origin $i$, checks completed realized errors in the active decision-time `origin_date` regime ($N_{\text{val}} \ge 12$); selects `ridge_autoregressive` iff prior in-regime MAE `<` Persistence MAE, otherwise defaults to `persistence`.
4. **`regime_interaction_ridge_ar`** *(Controlled Regime-Interaction Regularized Regression)*: `Ridge(alpha=1.0)` on 6 AR lags + 3 decision-time origin-regime dummies + 3 controlled interactions (`origin_regime_flag * rate_diff_1`), allowing slope modulation of short-run momentum across regimes without reading future targets.
5. **`multivariate_ar_spread`** *(Isolated Cross-Corridor Multivariate Test)*: `Ridge(alpha=1.0)` on 6 AR lags + 3 cross-corridor spread features (`competitor_rate_lag_0`, `corridor_spread_lag_0`, `corridor_spread_mean_3`).
6. **`multivariate_ar_vol_seasonal`** *(Isolated Volatility & Seasonality Multivariate Test)*: `Ridge(alpha=1.0)` on 6 AR lags + 5 own-route dispersion/seasonal features (`rolling_std_6`, `rolling_std_12`, `rolling_ratio_12`, `origin_sin_month`, `origin_cos_month`).
7. **`regime_partitioned_ridge_ar`** *(Strict Non-Fallback Separate Regime Model — Matched-Subset Audit)*: Trains `Ridge(alpha=1.0)` on 6 AR lags exclusively on eligible historical observations matching the decision-time origin regime when $N_{\text{regime}} \ge 36$; abstains otherwise.

---

## 5. Acceptance Criteria

- Zero target-period regime leakage (`row.regime_id` mutations do not alter any predicted value).
- Zero training-cutoff or scaling leakage (`StandardScaler` fit on `X_train` only; eligible `target_date <= obs_date` and `target_availability_date <= dec_date`).
- Exact common-sample target-date alignment (`319 / 315 / 309` for full-sample grid; matched `N_sub` for partitioned abstention test).
- Explicit reporting of insufficient-regime counts (`SUPERCYCLE_PEAK_CRASH = 0 valid / 36 skipped`) and Holm–Bonferroni corrected DM/HLN statistical significance.
- Housekeeping fix: `generate_feature_inventory()` derives `TradeObservation` count (`13`) from the database rather than hardcoding `11`.
- Persistence remains default in [`schemas.py`](../../backend/pelagos/forecasting/schemas.py) and [`service.py`](../../backend/pelagos/forecasting/service.py); all existing C1/C3.1/C3.2 tests + new C3.3 tests pass cleanly.

---

## 6. Traceability & Results

- **Upstream:** [`RES-C31-001`](./c3-1-feature-coverage-and-forecast-dataset.md), [`REPORT-C32-ML-BENCHMARK-001`](./c3-2-candidate-ml-benchmarking-report.md), [`ML-001`](./freight-index-and-forecasting-specification.md)
- **Implementation:** [`regime_experiments.py`](../../backend/pelagos/forecasting/regime_experiments.py), [`test_regime_aware_experiments.py`](../../tests/test_regime_aware_experiments.py)
- **Empirical Report:** [`REPORT-C33-REGIME-001`](./c3-3-regime-aware-forecasting-report.md) & [`c33_regime_investigation_results.json`](./artifacts/c33_regime_investigation_results.json)

---

## 7. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-11-11 | Implementation Agent | Promoted `SPEC-C33-REGIME-001` specification to permanent `docs/forecasting/` directory conforming to `DOC-001`. |

