"""Fine bet-size scan for v5r between 10% and 20% (plus anchors).

Same signals, no parking, no controls; only equal_weight_fraction
changes. Prints funded bets, average exposure, per-position mean, total
return and CAGR for both periods, and writes results/minervini_size_scan.csv
plus a chart. POST-HOC like everything on this data; the point is the
SHAPE of the curve, not picking a winner from it.

Run: python minervini_size_scan.py
"""
import copy

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config
from minervini_backtest import apply_v5, build_panel, pool_by_day, simulate

SIZES = [0.10, 0.11, 0.12, 0.125, 0.14, 0.15, 0.16, 0.18, 0.20]

base = apply_v5(load_config())
panel = build_panel(base, v5=True)
cal = panel['calendar']
pool = pool_by_day(panel['watch'])
periods = {}
# One continuous record, start to today: nothing here is fitted, so
# the 2019 split only ever cut one result in half (EVALUATION_SPEC.md).
for name, a, b in [('full', '2007-01-01', str(cal[-1].date()))]:
    j0 = int(cal.searchsorted(pd.Timestamp(a)))
    j1 = int(cal.searchsorted(pd.Timestamp(b), side='right')) - 1
    periods[name] = (j0, j1)

rows = []
for sz in SIZES:
    cfg = copy.deepcopy(base)
    cfg['minervini_trading']['equal_weight_fraction'] = sz
    for per, pr in periods.items():
        tr, eq, inv, _ = simulate(panel, cfg, pr, pool_days=pool, moc=True)
        yrs = (pr[1] - pr[0] + 1) / 252
        tot = eq.iloc[-1] / eq.iloc[0] - 1
        npos = (tr.ticker + '|' + tr.entry_date.astype(str)).nunique()
        rows.append({'size': sz, 'period': per, 'positions': npos,
                     'avg_invested': inv, 'total': tot,
                     'cagr': (1 + tot) ** (1 / yrs) - 1,
                     'maxdd': float((eq / eq.cummax() - 1).min())})
        print(f'{sz:5.1%} {per:4s}: {npos:4d} bets | invested {inv:5.1%} | '
              f'total {tot:+8.1%} | CAGR {(1+tot)**(1/yrs)-1:+.2%} | '
              f'maxDD {float((eq/eq.cummax()-1).min()):+.0%}', flush=True)

df = pd.DataFrame(rows)
df.to_csv(ROOT / 'results' / 'minervini_size_scan.csv', index=False)

fig, axes = plt.subplots(1, 2, figsize=(13, 5))
for ax, col, lab in [(axes[0], 'total', 'total return'),
                     (axes[1], 'positions', 'funded bets')]:
    for per, g in df.groupby('period'):   # one group: the whole record
        ax.plot(g['size'] * 100, g[col] * (100 if col == 'total' else 1),
                marker='o', label=per)
    ax.set_xlabel('slot size %')
    ax.set_ylabel(lab + (' %' if col == 'total' else ''))
    ax.grid(alpha=0.3)
    ax.legend()
fig.suptitle('v5r slot-size scan 10-20% (no parking): return vs funded bets')
fig.tight_layout()
fig.savefig(ROOT / 'results' / 'minervini_size_scan.png', dpi=120)
print('-> results/minervini_size_scan.csv, minervini_size_scan.png')
