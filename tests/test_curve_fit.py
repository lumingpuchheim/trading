import numpy as np
import pytest

from curve_fit import fit_decay


def test_recovers_lambda_from_exact_exponential():
    lam, p0, n = 0.05, 2.0, 40
    t = np.arange(1, n + 1)
    values = p0 * np.exp(-lam * t)

    fit = fit_decay(values, min_valid_points=20)

    assert fit is not None
    assert fit.lam == pytest.approx(lam, abs=1e-10)
    assert fit.p0 == pytest.approx(p0, abs=1e-10)
    assert fit.p_today == pytest.approx(p0 * np.exp(-lam * n), abs=1e-10)
    assert fit.r2 == pytest.approx(1.0, abs=1e-10)
    assert fit.n_used == n


def test_negative_lambda_for_growing_series():
    t = np.arange(1, 41)
    values = 0.5 * np.exp(0.03 * t)  # growing, so lambda should come out negative

    fit = fit_decay(values, min_valid_points=20)

    assert fit is not None
    assert fit.lam == pytest.approx(-0.03, abs=1e-10)


def test_noisy_exponential_recovers_lambda_approximately():
    rng = np.random.default_rng(0)
    lam, p0, n = 0.04, 1.5, 40
    t = np.arange(1, n + 1)
    values = p0 * np.exp(-lam * t) * np.exp(rng.normal(0, 0.1, n))

    fit = fit_decay(values, min_valid_points=20)

    assert fit is not None
    assert fit.lam == pytest.approx(lam, abs=0.01)
    assert 0 < fit.r2 < 1


def test_drops_nonpositive_and_nonfinite_values():
    lam, p0, n = 0.05, 2.0, 40
    t = np.arange(1, n + 1)
    values = p0 * np.exp(-lam * t)
    values[5] = 0.0
    values[10] = -1.0
    values[15] = np.nan

    fit = fit_decay(values, min_valid_points=20)

    # remaining points still lie exactly on the curve, so lambda is exact
    assert fit is not None
    assert fit.n_used == n - 3
    assert fit.lam == pytest.approx(lam, abs=1e-10)


def test_returns_none_when_too_few_valid_points():
    values = np.full(40, np.nan)
    values[:10] = 1.0

    assert fit_decay(values, min_valid_points=20) is None


def test_constant_series_has_zero_lambda():
    fit = fit_decay(np.full(40, 1.3), min_valid_points=20)

    assert fit is not None
    assert fit.lam == pytest.approx(0.0, abs=1e-12)
