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


# ----------------------------------------------------- v3 (spec section 9)

def lower_low_base() -> dict:
    """Same escalator shape but the second contraction UNDERCUTS the first
    low (88 -> 87.3) while still being shallower in depth (12% -> 10%)."""
    lead = np.concatenate([np.full(130, 20.0), np.linspace(20, 100, 200)])
    down1 = np.linspace(99, 88, 8)            # -12% from 100
    up1 = np.linspace(89, 97, 6)
    down2 = np.linspace(96, 87.5, 5)          # -9.8% from 97, LOWER low
    up2 = np.array([90.0, 93.5, 95.5, 96.4])
    close = np.concatenate([lead, down1, up1, down2, up2, [98.0]])
    n = len(close)
    volume = np.concatenate([np.full(n - 24, 1_000_000.0),
                             np.full(23, 300_000.0), [3_000_000.0]])
    high = close * 1.005
    high[-1] = 98.5
    op = close.copy()
    op[-1] = 96.6
    return bars_from(close, volume, high, op)


def test_higher_lows_rule_rejects_an_undercutting_base():
    bars = lower_low_base()
    v2 = mv.signals(bars, CFG)
    assert v2['setup'].any(), 'v2 (depths only) accepts the undercutting base'
    v3cfg = {**CFG, 'minervini': {**M, 'require_higher_lows': True}}
    assert not mv.signals(bars, v3cfg)['setup'].any(), \
        'v3 must reject a base whose final low undercuts the prior low'


def test_higher_lows_rule_keeps_the_healthy_escalator():
    v3cfg = {**CFG, 'minervini': {**M, 'require_higher_lows': True}}
    s = mv.signals(escalator(), v3cfg)
    assert s['setup'][-2] and s['trigger'][-1], \
        'ascending-bottom bases must still trigger under v3'


def test_report_within_blackout_window():
    cal = pd.bdate_range('2024-01-01', periods=40)
    reports = np.array([np.datetime64('2024-02-01')])
    hit = mv.report_within(reports, cal, 21)
    gap = (np.datetime64('2024-02-01') - cal.to_numpy()).astype('timedelta64[D]')
    expect = (gap.astype(int) >= 0) & (gap.astype(int) <= 21)
    assert (hit == expect).all()
    assert not hit[-1], 'days after the last known report are clear'


# ---------------------------------------------------- v4 (spec section 10)

def test_rs_line_at_high_flags_the_ratio_not_the_price():
    spy = np.full(300, 100.0)
    close = np.concatenate([np.full(260, 50.0), np.linspace(50, 60, 40)])
    flag = mv.rs_line_at_high(close, spy)
    assert flag[-1], 'ratio at a 250d high must flag'
    spy2 = np.concatenate([np.full(260, 100.0), np.linspace(100, 130, 40)])
    assert not mv.rs_line_at_high(close, spy2)[-1], \
        'price up but LAGGING the market is not leadership'


def test_weak_day_score_measures_only_spy_down_days():
    spy = np.array([100, 99, 100, 98, 100, 97, 100.0])
    close = np.array([50, 50.5, 50, 50.5, 50, 50.5, 50.0])  # up on down days
    age = np.array([0, 0, 0, 0, 0, 0, 6])
    sc = mv.weak_day_score(close, spy, age)
    assert sc[-1] == pytest.approx(0.01, abs=2e-3), \
        'the score is the mean stock return on SPY down-days in the base'


# ------------------------------------------- v9 (spec section 13): selling
#
# Section 13 conditions the profit-taking on HOW the stock got there:
# a fast +20% is held whole for 40 days, a slow one still sells half at
# +20%, and a still-whole position more than 30% up sells half into the
# largest up-day of its run. These tests drive the portfolio simulator
# over a hand-built one-name panel, so every rule is isolated from the
# signal layer.

from minervini_backtest import apply_v5, apply_v9, simulate   # noqa: E402

ENTRY_I = 60          # every path below enters here, at a close of 100


def sim_panel(close) -> tuple[dict, list]:
    """A one-ticker panel plus the pool that arms exactly one entry on
    ENTRY_I. Volume is neutral (volx 1.0) so only the depth leg of the
    decisive-break rule can fire, and the market light is always green."""
    close = np.asarray(close, dtype=float)
    n = len(close)
    cal = pd.bdate_range('2020-01-02', periods=n)
    col = close.reshape(n, 1)
    panel = {
        'calendar': cal, 'tickers': ['TEST'],
        'open': col.copy(), 'close': col.copy(),
        'sma50': pd.Series(close).rolling(50).mean().to_numpy().reshape(n, 1),
        'volx': np.ones((n, 1)),
        'fill_moc': col.copy(), 'fill_px': col.copy(),
        'trigger_moc': np.zeros((n, 1), bool), 'trigger': np.zeros((n, 1), bool),
        'vol_ok': np.ones((n, 1), bool),
        'last_i': np.array([n - 1]), 'green': np.ones(n, bool),
        'spy_close': pd.Series(np.full(n, 100.0), index=cal),
        'rsl_hi': np.ones((n, 1), bool), 'weak': np.zeros((n, 1)),
        'rs': np.zeros((n, 1)),
    }
    panel['trigger_moc'][ENTRY_I, 0] = True
    panel['trigger'][ENTRY_I, 0] = True
    empty = np.array([], dtype=int)
    pool = [np.array([0]) if i == ENTRY_I - 1 else empty for i in range(n)]
    return panel, pool


def run_both(close) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The same price path under the standing config v5r and under v9."""
    panel, pool = sim_panel(close)
    out = []
    for apply in (apply_v5, apply_v9):
        cfg = apply(yaml.safe_load(
            open(Path(__file__).parent.parent / 'config.yaml')))
        cfg['minervini_trading']['reentry_fast'] = True        # v5r keeps E3
        trades, _, _, _ = simulate(panel, cfg, (0, len(close) - 1),
                                   pool_days=pool, moc=True)
        out.append(trades)
    return out[0], out[1]


def path(after) -> np.ndarray:
    """60 flat days at 100 (so the SMA50 exists), then `after` from the
    entry close onwards."""
    return np.concatenate([np.full(ENTRY_I, 100.0), np.asarray(after, float)])


def day_of(trades: pd.DataFrame, reason: str) -> int:
    """Index of the entry-relative day a given exit printed on."""
    row = trades[trades['exit_reason'] == reason].iloc[0]
    return pd.bdate_range(row['entry_date'], row['exit_date']).size - 1


def test_a_fast_20_percent_is_held_whole_instead_of_halved():
    # +4%/day for five days: +21.7% on day 5, inside the 15-day window
    run = 100.0 * 1.04 ** np.arange(6)
    close = path(np.concatenate([run, np.full(74, run[-1])]))
    v5r, v9 = run_both(close)
    assert day_of(v5r, 'strength') == 16, \
        'v5r sells half the day after the +20% close clears the 15-day window'
    assert day_of(v9, 'strength') == 41, \
        'v9 holds the fast winner whole for 40 days, then normal rules resume'
    assert (v9['exit_reason'] == 'strength').sum() == 1


def test_a_slow_20_percent_still_sells_half_on_the_old_schedule():
    # +1%/day: +20% only on day 19, so the velocity exemption never arms
    close = path(100.0 * 1.01 ** np.arange(80))
    v5r, v9 = run_both(close)
    assert day_of(v5r, 'strength') == day_of(v9, 'strength') == 20, \
        'slow winners are untouched by section 13'


def test_the_velocity_exemption_suspends_the_trend_exit_then_restores_it():
    # entered BELOW a falling SMA50, so a close above entry can still be a
    # decisive break of the line
    pre = np.linspace(150.0, 100.0, ENTRY_I)
    run = 100.0 * 1.04 ** np.arange(6)
    close = np.concatenate([pre, run, np.full(45, 108.0), np.full(40, 100.5)])
    v5r, v9 = run_both(close)
    assert day_of(v5r, 'sma') < 20, 'v5r sells the dip below the SMA50'
    assert day_of(v9, 'sma') > 40, \
        'v9 tolerates it for 40 days, then the trend exit is live again'


def test_the_stop_still_sells_inside_the_velocity_hold():
    run = 100.0 * 1.04 ** np.arange(6)          # velocity by day 5
    close = path(np.concatenate([run, [91.0], np.full(70, 91.0)]))
    _, v9 = run_both(close)
    assert list(v9['exit_reason']) == ['stop'], \
        'the 8% stop is never suspended -- it caps every loss'


def test_a_climax_day_sells_half_at_that_close():
    run = 100.0 * 1.04 ** np.arange(6)          # velocity, largest day +4%
    flat = np.full(5, run[-1])
    climax = run[-1] * 1.08                     # +8%, and above +30%
    close = path(np.concatenate([run, flat, [climax], np.full(60, climax)]))
    v5r, v9 = run_both(close)
    cx = v9[v9['exit_reason'] == 'climax_partial']
    assert len(cx) == 1 and cx.iloc[0]['exit_px'] == pytest.approx(climax), \
        'the climax partial sells at the close of the largest up-day'
    assert day_of(v9, 'climax_partial') == 11
    assert 'climax_partial' not in set(v5r['exit_reason']), \
        'v5r has no climax rule; it takes the unconditional +20% half'


def test_a_five_percent_day_that_is_not_the_run_s_largest_does_not_sell():
    # +10% on the first day after entry sets the bar; the later +6% day
    # clears +30% and the 5% threshold but is not the run's largest, so
    # nothing is sold into it
    run = np.concatenate([[100.0, 110.0], 110.0 * 1.04 ** np.arange(1, 6)])
    close = path(np.concatenate([run, [run[-1] * 1.06],
                                 np.full(30, run[-1] * 1.06)]))
    _, v9 = run_both(close)
    assert 'climax_partial' not in set(v9['exit_reason']), \
        'only the largest single-day gain since entry is a climax'


# ---------------------------- v10 (spec section 14): pullback qualifiers
#
# Section 11.3 accepted any trend-template stock whose low tagged the
# SMA20 within ten days of a 60-day high, at any depth, on any volume,
# with no bounce and no check on how the high was made. Section 14 adds
# the four conditions the source states. Each test perturbs ONE of them
# on a base case that both configurations accept, so every qualifier is
# shown to be the thing doing the rejecting.

def strict_cfg() -> dict:
    return {**CFG, 'minervini_trading': {**CFG['minervini_trading'],
                                         'strict_pullback': True}}


def pullback_case(vol_mult: float = 0.6, spike: float = 100.0,
                  tail: tuple = (99.5, 99.9), gap: bool = False,
                  weak_close: bool = False) -> dict:
    """300 rising days, ten flat at 100 (the 60-day high), then a shallow
    two-day rest whose last day tags the SMA20 and closes up and strong
    on quiet volume -- a pullback the source would recognise."""
    close = np.concatenate([np.linspace(40.0, 100.0, 300), np.full(10, 100.0),
                            list(tail)])
    close[309] = spike
    open_ = close.copy()
    high = close * 1.004
    low = close * 0.996
    volume = np.full(len(close), 1_000_000.0)
    volume[-2:] *= vol_mult
    low[-1] = close[-1] * 0.985
    high[-1] = close[-1] * (1.05 if weak_close else 1.004)
    if gap:
        open_[309] = close[308] * 1.10
    return {'close': close, 'high': high, 'low': low, 'open': open_,
            'volume': volume}


def pullback_fires(bars: dict, cfg: dict) -> bool:
    s = mv.signals(bars, cfg)
    r = mv.repertoire(bars, cfg, s['setup'], s['pivot'], s['template'])
    return bool(r['trigger'][-1] and r['label'][-1] == 2)


def test_a_clean_pullback_is_taken_by_both_configurations():
    bars = pullback_case()
    assert pullback_fires(bars, CFG)
    assert pullback_fires(bars, strict_cfg()), \
        'section 14 must not reject the pullback it was written to keep'


def test_p1_rejects_a_pullback_on_above_average_volume():
    bars = pullback_case(vol_mult=1.6)
    assert pullback_fires(bars, CFG), 'v5r had no volume condition at all'
    assert not pullback_fires(bars, strict_cfg()), \
        'an impulsive decline on rising volume is distribution, not a rest'


def test_p2_rejects_a_pullback_deeper_than_eight_percent():
    # a one-day spike to 112, then a rest at 101 -- 9.8% below the high
    bars = pullback_case(spike=112.0, tail=(100.5, 101.0))
    assert pullback_fires(bars, CFG)
    assert not pullback_fires(bars, strict_cfg())


def test_p3_rejects_a_day_that_holds_the_line_without_bouncing():
    bars = pullback_case(tail=(100.0, 99.9))      # closes DOWN on the day
    assert pullback_fires(bars, CFG)
    assert not pullback_fires(bars, strict_cfg())


def test_p3_rejects_a_close_in_the_lower_half_of_the_range():
    bars = pullback_case(weak_close=True)
    assert pullback_fires(bars, CFG)
    assert not pullback_fires(bars, strict_cfg())


def test_p4_rejects_a_high_made_by_a_gap():
    bars = pullback_case(gap=True)
    assert pullback_fires(bars, CFG)
    assert not pullback_fires(bars, strict_cfg()), \
        'a price that teleported never made the advance it is resting from'


def test_app_the_case_that_forced_section_14():
    """APP 2025-02-24: -19.5% off a gapped high in five sessions on 1.46x
    volume, bought at the close for a -20% loss two days later. Every
    section-14 qualifier rejects it; v5r took it."""
    path = DATA / 'APP.parquet'
    if not path.exists():
        pytest.skip('APP not in the price cache')
    raw = pd.read_parquet(path)
    bars = {k: raw[k].to_numpy() for k in ('open', 'high', 'low', 'close',
                                           'volume')}
    i = int(raw.index.searchsorted(pd.Timestamp('2025-02-24')))
    fired = []
    for cfg in (CFG, strict_cfg()):
        s = mv.signals(bars, cfg)
        r = mv.repertoire(bars, cfg, s['setup'], s['pivot'], s['template'])
        fired.append(bool(r['trigger'][i] and r['label'][i] == 2))
    assert fired == [True, False]


# ------------------------ §15: the sales and margin legs of Code 33
#
# Sections 8/8b built the EPS leg only, on the recorded grounds that
# sales and margins were "not obtainable". They are obtainable from SEC
# XBRL, and these tests pin the two legs the EPS gate was missing --
# including that `filed`, not the period end, is the causal date.

def quarters(rev: list, ni: list, first: str = '2020-03-31') -> tuple:
    """Quarterly revenue and net income with filings 40 days after each
    period end, which is roughly the real median lag."""
    ends = pd.date_range(first, periods=len(rev), freq='QE')
    filed = (ends + pd.Timedelta(days=40)).to_numpy()
    return filed, np.array(rev, float), np.array(ni, float)


def legs_on(day: str, rev: list, ni: list) -> int:
    filed, r, n = quarters(rev, ni)
    cal = pd.bdate_range('2020-01-01', '2026-12-31')
    out = mv.code33_legs(filed, r, n, cal, CFG)
    return int(out[cal.searchsorted(pd.Timestamp(day))])


ACCEL = [100, 100, 100, 100, 118, 122, 128, 137]     # sales YoY 18/22/28/37%
FLAT = [100, 100, 100, 100, 130, 130, 130, 130]      # 30% every quarter


def test_accelerating_sales_and_expanding_margin_score_both_legs():
    ni = [8, 8, 8, 8, 10.6, 11.6, 13.1, 15.5]        # margin 9.0 -> 11.3%
    assert legs_on('2022-03-01', ACCEL, ni) == 2


def test_flat_sales_growth_is_not_acceleration():
    ni = [8, 8, 8, 8, 10.6, 11.6, 13.1, 15.5]
    assert legs_on('2022-03-01', FLAT, ni) == 1, \
        'only the margin leg may score: 30% four times running is not rising'


def test_a_shrinking_margin_loses_the_margin_leg():
    ni = [8, 8, 8, 8, 11.8, 11.9, 12.0, 12.1]        # margin 10.0 -> 8.8%
    assert legs_on('2022-03-01', ACCEL, ni) == 1


def test_sales_growth_under_fifteen_percent_fails_the_sales_leg():
    slow = [100, 100, 100, 100, 105, 108, 111, 114]  # accelerating but < 15%
    ni = [8, 8, 8, 8, 10.6, 11.6, 13.1, 15.5]
    assert legs_on('2022-03-01', slow, ni) == 1


def test_nothing_is_known_before_the_filing_date():
    """The eighth quarter ends 2021-12-31 and is filed 40 days later. The
    legs cannot score before that filing lands, whatever the period end
    says."""
    ni = [8, 8, 8, 8, 10.6, 11.6, 13.1, 15.5]
    filed, r, n = quarters(ACCEL, ni)
    cal = pd.bdate_range('2020-01-01', '2023-12-31')
    out = mv.code33_legs(filed, r, n, cal, CFG)
    last_filed = pd.Timestamp(filed[-1])
    before = cal[cal < last_filed]
    assert out[cal.searchsorted(before[-1])] < 2, 'no lookahead past `filed`'
    assert out[cal.searchsorted(last_filed)] == 2, 'and it scores once filed'


def test_a_stale_filing_scores_nothing():
    ni = [8, 8, 8, 8, 10.6, 11.6, 13.1, 15.5]
    # ~170 days after the last filing, past max_report_age_days (120)
    assert legs_on('2022-08-01', ACCEL, ni) == 0


# --------------------------- §16: industry-group strength

def test_group_strength_ranks_groups_by_their_median_member():
    # three groups of five; group 2 strongest, group 0 weakest
    rs = np.column_stack([np.full(10, 0.05)] * 5 + [np.full(10, 0.15)] * 5
                         + [np.full(10, 0.25)] * 5)
    groups = np.array([0] * 5 + [1] * 5 + [2] * 5)
    pct = mv.group_strength(rs, groups, CFG)
    assert pct[-1, 0] < pct[-1, 5] < pct[-1, 10]
    assert pct[-1, 10] == pytest.approx(1.0), 'the best group ranks at 1.0'
    # every member of a group carries that group's percentile
    assert len(set(pct[-1, :5])) == 1


def test_one_moonshot_does_not_carry_its_group():
    """The median is the point: a group of laggards with a single huge
    winner must not rank above a uniformly strong group."""
    rs = np.column_stack([np.full(10, 0.01)] * 4 + [np.full(10, 5.0)]
                         + [np.full(10, 0.20)] * 5)
    groups = np.array([0] * 5 + [1] * 5)
    pct = mv.group_strength(rs, groups, CFG)
    assert pct[-1, 0] < pct[-1, 5], 'median, not mean'


def test_a_group_below_the_member_minimum_is_unranked():
    rs = np.column_stack([np.full(10, 0.30)] * 3 + [np.full(10, 0.10)] * 5)
    groups = np.array([0] * 3 + [1] * 5)
    pct = mv.group_strength(rs, groups, CFG)
    assert np.isnan(pct[-1, 0]), 'three members is not a group reading'
    assert np.isfinite(pct[-1, 5])


def test_unclassified_tickers_get_no_group_reading():
    rs = np.column_stack([np.full(10, 0.10)] * 5 + [np.full(10, 0.20)])
    groups = np.array([0] * 5 + [-1])
    pct = mv.group_strength(rs, groups, CFG)
    assert np.isnan(pct[-1, 5])


def test_group_strength_follows_the_members_over_time():
    """A group that is weak early and strong late must rank that way."""
    weak_then_strong = np.concatenate([np.full(5, 0.01), np.full(5, 0.40)])
    steady = np.full(10, 0.20)
    rs = np.column_stack([weak_then_strong] * 5 + [steady] * 5)
    groups = np.array([0] * 5 + [1] * 5)
    pct = mv.group_strength(rs, groups, CFG)
    assert pct[0, 0] < pct[0, 5], 'weak early'
    assert pct[-1, 0] > pct[-1, 5], 'strong late'
