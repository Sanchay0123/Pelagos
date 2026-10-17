"""
Pelagos Experimental Freight Index Engine.
Implements the Pelagos Experimental Grain Index (U.S.–Japan) (PEGI-USJPN).
Traces to ML-001, REQ-FR-FRF-001, and REQ-NFR-DIP-001.

Guarantees:
1. Primary rate metric: Unweighted arithmetic mean across USG and PNW loading legs (USD/MT).
2. Primary relative index: Base-100 normalized two-route index relative to October 2024.
3. Exploratory scenario metric: Ton-mile distance-weighted rate (explicitly labeled exploratory).
4. Spread: Maintained strictly as a separate derived indicator (USG - PNW); never added to composite.
5. Zero imputation: Missing or invalid route rates trigger explicit 'insufficient_data' status.
"""

from typing import Dict, Any, List, Optional
from datetime import date
from dataclasses import dataclass, asdict

# Benchmark constants from specification ML-001
INDEX_CODE = "PEGI-USJPN"
INDEX_NAME = "Pelagos Experimental Grain Index (U.S.-Japan)"


DEFAULT_BASE_DATE = date(2024, 10, 1)
DEFAULT_BASE_USG_RATE = 53.65
DEFAULT_BASE_PNW_RATE = 30.05

BENCHMARK_DISTANCE_USG_NM = 9150.0
BENCHMARK_DISTANCE_PNW_NM = 4520.0

STATUS_CALCULATED = "calculated"
STATUS_INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class FreightIndexRecord:
    """Immutable representation of a single monthly index observation."""
    observation_date: date
    pegi_usd_mt: Optional[float] = None
    pegi_points: Optional[float] = None
    spread_usd_mt: Optional[float] = None
    pegi_distance_weighted_usd_mt: Optional[float] = None
    usg_rate: Optional[float] = None
    pnw_rate: Optional[float] = None
    status: str = STATUS_CALCULATED
    index_code: str = INDEX_CODE
    index_name: str = INDEX_NAME
    base_period: date = DEFAULT_BASE_DATE
    base_usg_usd_mt: float = DEFAULT_BASE_USG_RATE
    base_pnw_usd_mt: float = DEFAULT_BASE_PNW_RATE
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Converts record to JSON-serializable dictionary."""
        d = asdict(self)
        d["observation_date"] = self.observation_date.isoformat()
        d["base_period"] = self.base_period.isoformat()
        return d


def calculate_pegi_point(
    observation_date: date,
    usg_rate: Optional[float],
    pnw_rate: Optional[float],
    base_usg: float = DEFAULT_BASE_USG_RATE,
    base_pnw: float = DEFAULT_BASE_PNW_RATE,
    base_date: date = DEFAULT_BASE_DATE,
) -> FreightIndexRecord:
    """
    Computes a single monthly point of the Pelagos Experimental Grain Index.

    Formulas:
    - pegi_usd_mt = (USG + PNW) / 2
    - pegi_points = 0.5 * ((USG / base_usg) + (PNW / base_pnw)) * 100
    - spread_usd_mt = USG - PNW
    - pegi_distance_weighted_usd_mt = (w_usg * USG) + (w_pnw * PNW) [Exploratory Scenario]

    Strict Eligibility & Insufficient-Data Behavior:
    - If base rates are non-positive: raises ValueError.
    - If either USG or PNW rate is None, non-positive, or missing: returns an explicit
      'insufficient_data' record with None metrics. Zero imputation or synthetic data.
    """
    if base_usg is None or base_usg <= 0.0 or base_pnw is None or base_pnw <= 0.0:
        raise ValueError(f"Base rates must be strictly positive non-zero numbers: got base_usg={base_usg}, base_pnw={base_pnw}")

    # Enforce strict eligibility: both constituent routes must be observed and positive
    if (
        usg_rate is None
        or pnw_rate is None
        or usg_rate <= 0.0
        or pnw_rate <= 0.0
    ):
        return FreightIndexRecord(
            observation_date=observation_date,
            pegi_usd_mt=None,
            pegi_points=None,
            spread_usd_mt=None,
            pegi_distance_weighted_usd_mt=None,
            usg_rate=usg_rate,
            pnw_rate=pnw_rate,
            status=STATUS_INSUFFICIENT_DATA,
            index_code=INDEX_CODE,
            index_name=INDEX_NAME,
            base_period=base_date,
            base_usg_usd_mt=base_usg,
            base_pnw_usd_mt=base_pnw,
            notes="Missing or non-positive rate for one or more constituent routes. Zero imputation applied.",
        )

    # 1. Primary arithmetic mean rate (USD/MT)
    pegi_usd_mt = round((usg_rate + pnw_rate) / 2.0, 4)

    # 2. Normalized two-route relative index (Base 100 points)
    pegi_points = round(0.5 * ((usg_rate / base_usg) + (pnw_rate / base_pnw)) * 100.0, 2)

    # 3. Spread indicator: strictly USG - PNW (never added to composite)
    spread_usd_mt = round(usg_rate - pnw_rate, 2)

    # 4. Distance-weighted composite: retained only as an exploratory scenario
    total_dist = BENCHMARK_DISTANCE_USG_NM + BENCHMARK_DISTANCE_PNW_NM
    w_usg = BENCHMARK_DISTANCE_USG_NM / total_dist
    w_pnw = BENCHMARK_DISTANCE_PNW_NM / total_dist
    pegi_distance_weighted_usd_mt = round(w_usg * usg_rate + w_pnw * pnw_rate, 4)

    return FreightIndexRecord(
        observation_date=observation_date,
        pegi_usd_mt=pegi_usd_mt,
        pegi_points=pegi_points,
        spread_usd_mt=spread_usd_mt,
        pegi_distance_weighted_usd_mt=pegi_distance_weighted_usd_mt,
        usg_rate=usg_rate,
        pnw_rate=pnw_rate,
        status=STATUS_CALCULATED,
        index_code=INDEX_CODE,
        index_name=INDEX_NAME,
        base_period=base_date,
        base_usg_usd_mt=base_usg,
        base_pnw_usd_mt=base_pnw,
        notes=None,
    )


def compute_pegi_series(
    monthly_records: List[Dict[str, Any]],
    base_usg: Optional[float] = None,
    base_pnw: Optional[float] = None,
    base_date: date = DEFAULT_BASE_DATE,
) -> List[FreightIndexRecord]:
    """
    Computes a chronological series of PEGI index records from a sequence of monthly inputs.
    monthly_records item format: {"observation_date": date|str, "usg_rate": float|None, "pnw_rate": float|None}

    If base_usg or base_pnw is not provided:
    Checks if base_date is in monthly_records. If so, resolves base rates from that observation.
    Otherwise falls back to DEFAULT_BASE_USG_RATE and DEFAULT_BASE_PNW_RATE.
    """
    if not monthly_records:
        return []

    # Normalize observation dates and sort chronologically
    normalized_inputs = []
    for r in monthly_records:
        obs_d = r.get("observation_date")
        if isinstance(obs_d, str):
            obs_d = date.fromisoformat(obs_d)
        normalized_inputs.append({
            "observation_date": obs_d,
            "usg_rate": r.get("usg_rate"),
            "pnw_rate": r.get("pnw_rate"),
        })

    normalized_inputs.sort(key=lambda x: x["observation_date"])

    # Resolve base values
    resolved_base_usg = base_usg
    resolved_base_pnw = base_pnw

    if resolved_base_usg is None or resolved_base_pnw is None:
        base_match = next((item for item in normalized_inputs if item["observation_date"] == base_date), None)
        if (
            base_match
            and base_match["usg_rate"] is not None
            and base_match["usg_rate"] > 0
            and base_match["pnw_rate"] is not None
            and base_match["pnw_rate"] > 0
        ):
            resolved_base_usg = float(base_match["usg_rate"])
            resolved_base_pnw = float(base_match["pnw_rate"])
        else:
            resolved_base_usg = DEFAULT_BASE_USG_RATE
            resolved_base_pnw = DEFAULT_BASE_PNW_RATE

    results: List[FreightIndexRecord] = []
    for item in normalized_inputs:
        rec = calculate_pegi_point(
            observation_date=item["observation_date"],
            usg_rate=item["usg_rate"],
            pnw_rate=item["pnw_rate"],
            base_usg=resolved_base_usg,
            base_pnw=resolved_base_pnw,
            base_date=base_date,
        )
        results.append(rec)

    return results
