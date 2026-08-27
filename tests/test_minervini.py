"""Minervini Stage-2 breakout v2: unit tests and the spec's acceptance gate.

The synthetic escalator must trigger at its buy stop, a random walk must
essentially never trigger, the zigzag must only ever expose confirmed
swings, the chase guard must refuse a gapped entry, and no decision may
use data from after the decision day.

The SPHR / SMCI acceptance cases from MINERVINI_SPEC.md v2 are strict
xfails: the frozen v2 constants do NOT produce a trigger on either name.
The companion tests pin the measured reason in each case — for both
stocks the trend and the quiet days are present and it is the base that
never completes. See FINDINGS.md.
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

def bars_from(close, volume, high=None, open_=None) -> dict:
    close = np.asarray(close, dtype=float)
    return {'close': close, 'volume': np.asarray(volume, dtype=float),
            'high': close * 1.005 if high is None else np.asarray(high, float),
            'open': close.copy() if open_ is None else np.asarray(open_, float)}


def escalator() -> dict:
    """330 days of history topping at 100, then a two-contraction base
    (-9% then -5%, pivot 98) that dries up, then a breakout day whose
    high clears the buy stop at 98 x 1.001."""
    lead = np.concatenate([np.full(130, 20.0), np.linspace(20, 100, 200)])
    down1 = np.linspace(99, 91, 8)          # first contraction, -9%
    up1 = np.linspace(92, 98, 6)            # rally back to the pivot
    down2 = np.linspace(97, 93.1, 5)        # second contraction, -5%
    up2 = np.array([94.5, 96.2, 97.0, 97.4])  # confirms the trough, still < 98
    close = np.concatenate([lead, down1, up1, down2, up2, [101.0]])
    n = len(close)
    volume = np.concatenate([np.full(n - 24, 1_000_000.0),
                             np.full(23, 300_000.0), [3_000_000.0]])
    high = close * 1.005
    high[-1] = 101.5
    op = close.copy()
    op[-1] = 97.6                            # opens under the stop: stop fills
    return bars_from(close, volume, high, op)


def random_walks(n_series: int, n_days: int = 900) -> list:
    out = []
    for s in range(n_series):
        rng = np.random.default_rng(1000 + s)
        close = 50 * np.exp(np.cumsum(rng.normal(0, 0.02, n_days)))
        volume = np.exp(rng.normal(np.log(1_000_000), 0.4, n_days))
        out.append(bars_from(close, volume))
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
    ok = mv.rs_ok_matrix(rs, np.ones_like(rs, dtype=bool), CFG)
    assert ok.sum() == 3                                   # top 30% of ten
    assert ok[0, :3].all() and not ok[0, 3:].any()


def test_relative_strength_ignores_illiquid_names():
    rs = np.array([[0.9, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0, -0.1, -0.2, -0.3]])
    liquid = np.ones_like(rs, dtype=bool)
    liquid[0, 0] = False                 # the strongest name is illiquid
    ok = mv.rs_ok_matrix(rs, liquid, CFG)
    assert not ok[0, 0]
    assert ok[0, 1:4].all()              # ranking is over the liquid nine


# -------------------------------------------------------------------- zigzag

def test_zigzag_finds_alternating_confirmed_swings():
    close = np.concatenate([np.linspace(100, 120, 20), np.linspace(120, 108, 12),
                            np.linspace(108, 130, 22), np.linspace(130, 118, 12)])
    zz = mv.zigzag(close, M['zigzag_threshold'])
    # the series opens at its low, so the first confirmed swing is that low
    assert list(zz['kind'][:3]) == [-1, 1, -1]
    assert zz['price'][0] == pytest.approx(100.0)
    assert zz['price'][1] == pytest.approx(120.0)
    assert zz['price'][2] == pytest.approx(108.0)
    assert (np.diff(zz['kind']) != 0).all(), 'swings must alternate'


def test_zigzag_never_confirms_a_swing_before_it_happened():
    close = np.concatenate([np.linspace(100, 120, 20), np.linspace(120, 108, 12),
                            np.linspace(108, 130, 22)])
    zz = mv.zigzag(close, M['zigzag_threshold'])
    assert (zz['confirm'] > zz['idx']).all()
    assert (np.diff(zz['confirm']) > 0).all()


def test_zigzag_ignores_reversals_under_the_threshold():
    wobble = 100 + np.tile([0.0, 1.0], 60)          # 1% noise, never 3%
    assert len(mv.zigzag(wobble, M['zigzag_threshold'])['idx']) == 0


def test_anchor_reanchors_past_a_prior_cycle_collapse():
    """A 50% collapse means the old peak belongs to a previous cycle: the
    rim must move to the highest close since that trough."""
    close = np.concatenate([np.full(50, 200.0), np.linspace(200, 100, 60),
                            np.linspace(100, 150, 240)])
    peak = len(close) - 1
    # the 200 rim is 50% above the trough, so it is a prior cycle; the new
    # rim is the recovery high, which here is today -> no base yet
    assert mv.anchor_base(close, peak, CFG) is None

    close = np.concatenate([close, np.full(20, 148.0)])
    b_val, b_i = mv.anchor_base(close, len(close) - 1, CFG)
    assert b_val == pytest.approx(150.0)
    assert b_i == peak               # anchored to the recovery high, not 200


# ------------------------------------------------------------------- signals

def test_synthetic_escalator_triggers_at_its_buy_stop():
    s = mv.signals(escalator(), CFG)
    assert s['setup'][-2], 'the base must be on the watchlist the day before'
    assert s['pivot'][-2] == pytest.approx(98.0)
    assert s['n_contractions'][-2] == 2
    assert s['base_age'][-2] >= M['base_age_min']
    assert s['trigger'][-1], 'the breakout must fire'
    assert s['fill_px'][-1] == pytest.approx(98.0 * (1 + M['buy_stop_offset']))
    assert s['vol_ok'][-1], 'and it came on expanded volume'
    assert s['trigger'][:-1].sum() == 0, 'exactly one trigger in the series'


def test_a_gap_far_above_the_pivot_is_refused_not_chased():
    bars = escalator()
    bars['open'][-1] = 98.0 * 1.08          # opens 8% over the pivot
    bars['high'][-1] = 110.0
    bars['close'][-1] = 109.0
    s = mv.signals(bars, CFG)
    assert not s['trigger'][-1], 'the chase guard must refuse this fill'


def test_a_small_gap_over_the_pivot_fills_at_the_open():
    bars = escalator()
    bars['open'][-1] = 98.0 * 1.02          # inside the 5% chase guard
    s = mv.signals(bars, CFG)
    assert s['trigger'][-1]
    assert s['fill_px'][-1] == pytest.approx(98.0 * 1.02)


def test_a_breakout_that_never_reaches_the_stop_does_not_trigger():
    bars = escalator()
    bars['high'][-1] = 98.0                 # one tick short of the buy stop
    bars['close'][-1] = 97.9
    assert not mv.signals(bars, CFG)['trigger'][-1]


def test_quiet_volume_marks_the_breakout_unconfirmed():
    bars = escalator()
    bars['volume'][-1] = 400_000.0
    s = mv.signals(bars, CFG)
    assert s['trigger'][-1], 'the price entry still happens intraday'
    assert not s['vol_ok'][-1], 'but the close says the volume never showed'


def test_a_base_without_a_dry_up_day_does_not_set_up():
    bars = escalator()
    bars['volume'][:-1] = 1_000_000.0       # no quiet day anywhere
    assert not mv.signals(bars, CFG)['setup'].any()


def test_a_base_younger_than_the_minimum_does_not_set_up():
    strict = {**CFG, 'minervini': {**M, 'base_age_min': 40}}
    assert not mv.signals(escalator(), strict)['setup'].any()


def test_random_walks_essentially_never_trigger():
    series = random_walks(60)
    triggers = sum(int(mv.signals(b, CFG)['trigger'].sum()) for b in series)
    days = sum(len(b['close']) for b in series)
    assert triggers / days < 0.001, f'{triggers} triggers in {days} days'


def test_no_lookahead_in_any_signal():
    """A decision for day i must be identical whether or not the series
    continues past day i."""
    bars = escalator()
    full = mv.signals(bars, CFG)
    n = len(bars['close'])
    for i in (n - 1, n - 2, n - 3, n - 10):
        cut = mv.signals({k: v[:i + 1] for k, v in bars.items()}, CFG)
        for key in ('template', 'setup', 'trigger', 'vol_ok'):
            assert bool(cut[key][i]) == bool(full[key][i]), (key, i)
        assert cut['pivot'][i] == pytest.approx(full['pivot'][i], nan_ok=True)


# ------------------------------------------ acceptance gate (MINERVINI_SPEC v2)

def _case(ticker: str, start: str, end: str) -> dict:
    path = DATA / f'{ticker}.parquet'
    if not path.exists():
        pytest.skip(f'{ticker} not in the price cache')
    raw = pd.read_parquet(path)
    bars = {k: raw[k].to_numpy() for k in ('open', 'high', 'close', 'volume')}
    return {'raw': raw, 'signals': mv.signals(bars, CFG),
            'window': np.asarray((raw.index >= start) & (raw.index <= end))}


@pytest.mark.xfail(strict=True, reason='pre-registered acceptance case the v2 '
                   'constants do not meet: SPHR never rests long enough under '
                   'its own high for a base to complete (see FINDINGS.md)')
def test_sphr_triggers_below_100_during_the_escalator():
    c = _case('SPHR', '2025-09-01', '2026-01-31')
    close = c['raw']['close'].to_numpy()
    assert (c['window'] & c['signals']['trigger'] & (close < 100)).any()


def test_sphr_has_the_trend_and_the_quiet_days_but_never_a_base():
    """Diagnosis: the template holds and volume does dry up; what never
    happens is a rest. SPHR's longest stretch without a new all-time-high
    close in the escalator window is 16 trading days — barely the 3-week
    minimum, and never with a completed two-contraction base under it."""
    c = _case('SPHR', '2025-09-01', '2026-01-31')
    s, w = c['signals'], c['window']
    assert (s['template'] & s['dryup'])[w].sum() > 40
    assert not s['setup'][w].any()

    close = c['raw']['close'].to_numpy()
    new_high = close >= np.maximum.accumulate(close) - 1e-12
    idx = np.flatnonzero(w)
    highs = [i for i in idx if new_high[i]]
    longest = int(np.diff([idx[0] - 1] + highs + [idx[-1]]).max())
    assert longest <= 20, f'longest rest was {longest} days'


@pytest.mark.xfail(strict=True, reason='pre-registered acceptance case the v2 '
                   'constants do not meet: SMCI gaps over its pivots instead '
                   'of clearing them (see FINDINGS.md)')
def test_smci_triggers_in_the_amended_window():
    c = _case('SMCI', '2023-06-01', '2024-01-31')
    assert (c['window'] & c['signals']['trigger']).any()


def test_smci_has_the_trend_and_the_quiet_days_but_never_a_base():
    """Diagnosis: same shape as SPHR. Trend and dry-up are both present on
    more than 40 days of the amended window; the base never completes,
    because SMCI spent the period gapping over its own swing highs."""
    c = _case('SMCI', '2023-06-01', '2024-01-31')
    s, w = c['signals'], c['window']
    assert (s['template'] & s['dryup'])[w].sum() > 40
    assert not s['setup'][w].any()
