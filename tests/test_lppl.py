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
    g = CFG['lppl']
    tc_step = (g['tc_max_ahead_frac'] * 250 - g['tc_min_ahead']) / (g['tc_points'] - 1)
    m_step = (g['m_max'] - g['m_min']) / (g['m_points'] - 1)
    w_step = (g['w_max'] - g['w_min']) / (g['w_points'] - 1)

    fit = fit_window(synthetic_lppl(), grid250)
    assert fit.qualified
    assert fit.r2 > 0.999
    assert abs(fit.tc - 40.0) < 1.5 * tc_step
    assert abs(fit.m - 0.5) < 1.5 * m_step
    assert abs(fit.w - 8.0) < 1.5 * w_step
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


def synthetic_anti(n=250, tc_behind=40.0, m=0.5, w=8.0, b=-0.05, c=0.002):
    tau = tc_behind + np.arange(n)
    return 5.0 + b * tau ** m + c * tau ** m * np.cos(w * np.log(tau))


def test_mirror_grid_recovers_synthetic_antibubble(grid250):
    g = CFG['lppl']
    tc_step = (g['tc_max_ahead_frac'] * 250 - g['tc_min_ahead']) / (g['tc_points'] - 1)
    anti = WindowGrid(250, CFG, mirror=True)
    fit = fit_window(synthetic_anti(), anti)
    assert fit.qualified
    assert fit.r2 > 0.999
    assert abs(fit.tc - 40.0) < 1.5 * tc_step
    # the same decaying series must NOT qualify on the bubble grid
    assert not fit_window(synthetic_anti(), grid250).qualified
    # and a rising bubble series must not qualify on the mirror grid
    assert not fit_window(synthetic_lppl(), anti).qualified


def test_prescreen_anti_requires_deep_established_decline():
    from lppl import prescreen_anti
    n = 400
    rising = np.linspace(100, 150, n)
    assert not prescreen_anti(rising, n - 1, CFG)
    shallow = np.concatenate([np.full(200, 100.0), np.full(200, 90.0)])
    assert not prescreen_anti(shallow, n - 1, CFG)  # only -10% from high
    deep = np.concatenate([np.full(200, 100.0), np.linspace(100, 60, 200)])
    assert prescreen_anti(deep, n - 1, CFG)


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


def test_watchlist_exemption_evaluates_consolidations(tmp_path, monkeypatch):
    """A ticker with a votes>=1 evaluation in the trailing watchlist_days
    keeps being evaluated on refit days even where the pre-screen fails
    (consolidations after a first bubble leg); with the exemption off those
    days produce no evaluation rows."""
    import pandas as pd

    import lppl_detect

    log_p = synthetic_lppl(n=250)
    closes = np.exp(np.concatenate([log_p, np.full(200, log_p[-1])]))
    idx = pd.bdate_range('2015-01-02', periods=len(closes))
    path = tmp_path / 'FAKE.parquet'
    pd.DataFrame({'close': closes}, index=idx).to_parquet(path)

    def cfg_with(days):
        return {**CFG, 'lppl': {**CFG['lppl'], 'watchlist_days': days}}

    monkeypatch.setattr(lppl_detect, 'load_config', lambda: cfg_with(126))
    rows, _, _, _ = lppl_detect.detect_ticker(str(path))
    rows = pd.DataFrame(rows)
    assert (rows.loc[~rows['exempt'], 'votes'] >= 1).any()  # bubble phase fires
    flat_exempt = rows[(rows['i_local'] >= 255) & rows['exempt']]
    assert len(flat_exempt) > 0  # consolidation days still evaluated

    monkeypatch.setattr(lppl_detect, 'load_config', lambda: cfg_with(0))
    rows0, _, _, _ = lppl_detect.detect_ticker(str(path))
    evaluated0 = set(pd.DataFrame(rows0)['i_local']) if rows0 else set()
    assert not set(flat_exempt['i_local']) & evaluated0  # off = gaps return


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
