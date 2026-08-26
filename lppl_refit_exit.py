"""Refit-while-held exit: entries identical to baseline lppl_dip2, but held
positions keep being evaluated every refit day (no pre-screen — live this
costs <= max_positions fits per day), and fresh votes>=2 evaluations roll
the tc exit clock forward. Deconfounds the exit half of the rejected
watchlist experiment: the entry flag set stays the pre-screen-only cache.

Requires data/lppl_flags_watchlist.parquet (the preserved watchlist run)
as the dense evaluation stream. Run: python lppl_refit_exit.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel, metrics, simulate


def attach_refit_tc(panel: dict, cfg: dict) -> None:
    """Per ticker, a per-day tc pointer built from the dense (watchlist)
    evaluation stream with the same votes>=2 + persistence semantics the
    entry flags use. Stored as arrays[t]['tcw']."""
    g = cfg['lppl']
    cal = panel['calendar']
    cal_pos = {d: i for i, d in enumerate(cal)}
    n = len(cal)
    wf = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'lppl_flags_watchlist.parquet')
    by_t = {t: gg.sort_values('date') for t, gg in wf.groupby('ticker')}
    for t, a in panel['arrays'].items():
        tcw = np.full(n, -1, dtype=np.int64)
        prev_ok = 0
        gg = by_t.get(t)
        if gg is not None:
            for r in gg.itertuples():
                j = cal_pos.get(r.date)
                if j is None:
                    continue
                if r.votes >= g['min_votes_loose']:
                    prev_ok += 1
                    if prev_ok >= g['persistence'] and np.isfinite(r.tc_ahead):
                        until = min(n, j + g['refit_every'])
                        tcw[j:until] = j + int(round(r.tc_ahead))
                else:
                    prev_ok = 0
        a['tcw'] = tcw


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    panel = load_panel(cfg)
    attach_refit_tc(panel, cfg)

    today = str(panel['calendar'][-1].date())
    fmt = lambda x: f'{x:.4f}'
    runs = [('baseline', {}), ('refit_exit', {'tc_roll_key': 'tcw'})]
    for pname, period in [('dev', (bt['start'], bt['dev_end'])),
                          ('test', (bt['test_start'], today))]:
        rows, curves = {}, {}
        for label, kw in runs:
            trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2',
                                               period, **kw)
            rows[label] = metrics(trades, equity, avg_inv)
            curves[label] = equity
            trades.sort_values('entry_date').to_csv(
                results / f'lppl_refit_exit_{pname}_trades_{label}.csv',
                index=False)
            if label == 'refit_exit':
                smci = trades[trades['ticker'] == 'SMCI']
                if len(smci):
                    print(f'{pname} SMCI under refit_exit:')
                    print(smci.to_string(index=False))
        sm = pd.DataFrame(rows).T
        sm.to_csv(results / f'lppl_refit_exit_{pname}.csv')
        print(f'=== {pname} ===')
        print(sm.to_string(float_format=fmt))
        for label, _ in runs:
            tdf = pd.read_csv(results / f'lppl_refit_exit_{pname}_trades_{label}.csv')
            if len(tdf):
                print(f'  {label}: exits {tdf["exit_reason"].value_counts().to_dict()}, '
                      f'median hold {tdf["days_held"].median():.0f}d')

        spy = panel['spy_close']
        spy = spy[(spy.index >= period[0]) & (spy.index <= period[1])]
        plt.figure(figsize=(11, 6))
        plt.plot(spy.index, spy / spy.iloc[0], label='SPY', color='gray',
                 linestyle='--')
        for label, eq in curves.items():
            plt.plot(eq.index, eq / eq.iloc[0], label=label)
        plt.yscale('log')
        plt.title(f'refit-while-held exit vs baseline, {pname}')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(results / f'lppl_refit_exit_{pname}.png', dpi=120)
        plt.close()
    print(f'charts -> {results}/lppl_refit_exit_dev.png, lppl_refit_exit_test.png')


if __name__ == '__main__':
    main()
