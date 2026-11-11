---
title: "Stage C3.3: Regime-Aware & Controlled Multivariate Forecasting Investigation Report"
document_id: REPORT-C33-REGIME-001
type: research_notes
status: approved
version: 1.0.0
created: 2026-11-11
updated: 2026-11-11
tags:
  - forecasting
  - regime-aware
  - multivariate
  - statistical-testing
  - stage-c3-3
---

# Pelagos — Stage C3.3: Regime-Aware & Controlled Multivariate Forecasting Investigation Report

> [!NOTE]
> **Research Note Boundary (`DOC-001` Section 6):** This report documents empirical Stage C3.3 research outcomes. **Zero models were promoted to production; `persistence` (`DEFAULT_BASELINE_MODEL`) remains the active production forecast benchmark.**

**Specification Trace:** [`SPEC-C33-REGIME-001`](./c3-3-implementation-specification.md)  
**Baseline Reference:** Stage C3.2 ([`REPORT-C32-ML-BENCHMARK-001`](./c3-2-candidate-ml-benchmarking-report.md), baseline commit `7c0137fe8b60cb3951bd31dd7350513b524a67e0` + C3.1.2/C3.2 working tree)  
**Reproducibility Artifact:** [`c33_regime_investigation_results.json`](./artifacts/c33_regime_investigation_results.json) (SHA-256 prefix `592a324ec23990d5`)  
**Production Baseline Status:** **`persistence` (`DEFAULT_BASELINE_MODEL`) remains unchanged and active.**

---

## 1. Executive Summary & Empirical Verdict

> [!IMPORTANT]
> **Empirical Verdict: Neither decision-time regime-aware modeling nor orthogonal multivariate additions from currently available historical features outperform the operational Persistence (`persistence`) baseline across `PNW-JPN-BULK` or `USG-JPN-BULK` ($T+1, T+3, T+6$).**
> Zero Stage C3.3 experiments achieve `PROMISING` status or statistically significant MAE improvement over Persistence (all Holm–Bonferroni adjusted $p \ge 0.4674$ where favorable, and all favorable sub-regime differences remain `INCONCLUSIVE`). **Persistence remains the frozen production baseline.**

Key conclusions from reproducible walk-forward execution (`min_training_samples = 36`, `seed = 42`, strict-forward C3.1.2 semantics):
1. **Regime-Conditioned Walk-Forward Selector (`regime_conditioned_selector`):** Choosing strictly between `persistence` and C3.2 `ridge_autoregressive` using only realized prior in-regime MAE ($\ge 12$ completed validation steps, assigned by decision-time `origin_date`) eliminates the `+7.86%` to `+21.59%` degradation seen in unconditional `ridge_autoregressive`, landing within `0.00%` to `+1.89%` of Persistence on 5 corridor-horizon pairs and `-0.86%` (`11.59` vs `11.69` USD/MT, $p_{\text{Holm}} = 0.7603$, 95% CI `[-0.4001, +0.6401]`) on `USG-JPN-BULK` $T+6$. All 6 experiments classify **`INCONCLUSIVE`**.
2. **Controlled Regime Interaction (`regime_interaction_ridge_ar`):** Interacting decision-time origin regime flags with short-term momentum (`rate_diff_1`) worsens MAE relative to **both** Persistence (`+12.43%` to `+35.76%`) and C3.2 `ridge_autoregressive` across all 6 cells (`NOT_BETTER_THAN_BASELINE` in all 6 cells before/after CI & error-magnitude thresholds).
3. **Strict Non-Fallback Separate Regime Training (`regime_partitioned_ridge_ar`):** Proves empirically why separate regime models fail historically under Pelagos sample-size guardrails (`min_training_samples = 36`): `SUPERCYCLE_PEAK_CRASH` (`36` months total) attains at most `34` ($T+1$), `32` ($T+3$), and `29` ($T+6$) eligible historical pairs, causing **`0` valid steps and `36` abstained steps (100% insufficient history)**. Even on the exact `N_sub = 209 / 201 / 189` matched subset where regimes have $\ge 36$ training rows, Persistence and pooled `ridge_autoregressive` beat `regime_partitioned_ridge_ar` in **every single corridor and horizon**.
4. **Controlled Multivariate Feature Tests (`multivariate_ar_spread` & `multivariate_ar_vol_seasonal`):** Adding cross-corridor/spread predictors (`competitor_rate_lag_0`, `corridor_spread_lag_0`, `corridor_spread_mean_3`) or own-route volatility/mean-reversion + harmonic seasonality (`rolling_std_6/12`, `rolling_ratio_12`, `origin_sin/cos_month`) onto `ridge_autoregressive` increases out-of-sample MAE in **all 6 experiments** relative to both `Persistence` and `ridge_autoregressive`.
5. **External Non-Freight Domains Ineligible:** Live DB audits confirm bilateral trade (`13` rows, `99.1%` missing), buoys (`13` snapshots, `99.9%` missing), bunker fuel (`5` spot quotes on Nov 6, 2026; corridor hubs `0`), and AIS (`120` single-snapshot rows) cannot support multi-decade historical backtests and remain excluded as future data requirements.

### Why Persistence Remains Active in Production
- No tested ML, regime-conditioned, regime-partitioned, or historical multivariate model beat Persistence reliably out-of-sample across cycles.
- Retaining `"persistence"` prevents adding model-selection complexity or overfitting variance (`+12%` to `+48%` MAE spikes in regime interaction / volatility extensions) without statistically backed predictive gain.
- [`service.py`](../../backend/pelagos/forecasting/service.py) and `DEFAULT_BASELINE_MODEL` in [`schemas.py`](../../backend/pelagos/forecasting/schemas.py) remain untouched at `"persistence"`.

---

## 2. Multivariate Feasibility Audit Across All C3.1 Features & Data Domains

Verified live SQLite counts (`pelagos.db`) and historical modeling feasibility:

| Domain / Feature Family | Provenance | Historical Coverage & DB Count | Availability Timing | Missingness / Usable $N$ | Available at `decision_timestamp`? | Incremental Signal Beyond AR Lags |
|:---|:---|:---|:---|:---:|:---:|:---|
| **Autoregressive Freight Lags** (`6` features) | USDA AMS GTR Figure 20 / O'Neil (`ehs5-yac3`) | `370` months/route (`1996-01`–`2026-10`; `1,110` freight rows incl. spread) | Day 14 provisional operational assumption (`< dec_dt`) | `0.0%` (`356/354/351` labeled) | **Yes** | Baseline comparison (`ridge_autoregressive`) |
| **Cross-Corridor & Spread** (`3` added: `competitor_rate_lag_0`, `corridor_spread_lag_0`, `corridor_spread_mean_3`) | Competitor USDA AMS corridor (`USG` $\leftrightarrow$ `PNW`) | `370` months/route (`1996-01`–`2026-10`) | Strictly filtered `cutoff < dec_dt` | `0.0%` (`319/315/309` eval steps) | **Yes** | **Tested (`multivariate_ar_spread`) — zero aggregate improvement; hurts MAE in 6/6 experiments** |
| **Momentum, Volatility & Seasonality** (`5` added: `rolling_std_6/12`, `rolling_ratio_12`, `origin_sin/cos_month`) | Deterministic own-route freight transforms & calendar harmonics | `370` months (`1996-01`–`2026-10`) | Realized at `origin_date` | `0.0%` (`319/315/309` eval steps) | **Yes** | **Tested (`multivariate_ar_vol_seasonal`) — degrades MAE across 6/6 experiments (`+23.21%` to `+47.73%`)** |
| **Market Regime Indicators** (`3` non-ref `origin_date` flags + `rate_diff_1` interactions) | C1.3.2 macroeconomic segments anchored at `origin_date` | `370` months (`1996-01`–`2026-10`) | Known at `origin_date` (never `target_date`) | `0.0%` (`319/315/309` eval steps) | **Yes** | **Tested (`regime_interaction_ridge_ar` & `regime_conditioned_selector`) — no significant gain** |
| **Port / Route Metadata** (`route_benchmark_distance_nm`) | Freight Routes Catalog (`4520.0` / `9150.0` NM) | Invariant static (`1` value/corridor) | Static | `0.0%` (zero intra-corridor variance) | Yes (zero variance) | Non-informative inside single-corridor models |
| **Bilateral Trade Volumes** | USDA FAS GATS (`8` US $\rightarrow$ JP) + Indian DGCI&S (`5`) | **`13` DB rows** (`2024-10` to `2024-12` only) | Post-month monthly | **`99.1%` missing** | N/A | **Infeasible — Future data req: 1996–present monthly bulk grain exports** |
| **Oceanographic Buoys** | NOAA NDBC (`46041`: `6`, `42001`: `5`) + INCOIS (`AD01`: `2`) | **`13` snapshots** (`2024-10-15`, `2026-10-30`) | Snapshot telemetry | **`99.9%` missing** | N/A | **Infeasible — Future data req: 1996–present Pacific/Gulf marine weather indices** |
| **Commercial Bunker Fuel** | Bunker Fuel Prices API (`ARBUE`: `2`, `DKSKA`: `3`) | **`5` spot quotes** (`2026-11-06`; core corridors `0`) | Spot indication | **`100.0%` missing** pre-Nov 2026 | N/A | **Infeasible — Future data req: historical VLSFO/IFO380 at PNW/USG/JP hubs** |
| **Vessel AIS Telemetry** | NOAA MarineCadastre (`vessel_positions`) | **`120` rows** (single-window snapshot) | Snapshot telemetry | **`>99.7%` missing** | N/A | **Infeasible — Future data req: multi-year port congestion / queue-time indices** |

---

## 3. Aggregate Walk-Forward Results (`N = 319 / 315 / 309`)

*(Verified identical against [`c33_regime_investigation_results.json`](./artifacts/c33_regime_investigation_results.json).)*

### Table 3.1: `PNW-JPN-BULK` Common-Sample Comparisons
| Horizon ($N$) | Experiment | MAE | RMSE | MASE | MDA (%) | Rel MAE vs P | HLN Stat | Raw $p$ | Holm $p$ | 95% Bootstrap CI | Classification |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **$T+1$ (`319`)** | **`persistence`** | **3.49** | **5.91** | **1.97** | `N/A` | `0.00%` | — | — | — | — | **ACTIVE BASELINE** |
| | `ridge_autoregressive` (C3.2) | 3.97 | 6.63 | 2.27 | `52.37` | `+13.75%` | `-3.2830` | `0.0011` | `0.0033` | `[-0.8191, -0.1846]` | `NOT_BETTER_THAN_BASELINE` |
| | `regime_conditioned_selector` | **3.49** | **5.91** | **1.97** | `54.72` | `0.00%` | `+0.1141` | `0.9092` | `0.9092` | `[-0.0375, +0.0629]` | **`INCONCLUSIVE`** |
| | `regime_interaction_ridge_ar` | 4.03 | 6.82 | 2.30 | `52.68` | `+15.47%` | `-3.2814` | `0.0011` | `0.0033` | `[-0.9183, -0.2035]` | `NOT_BETTER_THAN_BASELINE` |
| | `multivariate_ar_spread` | 4.13 | 6.82 | 2.36 | `54.89` | `+18.34%` | `-3.5631` | `0.0004` | `0.0016` | `[-1.1005, -0.2133]` | `NOT_BETTER_THAN_BASELINE` |
| | `multivariate_ar_vol_seasonal` | 4.30 | 7.31 | 2.50 | `52.68` | `+23.21%` | `-4.9113` | `0.0000` | `0.0000` | `[-1.2454, -0.4481]` | `NOT_BETTER_THAN_BASELINE` |
| **$T+3$ (`315`)** | **`persistence`** | **5.28** | **9.03** | **3.10** | `N/A` | `0.00%` | — | — | — | — | **ACTIVE BASELINE** |
| | `ridge_autoregressive` (C3.2) | 6.42 | 10.95 | 3.82 | `52.06` | `+21.59%` | `-2.4828` | `0.0136` | `0.0376` | `[-1.9248, -0.4783]` | `NOT_BETTER_THAN_BASELINE` |
| | `regime_conditioned_selector` | 5.38 | 9.58 | 3.15 | `49.28` | `+1.89%` | `-0.5873` | `0.5574` | `0.5574` | `[-0.3907, +0.1355]` | **`INCONCLUSIVE`** |
| | `regime_interaction_ridge_ar` | 6.77 | 11.48 | 4.00 | `55.24` | `+28.22%` | `-3.2904` | `0.0011` | `0.0055` | `[-2.2695, -0.7550]` | `NOT_BETTER_THAN_BASELINE` |
| | `multivariate_ar_spread` | 6.84 | 11.35 | 4.01 | `53.97` | `+29.55%` | `-2.6134` | `0.0094` | `0.0376` | `[-2.5998, -0.6538]` | `NOT_BETTER_THAN_BASELINE` |
| | `multivariate_ar_vol_seasonal` | 6.54 | 11.46 | 3.98 | `53.33` | `+23.86%` | `-2.6139` | `0.0094` | `0.0376` | `[-2.1021, -0.5181]` | `NOT_BETTER_THAN_BASELINE` |
| **$T+6$ (`309`)** | **`persistence`** | **7.06** | **11.49** | **4.26** | `N/A` | `0.00%` | — | — | — | — | **ACTIVE BASELINE** |
| | `ridge_autoregressive` (C3.2) | 8.51 | 14.30 | 5.22 | `54.69` | `+20.54%` | `-2.0376` | `0.0424` | `0.1272` | `[-2.6427, -0.3218]` | `NOT_BETTER_THAN_BASELINE` |
| | `regime_conditioned_selector` | 7.09 | 11.55 | 4.31 | `60.13` | `+0.42%` | `-0.2072` | `0.8360` | `0.8360` | `[-0.3446, +0.2811]` | **`INCONCLUSIVE`** |
| | `regime_interaction_ridge_ar` | 9.49 | 17.53 | 5.74 | `52.43` | `+34.42%` | `-1.9393` | `0.0534` | `0.1272` | `[-4.7100, -0.7566]` | `NOT_BETTER_THAN_BASELINE` |
| | `multivariate_ar_spread` | 9.53 | 14.46 | 5.64 | `54.05` | `+34.99%` | `-2.9625` | `0.0033` | `0.0165` | `[-4.0868, -1.0577]` | `NOT_BETTER_THAN_BASELINE` |
| | `multivariate_ar_vol_seasonal` | 8.91 | 14.90 | 5.51 | `57.28` | `+26.20%` | `-2.5604` | `0.0109` | `0.0436` | `[-3.1211, -0.7198]` | `NOT_BETTER_THAN_BASELINE` |

### Table 3.2: `USG-JPN-BULK` Common-Sample Comparisons
| Horizon ($N$) | Experiment | MAE | RMSE | MASE | MDA (%) | Rel MAE vs P | HLN Stat | Raw $p$ | Holm $p$ | 95% Bootstrap CI | Classification |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **$T+1$ (`319`)** | **`persistence`** | **5.47** | **9.08** | **2.21** | `N/A` | `0.00%` | — | — | — | — | **ACTIVE BASELINE** |
| | `ridge_autoregressive` (C3.2) | 5.90 | 10.05 | 2.40 | `56.74` | `+7.86%` | `-1.9756` | `0.0491` | `0.1473` | `[-0.9281, +0.0434]` | `NOT_BETTER_THAN_BASELINE` |
| | `regime_conditioned_selector` | 5.54 | 9.35 | 2.26 | `54.65` | `+1.28%` | `-0.7275` | `0.4674` | `0.4674` | `[-0.3161, +0.1464]` | **`INCONCLUSIVE`** |
| | `regime_interaction_ridge_ar` | 6.15 | 10.55 | 2.49 | `53.29` | `+12.43%` | `-2.4980` | `0.0130` | `0.0520` | `[-1.2650, -0.1729]` | `NOT_BETTER_THAN_BASELINE` |
| | `multivariate_ar_spread` | 5.95 | 10.10 | 2.34 | `57.99` | `+8.78%` | `-1.6272` | `0.1047` | `0.2094` | `[-1.2703, +0.2212]` | **`INCONCLUSIVE`** |
| | `multivariate_ar_vol_seasonal` | 6.81 | 11.68 | 2.73 | `52.35` | `+24.50%` | `-4.1056` | `0.0001` | `0.0005` | `[-2.2742, -0.5406]` | `NOT_BETTER_THAN_BASELINE` |
| **$T+3$ (`315`)** | **`persistence`** | **8.55** | **14.43** | **3.50** | `N/A` | `0.00%` | — | — | — | — | **ACTIVE BASELINE** |
| | `ridge_autoregressive` (C3.2) | 10.00 | 17.65 | 4.12 | `52.38` | `+16.96%` | `-2.0305` | `0.0431` | `0.1293` | `[-2.6988, -0.4515]` | `NOT_BETTER_THAN_BASELINE` |
| | `regime_conditioned_selector` | 8.64 | 14.71 | 3.55 | `53.42` | `+1.05%` | `-0.6641` | `0.5071` | `0.5071` | `[-0.2927, +0.1467]` | **`INCONCLUSIVE`** |
| | `regime_interaction_ridge_ar` | 10.74 | 19.27 | 4.41 | `53.33` | `+25.61%` | `-2.8328` | `0.0049` | `0.0245` | `[-3.5780, -0.9235]` | `NOT_BETTER_THAN_BASELINE` |
| | `multivariate_ar_spread` | 10.40 | 17.56 | 4.05 | `59.68` | `+21.64%` | `-1.9514` | `0.0519` | `0.1293` | `[-3.5345, -0.4475]` | `NOT_BETTER_THAN_BASELINE` |
| | `multivariate_ar_vol_seasonal` | 12.34 | 22.21 | 4.96 | `51.75` | `+44.33%` | `-2.8266` | `0.0050` | `0.0245` | `[-6.2036, -1.8861]` | `NOT_BETTER_THAN_BASELINE` |
| **$T+6$ (`309`)** | **`persistence`** | 11.69 | **18.71** | **4.81** | `N/A` | `0.00%` | — | — | — | — | **ACTIVE BASELINE** |
| | `ridge_autoregressive` (C3.2) | 12.95 | 22.77 | 5.45 | `60.19` | `+10.78%` | `-1.1896` | `0.2351` | `0.4702` | `[-3.0714, +0.4781]` | `INCONCLUSIVE` |
| | `regime_conditioned_selector` | **11.59** | 18.89 | 4.88 | `62.94` | **`-0.86%`** | `+0.3054` | `0.7603` | `0.7603` | `[-0.4001, +0.6401]` | **`INCONCLUSIVE`** |
| | `regime_interaction_ridge_ar` | 15.87 | 27.96 | 6.44 | `55.02` | `+35.76%` | `-2.0417` | `0.0420` | `0.1260` | `[-7.9487, -1.6198]` | `NOT_BETTER_THAN_BASELINE` |
| | `multivariate_ar_spread` | 15.36 | 22.80 | 6.15 | `56.31` | `+31.39%` | `-2.4456` | `0.0150` | `0.0750` | `[-6.3711, -1.3890]` | `NOT_BETTER_THAN_BASELINE` |
| | `multivariate_ar_vol_seasonal` | 17.27 | 29.75 | 7.15 | `54.37` | `+47.73%` | `-2.3689` | `0.0185` | `0.0750` | `[-9.6677, -2.3760]` | `NOT_BETTER_THAN_BASELINE` |

---

## 4. Regime-Specific Analysis & Non-Fallback Abstention Audit

### 4.1 Decision-Time Transition Audit & `regime_partitioned_ridge_ar`
Strict decision-time regime assignment (`origin_date`) avoids reading future `target_date` regimes on `6` ($T+1$), `12` ($T+3$), and `21` ($T+6$) regime-boundary rows. Under non-fallback `regime_partitioned_ridge_ar` (`min_training_samples = 36` within the active origin regime):

| Corridor & Horizon | Valid / Skipped Steps (`N_sub` / `356..351`) | `PRE_SUPERCYCLE` Valid/Skip | `SUPERCYCLE_PEAK_CRASH` Valid/Skip | `POST_SUPERCYCLE` Valid/Skip | `PANDEMIC_DISRUPTION` Valid/Skip | Matched-Subset `Persistence` MAE | Matched-Subset `Ridge AR` MAE | `Partitioned AR` MAE ($p$-val, 95% CI) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`PNW` $T+1$** | `209` / `147` | `83` / `37` | **`0` / `36` (`INSUFFICIENT`)** | `83` / `37` | `43` / `37` | **2.26** | 2.59 | 2.75 (`p=0.0001`, `[-0.78, -0.23]`) |
| **`PNW` $T+3$** | `201` / `153` | `81` / `39` | **`0` / `36` (`INSUFFICIENT`)** | `81` / `39` | `39` / `39` | **3.55** | 4.43 | 4.57 (`p=0.0162`, `[-1.81, -0.36]`) |
| **`PNW` $T+6$** | `189` / `162` | `78` / `42` | **`0` / `36` (`INSUFFICIENT`)** | `78` / `42` | `33` / `42` | **5.24** | 6.67 | 7.00 (`p=0.0191`, `[-3.07, -0.78]`) |
| **`USG` $T+1$** | `209` / `147` | `83` / `37` | **`0` / `36` (`INSUFFICIENT`)** | `83` / `37` | `43` / `37` | **3.89** | 4.22 | 4.53 (`p=0.0016`, `[-1.14, -0.18]`) |
| **`USG` $T+3$** | `201` / `153` | `81` / `39` | **`0` / `36` (`INSUFFICIENT`)** | `81` / `39` | `39` / `39` | **6.22** | 6.99 | 7.34 (`p=0.0750`, `[-2.22, -0.10]`) |
| **`USG` $T+6$** | `189` / `162` | `78` / `42` | **`0` / `36` (`INSUFFICIENT`)** | `78` / `42` | `33` / `42` | **8.68** | 9.66 | 10.23 (`p=0.1802`, `[-3.59, +0.06]`) |

### 4.2 Target-Date Regime Stratification (MAE / Raw $p$ vs Persistence)
| Experiment | Regime ($N$) | `Persistence` | `Ridge AR` (C3.2) | `Selector` | `Regime x AR` | `AR + Spread` | `AR + VolSeas` |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **`PNW` $T+1$** | `PRE_SUPERCYCLE` (`81`) | **3.07** | 3.95 (`0.0017`) | **3.07** (`1.0000`) | 3.95 (`0.0017`) | 4.03 (`0.0066`) | 4.38 (`0.0003`) |
| | `SUPERCYCLE_PEAK_CRASH` (`36`) | **10.45** | 12.49 (`0.0594`) | **10.45** (`1.0000`) | 11.93 (`0.1758`) | 13.38 (`0.0190`) | 13.77 (`0.0023`) |
| | `POST_SUPERCYCLE` (`120`) | **2.26** | 2.36 (`0.1954`) | **2.26** (`1.0000`) | 2.59 (`0.0926`) | 2.40 (`0.2850`) | 2.49 (`0.1094`) |
| | `PANDEMIC_DISRUPTION` (`82`) | 2.64 | **2.59** (`0.6730`) | 2.63 (`0.9098`) | 2.75 (`0.5693`) | 2.68 (`0.8050`) | 2.73 (`0.5448`) |
| **`USG` $T+1$** | `PRE_SUPERCYCLE` (`81`) | 4.67 | 5.70 (`0.0262`) | 4.94 (`0.2154`) | 5.70 (`0.0262`) | **4.54** (`0.7997`) | 6.23 (`0.0068`) |
| | `SUPERCYCLE_PEAK_CRASH` (`36`) | **15.20** | 17.71 (`0.0775`) | 16.05 (`0.1154`) | 17.05 (`0.2870`) | 18.35 (`0.1283`) | 21.63 (`0.0045`) |
| | `POST_SUPERCYCLE` (`120`) | 3.26 | **3.22** (`0.8265`) | 3.24 (`0.2079`) | 3.82 (`0.0637`) | 3.64 (`0.1535`) | 4.00 (`0.0120`) |
| | `PANDEMIC_DISRUPTION` (`82`) | 5.23 | **4.82** (`0.1064`) | 4.90 (`0.1892`) | 5.22 (`0.9959`) | 5.27 (`0.8909`) | 4.98 (`0.4391`) |
| **`PNW` $T+6$** | `PRE_SUPERCYCLE` (`71`) | **7.57** | 11.09 (`0.0368`) | 7.75 (`0.1284`) | 11.09 (`0.0368`) | 11.06 (`0.0570`) | 10.68 (`0.0768`) |
| | `SUPERCYCLE_PEAK_CRASH` (`36`) | **20.33** | 27.86 (`0.0085`) | **20.33** (`1.0000`) | 34.30 (`0.0877`) | 24.55 (`0.0258`) | 28.86 (`0.0000`) |
| | `POST_SUPERCYCLE` (`120`) | 3.78 | **3.53** (`0.6141`) | 3.88 (`0.7797`) | 3.82 (`0.9297`) | 6.12 (`0.0845`) | 4.25 (`0.4347`) |
| | `PANDEMIC_DISRUPTION` (`82`) | 5.58 | **5.08** (`0.2893`) | 5.42 (`0.6375`) | 5.53 (`0.9360`) | 6.59 (`0.4056`) | 5.43 (`0.8521`) |
| **`USG` $T+6$** | `PRE_SUPERCYCLE` (`71`) | **9.87** | 12.75 (`0.2711`) | 10.57 (`0.2741`) | 12.75 (`0.2711`) | 13.09 (`0.2326`) | 18.12 (`0.2159`) |
| | `SUPERCYCLE_PEAK_CRASH` (`36`) | **35.58** | 46.08 (`0.0204`) | **35.58** (`1.0000`) | 57.62 (`0.0969`) | 40.31 (`0.3832`) | 53.79 (`0.0104`) |
| | `POST_SUPERCYCLE` (`120`) | 6.18 | **5.16** (`0.1714`) | 5.80 (`0.5541`) | 8.81 (`0.1163`) | 11.31 (`0.0362`) | 10.51 (`0.0919`) |
| | `PANDEMIC_DISRUPTION` (`82`) | 10.84 | **9.97** (`0.3474`) | 10.40 (`0.4545`) | 10.59 (`0.8352`) | 12.29 (`0.4925`) | 10.38 (`0.7611`) |

*(Full $T+3$ regime tables are preserved identically in [`c33_regime_investigation_results.json`](./artifacts/c33_regime_investigation_results.json); in $T+3$, `Selector` stays within `5.00–8.65` and every favorable candidate sub-regime MAE carries raw $p \ge 0.5062$, remaining `INCONCLUSIVE`.)*

---

## 5. Statistical Findings, Research Assessment & Production Boundary

- **Statistical Summary:** Across all `30` full-sample candidate hypothesis tests (`5` models $\times$ `6` corridor/horizon cells) and all `6` matched-subset abstention comparisons, **zero** tests yield significant improvement (`p < 0.05` favoring candidate or `PROMISING`).
- **Merit for Further Investigation:** Deeper parametric complexity over existing monthly USDA rates alone is **not** warranted; future model improvement depends on acquiring genuinely exogenous multi-decade predictors (`1996–present` bulk grain flows, bunker fuel series, fleet supply / congestion).
- **Production Safeguard:** **`persistence` remains the sole active production forecasting baseline.** [`service.py`](../../backend/pelagos/forecasting/service.py) and `DEFAULT_BASELINE_MODEL` ([`schemas.py`](../../backend/pelagos/forecasting/schemas.py)) remain unchanged.

---

## 6. Traceability, Artifacts & Verification

- **Specification:** [`SPEC-C33-REGIME-001`](./c3-3-implementation-specification.md)
- **Implementation & Tests:** [`regime_experiments.py`](../../backend/pelagos/forecasting/regime_experiments.py), [`dataset.py`](../../backend/pelagos/forecasting/dataset.py) (Section 7 DB-derived `generate_feature_inventory()` fix), [`test_regime_aware_experiments.py`](../../tests/test_regime_aware_experiments.py) (`8` tests), [`test_forecast_dataset.py`](../../tests/test_forecast_dataset.py) (`60` tests)
- **Reproducibility JSON:** [`c33_regime_investigation_results.json`](./artifacts/c33_regime_investigation_results.json)
- **Verification Snapshot (stage acceptance & full regression):** `8 passed` (`test_regime_aware_experiments.py`), `81 passed` (`C3.1+C3.2+C3.3` suite), `508 passed, 6 skipped` pre-Stage-D and `519 passed, 6 skipped` post-Stage-D.

---

## 7. Change History

| Version | Date | Author | Description |
|---|---|---|---|
| 1.0.0 | 2026-11-11 | Implementation Agent | Promoted verified Stage C3.3 empirical investigation report and linked permanent [`c33_regime_investigation_results.json`](./artifacts/c33_regime_investigation_results.json) artifact under `DOC-001`. |

