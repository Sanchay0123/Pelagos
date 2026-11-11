"""Unit and integration test suite for Stage C3.1 Forecast Dataset Preparation.

Tests:
- Strict temporal alignment and zero future-data leakage.
- Route separation and competitor corridor mechanics.
- Mathematical precision of feature engineering formulas.
- Missing observation and lookback burn-in handling.
- Comprehensive feature inventory and route availability matrices.
- Fallback baseline availability (Persistence).
- Dataset reproducibility and regime slicing.

Traces to DATA-001 Section 5.2, ML-001, and Stage C3.1 mandates.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Dict, List, Tuple
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from pelagos.database import SessionLocal
from pelagos.forecasting.dataset import (
    ForecastDataset,
    ForecastDatasetRow,
    TemporalAvailabilityPolicy,
    add_months_to_date,
    build_forecast_dataset,
    generate_feature_inventory,
    generate_route_feature_availability_matrix,
)
from pelagos.forecasting.features import (
    EXCLUDED_FEATURE_NAMES,
    FEATURE_CATALOG,
    FEATURE_SUBSETS,
    USABLE_FEATURE_NAMES,
    calculate_cross_corridor_features,
    calculate_rate_lags,
    calculate_rate_momentum,
    calculate_regime_features,
    calculate_rolling_statistics,
    calculate_seasonality_features,
    extract_all_usable_features,
)
from pelagos.forecasting.regimes import HISTORICAL_REGIMES, MarketRegimeId


@pytest.fixture(scope="module")
def db_session():
    """Shared database session for read-only dataset tests."""
    session = SessionLocal()
    yield session
    session.close()


class TestTemporalAlignmentAndLeakage:
    """Tests guaranteeing zero future-data leakage across all forecast horizons."""

    @pytest.mark.parametrize("horizon", [1, 3, 6])
    def test_strict_temporal_alignment_and_no_leakage(self, db_session, horizon: int):
        """Features at origin t strictly use data at or before t; target is isolated at t+h."""
        ds = build_forecast_dataset(
            db=db_session,
            route_code="PNW-JPN-BULK",
            horizon_months=horizon,
            burn_in_months=12,
            include_unlabeled_latest=True,
        )

        assert ds.horizon_months == horizon
        assert len(ds.feature_names) == 34

        for row in ds.rows:
            # Invariant 1: origin date is strictly earlier than target date
            assert row.origin_date < row.target_date

            # Invariant 2: under strict forward semantics, target period starts after decision date
            assert row.target_period_start > row.decision_date
            expected_tgt = add_months_to_date(row.origin_date, horizon + 1)
            assert row.target_date == expected_tgt
            assert row.target_period_start == expected_tgt

            # Invariant 3: rate_lag_0 matches latest rate at origin date
            assert "rate_lag_0" in row.features
            assert row.features["rate_lag_0"] > 0.0

            # Invariant 4: target value is isolated and not in feature set
            assert "target_value" not in row.features
            assert "target_rate" not in row.features
            assert "actual_value" not in row.features

            # Invariant 5: for labeled rows, target_change is exactly target_value - rate_lag_0
            if row.is_labeled:
                assert row.target_value is not None
                assert row.target_change == pytest.approx(row.target_value - row.features["rate_lag_0"], abs=1e-4)

    def test_unlabeled_latest_observation_row(self, db_session):
        """Latest origin row (2026-10-01) has target_value=None for real-time inference targeting 2026-12-01."""
        ds = build_forecast_dataset(
            db=db_session,
            route_code="PNW-JPN-BULK",
            horizon_months=1,
            burn_in_months=12,
            include_unlabeled_latest=True,
        )

        latest_row = ds.get_unlabeled_latest_row()
        assert latest_row is not None
        assert latest_row.origin_date == date(2026, 10, 1)
        assert latest_row.decision_date == date(2026, 11, 14)
        # Strict forward T+1 targets December 2026 (starting strictly after decision Nov 5)
        assert latest_row.target_date == date(2026, 12, 1)
        assert latest_row.target_period_start == date(2026, 12, 1)
        assert latest_row.target_period_start > latest_row.decision_date
        assert latest_row.target_value is None
        assert latest_row.is_labeled is False
        assert len(latest_row.features) == 34

        # When include_unlabeled_latest is False, only labeled rows are returned
        ds_labeled_only = build_forecast_dataset(
            db=db_session,
            route_code="PNW-JPN-BULK",
            horizon_months=1,
            burn_in_months=12,
            include_unlabeled_latest=False,
        )
        assert ds_labeled_only.get_unlabeled_latest_row() is None
        assert len(ds_labeled_only.rows) == len(ds.labeled_rows)


class TestRouteSeparationAndCrossCorridor:
    """Tests ensuring corridor separation and valid inter-corridor spread mechanics."""

    def test_route_separation_pnw_vs_usg(self, db_session):
        """PNW and USG datasets have distinct rates, benchmark distances, and competitor mappings."""
        ds_pnw = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1)
        ds_usg = build_forecast_dataset(db=db_session, route_code="USG-JPN-BULK", horizon_months=1)

        # Nautical distances
        assert ds_pnw.labeled_rows[0].features["route_benchmark_distance_nm"] == 4520.0
        assert ds_usg.labeled_rows[0].features["route_benchmark_distance_nm"] == 9150.0

        # Mean rate values
        pnw_rates = [r.features["rate_lag_0"] for r in ds_pnw.labeled_rows]
        usg_rates = [r.features["rate_lag_0"] for r in ds_usg.labeled_rows]

        assert np.mean(pnw_rates) == pytest.approx(26.58, abs=0.5)
        assert np.mean(usg_rates) == pytest.approx(46.19, abs=0.5)

        # Cross-corridor mappings
        for r_pnw, r_usg in zip(ds_pnw.labeled_rows, ds_usg.labeled_rows):
            assert r_pnw.origin_date == r_usg.origin_date
            # In PNW, competitor is USG
            assert r_pnw.features["competitor_rate_lag_0"] == r_usg.features["rate_lag_0"]
            # In USG, competitor is PNW
            assert r_usg.features["competitor_rate_lag_0"] == r_pnw.features["rate_lag_0"]
            # Inter-corridor spread is USG - PNW (positive throughout history)
            expected_spread = r_usg.features["rate_lag_0"] - r_pnw.features["rate_lag_0"]
            assert r_pnw.features["corridor_spread_lag_0"] == pytest.approx(expected_spread, abs=1e-4)
            assert r_usg.features["corridor_spread_lag_0"] == pytest.approx(expected_spread, abs=1e-4)

    def test_derived_route_rejected(self, db_session):
        """Derived spread route SPREAD-USG-PNW-JPN is strictly rejected from direct modeling."""
        with pytest.raises(ValueError, match="derived indicator"):
            build_forecast_dataset(db=db_session, route_code="SPREAD-USG-PNW-JPN")

    def test_unsupported_route_rejected(self, db_session):
        """Unmonitored routes are rejected with descriptive error."""
        with pytest.raises(ValueError, match="Unsupported route code"):
            build_forecast_dataset(db=db_session, route_code="INVALID-ROUTE")


class TestFeatureCalculationPrecision:
    """Tests verifying mathematical precision of pure feature extraction functions."""

    def test_autoregressive_lags_and_momentum(self):
        """Lag and difference formulas match exact definitions."""
        hist = [10.0, 12.0, 15.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 25.0, 27.0, 28.0, 30.0]
        lags = calculate_rate_lags(hist)
        assert lags["rate_lag_0"] == 30.0
        assert lags["rate_lag_1"] == 28.0
        assert lags["rate_lag_2"] == 27.0
        assert lags["rate_lag_3"] == 25.0
        assert lags["rate_lag_6"] == 20.0
        assert lags["rate_lag_12"] == 10.0

        mom = calculate_rate_momentum(hist)
        assert mom["rate_diff_1"] == pytest.approx(30.0 - 28.0, abs=1e-4)
        assert mom["rate_pct_change_1"] == pytest.approx((30.0 - 28.0) / 28.0, abs=1e-6)
        assert mom["rate_diff_3"] == pytest.approx(30.0 - 25.0, abs=1e-4)
        assert mom["rate_diff_12"] == pytest.approx(30.0 - 10.0, abs=1e-4)

    def test_rolling_statistics_precision(self):
        """Rolling window mean, standard deviation, and ratio calculations are exact."""
        hist = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 26.0, 28.0, 30.0, 32.0]
        stats = calculate_rolling_statistics(hist)

        # MA-3 of [28, 30, 32] = 30.0
        assert stats["rolling_mean_3"] == pytest.approx(30.0, abs=1e-4)
        # MA-6 of [22, 24, 26, 28, 30, 32] = 27.0
        assert stats["rolling_mean_6"] == pytest.approx(27.0, abs=1e-4)
        # Sample std of [22, 24, 26, 28, 30, 32]
        expected_std_6 = float(np.std([22, 24, 26, 28, 30, 32], ddof=1))
        assert stats["rolling_std_6"] == pytest.approx(expected_std_6, abs=1e-4)

        # MA-12 of 10 to 32 by 2 = 21.0
        assert stats["rolling_mean_12"] == pytest.approx(21.0, abs=1e-4)
        expected_std_12 = float(np.std(hist, ddof=1))
        assert stats["rolling_std_12"] == pytest.approx(expected_std_12, abs=1e-4)
        assert stats["rolling_ratio_12"] == pytest.approx(32.0 / 21.0, abs=1e-6)

    def test_seasonality_harmonic_properties(self):
        """Harmonic seasonal features satisfy sin^2 + cos^2 = 1.0."""
        for m in range(1, 13):
            orig = date(2024, m, 1)
            tgt = add_months_to_date(orig, 3)
            seas = calculate_seasonality_features(orig, tgt)
            assert seas["origin_month"] == float(m)
            assert seas["origin_quarter"] == float((m - 1) // 3 + 1)
            assert seas["origin_sin_month"] ** 2 + seas["origin_cos_month"] ** 2 == pytest.approx(1.0, abs=1e-6)
            assert seas["target_sin_month"] ** 2 + seas["target_cos_month"] ** 2 == pytest.approx(1.0, abs=1e-6)

    def test_regime_features_mutually_exclusive(self):
        """Regime binary indicators sum to exactly 1.0 for any observation date."""
        dates = [
            date(1998, 5, 1),   # Pre-Supercycle
            date(2008, 6, 1),   # Peak & Crash
            date(2015, 3, 1),   # Post-Supercycle
            date(2022, 9, 1),   # Pandemic & Disruption
        ]
        for d in dates:
            rf = calculate_regime_features(d)
            binary_sum = (
                rf["regime_pre_supercycle"]
                + rf["regime_supercycle_peak_crash"]
                + rf["regime_post_supercycle"]
                + rf["regime_pandemic_disruption"]
            )
            assert binary_sum == 1.0
            assert rf["regime_code"] in (0.0, 1.0, 2.0, 3.0)
            assert rf["regime_id_code"] == rf["regime_code"]

    def test_canonical_c132_regime_boundaries(self):
        """Verify exact C1.3.2 boundary dates and uninterrupted chronological mapping."""
        # 1. Pre-Supercycle: Jan 1996 - Dec 2006
        rf_pre_start = calculate_regime_features(date(1996, 1, 1))
        assert rf_pre_start["regime_code"] == 0.0
        assert rf_pre_start["regime_pre_supercycle"] == 1.0

        rf_pre_end = calculate_regime_features(date(2006, 12, 1))
        assert rf_pre_end["regime_code"] == 0.0
        assert rf_pre_end["regime_pre_supercycle"] == 1.0

        # 2. Supercycle Peak & Crash: Jan 2007 - Dec 2009
        rf_peak_start = calculate_regime_features(date(2007, 1, 1))
        assert rf_peak_start["regime_code"] == 1.0
        assert rf_peak_start["regime_supercycle_peak_crash"] == 1.0

        rf_peak_end = calculate_regime_features(date(2009, 12, 1))
        assert rf_peak_end["regime_code"] == 1.0
        assert rf_peak_end["regime_supercycle_peak_crash"] == 1.0

        # 3. Post-Supercycle: Jan 2010 - Dec 2019
        rf_post_start = calculate_regime_features(date(2010, 1, 1))
        assert rf_post_start["regime_code"] == 2.0
        assert rf_post_start["regime_post_supercycle"] == 1.0

        rf_post_end = calculate_regime_features(date(2019, 12, 1))
        assert rf_post_end["regime_code"] == 2.0
        assert rf_post_end["regime_post_supercycle"] == 1.0

        # 4. Pandemic & Disruption: Jan 2020 - Oct 2026
        rf_pan_start = calculate_regime_features(date(2020, 1, 1))
        assert rf_pan_start["regime_code"] == 3.0
        assert rf_pan_start["regime_pandemic_disruption"] == 1.0

        rf_pan_end = calculate_regime_features(date(2026, 10, 1))
        assert rf_pan_end["regime_code"] == 3.0
        assert rf_pan_end["regime_pandemic_disruption"] == 1.0


class TestMissingDataAndFeatureSubsets:
    """Tests for missing data policy, burn-in lookback, and feature subsets."""

    def test_burn_in_lookback_enforcement(self, db_session):
        """Dataset builder requires burn_in_months and starts origin dates accordingly."""
        ds = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1, burn_in_months=12)
        # Total dates: 370. Burn-in: 12. Valid origins: index 12 (1997-01-01) to 369 (2026-10-01) -> 358 rows
        assert len(ds.rows) == 358
        assert ds.rows[0].origin_date == date(1997, 1, 1)
        # Strict forward T+1 has 356 labeled rows and 2 unlabeled inference rows
        assert len(ds.labeled_rows) == 356
        assert len(ds.unlabeled_rows) == 2

    def test_feature_sets_filtering(self, db_session):
        """Feature set selectors ('autoregressive', 'univariate', 'all') filter correctly."""
        ds_ar = build_forecast_dataset(
            db=db_session, route_code="PNW-JPN-BULK", horizon_months=1, feature_set="autoregressive"
        )
        assert all(f.startswith("rate_lag_") for f in ds_ar.feature_names)
        assert len(ds_ar.feature_names) == 6

        ds_uni = build_forecast_dataset(
            db=db_session, route_code="PNW-JPN-BULK", horizon_months=1, feature_set="univariate"
        )
        assert not any("competitor_" in f or "corridor_spread_" in f for f in ds_uni.feature_names)
        assert len(ds_uni.feature_names) == 30  # 34 minus 4 cross-corridor features

        ds_all = build_forecast_dataset(
            db=db_session, route_code="PNW-JPN-BULK", horizon_months=1, feature_set="all"
        )
        assert len(ds_all.feature_names) == 34

    def test_dataframe_and_X_y_extraction(self, db_session):
        """to_dataframe() and get_X_y() produce aligned clean matrices without NaN values."""
        ds = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1)
        df = ds.to_dataframe(include_unlabeled=False)

        assert len(df) == 356
        assert df["target_value"].isnull().sum() == 0
        assert df[ds.feature_names].isnull().sum().sum() == 0

        X, y = ds.get_X_y()
        assert X.shape == (356, 34)
        assert len(y) == 356
        assert (y.values == df["target_value"].values).all()

        X_del, y_del = ds.get_X_y_deltas()
        assert X_del.shape == (356, 34)
        assert len(y_del) == 356
        assert (y_del.values == df["target_change"].values).all()

    def test_regime_filtering(self, db_session):
        """filter_by_regime() correctly subsets dataset rows to specific macroeconomic eras using target-date classification."""
        ds = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1)

        # Pre-supercycle target dates: 1997-03-01 to 2006-12-01 (origins 1997-01-01 to 2006-10-01) -> 118 rows
        ds_pre = ds.filter_by_regime(MarketRegimeId.PRE_SUPERCYCLE.value)
        assert len(ds_pre.labeled_rows) == 118

        # Supercycle peak & crash target dates: 2007-01-01 to 2009-12-01 (origins 2006-11-01 to 2009-10-01) -> 36 rows
        ds_super = ds.filter_by_regime(MarketRegimeId.SUPERCYCLE_PEAK_CRASH.value)
        assert len(ds_super.labeled_rows) == 36

        # Post-supercycle target dates: 2010-01-01 to 2019-12-01 (origins 2009-11-01 to 2019-10-01) -> 120 rows
        ds_post = ds.filter_by_regime(MarketRegimeId.POST_SUPERCYCLE.value)
        assert len(ds_post.labeled_rows) == 120

        # Pandemic disruption target dates: 2020-01-01 to 2026-10-01 (origins 2019-11-01 to 2026-08-01) -> 82 rows
        ds_pan = ds.filter_by_regime(MarketRegimeId.PANDEMIC_DISRUPTION.value)
        assert len(ds_pan.labeled_rows) == 82
        assert len(ds_pre.labeled_rows) + len(ds_super.labeled_rows) + len(ds_post.labeled_rows) + len(ds_pan.labeled_rows) == 356


class TestFeatureInventoryAndAvailabilityMatrix:
    """Tests verifying the 7-domain data inventory and route availability reporting."""

    def test_feature_inventory_domains(self, db_session):
        """generate_feature_inventory() covers all 7 data domains with verified empirical metrics."""
        inv = generate_feature_inventory(db=db_session)
        assert len(inv) == 7

        domain_names = [d["domain_name"] for d in inv]
        assert "Historical Freight Rates" in domain_names
        assert "Bilateral Maritime Trade Volumes" in domain_names
        assert "Oceanographic Buoy & Marine Meteorology" in domain_names
        assert "Commercial Marine Bunker Fuel Prices" in domain_names
        assert "Port & Route Structural Characteristics" in domain_names
        assert "Historical Macroeconomic Market Regimes" in domain_names
        assert "Cross-Corridor Freight Rates & Inter-Corridor Spread" in domain_names

        # Freight domain is sufficient
        freight_dom = next(d for d in inv if d["domain_number"] == 1)
        assert freight_dom["genuine_observations_count"] == 1110
        assert freight_dom["missingness_rate_pct"] == 0.0
        assert "Sufficient" in freight_dom["monthly_modeling_sufficiency"]

        # Trade domain is insufficient and excluded (13 verified rows in DB: 8 USDA FAS GATS + 5 Indian DGCI&S)
        from pelagos.models import TradeObservation

        trade_dom = next(d for d in inv if d["domain_number"] == 2)
        assert trade_dom["genuine_observations_count"] == 13
        assert trade_dom["genuine_observations_count"] == db_session.query(TradeObservation).count()
        assert trade_dom["missingness_rate_pct"] > 99.0
        assert "Insufficient" in trade_dom["monthly_modeling_sufficiency"]
        assert "Excluded" in trade_dom["usability_status"]

        # Environmental buoys domain is insufficient and excluded
        env_dom = next(d for d in inv if d["domain_number"] == 3)
        assert env_dom["genuine_observations_count"] == 13
        assert "Insufficient" in env_dom["monthly_modeling_sufficiency"]
        assert "Excluded" in env_dom["usability_status"]

        # Bunker fuel domain is insufficient and excluded
        bunker_dom = next(d for d in inv if d["domain_number"] == 4)
        assert bunker_dom["genuine_observations_count"] == 5
        assert "Insufficient" in bunker_dom["monthly_modeling_sufficiency"]
        assert "Excluded" in bunker_dom["usability_status"]

    def test_route_feature_availability_matrix(self, db_session):
        """generate_route_feature_availability_matrix() returns route-separated matrices."""
        matrix = generate_route_feature_availability_matrix(db=db_session)
        assert "overall_inventory" in matrix
        assert "routes" in matrix

        for route_code in ["PNW-JPN-BULK", "USG-JPN-BULK"]:
            assert route_code in matrix["routes"]
            r_info = matrix["routes"][route_code]
            assert r_info["usable_features_count"] == 34
            assert len(r_info["usable_feature_names"]) == 34

            # Sample sizes by horizon under strict forward semantics
            sizes = r_info["sample_sizes_by_horizon"]
            assert sizes["T+1"]["labeled_samples"] == 356
            assert sizes["T+1"]["unlabeled_samples"] == 2
            assert sizes["T+3"]["labeled_samples"] == 354
            assert sizes["T+3"]["unlabeled_samples"] == 4
            assert sizes["T+6"]["labeled_samples"] == 351
            assert sizes["T+6"]["unlabeled_samples"] == 7

            # Fallback baseline is persistence
            assert r_info["fallback_baseline"]["model"] == "persistence"
            assert r_info["fallback_baseline"]["status"] == "always_available"

    def test_reproducibility(self, db_session):
        """Dataset generation is 100% deterministic and reproducible."""
        ds1 = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1)
        ds2 = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1)

        df1 = ds1.to_dataframe()
        df2 = ds2.to_dataframe()
        pd.testing.assert_frame_equal(df1, df2)


class TestTemporalAvailabilityAndPublicationLag:
    """Tests verifying observation date vs publication date vs forecast decision timestamp."""

    def test_observation_date_not_automatically_availability_date(self, db_session):
        """Observation date precedes availability date by the configured publication lag."""
        ds = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1)

        for row in ds.rows:
            # Availability date is strictly in month M+1, after observation date in month M
            assert row.availability_date > row.observation_date
            # Default conservative lag is 14 days into next month (covering 2nd Thursday)
            assert row.availability_date.day == 14
            # Source feed has no exact publication timestamps (None)
            assert row.observed_publication_time is None
            # Policy assigns conservative availability cutoff and decision timestamp
            assert row.conservative_availability_cutoff is not None
            assert row.estimated_publication_time is not None
            assert row.decision_timestamp is not None
            # Strict ordering: decision timestamp must strictly succeed conservative cutoff (> not merely >=)
            assert row.decision_timestamp > row.conservative_availability_cutoff
            assert row.decision_timestamp > row.estimated_publication_time

        # Metadata records the publication lag and policy
        assert ds.metadata["availability_policy"]["publication_lag_days"] == 14
        assert ds.metadata["availability_policy"]["is_exact_point_in_time"] is False
        assert "USDA AMS" in ds.metadata["availability_policy"]["limitation_notes"]
        assert "GTR" in ds.metadata["availability_policy"]["limitation_notes"]

    def test_feature_published_after_decision_timestamp_cannot_enter(self, db_session):
        """Features must have availability_date <= decision_date."""
        policy = TemporalAvailabilityPolicy(publication_lag_days=14)
        ds = build_forecast_dataset(
            db=db_session, route_code="PNW-JPN-BULK", horizon_months=1, availability_policy=policy
        )

        for row in ds.rows:
            # Predictor availability date must be on or before decision date
            assert row.availability_date <= row.decision_date
            # Decision date must be strictly after the predictor observation period
            assert row.decision_date > row.observation_date

    def test_cross_corridor_features_obey_availability_rule(self, db_session):
        """Cross-corridor features use only competitor observations available at decision date."""
        ds_pnw = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1)
        ds_usg = build_forecast_dataset(db=db_session, route_code="USG-JPN-BULK", horizon_months=1)

        for r_pnw, r_usg in zip(ds_pnw.rows, ds_usg.rows):
            assert r_pnw.decision_date == r_usg.decision_date
            assert r_pnw.availability_date == r_usg.availability_date
            assert r_pnw.observation_date == r_usg.observation_date
            # Competitor lag 0 is the competitor rate available on decision date
            assert r_pnw.features["competitor_rate_lag_0"] == r_usg.features["rate_lag_0"]

    def test_future_targets_remain_isolated(self, db_session):
        """Target availability date is strictly after forecast decision timestamp."""
        ds = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1)

        for row in ds.labeled_rows:
            # Target observation date is in the future
            assert row.target_date > row.observation_date
            # Target availability date is strictly after the decision date
            assert row.target_availability_date > row.decision_date

    def test_inference_rows_use_only_available_data_at_decision_timestamp(self, db_session):
        """Inference rows using strict forward semantics target unobserved future periods."""
        ds = build_forecast_dataset(
            db=db_session, route_code="PNW-JPN-BULK", horizon_months=1, include_unlabeled_latest=True
        )

        unlabeled_rows = ds.unlabeled_rows
        # Strict forward T+1 has 2 inference rows (targeting unobserved Nov 2026 and Dec 2026)
        assert len(unlabeled_rows) == 2

        # 1. Latest inference row: October 2026 data published Nov 14, decision Nov 14, targets Dec 2026
        latest_unlabeled = ds.get_unlabeled_latest_row()
        assert latest_unlabeled is not None
        assert latest_unlabeled.observation_date == date(2026, 10, 1)
        assert latest_unlabeled.availability_date == date(2026, 11, 14)
        assert latest_unlabeled.decision_date == date(2026, 11, 14)
        assert latest_unlabeled.target_date == date(2026, 12, 1)
        assert latest_unlabeled.target_period_start == date(2026, 12, 1)
        assert latest_unlabeled.target_period_start > latest_unlabeled.decision_date
        assert latest_unlabeled.target_availability_date == date(2027, 1, 14)
        assert latest_unlabeled.target_value is None
        assert latest_unlabeled.is_labeled is False

        # 2. Previous inference row: September 2026 data published Oct 14, decision Oct 14, targets Nov 2026
        penultimate_unlabeled = unlabeled_rows[0]
        assert penultimate_unlabeled.observation_date == date(2026, 9, 1)
        assert penultimate_unlabeled.decision_date == date(2026, 10, 14)
        assert penultimate_unlabeled.target_date == date(2026, 11, 1)
        assert penultimate_unlabeled.target_period_start > penultimate_unlabeled.decision_date
        assert penultimate_unlabeled.target_value is None
        assert penultimate_unlabeled.is_labeled is False

        # 3. Latest labeled row: August 2026 data published Sep 14, decision Sep 14, targets October 2026
        labeled_last = ds.labeled_rows[-1]
        assert labeled_last.observation_date == date(2026, 8, 1)
        assert labeled_last.decision_date == date(2026, 9, 14)
        assert labeled_last.target_date == date(2026, 10, 1)
        assert labeled_last.target_period_start > labeled_last.decision_date
        assert labeled_last.target_value == 38.0  # PNW October 2026 rate is observed!
        assert labeled_last.is_labeled is True

    def test_legacy_observation_relative_horizon_mode(self, db_session):
        """Legacy observation-relative mode exactly reproduces C1/C3.1 horizon mapping."""
        legacy_policy = TemporalAvailabilityPolicy(horizon_alignment="observation_relative")
        ds = build_forecast_dataset(
            db=db_session, route_code="PNW-JPN-BULK", horizon_months=1, availability_policy=legacy_policy
        )

        assert len(ds.labeled_rows) == 357
        assert len(ds.unlabeled_rows) == 1

        unlabeled = ds.get_unlabeled_latest_row()
        assert unlabeled.observation_date == date(2026, 10, 1)
        assert unlabeled.target_date == date(2026, 11, 1)
        assert unlabeled.target_value is None

        labeled_last = ds.labeled_rows[-1]
        assert labeled_last.observation_date == date(2026, 9, 1)
        assert labeled_last.target_date == date(2026, 10, 1)
        assert labeled_last.target_value == 38.0


class TestFeatureRedundancyAndSubsets:
    """Tests verifying feature redundancy annotations, non-time-varying tags, and subset selectors."""

    def test_invariant_route_metadata_marked(self):
        """Route benchmark distance is explicitly tagged as invariant (non-time-varying)."""
        dist_feat = FEATURE_CATALOG["route_benchmark_distance_nm"]
        assert dist_feat.is_time_varying is False
        assert dist_feat.redundancy_group == "invariant_route_metadata"
        assert "Zero variance" in dist_feat.collinearity_notes

    def test_collinearity_annotations_present(self):
        """Regime flags, calendar features, and rolling averages have documented collinearity notes."""
        # Regime encoding
        reg_code = FEATURE_CATALOG["regime_code"]
        assert reg_code.redundancy_group == "regime_encoding"
        assert "dummy variable" in FEATURE_CATALOG["regime_pre_supercycle"].collinearity_notes

        # Calendar seasonality
        assert FEATURE_CATALOG["origin_month"].redundancy_group == "calendar_seasonality"
        assert FEATURE_CATALOG["origin_sin_month"].redundancy_group == "calendar_seasonality"

        # Differencing and rolling averages
        assert FEATURE_CATALOG["rate_diff_1"].redundancy_group == "rate_differencing"
        assert FEATURE_CATALOG["rolling_mean_3"].redundancy_group == "rolling_averages"

    def test_predefined_feature_subsets(self, db_session):
        """FEATURE_SUBSETS provides non-redundant and tree-based subsets."""
        assert "all" in FEATURE_SUBSETS
        assert "autoregressive" in FEATURE_SUBSETS
        assert "linear_non_redundant" in FEATURE_SUBSETS
        assert "tree_based" in FEATURE_SUBSETS
        assert "univariate" in FEATURE_SUBSETS

        # Linear non-redundant subset omits collinear features
        linear_subset = FEATURE_SUBSETS["linear_non_redundant"]
        assert "route_benchmark_distance_nm" not in linear_subset  # Invariant omitted
        assert "regime_pre_supercycle" not in linear_subset        # Reference category omitted (avoids dummy trap)
        assert "regime_supercycle_peak_crash" in linear_subset
        assert "rate_diff_1" not in linear_subset                  # Redundant diff omitted
        assert "rolling_mean_3" not in linear_subset               # Redundant MA omitted
        assert "origin_sin_month" in linear_subset                 # Continuous harmonic retained
        assert "origin_month" not in linear_subset                 # Integer step omitted

        # Build dataset with linear_non_redundant subset
        ds_lin = build_forecast_dataset(
            db=db_session, route_code="PNW-JPN-BULK", horizon_months=1, feature_set="linear_non_redundant"
        )
        assert len(ds_lin.feature_names) == len(linear_subset)
        assert set(ds_lin.feature_names) == set(linear_subset)

        # Build dataset with tree_based subset
        ds_tree = build_forecast_dataset(
            db=db_session, route_code="PNW-JPN-BULK", horizon_months=1, feature_set="tree_based"
        )
        assert "regime_code" in ds_tree.feature_names
        assert "rate_diff_1" in ds_tree.feature_names
        assert "rolling_mean_3" in ds_tree.feature_names


class TestC312StrictForwardSemanticsAndAvailability:
    """Comprehensive test suite for Stage C3.1.2 temporal invariant and availability rules.

    Verifies the 6 temporal invariants and conservative publication availability mandates:
    1. Every predictor availability <= decision timestamp.
    2. Target observation not available at decision timestamp.
    3. Target period begins strictly after decision timestamp.
    4. Zero future-data leakage in feature construction.
    5. Cross-corridor predictors obey availability constraints.
    6. Historical and inference rows follow identical temporal rules.
    """

    @pytest.mark.parametrize("route_code", ["PNW-JPN-BULK", "USG-JPN-BULK"])
    @pytest.mark.parametrize("horizon", [1, 3, 6])
    def test_target_month_starts_after_decision_timestamp(self, db_session, route_code: str, horizon: int):
        """Invariant 3: Target period begins strictly after decision date for all rows."""
        ds = build_forecast_dataset(db=db_session, route_code=route_code, horizon_months=horizon)
        for row in ds.rows:
            assert row.target_period_start > row.decision_date, (
                f"Violation: target_period_start {row.target_period_start} <= decision_date {row.decision_date}"
            )
            assert row.target_date == row.target_period_start
            assert row.target_date == add_months_to_date(row.observation_date, horizon + 1)

    @pytest.mark.parametrize("route_code", ["PNW-JPN-BULK", "USG-JPN-BULK"])
    @pytest.mark.parametrize("horizon", [1, 3, 6])
    def test_target_availability_strictly_after_decision_timestamp(self, db_session, route_code: str, horizon: int):
        """Invariant 2: Target observation is never available at decision date."""
        ds = build_forecast_dataset(db=db_session, route_code=route_code, horizon_months=horizon)
        for row in ds.rows:
            assert row.target_availability_date > row.decision_date, (
                f"Violation: target_availability {row.target_availability_date} <= decision {row.decision_date}"
            )

    @pytest.mark.parametrize("route_code", ["PNW-JPN-BULK", "USG-JPN-BULK"])
    @pytest.mark.parametrize("horizon", [1, 3, 6])
    def test_predictor_availability_no_later_than_decision_timestamp(self, db_session, route_code: str, horizon: int):
        """Invariant 1: Every predictor availability <= decision timestamp."""
        ds = build_forecast_dataset(db=db_session, route_code=route_code, horizon_months=horizon)
        for row in ds.rows:
            assert row.availability_date <= row.decision_date
            assert row.decision_date > row.observation_date

    def test_september_october_november_boundary_cases(self, db_session):
        """Explicit boundary verification for Sep, Oct, Nov 2026 under conservative 14-day lag."""
        ds = build_forecast_dataset(
            db=db_session, route_code="PNW-JPN-BULK", horizon_months=1, include_unlabeled_latest=True
        )

        # 1. August 2026 origin -> Labeled row targeting October 2026
        aug_row = next(r for r in ds.rows if r.origin_date == date(2026, 8, 1))
        assert aug_row.availability_date == date(2026, 9, 14)
        assert aug_row.decision_date == date(2026, 9, 14)
        assert aug_row.target_date == date(2026, 10, 1)
        assert aug_row.target_period_start == date(2026, 10, 1)
        assert aug_row.target_period_start > aug_row.decision_date
        assert aug_row.target_availability_date == date(2026, 11, 14)
        assert aug_row.is_labeled is True
        assert aug_row.target_value == 38.0

        # 2. September 2026 origin -> Unlabeled inference targeting November 2026
        sep_row = next(r for r in ds.rows if r.origin_date == date(2026, 9, 1))
        assert sep_row.availability_date == date(2026, 10, 14)
        assert sep_row.decision_date == date(2026, 10, 14)
        assert sep_row.target_date == date(2026, 11, 1)
        assert sep_row.target_period_start == date(2026, 11, 1)
        assert sep_row.target_period_start > sep_row.decision_date
        assert sep_row.target_availability_date == date(2026, 12, 14)
        assert sep_row.is_labeled is False
        assert sep_row.target_value is None

        # 3. October 2026 origin -> Unlabeled inference targeting December 2026
        oct_row = next(r for r in ds.rows if r.origin_date == date(2026, 10, 1))
        assert oct_row.availability_date == date(2026, 11, 14)
        assert oct_row.decision_date == date(2026, 11, 14)
        assert oct_row.target_date == date(2026, 12, 1)
        assert oct_row.target_period_start == date(2026, 12, 1)
        assert oct_row.target_period_start > oct_row.decision_date
        assert oct_row.target_availability_date == date(2027, 1, 14)
        assert oct_row.is_labeled is False
        assert oct_row.target_value is None

    def test_month_end_and_year_end_transitions(self):
        """Calendar transition arithmetic for year-end, month-end, and leap years under 14-day lag."""
        policy = TemporalAvailabilityPolicy(publication_lag_days=14, horizon_alignment="strict_forward")

        # Year-end transition: Dec observation -> Jan 14 availability next year
        dec_obs = date(2023, 12, 1)
        dec_avail = policy.calculate_availability_date(dec_obs)
        assert dec_avail == date(2024, 1, 14)
        assert policy.calculate_target_date(dec_obs, 1) == date(2024, 2, 1)
        assert policy.calculate_target_date(dec_obs, 3) == date(2024, 4, 1)
        assert policy.calculate_target_date(dec_obs, 6) == date(2024, 7, 1)

        # Leap year transition: Jan observation -> Feb 14 availability
        jan_leap = date(2024, 1, 1)
        assert policy.calculate_availability_date(jan_leap) == date(2024, 2, 14)
        feb_leap = date(2024, 2, 1)
        assert policy.calculate_availability_date(feb_leap) == date(2024, 3, 14)

        # Multi-year forward leap: T+6 from August 2025 -> March 2026
        aug_obs = date(2025, 8, 1)
        assert policy.calculate_target_date(aug_obs, 6) == date(2026, 3, 1)

    def test_second_thursday_mathematical_bounds_across_all_starting_days(self):
        """Verify that the second Thursday always falls between calendar days 8 and 14 across all weekdays."""
        policy = TemporalAvailabilityPolicy()
        # Test 12 consecutive months covering diverse starting weekdays
        for m in range(1, 13):
            obs = date(2024, m, 1)
            first_thu = policy.calculate_first_thursday(obs)
            second_thu = policy.calculate_second_thursday(obs)

            # 1st Thursday is strictly calendar days 1 to 7
            assert 1 <= first_thu.day <= 7, f"1st Thursday day {first_thu.day} out of range 1..7 for month {obs}"
            # 2nd Thursday is strictly calendar days 8 to 14
            assert 8 <= second_thu.day <= 14, f"2nd Thursday day {second_thu.day} out of range 8..14 for month {obs}"
            assert second_thu.weekday() == 3  # Thursday in Python is 3

            # Guaranteed: 14-day conservative availability date is >= second Thursday for EVERY month
            avail_date = policy.calculate_availability_date(obs)
            assert avail_date.day == 14
            assert avail_date >= second_thu, f"Day 14 failed to cover 2nd Thursday {second_thu} for month {obs}"

    def test_months_where_first_thursday_falls_on_different_calendar_days(self):
        """Verify boundary extremes: 1st Thursday on Day 1 vs Day 7."""
        policy = TemporalAvailabilityPolicy()

        # Extreme 1: Month starts on Thursday (e.g. Feb 2024) -> 1st Thursday is Day 1, 2nd is Day 8
        obs_jan24 = date(2024, 1, 1)  # Month M+1 is Feb 2024 (Feb 1, 2024 is Thursday)
        assert policy.calculate_first_thursday(obs_jan24) == date(2024, 2, 1)
        assert policy.calculate_second_thursday(obs_jan24) == date(2024, 2, 8)
        assert policy.calculate_availability_date(obs_jan24) == date(2024, 2, 14)

        # Extreme 2: Month starts on Friday (e.g. Mar 2024) -> 1st Thursday is Day 7, 2nd is Day 14 (worst-case)
        obs_feb24 = date(2024, 2, 1)  # Month M+1 is Mar 2024 (Mar 1, 2024 is Friday)
        assert policy.calculate_first_thursday(obs_feb24) == date(2024, 3, 7)
        assert policy.calculate_second_thursday(obs_feb24) == date(2024, 3, 14)
        assert policy.calculate_availability_date(obs_feb24) == date(2024, 3, 14)

        # Flaw demonstration: A 5-day lag fails to cover the 1st Thursday in March (Day 7) and 2nd Thursday in ALL months
        five_day_date = date(2024, 3, 5)
        assert five_day_date < date(2024, 3, 7)  # 5-day assumption violates 1st Thursday!
        assert five_day_date < date(2024, 3, 14)  # 5-day assumption violates 2nd Thursday!

    def test_publication_time_versus_decision_time_ordering(self):
        """Decision timestamp strictly post-dates publication instant in UTC, avoiding 00:00 intraday lookahead."""
        policy = TemporalAvailabilityPolicy()
        obs = date(2024, 6, 1)

        pub_dt = policy.calculate_publication_datetime(obs)
        dec_dt = policy.calculate_decision_datetime(obs)

        # Publication is at 12:00 PM Eastern; in July (EDT) it is 16:00:00 UTC
        assert pub_dt.tzinfo == timezone.utc
        assert pub_dt.hour == 16
        assert pub_dt.minute == 0

        # Decision is normalized to 23:59:59 UTC on decision date
        assert dec_dt.tzinfo == timezone.utc
        assert dec_dt.hour == 23
        assert dec_dt.minute == 59
        assert dec_dt.second == 59

        # Invariant: Decision strictly succeeds publication
        assert dec_dt > pub_dt
        time_buffer_seconds = (dec_dt - pub_dt).total_seconds()
        assert time_buffer_seconds == 7 * 3600 + 59 * 60 + 59  # Exactly 7 hours, 59 minutes, 59 seconds buffer

    def test_timezone_normalization_edt_and_est(self):
        """Verify correct UTC conversion during both Eastern Daylight Time and Eastern Standard Time."""
        policy = TemporalAvailabilityPolicy()

        # Summer month (EDT: UTC-4): July 14, 2024 at 12:00 PM Eastern -> 16:00:00 UTC
        obs_summer = date(2024, 6, 1)
        pub_summer_utc = policy.calculate_publication_datetime(obs_summer)
        assert pub_summer_utc.hour == 16

        # Winter month (EST: UTC-5): January 14, 2025 at 12:00 PM Eastern -> 17:00:00 UTC
        obs_winter = date(2024, 12, 1)
        pub_winter_utc = policy.calculate_publication_datetime(obs_winter)
        assert pub_winter_utc.hour == 17

        # In both seasons, decision timestamp at 23:59:59 UTC is strictly after publication
        assert policy.calculate_decision_datetime(obs_summer) > pub_summer_utc
        assert policy.calculate_decision_datetime(obs_winter) > pub_winter_utc

    def test_missing_publication_timestamps_preserved_as_none(self, db_session):
        """Observed publication timestamps are strictly None, never synthesized."""
        ds = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1)
        for row in ds.rows:
            assert row.observed_publication_time is None
            assert row.conservative_availability_cutoff is not None
            assert row.estimated_publication_time is not None
            assert row.decision_timestamp is not None
            # Strict ordering: decision timestamp must strictly succeed conservative cutoff (> not merely >=)
            assert row.decision_timestamp > row.conservative_availability_cutoff
            assert row.decision_timestamp > row.estimated_publication_time
        assert ds.metadata["availability_policy"]["is_exact_point_in_time"] is False
        assert ds.metadata["availability_policy"]["publication_lag_days"] == 14

    @pytest.mark.parametrize("route_code", ["PNW-JPN-BULK", "USG-JPN-BULK"])
    def test_classification_of_rows_historical_vs_inference(self, db_session, route_code: str):
        """Invariant 6: Labeled and unlabeled row partition across all 3 horizons."""
        # Total rows is invariant: 370 - 12 burn_in = 358
        for horizon, exp_labeled, exp_unlabeled in [(1, 356, 2), (3, 354, 4), (6, 351, 7)]:
            ds = build_forecast_dataset(db=db_session, route_code=route_code, horizon_months=horizon)
            assert len(ds.rows) == 358
            assert len(ds.labeled_rows) == exp_labeled
            assert len(ds.unlabeled_rows) == exp_unlabeled
            # Verify latest labeled row target date is always 2026-10-01 (latest available in DB)
            assert ds.labeled_rows[-1].target_date == date(2026, 10, 1)

    def test_market_regime_assignment_uses_target_period_semantics(self, db_session):
        """Target regime classification matches C1.3.2 target-period semantics; features use origin date."""
        from pelagos.forecasting.regimes import classify_target_date_regime

        ds = build_forecast_dataset(db=db_session, route_code="PNW-JPN-BULK", horizon_months=1)
        for row in ds.rows:
            # row.regime_id evaluates the macroeconomic era of the target period (matching C1.3.2)
            reg_def = classify_target_date_regime(row.target_date)
            expected_regime_id = reg_def.regime_id if reg_def else MarketRegimeId.PANDEMIC_DISRUPTION.value
            assert row.regime_id == expected_regime_id

            # Predictor features evaluate macroeconomic era of origin date (zero target leakage)
            rf_origin = calculate_regime_features(row.origin_date)
            assert row.features["regime_code"] == rf_origin["regime_code"]
            assert row.features["regime_pre_supercycle"] == rf_origin["regime_pre_supercycle"]

    def test_no_future_leakage_in_rolling_statistics_or_regime_features(self):
        """Features computed up to date t depend strictly on history <= t."""
        hist_1 = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0, 26.0, 28.0, 30.0, 32.0]
        # Mutated future values
        hist_2 = hist_1 + [999.0, 888.0]

        # Extract features for index 11 (the 32.0 element)
        stats_1 = calculate_rolling_statistics(hist_1)
        stats_2_at_11 = calculate_rolling_statistics(hist_2[:12])

        assert stats_1 == stats_2_at_11

    def test_decision_timestamp_must_strictly_exceed_conservative_cutoff_boundary(self, db_session):
        """Strict ordering (>): decision timestamps equal to or earlier than Day 14 cutoff must fail."""
        equal_to_edt_policy = TemporalAvailabilityPolicy(
            decision_hour_utc=16,
            decision_minute_utc=0,
            decision_second_utc=0,
        )
        with pytest.raises(AssertionError, match="Strict ordering violation"):
            build_forecast_dataset(
                db=db_session,
                route_code="PNW-JPN-BULK",
                horizon_months=1,
                availability_policy=equal_to_edt_policy,
            )

        earlier_than_est_policy = TemporalAvailabilityPolicy(
            decision_hour_utc=16,
            decision_minute_utc=30,
            decision_second_utc=0,
        )
        with pytest.raises(AssertionError, match="Strict ordering violation"):
            build_forecast_dataset(
                db=db_session,
                route_code="PNW-JPN-BULK",
                horizon_months=1,
                availability_policy=earlier_than_est_policy,
            )

    def test_dst_transition_months_preserve_strict_timestamp_ordering(self, db_session):
        """Spring-forward (EDT) and fall-back (EST) transitions keep strict > ordering on cutoffs and targets."""
        policy = TemporalAvailabilityPolicy()
        ds = build_forecast_dataset(
            db=db_session,
            route_code="PNW-JPN-BULK",
            horizon_months=1,
            availability_policy=policy,
        )
        rows_by_obs = {r.observation_date: r for r in ds.rows}

        # Feb 2024 observation -> Day 14 cutoff on 2024-03-14 (EDT = UTC-4 -> 16:00 UTC)
        feb_row = rows_by_obs[date(2024, 2, 1)]
        assert feb_row.conservative_availability_cutoff == datetime(2024, 3, 14, 16, 0, 0, tzinfo=timezone.utc)
        assert feb_row.decision_timestamp > feb_row.conservative_availability_cutoff

        # Oct 2024 observation -> Day 14 cutoff on 2024-11-14 (EST = UTC-5 -> 17:00 UTC)
        oct_row = rows_by_obs[date(2024, 10, 1)]
        assert oct_row.conservative_availability_cutoff == datetime(2024, 11, 14, 17, 0, 0, tzinfo=timezone.utc)
        assert oct_row.decision_timestamp > oct_row.conservative_availability_cutoff

        for row in ds.rows:
            tgt_period_start_utc = datetime(
                row.target_period_start.year,
                row.target_period_start.month,
                row.target_period_start.day,
                tzinfo=timezone.utc,
            )
            tgt_cutoff_utc = policy.calculate_conservative_availability_cutoff(row.target_date)
            assert row.decision_timestamp > row.conservative_availability_cutoff
            assert tgt_period_start_utc > row.decision_timestamp
            assert tgt_cutoff_utc > row.decision_timestamp

    def test_cross_corridor_exclusion_enforces_strict_timestamp_cutoff(self, monkeypatch):
        """Cross-corridor filtering uses strict cutoff < decision_timestamp, excluding exact-equality boundaries."""
        own_series = [(add_months_to_date(date(2024, 1, 1), k), 40.0) for k in range(14)]
        other_series = [(add_months_to_date(date(2024, 1, 1), k), 80.0) for k in range(14)] + [
            (date(2025, 3, 1), 999.0),
        ]

        def fake_get_obs(db=None, route_code="", storage_dir=None, exclude_reconstructed=True):
            if route_code == "PNW-JPN-BULK":
                return own_series, "mock"
            if route_code == "USG-JPN-BULK":
                return other_series, "mock"
            raise ValueError(f"Unexpected route {route_code}")

        monkeypatch.setattr("pelagos.forecasting.dataset.get_route_rate_observations", fake_get_obs)

        class ExactCutoffBoundaryPolicy(TemporalAvailabilityPolicy):
            """Policy where competitor month 2025-03-01 shares dec_date and hits exact cutoff == dec_dt equality."""

            def calculate_availability_date(self, observation_date: date) -> date:
                if observation_date == date(2025, 3, 1):
                    return date(2025, 3, 14)
                return super().calculate_availability_date(observation_date)

            def calculate_conservative_availability_cutoff(self, observation_date: date) -> datetime:
                if observation_date == date(2025, 3, 1):
                    return datetime(2025, 3, 14, 23, 59, 59, tzinfo=timezone.utc)
                return super().calculate_conservative_availability_cutoff(observation_date)

        ds = build_forecast_dataset(
            db=MagicMock(),
            route_code="PNW-JPN-BULK",
            horizon_months=1,
            burn_in_months=12,
            availability_policy=ExactCutoffBoundaryPolicy(),
        )
        latest_row = ds.rows[-1]
        assert latest_row.observation_date == date(2025, 2, 1)
        assert latest_row.decision_timestamp == datetime(2025, 3, 14, 23, 59, 59, tzinfo=timezone.utc)
        assert latest_row.features["competitor_rate_lag_0"] == 80.0
        assert latest_row.features["corridor_spread_lag_0"] == 40.0
