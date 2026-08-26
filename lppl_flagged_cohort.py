"""Stage 1 only: the flagged-cohort ratio — no backtest.

Cohort: every stock with a 2-of-5 bubble evaluation within the trailing
126 trading days (membership decided at each close, applied from the next
day). Index: equal-weight chain-linked daily returns of current members.
Ratio: cohort index / SPY. Gate declared before running:

  hostile (entries would be blocked) when
    ratio < its own value 126 trading days ago
  AND the cohort held >= 10 members on at least half of those 126 days
  (a sparse or empty cohort gives no evidence; the gate stays open).

Verification only: regime coverage, good-year false positives, recovery
timing, cohort size.  Run: python lppl_flagged_cohort.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel


def main() -> None:
    cfg = load_config()
    results = ROOT / cfg['backtest']['results_dir']
    panel = load_panel(cfg)
    cal = panel['calendar']
    n = len(cal)
    lb = cfg['lppl']['rs_lookback']  # 126, reused as membership + slope window

    tickers = sorted(panel['arrays'])
    tpos = {t: j for j, t in enumerate(tickers)}
    cal_pos = {d: i for i, d in enumerate(cal)}
    close = np.column_stack([panel['arrays'][t]['close_f'] for t in tickers])

    flags = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'lppl_flags.parquet')
    qual = flags[flags['votes'] >= cfg['lppl']['min_votes_loose']]
    member = np.zeros((n, len(tickers)), dtype=bool)
    for r in qual.itertuples():
        i = cal_pos.get(r.date)
        j = tpos.get(r.ticker)
        if i is not None and j is not None:
            member[i:i + lb, j] = True

    size = member.sum(axis=1)
    idx_ret = np.zeros(n)
    for i in range(1, n):
        m = member[i - 1]  # membership decided at the previous close
        if m.any():
            with np.errstate(invalid='ignore'):
                r = close[i, m] / close[i - 1, m] - 1
            r = r[np.isfinite(r)]
            idx_ret[i] = r.mean() if len(r) else 0.0
    cohort = np.cumprod(1 + idx_ret)
    spy = panel['spy_close'].to_numpy()
    ratio = cohort / spy

    populated = pd.Series(size >= 10, index=cal).rolling(lb).mean() >= 0.5
    hostile = np.zeros(n, dtype=bool)
    hostile[lb:] = (ratio[lb:] < ratio[:-lb]) & populated.to_numpy()[lb:]
    h = pd.Series(hostile, index=cal)

    print('median cohort size per year / fraction of days blocked:')
    sz = pd.Series(size, index=cal)
    for y in range(2007, 2027):
        yy = str(y)
        print(f'  {y}: size {sz[yy].median():4.0f}   blocked {h[yy].mean():4.0%}')
    print()
    for label, a, b in [('2008 (target)', '2008-01-01', '2008-12-31'),
                        ('2020 crash (target)', '2020-02-20', '2020-04-15'),
                        ('2021 (target!)', '2021-01-01', '2021-12-31'),
                        ('2021 H2 (target!)', '2021-07-01', '2021-12-31'),
                        ('2022 (target)', '2022-01-01', '2022-12-31'),
                        ('2009-2013 GOOD', '2009-01-01', '2013-12-31'),
                        ('2016 GOOD', '2016-01-01', '2016-12-31'),
                        ('2023-2025 GOOD', '2023-01-01', '2025-12-31')]:
        print(f'  {label:22s} blocked {h[a:b].mean():4.0%}')
    w = h['2009-03-09':]
    first_open = w[~w].index[0] if (~w).any() else None
    print(f'  first open after the 2009-03-09 bottom: '
          f'{first_open.date() if first_open is not None else "never"}')

    fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True,
                             height_ratios=[2, 1])
    axes[0].plot(cal, ratio / ratio[500], color='black', lw=1,
                 label='flagged-cohort / SPY')
    axes[0].fill_between(cal, 0, 1, where=hostile,
                         transform=axes[0].get_xaxis_transform(),
                         color='red', alpha=0.15, label='gate hostile')
    axes[0].legend(loc='upper left')
    axes[0].grid(alpha=0.3)
    axes[0].set_title('flagged-cohort ratio: recently-flagged bubbles vs SPY')
    axes[1].plot(cal, size, color='tab:blue', lw=0.8)
    axes[1].set_ylabel('cohort size')
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(results / 'lppl_flagged_cohort.png', dpi=120)
    print(f'chart -> {results / "lppl_flagged_cohort.png"}')


if __name__ == '__main__':
    main()
