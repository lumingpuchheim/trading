"""The ranker's three moving parts, each pinned where it can go wrong.

1. THE TARGET. `ln(y)/t` is easy; the split bet is not. Two capital
   streams, one of which stops consuming a slot early, and multiples that
   decompose arithmetically by capital share while rates combine by those
   same shares. The hand-built example here is the one from
   `test_bet_multiples.py` -- the same price path, priced by the same
   `price_bet` -- so the leg blend is checked against a bet whose every
   fill is already known to the cent.

2. THE CONTROL'S ENCODING. `StrengthScore` has to squeeze a four-key
   lexicographic sort into one float. If it does not, the do-nothing arm
   silently stops being the do-nothing arm, and every fitted row measured
   against it is measured against nothing.

3. THE ESTIMATOR. Ridge with alpha chosen in closed form, checked against
   sklearn's Ridge at a fixed alpha so the eigen route cannot drift away
   from the textbook solution.
"""

import numpy as np
import pytest
from sklearn.linear_model import Ridge

from bets_common import EMBARGO_DAYS, T_FLOOR, rate_target
from rankers import (inner_split, loo_ridge, strength_matrix,
                     ycv_ridge)
from test_bet_multiples import DIV, ENTRY_I, PATH, _cfg, _ledger_y, _panel


# ------------------------------------------------------------ the target

def test_an_unsplit_bet_is_the_log_multiple_over_the_days_held():
    r = rate_target([1.20], [30])
    assert r[0] == pytest.approx(np.log(1.20) / 30)


def test_the_floor_stops_a_three_day_stop_out_dominating():
    # 1.8% of bets close inside three days; without the floor a -8% stop
    # on day one is -0.083/day against a best of +0.012 and the fit sees
    # nothing else.
    fast = rate_target([0.92], [1])[0]
    assert fast == pytest.approx(np.log(0.92) / T_FLOOR)
    assert rate_target([0.92], [1], t_floor=1)[0] == pytest.approx(
        np.log(0.92)), 'the floor has to be the only thing doing that'


def test_a_split_bet_blends_the_two_legs_at_their_capital_shares():
    # banked half: +25% in 10 days. The rest: the whole bet returned 1.20,
    # so the rider returned (1.20 - 0.5*1.25) / 0.5 = 1.15 over 30 days.
    r = rate_target([1.20], [30], [0.5], [1.25], [10])[0]
    assert r == pytest.approx(0.5 * np.log(1.25) / 10
                              + 0.5 * np.log(1.15) / 30)
    # and it is NOT the same as pricing the whole bet at one rate: ending
    # the banked half's clock early is the entire point of the target
    assert r > np.log(1.20) / 30


def test_the_legs_are_a_decomposition_of_the_bet_not_a_second_opinion():
    y, f, y_half = 1.20, 0.5, 1.25
    y_rest = (y - f * y_half) / (1.0 - f)
    assert f * y_half + (1.0 - f) * y_rest == pytest.approx(y)


def test_half_frac_zero_falls_back_to_the_unsplit_rate():
    got = rate_target([1.20], [30], [0.0], [np.nan], [0])
    assert got[0] == pytest.approx(np.log(1.20) / 30)


def test_the_ledger_carries_the_legs_the_target_needs():
    """The hand-built bet of `test_bet_multiples.py`, now read as a rate.

    Entry at 100. Day 15 closes 125, which arms the +20% half-sale; the
    half fills at 130 on day 16. Day 17 closes back under the entry, so
    the breakeven exit fills at 90 on day 18. Dividends of 1.00 land on
    days 5, 16 and 18: the first two are collected by the whole position
    (a sale that fills in the morning still holds into the ex-date), the
    third only by the surviving half.
    """
    panel, _ = _panel(PATH, DIV)
    row = _ledger_y(panel, _cfg())

    assert row['half_frac'] == pytest.approx(0.5)
    assert row['half_days_held'] == 15
    assert row['days_held'] == 17
    assert row['y'] == pytest.approx(1.125)
    # the banked leg: 130 back, plus the 2.00 of dividends the whole
    # position had collected by then
    assert row['y_half'] == pytest.approx(1.32)

    f = row['half_frac']
    y_rest = (row['y'] - f * row['y_half']) / (1.0 - f)
    assert y_rest == pytest.approx(0.93)          # 90 out, 3.00 of dividends

    r = rate_target([row['y']], [row['days_held']], [f], [row['y_half']],
                    [row['half_days_held']])[0]
    assert r == pytest.approx(0.5 * np.log(1.32) / 15
                              + 0.5 * np.log(0.93) / 17)


# -------------------------------------------------- the control encoding

def _panel_for_keys(rsl, weak, rs):
    n_days, n_tick = np.shape(rsl)
    return {'rsl_hi': np.asarray(rsl), 'weak': np.asarray(weak, float),
            'rs': np.asarray(rs, float),
            'close': np.zeros((n_days, n_tick))}


def test_the_encoding_is_the_same_permutation_as_the_tuple_sort():
    rng = np.random.default_rng(0)
    n_tick = 40
    tickers = np.array([f'T{k:03d}' for k in range(n_tick)])
    for trial in range(50):
        rsl = rng.integers(0, 2, (1, n_tick))
        weak = np.where(rng.random((1, n_tick)) < 0.9, np.nan,
                        rng.integers(0, 3, (1, n_tick)).astype(float))
        rs = np.where(rng.random((1, n_tick)) < 0.2, np.nan,
                      rng.integers(0, 5, (1, n_tick)).astype(float))
        pool = [np.arange(n_tick)]
        E, _ = strength_matrix(_panel_for_keys(rsl, weak, rs), pool, 0, 0)

        def key(j):                      # simulate()'s own sort, verbatim
            return (-int(rsl[0, j]),
                    -(weak[0, j] if np.isfinite(weak[0, j]) else -np.inf),
                    -(rs[0, j] if np.isfinite(rs[0, j]) else -np.inf),
                    tickers[j])
        want = sorted(range(n_tick), key=key)
        got = sorted(range(n_tick), key=lambda j: (-E[0, j], tickers[j]))
        assert got == want, f'trial {trial}'


def test_ties_fall_through_to_the_next_key_and_then_to_the_ticker():
    # every name identical except rs: the ordering must be rs then ticker
    rsl = np.ones((1, 4), int)
    weak = np.full((1, 4), np.nan)
    rs = np.array([[1.0, 3.0, 3.0, 2.0]])
    E, _ = strength_matrix(_panel_for_keys(rsl, weak, rs), [np.arange(4)],
                           0, 0)
    tickers = np.array(['D', 'B', 'A', 'C'])
    got = sorted(range(4), key=lambda j: (-E[0, j], tickers[j]))
    assert got == [2, 1, 3, 0]         # rs 3 (A then B), rs 2, rs 1


def test_a_name_outside_the_days_pool_scores_minus_infinity():
    E, _ = strength_matrix(_panel_for_keys(np.ones((1, 3), int),
                                           np.full((1, 3), np.nan),
                                           np.zeros((1, 3))),
                           [np.array([0, 2])], 0, 0)
    assert np.isfinite(E[0, 0]) and np.isfinite(E[0, 2])
    assert E[0, 1] == -np.inf


# ----------------------------------------------------------- the estimator

def test_the_eigen_route_is_the_textbook_ridge_solution():
    rng = np.random.default_rng(1)
    for n, p in ((300, 40), (40, 300)):         # primal and dual
        X = rng.normal(size=(n, p)).astype(np.float32)
        X -= X.mean(0)
        y = X[:, 0] * 0.4 - X[:, 1] * 0.2 + rng.normal(scale=0.1, size=n)
        coef, b0, alpha, pred = loo_ridge(X, y, alphas=[7.0])
        ref = Ridge(alpha=7.0, fit_intercept=True).fit(X, y)
        assert alpha == 7.0
        assert coef == pytest.approx(ref.coef_, abs=2e-4)
        assert b0 == pytest.approx(ref.intercept_, abs=2e-4)
        assert pred == pytest.approx(ref.predict(X), abs=2e-4)


def test_the_alpha_search_prefers_more_shrinkage_on_pure_noise():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(200, 60)).astype(np.float32)
    X -= X.mean(0)
    noise = rng.normal(size=200)
    signal = X[:, 0] * 3.0 + rng.normal(scale=0.05, size=200)
    _, _, a_noise, _ = loo_ridge(X, noise)
    _, _, a_signal, _ = loo_ridge(X, signal)
    assert a_noise > a_signal


# --------------------------------- the alpha criterion (Amendment 1)

def test_the_purge_is_symmetric_and_keeps_the_year_out_of_the_inner_fit():
    when = np.arange(np.datetime64('2008-01-01'),
                     np.datetime64('2016-01-01')).astype('datetime64[D]')
    for Y in range(2009, 2015):
        out, held = inner_split(when, Y, EMBARGO_DAYS)
        assert held.sum() in (365, 366)
        assert (when[held] >= np.datetime64(f'{Y}-01-01')).all()
        assert (when[held] <= np.datetime64(f'{Y}-12-31')).all()
        # nothing the inner fit may see is within the embargo of either
        # boundary -- the acceptance condition, stated as the test
        gap = np.timedelta64(EMBARGO_DAYS, 'D')
        inner = when[~out]
        d0 = np.abs(inner - np.datetime64(f'{Y}-01-01'))
        d1 = np.abs(inner - np.datetime64(f'{Y}-12-31'))
        assert (d0 > gap).all() and (d1 > gap).all()
        assert not (held & ~out).any()


def test_the_purge_reaches_both_ways_not_just_backwards():
    when = np.array(['2010-06-01', '2011-06-01', '2013-06-01', '2014-06-01'],
                    dtype='datetime64[D]')
    out, held = inner_split(when, 2012, 400)
    # 2011 and 2013 are inside 400 days of a boundary; 2010 and 2014 are
    # not. A one-sided purge would keep 2013.
    assert list(out) == [False, True, True, False]
    assert not held.any()


def test_gram_subtraction_equals_the_directly_computed_inner_gram():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(400, 25)).astype(np.float32)
    y = rng.normal(size=400)
    out = rng.random(400) < 0.3
    G_direct = np.asarray(X[~out].T @ X[~out], dtype=np.float64)
    G_sub = (np.asarray(X.T @ X, dtype=np.float64)
             - np.asarray(X[out].T @ X[out], dtype=np.float64))
    assert G_sub == pytest.approx(G_direct, rel=1e-5, abs=1e-4)
    b_direct = np.asarray(X[~out].T @ y[~out].astype(np.float32), np.float64)
    b_sub = (np.asarray(X.T @ y.astype(np.float32), np.float64)
             - np.asarray(X[out].T @ y[out].astype(np.float32), np.float64))
    assert b_sub == pytest.approx(b_direct, rel=1e-5, abs=1e-4)


def _twins(n_years, rows_per_day, days_per_year, p, rng, start=2000):
    """Rows that come in twins: everyone trading on the same day shares a
    feature direction AND the outcome noise that direction can be used to
    memorise. This is the shape of the real ledger -- ~12 bets share each
    day's market move -- reduced to the smallest thing that has it."""
    when, X, y = [], [], []
    beta = rng.normal(size=p) / np.sqrt(p)
    for k in range(n_years):
        Y = start + k
        for d in range(days_per_year):
            day = (np.datetime64(f'{Y}-01-01')
                   + np.timedelta64(int(d * 360 / days_per_year), 'D'))
            u = rng.normal(size=p)                 # the day's direction
            g = rng.normal() * 1.5                 # the day's shared noise
            xs = u + 0.5 * rng.normal(size=(rows_per_day, p))
            when += [day] * rows_per_day
            X.append(xs)
            y.append(xs @ beta * 0.10 + g + 0.3 * rng.normal(rows_per_day))
    X = np.concatenate(X).astype(np.float32)
    return (np.array(when, dtype='datetime64[D]'), X - X.mean(0),
            np.concatenate(y))


def test_leave_one_out_is_fooled_by_the_twins_and_the_grouping_is_not():
    """The mechanism this amendment exists for, in one test.

    Leave-ONE-out hides a row and leaves its same-day twins in the
    training set, so the day's shared noise is still there to be
    recognised and the criterion under-regularises. Grouping by year
    hides a row together with every twin it has. If these two agree, the
    implementation has missed the point.
    """
    rng = np.random.default_rng(11)
    grid = np.logspace(-2, 6, 17)
    when, X, y = _twins(12, 8, 30, 120, rng)
    _, _, a_loo, _ = loo_ridge(X, y, alphas=grid)
    coef_y, b_y, a_ycv, _, used = ycv_ridge(X, y, when, alphas=grid,
                                            embargo=400, inner_min=200)
    assert len(used) >= 2
    assert a_loo < a_ycv, (a_loo, a_ycv)

    # and the grouped choice is the better one on a held-out continuation
    when2, X2, y2 = _twins(3, 8, 30, 120, rng, start=2100)
    X2 = X2.astype(np.float32)
    coef_l, b_l, _, _ = loo_ridge(X, y, alphas=[a_loo])
    err_loo = np.mean((X2 @ coef_l.astype(np.float32) + b_l - y2) ** 2)
    err_ycv = np.mean((X2 @ coef_y.astype(np.float32) + b_y - y2) ** 2)
    assert err_ycv < err_loo, (err_ycv, err_loo)


def test_a_window_with_too_few_purged_years_fits_nothing():
    rng = np.random.default_rng(12)
    when, X, y = _twins(2, 8, 30, 40, rng)      # two years, 400d purge
    assert ycv_ridge(X, y, when, embargo=400, inner_min=200) is None
