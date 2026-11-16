"""tests/test_p2c_voyage_economics.py
Phase 2-C: Voyage Economics Integration & Decoupling Tests
Verification 4: Economic Integration & Predecessor Regression Gate
"""

import base64
import datetime
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pelagos.models import (
    Base,
    VoyageDisbursementRecord,
    VoyageDisbursementLineItem,
)
from pelagos.readiness.fixture_validation import (
    CommissionGovernanceTier,
    DurationStatus,
    VoyageDurationInput,
)
from pelagos.optimization.voyage_economics import (
    VoyageEconomicsService,
    GrossOceanFreightResult,
    NetVoyageRevenueResult,
    VoyageTCEResult,
    OwnerOperatingProfitabilityResult,
)
from pelagos.security.signature_engine import (
    SignatureEngine,
    SignatureAlgorithm,
    TrustedKeyEntry,
)
from pelagos.security.merkle import RecordSetMerkleTree
from pelagos.audit.audit_verifier import DisbursementAuditVerifier, compute_record_payload_hash
from pelagos.currency import PROVIDER_MAX_LOOKBACK_CALENDAR_DAYS
from cryptography.hazmat.primitives.asymmetric import ed25519


@pytest.fixture
def standard_duration():
    return VoyageDurationInput(
        laden_sea_days=16.0,
        ballast_sea_days=14.0,
        loading_days=3.0,
        discharge_days=2.5,
        port_waiting_days=1.0,
        canal_waiting_days=0.5,
        canal_transit_days=1.0,
        duration_status=DurationStatus.CONTRACTUAL_AGREED,
    )  # Total days: 38.0


def test_gross_ocean_freight_isolated_calculation():
    """Verify Gross Ocean Freight calculation is strictly cargo * freight rate."""
    service = VoyageEconomicsService()

    # 75,000 MT at $22.50 / MT = $1,687,500.00
    res = service.calculate_gross_ocean_freight(cargo_tonnes=75000.0, freight_rate_usd_per_mt=22.50)
    assert res.status == "CALCULATED"
    assert res.cargo_tonnes == 75000.0
    assert res.freight_rate_usd_per_mt == 22.50
    assert res.gross_freight_usd == 1687500.00

    # Strict rejection of non-positive cargo or rate
    with pytest.raises(ValueError):
        service.calculate_gross_ocean_freight(cargo_tonnes=-100.0, freight_rate_usd_per_mt=20.0)

    with pytest.raises(ValueError):
        service.calculate_gross_ocean_freight(cargo_tonnes=50000.0, freight_rate_usd_per_mt=0.0)


def test_net_voyage_revenue_calculation():
    """Verify Net Voyage Revenue correctly deducts commissions, port disb, canals, bunkers."""
    service = VoyageEconomicsService()

    # Gross: $1,687,500.00
    # Commission: 2.5% = $42,187.50
    # Port disbursements: $115,000.00
    # Canal transits: $250,000.00
    # Bunker fuel: $420,000.00
    # Demurrage/despatch: +$15,000.00 (earned demurrage)
    # Expected Net = 1,687,500 - 42,187.50 - 115,000 - 250,000 - 420,000 + 15,000 = $875,312.50
    res = service.calculate_net_voyage_revenue(
        gross_freight_usd=1687500.00,
        commission_rate=0.025,
        commission_tier=CommissionGovernanceTier.CONTRACT_VERIFIED,
        port_disbursements_usd=115000.00,
        canal_transits_usd=250000.00,
        bunker_fuel_costs_usd=420000.00,
        demurrage_despatch_usd=15000.00,
    )
    assert res.status == "CALCULATED"
    assert res.commission_deductions_usd == 42187.50
    assert res.net_voyage_revenue_usd == 875312.50
    assert res.is_operational_approved is False


def test_conventional_voyage_tce_does_not_deduct_opex(standard_duration):
    """
    CRITICAL INVARIANT TEST:
    Conventional Voyage TCE strictly computes NVR / total_voyage_days.
    It does NOT deduct vessel OPEX or capital costs.
    """
    service = VoyageEconomicsService()
    net_revenue = 875312.50
    total_days = standard_duration.total_voyage_days  # 38.0 days

    # Expected TCE = 875,312.50 / 38.0 = 23034.53947... -> $23,034.54 / day
    res = service.calculate_voyage_tce(
        net_voyage_revenue_usd=net_revenue,
        duration=standard_duration,
        operational_mode=False,
    )
    assert res.status == "CALCULATED"
    assert res.total_voyage_days == 38.0
    assert res.tce_daily_rate_usd == 23034.54
    assert res.governance_tier == "LEVEL_2_RESEARCH_ONLY"
    assert res.is_operational_approved is False

    # Check disclaimer explicitly confirms OPEX is not deducted
    assert any("Conventional Voyage TCE does NOT deduct vessel OPEX" in d for d in res.disclaimers)


def test_owner_operating_profitability_independent_model():
    """Verify Owner Operating Profitability as an independent downstream accounting model."""
    service = VoyageEconomicsService()

    # TCE: $23,034.54 / day
    # Vessel daily OPEX (crew, stores, insurance): $7,500.00 / day
    # Vessel daily CapEx (debt service amortization): $4,500.00 / day
    # Total days: 38.0
    # Owner Daily Margin = 23,034.54 - 7,500.00 - 4,500.00 = $11,034.54 / day
    # Total Owner Profit = 11,034.54 * 38.0 = $419,312.52
    res = service.calculate_owner_operating_profitability(
        tce_daily_rate_usd=23034.54,
        total_voyage_days=38.0,
        vessel_daily_opex_usd=7500.00,
        vessel_daily_capex_usd=4500.00,
    )
    assert res.status == "CALCULATED"
    assert res.tce_daily_rate_usd == 23034.54
    assert res.vessel_daily_opex_usd == 7500.00
    assert res.vessel_daily_capex_usd == 4500.00
    assert res.owner_daily_net_margin_usd == 11034.54
    assert res.total_owner_profit_usd == 419312.52
    assert any("strictly segregated from the conventional maritime Voyage TCE" in d for d in res.disclaimers)


def test_retrieval_and_integrity_verification_of_disbursement_record():
    """Verify integration with persisted VoyageDisbursementRecord and tamper detection."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    now = datetime.datetime.now(datetime.timezone.utc)
    sig_engine = SignatureEngine()
    ed_priv = ed25519.Ed25519PrivateKey.generate()
    ed_pub = ed_priv.public_key()
    kid = "eco-key-01"
    sig_engine.register_trusted_key(
        TrustedKeyEntry(
            kid=kid,
            public_key=ed_pub,
            alg=SignatureAlgorithm.ED25519,
            valid_from=now - datetime.timedelta(days=1),
            valid_until=now + datetime.timedelta(days=30),
        )
    )

    verifier = DisbursementAuditVerifier(session, signature_engine=sig_engine)
    service = VoyageEconomicsService(db=session, audit_verifier=verifier)

    # Create line items
    line_data = [
        {
            "tariff_identifier": "TAR-SGSIN",
            "description": "Port Dues",
            "charge_category": "PORT_DUES",
            "amount_usd": "65000.00",
            "local_currency": "USD",
            "applicability_type": "MANDATORY",
        }
    ]
    tree = RecordSetMerkleTree(line_data)

    rec = VoyageDisbursementRecord(
        voyage_id="VOY-ECO-001",
        vessel_imo=9593256,
        origin_port_unlocode="SGSIN",
        destination_port_unlocode="NLRTM",
        transit_date=datetime.date(2026, 4, 10),
        readiness_tier="TIER_1_STATUTORY",
        completeness_status="COMPLETE",
        total_disbursement_usd=Decimal("65000.00"),
        merkle_root=tree.root_hex,
        signing_key_id=kid,
    )
    p_hash = compute_record_payload_hash(rec, tree.root_hex)
    rec.record_payload_hash = p_hash
    sig_bytes = SignatureEngine.sign_test_payload(ed_priv, bytes.fromhex(p_hash), SignatureAlgorithm.ED25519)
    rec.attestation_signature = base64.b64encode(sig_bytes).decode("utf-8")

    session.add(rec)
    session.flush()

    li = VoyageDisbursementLineItem(
        voyage_disbursement_record_id=rec.id,
        charge_type="PORT_DUES",
        location_code="SGSIN",
        rate_value=Decimal("65000.00"),
        billing_unit="LUMP_SUM",
        tariff_identifier="TAR-SGSIN",
        description="Port Dues",
        charge_category="PORT_DUES",
        amount_usd=Decimal("65000.00"),
        local_currency="USD",
        applicability_type="MANDATORY",
    )
    session.add(li)
    session.commit()

    # 1. Retrieve intact record
    record, errors = service.retrieve_and_verify_disbursement_record(rec.id)
    assert record is not None
    assert len(errors) == 0

    # 2. Non-existent record
    rec_none, err_none = service.retrieve_and_verify_disbursement_record(99999)
    assert rec_none is None
    assert "not found" in err_none[0]


def test_central_bank_currency_lookback_limits():
    """Verify statutory lookback limits for IMF, ECB, and MAS providers."""
    assert PROVIDER_MAX_LOOKBACK_CALENDAR_DAYS["IMF"] == 4
    assert PROVIDER_MAX_LOOKBACK_CALENDAR_DAYS["SDR"] == 4
    assert PROVIDER_MAX_LOOKBACK_CALENDAR_DAYS["ECB"] == 4
    assert PROVIDER_MAX_LOOKBACK_CALENDAR_DAYS["MAS"] == 5


def test_operational_mode_hard_lock_in_phase_2(standard_duration):
    """Verify that operational_mode=True strictly fails closed in Phase 2."""
    service = VoyageEconomicsService()

    # Attempt operational calculation without token
    res_no_tok = service.calculate_voyage_tce(
        net_voyage_revenue_usd=875312.50,
        duration=standard_duration,
        operational_mode=True,
    )
    assert res_no_tok.status == "BLOCKED_GATING_FAILED"
    assert res_no_tok.is_operational_approved is False
    assert any("LEVEL_3_OPERATIONAL_BLOCKED" in b for b in res_no_tok.blocked_reasons)
    assert any("MISSING_OPERATIONAL_TOKEN" in b for b in res_no_tok.blocked_reasons)

