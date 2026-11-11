"""Pelagos Leakage-Safe Historical Forecast Dataset Preparation Layer.

Provides structured, leakage-safe dataset construction for freight forecasting,
generating aligned feature matrices and target variables across T+1, T+3, and T+6
horizons without lookahead bias.

Traces to DATA-001 Section 5.2, ML-001, and Stage C3.1 mandates:
- Origin-date temporal anchoring: all explanatory features strictly depend on history at or before t.
- Target separation: target rate y_{t+h} is isolated and strictly guarded against feature leakage.
- Excluded candidate audit: non-viable features (trade volumes, buoy readings, bunker prices)
  are audited, documented, and excluded rather than fabricated with synthetic proxies.
- Fallback baseline availability: Persistence remains universally available.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy.orm import Session

from pelagos.forecasting.features import (
    EXCLUDED_FEATURE_NAMES,
    FEATURE_CATALOG,
    FEATURE_SUBSETS,
    FeatureDefinition,
    ROUTE_METADATA,
    USABLE_FEATURE_NAMES,
    extract_all_usable_features,
)
from pelagos.forecasting.regimes import (
    HISTORICAL_REGIMES,
    MarketRegimeId,
    classify_target_date_regime,
)
from pelagos.forecasting.schemas import (
    DISALLOWED_DERIVED_ROUTES,
    SUPPORTED_DIRECT_ROUTES,
)
from pelagos.forecasting.service import get_route_rate_observations

logger = logging.getLogger(__name__)

SUPPORTED_HORIZONS = (1, 3, 6)
DEFAULT_BURN_IN_MONTHS = 12


@dataclass(frozen=True)
class TemporalAvailabilityPolicy:
    """Policy governing observation availability, publication lag, and forecast decision dates.

    Core Concepts & Semantics (C3.1.2):
    1. Observation Period / Date (observation_date):
       The calendar month of the freight rate measurement (e.g. 2026-10-01 represents October 2026).
    2. Actual Publication Timestamps vs. Conditional Rule-Based Availability Cutoffs:
       - Actual Publication Timestamp: SODA dataset ehs5-yac3 (AgTransport 'Vessel Rates',
         https://agtransport.usda.gov/Bulk/Vessel-Rates/ehs5-yac3) does NOT provide record-level
         release timestamps; observed_publication_time is strictly preserved as None.
       - Authoritative USDA AMS Sources & What Each Supports:
         * https://www.ams.usda.gov/services/transportation-analysis/gtr verifies weekly Thursday
           publication of the Grain Transportation Report (GTR), without advertising a guaranteed
           12:00 noon Eastern SLA.
         * https://www.ams.usda.gov/services/transportation-analysis/gtr-datasets separates weekly
           'Table 1: Grain transport cost indicators' from monthly ocean rates in 'Figure 20: Grain
           vessel rates, U.S. to Japan' (source: O'Neil Commodity Consulting).
         * https://www.ams.usda.gov/services/transportation-analysis/gtr/archive indexes 2020-2026
           Thursday PDFs (1996-2019 is unindexed in this web archive) and evidences a 7-week lapse
           in archive-2025 between 2025-09-25 and 2025-11-20.
         * Inspected PDFs (GTR10012026.pdf, GTR10082026.pdf, GTR11052026.pdf, Figure 20) provide
           several examples of monthly U.S.-to-Japan ocean rates appearing in first-Thursday
           editions post-month-end (e.g., Sept 2026 on Oct 1; Oct 2026 on Nov 5), while SODA view
           metadata (api/views/ehs5-yac3.json) recorded rowsUpdatedAt = 2026-11-05T17:16:28+00:00
           (12:16:28 PM EST).
         * Verification Boundary: Available USDA evidence does NOT establish that monthly
           Figure 20 is consistently published by the second Thursday following every observation
           month; the monthly Figure 20 publication deadline has not been independently verified
           across the historical period.
       - Pelagos Conservative Operational Assumption (Day 14 Cutoff):
         Calendar arithmetic alone establishes that in any month M+1:
         * 1st Thursday of month M+1 falls on calendar days 1 to 7.
         * 2nd Thursday of month M+1 falls on calendar days 8 to 14.
         A fixed 14-calendar-day post-month-end cutoff (`publication_lag_days = 14`) with a
         provisional intraday instant at `12:00 PM America/New_York` (16:00 UTC EDT / 17:00 UTC
         EST) is a **Pelagos conservative operational assumption**, informed by the weekly
         Thursday GTR publication pattern and second-Thursday calendar arithmetic. It is NOT
         an observed historical timestamp, is NOT a proven upper bound for monthly Figure 20
         publication, and does NOT guarantee availability during Federal holidays, exceptional
         release delays, incomplete online archive periods (1996-2019), or government
         shutdowns/funding lapses.
    3. Forecast Decision Date & Strict Timestamp Ordering (decision_date, decision_timestamp):
       To prevent treating date-only values as proof of intraday 00:00 availability and allow for
       early-afternoon updates (e.g., 12:16:28 PM EST), decision execution is normalized to
       end-of-day UTC (23:59:59 UTC). This enforces strict timestamp ordering:
       `decision_timestamp > conservative_availability_cutoff`.
    4. Target Period & Strict Forward Invariants:
       Under strict forward-forecast semantics (horizon_alignment='strict_forward'):
       - Target period start (00:00:00 UTC on day 1 of target month) and target conservative cutoff
         must both be strictly later than decision_timestamp.
       - Because decision_date occurs in month M+1, month M+1 has already begun on the 1st.
       - Therefore, an estimate of month M+1 at decision_date is a concurrent nowcast/in-month estimate.
       - A strict forward T+1 forecast targets month M+2 (target_offset = horizon_months + 1 relative to observation).
       - T+3 targets month M+4; T+6 targets month M+7.
    """

    policy_name: str = "conservative_second_thursday_gtr_release"
    publication_lag_days: int = 14  # Pelagos conservative operational assumption; informed by 2nd-Thursday calendar range (days 8-14)
    publication_hour_eastern: int = 12  # Provisional rule cutoff instant: 12:00 PM Eastern (normalized 23:59:59 UTC decision follows)
    publication_minute_eastern: int = 0
    publication_timezone: str = "America/New_York"
    normalization_timezone: str = "UTC"
    decision_hour_utc: int = 23  # Decision executed at end-of-day UTC (23:59:59 UTC), strictly after conservative cutoff
    decision_minute_utc: int = 59
    decision_second_utc: int = 59
    is_exact_point_in_time: bool = False
    horizon_alignment: str = "strict_forward"  # "strict_forward" (C3.1.2) or "observation_relative" (legacy C1)
    limitation_notes: str = (
        "AgTransport SODA dataset ehs5-yac3 (https://agtransport.usda.gov/Bulk/Vessel-Rates/ehs5-yac3) "
        "does not provide record-level publication timestamps; observed_publication_time is strictly "
        "preserved as None. Authoritative USDA AMS references: weekly Thursday GTR publication "
        "(https://www.ams.usda.gov/services/transportation-analysis/gtr; no official noon Eastern SLA "
        "verified), monthly ocean rate series in Figure 20 'Grain vessel rates, U.S. to Japan' compiled "
        "with O'Neil Commodity Consulting rather than weekly Table 1 cost indicators "
        "(https://www.ams.usda.gov/services/transportation-analysis/gtr-datasets), 2020-2026 issue "
        "archives (https://www.ams.usda.gov/services/transportation-analysis/gtr/archive; 1996-2019 absent), "
        "and inspected first-Thursday Figure 20 examples (GTR10012026.pdf, GTR11052026.pdf). Available "
        "evidence does not establish consistent second-Thursday publication across every observation month, "
        "and the monthly Figure 20 publication deadline has not been independently verified across the "
        "historical period. The fixed 14-calendar-day cutoff at provisional 12:00 PM America/New_York "
        "(16:00 EDT / 17:00 EST UTC) is a Pelagos conservative operational assumption informed by weekly "
        "Thursday publication and 2nd-Thursday calendar arithmetic (days 8-14) — not an observed release "
        "timestamp and not a proven upper bound for monthly Figure 20 publication. AgTransport SODA view "
        "metadata (api/views/ehs5-yac3.json) showed rowsUpdatedAt at 17:16:28 UTC (12:16:28 PM EST) on "
        "2026-11-05, archive-2025 exhibits a 7-week lapse (2025-09-25 to 2025-11-20), and Federal holidays, "
        "release delays, archive gaps, and shutdowns can exceed Day 14. Decision execution at 23:59:59 UTC "
        "enforces strict ordering (decision_timestamp > conservative_availability_cutoff)."
    )

    def calculate_second_thursday(self, observation_date: date) -> date:
        """Calculate the exact calendar date of the second Thursday in month M+1."""
        next_month_1st = add_months_to_date(observation_date, 1)
        days_to_first_thursday = (3 - next_month_1st.weekday()) % 7
        first_thursday = next_month_1st + timedelta(days=days_to_first_thursday)
        return first_thursday + timedelta(days=7)

    def calculate_first_thursday(self, observation_date: date) -> date:
        """Calculate the exact calendar date of the first Thursday in month M+1."""
        next_month_1st = add_months_to_date(observation_date, 1)
        days_to_first_thursday = (3 - next_month_1st.weekday()) % 7
        return next_month_1st + timedelta(days=days_to_first_thursday)

    def calculate_availability_date(self, observation_date: date) -> date:
        """Calculate the estimated availability date for an observation month."""
        next_month_1st = add_months_to_date(observation_date, 1)
        return next_month_1st + timedelta(days=max(0, self.publication_lag_days - 1))

    def calculate_decision_date(self, observation_date: date) -> date:
        """Calculate the forecast decision date for a model run triggered upon month M publication."""
        return self.calculate_availability_date(observation_date)

    def calculate_conservative_availability_cutoff(self, observation_date: date) -> datetime:
        """Calculate Pelagos's conservative operational availability cutoff in UTC.

        Evaluated at the provisional publication_hour_eastern (12:00 PM) in America/New_York on
        Day 14 post-month-end (informed by the weekly Thursday GTR pattern and 2nd-Thursday calendar
        arithmetic across days 8-14), then normalized to UTC.

        NOTE: This is a Pelagos conservative operational assumption, NOT an observed historical
        publication timestamp and NOT a proven upper bound for monthly Figure 20 publication (the
        monthly Figure 20 publication deadline has not been independently verified across the
        historical period). Subject to holiday, delayed-release, archive-gap, and shutdown caveats.
        """
        avail_date = self.calculate_availability_date(observation_date)
        eastern_tz = ZoneInfo(self.publication_timezone)
        pub_dt_eastern = datetime(
            avail_date.year,
            avail_date.month,
            avail_date.day,
            self.publication_hour_eastern,
            self.publication_minute_eastern,
            0,
            tzinfo=eastern_tz,
        )
        return pub_dt_eastern.astimezone(timezone.utc)

    def calculate_publication_datetime(self, observation_date: date) -> datetime:
        """Backward-compatible alias for calculate_conservative_availability_cutoff."""
        return self.calculate_conservative_availability_cutoff(observation_date)

    def calculate_decision_datetime(self, observation_date: date) -> datetime:
        """Calculate the normalized forecast decision timestamp in UTC.

        Normalized to 23:59:59 UTC on decision_date, guaranteeing that
        decision_datetime > conservative_availability_cutoff (16:00/17:00 UTC) strictly.
        """
        dec_date = self.calculate_decision_date(observation_date)
        return datetime(
            dec_date.year,
            dec_date.month,
            dec_date.day,
            self.decision_hour_utc,
            self.decision_minute_utc,
            self.decision_second_utc,
            tzinfo=timezone.utc,
        )

    def calculate_target_date(self, observation_date: date, horizon_months: int) -> date:
        """Calculate the target observation date based on policy horizon alignment.

        If horizon_alignment == 'strict_forward':
            Ensures target_period_start > decision_date.
            Since decision_date is in month M+1, earliest future month is M+2 (horizon=1).
            T+h corresponds to (horizon_months + 1) months ahead of observation_date.
        Else ('observation_relative'):
            Legacy C1/C3.1 semantics: target is M + horizon_months.
        """
        if self.horizon_alignment == "strict_forward":
            return add_months_to_date(observation_date, horizon_months + 1)
        return add_months_to_date(observation_date, horizon_months)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "publication_lag_days": self.publication_lag_days,
            "publication_hour_eastern": self.publication_hour_eastern,
            "publication_timezone": self.publication_timezone,
            "normalization_timezone": self.normalization_timezone,
            "is_exact_point_in_time": self.is_exact_point_in_time,
            "horizon_alignment": self.horizon_alignment,
            "limitation_notes": self.limitation_notes,
        }


@dataclass(frozen=True)
class ForecastDatasetRow:
    """A single chronologically aligned forecast observation row."""

    # 1. Observation period / date of latest predictor
    observation_date: date
    # 2. Actual or defensibly estimated publication / availability date
    availability_date: date
    # 3. Forecast decision timestamp
    decision_date: date
    # 4. Target date and target period start
    target_date: date
    target_period_start: date
    # 5. Target availability date
    target_availability_date: date
    horizon_months: int
    route_code: str
    target_value: Optional[float]
    target_change: Optional[float]  # y_{t+h} - y_t
    features: Dict[str, float]
    regime_id: str
    is_labeled: bool
    is_valid: bool = True
    origin_date: Optional[date] = None  # Backwards compatibility alias for observation_date / decision anchor
    skip_reason: Optional[str] = None
    observed_publication_time: Optional[datetime] = None  # Strictly None for historical SODA feeds
    conservative_availability_cutoff: Optional[datetime] = None  # Rule-based Day 14 12:00 PM Eastern / UTC cutoff
    estimated_publication_time: Optional[datetime] = None  # Backward-compatible alias for conservative_availability_cutoff
    decision_timestamp: Optional[datetime] = None  # Normalized 23:59:59 UTC timestamp strictly > cutoff
    horizon_mode: str = "strict_forward"

    def __post_init__(self):
        if self.origin_date is None:
            object.__setattr__(self, "origin_date", self.observation_date)
        if self.target_period_start is None:
            object.__setattr__(self, "target_period_start", self.target_date)
        # Synchronize conservative_availability_cutoff and estimated_publication_time
        if self.conservative_availability_cutoff is None and self.estimated_publication_time is not None:
            object.__setattr__(self, "conservative_availability_cutoff", self.estimated_publication_time)
        elif self.estimated_publication_time is None and self.conservative_availability_cutoff is not None:
            object.__setattr__(self, "estimated_publication_time", self.conservative_availability_cutoff)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["observation_date"] = self.observation_date.isoformat()
        d["availability_date"] = self.availability_date.isoformat()
        d["decision_date"] = self.decision_date.isoformat()
        d["origin_date"] = self.origin_date.isoformat() if self.origin_date else self.observation_date.isoformat()
        d["target_date"] = self.target_date.isoformat()
        d["target_period_start"] = self.target_period_start.isoformat()
        d["target_availability_date"] = self.target_availability_date.isoformat()
        d["horizon_mode"] = self.horizon_mode
        if self.observed_publication_time:
            d["observed_publication_time"] = self.observed_publication_time.isoformat()
        if self.conservative_availability_cutoff:
            d["conservative_availability_cutoff"] = self.conservative_availability_cutoff.isoformat()
        if self.estimated_publication_time:
            d["estimated_publication_time"] = self.estimated_publication_time.isoformat()
        if self.decision_timestamp:
            d["decision_timestamp"] = self.decision_timestamp.isoformat()
        return d


@dataclass
class ForecastDataset:
    """Container for an aligned multivariate forecast dataset."""

    route_code: str
    horizon_months: int
    burn_in_months: int
    feature_names: List[str]
    rows: List[ForecastDatasetRow]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def labeled_rows(self) -> List[ForecastDatasetRow]:
        """Rows with verified historical target values."""
        return [r for r in self.rows if r.is_labeled and r.is_valid]

    @property
    def unlabeled_rows(self) -> List[ForecastDatasetRow]:
        """Rows where origin is latest observed date and target is in the unobserved future."""
        return [r for r in self.rows if not r.is_labeled and r.is_valid]

    def to_dataframe(self, include_unlabeled: bool = True) -> pd.DataFrame:
        """Convert dataset rows to tabular pandas DataFrame."""
        rows_to_use = self.rows if include_unlabeled else self.labeled_rows
        if not rows_to_use:
            return pd.DataFrame()

        data: List[Dict[str, Any]] = []
        for r in rows_to_use:
            row_dict = {
                "observation_date": r.observation_date,
                "availability_date": r.availability_date,
                "decision_date": r.decision_date,
                "conservative_availability_cutoff": r.conservative_availability_cutoff,
                "estimated_publication_time": r.estimated_publication_time,
                "decision_timestamp": r.decision_timestamp,
                "origin_date": r.origin_date,
                "target_date": r.target_date,
                "target_period_start": r.target_period_start,
                "target_availability_date": r.target_availability_date,
                "horizon_months": r.horizon_months,
                "horizon_mode": r.horizon_mode,
                "route_code": r.route_code,
                "target_value": r.target_value,
                "target_change": r.target_change,
                "regime_id": r.regime_id,
                "is_labeled": r.is_labeled,
            }
            # Add feature values
            for fname in self.feature_names:
                row_dict[fname] = r.features.get(fname)
            data.append(row_dict)

        df = pd.DataFrame(data)
        df["observation_date"] = pd.to_datetime(df["observation_date"])
        df["availability_date"] = pd.to_datetime(df["availability_date"])
        df["decision_date"] = pd.to_datetime(df["decision_date"])
        df["origin_date"] = pd.to_datetime(df["origin_date"])
        df["target_date"] = pd.to_datetime(df["target_date"])
        df["target_period_start"] = pd.to_datetime(df["target_period_start"])
        df["target_availability_date"] = pd.to_datetime(df["target_availability_date"])
        return df

    def get_X_y(self) -> Tuple[pd.DataFrame, pd.Series]:
        """Extract labeled feature matrix X and target vector y for model training/evaluation."""
        labeled = self.labeled_rows
        if not labeled:
            empty_X = pd.DataFrame(columns=self.feature_names)
            empty_y = pd.Series(dtype=float, name="target_value")
            return empty_X, empty_y

        X_data = [{fname: r.features.get(fname) for fname in self.feature_names} for r in labeled]
        y_data = [r.target_value for r in labeled]

        X = pd.DataFrame(X_data, index=[pd.to_datetime(r.origin_date) for r in labeled])
        y = pd.Series(y_data, index=[pd.to_datetime(r.origin_date) for r in labeled], name="target_value")
        return X, y

    def get_X_y_deltas(self) -> Tuple[pd.DataFrame, pd.Series]:
        """Extract feature matrix X and rate change delta vector (y_{t+h} - y_t)."""
        labeled = self.labeled_rows
        if not labeled:
            empty_X = pd.DataFrame(columns=self.feature_names)
            empty_y = pd.Series(dtype=float, name="target_change")
            return empty_X, empty_y

        X_data = [{fname: r.features.get(fname) for fname in self.feature_names} for r in labeled]
        y_deltas = [r.target_change for r in labeled]

        X = pd.DataFrame(X_data, index=[pd.to_datetime(r.origin_date) for r in labeled])
        y = pd.Series(y_deltas, index=[pd.to_datetime(r.origin_date) for r in labeled], name="target_change")
        return X, y

    def get_unlabeled_latest_row(self) -> Optional[ForecastDatasetRow]:
        """Return the latest origin row without target value for out-of-sample inference."""
        unlabeled = self.unlabeled_rows
        return unlabeled[-1] if unlabeled else None

    def filter_by_regime(self, regime_id: str) -> ForecastDataset:
        """Filter dataset rows to a specific historical macroeconomic market regime."""
        filtered_rows = [r for r in self.rows if r.regime_id == regime_id]
        return ForecastDataset(
            route_code=self.route_code,
            horizon_months=self.horizon_months,
            burn_in_months=self.burn_in_months,
            feature_names=list(self.feature_names),
            rows=filtered_rows,
            metadata={**self.metadata, "regime_filter": regime_id},
        )

    def summary(self) -> Dict[str, Any]:
        """Compute dataset summary statistics."""
        labeled = self.labeled_rows
        unlabeled = self.unlabeled_rows

        regime_counts: Dict[str, int] = {}
        for r in labeled:
            regime_counts[r.regime_id] = regime_counts.get(r.regime_id, 0) + 1

        start_orig = self.rows[0].origin_date.isoformat() if self.rows else None
        end_orig = self.rows[-1].origin_date.isoformat() if self.rows else None
        start_dec = self.rows[0].decision_date.isoformat() if self.rows else None
        end_dec = self.rows[-1].decision_date.isoformat() if self.rows else None
        start_tgt = self.rows[0].target_date.isoformat() if self.rows else None
        end_tgt = self.rows[-1].target_date.isoformat() if self.rows else None

        return {
            "route_code": self.route_code,
            "horizon_months": self.horizon_months,
            "horizon_mode": self.metadata.get("horizon_mode", "strict_forward"),
            "burn_in_months": self.burn_in_months,
            "feature_count": len(self.feature_names),
            "feature_names": self.feature_names,
            "total_rows": len(self.rows),
            "labeled_rows_count": len(labeled),
            "unlabeled_rows_count": len(unlabeled),
            "earliest_decision_date": start_dec,
            "latest_decision_date": end_dec,
            "earliest_target_period": start_tgt,
            "latest_target_period": end_tgt,
            "origin_start_date": start_orig,
            "origin_end_date": end_orig,
            "target_start_date": start_tgt,
            "target_end_date": end_tgt,
            "observation_start_date": self.rows[0].observation_date.isoformat() if self.rows else None,
            "observation_end_date": self.rows[-1].observation_date.isoformat() if self.rows else None,
            "labeled_target_start_date": labeled[0].target_date.isoformat() if labeled else None,
            "labeled_target_end_date": labeled[-1].target_date.isoformat() if labeled else None,
            "regime_sample_distribution": regime_counts,
            "availability_policy": self.metadata.get("availability_policy"),
        }


def add_months_to_date(d: date, months: int) -> date:
    """Add a specified number of calendar months to a 1st-of-month date."""
    target_month = (d.month - 1 + months) % 12 + 1
    target_year = d.year + (d.month - 1 + months) // 12
    return date(target_year, target_month, 1)


def build_forecast_dataset(
    db: Session,
    route_code: str,
    horizon_months: int = 1,
    burn_in_months: int = DEFAULT_BURN_IN_MONTHS,
    include_unlabeled_latest: bool = True,
    storage_dir: Optional[Path] = None,
    exclude_reconstructed: bool = False,
    feature_set: Union[str, List[str]] = "all",
    availability_policy: Optional[TemporalAvailabilityPolicy] = None,
) -> ForecastDataset:
    """Build a leakage-safe, chronologically aligned multivariate forecasting dataset.

    Args:
        db: Active SQLAlchemy Session.
        route_code: Directly monitored freight corridor (e.g. PNW-JPN-BULK, USG-JPN-BULK).
        horizon_months: Forecast horizon in calendar months (1, 3, or 6).
        burn_in_months: Minimum trailing observation lookback required (default: 12).
        include_unlabeled_latest: If True, includes the latest origin row with target=None.
        storage_dir: Optional override directory for Parquet reading.
        exclude_reconstructed: If True, filters out C1.1 reconstructed observations.
        feature_set: Feature subset selector or list of feature names.
        availability_policy: Explicit publication lag policy governing when data becomes available.

    Returns:
        Structured ForecastDataset containing aligned rows, features, and metadata.
    """
    if route_code in DISALLOWED_DERIVED_ROUTES:
        raise ValueError(f"Route '{route_code}' is a derived indicator and cannot be modeled directly.")

    if route_code not in SUPPORTED_DIRECT_ROUTES:
        raise ValueError(f"Unsupported route code '{route_code}'. Supported direct routes: {list(SUPPORTED_DIRECT_ROUTES)}")

    if horizon_months not in SUPPORTED_HORIZONS:
        raise ValueError(f"Unsupported horizon '{horizon_months}'. Supported horizons: {list(SUPPORTED_HORIZONS)}")

    if burn_in_months < 1:
        raise ValueError(f"Burn-in months must be at least 1: got {burn_in_months}")

    if availability_policy is None:
        availability_policy = TemporalAvailabilityPolicy()

    # Resolve target features to include
    if isinstance(feature_set, list):
        target_features = set(feature_set)
    elif feature_set in FEATURE_SUBSETS:
        target_features = set(FEATURE_SUBSETS[feature_set])
    elif feature_set == "all":
        target_features = set(USABLE_FEATURE_NAMES)
    else:
        raise ValueError(
            f"Unknown feature_set '{feature_set}'. Supported subsets: {list(FEATURE_SUBSETS.keys())}"
        )

    # 1. Retrieve own-route chronological rate observations
    obs_own, source_own = get_route_rate_observations(
        db=db,
        route_code=route_code,
        storage_dir=storage_dir,
        exclude_reconstructed=exclude_reconstructed,
    )

    if len(obs_own) <= burn_in_months:
        raise ValueError(
            f"Insufficient observations for route '{route_code}': total={len(obs_own)} <= burn_in={burn_in_months}"
        )

    # 2. Retrieve competitor route observations for cross-corridor features
    competitor_code = ROUTE_METADATA.get(route_code, {}).get("competitor_route")
    obs_other: List[Tuple[date, float]] = []
    if competitor_code:
        try:
            obs_other, _ = get_route_rate_observations(
                db=db,
                route_code=competitor_code,
                storage_dir=storage_dir,
                exclude_reconstructed=exclude_reconstructed,
            )
        except Exception as exc:
            logger.warning("Could not load competitor route '%s': %s", competitor_code, exc)

    # Map dates to rates for fast lookup
    own_date_map: Dict[date, float] = {d: r for d, r in obs_own}

    rows: List[ForecastDatasetRow] = []
    all_feature_names: Set[str] = set()

    # 3. Construct chronological forecast steps starting from burn_in index
    for i in range(burn_in_months, len(obs_own)):
        orig_date, orig_rate = obs_own[i]
        avail_date = availability_policy.calculate_availability_date(orig_date)
        dec_date = availability_policy.calculate_decision_date(orig_date)
        pub_dt = availability_policy.calculate_conservative_availability_cutoff(orig_date)
        dec_dt = availability_policy.calculate_decision_datetime(orig_date)
        tgt_date = availability_policy.calculate_target_date(orig_date, horizon_months)
        tgt_period_start = tgt_date
        tgt_avail_date = availability_policy.calculate_availability_date(tgt_date)
        tgt_cutoff_dt = availability_policy.calculate_conservative_availability_cutoff(tgt_date)

        # Slice historical window strictly at or before origin date t
        hist_own = [r for _, r in obs_own[: i + 1]]

        # Corresponding historical window for competitor route strictly available before decision timestamp
        hist_other: List[float] = []
        if obs_other:
            hist_other = [
                r
                for d, r in obs_other
                if availability_policy.calculate_availability_date(d) <= dec_date
                and availability_policy.calculate_conservative_availability_cutoff(d) < dec_dt
            ]

        # Extract features (origin_date used to prevent future lookahead)
        raw_features = extract_all_usable_features(
            route_code=route_code,
            origin_date=orig_date,
            target_date=tgt_date,
            history_own=hist_own,
            history_other=hist_other,
        )

        selected_features = {k: v for k, v in raw_features.items() if k in target_features}
        all_feature_names.update(selected_features.keys())

        # Check target value availability
        tgt_rate = own_date_map.get(tgt_date)
        is_labeled = tgt_rate is not None

        target_change = None
        if is_labeled:
            target_change = round(tgt_rate - orig_rate, 4)

        # Regime assignment uses target-period semantics (C1.3.2)
        regime = classify_target_date_regime(tgt_date)
        regime_id = regime.regime_id if regime else MarketRegimeId.PANDEMIC_DISRUPTION.value

        # Temporal Invariant Assertions (C3.1.2):
        # 1. Observation period strictly precedes availability date (cannot be available before observed)
        assert avail_date > orig_date, f"Availability violation: {avail_date} <= {orig_date}"
        # 2. Decision date must be on or after predictor availability date
        assert dec_date >= avail_date, f"Predictor availability violation: {dec_date} < {avail_date}"
        # 3. Decision timestamp must strictly succeed the conservative availability cutoff (strict ordering: dec_dt > cutoff_dt)
        assert dec_dt > pub_dt, (
            f"Strict ordering violation: decision timestamp {dec_dt} must strictly succeed "
            f"conservative availability cutoff {pub_dt}"
        )
        # 4. Target availability date and conservative cutoff must strictly succeed decision date/timestamp
        assert tgt_avail_date > dec_date, f"Target lookahead violation: target avail {tgt_avail_date} <= decision {dec_date}"
        assert tgt_cutoff_dt > dec_dt, (
            f"Target cutoff lookahead violation: target cutoff {tgt_cutoff_dt} <= decision timestamp {dec_dt}"
        )
        # 5. Target period start must strictly succeed forecast decision date and timestamp (under strict_forward)
        if availability_policy.horizon_alignment == "strict_forward":
            tgt_period_start_dt = datetime(
                tgt_period_start.year,
                tgt_period_start.month,
                tgt_period_start.day,
                tzinfo=timezone.utc,
            )
            assert tgt_period_start > dec_date, (
                f"Strict forward violation: target period start {tgt_period_start} <= decision date {dec_date}"
            )
            assert tgt_period_start_dt > dec_dt, (
                f"Strict forward timestamp violation: target period start {tgt_period_start_dt} <= decision timestamp {dec_dt}"
            )
        # 6. Target observation period must strictly succeed predictor observation period
        assert tgt_date > orig_date, f"Target ordering violation: target {tgt_date} <= observation {orig_date}"
        # 7. Latest historical rate used in features must match orig_rate
        assert raw_features.get("rate_lag_0") == orig_rate

        row = ForecastDatasetRow(
            observation_date=orig_date,
            availability_date=avail_date,
            decision_date=dec_date,
            target_date=tgt_date,
            target_period_start=tgt_period_start,
            target_availability_date=tgt_avail_date,
            horizon_months=horizon_months,
            route_code=route_code,
            target_value=tgt_rate,
            target_change=target_change,
            features=selected_features,
            regime_id=regime_id,
            is_labeled=is_labeled,
            is_valid=True,
            origin_date=orig_date,
            observed_publication_time=None,  # Historical USDA AMS feed has no record-level publication time
            conservative_availability_cutoff=pub_dt,
            estimated_publication_time=pub_dt,
            decision_timestamp=dec_dt,
            horizon_mode=availability_policy.horizon_alignment,
        )
        rows.append(row)

    # Filter out unlabeled rows if user did not request them
    if not include_unlabeled_latest:
        rows = [r for r in rows if r.is_labeled]

    if isinstance(feature_set, str) and feature_set in FEATURE_SUBSETS:
        feature_names = [f for f in FEATURE_SUBSETS[feature_set] if f in all_feature_names]
    elif isinstance(feature_set, list):
        feature_names = [f for f in feature_set if f in all_feature_names]
    else:
        feature_names = sorted(all_feature_names)

    meta = {
        "source": source_own,
        "feature_set": feature_set if isinstance(feature_set, str) else "custom_list",
        "horizon_mode": availability_policy.horizon_alignment,
        "burn_in_months": burn_in_months,
        "exclude_reconstructed": exclude_reconstructed,
        "availability_policy": availability_policy.to_dict(),
        "temporal_alignment": {
            "publication_lag_days": availability_policy.publication_lag_days,
            "horizon_alignment": availability_policy.horizon_alignment,
            "decision_rule": "post_conservative_day_14_availability_cutoff",
            "historical_exact_timestamps_available": False,
            "limitation": (
                "AgTransport SODA dataset ehs5-yac3 does not provide record-level publication timestamps "
                "(observed_publication_time is preserved as None). Day 14 at provisional 12:00 PM America/New_York "
                "(16:00 EDT / 17:00 EST UTC) is a Pelagos conservative operational assumption informed by the "
                "weekly Thursday USDA AMS GTR pattern and 2nd-Thursday calendar arithmetic (days 8-14) — not an "
                "observed timestamp and not a proven upper bound for monthly Figure 20 publication (the monthly "
                "Figure 20 deadline has not been independently verified across the historical period). Availability "
                "is not guaranteed during Federal holidays, release delays (e.g., 12:16:28 PM EST SODA update on "
                "2026-11-05), incomplete archives (1996-2019 absent online), or shutdowns (e.g., 7-week lapse in "
                "archive-2025 from 2025-09-25 to 2025-11-20). Decision timestamps at 23:59:59 UTC strictly "
                "succeed the cutoff."
            ),
        },
    }

    return ForecastDataset(
        route_code=route_code,
        horizon_months=horizon_months,
        burn_in_months=burn_in_months,
        feature_names=feature_names,
        rows=rows,
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Feature Inventory & Route Availability Matrix Auditing
# ---------------------------------------------------------------------------

def generate_feature_inventory(db: Optional[Session] = None) -> List[Dict[str, Any]]:
    """Compile comprehensive historical coverage inventory across all 7 data domains."""
    from pelagos.database import SessionLocal

    session = db or SessionLocal()
    try:
        # 1. Freight observations
        from pelagos.models import (
            FreightObservation,
            TradeObservation,
            EnvironmentalObservation,
            BunkerPriceObservation,
        )

        # Query authoritative live observation counts from database session
        freight_count = session.query(FreightObservation).count()
        trade_count = session.query(TradeObservation).count()
        env_count = session.query(EnvironmentalObservation).count()
        bunker_count = session.query(BunkerPriceObservation).count()

        inventory: List[Dict[str, Any]] = [
            {
                "domain_number": 1,
                "domain_name": "Historical Freight Rates",
                "source": "USDA Agricultural Marketing Service (AMS) / O'Neil Commodity Consulting",
                "available_routes_or_locations": "PNW-JPN-BULK (4,520 NM), USG-JPN-BULK (9,150 NM), SPREAD-USG-PNW-JPN",
                "earliest_observation": "1996-01-01",
                "latest_observation": "2026-10-01",
                "observation_frequency": "Monthly (first of month calendar anchor)",
                "genuine_observations_count": freight_count,
                "missingness_rate_pct": 0.0,
                "monthly_modeling_sufficiency": "Sufficient (370 consecutive months, 30.8 years)",
                "alignment_feasibility": "Feasible (Canonical monthly 1-to-1 date alignment)",
                "usability_status": "Usable (Primary target & autoregressive predictors)",
                "justification": "Complete monthly continuity across all 4 macroeconomic regimes without temporal gaps.",
            },
            {
                "domain_number": 2,
                "domain_name": "Bilateral Maritime Trade Volumes",
                "source": "USDA FAS GATS / Indian DGCI&S",
                "available_routes_or_locations": "US to Japan (Wheat, Corn, Soybeans); India exports (Rice, Iron Ore, Ferro-chrome)",
                "earliest_observation": "2024-10-01",
                "latest_observation": "2024-12-01",
                "observation_frequency": "Monthly",
                "genuine_observations_count": trade_count,
                "missingness_rate_pct": 99.1,
                "monthly_modeling_sufficiency": "Insufficient (< 1% timeline coverage; only 3 months in late 2024)",
                "alignment_feasibility": "Infeasible for 1996-2026 historical model training",
                "usability_status": "Excluded from C3.1 historical training sets",
                "justification": "Severe historical data truncation: 367 of 370 months missing. Synthetic backfilling strictly disallowed by Pelagos integrity policy.",
            },
            {
                "domain_number": 3,
                "domain_name": "Oceanographic Buoy & Marine Meteorology",
                "source": "NOAA National Data Buoy Center (NDBC) / INCOIS",
                "available_routes_or_locations": "Buoys 46041 (Cape Elizabeth, WA), 42001 (Mid Gulf, LA), AD01 (Arabian Sea)",
                "earliest_observation": "2024-10-15",
                "latest_observation": "2026-10-30",
                "observation_frequency": "Point observations (sub-daily / hourly snapshots)",
                "genuine_observations_count": env_count,
                "missingness_rate_pct": 99.9,
                "monthly_modeling_sufficiency": "Insufficient (< 0.1% coverage; isolated snapshots in 2024 and 2026)",
                "alignment_feasibility": "Infeasible for multi-year historical training; deep-sea open ocean segments unmonitored",
                "usability_status": "Excluded from C3.1 historical training sets",
                "justification": "Moored buoys cover localized departure fairways on select dates only. Retained strictly as real-time departure water context in route intelligence.",
            },
            {
                "domain_number": 4,
                "domain_name": "Commercial Marine Bunker Fuel Prices",
                "source": "Bunker Fuel Prices (bunkerfuelprices.com)",
                "available_routes_or_locations": "Buenos Aires (ARBUE), Skagen (DKSKA); corridor hubs (Seattle, Houston, Yokohama) unquoted",
                "earliest_observation": "2026-11-06",
                "latest_observation": "2026-11-06",
                "observation_frequency": "Spot market indications",
                "genuine_observations_count": bunker_count,
                "missingness_rate_pct": 100.0,
                "monthly_modeling_sufficiency": "Insufficient (Zero historical depth prior to November 2026)",
                "alignment_feasibility": "Infeasible for 1996-2026 historical model training",
                "usability_status": "Excluded from C3.1 historical training sets",
                "justification": "Free provider feed exposes only latest spot indications. No historical time-series available. Substituting crude oil proxies (WTI, Brent) is strictly prohibited.",
            },
            {
                "domain_number": 5,
                "domain_name": "Port & Route Structural Characteristics",
                "source": "UNECE UN/LOCODE, World Port Index (Pub 150), Freight Routes Catalog",
                "available_routes_or_locations": "USSEA, USPDX, USHOU, USMSY, JPYOK, PNW-JPN-BULK, USG-JPN-BULK",
                "earliest_observation": "Static metadata",
                "latest_observation": "Static metadata",
                "observation_frequency": "Invariant over time",
                "genuine_observations_count": 18,
                "missingness_rate_pct": 0.0,
                "monthly_modeling_sufficiency": "Sufficient (Static structural metadata)",
                "alignment_feasibility": "Feasible (Constant across all monthly steps)",
                "usability_status": "Usable (Benchmark distance, region, and vessel class metadata)",
                "justification": "Provides verified geometric distance (4,520 NM vs 9,150 NM) and vessel compatibility constraints.",
            },
            {
                "domain_number": 6,
                "domain_name": "Historical Macroeconomic Market Regimes",
                "source": "Pelagos Macroeconomic Market Regime Definitions (Stage C1.3.2)",
                "available_routes_or_locations": "Global maritime grain trade (applied to both PNW and USG corridors)",
                "earliest_observation": "1996-01-01",
                "latest_observation": "2026-10-01",
                "observation_frequency": "Monthly regime classification",
                "genuine_observations_count": 370,
                "missingness_rate_pct": 0.0,
                "monthly_modeling_sufficiency": "Sufficient (Full 370-month continuous classification)",
                "alignment_feasibility": "Feasible (Origin date t deterministically classified without lookahead bias)",
                "usability_status": "Usable (Regime binary flags and categorical codes)",
                "justification": "Standardized analytical segments (Pre-Supercycle, Peak/Crash, Post-Supercycle, Pandemic Disruption) documented under C1.3.2.",
            },
            {
                "domain_number": 7,
                "domain_name": "Cross-Corridor Freight Rates & Inter-Corridor Spread",
                "source": "USDA Agricultural Marketing Service (AMS)",
                "available_routes_or_locations": "Simultaneous USG and PNW grain export corridors to Japan",
                "earliest_observation": "1996-01-01",
                "latest_observation": "2026-10-01",
                "observation_frequency": "Monthly",
                "genuine_observations_count": 370,
                "missingness_rate_pct": 0.0,
                "monthly_modeling_sufficiency": "Sufficient (370 consecutive months)",
                "alignment_feasibility": "Feasible (Contemporaneous and lagged competitor rates available at origin t)",
                "usability_status": "Usable (Competitor lag-0/lag-1, corridor spread lag-0, spread MA-3)",
                "justification": "Defensible economic relationship: US Gulf and PNW grain elevators directly compete for Japanese grain import tenders.",
            },
        ]
        return inventory
    finally:
        if db is None:
            session.close()


def generate_route_feature_availability_matrix(db: Optional[Session] = None) -> Dict[str, Any]:
    """Generate route-by-route feature availability matrices for PNW-JPN-BULK and USG-JPN-BULK."""
    from pelagos.database import SessionLocal

    session = db or SessionLocal()
    try:
        inventory = generate_feature_inventory(db=session)

        routes_result: Dict[str, Any] = {}
        for route_code in SUPPORTED_DIRECT_ROUTES:
            # Build datasets across all 3 horizons to get exact sample sizes
            ds_1 = build_forecast_dataset(db=session, route_code=route_code, horizon_months=1, burn_in_months=12)
            ds_3 = build_forecast_dataset(db=session, route_code=route_code, horizon_months=3, burn_in_months=12)
            ds_6 = build_forecast_dataset(db=session, route_code=route_code, horizon_months=6, burn_in_months=12)

            routes_result[route_code] = {
                "route_code": route_code,
                "route_name": ROUTE_METADATA.get(route_code, {}).get("origin_region") + " to Japan Bulk Corridor",
                "benchmark_distance_nm": ROUTE_METADATA.get(route_code, {}).get("benchmark_distance_nm"),
                "competitor_corridor": ROUTE_METADATA.get(route_code, {}).get("competitor_route"),
                "usable_features_count": len(ds_1.feature_names),
                "usable_feature_names": ds_1.feature_names,
                "sample_sizes_by_horizon": {
                    "T+1": {
                        "horizon_months": 1,
                        "labeled_samples": ds_1.summary()["labeled_rows_count"],
                        "unlabeled_samples": ds_1.summary()["unlabeled_rows_count"],
                        "origin_range": f"{ds_1.summary()['origin_start_date']} to {ds_1.summary()['origin_end_date']}",
                        "target_range": f"{ds_1.summary()['target_start_date']} to {ds_1.summary()['target_end_date']}",
                    },
                    "T+3": {
                        "horizon_months": 3,
                        "labeled_samples": ds_3.summary()["labeled_rows_count"],
                        "unlabeled_samples": ds_3.summary()["unlabeled_rows_count"],
                        "origin_range": f"{ds_3.summary()['origin_start_date']} to {ds_3.summary()['origin_end_date']}",
                        "target_range": f"{ds_3.summary()['target_start_date']} to {ds_3.summary()['target_end_date']}",
                    },
                    "T+6": {
                        "horizon_months": 6,
                        "labeled_samples": ds_6.summary()["labeled_rows_count"],
                        "unlabeled_samples": ds_6.summary()["unlabeled_rows_count"],
                        "origin_range": f"{ds_6.summary()['origin_start_date']} to {ds_6.summary()['origin_end_date']}",
                        "target_range": f"{ds_6.summary()['target_start_date']} to {ds_6.summary()['target_end_date']}",
                    },
                },
                "excluded_candidate_features": [
                    {
                        "feature_name": name,
                        "source": FEATURE_CATALOG[name].source,
                        "reason": FEATURE_CATALOG[name].exclusion_reason,
                    }
                    for name in EXCLUDED_FEATURE_NAMES
                ],
                "fallback_baseline": {
                    "model": "persistence",
                    "status": "always_available",
                    "minimum_history_required": 1,
                    "notes": "Guaranteed fallback whenever latest rate observation y_t is observed.",
                },
            }

        return {
            "overall_inventory": inventory,
            "routes": routes_result,
        }
    finally:
        if db is None:
            session.close()
