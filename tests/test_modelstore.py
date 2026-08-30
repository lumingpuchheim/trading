"""The store may skip work. It may never change a number, and it may
never hand a block a model that saw into it.

MODEL_STORE_SPEC.md, acceptance section. The two rules that matter are
correctness rules, not conveniences:

  snap DOWN   a model whose training ended after `block_open - embargo`
              has seen data closer to the block than was asked for
  never cross a model must never have trained past the block's open,
              whatever embargo was requested -- including zero
"""

import numpy as np
import pandas as pd
import pytest

import fitcache
import modelstore

DATES = np.array(pd.date_range('2007-01-03', '2026-08-27', freq='3D'),
                 dtype='datetime64[D]')
GRID = modelstore.month_ends(DATES)


# ----------------------------------------------------------- the lookup

def test_the_grid_covers_the_record_monthly():
    assert len(GRID) == pytest.approx(235, abs=3)
    assert GRID[0] >= DATES.min()
    assert GRID[-1] <= DATES.max()
    gaps = np.diff(GRID).astype(int)
    assert gaps.min() >= 28 and gaps.max() <= 31


def test_snap_never_goes_past_what_was_asked_for():
    for want in ('2019-11-28', '2020-01-01', '2021-06-30', '2015-02-14'):
        te = modelstore.snap(GRID, np.datetime64(want))
        assert te <= np.datetime64(want), f'{want}: snapped up to {te}'


def test_snap_takes_the_latest_allowed_date():
    te = modelstore.snap(GRID, np.datetime64('2019-11-28'))
    assert str(te) == '2019-10-31'
    nxt = GRID[GRID > te][0]
    assert nxt > np.datetime64('2019-11-28'), 'a closer date was available'


def test_snap_returns_none_before_the_record_starts():
    assert modelstore.snap(GRID, np.datetime64('2000-01-01')) is None


def test_realised_embargo_is_never_shorter_than_requested():
    """The whole point of snapping down: you may get more buffer than you
    asked for, never less."""
    for Y in range(2010, 2027):
        opens = np.datetime64(f'{Y}-01-01')
        for asked in (0, 100, 200, 300, 400):
            te = modelstore.snap(GRID, opens - np.timedelta64(asked, 'D'))
            if te is None:
                continue
            got = int((opens - te).astype(int))
            assert got >= asked, f'{Y}/{asked}: realised only {got}d'
            assert got <= asked + 31, f'{Y}/{asked}: {got}d is off-grid'


def test_a_model_never_trains_into_its_own_block():
    """Correctness rule 2, at the hardest setting: embargo zero."""
    for Y in range(2010, 2027):
        opens = np.datetime64(f'{Y}-01-01')
        te = modelstore.snap(GRID, opens - np.timedelta64(0, 'D'))
        assert te < opens
        m = modelstore.train_mask(DATES, te, 3)
        assert DATES[m].max() < opens


# --------------------------------------------------------- the window

def test_lookback_bounds_the_window_at_both_ends():
    te = np.datetime64('2020-12-31')
    m = modelstore.train_mask(DATES, te, 3)
    assert DATES[m].max() <= te
    assert DATES[m].min() > te - np.timedelta64(3 * 365 + 1, 'D')


def test_no_lookback_means_everything_so_far():
    te = np.datetime64('2020-12-31')
    m = modelstore.train_mask(DATES, te, None)
    assert DATES[m].min() == DATES.min()
    assert DATES[m].max() <= te


# --------------------------------------------------------- the identity

def test_every_ingredient_changes_the_key():
    base = modelstore.key('srcA', 'ridge', '2020-12-31', 3, 100, 0.8)
    assert base != modelstore.key('srcB', 'ridge', '2020-12-31', 3, 100, 0.8)
    assert base != modelstore.key('srcA', 'shapelet', '2020-12-31', 3, 100, 0.8)
    assert base != modelstore.key('srcA', 'ridge', '2020-11-30', 3, 100, 0.8)
    assert base != modelstore.key('srcA', 'ridge', '2020-12-31', 5, 100, 0.8)
    assert base != modelstore.key('srcA', 'ridge', '2020-12-31', 3, 316, 0.8)
    assert base != modelstore.key('srcA', 'ridge', '2020-12-31', 3, 100, 0.9)


def test_two_embargoes_that_land_on_one_model_share_it():
    """This is where the free tuning comes from: 400 and 415 days before
    the same block both snap to the same month end, so the second costs
    nothing at all."""
    opens = np.datetime64('2021-01-01')
    a = modelstore.snap(GRID, opens - np.timedelta64(400, 'D'))
    b = modelstore.snap(GRID, opens - np.timedelta64(415, 'D'))
    assert a == b
    assert (modelstore.key('s', 'ridge', a, 3, 100, 0.8)
            == modelstore.key('s', 'ridge', b, 3, 100, 0.8))


# ------------------------------------------- fit, store, and reuse

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(fitcache, 'CACHE', tmp_path)
    return tmp_path


def _toy(n=1200, p=8, seed=0):
    rng = np.random.default_rng(seed)
    dates = np.array(pd.date_range('2015-01-01', periods=n, freq='D'),
                     dtype='datetime64[D]')
    feats = rng.normal(size=(n, p)).astype(np.float32)
    y = 1.0 + 0.05 * feats[:, 0] + 0.01 * rng.normal(size=n)
    return dates, feats, y


def test_a_stored_model_scores_identically_to_a_fresh_fit(store):
    dates, feats, y = _toy()
    te = np.datetime64('2017-06-30')
    rows = dates > np.datetime64('2017-07-01')
    a, hit_a = modelstore.get_or_fit('src', 'ridge', te, None, 100, 0.8,
                                     feats, y, dates, min_train=100)
    b, hit_b = modelstore.get_or_fit('src', 'ridge', te, None, 100, 0.8,
                                     feats, y, dates, min_train=100)
    assert hit_a is False and hit_b is True, 'the second call refitted'
    assert np.allclose(modelstore.apply(a, feats, rows),
                       modelstore.apply(b, feats, rows), atol=1e-5)


def test_the_label_threshold_travels_with_the_model(store):
    dates, feats, y = _toy()
    te = np.datetime64('2017-06-30')
    rec, _ = modelstore.get_or_fit('src', 'ridge', te, None, 100, 0.8,
                                   feats, y, dates, min_train=100)
    m = modelstore.train_mask(dates, te, None)
    assert float(rec['thr']) == pytest.approx(float(np.quantile(y[m], 0.8)),
                                              abs=1e-6)


def test_a_thin_window_is_refused_rather_than_fitted(store):
    dates, feats, y = _toy()
    rec, _ = modelstore.get_or_fit('src', 'ridge', np.datetime64('2015-01-05'),
                                   None, 100, 0.8, feats, y, dates,
                                   min_train=500)
    assert rec is None
