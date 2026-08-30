"""Expected value per bet under the MiniRocket filter, with error bars.

The pipeline reported point estimates. On this distribution -- skew +4.5,
median bet below 1.0, the top 5% carrying 40% of gross profit -- a mean
without an interval is not a number you can act on, and the interval is
wide for reasons that do not shrink with more rows.

Fits ONCE and sweeps the threshold afterwards. The pipeline refitted for
every keep level, which cost three MiniRocket transforms to answer one
question; the transform does not depend on the threshold.

Reports per keep level:
  EV        the GEOMETRIC mean of y, minus 1: what one euro becomes per
            bet when the same euro is cycled through them. Arithmetic
            until 2026-08-29, which made this table incomparable with
            every portfolio figure in the repo.
  CI        percentile bootstrap over bets, 10,000 resamples, each
            resample averaged the same geometric way
  ex-top1%  the same EV with the best 1% of selected bets removed --
            says whether the edge is broad or three lucky rows
  t         on log multiples, for scale only; bets overlap in time so
            this overstates significance and is not a p-value

Usage
    python rocket_ev.py --data results/..._f16.npz
    python rocket_ev.py --boot 10000 --keeps 0,0.5,0.8,0.9,0.95,0.98
"""

import sys

import numpy as np

from geostats import geo_mean_per_euro
from filters import RocketFilter
from bets_common import AUX_Q, LOOKBACK_YEARS, label_from, load, year_blocks


def ev_block(y: np.ndarray, boot: int, rng) -> dict:
    n = len(y)
    if n < 30:
        return {'n': n, 'ev': np.nan, 'lo': np.nan, 'hi': np.nan,
                'ex': np.nan, 't': np.nan, 'win': np.nan}
    # multiples compound, so they are averaged by averaging their logs
    ly = np.log(np.clip(y, 1e-9, None))
    idx = rng.integers(0, n, size=(boot, n))
    geos = np.exp(ly[idx].mean(axis=1))
    keep = y <= np.quantile(y, 0.99)
    se = ly.std(ddof=1) / np.sqrt(n)
    return {'n': n, 'ev': geo_mean_per_euro(y) - 1.0,
            'lo': float(np.quantile(geos, 0.025)) - 1.0,
            'hi': float(np.quantile(geos, 0.975)) - 1.0,
            'ex': geo_mean_per_euro(y[keep]) - 1.0,
            't': float(ly.mean() / se) if se > 0 else np.nan,
            'win': float((y > 1).mean())}


def show(tag: str, m: dict) -> None:
    print(f'{tag:22s} n={m["n"]:6,d}  EV {m["ev"]*100:+6.2f}%  '
          f'95% CI [{m["lo"]*100:+6.2f}%, {m["hi"]*100:+6.2f}%]  '
          f'ex-top1% {m["ex"]*100:+6.2f}%  win {m["win"]:5.1%}  '
          f't {m["t"]:+5.2f}')


def main() -> None:
    av = sys.argv
    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    d = load(opt('--data', None))
    x, y, date = d['x'], d['y'], d['date']
    lookback = opt('--lookback', LOOKBACK_YEARS or 0, float) or None

    # Out-of-fold over the WHOLE record: each block is scored by a fit
    # that ended `embargo` days before it, and the scores are pooled.
    # No reserved tail, no special year (EVALUATION_SPEC.md rule 1).
    print('walk-forward MiniRocket fits; thresholds swept afterwards',
          flush=True)
    score = np.full(len(y), np.nan)
    cut_of = np.full(len(y), np.nan)     # this block's own train quantiles
    train_scores = {}
    for Y, trm, ev in year_blocks(date, d['exit'], lookback_years=lookback):
        a_tr = label_from(y, trm)[trm].astype(np.float32)
        if len(set(a_tr.tolist())) < 2:
            continue
        f = RocketFilter()
        f.fit(x[trm], y[trm], a_tr, keep=0.90)
        score[ev] = f.score(x[ev])
        train_scores[Y] = f.score(x[trm])
        cut_of[ev] = Y
        print(f'  {Y}: fit {int(trm.sum()):,}, scored {int(ev.sum()):,}, '
              f'alpha {f.alpha_:.3g}', flush=True)

    te = np.isfinite(score)
    s_te = score[te]
    y_te = y[te].astype(np.float64)
    block = cut_of[te]

    boot = opt('--boot', 10000, int)
    rng = np.random.default_rng(0)
    keeps = [float(k) for k in
             opt('--keeps', '0,0.5,0.8,0.9,0.95,0.98').split(',')]

    def approved(k):
        """Rows the filter takes at keep=k. The cut is the k-th quantile of
        the scores of THAT block's own training rows, so it is frozen
        before the block opens rather than read off the block itself."""
        sel = np.zeros(len(s_te), bool)
        for Y, s_tr in train_scores.items():
            here = block == Y
            if here.any():
                sel[here] = s_te[here] >= float(np.quantile(s_tr, k))
        return sel

    print()
    print(f'ALL {int(te.sum()):,} BETS, out of fold, one euro each, '
          f'no fees or tax')
    show('AllPass (everything)', ev_block(y_te, boot, rng))
    print()
    for k in keeps:
        if k <= 0:
            continue
        show(f'MiniRocket keep={k:.2f}',
             ev_block(y_te[approved(k)], boot, rng))

    print()
    print('the same cuts on the rejected side -- what the filter declined')
    for k in keeps:
        if k <= 0:
            continue
        show(f'  rejected @{k:.2f}',
             ev_block(y_te[~approved(k)], boot, rng))


if __name__ == '__main__':
    main()
