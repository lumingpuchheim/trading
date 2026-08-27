"""Minervini Stage-2 breakout: unit tests and the spec's acceptance cases.

The synthetic escalator must trigger, a random walk must essentially never
trigger, the pivot arithmetic must match a brute-force reference, and no
decision may use data from after the decision day.

The SPHR / SMCI acceptance cases from MINERVINI_SPEC.md are here as
strict xfails: the frozen constants do NOT produce a trigger on either
name (see the companion tests, which pin exactly which condition blocks
them, and FINDINGS.md). They flip back to passes on their own if the spec
is ever amended.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

import minervini as mv

CFG = yaml.safe_load(open(Path(__file__).parent.parent / 'config.yaml'))
M = CFG['minervini']
DATA = Path(__file__).parent.parent / 'data' / 'ohlcv'


# ---------------------------------------------------------------- synthetics

def escalator(seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """300-day advance to 100, a 40-day base that tightens and dries up,
    then a breakout to 103 on 2M shares — one clean VCP by construction."""
    rng = np.random.default_rng(seed)
    rise = np.linspace(20, 100, 300) * (1 + rng.normal(0, 0.015, 300))
    rise[-1] = 100.0
    early = np.linspace(94, 95, 30) + rng.normal(0, 0.8, 30)   # looser half
    late = np.linspace(95.8, 96.6, 10) + rng.normal(0, 0.2, 10)  # tightening
    close = np.concatenate([rise, early, late, [103.0]])
    volume = np.concatenate([np.full(300, 1_000_000.0),
                             np.full(40, 300_000.0), [2_000_000.0]])
    return close, volume


def random_walks(n_series: int, n_days: int = 800) -> list:
    out = []
    for s in range(n_series):
        rng = np.random.default_rng(1000 + s)
        close = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, n_days)))
        volume = np.exp(rng.normal(np.log(1_000_000), 0.4, n_days))
        out.append((close, volume))
    return out


# ------------------------------------------------------------ trend template

def steady_uptrend(n: int = 400) -> np.ndarray:
    return np.linspace(50, 100, n) * (1 + np.sin(np.arange(n) / 7) * 0.004)


def test_template_accepts_a_stage2_uptrend():
    assert mv.trend_template(steady_uptrend(), CFG)[-1]


def test_template_rejects_a_shallow_rise_under_the_52w_low_rule():
    close = np.linspace(90, 100, 400)
    assert not mv.trend_template(close, CFG)[-1]
    relaxed = {**CFG, 'minervini': {**M, 'min_above_52w_low': 1.0}}
    assert mv.trend_template(close, relaxed)[-1]   # only condition 7 failed


def test_template_rejects_a_close_too_far_below_the_52w_high():
    close = steady_uptrend()
    close[300] = 200.0
    assert not mv.trend_template(close, CFG)[-1]
    relaxed = {**CFG, 'minervini': {**M, 'min_of_52w_high': 0.0}}
    assert mv.trend_template(close, relaxed)[-1]   # only condition 8 failed


def test_template_rejects_a_close_below_its_sma50():
    close = steady_uptrend()
    close[-1] = 80.0
    assert not mv.trend_template(close, CFG)[-1]


def test_template_rejects_a_falling_sma200():
    """A crash 150-200 days back, then a partial recovery: price is above a
    correctly stacked set of SMAs and inside both 52-week bands, but the
    200-day average is still lower than it was 21 days ago (condition 6)."""
    close = np.concatenate([np.full(250, 150.0), np.full(50, 80.0),
                            np.linspace(80, 120, 150)])
    c = pd.Series(close)
    sma_f = c.rolling(M['sma_fast']).mean().iloc[-1]
    sma_m = c.rolling(M['sma_mid']).mean().iloc[-1]
    sma_s = c.rolling(M['sma_slow']).mean()
    assert close[-1] > sma_f > sma_m > sma_s.iloc[-1]                 # 1-5
    assert close[-1] >= M['min_above_52w_low'] * c.rolling(
        M['week52_window']).min().iloc[-1]                            # 7
    assert close[-1] >= M['min_of_52w_high'] * c.rolling(
        M['week52_window']).max().iloc[-1]                            # 8
    assert sma_s.iloc[-1] < sma_s.iloc[-1 - M['sma_slow_rising_lookback']]
    assert not mv.trend_template(close, CFG)[-1]


def test_relative_strength_selects_the_top_fraction():
    rs = np.array([[0.5, 0.4, 0.3, 0.2, 0.1, 0.0, -0.1, -0.2, -0.3, -0.4]])
    liquid = np.ones_like(rs, dtype=bool)
    ok = mv.rs_ok_matrix(rs, liquid, CFG)
    assert ok.sum() == 3                                   # top 30% of ten
    assert ok[0, :3].all() and not ok[0, 3:].any()


def test_relative_strength_ignores_illiquid_names():
    rs = np.array([[0.9, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0, -0.1, -0.2, -0.3]])
    liquid = np.ones_like(rs, dtype=bool)
    liquid[0, 0] = False                 # the strongest name is illiquid
    ok = mv.rs_ok_matrix(rs, liquid, CFG)
    assert not ok[0, 0]
    assert ok[0, 1:4].all()              # ranking is over the liquid nine


# ---------------------------------------------------------------- pivot / VCP

def test_pivot_and_age_match_a_brute_force_reference():
    close, _ = escalator()
    piv, age = mv.pivot(close, CFG)
    w, lag = M['pivot_window'], M['pivot_exclude_recent']
    for i in range(w + 5, len(close), 7):
        window = close[i - w + 1:i - lag + 1]
        assert piv[i] == pytest.approx(window.max())
        assert age[i] == i - (i - w + 1 + int(np.argmax(window)))


def test_pivot_is_undefined_before_the_window_is_complete():
    close, _ = escalator()
    piv, age = mv.pivot(close, CFG)
    first = M['pivot_window'] - 1
    assert np.isnan(piv[:first]).all() and (age[:first] == -1).all()
    assert np.isfinite(piv[first])


def test_tightness_and_dryup_read_the_spec_thresholds():
    close = np.concatenate([np.full(50, 100.0), np.full(10, 100.0)])
    close[-1] = 100.0 * (1 - M['tight_max_range']) - 0.01   # just too wide
    volume = np.full(60, 1_000_000.0)
    st = mv.vcp_state(close, volume, CFG)
    assert not st['tight'][-1]
    close[-1] = 100.0 * (1 - M['tight_max_range']) + 0.01   # just tight enough
    assert mv.vcp_state(close, volume, CFG)['tight'][-1]

    # the 50-day mean contains the dried-up days too: with 40 days at 1.0
    # and 10 at f, the rule f <= 0.75 * (40 + 10f)/50 flips at f = 0.7059
    volume[-10:] = 0.70 * 1_000_000.0
    assert mv.vcp_state(close, volume, CFG)['dryup'][-1]
    volume[-10:] = 0.71 * 1_000_000.0
    assert not mv.vcp_state(close, volume, CFG)['dryup'][-1]


# ------------------------------------------------------------------- signals

def test_synthetic_escalator_triggers_on_its_breakout_day():
    close, volume = escalator()
    s = mv.signals(close, volume, CFG)
    assert s['trigger'][-1], 'the constructed VCP breakout must fire'
    assert s['setup'][-2], 'and must have been on the watchlist the day before'
    assert s['pivot'][-1] == pytest.approx(100.0)
    assert not s['trigger'][:-1].any(), 'exactly one trigger in the series'


def test_the_breakout_needs_expanded_volume():
    close, volume = escalator()
    volume[-1] = 400_000.0               # clears the pivot on quiet volume
    assert not mv.signals(close, volume, CFG)['trigger'].any()


def test_a_base_younger_than_the_minimum_does_not_trigger():
    close, volume = escalator()
    strict = {**CFG, 'minervini': {**M, 'base_age_min': 60}}
    assert not mv.signals(close, volume, strict)['trigger'].any()


def test_random_walks_essentially_never_trigger():
    series = random_walks(60)
    triggers = sum(int(mv.signals(c, v, CFG)['trigger'].sum())
                   for c, v in series)
    days = sum(len(c) for c, _ in series)
    assert triggers / days < 0.001, f'{triggers} triggers in {days} days'


def test_no_lookahead_in_any_signal():
    """A decision for day i must be identical whether or not the series
    continues past day i."""
    close, volume = escalator()
    full = mv.signals(close, volume, CFG)
    for i in (len(close) - 1, len(close) - 2, 320, 300):
        cut = mv.signals(close[:i + 1], volume[:i + 1], CFG)
        for key in ('template', 'ready', 'setup', 'trigger'):
            assert bool(cut[key][i]) == bool(full[key][i]), (key, i)
        assert cut['pivot'][i] == pytest.approx(full['pivot'][i], nan_ok=True)


# ------------------------------------------- acceptance cases (MINERVINI_SPEC)

def _case(ticker: str, start: str, end: str) -> dict:
    path = DATA / f'{ticker}.parquet'
    if not path.exists():
        pytest.skip(f'{ticker} not in the price cache')
    raw = pd.read_parquet(path)
    raw = raw[(raw.index >= '2005-01-01')]
    s = mv.signals(raw['close'].to_numpy(), raw['volume'].to_numpy(), CFG)
    window = (raw.index >= start) & (raw.index <= end)
    return {'signals': s, 'window': window, 'close': raw['close'].to_numpy(),
            'state': mv.vcp_state(raw['close'].to_numpy(),
                                  raw['volume'].to_numpy(), CFG)}


@pytest.mark.xfail(strict=True, reason='pre-registered acceptance case that '
                   'the frozen constants do not meet: SPHR never holds a '
                   '20-day base during the escalator (see FINDINGS.md)')
def test_sphr_triggers_below_100_during_the_escalator():
    c = _case('SPHR', '2025-09-01', '2026-01-31')
    hits = c['window'] & c['signals']['trigger'] & (c['close'] < 100)
    assert hits.any()


def test_sphr_is_blocked_by_the_base_never_forming_not_by_the_template():
    """Diagnosis of the xfail above: the trend template does hold through
    the escalator (70 of the 105 days below $100). What never happens is a
    base: the stock steps to new highs faster than a pivot can age to 20
    days, and on the few days it does, volume has not dried up. The four
    VCP conditions never hold together, so there is nothing to break out
    of."""
    c = _case('SPHR', '2025-09-01', '2026-01-31')
    w = c['window'] & (c['close'] < 100)
    st = c['state']
    assert c['signals']['template'][w].sum() > 40, 'template holds for months'
    assert st['age_ok'][w].any() and st['dryup'][w].any()
    assert not (st['age_ok'] & st['dryup'])[w].any()
    assert not c['signals']['setup'][w].any()


@pytest.mark.xfail(strict=True, reason='pre-registered acceptance case that '
                   'the frozen constants do not meet: SMCI never dries up '
                   'in H1 2023 (see FINDINGS.md)')
def test_smci_triggers_in_h1_2023():
    c = _case('SMCI', '2023-01-01', '2023-06-30')
    assert (c['window'] & c['signals']['trigger']).any()


def test_smci_is_blocked_by_the_volume_dryup():
    """Diagnosis: SMCI's 10-day volume never falls to 75% of its 50-day
    mean in H1 2023 — it was accumulating on rising volume the whole way."""
    c = _case('SMCI', '2023-01-01', '2023-06-30')
    assert not c['state']['dryup'][c['window']].any()
