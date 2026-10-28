"""Unit tests for Pelagos Voyage Freight-Cost Calculator (OPT-001).

Validates:
1. Deterministic arithmetic for gross freight cost (cargo tonnes * rate in USD/MT).
2. Input validation (positive tonnage, valid units).
3. Missing rate data and unsupported trade corridors return explicit UNAVAILABLE.
4. Provenance and unit verification for rate and distance.
5. Strict omission of unvalidated forward recommendations, Net TCE, or bunker fuel assumptions.
"""

from __future__ import annotations

from datetime import date
import pytest
from sqlalchemy.orm import Session

from pelagos.database import SessionLocal
from pelagos.optimization.schemas import (
    CalculationStatus,
    VoyageCalculationRequest,
    VoyageCalculationResult,
)
from pelagos.optimization.calculator import calculate_voyage_freight


@pytest.fixture
def db_session():
    """Database session fixture."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


class TestVoyageCalculatorArithmetic:
    """Deterministic arithmetic validation for gross freight cost."""

    def test_seattle_to_yokohama_gross_freight_cost(self, db_session: Session) -> None:
        """50,000 MT Seattle (USSEA) -> Yokohama (JPYOK) at 37.31 USD/MT equals 1,865,500.00 USD."""
        req = VoyageCalculationRequest(
            origin="USSEA",
            destination="JPYOK",
            cargo_tonnes=50000.0,
        )
        result = calculate_voyage_freight(db=db_session, request=req)

        assert result.status == CalculationStatus.CALCULATED
        assert result.cargo_tonnes == 50000.0
        assert result.cargo_unit == "MT"
        assert result.route is not None
        assert result.route.route_code == "PNW-JPN-BULK"
        assert result.applicable_rate is not None
        assert result.applicable_rate.rate_value == 37.31
        assert result.applicable_rate.currency == "USD"
        assert result.applicable_rate.unit == "USD/MT"

        # Deterministic gross freight cost calculation: 50,000 * 37.31 = 1,865,500.00
        expected_cost = round(50000.0 * 37.31, 2)
        assert result.gross_freight_cost == 1865500.0
        assert result.gross_freight_cost == expected_cost
        assert result.gross_freight_cost_currency == "USD"

        # Distance verification
        assert result.distance is not None
        assert result.distance.distance_unit == "NM"
        assert result.distance.distance_nm > 0

    def test_us_gulf_to_japan_gross_freight_cost(self, db_session: Session) -> None:
        """60,000 MT US Gulf (USMSY) -> Yokohama (JPYOK) at 76.38 USD/MT equals 4,582,800.00 USD."""
        req = VoyageCalculationRequest(
            origin="USMSY",
            destination="JPYOK",
            cargo_tonnes=60000.0,
        )
        result = calculate_voyage_freight(db=db_session, request=req)

        assert result.status == CalculationStatus.CALCULATED
        assert result.cargo_tonnes == 60000.0
        assert result.route is not None
        assert result.route.route_code == "USG-JPN-BULK"
        assert result.applicable_rate is not None
        assert result.applicable_rate.rate_value == 76.38

        # Deterministic gross freight cost: 60,000 * 76.38 = 4,582,800.00
        assert result.gross_freight_cost == 4582800.0
        assert result.gross_freight_cost_currency == "USD"

    def test_direct_route_code_calculation(self, db_session: Session) -> None:
        """Direct route_code query bypasses port snapping and calculates correctly."""
        req = VoyageCalculationRequest(
            route_code="PNW-JPN-BULK",
            cargo_tonnes=45000.0,
        )
        result = calculate_voyage_freight(db=db_session, request=req)

        assert result.status == CalculationStatus.CALCULATED
        assert result.route is not None
        assert result.route.route_code == "PNW-JPN-BULK"
        assert result.gross_freight_cost == round(45000.0 * 37.31, 2)

    def test_as_of_date_historical_point_in_time(self, db_session: Session) -> None:
        """Specifying historical as_of_date retrieves rate as of that date with zero lookahead."""
        # On 2026-08-01, PNW-JPN-BULK was 36.75 USD/MT
        req = VoyageCalculationRequest(
            route_code="PNW-JPN-BULK",
            cargo_tonnes=50000.0,
            as_of_date=date(2026, 8, 1),
        )
        result = calculate_voyage_freight(db=db_session, request=req)

        assert result.status == CalculationStatus.CALCULATED
        assert result.applicable_rate is not None
        assert result.applicable_rate.observation_date == "2026-08-01"
        assert result.applicable_rate.rate_value == 36.75
        assert result.gross_freight_cost == round(50000.0 * 36.75, 2)
        assert result.gross_freight_cost == 1837500.0


class TestVoyageCalculatorInputValidation:
    """Validation of inputs, units, and boundary conditions."""

    def test_negative_cargo_tonnage_rejected_by_pydantic(self) -> None:
        """Negative cargo tonnage raises ValueError in schema."""
        with pytest.raises(ValueError, match="strictly positive"):
            VoyageCalculationRequest(
                origin="USSEA",
                destination="JPYOK",
                cargo_tonnes=-5000.0,
            )

    def test_zero_cargo_tonnage_rejected_by_pydantic(self) -> None:
        """Zero cargo tonnage raises ValueError in schema."""
        with pytest.raises(ValueError, match="strictly positive"):
            VoyageCalculationRequest(
                origin="USSEA",
                destination="JPYOK",
                cargo_tonnes=0.0,
            )

    def test_dict_input_with_invalid_tonnage_returns_invalid_input(self, db_session: Session) -> None:
        """Passing a raw dictionary with negative tonnage returns status INVALID_INPUT."""
        result = calculate_voyage_freight(
            db=db_session,
            request={"origin": "USSEA", "destination": "JPYOK", "cargo_tonnes": -100.0},
        )
        assert result.status == CalculationStatus.INVALID_INPUT
        assert "positive" in (result.message or "").lower()

    def test_unsupported_cargo_unit_rejected(self, db_session: Session) -> None:
        """Non-metric cargo unit (e.g. 'bushels') is rejected."""
        req = VoyageCalculationRequest(
            origin="USSEA",
            destination="JPYOK",
            cargo_tonnes=50000.0,
            cargo_unit="bushels",
        )
        result = calculate_voyage_freight(db=db_session, request=req)
        assert result.status == CalculationStatus.INVALID_INPUT
        assert "unsupported cargo unit" in (result.message or "").lower()

    def test_missing_route_and_endpoints_rejected(self, db_session: Session) -> None:
        """Omitting both route_code and endpoints returns INVALID_INPUT."""
        req = VoyageCalculationRequest(
            cargo_tonnes=50000.0,
        )
        result = calculate_voyage_freight(db=db_session, request=req)
        assert result.status == CalculationStatus.INVALID_INPUT
        assert "either" in (result.message or "").lower()


class TestVoyageCalculatorMissingDataAndUnsupportedRoutes:
    """Integrity enforcement for unmonitored routes and missing observations."""

    def test_unmonitored_rotterdam_to_singapore_returns_unavailable(self, db_session: Session) -> None:
        """Rotterdam (NLRTM) -> Singapore (SGSIN) returns UNAVAILABLE without fabricating rates or cost."""
        req = VoyageCalculationRequest(
            origin="NLRTM",
            destination="SGSIN",
            cargo_tonnes=55000.0,
        )
        result = calculate_voyage_freight(db=db_session, request=req)

        assert result.status == CalculationStatus.UNAVAILABLE
        assert result.gross_freight_cost is None
        assert result.applicable_rate is None
        assert result.route is None
        assert "unmonitored" in (result.message or "").lower()

    def test_unsupported_direct_route_code_returns_unavailable(self, db_session: Session) -> None:
        """Unknown corridor route code returns UNAVAILABLE."""
        req = VoyageCalculationRequest(
            route_code="NONEXISTENT-TRADE-LANE",
            cargo_tonnes=50000.0,
        )
        result = calculate_voyage_freight(db=db_session, request=req)

        assert result.status == CalculationStatus.UNAVAILABLE
        assert result.gross_freight_cost is None
        assert "not a monitored" in (result.message or "").lower()

    def test_derived_route_spread_rejected(self, db_session: Session) -> None:
        """SPREAD-USG-PNW-JPN is a derived rate difference and cannot be used for freight cost calculation."""
        req = VoyageCalculationRequest(
            route_code="SPREAD-USG-PNW-JPN",
            cargo_tonnes=50000.0,
        )
        result = calculate_voyage_freight(db=db_session, request=req)

        assert result.status == CalculationStatus.UNAVAILABLE
        assert result.gross_freight_cost is None
        assert "derived" in (result.message or "").lower()

    def test_historical_date_prior_to_data_coverage_returns_unavailable(self, db_session: Session) -> None:
        """Querying as_of_date before any recorded observation returns UNAVAILABLE without guessing."""
        req = VoyageCalculationRequest(
            route_code="PNW-JPN-BULK",
            cargo_tonnes=50000.0,
            as_of_date=date(1990, 1, 1),
        )
        result = calculate_voyage_freight(db=db_session, request=req)

        assert result.status == CalculationStatus.UNAVAILABLE
        assert result.gross_freight_cost is None
        assert "no verified freight rate observations" in (result.message or "").lower()


class TestVoyageCalculatorProvenanceAndCommercialIntegrity:
    """Verification of provenance, units, and omission of unvalidated speculative assumptions."""

    def test_rate_and_distance_provenance_auditability(self, db_session: Session) -> None:
        """Audit trail confirms rate source, period, dataset, and distance provenance."""
        req = VoyageCalculationRequest(
            origin="USSEA",
            destination="JPYOK",
            cargo_tonnes=50000.0,
        )
        result = calculate_voyage_freight(db=db_session, request=req)

        # Rate provenance
        rate = result.applicable_rate
        assert rate is not None
        assert rate.provenance_validated is True
        assert "USDA" in rate.source_name
        assert rate.unit == "USD/MT"
        assert rate.currency == "USD"
        assert rate.observation_period == "monthly"

        # Distance provenance
        dist = result.distance
        assert dist is not None
        assert dist.distance_unit == "NM"
        assert dist.distance_nm > 0
        assert len(dist.provenance) > 0

    def test_strict_omission_of_unvalidated_recommendations_and_bunker_assumptions(self, db_session: Session) -> None:
        """Ensure no FIX_NOW / WAIT_FORWARD, Net TCE, or arbitrary bunker assumptions are included."""
        req = VoyageCalculationRequest(
            origin="USSEA",
            destination="JPYOK",
            cargo_tonnes=50000.0,
        )
        result = calculate_voyage_freight(db=db_session, request=req)
        res_dict = result.model_dump()

        # Omission checks
        assert "recommendation" not in res_dict
        assert "fix_now" not in str(res_dict).lower()
        assert "wait_forward" not in str(res_dict).lower()
        assert "net_tce" not in res_dict
        assert "bunker_cost" not in res_dict
        assert "ifo380" not in str(res_dict).lower()
        assert "vlsfo" not in str(res_dict).lower()

        # Mandatory disclaimers present
        assert len(result.disclaimers) >= 3
        assert any("grain" in d.lower() for d in result.disclaimers)
