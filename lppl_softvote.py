"""Soft-vote sizing ensemble: bet less when fewer regime voters agree.

Members (one per independent axis, declared before running):
  S1 slow-bear:  SPY < 200-day SMA
  S3 crash:      SPY 20d vol > trailing 756d 90th percentile
  V3 mania:      63d pre-screen census > trailing 756d 90th percentile
  FC habitat:    flagged-cohort/SPY ratio below its value 126 days ago
                 (only while the cohort was populated)

Position size = 10% x (4 - hostile_votes)/4. Four hostile = no entry.
Prints the hostile-count trade audit, runs baseline vs ensemble in both
periods, writes equity charts.  Run: python lppl_softvote.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel, metrics, simulate


def build_votes(panel: dict, cfg: dict) -> np.ndarray:
    cal = panel['calendar']
    n = len(cal)
    spy = panel['spy_close']
    lb = cfg['lppl']['rs_lookback']

    s1 = (spy < spy.rolling(200).mean()).to_numpy()
    vol = spy.pct_change().rolling(20).std()
    s3 = (vol > vol.rolling(756).quantile(0.90)).to_numpy()

    flags = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'lppl_flags.parquet')
    census = flags.groupby('date').size().reindex(cal).fillna(0.0).rolling(63).sum()
    v3 = (census > census.rolling(756).quantile(0.90)).to_numpy()

    tickers = sorted(panel['arrays'])
    tpos = {t: j for j, t in enumerate(tickers)}
    cal_pos = {d: i for i, d in enumerate(cal)}
    close = np.column_stack([panel['arrays'][t]['close_f'] for t in tickers])
    qual = flags[flags['votes'] >= cfg['lppl']['min_votes_loose']]
    member = np.zeros((n, len(tickers)), dtype=bool)
    for r in qual.itertuples():
        i = cal_pos.get(r.date)
        j = tpos.get(r.ticker)
        if i is not None and j is not None:
            member[i:i + lb, j] = True
    idx_ret = np.zeros(n)
    for i in range(1, n):
        m = member[i - 1]
        if m.any():
            with np.errstate(invalid='ignore'):
                r = close[i, m] / close[i - 1, m] - 1
            r = r[np.isfinite(r)]
            idx_ret[i] = r.mean() if len(r) else 0.0
    ratio = np.cumprod(1 + idx_ret) / spy.to_numpy()
    populated = (pd.Series(member.sum(axis=1) >= 10, index=cal)
                 .rolling(lb).mean() >= 0.5).to_numpy()
    fc = np.zeros(n, dtype=bool)
    fc[lb:] = (ratio[lb:] < ratio[:-lb]) & populated[lb:]

    return s1.astype(int) + s3.astype(int) + v3.astype(int) + fc.astype(int)


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    panel = load_panel(cfg)
    cal = panel['calendar']
    cal_pos = {d: i for i, d in enumerate(cal)}

    votes = build_votes(panel, cfg)
    mult = (4 - votes) / 4.0

    print('hostile-count audit of the baseline trades:')
    for p in ['dev', 'test']:
        t = pd.read_csv(f'results/lppl_{p}_trades_lppl_dip2.csv',
                        parse_dates=['entry_date'])
        h = np.array([votes[cal_pos[d] - 1] for d in t.entry_date])
        g = t.groupby(h)['ret_net'].agg(['count', 'mean'])
        g.index.name = f'{p}: hostile votes'
        print(g.to_string(float_format=lambda x: f'{x:.4f}'))

    today = str(cal[-1].date())
    fmt = lambda x: f'{x:.4f}'
    for pname, period in [('dev', (bt['start'], bt['dev_end'])),
                          ('test', (bt['test_start'], today))]:
        rows, curves = {}, {}
        for label, sm in [('baseline', None), ('softvote', mult)]:
            trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2', period,
                                               size_mult=sm)
            rows[label] = metrics(trades, equity, avg_inv)
            curves[label] = equity
        smry = pd.DataFrame(rows).T
        smry.to_csv(results / f'lppl_softvote_{pname}.csv')
        print(f'\n=== {pname} ===')
        print(smry.to_string(float_format=fmt))

        spy = panel['spy_close']
        spy = spy[(spy.index >= period[0]) & (spy.index <= period[1])]
        plt.figure(figsize=(11, 6))
        for label, eq in curves.items():
            plt.plot(eq.index, eq / eq.iloc[0], label=label)
        plt.plot(spy.index, spy / spy.iloc[0], label='SPY', color='gray',
                 linestyle='--')
        plt.yscale('log')
        plt.title(f'soft-vote sizing ensemble vs baseline, {pname} period')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(results / f'lppl_softvote_{pname}.png', dpi=120)
        plt.close()
    print(f'\ncharts -> {results}/lppl_softvote_dev.png, lppl_softvote_test.png')


if __name__ == '__main__':
    main()
