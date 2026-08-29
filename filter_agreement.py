"""Do MiniRocket and the Shapelet disagree, and does the disagreement pay?

The case for an ensemble rests on the two filters being independent. That
is testable and it decides whether any combiner -- AND, weighted rank, or
a trained stacker -- is worth building.

Three questions, cheapest first:

  1. Spearman correlation of the two scores on the same candidates.
     Near 1 and there is nothing to combine.
  2. Overlap of the sets each approves at the same k. Reported as a
     Jaccard index and as the raw four-way split.
  3. THE ONE THAT DECIDES IT: mean y in each cell of that split. If
     "rocket approves, shapelet rejects" and "shapelet approves, rocket
     rejects" return the same thing, the disagreement is noise and no
     weighting of it can help. If they differ, there is signal in the
     disagreement and a combiner has something to work with.

Both filters are scored walk-forward exactly as in `filter_backtest.py`:
each year fitted on data ending 400 days earlier, threshold frozen from
that fit's own training scores. Dev only.

Usage
    python filter_agreement.py --keep 0.5 --seeds 3 --epochs 40
"""

import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import RidgeClassifierCV

from filters import ShapeletFilter
from lppl_backtest import ROOT
from bets_common import AUX_Q, DEV_END, load
from minervini_rocket import ALPHAS, fit_biases, kernels, transform

LEDGER = ROOT / 'results' / 'minervini_bets_v5r.csv'
WINDOWS = ROOT / 'results' / 'minervini_bets_v5r_windows.npz'


def main() -> None:
    av = sys.argv
    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    keep = opt('--keep', 0.5, float)
    embargo = opt('--embargo', 400, int)

    d = load(str(WINDOWS))
    led = pd.read_csv(LEDGER, parse_dates=['entry_date'])
    w = pd.DataFrame({'ticker': [str(t) for t in d['ticker']],
                      'entry_date': pd.to_datetime(d['entry_date']),
                      'wrow': np.arange(len(d['y']))})
    m = (w.merge(led[['ticker', 'entry_date', 'y']], on=['ticker', 'entry_date'],
                 how='inner').drop_duplicates('wrow').reset_index(drop=True))
    x = d['x'][m['wrow'].to_numpy()]
    y = m['y'].to_numpy(np.float64)
    date = m['entry_date'].to_numpy().astype('datetime64[D]')
    yr = m['entry_date'].dt.year.to_numpy()
    thr = float(np.quantile(y[date <= DEV_END], AUX_Q))
    aux = (y >= thr).astype(np.int8)
    years = sorted(set(yr[date <= DEV_END]))

    W = kernels(); dil = [1, 2, 4, 8, 16]
    qs = np.linspace(0.0, 1.0, 4)[1:-1].astype(np.float32)
    rg = np.random.default_rng(0)
    sd_rows = rg.choice(np.flatnonzero(date <= DEV_END),
                        size=min(2000, int((date <= DEV_END).sum())),
                        replace=False)
    feats = transform(x, W, dil, fit_biases(x, W, dil, 2, sd_rows, qs))

    score = {k: np.full(len(y), np.nan) for k in ('rocket', 'shapelet')}
    cut = {k: np.full(len(y), np.inf) for k in ('rocket', 'shapelet')}
    for Y in years:
        tr = date < np.datetime64(f'{Y}-01-01') - np.timedelta64(embargo, 'D')
        ev = (yr == Y) & (date <= DEV_END)
        if tr.sum() < 2000 or not ev.any() or len(set(aux[tr])) < 2:
            continue
        mu, sd = feats[tr].mean(0), feats[tr].std(0) + 1e-8
        clf = RidgeClassifierCV(alphas=ALPHAS, class_weight='balanced')
        clf.fit((feats[tr] - mu) / sd, aux[tr])
        score['rocket'][ev] = clf.decision_function((feats[ev] - mu) / sd)
        cut['rocket'][ev] = np.quantile(
            clf.decision_function((feats[tr] - mu) / sd), keep)

        f = ShapeletFilter(gamma=0.0, seeds=opt('--seeds', 3, int),
                           epochs=opt('--epochs', 40, int), loss='class')
        f.fit(x[tr], y[tr], aux[tr].astype(np.float32), keep=keep)
        score['shapelet'][ev] = f.score(x[ev])
        cut['shapelet'][ev] = np.quantile(f.score(x[tr]), keep)
        print(f'  {Y}: both filters fitted on {int(tr.sum()):,}, '
              f'scored {int(ev.sum()):,}', flush=True)

    ok = np.isfinite(score['rocket']) & np.isfinite(score['shapelet'])
    R, S = score['rocket'][ok], score['shapelet'][ok]
    yy, aa = y[ok], aux[ok]
    ar = R >= cut['rocket'][ok]
    as_ = S >= cut['shapelet'][ok]

    print(f'\n{int(ok.sum()):,} dev candidates scored by both')
    print(f'\n1. rank agreement')
    print(f'   spearman(rocket, shapelet) = {spearmanr(R, S).statistic:+.3f}')

    inter = int((ar & as_).sum()); union = int((ar | as_).sum())
    print(f'\n2. approved-set overlap at keep={keep:.2f}')
    print(f'   rocket approves {int(ar.sum()):,}, shapelet {int(as_.sum()):,}, '
          f'both {inter:,}, either {union:,}')
    print(f'   Jaccard = {inter / max(1, union):.3f}   '
          f'(1.00 = identical, {keep:.2f} approx = independent)')

    print(f'\n3. does the disagreement pay?  pool mean y = {yy.mean():.4f}')
    print(f'   {"cell":26s} {"n":>7s} {"mean y":>9s} {"vs pool":>9s} '
          f'{">5% share":>10s}')
    cells = [('both approve', ar & as_),
             ('rocket only', ar & ~as_),
             ('shapelet only', ~ar & as_),
             ('both reject', ~ar & ~as_)]
    for nm, sel in cells:
        if sel.sum() == 0:
            continue
        print(f'   {nm:26s} {int(sel.sum()):7,d} {yy[sel].mean():9.4f} '
              f'{yy[sel].mean() - yy.mean():+9.4f} {aa[sel].mean():10.1%}')

    a, b = (ar & ~as_), (~ar & as_)
    if a.sum() and b.sum():
        gap = yy[a].mean() - yy[b].mean()
        print(f'\n   VERDICT: the two disagreement cells differ by '
              f'{gap:+.4f} in mean y.')
        print('   Near zero -> the disagreement is noise, no combiner helps.')
        print('   Clearly non-zero -> there is signal in WHO disagrees.')


if __name__ == '__main__':
    main()
