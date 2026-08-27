"""Minervini Stage-2 breakout signal (MINERVINI_SPEC.md v2, pre-registered).

Two parts, both mechanical, all constants frozen in the `minervini:` block
of config.yaml:

  1. Trend template — nine conditions saying the stock is in a Stage-2
     uptrend (1-8 per stock; 9, relative strength, is a universe-level
     membership filter supplied by the caller). Unchanged from v1.
  2. The base — a run of progressively shallower pullbacks under a rim,
     with volume drying up, bought with a buy stop just over the top of
     the last pullback.

Everything a day-i decision uses is measured on data up to and including
day i. The entry fills intraday on the trigger day at a resting buy stop;
the volume verdict is taken at that day's close, so no decision uses a
price it could not have known.

v1 (rejected) is the cautionary tale: it defined the pivot as a 60-day
high, demanded a 4-week base, measured volume dry-up as a 10-day MEAN
against a 50-day mean, tested "contraction" as a std ordering over fixed
calendar blocks, and filled at the next open. All four deviate from the
source method, and together they fired 202 times in 21 years over 1,496
names while missing both acceptance cases. See FINDINGS.md.

Interpretation choices, where the spec left room (declared, not tuned):

- 52-week high/low are the max/min of the last 250 trading-day CLOSES,
  the convention `screener.py` already uses.
- The zigzag runs on closes. Where an extreme is printed on several days
  the LAST one is taken, so the base rim and `day(B)` agree.
- A swing high is confirmed once price closes `zigzag_threshold` below
  it; a swing low once price closes the same distance above it. Only
  confirmed swings are ever used, which is what makes the structure
  causal and what the spec means by "the final trough must be confirmed".
- A base needs a full `base_lookback` of history (325 days), ~3 months
  more than the trend template itself needs. New listings are simply not
  evaluated until then.
- Dry-up compares the last `dryup_window` days' volume against ONE
  reference, today's 50-day mean.
- The trigger uses the pivot as published on the previous day's
  watchlist, which is the price a resting order would have carried.
"""

import numpy as np
import pandas as pd


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


def zigzag(close: np.ndarray, threshold: float) -> dict:
    """Confirmed alternating swing highs and lows on closes.

    A running high becomes a confirmed swing high on the day price closes
    `threshold` below it, and symmetrically for lows. Returns the swing
    index, price, kind (+1 high, -1 low) and the day each swing was
    confirmed — nothing here is visible before its confirmation day."""
    c = np.asarray(close, dtype=float)
    n = len(c)
    idx, price, kind, confirm = [], [], [], []
    start = int(np.argmax(np.isfinite(c))) if np.isfinite(c).any() else n
    if start >= n:
        return {'idx': np.array([], dtype=np.int64),
                'price': np.array([]), 'kind': np.array([], dtype=np.int8),
                'confirm': np.array([], dtype=np.int64)}
    direction = 0
    hi = lo = c[start]
    hi_i = lo_i = start
    for i in range(start + 1, n):
        x = c[i]
        if not np.isfinite(x):
            continue
        if direction >= 0 and x >= hi:
            hi, hi_i = x, i
        if direction <= 0 and x <= lo:
            lo, lo_i = x, i
        if direction >= 0 and x <= hi * (1.0 - threshold):
            idx.append(hi_i), price.append(hi), kind.append(1), confirm.append(i)
            direction = -1
            lo, lo_i = x, i
        elif direction <= 0 and x >= lo * (1.0 + threshold):
            idx.append(lo_i), price.append(lo), kind.append(-1), confirm.append(i)
            direction = 1
            hi, hi_i = x, i
    return {'idx': np.array(idx, dtype=np.int64), 'price': np.array(price),
            'kind': np.array(kind, dtype=np.int8),
            'confirm': np.array(confirm, dtype=np.int64)}


def anchor_base(close: np.ndarray, i: int, cfg: dict) -> tuple[float, int] | None:
    """The base rim (B, day(B)) as seen on day i.

    B starts as the highest close of the trailing `base_lookback` days,
    taken at the last day it printed. If price fell more than
    `max_correction` below it since, that peak belonged to a previous
    cycle: re-anchor to the highest close since the trough and try
    again. Returns None when no rim survives."""
    m = cfg['minervini']
    lb = m['base_lookback']
    if i < lb - 1:
        return None
    a = i - lb + 1
    seg = close[a:i + 1]
    if not np.isfinite(seg).all():
        return None
    b_val = float(seg.max())
    b_i = a + len(seg) - 1 - int(seg[::-1].argmax())
    floor = 1.0 - m['max_correction']
    while True:
        if b_i >= i:
            return None                      # the rim is today: no base yet
        after = close[b_i:i + 1]
        lo_i = b_i + int(after.argmin())
        if float(after.min()) >= floor * b_val:
            return b_val, b_i
        if lo_i >= i:
            return None
        seg2 = close[lo_i + 1:i + 1]
        b_val = float(seg2.max())
        b_i = lo_i + 1 + len(seg2) - 1 - int(seg2[::-1].argmax())


def _base_day(close: np.ndarray, i: int, zz: dict, depth: np.ndarray,
              hi_idx: np.ndarray, cfg: dict) -> tuple[float, int, int] | None:
    """Full base test for one day: rim, age, contractions, pivot.

    Returns (pivot, base age, number of contractions) or None."""
    m = cfg['minervini']
    anc = anchor_base(close, i, cfg)
    if anc is None:
        return None
    b_val, b_i = anc
    age = i - b_i
    if not (m['base_age_min'] <= age <= m['base_age_max']):
        return None

    n_conf = int(np.searchsorted(zz['confirm'], i, side='right'))
    last = n_conf - 1
    if last < 1 or zz['kind'][last] != -1 or zz['kind'][last - 1] != 1:
        return None                          # need a confirmed final trough
    pivot = float(zz['price'][last - 1])
    if pivot < m['pivot_min_of_base'] * b_val:
        return None                          # coil collapsed away from the rim
    if close[i] >= pivot:
        return None                          # already through: not a setup

    depths = []
    s = last
    while s >= 1 and hi_idx[s] >= b_i:
        depths.append(depth[s])
        s -= 2
    k = len(depths)
    if k < m['min_contractions'] or depths[0] > m['final_contraction_max']:
        return None
    # depths[0] is the newest: chronological order must strictly decrease
    for a_, b_ in zip(depths, depths[1:]):
        if not a_ < b_:
            return None
    return pivot, age, k


def signals(bars: dict, cfg: dict, rs_ok: np.ndarray | None = None,
            liquid: np.ndarray | None = None) -> dict:
    """Per-day signal state for one stock.

    `bars` needs 'open', 'high', 'close', 'volume' arrays on one calendar.

    template — trend template (conditions 1-9 when `rs_ok` is given)
    setup    — template + a complete base, pivot not yet cleared: the
               watchlist state, with `pivot` the published trigger price
    trigger  — a resting buy stop at pivot x (1 + buy_stop_offset) was
               touched, and the fill was not extended past the chase
               guard. `fill_px` is the price paid, THAT day, intraday.
    vol_ok   — the breakout-day volume confirmation, read at the close;
               a trigger without it is a failed breakout for the caller
               to eject.
    """
    m = cfg['minervini']
    close = np.asarray(bars['close'], dtype=float)
    high = np.asarray(bars['high'], dtype=float)
    op = np.asarray(bars['open'], dtype=float)
    volume = np.asarray(bars['volume'], dtype=float)
    n = len(close)

    tmpl = trend_template(close, cfg, rs_ok)
    ok = np.asarray(liquid, dtype=bool) if liquid is not None \
        else np.ones(n, dtype=bool)

    v = pd.Series(volume)
    v_long = v.rolling(m['dryup_long']).mean().to_numpy()
    quiet = v.rolling(m['dryup_window']).min().to_numpy()
    dryup = quiet <= m['dryup_max_ratio'] * v_long
    vol_ok = volume >= m['breakout_volume_mult'] * v_long

    zz = zigzag(close, m['zigzag_threshold'])
    depth = np.zeros(len(zz['idx']))
    hi_idx = np.full(len(zz['idx']), -1, dtype=np.int64)
    for s in range(1, len(zz['idx'])):
        if zz['kind'][s] == -1 and zz['kind'][s - 1] == 1:
            depth[s] = (zz['price'][s - 1] - zz['price'][s]) / zz['price'][s - 1]
            hi_idx[s] = zz['idx'][s - 1]

    pivot = np.full(n, np.nan)
    base_age = np.full(n, -1, dtype=np.int64)
    n_contractions = np.zeros(n, dtype=np.int8)
    setup = np.zeros(n, dtype=bool)
    cand = np.flatnonzero(tmpl & ok & dryup & np.isfinite(close))
    for i in cand:
        if i < m['base_lookback'] - 1:
            continue
        res = _base_day(close, int(i), zz, depth, hi_idx, cfg)
        if res is None:
            continue
        pivot[i], base_age[i], n_contractions[i] = res
        setup[i] = True

    prev_setup = np.concatenate(([False], setup[:-1]))
    prev_pivot = np.concatenate(([np.nan], pivot[:-1]))
    stop_px = prev_pivot * (1.0 + m['buy_stop_offset'])
    fill_px = np.where(np.isfinite(op), np.maximum(op, stop_px), stop_px)
    touched = np.isfinite(high) & (high >= stop_px)
    affordable = fill_px <= prev_pivot * (1.0 + m['max_chase'])
    trigger = prev_setup & touched & affordable & ok

    return {'template': tmpl, 'setup': setup, 'pivot': pivot,
            'base_age': base_age, 'n_contractions': n_contractions,
            'dryup': dryup, 'trigger': trigger,
            'fill_px': np.where(trigger, fill_px, np.nan),
            'stop_px': stop_px, 'vol_ok': vol_ok, 'zigzag': zz}
