import numpy as np
import pytest

from lppl import WindowGrid, evaluate_day, fit_window, prescreen
from screener import load_config

CFG = load_config()


@pytest.fixture(scope='module')
def grid250():
    return WindowGrid(250, CFG)


def synthetic_lppl(n=250, tc_ahead=40.0, m=0.5, w=8.0, b=-0.05, c=0.002):
    t = np.arange(n)
    dt = (n - 1 + tc_ahead) - t
    return 5.0 + b * dt ** m + c * dt ** m * np.cos(w * np.log(dt))


def test_recovers_synthetic_lppl_within_grid_resolution(grid250):
    fit = fit_window(synthetic_lppl(), grid250)
    assert fit.qualified
    assert fit.r2 > 0.999
    assert abs(fit.tc - 40.0) < 7       # tc grid step ~6.3 days
    assert abs(fit.m - 0.5) < 0.1       # m grid step ~0.09
    assert abs(fit.w - 8.0) < 0.8       # w grid step ~0.78
    assert fit.b < 0


def test_declining_series_is_not_a_positive_bubble(grid250):
    # mirror-image (negative bubble): B comes out positive -> not qualified
    t = np.arange(250)
    dt = (249 + 40.0) - t
    y = 5.0 + 0.05 * dt ** 0.5  # falling super-exponentially INTO the end
    fit = fit_window(y, grid250)
    assert not fit.qualified


def test_pure_noise_rarely_qualifies(grid250):
    rng = np.random.default_rng(7)
    hits = sum(fit_window(np.cumsum(rng.normal(0, 0.02, 250)) + 5.0,
                          grid250).qualified for _ in range(20))
    assert hits <= 2  # random walks should almost never pass qualification


def test_prescreen_requires_accelerating_runup():
    n = 400
    flat = np.full(n, 100.0)
    assert not prescreen(flat, n - 1, CFG)

    linear = np.linspace(100, 140, n)  # +40% but decelerating in log terms
    assert not prescreen(linear, n - 1, CFG)

    accel = 100 * np.exp(0.5 * (np.arange(n) / (n - 1)) ** 2)  # accelerating
    assert prescreen(accel, n - 1, CFG)


def test_prescreen_is_necessary_for_qualification(grid250):
    """A day failing the pre-screen should (almost) never produce a bubble
    verdict — the pre-screen only skips known-outcome fits."""
    grids = [WindowGrid(n, CFG) for n in CFG['lppl']['windows']]
    rng = np.random.default_rng(11)
    checked = qualified_anyway = 0
    for k in range(15):
        drift = rng.uniform(-0.001, 0.001)
        closes = 50 * np.exp(np.cumsum(rng.normal(drift, 0.015, 600)))
        i = len(closes) - 1
        if not prescreen(closes, i, CFG):
            checked += 1
            ev = evaluate_day(np.log(closes), i, grids, CFG)
            qualified_anyway += int(ev['bubble'])
    assert checked >= 5
    assert qualified_anyway == 0


def test_evaluation_uses_only_past_data():
    """Truncation check: the verdict for day i must be identical with all
    future data removed."""
    grids = [WindowGrid(n, CFG) for n in CFG['lppl']['windows']]
    rng = np.random.default_rng(3)
    closes = 50 * np.exp(np.cumsum(rng.normal(0.002, 0.01, 600)))
    log_close = np.log(closes)
    i = 520
    full = evaluate_day(log_close, i, grids, CFG)
    trunc = evaluate_day(log_close[:i + 1], i, grids, CFG)
    assert full == trunc
