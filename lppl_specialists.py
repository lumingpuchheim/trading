"""Stage 1 only: verify three specialist regime classifiers — no backtest.

Declared before running:
  S1 slow-bear:  SPY close < its 200-day SMA
  S2 hidden-top: fraction of universe stocks above their own 200-day SMA < 50%
  S3 crash:      SPY 20-day realised volatility > trailing 756-day 90th pctile
  OR-gate:       hostile if ANY specialist fires

Each specialist is judged on (a) coverage of its target regime and
(b) false-positive rate in the model's good years. Run: python lppl_specialists.py
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
    spy = panel['spy_close']

    # S1: slow bear
    s1 = (spy < spy.rolling(200).mean()).to_numpy()

    # S2: breadth — fraction of stocks above their own 200-day SMA
    close = pd.DataFrame({t: a['close_f'] for t, a in panel['arrays'].items()},
                         index=cal)
    above = close.gt(close.rolling(200).mean())
    valid = close.notna() & close.rolling(200).mean().notna()
    breadth = above.sum(axis=1) / valid.sum(axis=1).clip(lower=1)
    s2 = (breadth < 0.50).to_numpy()

    # S3: crash — SPY 20d realised vol above trailing 3y 90th percentile
    vol = spy.pct_change().rolling(20).std()
    s3 = (vol > vol.rolling(756).quantile(0.90)).to_numpy()

    gate_or = s1 | s2 | s3
    sig = pd.DataFrame({'S1_slowbear': s1, 'S2_breadth': s2, 'S3_vol': s3,
                        'OR': gate_or}, index=cal)

    windows = [('2008 (target S1/S3)', '2008-01-01', '2008-12-31'),
               ('2020 crash (target S3)', '2020-02-20', '2020-04-15'),
               ('2021 (target S2)', '2021-01-01', '2021-12-31'),
               ('2021 H2 (target S2)', '2021-07-01', '2021-12-31'),
               ('2022 (target S1)', '2022-01-01', '2022-12-31'),
               ('2009-2013 GOOD', '2009-01-01', '2013-12-31'),
               ('2016 GOOD', '2016-01-01', '2016-12-31'),
               ('2023-2025 GOOD', '2023-01-01', '2025-12-31')]
    print(f'{"window":26s} {"S1":>5s} {"S2":>5s} {"S3":>5s} {"OR":>5s}   (fraction of days hostile)')
    for label, a, b in windows:
        w = sig[a:b]
        print(f'{label:26s} ' + ' '.join(f'{w[c].mean():5.0%}' for c in sig.columns))

    for name, s in [('S1', s1), ('S2', s2)]:
        w = pd.Series(s, index=cal)['2009-03-09':]
        first_off = w[~w].index[0] if (~w).any() else None
        print(f'{name} first friendly after the 2009-03-09 bottom: '
              f'{first_off.date() if first_off is not None else "never"}')

    fig, ax = plt.subplots(figsize=(13, 5))
    ax.plot(spy.index, spy / spy.iloc[0], color='black', lw=1, label='SPY')
    ax.set_yscale('log')
    for s, color, label in [(s1, 'tab:red', 'S1 slow-bear'),
                            (s2, 'tab:orange', 'S2 breadth<50%'),
                            (s3, 'tab:purple', 'S3 high vol')]:
        ax.fill_between(cal, 0, 1, where=s, transform=ax.get_xaxis_transform(),
                        color=color, alpha=0.12, label=label)
    ax.legend(loc='upper left', fontsize=9)
    ax.set_title('three specialist regime classifiers (shaded = hostile)')
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(results / 'lppl_specialists.png', dpi=120)
    print(f'chart -> {results / "lppl_specialists.png"}')


if __name__ == '__main__':
    main()
