"""Cohort-ratio regime gate: build it, verify the claim, then backtest it.

Cohort index: equal-weight, monthly-rebalanced top decile of the universe by
trailing 126-day return (chain-linked daily returns — same construction as
any commercial index). Ratio = cohort / SPY. Gate: new entries allowed only
while ratio > its own value 126 trading days ago (positive trailing slope).
All decisions use closes up to the decision day only.

Stage 1 verifies the THEORY: per-year gate-ON fraction vs the model's known
good and bad years (was the gate actually OFF in 2008/2020/2021/2022?).
Stage 2 backtests lppl_dip2 with the gate, dev first, test as audit.

Run:  python lppl_cohort_gate.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel, metrics, simulate


def build_gate(panel: dict, cfg: dict) -> tuple[np.ndarray, pd.Series]:
    cal = panel['calendar']
    arrays = panel['arrays']
    tickers = sorted(arrays)
    close = np.column_stack([arrays[t]['close_f'] for t in tickers])
    n = len(cal)
    lb = cfg['lppl']['rs_lookback']

    months = pd.Series(cal).dt.to_period('M').values
    rebalance = {int(i) for i in np.flatnonzero(months[1:] != months[:-1]) + 1}

    idx_ret = np.zeros(n)
    members: np.ndarray | None = None
    for i in range(1, n):
        if (i in rebalance or members is None) and i - 1 >= lb:
            with np.errstate(invalid='ignore'):
                r6 = close[i - 1] / close[i - 1 - lb] - 1
            valid = np.isfinite(r6)
            if valid.sum() >= 50:
                k = max(1, int(valid.sum() * 0.10))
                members = np.argsort(np.where(valid, r6, -np.inf))[-k:]
        if members is not None:
            with np.errstate(invalid='ignore'):
                r = close[i, members] / close[i - 1, members] - 1
            r = r[np.isfinite(r)]
            idx_ret[i] = r.mean() if len(r) else 0.0

    cohort = np.cumprod(1 + idx_ret)
    spy = panel['spy_close'].to_numpy()
    ratio = pd.Series(cohort / spy, index=cal)
    gate = np.zeros(n, dtype=bool)
    gate[lb:] = ratio.to_numpy()[lb:] > ratio.to_numpy()[:-lb]
    return gate, ratio


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    panel = load_panel(cfg)
    cal = panel['calendar']
    gate, ratio = build_gate(panel, cfg)

    # ---- stage 1: does the gate match the claimed regimes?
    g = pd.Series(gate, index=cal)
    print('per-year fraction of days the gate is ON:')
    per_year = g.groupby(g.index.year).mean()
    for y, v in per_year.items():
        if y >= 2007:
            print(f'  {y}: {v:.0%}')
    for label, a, b in [('2008', '2008-01-01', '2008-12-31'),
                        ('2020 crash (Feb20-Apr15)', '2020-02-20', '2020-04-15'),
                        ('2021', '2021-01-01', '2021-12-31'),
                        ('2021 H2', '2021-07-01', '2021-12-31'),
                        ('2022', '2022-01-01', '2022-12-31')]:
        print(f'  claimed OFF — {label}: ON {g[a:b].mean():.0%} of days')
    for label, start in [('2009 bottom (Mar-09)', '2009-03-09'),
                        ('2023 start', '2023-01-01')]:
        w = g[start:]
        first_on = w[w].index[0] if w.any() else None
        print(f'  gate first ON after {label}: {first_on.date() if first_on is not None else "never"}')

    fig, ax = plt.subplots(figsize=(13, 5))
    ax.plot(ratio.index, ratio / ratio.iloc[200], color='black', lw=1,
            label='cohort / SPY ratio')
    on = g.astype(int)
    ax.fill_between(ratio.index, 0, 1, where=on.to_numpy() > 0,
                    transform=ax.get_xaxis_transform(), color='green',
                    alpha=0.10, label='gate ON (entries allowed)')
    ax.set_title('cohort-ratio (top-decile momentum cohort vs SPY) and the entry gate')
    ax.legend(loc='upper left')
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(results / 'lppl_cohort_gate.png', dpi=120)

    # ---- stage 2: gated backtest, dev then test
    today = str(cal[-1].date())
    fmt = lambda x: f'{x:.4f}'
    for pname, period in [('dev', (bt['start'], bt['dev_end'])),
                          ('test', (bt['test_start'], today))]:
        rows = {}
        for label, eg in [('baseline', None), ('cohort_gate', gate)]:
            trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2', period,
                                               entry_gate=eg)
            rows[label] = metrics(trades, equity, avg_inv)
        sm = pd.DataFrame(rows).T
        sm.to_csv(results / f'lppl_cohort_gate_{pname}.csv')
        print(f'\n=== {pname} ===')
        print(sm.to_string(float_format=fmt))


if __name__ == '__main__':
    main()
