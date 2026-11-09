"""Pelagos Feature Engineering and Feature Catalog for Maritime Freight Forecasting.

Defines the authoritative feature dictionary, mathematical formulas, lookback requirements,
and leakage-safe feature extraction functions for Stage C3.1.

Traces to DATA-001 Section 5.2, ML-001, and Stage C3.1 requirements:
- Strictly empirical: only genuine historical data with verified coverage is computed.
- Leakage-safe: features at origin t depend exclusively on observations at or before t.
- Excluded features (trade volumes, environmental buoys, bunker prices) are audited and
  documented with empirical justification rather than backfilled with synthetic proxies.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from pelagos.forecasting.regimes import (
    HISTORICAL_REGIMES,
    MarketRegimeId,
    classify_target_date_regime,
)

# Route-level static metadata
ROUTE_METADATA: Dict[str, Dict[str, Any]] = {
    "PNW-JPN-BULK": {
        "benchmark_distance_nm": 4520.0,
        "origin_region": "U.S. Pacific Northwest",
        "destination_region": "Japan",
        "competitor_route": "USG-JPN-BULK",
        "cargo_type": "Agricultural Bulk Grain",
        "vessel_class": "Panamax / Supramax",
    },
    "USG-JPN-BULK": {
        "benchmark_distance_nm": 9150.0,
        "origin_region": "U.S. Gulf Coast",
        "destination_region": "Japan",
        "competitor_route": "PNW-JPN-BULK",
        "cargo_type": "Agricultural Bulk Grain",
        "vessel_class": "Panamax / Supramax",
    },
}


@dataclass(frozen=True)
class FeatureDefinition:
    """Authoritative metadata and documentation contract for an explanatory feature."""

    name: str
    category: str  # "autoregressive_lag" | "momentum" | "rolling_statistic" | "seasonality" | "regime" | "cross_corridor" | "route_metadata" | "excluded_source"
    unit: str
    source: str
    formula: str
    description: str
    required_lookback_months: int
    is_usable_historically: bool
    exclusion_reason: Optional[str] = None
    is_time_varying: bool = True
    redundancy_group: Optional[str] = None
    collinearity_notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Comprehensive feature dictionary documenting both usable and audited-excluded features
FEATURE_CATALOG: Dict[str, FeatureDefinition] = {
    # 1. Autoregressive Lags (Own Route)
    "rate_lag_0": FeatureDefinition(
        name="rate_lag_0",
        category="autoregressive_lag",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="y_t",
        description="Latest observed freight rate at forecast origin date t. Also serves as the Persistence baseline forecast.",
        required_lookback_months=0,
        is_usable_historically=True,
    ),
    "rate_lag_1": FeatureDefinition(
        name="rate_lag_1",
        category="autoregressive_lag",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="y_{t-1}",
        description="Freight rate 1 month prior to forecast origin date t.",
        required_lookback_months=1,
        is_usable_historically=True,
    ),
    "rate_lag_2": FeatureDefinition(
        name="rate_lag_2",
        category="autoregressive_lag",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="y_{t-2}",
        description="Freight rate 2 months prior to forecast origin date t.",
        required_lookback_months=2,
        is_usable_historically=True,
    ),
    "rate_lag_3": FeatureDefinition(
        name="rate_lag_3",
        category="autoregressive_lag",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="y_{t-3}",
        description="Freight rate 3 months prior to forecast origin date t.",
        required_lookback_months=3,
        is_usable_historically=True,
    ),
    "rate_lag_6": FeatureDefinition(
        name="rate_lag_6",
        category="autoregressive_lag",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="y_{t-6}",
        description="Freight rate 6 months prior to forecast origin date t (semi-annual lag).",
        required_lookback_months=6,
        is_usable_historically=True,
    ),
    "rate_lag_12": FeatureDefinition(
        name="rate_lag_12",
        category="autoregressive_lag",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="y_{t-12}",
        description="Freight rate 12 months prior to forecast origin date t (annual lag).",
        required_lookback_months=12,
        is_usable_historically=True,
    ),
    # 2. Rate Changes and Momentum (Own Route)
    "rate_diff_1": FeatureDefinition(
        name="rate_diff_1",
        category="momentum",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="y_t - y_{t-1}",
        description="Month-over-month absolute rate change.",
        required_lookback_months=1,
        is_usable_historically=True,
        redundancy_group="rate_differencing",
        collinearity_notes="Exact linear combination: rate_lag_0 - rate_lag_1. High collinearity with lags in linear models.",
    ),
    "rate_diff_3": FeatureDefinition(
        name="rate_diff_3",
        category="momentum",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="y_t - y_{t-3}",
        description="Quarterly (3-month) absolute rate change.",
        required_lookback_months=3,
        is_usable_historically=True,
        redundancy_group="rate_differencing",
        collinearity_notes="Exact linear combination: rate_lag_0 - rate_lag_3.",
    ),
    "rate_diff_12": FeatureDefinition(
        name="rate_diff_12",
        category="momentum",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="y_t - y_{t-12}",
        description="Annual (12-month) absolute rate change.",
        required_lookback_months=12,
        is_usable_historically=True,
        redundancy_group="rate_differencing",
        collinearity_notes="Exact linear combination: rate_lag_0 - rate_lag_12.",
    ),
    "rate_pct_change_1": FeatureDefinition(
        name="rate_pct_change_1",
        category="momentum",
        unit="dimensionless",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="(y_t - y_{t-1}) / y_{t-1}",
        description="Month-over-month relative percentage rate change.",
        required_lookback_months=1,
        is_usable_historically=True,
        redundancy_group="rate_momentum_relative",
        collinearity_notes="Nonlinear relative momentum indicator.",
    ),
    "rate_pct_change_3": FeatureDefinition(
        name="rate_pct_change_3",
        category="momentum",
        unit="dimensionless",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="(y_t - y_{t-3}) / y_{t-3}",
        description="Quarterly (3-month) relative percentage rate change.",
        required_lookback_months=3,
        is_usable_historically=True,
        redundancy_group="rate_momentum_relative",
        collinearity_notes="Nonlinear relative momentum indicator.",
    ),
    # 3. Rolling Statistics & Volatility (Own Route)
    "rolling_mean_3": FeatureDefinition(
        name="rolling_mean_3",
        category="rolling_statistic",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="(1/3) * sum_{i=0}^{2} y_{t-i}",
        description="Trailing 3-month simple moving average rate.",
        required_lookback_months=3,
        is_usable_historically=True,
        redundancy_group="rolling_averages",
        collinearity_notes="Exact linear combination of lags 0, 1, 2. Collinear with lags in linear models.",
    ),
    "rolling_mean_6": FeatureDefinition(
        name="rolling_mean_6",
        category="rolling_statistic",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="(1/6) * sum_{i=0}^{5} y_{t-i}",
        description="Trailing 6-month simple moving average rate.",
        required_lookback_months=6,
        is_usable_historically=True,
        redundancy_group="rolling_averages",
        collinearity_notes="Exact linear combination of lags 0..5.",
    ),
    "rolling_mean_12": FeatureDefinition(
        name="rolling_mean_12",
        category="rolling_statistic",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="(1/12) * sum_{i=0}^{11} y_{t-i}",
        description="Trailing 12-month simple moving average rate.",
        required_lookback_months=12,
        is_usable_historically=True,
        redundancy_group="rolling_averages",
        collinearity_notes="Exact linear combination of lags 0..11.",
    ),
    "rolling_std_6": FeatureDefinition(
        name="rolling_std_6",
        category="rolling_statistic",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="sqrt( (1/5) * sum_{i=0}^{5} (y_{t-i} - MA_6)^2 )",
        description="Trailing 6-month sample standard deviation (short-term volatility).",
        required_lookback_months=6,
        is_usable_historically=True,
        redundancy_group="rolling_volatility",
        collinearity_notes="Measures medium-term rate dispersion; non-collinear with rate level.",
    ),
    "rolling_std_12": FeatureDefinition(
        name="rolling_std_12",
        category="rolling_statistic",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="sqrt( (1/11) * sum_{i=0}^{11} (y_{t-i} - MA_12)^2 )",
        description="Trailing 12-month sample standard deviation (annual volatility).",
        required_lookback_months=12,
        is_usable_historically=True,
        redundancy_group="rolling_volatility",
        collinearity_notes="Measures annual rate dispersion; non-collinear with rate level.",
    ),
    "rolling_ratio_12": FeatureDefinition(
        name="rolling_ratio_12",
        category="rolling_statistic",
        unit="dimensionless",
        source="USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
        formula="y_t / MA_12",
        description="Ratio of latest rate to trailing 12-month mean (mean-reversion indicator).",
        required_lookback_months=12,
        is_usable_historically=True,
        redundancy_group="mean_reversion",
        collinearity_notes="Normalized ratio around 1.0 indicating cyclical rate stretch.",
    ),
    # 4. Seasonality & Calendar Features
    "origin_month": FeatureDefinition(
        name="origin_month",
        category="seasonality",
        unit="month_index",
        source="Calendar",
        formula="month(t)",
        description="Calendar month index (1-12) of the forecast origin date t.",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="calendar_seasonality",
        collinearity_notes="Integer 1-12. Redundant with origin_quarter and harmonic sine/cosine components.",
    ),
    "origin_sin_month": FeatureDefinition(
        name="origin_sin_month",
        category="seasonality",
        unit="dimensionless",
        source="Calendar",
        formula="sin(2 * pi * month(t) / 12)",
        description="Cyclical harmonic sine transformation of origin calendar month.",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="calendar_seasonality",
        collinearity_notes="Continuous circular harmonic component. Preferred over integer month in linear models.",
    ),
    "origin_cos_month": FeatureDefinition(
        name="origin_cos_month",
        category="seasonality",
        unit="dimensionless",
        source="Calendar",
        formula="cos(2 * pi * month(t) / 12)",
        description="Cyclical harmonic cosine transformation of origin calendar month.",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="calendar_seasonality",
        collinearity_notes="Continuous circular harmonic component. sin^2 + cos^2 = 1.",
    ),
    "origin_quarter": FeatureDefinition(
        name="origin_quarter",
        category="seasonality",
        unit="quarter_index",
        source="Calendar",
        formula="(month(t) - 1) // 3 + 1",
        description="Calendar quarter (1-4) of the forecast origin date t.",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="calendar_seasonality",
        collinearity_notes="Coarser step-function projection of origin_month.",
    ),
    "target_month": FeatureDefinition(
        name="target_month",
        category="seasonality",
        unit="month_index",
        source="Calendar",
        formula="month(t + h)",
        description="Calendar month index (1-12) of the forecast target date t+h.",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="calendar_seasonality",
        collinearity_notes="Target calendar month index.",
    ),
    "target_sin_month": FeatureDefinition(
        name="target_sin_month",
        category="seasonality",
        unit="dimensionless",
        source="Calendar",
        formula="sin(2 * pi * month(t + h) / 12)",
        description="Cyclical harmonic sine transformation of forecast target calendar month.",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="calendar_seasonality",
        collinearity_notes="Continuous circular harmonic component for target date.",
    ),
    "target_cos_month": FeatureDefinition(
        name="target_cos_month",
        category="seasonality",
        unit="dimensionless",
        source="Calendar",
        formula="cos(2 * pi * month(t + h) / 12)",
        description="Cyclical harmonic cosine transformation of forecast target calendar month.",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="calendar_seasonality",
        collinearity_notes="Continuous circular harmonic component for target date.",
    ),
    # 5. Macroeconomic Market Regime (Canonical C1.3.2)
    "regime_pre_supercycle": FeatureDefinition(
        name="regime_pre_supercycle",
        category="regime",
        unit="binary",
        source="Pelagos Macroeconomic Market Regime Definitions (C1.3.2)",
        formula="1 if t in [1996-01-01, 2006-12-01] else 0",
        description="Binary indicator for Pre-Supercycle historical macroeconomic regime (Jan 1996 - Dec 2006).",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="regime_encoding",
        collinearity_notes="Binary flag. Sum of all 4 regime flags equals 1.0 (dummy variable trap). Drop one reference flag in linear models.",
    ),
    "regime_supercycle_peak_crash": FeatureDefinition(
        name="regime_supercycle_peak_crash",
        category="regime",
        unit="binary",
        source="Pelagos Macroeconomic Market Regime Definitions (C1.3.2)",
        formula="1 if t in [2007-01-01, 2009-12-01] else 0",
        description="Binary indicator for Supercycle Peak & Crash macroeconomic regime (Jan 2007 - Dec 2009).",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="regime_encoding",
        collinearity_notes="Binary flag. Sum of all 4 regime flags equals 1.0.",
    ),
    "regime_post_supercycle": FeatureDefinition(
        name="regime_post_supercycle",
        category="regime",
        unit="binary",
        source="Pelagos Macroeconomic Market Regime Definitions (C1.3.2)",
        formula="1 if t in [2010-01-01, 2019-12-01] else 0",
        description="Binary indicator for Post-Supercycle prolonged fleet expansion regime (Jan 2010 - Dec 2019).",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="regime_encoding",
        collinearity_notes="Binary flag. Sum of all 4 regime flags equals 1.0.",
    ),
    "regime_pandemic_disruption": FeatureDefinition(
        name="regime_pandemic_disruption",
        category="regime",
        unit="binary",
        source="Pelagos Macroeconomic Market Regime Definitions (C1.3.2)",
        formula="1 if t in [2020-01-01, 2026-10-01] else 0",
        description="Binary indicator for Pandemic & Disruption macroeconomic regime (Jan 2020 - Oct 2026).",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="regime_encoding",
        collinearity_notes="Binary flag. Sum of all 4 regime flags equals 1.0.",
    ),
    "regime_code": FeatureDefinition(
        name="regime_code",
        category="regime",
        unit="categorical_code",
        source="Pelagos Macroeconomic Market Regime Definitions (C1.3.2)",
        formula="0: PRE (1996-2006), 1: PEAK (2007-2009), 2: POST (2010-2019), 3: PANDEMIC (2020-2026)",
        description="Integer categorical encoding of the active market regime as of observation date t.",
        required_lookback_months=0,
        is_usable_historically=True,
        redundancy_group="regime_encoding",
        collinearity_notes="Integer 0..3 encoding. Perfectly redundant with the 4 binary regime flags. Preferred for tree-based models.",
    ),
    "regime_id_code": FeatureDefinition(
        name="regime_id_code",
        category="regime",
        unit="categorical_code",
        source="Pelagos Macroeconomic Market Regime Definitions (C1.3.2)",
        formula="0: PRE, 1: PEAK, 2: POST, 3: PANDEMIC",
        description="Backward compatibility alias for canonical regime_code.",
        required_lookback_months=0,
        is_usable_historically=False,
        exclusion_reason="Superseded by canonical feature name 'regime_code'; preserved as backward compatibility alias.",
    ),
    # 6. Cross-Corridor Grain Market Features
    "competitor_rate_lag_0": FeatureDefinition(
        name="competitor_rate_lag_0",
        category="cross_corridor",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS)",
        formula="y_t^{competitor}",
        description="Contemporaneous rate at origin date t for the alternative grain export corridor (USG for PNW, PNW for USG).",
        required_lookback_months=0,
        is_usable_historically=True,
    ),
    "competitor_rate_lag_1": FeatureDefinition(
        name="competitor_rate_lag_1",
        category="cross_corridor",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS)",
        formula="y_{t-1}^{competitor}",
        description="1-month lagged rate for the alternative grain export corridor.",
        required_lookback_months=1,
        is_usable_historically=True,
    ),
    "corridor_spread_lag_0": FeatureDefinition(
        name="corridor_spread_lag_0",
        category="cross_corridor",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS)",
        formula="y_t^{USG} - y_t^{PNW}",
        description="Contemporaneous inter-corridor freight rate spread (USG minus PNW) at origin date t.",
        required_lookback_months=0,
        is_usable_historically=True,
    ),
    "corridor_spread_mean_3": FeatureDefinition(
        name="corridor_spread_mean_3",
        category="cross_corridor",
        unit="USD/MT",
        source="USDA Agricultural Marketing Service (AMS)",
        formula="(1/3) * sum_{i=0}^{2} (y_{t-i}^{USG} - y_{t-i}^{PNW})",
        description="Trailing 3-month moving average of the inter-corridor freight rate spread.",
        required_lookback_months=3,
        is_usable_historically=True,
    ),
    # 7. Route Static Metadata
    "route_benchmark_distance_nm": FeatureDefinition(
        name="route_benchmark_distance_nm",
        category="route_metadata",
        unit="NM",
        source="Freight Routes Catalog / DATA-001 Section 5.2",
        formula="benchmark_distance_nm",
        description="Standard nautical distance for the freight corridor (4,520 NM for PNW, 9,150 NM for USG).",
        required_lookback_months=0,
        is_usable_historically=True,
        is_time_varying=False,
        redundancy_group="invariant_route_metadata",
        collinearity_notes="Invariant within any single corridor (PNW=4520, USG=9150). Zero variance within a single route time series; omit in single-route models to avoid collinearity with intercept. Useful in multi-corridor pooled models.",
    ),
    # 8. Audited Excluded Candidate Features (Strictly Documented)
    "trade_volume_grain_us_jp": FeatureDefinition(
        name="trade_volume_grain_us_jp",
        category="excluded_source",
        unit="MT",
        source="USDA FAS GATS",
        formula="volume_metric_tons",
        description="Bilateral monthly agricultural bulk grain export volume from United States to Japan.",
        required_lookback_months=1,
        is_usable_historically=False,
        exclusion_reason="Severe historical data truncation: database contains only 3 months of US-Japan grain trade in late 2024 (< 1% timeline coverage). Absent from 1996 to Sept 2024.",
    ),
    "environmental_wave_height_departure": FeatureDefinition(
        name="environmental_wave_height_departure",
        category="excluded_source",
        unit="meters",
        source="NOAA National Data Buoy Center (NDBC)",
        formula="wave_height_meters",
        description="Significant wave height recorded at corridor coastal departure fairway buoy.",
        required_lookback_months=0,
        is_usable_historically=False,
        exclusion_reason="Severe sparsity: moored buoy observations cover only isolated point snapshots in Oct 2024 and Oct 2026 (< 0.1% coverage). Zero continuous multi-year history in Pelagos storage.",
    ),
    "environmental_wind_speed_departure": FeatureDefinition(
        name="environmental_wind_speed_departure",
        category="excluded_source",
        unit="knots",
        source="NOAA National Data Buoy Center (NDBC)",
        formula="wind_speed_knots",
        description="Wind speed recorded at corridor coastal departure fairway buoy.",
        required_lookback_months=0,
        is_usable_historically=False,
        exclusion_reason="Severe sparsity: covers only isolated point snapshots in Oct 2024 and Oct 2026 (< 0.1% coverage). Zero continuous multi-year history in Pelagos storage.",
    ),
    "bunker_price_vlsfo_delivered": FeatureDefinition(
        name="bunker_price_vlsfo_delivered",
        category="excluded_source",
        unit="USD/MT",
        source="Bunker Fuel Prices (bunkerfuelprices.com)",
        formula="usdPerMt",
        description="Delivered spot market price for VLSFO marine bunker fuel at corridor hubs.",
        required_lookback_months=0,
        is_usable_historically=False,
        exclusion_reason="Zero historical coverage: public provider feed contains only latest point observations (Nov 2026) for non-corridor ports (ARBUE, DKSKA); corridor hubs are unquoted. No historical series exists prior to Nov 2026. Substituting crude oil proxies (WTI, Brent) is strictly prohibited.",
    ),
    "ais_fleet_active_density": FeatureDefinition(
        name="ais_fleet_active_density",
        category="excluded_source",
        unit="vessels_count",
        source="NOAA MarineCadastre",
        formula="count(mmsi)",
        description="Active commercial bulk carrier density derived from AIS vessel telemetry.",
        required_lookback_months=0,
        is_usable_historically=False,
        exclusion_reason="Zero historical continuity: repository contains only a single-day sample (2024-01-01). Continuous multi-year historical fleet tracking is unavailable.",
    ),
}

USABLE_FEATURE_NAMES = [name for name, defn in FEATURE_CATALOG.items() if defn.is_usable_historically]
EXCLUDED_FEATURE_NAMES = [name for name, defn in FEATURE_CATALOG.items() if not defn.is_usable_historically]

FEATURE_SUBSETS: Dict[str, List[str]] = {
    "all": USABLE_FEATURE_NAMES,
    "autoregressive": [
        "rate_lag_0",
        "rate_lag_1",
        "rate_lag_2",
        "rate_lag_3",
        "rate_lag_6",
        "rate_lag_12",
    ],
    "linear_non_redundant": [
        # Rate lags (avoiding rolling averages and diffs which are linear combinations)
        "rate_lag_0",
        "rate_lag_1",
        "rate_lag_2",
        "rate_lag_3",
        # Rolling volatility
        "rolling_std_6",
        "rolling_std_12",
        # Continuous harmonic seasonality (omitting collinear month/quarter step functions)
        "origin_sin_month",
        "origin_cos_month",
        # 3 regime flags (omitting reference PRE_SUPERCYCLE to avoid dummy variable trap)
        "regime_supercycle_peak_crash",
        "regime_post_supercycle",
        "regime_pandemic_disruption",
        # Cross-corridor rate & spread
        "competitor_rate_lag_0",
        "corridor_spread_lag_0",
    ],
    "tree_based": [
        "rate_lag_0",
        "rate_lag_1",
        "rate_lag_2",
        "rate_lag_3",
        "rate_lag_6",
        "rate_lag_12",
        "rate_diff_1",
        "rate_diff_3",
        "rate_diff_12",
        "rate_pct_change_1",
        "rate_pct_change_3",
        "rolling_mean_3",
        "rolling_mean_6",
        "rolling_mean_12",
        "rolling_std_6",
        "rolling_std_12",
        "rolling_ratio_12",
        "origin_month",
        "origin_quarter",
        "target_month",
        "regime_code",
        "competitor_rate_lag_0",
        "competitor_rate_lag_1",
        "corridor_spread_lag_0",
        "corridor_spread_mean_3",
    ],
    "univariate": [
        name
        for name in USABLE_FEATURE_NAMES
        if not name.startswith("competitor_")
        and not name.startswith("corridor_spread_")
    ],
}


# ---------------------------------------------------------------------------
# Pure Leakage-Safe Feature Extraction Functions
# ---------------------------------------------------------------------------

def calculate_rate_lags(history: List[float]) -> Dict[str, float]:
    """Extract autoregressive lag features from chronological rate history up to origin t."""
    if not history:
        raise ValueError("Cannot calculate lag features from empty history")

    n = len(history)
    features: Dict[str, float] = {
        "rate_lag_0": float(history[-1]),
    }
    if n >= 2:
        features["rate_lag_1"] = float(history[-2])
    if n >= 3:
        features["rate_lag_2"] = float(history[-3])
    if n >= 4:
        features["rate_lag_3"] = float(history[-4])
    if n >= 7:
        features["rate_lag_6"] = float(history[-7])
    if n >= 13:
        features["rate_lag_12"] = float(history[-13])

    return features


def calculate_rate_momentum(history: List[float]) -> Dict[str, float]:
    """Calculate rate change and percentage momentum features from chronological history."""
    if not history:
        raise ValueError("Cannot calculate momentum features from empty history")

    n = len(history)
    curr = float(history[-1])
    features: Dict[str, float] = {}

    if n >= 2:
        prev_1 = float(history[-2])
        features["rate_diff_1"] = round(curr - prev_1, 4)
        features["rate_pct_change_1"] = round((curr - prev_1) / prev_1, 6) if prev_1 > 0 else 0.0

    if n >= 4:
        prev_3 = float(history[-4])
        features["rate_diff_3"] = round(curr - prev_3, 4)
        features["rate_pct_change_3"] = round((curr - prev_3) / prev_3, 6) if prev_3 > 0 else 0.0

    if n >= 13:
        prev_12 = float(history[-13])
        features["rate_diff_12"] = round(curr - prev_12, 4)

    return features


def calculate_rolling_statistics(history: List[float]) -> Dict[str, float]:
    """Calculate rolling window mean and standard deviation features from chronological history."""
    if not history:
        raise ValueError("Cannot calculate rolling statistics from empty history")

    n = len(history)
    features: Dict[str, float] = {}

    if n >= 3:
        w3 = history[-3:]
        m3 = sum(w3) / 3.0
        features["rolling_mean_3"] = round(m3, 4)

    if n >= 6:
        w6 = history[-6:]
        m6 = sum(w6) / 6.0
        features["rolling_mean_6"] = round(m6, 4)
        var6 = sum((x - m6) ** 2 for x in w6) / 5.0
        features["rolling_std_6"] = round(math.sqrt(max(0.0, var6)), 4)

    if n >= 12:
        w12 = history[-12:]
        m12 = sum(w12) / 12.0
        features["rolling_mean_12"] = round(m12, 4)
        var12 = sum((x - m12) ** 2 for x in w12) / 11.0
        features["rolling_std_12"] = round(math.sqrt(max(0.0, var12)), 4)
        curr = float(history[-1])
        features["rolling_ratio_12"] = round(curr / m12, 6) if m12 > 0 else 1.0

    return features


def calculate_seasonality_features(origin_date: date, target_date: date) -> Dict[str, float]:
    """Calculate cyclical harmonic and index calendar features for origin and target dates."""
    m_orig = origin_date.month
    q_orig = (m_orig - 1) // 3 + 1
    m_tgt = target_date.month

    return {
        "origin_month": float(m_orig),
        "origin_sin_month": round(math.sin(2.0 * math.pi * m_orig / 12.0), 6),
        "origin_cos_month": round(math.cos(2.0 * math.pi * m_orig / 12.0), 6),
        "origin_quarter": float(q_orig),
        "target_month": float(m_tgt),
        "target_sin_month": round(math.sin(2.0 * math.pi * m_tgt / 12.0), 6),
        "target_cos_month": round(math.cos(2.0 * math.pi * m_tgt / 12.0), 6),
    }


def calculate_regime_features(origin_date: date) -> Dict[str, float]:
    """Classify origin date into historical macroeconomic market regime without future leakage."""
    regime = classify_target_date_regime(origin_date)
    regime_id = regime.regime_id if regime else MarketRegimeId.PANDEMIC_DISRUPTION.value

    # Categorical code mapping
    code_map = {
        MarketRegimeId.PRE_SUPERCYCLE.value: 0.0,
        MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value: 1.0,
        MarketRegimeId.POST_SUPERCYCLE.value: 2.0,
        MarketRegimeId.PANDEMIC_DISRUPTION.value: 3.0,
    }

    return {
        "regime_pre_supercycle": 1.0 if regime_id == MarketRegimeId.PRE_SUPERCYCLE.value else 0.0,
        "regime_supercycle_peak_crash": 1.0 if regime_id == MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value else 0.0,
        "regime_post_supercycle": 1.0 if regime_id == MarketRegimeId.POST_SUPERCYCLE.value else 0.0,
        "regime_pandemic_disruption": 1.0 if regime_id == MarketRegimeId.PANDEMIC_DISRUPTION.value else 0.0,
        "regime_code": code_map.get(regime_id, 3.0),
        "regime_id_code": code_map.get(regime_id, 3.0),
    }


def calculate_cross_corridor_features(
    route_code: str,
    history_own: List[float],
    history_other: List[float],
) -> Dict[str, float]:
    """Calculate economically justified cross-corridor rate and inter-corridor spread features."""
    if not history_own or not history_other:
        return {}

    curr_own = float(history_own[-1])
    curr_other = float(history_other[-1])
    features: Dict[str, float] = {
        "competitor_rate_lag_0": curr_other,
    }

    if len(history_other) >= 2:
        features["competitor_rate_lag_1"] = float(history_other[-2])

    # Inter-corridor spread is canonically defined as USG minus PNW
    if route_code == "PNW-JPN-BULK":
        spread_0 = curr_other - curr_own  # USG - PNW
    else:
        spread_0 = curr_own - curr_other  # USG - PNW

    features["corridor_spread_lag_0"] = round(spread_0, 4)

    # 3-month trailing average spread
    min_len = min(len(history_own), len(history_other))
    if min_len >= 3:
        if route_code == "PNW-JPN-BULK":
            spreads_3 = [float(history_other[-i]) - float(history_own[-i]) for i in (1, 2, 3)]
        else:
            spreads_3 = [float(history_own[-i]) - float(history_other[-i]) for i in (1, 2, 3)]
        features["corridor_spread_mean_3"] = round(sum(spreads_3) / 3.0, 4)

    return features


def calculate_route_metadata_features(route_code: str) -> Dict[str, float]:
    """Extract invariant route structural characteristics."""
    meta = ROUTE_METADATA.get(route_code, {})
    dist = meta.get("benchmark_distance_nm", 0.0)
    return {
        "route_benchmark_distance_nm": float(dist),
    }


def extract_all_usable_features(
    route_code: str,
    origin_date: date,
    target_date: date,
    history_own: List[float],
    history_other: Optional[List[float]] = None,
) -> Dict[str, float]:
    """Extract all usable explanatory features for a given forecast origin date t.

    Guarantees strict temporal alignment:
    - Only observations in history_own and history_other (preceding or at t) are used.
    - No information from target_date or beyond is leaked into the feature vector.
    """
    features: Dict[str, float] = {}

    # 1. Autoregressive lags
    features.update(calculate_rate_lags(history_own))

    # 2. Momentum & differences
    features.update(calculate_rate_momentum(history_own))

    # 3. Rolling statistics
    features.update(calculate_rolling_statistics(history_own))

    # 4. Seasonality
    features.update(calculate_seasonality_features(origin_date, target_date))

    # 5. Market regime
    features.update(calculate_regime_features(origin_date))

    # 6. Cross-corridor features (if competitor history available)
    if history_other:
        features.update(calculate_cross_corridor_features(route_code, history_own, history_other))

    # 7. Route metadata
    features.update(calculate_route_metadata_features(route_code))

    return features
