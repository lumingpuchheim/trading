"""Compare: SPY, baseline, soft-vote ensemble incl. the sector cohort as a
fifth voter, and the sector cohort as a hard entry gate.

  baseline          lppl_dip2, 10% positions
  softvote_c3       size = 10% x (5 - hostile)/5 over
                    {S1 200SMA, S3 vol, V3 mania, FC flagged-cohort, C3 sector}
  hardgate_c3       no new entries while the sector-cohort gate is hostile

Run: python lppl_c3_compare.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel, metrics, simulate
from lppl_softvote import build_votes

GROWTH = {'Information Technology', 'Communication Services',
          'Consumer Discretionary', 'Health Care'}


def sector_hostile(panel: dict, cfg: dict) -> np.ndarray:
    cal = panel['calendar']
    n = len(cal)
    lb = cfg['lppl']['rs_lookback']
    sec = pd.read_csv(ROOT / 'data' / 'sectors.csv')
    ticks = [t for t in panel['arrays']
             if t in set(sec[sec.sector.isin(GROWTH)].ticker)]
    close = np.column_stack([panel['arrays'][t]['close_f'] for t in ticks])
    with np.errstate(invalid='ignore'):
        rets = close[1:] / close[:-1] - 1
    idx_ret = np.zeros(n)
    idx_ret[1:] = np.nan_to_num(
        np.nanmean(np.where(np.isfinite(rets), rets, np.nan), axis=1))
    ratio = np.cumprod(1 + idx_ret) / panel['spy_close'].to_numpy()
    hostile = np.zeros(n, dtype=bool)
    hostile[lb:] = ratio[lb:] < ratio[:-lb]
    return hostile


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    panel = load_panel(cfg)
    cal = panel['calendar']

    c3 = sector_hostile(panel, cfg)
    votes5 = build_votes(panel, cfg) + c3.astype(int)
    mult5 = (5 - votes5) / 5.0
    gate3 = ~c3

    today = str(cal[-1].date())
    fmt = lambda x: f'{x:.4f}'
    runs = [('baseline', {}), ('softvote_c3', {'size_mult': mult5}),
            ('hardgate_c3', {'entry_gate': gate3})]
    for pname, period in [('dev', (bt['start'], bt['dev_end'])),
                          ('test', (bt['test_start'], today))]:
        rows, curves = {}, {}
        for label, kw in runs:
            trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2',
                                               period, **kw)
            rows[label] = metrics(trades, equity, avg_inv)
            curves[label] = equity
        sm = pd.DataFrame(rows).T
        sm.to_csv(results / f'lppl_c3_{pname}.csv')
        print(f'=== {pname} ===')
        print(sm.to_string(float_format=fmt))

        spy = panel['spy_close']
        spy = spy[(spy.index >= period[0]) & (spy.index <= period[1])]
        plt.figure(figsize=(11, 6))
        plt.plot(spy.index, spy / spy.iloc[0], label='SPY', color='gray',
                 linestyle='--')
        for label, eq in curves.items():
            plt.plot(eq.index, eq / eq.iloc[0], label=label)
        plt.yscale('log')
        plt.title(f'sector-cohort variants vs baseline vs SPY, {pname}')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(results / f'lppl_c3_{pname}.png', dpi=120)
        plt.close()
    print(f'charts -> {results}/lppl_c3_dev.png, lppl_c3_test.png')


if __name__ == '__main__':
    main()
