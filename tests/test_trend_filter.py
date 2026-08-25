import copy

import numpy as np
import pandas as pd

from screener import add_indicators, load_config


def make_df(closes, volume=1_000_000.0):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range('2015-01-01', periods=len(closes))
    return pd.DataFrame({
        'open': closes, 'high': closes * 1.01, 'low': closes * 0.99,
        'close': closes, 'volume': np.full(len(closes), volume),
    }, index=idx)


def test_steady_uptrend_qualifies():
    df = add_indicators(make_df(np.linspace(50, 100, 400)), load_config())
    assert bool(df['qualify'].iloc[-1])


def test_fails_when_not_enough_above_52w_low():
    # shallow rise: close is under 1.30 x 52-week low, everything else holds
    cfg = load_config()
    df = add_indicators(make_df(np.linspace(90, 100, 400)), cfg)
    assert not bool(df['trend_ok'].iloc[-1])

    relaxed = copy.deepcopy(cfg)
    relaxed['trend_filter']['min_above_52w_low'] = 1.0
    df2 = add_indicators(make_df(np.linspace(90, 100, 400)), relaxed)
    assert bool(df2['trend_ok'].iloc[-1])  # only the 52w-low condition failed


def test_fails_when_too_far_below_52w_high():
    # one old spike puts the 52-week high far above today's close
    cfg = load_config()
    closes = np.linspace(50, 100, 400)
    closes[300] = 150.0
    df = add_indicators(make_df(closes), cfg)
    assert not bool(df['trend_ok'].iloc[-1])

    relaxed = copy.deepcopy(cfg)
    relaxed['trend_filter']['min_of_52w_high'] = 0.0
    df2 = add_indicators(make_df(closes), relaxed)
    assert bool(df2['trend_ok'].iloc[-1])  # only the 52w-high condition failed


def test_fails_when_close_drops_below_sma50():
    closes = np.linspace(50, 100, 400)
    closes[-1] = 80.0
    df = add_indicators(make_df(closes), load_config())
    assert not bool(df['trend_ok'].iloc[-1])


def test_fails_liquidity_when_price_below_minimum():
    closes = np.linspace(2.5, 4.9, 400)  # trend fine, price under $5
    df = add_indicators(make_df(closes), load_config())
    assert bool(df['trend_ok'].iloc[-1])
    assert not bool(df['liquid'].iloc[-1])
    assert not bool(df['qualify'].iloc[-1])


def test_fails_liquidity_when_dollar_volume_too_low():
    df = add_indicators(make_df(np.linspace(50, 100, 400), volume=100.0),
                        load_config())
    assert bool(df['trend_ok'].iloc[-1])
    assert not bool(df['qualify'].iloc[-1])
