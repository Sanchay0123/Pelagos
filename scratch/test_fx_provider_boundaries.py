"""
Independent Boundary Verification Script for FX Provider-Specific Lookback (VERIFY-F143-PHASE1-RECON-001)
Section 2: Provider-specific FX lookback verification
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
import datetime
from pelagos.currency import (
    CurrencyConversionService,
    CurrencyEvidenceClassification,
    resolve_provider_max_lookback,
)

def run_fx_boundary_tests():
    print("=" * 70)
    print("RUNNING FX PROVIDER LOOKBACK BOUNDARY VERIFICATION")
    print("=" * 70)

    # 1. Test resolve_provider_max_lookback
    assert resolve_provider_max_lookback("IMF", "SDR") == 4
    assert resolve_provider_max_lookback("IMF Daily SDR Valuation", "USD", "SDR") == 4
    assert resolve_provider_max_lookback("ECB", "EUR") == 4
    assert resolve_provider_max_lookback("European Central Bank (ECB) Reference Rate", "EUR", "USD") == 4
    assert resolve_provider_max_lookback("MAS", "SGD") == 5
    assert resolve_provider_max_lookback("Monetary Authority of Singapore (MAS) Daily Rates", "SGD", "USD") == 5
    assert resolve_provider_max_lookback("UNKNOWN", "GBP") == 0
    print("[PASS] resolve_provider_max_lookback unit limits verified: IMF=4, ECB=4, MAS=5, other=0")

    # Initialize standalone service
    service = CurrencyConversionService()

    # Register anchor rates on 2024-01-10
    base_date = datetime.date(2024, 1, 10)
    service.register_rate("SDR", "USD", 1.3300, base_date, "IMF Daily SDR Valuation", "https://imf.org", "DIRECTLY_VERIFIED")
    service.register_rate("EUR", "USD", 1.0900, base_date, "European Central Bank (ECB) Reference Rate", "https://ecb.europa.eu", "DIRECTLY_VERIFIED")
    service.register_rate("SGD", "USD", 0.7500, base_date, "Monetary Authority of Singapore (MAS) Daily Rates", "https://mas.gov.sg", "DIRECTLY_VERIFIED")
    service.register_rate("EUR", "USD", 1.0850, base_date, "ECB Benchmark (Holiday Interpolation)", "https://ecb.europa.eu", "PARTIALLY_VERIFIED")

    # 2. Test IMF SDR Boundaries (Limit = 4 calendar days)
    # Exact limit: 2024-01-10 to 2024-01-14 (lag = 4)
    res_imf_exact = service.convert(100.0, "SDR", "USD", valuation_date=datetime.date(2024, 1, 14))
    assert res_imf_exact.is_valid is True, f"IMF lag 4 failed: {res_imf_exact.failure_reason}"
    assert res_imf_exact.observation_lag_days == 4
    assert res_imf_exact.actual_observation_date == base_date
    print("[PASS] IMF SDR @ exactly 4 days: VALID (lag=4)")

    # 1 day beyond: 2024-01-10 to 2024-01-15 (lag = 5)
    res_imf_stale = service.convert(100.0, "SDR", "USD", valuation_date=datetime.date(2024, 1, 15))
    assert res_imf_stale.is_valid is False
    assert "EXPIRED_OR_STALE_CURRENCY_OBSERVATION" in res_imf_stale.failure_reason
    assert res_imf_stale.observation_lag_days == 5
    print("[PASS] IMF SDR @ 5 days (1 day beyond): REJECTED (lag=5)")

    # 3. Test ECB EUR Boundaries (Limit = 4 calendar days)
    # Exact limit: 2024-01-10 to 2024-01-14 (lag = 4)
    res_ecb_exact = service.convert(100.0, "EUR", "USD", valuation_date=datetime.date(2024, 1, 14), rate_provider="European Central Bank (ECB) Reference Rate")
    assert res_ecb_exact.is_valid is True, f"ECB lag 4 failed: {res_ecb_exact.failure_reason}"
    assert res_ecb_exact.observation_lag_days == 4
    assert res_ecb_exact.actual_observation_date == base_date
    print("[PASS] ECB EUR @ exactly 4 days: VALID (lag=4)")

    # 1 day beyond: 2024-01-10 to 2024-01-15 (lag = 5)
    res_ecb_stale = service.convert(100.0, "EUR", "USD", valuation_date=datetime.date(2024, 1, 15), rate_provider="European Central Bank (ECB) Reference Rate")
    assert res_ecb_stale.is_valid is False
    assert "EXPIRED_OR_STALE_CURRENCY_OBSERVATION" in res_ecb_stale.failure_reason
    assert res_ecb_stale.observation_lag_days == 5
    print("[PASS] ECB EUR @ 5 days (1 day beyond): REJECTED (lag=5)")

    # 4. Test MAS SGD Boundaries (Limit = 5 calendar days)
    # Exact limit: 2024-01-10 to 2024-01-15 (lag = 5)
    res_mas_exact = service.convert(100.0, "SGD", "USD", valuation_date=datetime.date(2024, 1, 15))
    assert res_mas_exact.is_valid is True, f"MAS lag 5 failed: {res_mas_exact.failure_reason}"
    assert res_mas_exact.observation_lag_days == 5
    assert res_mas_exact.actual_observation_date == base_date
    print("[PASS] MAS SGD @ exactly 5 days: VALID (lag=5)")

    # 1 day beyond: 2024-01-10 to 2024-01-16 (lag = 6)
    res_mas_stale = service.convert(100.0, "SGD", "USD", valuation_date=datetime.date(2024, 1, 16))
    assert res_mas_stale.is_valid is False
    assert "EXPIRED_OR_STALE_CURRENCY_OBSERVATION" in res_mas_stale.failure_reason
    assert res_mas_stale.observation_lag_days == 6
    print("[PASS] MAS SGD @ 6 days (1 day beyond): REJECTED (lag=6)")

    # 5. Future-dated observation
    # Valuation date 2024-01-08 when observation is 2024-01-10
    res_future = service.convert(100.0, "SDR", "USD", valuation_date=datetime.date(2024, 1, 8))
    assert res_future.is_valid is False
    assert "FUTURE_EXCHANGE_RATE_OBSERVATION_REJECTED" in res_future.failure_reason
    print("[PASS] Future-dated observation: REJECTED fail-closed")

    # 6. Missing observation
    # JPY has no registered rates
    res_missing = service.convert(100.0, "JPY", "USD", valuation_date=datetime.date(2024, 1, 10))
    assert res_missing.is_valid is False
    assert "MISSING_EXCHANGE_RATE_FOR_DATE" in res_missing.failure_reason
    print("[PASS] Missing observation: REJECTED fail-closed")

    # 7. USD identity conversion without external observation date
    res_identity = service.convert(1000.0, "USD", "USD", valuation_date=datetime.date(2024, 1, 10))
    assert res_identity.is_valid is True
    assert res_identity.exchange_rate == 1.0
    assert res_identity.converted_amount == 1000.0
    assert res_identity.rate_provider == "IDENTITY"
    assert res_identity.effective_date is None
    assert res_identity.actual_observation_date is None
    assert res_identity.observation_lag_days == 0
    assert res_identity.conversion_evidence_classification == CurrencyEvidenceClassification.IDENTITY
    print("[PASS] USD identity conversion: VALID (rate=1.0, lag=0, effective_date=None, observation_date=None)")

    # 8. Benchmark rate rejected in operational mode
    res_bench_res = service.convert(100.0, "EUR", "USD", valuation_date=base_date, rate_provider="ECB Benchmark (Holiday Interpolation)", operational_mode=False)
    assert res_bench_res.is_valid is True
    assert res_bench_res.conversion_evidence_classification == CurrencyEvidenceClassification.BENCHMARK

    res_bench_op = service.convert(100.0, "EUR", "USD", valuation_date=base_date, rate_provider="ECB Benchmark (Holiday Interpolation)", operational_mode=True)
    assert res_bench_op.is_valid is False
    assert "BENCHMARK_EXCHANGE_RATE_REJECTED" in res_bench_op.failure_reason
    print("[PASS] Benchmark rate: ALLOWED in research mode, REJECTED in operational mode")

    print("=" * 70)
    print("ALL 8 FX PROVIDER BOUNDARY TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    run_fx_boundary_tests()
