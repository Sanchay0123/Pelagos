"""
Pelagos Forecast Statistical Testing and Comparative Significance Engine.
Implements dependency-aware statistical hypothesis testing for out-of-sample forecast comparisons:
1. Loss differential: d_tau = L(e_{1, tau}) - L(e_{k, tau}) (MAE loss differential, positive favors candidate).
2. Diebold-Mariano (1995) test with Harvey-Leybourne-Newbold (HLN 1997) small-sample modification,
   evaluated against Student's t-distribution with (N - 1) degrees of freedom.
3. Moving Block Bootstrap (Kunsch 1989) 95% confidence intervals with deterministic seed.
4. Holm's (1979) step-down multiple-comparison procedure for family-wise error rate (FWER) control.
5. Formal candidate research classifications: PROMISING, INCONCLUSIVE, NOT_BETTER_THAN_BASELINE.

Traces to REQ-FR-FRF-001, ML-001 Section 5.3, and Pelagos C1.3.3.
"""

import math
from typing import List, Tuple, Dict, Any, Optional
from datetime import date
from dataclasses import dataclass, field, asdict
import numpy as np

from pelagos.forecasting.schemas import EvaluationStep

# Research Classification Constants
RESEARCH_CLASSIFICATION_PROMISING = "PROMISING"
RESEARCH_CLASSIFICATION_INCONCLUSIVE = "INCONCLUSIVE"
RESEARCH_CLASSIFICATION_NOT_BETTER = "NOT_BETTER_THAN_BASELINE"

# Thresholds for classification
PROVISIONAL_IMPROVEMENT_THRESHOLD_PCT = 5.0
SIGNIFICANCE_ALPHA = 0.05


@dataclass(frozen=True)
class StatisticalComparisonResult:
    """Structured result of statistical hypothesis testing between baseline and candidate model."""
    model_a: str
    model_b: str
    route_code: str
    horizon_months: int
    sample_size: int
    mean_loss_a: float
    mean_loss_b: float
    mean_differential: float
    percent_improvement: float
    dm_statistic: float
    hln_statistic: float
    p_value: float
    statistically_significant: bool
    favors_candidate: bool
    favors_baseline: bool
    bootstrap_ci_lower: float
    bootstrap_ci_upper: float
    ci_contains_zero: bool
    block_length: int
    bootstrap_iterations: int
    research_classification: str
    degrees_of_freedom: int = 0
    condition: Optional[str] = None
    regime_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _betacf(a: float, b: float, x: float, max_iter: int = 200, eps: float = 1e-15) -> float:
    """Continued fraction evaluation for regularized incomplete beta function (Lentz's method)."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        del_h = d * c
        h *= del_h
        if abs(del_h - 1.0) <= eps:
            break
    return h


def _ibeta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    bt = math.exp(-lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    else:
        return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def student_t_two_sided_p_value(t_stat: float, df: int) -> float:
    """
    Computes exact two-sided p-value for Student's t-distribution with df degrees of freedom.
    p = 2 * P(T >= |t|) = I_{df / (df + t^2)}(df / 2, 1 / 2).
    """
    if df <= 0:
        return 1.0
    if abs(t_stat) < 1e-12:
        return 1.0
    x = float(df) / (float(df) + float(t_stat) ** 2)
    return float(_ibeta(df / 2.0, 0.5, x))


def holm_bonferroni_step_down(p_values: List[float]) -> List[float]:
    """
    Applies Holm's (1979) step-down procedure to control Family-Wise Error Rate (FWER).
    Maintains monotonicity and preserves original p-value ordering.
    """
    m = len(p_values)
    if m <= 1:
        return list(p_values)

    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    adjusted = [0.0] * m
    running_max = 0.0

    for rank, (orig_idx, p) in enumerate(indexed):
        adj = min(1.0, (m - rank) * p)
        running_max = max(running_max, adj)
        adjusted[orig_idx] = round(running_max, 4)

    return adjusted


def calculate_loss_differentials(
    errors_a: List[float],
    errors_b: List[float],
    loss_type: str = "absolute",
) -> List[float]:
    """
    Computes step-level loss differentials: d_tau = L(e_{a, tau}) - L(e_{b, tau}).
    By convention: Model A is Persistence (baseline), Model B is Candidate.
    A positive differential (d_tau > 0) indicates Candidate achieved lower loss than Baseline.
    """
    if len(errors_a) != len(errors_b):
        raise ValueError(
            f"Error arrays must have identical length: got {len(errors_a)} vs {len(errors_b)}"
        )
    if not errors_a:
        raise ValueError("Error arrays cannot be empty")

    norm_loss = loss_type.lower().strip()
    differentials: List[float] = []

    for ea, eb in zip(errors_a, errors_b):
        if norm_loss == "absolute":
            la = abs(ea)
            lb = abs(eb)
        elif norm_loss == "squared":
            la = ea ** 2
            lb = eb ** 2
        else:
            raise ValueError(f"Unsupported loss type: '{loss_type}' (use 'absolute' or 'squared')")
        differentials.append(round(la - lb, 4))

    return differentials


def diebold_mariano_test(
    loss_differentials: List[float],
    horizon: int = 1,
) -> Tuple[float, float, float, float]:
    """
    Executes Diebold-Mariano (1995) test with Harvey-Leybourne-Newbold (HLN 1997) adjustment.
    Accounts for (h - 1) autocorrelation in out-of-sample forecast errors at horizon h.
    Evaluated strictly against the Student's t-distribution with (N - 1) degrees of freedom
    as proven by Harvey, Leybourne, and Newbold (1997).

    Returns:
      (dm_stat, hln_stat, p_value, mean_d)
    """
    N = len(loss_differentials)
    if N < 2:
        return 0.0, 0.0, 1.0, float(loss_differentials[0]) if loss_differentials else 0.0

    d_arr = np.array(loss_differentials, dtype=np.float64)
    mean_d = float(np.mean(d_arr))

    # All identical values (e.g. constant 0 difference)
    if np.all(np.isclose(d_arr, mean_d)):
        return 0.0, 0.0, 1.0, mean_d

    d_centered = d_arr - mean_d

    # Sample autocovariances up to lag h - 1
    gamma = []
    for k in range(horizon):
        if k == 0:
            cov = float(np.sum(d_centered ** 2) / N)
        else:
            cov = float(np.sum(d_centered[k:] * d_centered[:-k]) / N)
        gamma.append(cov)

    # Long-run variance with rectangular kernel
    lr_var = gamma[0] + 2.0 * sum(gamma[1:])

    # Non-positive variance fallback: Bartlett / Newey-West kernel
    if lr_var <= 0.0:
        lr_var = gamma[0] + 2.0 * sum((1.0 - k / float(horizon)) * gamma[k] for k in range(1, horizon))

    # Final guarantee of positive variance
    if lr_var <= 0.0:
        lr_var = max(gamma[0], 1e-8)

    var_mean_d = lr_var / float(N)
    dm_stat = mean_d / math.sqrt(var_mean_d) if var_mean_d > 0.0 else 0.0

    # Harvey, Leybourne, and Newbold (1997) small-sample modification factor
    hln_term = (N + 1 - 2 * horizon + (horizon * (horizon - 1)) / float(N)) / float(N)
    hln_factor = math.sqrt(max(0.0, hln_term))
    hln_stat = dm_stat * hln_factor

    # Two-sided p-value evaluated against Student's t-distribution with df = N - 1
    df = N - 1
    p_value = student_t_two_sided_p_value(hln_stat, df=df)

    return round(dm_stat, 4), round(hln_stat, 4), round(p_value, 4), round(mean_d, 4)


def moving_block_bootstrap_ci(
    loss_differentials: List[float],
    horizon: int = 1,
    block_length: Optional[int] = None,
    n_boot: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
) -> Tuple[float, float]:
    """
    Computes empirical moving block bootstrap confidence interval for mean loss differential.
    Preserves time-series serial correlation structure through overlapping blocks.

    Default block length: b = max(horizon, 3).
    Deterministic PRNG ensures exact reproducibility.
    """
    N = len(loss_differentials)
    if N < 2:
        val = float(loss_differentials[0]) if loss_differentials else 0.0
        return round(val, 4), round(val, 4)

    if block_length is None:
        block_length = max(horizon, 3)
    block_length = min(block_length, N)

    d_arr = np.array(loss_differentials, dtype=np.float64)
    rng = np.random.RandomState(seed)

    n_blocks = int(math.ceil(N / float(block_length)))
    max_start = N - block_length + 1

    boot_means: List[float] = []
    for _ in range(n_boot):
        starts = rng.randint(0, max_start, size=n_blocks)
        indices = np.concatenate([np.arange(s, s + block_length) for s in starts])[:N]
        boot_means.append(float(np.mean(d_arr[indices])))

    boot_means.sort()
    lower_idx = int((alpha / 2.0) * n_boot)
    upper_idx = int((1.0 - alpha / 2.0) * n_boot)
    ci_lower = boot_means[min(lower_idx, n_boot - 1)]
    ci_upper = boot_means[min(upper_idx, n_boot - 1)]

    return round(ci_lower, 4), round(ci_upper, 4)


def classify_candidate_performance(
    percent_improvement: float,
    p_value: float,
    ci_lower: float,
    ci_upper: float,
    mean_differential: float,
) -> str:
    """
    Applies formal Pelagos C1.3.3 research classification criteria:
    - PROMISING: >= 5% common-sample MAE improvement, statistically significant (p < 0.05),
      and 95% CI strictly positive.
    - NOT_BETTER_THAN_BASELINE: Statistically significantly worse (p < 0.05 with negative diff)
      or substantially worse (<= -5% improvement).
    - INCONCLUSIVE: Marginal difference (-5% < imp < +5%), statistically indistinguishable (p >= 0.05),
      or CI contains zero.
    """
    ci_spans_zero = (ci_lower <= 0.0 <= ci_upper)

    if percent_improvement >= PROVISIONAL_IMPROVEMENT_THRESHOLD_PCT and p_value < SIGNIFICANCE_ALPHA and ci_lower > 0.0:
        return RESEARCH_CLASSIFICATION_PROMISING

    if percent_improvement <= -PROVISIONAL_IMPROVEMENT_THRESHOLD_PCT and (p_value < SIGNIFICANCE_ALPHA or ci_upper < 0.0):
        return RESEARCH_CLASSIFICATION_NOT_BETTER

    if p_value < SIGNIFICANCE_ALPHA and mean_differential < 0.0 and ci_upper < 0.0:
        return RESEARCH_CLASSIFICATION_NOT_BETTER

    return RESEARCH_CLASSIFICATION_INCONCLUSIVE


def compare_candidate_to_baseline(
    baseline_steps: List[EvaluationStep],
    candidate_steps: List[EvaluationStep],
    model_b: str,
    route_code: str,
    horizon_months: int,
    model_a: str = "persistence",
    loss_type: str = "absolute",
    block_length: Optional[int] = None,
    n_boot: int = 1000,
    seed: int = 42,
    condition: Optional[str] = None,
    regime_id: Optional[str] = None,
) -> StatisticalComparisonResult:
    """
    Executes end-to-end common-sample statistical comparison between baseline (Model A)
    and a candidate forecasting model (Model B).
    Strictly aligns steps by matching calendar target_date.
    Evaluated with Student's t-distribution on df = N - 1 degrees of freedom.
    """
    b_map: Dict[date, EvaluationStep] = {s.target_date: s for s in baseline_steps if s.is_valid and s.actual_value is not None}
    c_map: Dict[date, EvaluationStep] = {s.target_date: s for s in candidate_steps if s.is_valid and s.actual_value is not None}

    common_dates = sorted(set(b_map.keys()).intersection(set(c_map.keys())))
    N = len(common_dates)

    if N == 0:
        return StatisticalComparisonResult(
            model_a=model_a,
            model_b=model_b,
            route_code=route_code,
            horizon_months=horizon_months,
            sample_size=0,
            mean_loss_a=0.0,
            mean_loss_b=0.0,
            mean_differential=0.0,
            percent_improvement=0.0,
            dm_statistic=0.0,
            hln_statistic=0.0,
            p_value=1.0,
            statistically_significant=False,
            favors_candidate=False,
            favors_baseline=False,
            bootstrap_ci_lower=0.0,
            bootstrap_ci_upper=0.0,
            ci_contains_zero=True,
            block_length=block_length or max(horizon_months, 3),
            bootstrap_iterations=n_boot,
            research_classification=RESEARCH_CLASSIFICATION_INCONCLUSIVE,
            degrees_of_freedom=0,
            condition=condition,
            regime_id=regime_id,
        )

    # Extract aligned actuals and predictions
    errors_a: List[float] = [b_map[d].actual_value - b_map[d].predicted_value for d in common_dates]
    errors_b: List[float] = [c_map[d].actual_value - c_map[d].predicted_value for d in common_dates]

    # Calculate losses
    if loss_type == "absolute":
        losses_a = [abs(e) for e in errors_a]
        losses_b = [abs(e) for e in errors_b]
    else:
        losses_a = [e ** 2 for e in errors_a]
        losses_b = [e ** 2 for e in errors_b]

    mean_loss_a = round(sum(losses_a) / float(N), 4)
    mean_loss_b = round(sum(losses_b) / float(N), 4)

    # Loss differentials d_tau = L(e_a) - L(e_b)
    diffs = calculate_loss_differentials(errors_a, errors_b, loss_type=loss_type)
    mean_diff = round(sum(diffs) / float(N), 4)

    # Percent improvement (positive favors candidate)
    pct_imp = round((mean_diff / mean_loss_a) * 100.0, 2) if mean_loss_a > 0.0 else 0.0

    # DM and HLN test (evaluated on Student-t with df = N - 1)
    dm_stat, hln_stat, p_val, _ = diebold_mariano_test(diffs, horizon=horizon_months)

    # Moving block bootstrap CI
    blen = block_length if block_length is not None else max(horizon_months, 3)
    ci_low, ci_high = moving_block_bootstrap_ci(
        diffs,
        horizon=horizon_months,
        block_length=blen,
        n_boot=n_boot,
        seed=seed,
    )
    ci_spans_zero = (ci_low <= 0.0 <= ci_high)

    sig = p_val < SIGNIFICANCE_ALPHA
    fav_cand = mean_diff > 0.0 and sig
    fav_base = mean_diff < 0.0 and sig

    classification = classify_candidate_performance(
        percent_improvement=pct_imp,
        p_value=p_val,
        ci_lower=ci_low,
        ci_upper=ci_high,
        mean_differential=mean_diff,
    )

    return StatisticalComparisonResult(
        model_a=model_a,
        model_b=model_b,
        route_code=route_code,
        horizon_months=horizon_months,
        sample_size=N,
        mean_loss_a=mean_loss_a,
        mean_loss_b=mean_loss_b,
        mean_differential=mean_diff,
        percent_improvement=pct_imp,
        dm_statistic=dm_stat,
        hln_statistic=hln_stat,
        p_value=p_val,
        statistically_significant=sig,
        favors_candidate=fav_cand,
        favors_baseline=fav_base,
        bootstrap_ci_lower=ci_low,
        bootstrap_ci_upper=ci_high,
        ci_contains_zero=ci_spans_zero,
        block_length=blen,
        bootstrap_iterations=n_boot,
        research_classification=classification,
        degrees_of_freedom=N - 1,
        condition=condition,
        regime_id=regime_id,
    )
