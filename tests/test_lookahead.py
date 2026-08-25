"""Lookahead checks on the full screener pipeline.

1. Shift test (as specified): move all data one day forward; the signals must
   move with it — same positional days, same values.
2. Truncation test (the sharper check): the decision for day i must be
   identical whether or not any data after day i exists.
"""

import numpy as np
import pandas as pd
import pytest

from screener import add_indicators, compute_signals, load_config

CFG = load_config()


def synthetic_ticker() -> pd.DataFrame:
    """300-day uptrend to ~97, then a 45-day flat base at 98.5 with range and
    volume both decaying exponentially — should produce model_pass signals."""
    rise = np.linspace(50, 97, 300)
    base = np.full(45, 98.5)
    closes = np.concatenate([rise, base])
    n = len(closes)

    range_frac = np.full(n, 0.02)
    t = np.arange(1, 46)
    range_frac[300:] = 0.02 * np.exp(-0.05 * t)
    volume = np.full(n, 1_000_000.0)
    volume[300:] = 1_000_000.0 * np.exp(-0.04 * t)

    half = closes * range_frac / 2
    idx = pd.bdate_range('2014-01-01', periods=n)
    return pd.DataFrame({
        'open': closes, 'high': closes + half, 'low': closes - half,
        'close': closes, 'volume': volume,
    }, index=idx)


def synthetic_spy(index) -> pd.DataFrame:
    """Flat market series so lambda_market is well-defined but neutral."""
    n = len(index)
    return pd.DataFrame({
        'open': np.full(n, 100.0), 'high': np.full(n, 101.0),
        'low': np.full(n, 99.0), 'close': np.full(n, 100.0),
        'volume': np.full(n, 1_000_000.0),
    }, index=index)


def spy_norm(index):
    return add_indicators(synthetic_spy(index), CFG)[['norm_range', 'norm_vol']]


@pytest.fixture(scope='module')
def signals():
    df = synthetic_ticker()
    sig = compute_signals(add_indicators(df, CFG), CFG, spy_norm(df.index))
    assert not sig.empty, 'synthetic ticker should produce signals'
    assert sig['sanity_pass'].any(), 'synthetic decay should pass the sanity filter'
    return df, sig


def test_signals_shift_with_the_data(signals):
    df, sig = signals
    shifted = df.copy()
    shifted.index = pd.bdate_range(
        df.index[0] + pd.tseries.offsets.BDay(1), periods=len(df))
    sig2 = compute_signals(add_indicators(shifted, CFG), CFG, spy_norm(shifted.index))

    pos = [df.index.get_loc(d) for d in sig['date']]
    pos2 = [shifted.index.get_loc(d) for d in sig2['date']]
    assert pos == pos2
    for col in ['base_top', 'lambda', 'p_today_range', 'r2_range', 'lambda_market']:
        np.testing.assert_allclose(sig[col].to_numpy(), sig2[col].to_numpy())


def test_decision_unchanged_when_future_data_removed(signals):
    df, sig = signals
    # cut the series right after a mid-base signal day; that day's signal
    # must come out identical without the future data
    cut_date = sig['date'].iloc[len(sig) // 2]
    p = df.index.get_loc(cut_date)
    assert p < len(df) - 1, 'pick a non-final day so the test means something'

    truncated = df.iloc[:p + 1]
    sig_t = compute_signals(add_indicators(truncated, CFG), CFG,
                            spy_norm(truncated.index))

    full = sig[sig['date'] <= cut_date].reset_index(drop=True)
    trunc = sig_t.reset_index(drop=True)
    assert list(full['date']) == list(trunc['date'])
    for col in ['base_top', 'base_len', 'lambda_range', 'lambda_vol',
                'p_today_range', 'p_today_vol', 'r2_range', 'r2_vol',
                'lambda_market']:
        np.testing.assert_allclose(full[col].to_numpy(), trunc[col].to_numpy())
    assert list(full['sanity_pass']) == list(trunc['sanity_pass'])
