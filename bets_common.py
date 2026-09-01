"""Shared plumbing for the filter work: data, folds, metrics, labels.

Extracted from `minervini_cnn.py` on 2026-08-29 when the CNN was removed
(DECISIONS.md: too many parameters, hard to train). None of this was ever
CNN-specific -- it only lived there because that was the first model
written. Every filter, every diagnostic and every backtest driver reads
its data, cuts its folds and computes its metrics here, so the numbers in
one report are comparable to the numbers in another.

Nothing in this file trains anything.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from geostats import geo_mean_per_euro
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

try:                                  # in the repo: use its root
    from lppl_backtest import ROOT
except ImportError:                   # standalone (e.g. on a compute box)
    ROOT = Path('.')

WINDOWS = ROOT / 'results' / 'minervini_bets_v5r_windows.npz'
DEV = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
TOP_FRAC = 0.10          # the decile a filter would actually bet
TAIL_Q = 0.99            # "jackpot" for the retention check = top 1% of y
AUX_Q = 0.80             # the trained label: top 20% of y
LOOKBACK_YEARS = None    # None = expanding: every year of history so far
EMBARGO_DAYS = 400       # blanket purge, the default: keep a training bet
LEGACY_EMBARGO = EMBARGO_DAYS         # only if it was ENTERED this long
                                      # before the block. Per-row purging
                                      # on the exit date is available and
                                      # needs no constant -- pass
                                      # embargo_days=None with exits -- but
                                      # the default is the blanket rule, by
                                      # the operator's decision, so results
                                      # stay comparable with everything
                                      # recorded before 2026-08-29.
MIN_TRAIN = 2000         # a block with less history than this is not scored
INNER_MIN = 1000         # RANKER_SPEC Amendment 1: a held-out YEAR inside
                         # a fold's training window counts toward the
                         # alpha choice only if at least this many rows
                         # survive the symmetric 400-day purge around it.
                         # A fold with fewer than two usable years fits
                         # nothing and keeps the control ordering.
T_FLOOR = 3              # trading days: the shortest hold a rate is
                         # allowed to be divided by. 1.8% of bets close
                         # inside three days and carry ~14% of the total
                         # |rate| mass -- overwhelmingly fast stop-outs
                         # whose rates reach -0.11/day against a best of
                         # +0.012. Without the floor the target is a
                         # stop-out detector (RANKER_SPEC.md).

# There is NO development period and no test period -- EVALUATION_SPEC.md.
# A model may only see data from before the block it scores, and that is
# expressed as a rolling schedule over the whole record, never as a
# calendar constant. `DEV_END` was deleted on 2026-08-29.


def _purge(entries, exits, opens, embargo_days=None):
    """Training rows a block is allowed to see.

    A bet's label is not known when it is entered -- it is made of prices
    over the whole holding period -- so a bet entered before the block can
    still carry the block's own outcomes.

    DEFAULT, per row: keep a bet only if it had CLOSED before the block
    opened. No constant, nothing to tune, and it cannot go stale.

    `embargo_days` switches to the pre-2026-08-29 BLANKET rule: keep a bet
    only if it was ENTERED that many days before the block. It is kept so
    the old schedule can be reproduced and compared, and for callers whose
    windows carry no exit dates. It is not the default and should not
    become one: the buffer has to equal the longest hold anyone has seen
    (400 days here), which pushes every block's training data more than a
    year into the past to cover the 0.1% of bets that run that long, and
    it has to be raised again the first time a longer trade appears.
    """
    if embargo_days is not None:
        return entries < opens - np.timedelta64(int(embargo_days), 'D')
    return exits < opens


def year_blocks(dates: np.ndarray, exits: np.ndarray | None,
                min_train: int = MIN_TRAIN,
                lookback_years=LOOKBACK_YEARS,
                embargo_days=EMBARGO_DAYS) -> list:
    """The walk-forward schedule every fitted thing in this repo shares.

    Returns (year, train_mask, block_mask) per calendar year of the record
    that has enough history behind it. Training rows are purged per row by
    exit date (see `_purge`), so a block never learns from a bet that was
    still open while it ran.

    LOOKBACK -- `lookback_years` keeps only the last N years of whatever
    survived the purge; None trains on everything so far. Expanding makes
    the 2026 model see nine times the rows the 2012 model saw, so a change
    in measured skill cannot be told apart from the training set growing;
    a fixed window holds every block to the same evidence. It is also what
    decides cost: expanding is quadratic in years across the whole
    walk-forward, a window is linear.

    The window is measured back from the NEWEST usable bet, so the purge
    shifts nothing -- with per-row purging the freshest training bet sits
    about three weeks before the block instead of thirteen months.
    """
    d = np.asarray(dates).astype('datetime64[D]')
    if exits is None and embargo_days is None:
        raise ValueError(
            'year_blocks needs exit dates to purge overlapping labels; '
            'rebuild the windows with:  python minervini_bets.py '
            '--windows 252  (or pass embargo_days for the old blanket '
            'rule)')
    ex = None if exits is None else np.asarray(exits).astype('datetime64[D]')
    yr = pd.to_datetime(d).year.to_numpy()
    span = None if not lookback_years else np.timedelta64(
        int(round(float(lookback_years) * 365)), 'D')
    out = []
    for Y in sorted(set(int(v) for v in yr)):
        block = yr == Y
        tr = _purge(d, ex, np.datetime64(str(Y) + '-01-01'), embargo_days)
        if span is not None and tr.any():
            tr = tr & (d >= d[tr].max() - span)
        if tr.sum() >= min_train and block.any():
            out.append((Y, tr, block))
    return out


def label_from(y: np.ndarray, train: np.ndarray) -> np.ndarray:
    """The jackpot label, cut at AUX_Q of the TRAINING rows alone.

    Cutting it once on a fixed slice of history -- what `DEV_END` used to
    do -- measures a 2026 fold against a yardstick made in 2018."""
    return (y >= float(np.quantile(y[train], AUX_Q))).astype(np.int8)


def rate_target(y, days_held, half_frac=None, y_half=None,
                half_days_held=None, t_floor=T_FLOOR) -> np.ndarray:
    """THE ranker target: ln(y)/t, one vote per bet (RANKER_SPEC.md).

    `y` is euros returned per euro committed (dividends in,
    `geostats.bet_multiples` convention) and `t` is TRADING days held --
    calendar days have a minimum of zero and would divide by zero --
    floored at `t_floor`.

    A split bet is two capital streams of the one bet. The banked half
    earned `ln(y_half)` over its own `t_half` days and then stopped
    consuming a slot; the rest earned `ln(y_rest)` over the full `t`. Sum
    both wins, each stream at its own rate and its own capital share `f`:

        r = f*ln(y_half)/t_half + (1-f)*ln(y_rest)/t
        y_rest = (y - f*y_half) / (1-f)

    Multiples decompose ARITHMETICALLY by capital share (never logs); the
    streams' rates then combine by those same shares. Ending the first
    stream's clock at the half-sale is the point -- banked capital is
    free capital, and this target credits it.

    Equal weight per bet everywhere: every bet is a flat tenth of equity,
    so size is a constant and never weights anything. A euro-day weight
    was proposed and rejected on 2026-08-31; do not re-propose it.
    """
    y = np.asarray(y, dtype=np.float64)
    t = np.maximum(np.asarray(days_held, dtype=np.float64), float(t_floor))
    if half_frac is None:
        return np.log(np.maximum(y, 1e-9)) / t
    f = np.asarray(half_frac, dtype=np.float64)
    yh = np.asarray(y_half, dtype=np.float64)
    th = np.maximum(np.asarray(half_days_held, dtype=np.float64),
                    float(t_floor))
    split = (f > 0) & np.isfinite(yh)
    f = np.where(split, f, 0.0)
    yh = np.where(split, yh, 1.0)
    y_rest = np.where(split, (y - f * yh) / np.maximum(1.0 - f, 1e-12), y)
    r = ((1.0 - f) * np.log(np.maximum(y_rest, 1e-9)) / t
         + f * np.log(np.maximum(yh, 1e-9)) / th)
    return r


def rent_legs(y, days_held, half_frac=None, y_half=None,
              half_days_held=None):
    """The TWO HEADS of the rent target (RANKER_SPEC Amendment 4).

        profit = ln(y)          the log-profit a bet returned
        days   = t              the trading days it blocked a slot
        r      = profit - c * days

    A slot's long-run growth is total log-profit over total days across
    the bets that occupy it -- a ratio of SUMS. The old target averaged
    each bet's OWN ratio, which is a different quantity and disagrees
    exactly where the money is: it ranked a +10% in 20 days above a +40%
    in 180, and let a -8% stop-out in three days dominate the loss.
    Renting the slot by the day fixes the shape: a bet pays its profit
    and owes rent for every day it blocks the slot, and ranking by
    expected `r` is the greedy-optimal slot decision for the
    ratio-of-sums objective.

    NO FLOOR. Nothing divides by days any more, so nothing explodes when
    a bet closes in one.

    A split bet decomposes exactly as before -- multiples arithmetically
    by capital share -- and each stream owes rent for its own days:

        r = f*(ln(y_half) - c*t_half) + (1-f)*(ln(y_rest) - c*t)

    so the heads are the capital-weighted profit and the capital-weighted
    holding time. The second is the same quantity the trades table gives
    as sum(weight * days_held) over a position's rows.

    Returns (profit, days); `c` never appears here, which is what lets
    one fit pair serve the whole rent grid.
    """
    y = np.asarray(y, dtype=np.float64)
    t = np.asarray(days_held, dtype=np.float64)
    if half_frac is None:
        return np.log(np.maximum(y, 1e-9)), t
    f = np.asarray(half_frac, dtype=np.float64)
    yh = np.asarray(y_half, dtype=np.float64)
    th = np.asarray(half_days_held, dtype=np.float64)
    split = (f > 0) & np.isfinite(yh)
    f = np.where(split, f, 0.0)
    yh = np.where(split, yh, 1.0)
    th = np.where(split, th, 0.0)
    y_rest = np.where(split, (y - f * yh) / np.maximum(1.0 - f, 1e-12), y)
    profit = ((1.0 - f) * np.log(np.maximum(y_rest, 1e-9))
              + f * np.log(np.maximum(yh, 1e-9)))
    return profit, (1.0 - f) * t + f * th


def value_target(y, half_frac=None, y_half=None):
    """The plain per-bet log multiple (RANKER_SPEC Amendment 6).

        r = ln(y)                                unsplit
        r = 0.5*ln(y_half) + 0.5*ln(y_rest)      split

    No rent, no ratio, no floor, and no holding time anywhere. It is
    trainable only because the trading rule caps the hold: with every
    position force-sold after H days, "profit per bet" and "profit per
    slot-time" stop being different rankings, so the mismatch that
    consumed the ratio and rent targets is removed by the exit ladder
    instead of by loss engineering.

    THE SPLIT CONVENTION IS HALF AND HALF BY DECISION, not by reading
    `half_frac`. The two legs are one bet's two capital streams and each
    counts once; `strength_sell_frac` happens to be 0.5 today, so the
    two agree, and pinning it here keeps the target fixed if that knob
    ever moves.
    """
    y = np.asarray(y, dtype=np.float64)
    if half_frac is None:
        return np.log(np.maximum(y, 1e-9))
    f = np.asarray(half_frac, dtype=np.float64)
    yh = np.asarray(y_half, dtype=np.float64)
    split = (f > 0) & np.isfinite(yh)
    y_rest = np.where(split, (y - f * yh) / np.maximum(1.0 - f, 1e-12), y)
    return np.where(split,
                    0.5 * np.log(np.maximum(np.where(split, yh, 1.0), 1e-9))
                    + 0.5 * np.log(np.maximum(y_rest, 1e-9)),
                    np.log(np.maximum(y, 1e-9)))


def warmup_rows(dates: np.ndarray, n: int, rng) -> np.ndarray:
    """Rows for fitting the MiniRocket bias quantiles: a sample of the
    EARLIEST bets in the record.

    The biases are quantiles of random convolution outputs and carry no
    label information, but they are still fitted from data, so they have
    to come from before every block that will be scored. Taking them from
    the start of the record keeps that true for every fold without
    re-running the transform once per fold."""
    order = np.argsort(np.asarray(dates).astype('datetime64[D]'), kind='stable')
    pool = order[:max(n * 3, n)]
    return rng.choice(pool, size=min(n, len(pool)), replace=False)


def jackpot_loss(logit, a, y, gamma: float, rho: float):
    """Cost-sensitive BCE. Positives carry the class-balance weight rho.
    Negatives carry 1 + gamma * max(0, 1-y): calling a jackpot on a bet
    that lost 8 cents is punished by those 8 cents, while calling one on a
    bet that merely underperformed costs nothing extra.

    gamma=0 is plain balanced BCE -- pure jackpot classification, and the
    standing setting (DECISIONS.md, filter layer)."""
    w = torch.where(a > 0.5, torch.full_like(y, rho),
                    1.0 + gamma * torch.clamp(1.0 - y, min=0.0))
    bce = F.binary_cross_entropy_with_logits(logit, a, reduction='none')
    return (w * bce).mean()


def load(path=None) -> dict:
    """The bet windows written by `minervini_bets.py --windows`."""
    src = Path(path) if path else WINDOWS
    if not src.exists():
        sys.exit(f'missing {src}; build it with:\n'
                 f'    python minervini_bets.py --windows 252')
    z = np.load(src, allow_pickle=True)
    d = {k: z[k] for k in z.files}
    d['x'] = d['x'].astype(np.float32)      # a shipped file may be float16
    d['date'] = d['entry_date'].astype('datetime64[D]')
    # exit dates are what purges overlapping labels. Windows written
    # before 2026-08-29 have none, and year_blocks says so rather than
    # quietly falling back to a constant that would need re-tuning.
    d['exit'] = (d['exit_date'].astype('datetime64[D]')
                 if 'exit_date' in d else None)
    print(f'{len(d["y"]):,} bets, windows {d["x"].shape}, device {DEV}, '
          f'channels {[str(c) for c in d["channels"]]}')
    return d


def folds(dates: np.ndarray, k: int, exits: np.ndarray | None,
          embargo_days=None) -> list:
    """Expanding-window walk-forward over the WHOLE record. Training
    rows are purged per row by exit date, so no training bet's label can
    resolve inside the block it is scored on (see `_purge`).

    The last block runs to the end of the record: no tail is reserved,
    and every block is scored by a fit that ended before it."""
    ex = None if exits is None else np.asarray(exits).astype('datetime64[D]')
    edges = pd.to_datetime(pd.Series(dates)).quantile(
        np.linspace(0.4, 1.0, k + 1)).to_numpy().astype('datetime64[D]')
    out = []
    for i in range(k):
        v0, v1 = edges[i], edges[i + 1]
        tr = _purge(dates, ex, v0, embargo_days)
        va = (dates >= v0) & (dates < v1) if i < k - 1 else \
             (dates >= v0)
        if tr.sum() > 500 and va.sum() > 200:
            out.append((tr, va, str(v0), str(v1)))
    return out


def report(score: np.ndarray, y: np.ndarray) -> dict:
    """What decides whether a filter is worth anything: what a euro
    becomes on the bets it would BUY, and whether it kept the jackpots.
    Read `keep1%` -- a model can lift the selected mean while quietly
    dropping the tail.

    GEOMETRIC (arithmetic removed 2026-08-29). `y` is one multiple per
    bet, and multiples are averaged by multiplying: a filter that raises
    the arithmetic mean by catching one 6x while losing on the rest has
    not found a book anyone can hold."""
    n = len(y)
    k = max(1, int(round(n * TOP_FRAC)))
    sel = np.argsort(-score)[:k]
    tail = np.flatnonzero(y >= np.quantile(y, TAIL_Q))
    keep = len(np.intersect1d(sel, tail)) / max(1, len(tail))
    with np.errstate(invalid='ignore'):
        rho = spearmanr(score, y).statistic
    lab = (y >= np.quantile(y, AUX_Q)).astype(int)
    auc = roc_auc_score(lab, score) if 0 < lab.sum() < n else np.nan
    pool, top = geo_mean_per_euro(y), geo_mean_per_euro(y[sel])
    return {'n': n, 'pool': pool, 'top': top,
            'lift': top - pool, 'keep1%': keep,
            'rho': rho, 'auc': auc}


def line(tag: str, m: dict) -> str:
    return (f'{tag:22s} n={m["n"]:>6,d}  pool {m["pool"]:.4f}  '
            f'top{int(TOP_FRAC*100)}% {m["top"]:.4f}  '
            f'lift {m["lift"]:+.4f}  keep1% {m["keep1%"]:5.1%}  '
            f'rho {m["rho"]:+.3f}  auc {m["auc"]:.3f}')
