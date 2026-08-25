"""Scan the stop-loss depth of lppl_dip2 — with discipline.

Protocol, fixed before running: scan stops on the DEVELOPMENT period only,
select by highest dev t-stat, then run the TEST period once for the
selected value only (the baseline 8% test result is already known).

Run:  python lppl_stop_scan.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel, metrics, simulate

STOPS = [0.96, 0.94, 0.92, 0.90, 0.88, 0.85]  # 4% .. 15% loss


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    panel = load_panel(cfg)
    dev = (bt['start'], bt['dev_end'])
    test = (bt['test_start'], str(panel['calendar'][-1].date()))

    rows = []
    for s in STOPS:
        trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2', dev,
                                           stop_loss=s)
        rows.append({'stop_pct': round((1 - s) * 100, 1),
                     **metrics(trades, equity, avg_inv)})
    scan = pd.DataFrame(rows)
    scan.to_csv(results / 'lppl_stop_scan_dev.csv', index=False)
    cols = ['stop_pct', 'total_return', 'ann_return', 'max_drawdown',
            'n_trades', 'win_rate', 'avg_winner', 'avg_loser', 'avg_trade',
            't_stat']
    fmt = lambda x: f'{x:.4f}'
    print('=== dev: stop-loss scan ===')
    print(scan[cols].to_string(index=False, float_format=fmt))

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(scan['stop_pct'], scan['ann_return'] * 100, marker='o',
            color='tab:blue', label='annualised return (%)')
    ax.plot(scan['stop_pct'], -scan['max_drawdown'] * 100, marker='^',
            color='tab:green', label='max drawdown (%)')
    ax.set_xlabel('stop-loss depth (%)')
    ax.set_ylabel('percent')
    ax2 = ax.twinx()
    ax2.plot(scan['stop_pct'], scan['t_stat'], marker='s', color='tab:red',
             label='t-stat')
    ax2.set_ylabel('t-stat', color='tab:red')
    ax.axvline(8, color='gray', ls=':', lw=1)
    ax.set_title('dev: stop depth scan (dotted line = 8% baseline)')
    ax.grid(alpha=0.3)
    ax.legend(loc='upper left')
    fig.tight_layout()
    fig.savefig(results / 'lppl_stop_scan.png', dpi=120)

    best_row = scan.loc[scan['t_stat'].idxmax()]
    best = float(1 - best_row['stop_pct'] / 100)
    print(f'\nselected by highest dev t-stat: {best_row["stop_pct"]:.0f}% stop')
    if abs(best - cfg['lppl_trading']['stop_loss']) < 1e-9:
        print('selected stop is the baseline; test already known, no new run')
        return
    trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2', test,
                                       stop_loss=best)
    m = metrics(trades, equity, avg_inv)
    pd.DataFrame([{'stop_pct': best_row['stop_pct'], **m}]).to_csv(
        results / 'lppl_stop_scan_test_selected.csv', index=False)
    print(f'\n=== test, selected stop ONLY ===')
    print(pd.DataFrame([m]).to_string(index=False, float_format=fmt))


if __name__ == '__main__':
    main()
