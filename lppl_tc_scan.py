"""Tune the tc exit of lppl_dip2 — with discipline.

The knob: exit at (last estimated tc) + shift trading days. Negative shift
leaves before the predicted critical time, positive overstays it.

Protocol, fixed before running: scan shifts on the DEVELOPMENT period only,
select the shift with the highest dev t-stat, then run the TEST period once,
for the selected shift only. The rest of the test surface stays unseen.

Run:  python lppl_tc_scan.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel, metrics, simulate

SHIFTS = [-15, -10, -5, 0, 5, 10, 15, 20]


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    panel = load_panel(cfg)
    dev = (bt['start'], bt['dev_end'])
    test = (bt['test_start'], str(panel['calendar'][-1].date()))

    rows, curves = [], {}
    for s in SHIFTS:
        trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2', dev,
                                           tc_shift=s)
        rows.append({'tc_shift': s, **metrics(trades, equity, avg_inv)})
        curves[s] = equity
    scan = pd.DataFrame(rows)
    scan.to_csv(results / 'lppl_tc_scan_dev.csv', index=False)
    cols = ['tc_shift', 'total_return', 'ann_return', 'max_drawdown',
            'n_trades', 'win_rate', 'avg_trade', 't_stat']
    fmt = lambda x: f'{x:.4f}'
    print('=== dev: tc-shift scan ===')
    print(scan[cols].to_string(index=False, float_format=fmt))

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    ax = axes[0]
    ax.plot(scan['tc_shift'], scan['ann_return'] * 100, marker='o',
            color='tab:blue', label='annualised return (%)')
    ax.set_xlabel('tc exit shift (trading days)')
    ax.set_ylabel('annualised return (%)', color='tab:blue')
    ax.set_ylim(0, 12)
    ax2 = ax.twinx()
    ax2.plot(scan['tc_shift'], scan['t_stat'], marker='s',
             color='tab:red', label='t-stat')
    ax2.set_ylabel('t-stat of avg trade', color='tab:red')
    ax2.set_ylim(0, 4)
    ax2.axhline(2.0, color='tab:red', ls=':', lw=0.8)
    ax.set_title('dev metrics vs tc shift (flat = nothing to tune)')
    ax.grid(alpha=0.3)

    for s, eq in curves.items():
        axes[1].plot(eq.index, eq / eq.iloc[0], lw=1,
                     label=f'shift {s:+d}', alpha=0.8)
    axes[1].set_yscale('log')
    axes[1].set_title('dev equity curves, one per tc shift')
    axes[1].legend(fontsize=8, ncol=2)
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(results / 'lppl_tc_scan.png', dpi=120)
    print(f'chart -> {results / "lppl_tc_scan.png"}')

    best = int(scan.loc[scan['t_stat'].idxmax(), 'tc_shift'])
    print(f'\nselected by highest dev t-stat: shift = {best}')
    if best == 0:
        print('selected shift is the baseline; test period already known, '
              'no new test run needed')
        return
    trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2', test,
                                       tc_shift=best)
    m = metrics(trades, equity, avg_inv)
    pd.DataFrame([{'tc_shift': best, **m}]).to_csv(
        results / 'lppl_tc_scan_test_selected.csv', index=False)
    print(f'\n=== test, selected shift {best} ONLY ===')
    print(pd.DataFrame([m]).to_string(index=False, float_format=fmt))


if __name__ == '__main__':
    main()
