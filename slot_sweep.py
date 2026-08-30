"""More, smaller positions: does capacity beat selection?

`slot_pressure.py` measured the constraint: the median number of free
slots is ZERO, and on 70.5% of trading days the book is full while
signals keep arriving. A filter can only reorder that queue. Adding slots
removes the queue.

This compares 10 slots at 10% against 20 slots at 5% -- same 100% gross
exposure, twice the names, half the bet. No filter, both periods, through
the same `simulate()` as everything else.

Two effects pull in opposite directions and the net is not obvious:
  + more of the signal stream gets taken instead of being queued away
  - each winner contributes half as much, so the fat right tail that
    carries this book (the top 5% of bets supply ~40% of gross profit)
    is diluted

Usage
    python slot_sweep.py
    python slot_sweep.py --configs 10:0.10,20:0.05,30:0.033
"""

import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from geostats import geo_per_bet
from lppl_backtest import ROOT, load_config, metrics
from minervini_backtest import apply_v5, build_panel, pool_by_day, simulate

OUT = ROOT / 'results' / 'slot_sweep.png'


def main() -> None:
    av = sys.argv
    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    specs = []
    for part in opt('--configs', '10:0.10,20:0.05').split(','):
        n, w = part.split(':')
        specs.append((int(n), float(w)))

    base = apply_v5(load_config())
    base['minervini_trading']['reentry_fast'] = True
    panel = build_panel(base, v5=True)
    cal = panel['calendar']
    pool = pool_by_day(panel['watch'] if 'watch' in panel else panel['setup'])
    bt = base['backtest']

    periods = {}
    for name, a, b in [('full', bt['start'], str(cal[-1].date()))]:
        j0 = int(cal.searchsorted(pd.Timestamp(a)))
        j1 = int(cal.searchsorted(pd.Timestamp(b), side='right')) - 1
        periods[name] = (j0, j1)

    curves: dict = {}
    print(f'{"config":14s} {"period":6s} {"total":>9s} {"ann":>7s} '
          f'{"maxDD":>8s} {"trades":>7s} {"geo/bet":>10s} {"invested":>9s}')
    for n_slots, wt in specs:
        cfg = apply_v5(load_config())
        cfg['minervini_trading']['reentry_fast'] = True
        cfg['minervini_trading']['max_positions'] = n_slots
        cfg['minervini_trading']['equal_weight_fraction'] = wt
        tag = f'{n_slots} x {wt:.0%}'
        for pname, per in periods.items():
            tr_, eq, inv, _ = simulate(panel, cfg, per, moc=True,
                                       pool_days=pool)
            t = pd.DataFrame(tr_)
            m = metrics(t, eq, inv)
            geo = geo_per_bet(t) - 1.0        # one vote per position
            curves[(tag, pname)] = eq
            print(f'{tag:14s} {pname:6s} {m["total_return"]:+8.1%} '
                  f'{m["ann_return"]:+6.1%} {m["max_drawdown"]:+7.1%} '
                  f'{m["n_trades"]:7d} {geo:+9.2%} {inv:8.1%}', flush=True)

    fig, ax = plt.subplots(1, 2, figsize=(13.5, 5.2))
    colors = ['#1B3B6F', '#C44E52', '#55A868', '#8172B2']
    for k, pname in enumerate(('dev', 'test')):
        for c, (n_slots, wt) in enumerate(specs):
            tag = f'{n_slots} x {wt:.0%}'
            eq = curves[(tag, pname)]
            ax[k].plot(eq.index, eq.values / eq.iloc[0] * 100_000,
                       lw=1.6, color=colors[c % len(colors)], label=tag)
        ax[k].axhline(100_000, color='#999', lw=.8, ls=':')
        ax[k].set_title(f'{pname} — same 100% gross exposure, '
                        f'different number of names')
        ax[k].set_ylabel('equity (EUR, start 100,000)')
        ax[k].legend(loc='upper left', fontsize=9)
        ax[k].grid(alpha=.15)
    fig.tight_layout()
    fig.savefig(OUT, dpi=140)
    print(f'-> {OUT}')


if __name__ == '__main__':
    main()
