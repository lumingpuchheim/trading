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
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

try:                                  # in the repo: use its root
    from lppl_backtest import ROOT
except ImportError:                   # standalone (e.g. on a compute box)
    ROOT = Path('.')

WINDOWS = ROOT / 'results' / 'minervini_bets_v5r_windows.npz'
DEV = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
DEV_END = np.datetime64('2018-12-31')
TOP_FRAC = 0.10          # the decile a filter would actually bet
TAIL_Q = 0.99            # "jackpot" for the retention check = top 1% of y
AUX_Q = 0.80             # the trained label: top 20% of y (y >= ~1.049)


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
    print(f'{len(d["y"]):,} bets, windows {d["x"].shape}, device {DEV}, '
          f'channels {[str(c) for c in d["channels"]]}')
    return d


def folds(dates: np.ndarray, k: int, embargo_days: int) -> list:
    """Expanding-window walk-forward inside dev, each validation block
    purged from the training set by `embargo_days` CALENDAR days -- longer
    than the ledger's longest hold, so no training bet's label can resolve
    inside the block it is scored on."""
    dev = dates[dates <= DEV_END]
    edges = pd.to_datetime(pd.Series(dev)).quantile(
        np.linspace(0.4, 1.0, k + 1)).to_numpy().astype('datetime64[D]')
    out = []
    for i in range(k):
        v0, v1 = edges[i], edges[i + 1]
        tr = (dates < v0 - np.timedelta64(embargo_days, 'D'))
        va = (dates >= v0) & (dates < v1) if i < k - 1 else \
             (dates >= v0) & (dates <= DEV_END)
        if tr.sum() > 500 and va.sum() > 200:
            out.append((tr, va, str(v0), str(v1)))
    return out


def report(score: np.ndarray, y: np.ndarray) -> dict:
    """What decides whether a filter is worth anything: the mean of what it
    would BUY, and whether it kept the jackpots. Read `keep1%` -- a model
    can lift the selected mean while quietly dropping the tail."""
    n = len(y)
    k = max(1, int(round(n * TOP_FRAC)))
    sel = np.argsort(-score)[:k]
    tail = np.flatnonzero(y >= np.quantile(y, TAIL_Q))
    keep = len(np.intersect1d(sel, tail)) / max(1, len(tail))
    with np.errstate(invalid='ignore'):
        rho = spearmanr(score, y).statistic
    lab = (y >= np.quantile(y, AUX_Q)).astype(int)
    auc = roc_auc_score(lab, score) if 0 < lab.sum() < n else np.nan
    return {'n': n, 'pool': y.mean(), 'top': y[sel].mean(),
            'lift': y[sel].mean() - y.mean(), 'keep1%': keep,
            'rho': rho, 'auc': auc}


def line(tag: str, m: dict) -> str:
    return (f'{tag:22s} n={m["n"]:>6,d}  pool {m["pool"]:.4f}  '
            f'top{int(TOP_FRAC*100)}% {m["top"]:.4f}  '
            f'lift {m["lift"]:+.4f}  keep1% {m["keep1%"]:5.1%}  '
            f'rho {m["rho"]:+.3f}  auc {m["auc"]:.3f}')
