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


def yoy_growth(q: np.ndarray, k: int) -> float:
    """Year-on-year growth of the k-th most recent quarter (k=1 is the
    latest), scaled by the ABSOLUTE year-ago value so a swing from a loss
    to a profit is measured rather than dropped: -1.00 -> +0.50 scores
    +150%. NaN when the year-ago quarter is exactly zero."""
    prior = q[-k - 4]
    if prior == 0:
        return np.nan
    return (q[-k] - prior) / abs(prior)


def eps_gate(report_dates: np.ndarray, eps: np.ndarray,
             calendar: pd.DatetimeIndex, cfg: dict) -> np.ndarray:
    """SEPA pillar 2, the EPS leg of Code 33 (MINERVINI_SPEC.md 8b).

    Per calendar day, using only reports dated on or before that day:

      F0  at least `min_quarters` reports
      F1  the MOST RECENT quarter is profitable and grew at least
          `quarter_growth_min` year on year (not a TTM average)
      F2  the growth rate rose in each of the last `accel_quarters`
          comparisons: g1 > g2 > g3 > g4, strictly
      F3  that latest report is no older than `max_report_age_days`

    The verdict only changes when a new report lands, so it is evaluated
    once per report and broadcast onto the calendar.
    """
    f = cfg['minervini_fundamentals']
    n_days = len(calendar)
    out = np.zeros(n_days, dtype=bool)
    q_all = np.asarray(eps, dtype=float)
    n = len(q_all)
    if n < f['min_quarters']:
        return out

    def verdict(k: int) -> bool:
        """Using the first k reports (q_all[:k]) as everything known."""
        if k < f['min_quarters']:
            return False
        q = q_all[:k]
        if not q[-1] > 0:                      # F1: a shrinking loss is not growth
            return False
        g = [yoy_growth(q, j + 1) for j in range(f['accel_quarters'] + 1)]
        if not np.isfinite(g).all():
            return False
        if g[0] < f['quarter_growth_min']:
            return False
        return all(a > b for a, b in zip(g, g[1:]))   # F2: strictly rising

    passes = np.array([verdict(k) for k in range(n + 1)])
    known = np.searchsorted(report_dates, calendar.to_numpy(), side='right')
    fresh = np.zeros(n_days, dtype=bool)
    has = known > 0
    if has.any():
        age = (calendar.to_numpy()[has]
               - report_dates[known[has] - 1]).astype('timedelta64[D]')
        fresh[has] = age.astype(int) <= f['max_report_age_days']
    return passes[known] & fresh


def group_strength(rs: np.ndarray, groups: np.ndarray, cfg: dict
                   ) -> np.ndarray:
    """Industry-group percentile per (day, ticker) -- MINERVINI_SPEC.md 16.

    `rs` is the (days x tickers) trailing-return matrix the trend
    template's condition 9 already uses; `groups` is one integer group id
    per ticker, -1 for unclassified. A group's strength on a day is the
    MEDIAN trailing return of its members with a finite value that day,
    computed only where at least `group_min_members` members qualify --
    a median over three names is not a group reading. The result is that
    group's rank among all ranked groups that day, scaled to [0, 1],
    higher being stronger; NaN where the group is unranked.
    """
    f = cfg['minervini']
    n, k = rs.shape
    gids = np.unique(groups[groups >= 0])
    med = np.full((n, len(gids)), np.nan)
    for c, g in enumerate(gids):
        members = np.flatnonzero(groups == g)
        if len(members) < f['group_min_members']:
            continue
        block = rs[:, members]
        enough = np.isfinite(block).sum(axis=1) >= f['group_min_members']
        with np.errstate(invalid='ignore'):
            m = np.nanmedian(np.where(np.isfinite(block), block, np.nan),
                             axis=1)
        med[enough, c] = m[enough]
    pct = pd.DataFrame(med).rank(axis=1, pct=True).to_numpy()
    out = np.full((n, k), np.nan)
    col = {g: c for c, g in enumerate(gids)}
    for j in range(k):
        c = col.get(groups[j], -1)
        if c >= 0:
            out[:, j] = pct[:, c]
    return out


def code33_legs(filed: np.ndarray, revenue: np.ndarray,
                net_income: np.ndarray, calendar: pd.DatetimeIndex,
                cfg: dict) -> np.ndarray:
    """The sales and margin legs of Code 33 (MINERVINI_SPEC.md 15).

    Returns, per calendar day, how many of the two legs hold (0, 1 or 2)
    using only facts FILED on or before that day -- `filed`, not the
    period end, is what makes this causal.

      C1  s1 >= sales_growth_min          (the source's >15%)
      C2  s1 > s2 > s3 > s4               sales growth accelerating
      C3  m1 > m2 > m3 > m4               net margin expanding
      C5  the latest filing within max_report_age_days

    C1+C2 together are the sales leg; C3 is the margin leg. Both require
    C0 (>= min_quarters) and C5; a name that fails those scores 0, which
    is the same treatment section 8c gives a missing surprise figure.
    """
    f = cfg['minervini_fundamentals']
    n_days = len(calendar)
    out = np.zeros(n_days, dtype=np.int8)
    rev = np.asarray(revenue, dtype=float)
    ni = np.asarray(net_income, dtype=float)
    n = len(rev)
    if n < f['min_quarters']:
        return out

    with np.errstate(invalid='ignore', divide='ignore'):
        margin = np.where(rev != 0, ni / rev, np.nan)

    def legs(k: int) -> int:
        """Using the first k filings as everything known."""
        if k < f['min_quarters']:
            return 0
        r, m = rev[:k], margin[:k]
        score = 0
        g = [yoy_growth(r, j + 1) for j in range(f['accel_quarters'] + 1)]
        if (np.isfinite(g).all() and g[0] >= f['sales_growth_min']
                and all(a > b for a, b in zip(g, g[1:]))):
            score += 1                                    # C1 + C2
        mm = [m[-j - 1] for j in range(f['accel_quarters'] + 1)]
        if (np.isfinite(mm).all()
                and all(a > b for a, b in zip(mm, mm[1:]))):
            score += 1                                    # C3
        return score

    scored = np.array([legs(k) for k in range(n + 1)], dtype=np.int8)
    known = np.searchsorted(filed, calendar.to_numpy(), side='right')
    fresh = np.zeros(n_days, dtype=bool)
    has = known > 0
    if has.any():
        age = (calendar.to_numpy()[has]
               - filed[known[has] - 1]).astype('timedelta64[D]')
        fresh[has] = age.astype(int) <= f['max_report_age_days']
    return np.where(fresh, scored[known], 0).astype(np.int8)


def beat_gate(report_dates: np.ndarray, surprise_pct: np.ndarray,
              calendar: pd.DatetimeIndex, cfg: dict) -> np.ndarray:
    """SEPA catalyst leg (MINERVINI_SPEC.md 8c): did the most recent
    report on or before each day beat consensus?

    A report with no surprise figure fails rather than passing by
    default, and the same `max_report_age_days` staleness rule applies as
    for the EPS gate."""
    f = cfg['minervini_fundamentals']
    n_days = len(calendar)
    out = np.zeros(n_days, dtype=bool)
    sp = np.asarray(surprise_pct, dtype=float)
    if not len(sp):
        return out
    beat = np.concatenate(([False], np.isfinite(sp) & (sp > 0)))
    known = np.searchsorted(report_dates, calendar.to_numpy(), side='right')
    has = known > 0
    fresh = np.zeros(n_days, dtype=bool)
    if has.any():
        age = (calendar.to_numpy()[has]
               - report_dates[known[has] - 1]).astype('timedelta64[D]')
        fresh[has] = age.astype(int) <= f['max_report_age_days']
    return beat[known] & fresh


def report_within(report_dates: np.ndarray, calendar: pd.DatetimeIndex,
                  days: int) -> np.ndarray:
    """Per calendar day: is the NEXT known report within `days` calendar
    days (today included)? Days past the last known report are clear —
    absence of a calendar is not evidence of an imminent report."""
    cal = calendar.to_numpy()
    nxt = np.searchsorted(report_dates, cal, side='left')
    out = np.zeros(len(cal), dtype=bool)
    has = nxt < len(report_dates)
    if has.any():
        gap = (report_dates[nxt[has]] - cal[has]).astype('timedelta64[D]')
        out[has] = gap.astype(int) <= days
    return out


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

    depths, lows = [], []
    s = last
    while s >= 1 and hi_idx[s] >= b_i:
        depths.append(depth[s])
        lows.append(float(zz['price'][s]))
        s -= 2
    k = len(depths)
    if k < m['min_contractions'] or depths[0] > m['final_contraction_max']:
        return None
    # depths[0] is the newest: chronological order must strictly decrease
    for a_, b_ in zip(depths, depths[1:]):
        if not a_ < b_:
            return None
    if m.get('require_higher_lows'):
        # v3: ascending bottoms — a base undercutting a prior low is
        # distribution, not contraction (lows[0] is the newest)
        for a_, b_ in zip(lows, lows[1:]):
            if not a_ > b_:
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

    # Third fill convention, the only one daily bars can express without
    # inventing something: judge price AND volume together at the close and
    # buy market-on-close. v1 chased the next open; v2's buy stop fills
    # before the volume is knowable and has to eject. This one asks both
    # questions at the same moment, with no future information.
    above_prev = close > prev_pivot
    trigger_moc = (prev_setup & ok & above_prev & vol_ok
                   & (close <= prev_pivot * (1.0 + m['max_chase'])))

    return {'template': tmpl, 'setup': setup, 'pivot': pivot,
            'base_age': base_age, 'n_contractions': n_contractions,
            'dryup': dryup, 'trigger': trigger,
            'fill_px': np.where(trigger, fill_px, np.nan),
            'stop_px': stop_px, 'vol_ok': vol_ok, 'zigzag': zz,
            'trigger_moc': trigger_moc,
            'fill_moc': np.where(trigger_moc, close, np.nan)}


def rs_line_at_high(close: np.ndarray, spy_close: np.ndarray,
                    window: int = 250) -> np.ndarray:
    """Anticipating leadership (spec 10.2): is the stock/SPY ratio at its
    `window`-day high today? True while the RATIO leads even if the price
    itself still sits under the pivot."""
    with np.errstate(invalid='ignore', divide='ignore'):
        r = np.asarray(close, dtype=float) / np.asarray(spy_close, dtype=float)
    hi = pd.Series(r).rolling(window, min_periods=window).max().to_numpy()
    return np.isfinite(r) & np.isfinite(hi) & (r >= hi)


def weak_day_score(close: np.ndarray, spy_close: np.ndarray,
                   base_age: np.ndarray) -> np.ndarray:
    """Holds-up-when-weak (spec 10.2): the stock's average daily return on
    the SPY down-days inside its own base, per day. NaN without a base or
    without any SPY down-day in it. Leaders fall least, so higher = better."""
    c = np.asarray(close, dtype=float)
    ret = np.concatenate(([np.nan], c[1:] / c[:-1] - 1.0))
    spy = np.asarray(spy_close, dtype=float)
    down = np.concatenate(([False], spy[1:] < spy[:-1]))
    x = np.where(down & np.isfinite(ret), ret, 0.0)
    cs = np.cumsum(x)
    cnt = np.cumsum(down.astype(np.int64))
    out = np.full(len(c), np.nan)
    for i in np.flatnonzero(np.asarray(base_age) > 0):
        a = i - int(base_age[i])
        n = cnt[i] - cnt[a]
        if n > 0:
            out[i] = (cs[i] - cs[a]) / n
    return out


def repertoire(bars: dict, cfg: dict, setup: np.ndarray, pivot: np.ndarray,
               tmpl: np.ndarray, blackout_clear: np.ndarray | None = None) -> dict:
    """The v5 entries (MINERVINI_SPEC.md section 11): cheat, pullback to
    the 20-day, power play. Returns per-day trigger booleans (MOC fills at
    that day's close) and a label array. The pivot breakout stays in
    `signals`."""
    m5 = cfg['minervini_v5']
    m = cfg['minervini']
    close = np.asarray(bars['close'], dtype=float)
    low = np.asarray(bars['low'], dtype=float) if 'low' in bars \
        else np.asarray(bars['open'], dtype=float)   # caller supplies low
    open_ = np.asarray(bars['open'], dtype=float)
    high = np.asarray(bars['high'], dtype=float) if 'high' in bars \
        else np.maximum(close, open_)                # §14 needs the range
    volume = np.asarray(bars['volume'], dtype=float)
    n = len(close)
    c = pd.Series(close)
    v_long = pd.Series(volume).rolling(m['dryup_long']).mean().to_numpy()
    vol_ok = volume >= m['breakout_volume_mult'] * v_long
    clear = np.ones(n, bool) if blackout_clear is None else blackout_clear

    trig = np.zeros(n, bool)
    label = np.zeros(n, dtype=np.int8)          # 1 cheat, 2 pullback, 3 power

    # cheat: yesterday a setup; today crosses the 10d pause ceiling < P
    ceil10 = c.rolling(m5['cheat_pause_days']).max().shift(1).to_numpy()
    r5 = (c.rolling(5).max() / c.rolling(5).min() - 1.0).shift(1).to_numpy()
    prev_setup = np.concatenate(([False], setup[:-1]))
    prev_pivot = np.concatenate(([np.nan], pivot[:-1]))
    cheat = (prev_setup & np.isfinite(ceil10) & (ceil10 < prev_pivot)
             & (r5 <= m5['cheat_tight']) & (close > ceil10)
             & (close <= m5['cheat_max_chase'] * ceil10) & vol_ok & clear)

    # pullback to the SMA20 after a fresh 60d-high close
    sma20 = c.rolling(m5['pb_ma']).mean().to_numpy()
    hi60 = c.rolling(m5['pb_high_window']).max().to_numpy()
    new60 = np.isfinite(hi60) & (close >= hi60 - 1e-12)
    recent = pd.Series(new60).rolling(m5['pb_recent_days']).max().shift(1) \
        .fillna(0).to_numpy().astype(bool)
    pull = (tmpl & recent & np.isfinite(sma20)
            & (low <= m5['pb_touch'] * sma20) & (close >= sma20) & clear)

    m10 = cfg.get('minervini_v10') if cfg.get('minervini_trading', {}).get(
        'strict_pullback') else None
    if m10 is not None:
        # §14: the four qualifiers 11.3 left out, each evaluated against
        # the last new 60-day-high close on or before the day -- the high
        # the pullback is a pullback FROM.
        hi_i = pd.Series(np.where(new60, np.arange(n), np.nan)).ffill()
        has_hi = np.isfinite(hi_i.to_numpy())
        hj = hi_i.fillna(0).to_numpy().astype(int)

        # P1 dry-up: the pullback's own volume, and today's, at or under
        # the 50-day mean
        with np.errstate(invalid='ignore', divide='ignore'):
            volr = volume / v_long
        cum = np.concatenate(([0.0], np.nancumsum(volr)))
        idx = np.arange(n)
        span = idx - hj
        with np.errstate(invalid='ignore'):
            pull_vol = np.where(span > 0,
                                (cum[idx + 1] - cum[hj + 1])
                                / np.maximum(span, 1), np.inf)
        p1 = (pull_vol <= m10['pb_vol_max']) & (volr <= m10['pb_vol_max'])

        # P2 depth: no more than 8% below the high it is resting from
        p2 = close >= (1.0 - m10['pb_max_depth']) * close[hj]

        # P3 hold AND bounce: an up close, in the upper half of the range
        prev_c = np.concatenate(([np.nan], close[:-1]))
        rng = high - low
        mid = np.where(rng > 0, (high + low) / 2.0, close)
        p3 = ((close > prev_c) & (close >= mid) if m10['pb_bounce']
              else np.ones(n, bool))

        # P4 the high must not sit just after a gap open
        gap = np.zeros(n, bool)
        with np.errstate(invalid='ignore'):
            gap[1:] = open_[1:] > (1.0 + m10['pb_gap_max']) * close[:-1]
        near = pd.Series(gap).rolling(m10['pb_gap_window'] + 1,
                                      min_periods=1).max().to_numpy()
        p4 = ~near[hj].astype(bool)

        pull &= has_hi & p1 & p2 & p3 & p4

    # power play: doubled 10-40d ago, tight flag, breaks the flag high
    dbl = np.zeros(n, bool)
    lb = m5['pp_lookback']
    with np.errstate(invalid='ignore'):
        dbl[lb:] = close[lb:] / close[:-lb] >= m5['pp_double']
    power = np.zeros(n, bool)
    for i in np.flatnonzero(tmpl & vol_ok & clear):
        i = int(i)
        if i < m5['pp_flag_max'] + 1:
            continue
        ps = np.flatnonzero(dbl[max(0, i - lb):i - m5['pp_flag_min'] + 1])
        if not len(ps):
            continue
        p0 = max(0, i - lb) + int(ps[-1])
        flag = close[p0:i]
        if len(flag) < m5['pp_flag_min'] or len(flag) > m5['pp_flag_max']:
            continue
        h = float(np.nanmax(flag))
        if np.nanmin(flag) >= (1 - m5['pp_max_corr']) * h and close[i] > h:
            power[i] = True

    for arr, code in ((cheat, 1), (pull, 2), (power, 3)):
        fresh = arr & ~trig
        trig |= fresh
        label[fresh] = code
    # names worth a next-day order: base setups arm the cheat; a fresh
    # 60d high arms the pullback; a recent doubling arms the power play
    dbl_any = pd.Series(dbl).rolling(lb).max().fillna(0).to_numpy().astype(bool)
    armed = (tmpl & recent) | (tmpl & dbl_any) | setup
    return {'trigger': trig, 'label': label, 'armed': armed}
