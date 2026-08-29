"""The marco-hui-95 VCP definition, made causal and evaluated per day.

Source: https://github.com/marco-hui-95/vcp_screener.github.io/
(`vcp_screener.py`, function `vcp()` and its helpers). Read 2026-08-28.

WHAT THIS IS. That repo is a live watchlist screener: it asks "does this
chart look like a VCP *today*" and writes the passing tickers to a
spreadsheet. It names no entry price, no stop and no exit, and it
contains no backtest. This module ports its pattern test -- and only its
pattern test -- to a per-day boolean array so it can gate our entries.

WHAT IS PORTED (their `vcp`, verbatim in intent):

  1. swings   `argrelextrema(High, greater, order=10)` and the mirror on
              Low: a bar that is the strict extreme of its own +/- 10-bar
              window. Time-scaled, on INTRADAY extremes -- not our
              3%-confirmed close zigzag.
  2. alternation  consecutive same-kind extrema collapse to the LAST of
              each run, giving H, L, H, L, ...
  3. depths   (High[h] - Low[l]) / High[h] * 100, each swing high paired
              with the next swing low after it, in percent.
  4. count    walking newest -> oldest, count while each older
              contraction is strictly DEEPER, and stop at the first that
              is not. Anything before that break is ignored.
  5. flags    2 <= count <= 4; newest contraction <= 15%; deepest
              counted contraction <= 50%; (today - oldest counted high)
              / 5 >= 2 "weeks"; mean(volume, 5) < mean(volume, 30);
              today's High below the most recent swing high ("not
              broken out yet").

All five flags must hold. Their final `if flag_num == 1 & flag_max == 1
& ...` reads like an operator-precedence bug but Python chains it into
"all flags equal, and the last equals 1", which is the intended
all-ones test; this port states that directly.

CAUSALITY, THE ONE CHANGE THAT MATTERS. `argrelextrema(order=10)` needs
the 10 bars AFTER a bar to know it was an extreme. Run once on today's
chart that is harmless -- their newest usable swing is simply always at
least 10 bars old. Fed a backtest it is lookahead. So here an extremum
at index e becomes visible on day e + 10 and not before, which is what
their screener sees anyway. `test_vcp_marco.py` asserts the flag for day
i is unchanged when the series is truncated at i.

TWO DELIBERATE DEVIATIONS, both in `local_high_low`:

  - Their tail branch (when one extremum list outlives the other) does
    `adjusted_local_high.pop(-1)` and then appends `local_high[-1]`,
    which DROPS the swing high immediately before the final swing low
    and shifts every pairing after it. The main loop's own comment says
    the intent is only to "eliminate for consecutive highs or lows", so
    this port implements the collapse rule uniformly and does not
    reproduce the tail. Reproducing it would inherit a bug, not a method.
  - They rebuild the alternation from the whole extrema list on every
    call; this port maintains it incrementally, which is the same
    sequence and is what makes a per-day evaluation affordable.

NOT PORTED: their trend template and RS rating. Their `condition_8`
computes the RS-line slope into `slope_rs` and then tests `slope`, the
MA200 slope from condition 3, so the RS-line condition never runs; and
their RS rating is a percentile over whatever page the Finviz
performance screener returned, a different universe from the one being
screened. Our own template and condition-9 rank already do that job on
the same universe, point in time.
"""

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

ORDER = 10              # argrelextrema window, both sides
MIN_CONTRACTIONS = 2    # 2 <= count <= 4
MAX_CONTRACTIONS = 4
MIN_C_MAX = 15.0        # newest contraction, percent
MAX_C_MAX = 50.0        # deepest counted contraction, percent
MIN_WEEKS = 2.0         # (today - oldest counted high) / 5
VOL_SHORT = 5
VOL_LONG = 30
MAX_PAIRS = MAX_CONTRACTIONS + 2    # enough to see the run break


def _extrema_events(high: np.ndarray, low: np.ndarray,
                    order: int) -> list[tuple[int, int, int]]:
    """(visible_day, index, kind) for every local extreme, in the order a
    causal observer learns of them. kind is +1 for a high, -1 for a low.

    An extreme at index e is knowable only once the `order` bars after it
    exist, so it becomes visible on day e + order. NaN bars produce no
    extrema at all: every comparison against NaN is False, which is the
    behaviour their screener has around missing data too."""
    n = len(high)
    hi = argrelextrema(high, np.greater, order=order)[0]
    lo = argrelextrema(low, np.less, order=order)[0]
    ev = [(int(e) + order, int(e), 1) for e in hi if int(e) + order < n]
    ev += [(int(e) + order, int(e), -1) for e in lo if int(e) + order < n]
    ev.sort(key=lambda t: (t[0], t[1]))
    return ev


def _count_run(depths: list[float]) -> int:
    """Their `num_of_contractions`: newest first, count while strictly
    deepening as you walk back, stop at the first that is not."""
    n, prev = 0, 0.0
    for d in depths:
        if d > prev:
            n += 1
            prev = d
        else:
            break
    return n


def marco_flags(bars: dict, order: int = ORDER) -> np.ndarray:
    """Per-day boolean: does this stock show their VCP at today's close?

    `bars` needs 'high', 'low', 'volume' arrays on one calendar. Day i
    uses only information available on day i."""
    high = np.asarray(bars['high'], dtype=float)
    low = np.asarray(bars['low'], dtype=float)
    volume = np.asarray(bars['volume'], dtype=float)
    n = len(high)
    out = np.zeros(n, dtype=bool)
    if n < VOL_LONG + 1:
        return out

    v = pd.Series(volume)
    vol_dry = (v.rolling(VOL_SHORT).mean()
               < v.rolling(VOL_LONG).mean()).to_numpy()

    events = _extrema_events(high, low, order)
    # the alternating swing sequence as known so far: (index, kind),
    # consecutive same-kind extrema collapsed to the last of the run
    stack: list[tuple[int, int]] = []
    e = 0
    for i in range(n):
        while e < len(events) and events[e][0] <= i:
            _, idx, kind = events[e]
            if stack and stack[-1][1] == kind:
                stack[-1] = (idx, kind)      # keep the LAST of the run
            else:
                stack.append((idx, kind))
            e += 1
        if len(stack) < 2 or not vol_dry[i]:
            continue

        # "not broken out yet": today's high under the most recent swing
        # high (their flag_consolidation, on the last adjusted high)
        h_last = next((idx for idx, kind in reversed(stack) if kind == 1), -1)
        if h_last < 0 or not (high[i] < high[h_last]):
            continue

        # pair each swing high with the next swing low after it, newest
        # first; a trailing high with no low after it is skipped
        end = len(stack) - 1 if stack[-1][1] == -1 else len(stack) - 2
        depths, highs = [], []
        s = end
        while s >= 1 and len(depths) < MAX_PAIRS:
            l_idx, h_idx = stack[s][0], stack[s - 1][0]
            hp = high[h_idx]
            if not np.isfinite(hp) or hp <= 0 or not np.isfinite(low[l_idx]):
                break
            depths.append(round((hp - low[l_idx]) / hp * 100.0, 2))
            highs.append(h_idx)
            s -= 2
        if not depths:
            continue

        k = _count_run(depths)
        if not MIN_CONTRACTIONS <= k <= MAX_CONTRACTIONS:
            continue
        if depths[0] > MIN_C_MAX or depths[k - 1] > MAX_C_MAX:
            continue
        if (i + 1 - highs[k - 1]) / 5.0 < MIN_WEEKS:
            continue
        out[i] = True
    return out
