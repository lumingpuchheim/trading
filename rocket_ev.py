"""Expected value per bet under the MiniRocket filter, with error bars.

The pipeline reported point estimates. On this distribution -- skew +4.5,
median bet below 1.0, the top 5% carrying 40% of gross profit -- a mean
without an interval is not a number you can act on, and the interval is
wide for reasons that do not shrink with more rows.

Fits ONCE and sweeps the threshold afterwards. The pipeline refitted for
every keep level, which cost three MiniRocket transforms to answer one
question; the transform does not depend on the threshold.

Reports per keep level:
  EV        mean(y) - 1, the expected profit on one euro staked
  CI        percentile bootstrap over bets, 10,000 resamples
  ex-top1%  the same EV with the best 1% of selected bets removed --
            says whether the edge is broad or three lucky rows
  t         EV / standard error, for scale only; bets overlap in time so
            this overstates significance and is not a p-value

Usage
    python rocket_ev.py --data results/..._f16.npz
    python rocket_ev.py --boot 10000 --keeps 0,0.5,0.8,0.9,0.95,0.98
"""

import sys

import numpy as np

from filters import RocketFilter
from bets_common import AUX_Q, DEV_END, load


def ev_block(y: np.ndarray, boot: int, rng) -> dict:
    n = len(y)
    if n < 30:
        return {'n': n, 'ev': np.nan, 'lo': np.nan, 'hi': np.nan,
                'ex': np.nan, 't': np.nan, 'win': np.nan}
    idx = rng.integers(0, n, size=(boot, n))
    means = y[idx].mean(axis=1)
    keep = y <= np.quantile(y, 0.99)
    se = y.std(ddof=1) / np.sqrt(n)
    return {'n': n, 'ev': y.mean() - 1.0,
            'lo': float(np.quantile(means, 0.025)) - 1.0,
            'hi': float(np.quantile(means, 0.975)) - 1.0,
            'ex': y[keep].mean() - 1.0,
            't': (y.mean() - 1.0) / se if se > 0 else np.nan,
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
    embargo = opt('--embargo', 400, int)
    tr = date <= DEV_END - np.timedelta64(embargo, 'D')
    te = date > DEV_END
    thr = np.quantile(y[date <= DEV_END], AUX_Q)
    aux = (y >= thr).astype(np.float32)

    print(f'train {int(tr.sum()):,}   test {int(te.sum()):,} (2019-2026)')
    print('fitting MiniRocket once; thresholds swept afterwards', flush=True)
    f = RocketFilter()
    f.fit(x[tr], y[tr], aux[tr], keep=0.90)
    s_te = f.score(x[te])
    s_tr = f.score(x[tr])
    y_te = y[te].astype(np.float64)
    print(f'ridge alpha {f.alpha_:.3g}', flush=True)

    boot = opt('--boot', 10000, int)
    rng = np.random.default_rng(0)
    keeps = [float(k) for k in
             opt('--keeps', '0,0.5,0.8,0.9,0.95,0.98').split(',')]

    print()
    print('TEST PERIOD 2019-2026, one euro per approved bet, no fees or tax')
    show('AllPass (everything)', ev_block(y_te, boot, rng))
    print()
    for k in keeps:
        if k <= 0:
            continue
        cut = float(np.quantile(s_tr, k))      # threshold from TRAIN scores
        sel = s_te >= cut
        m = ev_block(y_te[sel], boot, rng)
        show(f'MiniRocket keep={k:.2f}', m)

    print()
    print('the same cuts on the rejected side -- what the filter declined')
    for k in keeps:
        if k <= 0:
            continue
        cut = float(np.quantile(s_tr, k))
        rej = s_te < cut
        show(f'  rejected @{k:.2f}', ev_block(y_te[rej], boot, rng))


if __name__ == '__main__':
    main()
