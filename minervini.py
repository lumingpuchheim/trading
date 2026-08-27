"""Minervini Stage-2 breakout signal (MINERVINI_SPEC.md, pre-registered).

Two parts, both mechanical, all constants frozen in the `minervini:` block
of config.yaml:

  1. Trend template — nine conditions that say the stock is in a Stage-2
     uptrend (conditions 1-8 are per-stock; condition 9, relative strength,
     is a universe-level membership filter supplied by the caller).
  2. VCP + pivot breakout — a base with contracting volatility, a tight
     final range and dried-up volume, bought when today's close clears the
     pivot on expanded volume.

Everything a day-i decision uses is measured on data up to and including
day i; fills happen at the next open (the caller's job).

Interpretation choices, where the spec left room (declared here, not tuned):

- 52-week high/low are the max/min of the last 250 trading-day CLOSES, the
  convention `screener.py` already uses.
- The pivot window covers ages 5..59 trading days, so the frozen base-age
  band 20..90 binds only at its lower edge — an age above 59 cannot occur
  by construction. Both bounds are still applied literally.
- A pivot's age is measured from the FIRST day that printed the high (the
  day the base's left side was set).
- "prior 20d" / "prior 40d" are the non-overlapping blocks immediately
  before the 10-day window: returns over days i-9..i, i-29..i-10 and
  i-69..i-30.
- The 50-day mean volume includes today, both in the dry-up ratio and in
  the 1.5x breakout threshold.
- Volatility contraction, tightness and dry-up are computed on the series
  as given; a caller that reindexes a stock onto the market calendar should
  forward-fill it (flat days are then genuinely flat).
- The nine template conditions are read on the trigger day, as the spec
  says. The VCP measurements (base age, contraction, tightness, dry-up)
  describe the BASE and are read on the day BEFORE the breakout, together
  with "the pivot was not yet cleared". Requiring a dried-up 10-day volume
  mean and an 8%-tight 10-day range on a day that is by definition a
  volume-expansion breakout is self-defeating: over the whole 1,496-name
  universe, 2005-2026, that reading fires 15 times in 21 years. Reading
  them on the prior day is also what the spec's simulator section implies —
  a name sits on the BLOCKED "waiting for breakout above $X" list, and the
  day it clears $X on volume is the buy — and it stops a name that simply
  stays above its pivot from re-triggering day after day.
"""

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


def trend_template(close: np.ndarray, cfg: dict,
                   rs_ok: np.ndarray | None = None) -> np.ndarray:
    """Conditions 1-8 per day; condition 9 is `rs_ok` when supplied."""
    m = cfg['minervini']
    c = pd.Series(np.asarray(close, dtype=float))
    sma_f = c.rolling(m['sma_fast']).mean()
    sma_m = c.rolling(m['sma_mid']).mean()
    sma_s = c.rolling(m['sma_slow']).mean()
    hi52 = c.rolling(m['week52_window']).max()
    lo52 = c.rolling(m['week52_window']).min()
    ok = ((c > sma_f) & (c > sma_m) & (c > sma_s)                 # 1, 2, 3
          & (sma_f > sma_m) & (sma_m > sma_s)                     # 4, 5
          & (sma_s > sma_s.shift(m['sma_slow_rising_lookback']))  # 6
          & (c >= m['min_above_52w_low'] * lo52)                  # 7
          & (c >= m['min_of_52w_high'] * hi52)                    # 8
          ).to_numpy()
    if rs_ok is not None:
        ok = ok & np.asarray(rs_ok, dtype=bool)
    return ok


def rs_return(close: np.ndarray, cfg: dict) -> np.ndarray:
    """Trailing `rs_lookback`-day return, the raw input to condition 9."""
    c = np.asarray(close, dtype=float)
    lb = cfg['minervini']['rs_lookback']
    out = np.full(len(c), np.nan)
    if len(c) > lb:
        with np.errstate(invalid='ignore', divide='ignore'):
            r = c[lb:] / c[:-lb] - 1.0
        out[lb:] = np.where(np.isfinite(r), r, np.nan)
    return out


def pivot(close: np.ndarray, cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    """Pivot high and its age in trading days, per day.

    Pivot = highest close of the trailing `pivot_window` days excluding the
    last `pivot_exclude_recent`. Age counts back to the first day that
    printed it. NaN / -1 until the window is complete."""
    m = cfg['minervini']
    c = np.asarray(close, dtype=float)
    n = len(c)
    w = m['pivot_window'] - m['pivot_exclude_recent']
    lag = m['pivot_exclude_recent']
    piv = np.full(n, np.nan)
    age = np.full(n, -1, dtype=np.int64)
    if n >= w + lag:
        start = w + lag - 1
        filled = np.where(np.isfinite(c), c, -np.inf)
        # row k spans close[k .. k+w-1] and belongs to day k + start
        sw = sliding_window_view(filled, w)[:n - start]
        vals = sw.max(axis=1)
        arg = sw.argmax(axis=1)
        piv[start:] = np.where(np.isfinite(vals), vals, np.nan)
        age[start:] = np.where(np.isfinite(vals), (w - 1 - arg) + lag, -1)
    return piv, age


def vcp_state(close: np.ndarray, volume: np.ndarray, cfg: dict) -> dict:
    """The mechanical VCP measurements: contraction, tightness, dry-up,
    pivot and its age, plus today's breakout-volume flag."""
    m = cfg['minervini']
    c = pd.Series(np.asarray(close, dtype=float))
    v = pd.Series(np.asarray(volume, dtype=float))

    r = c.pct_change()
    s_short = r.rolling(m['contraction_short']).std()
    s_mid = r.rolling(m['contraction_mid']).std().shift(m['contraction_short'])
    s_long = r.rolling(m['contraction_long']).std().shift(
        m['contraction_short'] + m['contraction_mid'])
    contract = ((s_short < s_mid) & (s_mid < s_long)).to_numpy()

    hi = c.rolling(m['tight_window']).max()
    lo = c.rolling(m['tight_window']).min()
    with np.errstate(invalid='ignore', divide='ignore'):
        tight = (((hi - lo) / hi) <= m['tight_max_range']).to_numpy()

    v_short = v.rolling(m['dryup_short']).mean()
    v_long = v.rolling(m['dryup_long']).mean()
    dryup = (v_short <= m['dryup_max_ratio'] * v_long).to_numpy()
    vol_expand = (v >= m['breakout_volume_mult'] * v_long).to_numpy()

    piv, age = pivot(close, cfg)
    age_ok = (age >= m['base_age_min']) & (age <= m['base_age_max'])
    return {'contract': contract, 'tight': tight, 'dryup': dryup,
            'vol_expand': vol_expand, 'pivot': piv, 'pivot_age': age,
            'age_ok': age_ok}


def signals(close: np.ndarray, volume: np.ndarray, cfg: dict,
            rs_ok: np.ndarray | None = None,
            liquid: np.ndarray | None = None) -> dict:
    """Per-day booleans for one stock.

    template — trend template (conditions 1-9 when `rs_ok` is given)
    setup    — template + VCP base complete, pivot NOT yet cleared: the
               "setting up" watchlist, with `pivot` as the trigger price
    trigger  — the buy signal: setup conditions plus close > pivot on
               volume >= 1.5x the 50-day mean. Fill at the NEXT open.
    """
    close = np.asarray(close, dtype=float)
    tmpl = trend_template(close, cfg, rs_ok)
    st = vcp_state(close, volume, cfg)
    ok = np.asarray(liquid, dtype=bool) if liquid is not None \
        else np.ones(len(close), dtype=bool)
    base = (st['age_ok'] & st['contract'] & st['tight'] & st['dryup']
            & np.isfinite(close) & np.isfinite(st['pivot']))
    above = close > st['pivot']
    ready = tmpl & ok & base
    setup = ready & ~above
    # the base has to have still been a base yesterday: that is what makes
    # today a breakout, and it stops a name that simply stays above its
    # pivot from re-triggering day after day
    intact = np.concatenate(([False], (base & ~above)[:-1]))
    trigger = tmpl & ok & intact & above & st['vol_expand']
    return {'template': tmpl, 'ready': ready, 'setup': setup,
            'trigger': trigger,
            **{k: st[k] for k in ('pivot', 'pivot_age', 'contract', 'tight',
                                  'dryup', 'vol_expand')}}


def rs_ok_matrix(rs: np.ndarray, liquid: np.ndarray, cfg: dict) -> np.ndarray:
    """Condition 9 across the universe: per day, is this stock's trailing
    return in the top `rs_top_fraction` of the LIQUID universe that day?

    `rs` and `liquid` are (days x tickers). Days with fewer than two liquid
    stocks with a finite return produce all-False."""
    frac = cfg['minervini']['rs_top_fraction']
    rs = np.asarray(rs, dtype=float)
    elig = np.asarray(liquid, dtype=bool) & np.isfinite(rs)
    masked = np.where(elig, rs, np.nan)
    thr = np.full(len(rs), np.nan)
    rows = np.flatnonzero(elig.sum(axis=1) >= 2)
    if len(rows):
        thr[rows] = np.nanquantile(masked[rows], 1.0 - frac, axis=1)
    return elig & (masked >= thr[:, None])
