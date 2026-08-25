"""Tune the tc exit of lppl_dip2 — with discipline.

The knob: exit at (last estimated tc) + shift trading days. Negative shift
leaves before the predicted critical time, positive overstays it.

Protocol, fixed before running: scan shifts on the DEVELOPMENT period only,
select the shift with the highest dev t-stat, then run the TEST period once,
for the selected shift only. The rest of the test surface stays unseen.

Run:  python lppl_tc_scan.py
"""

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

    rows = []
    for s in SHIFTS:
        trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2', dev,
                                           tc_shift=s)
        rows.append({'tc_shift': s, **metrics(trades, equity, avg_inv)})
    scan = pd.DataFrame(rows)
    scan.to_csv(results / 'lppl_tc_scan_dev.csv', index=False)
    cols = ['tc_shift', 'total_return', 'ann_return', 'max_drawdown',
            'n_trades', 'win_rate', 'avg_trade', 't_stat']
    fmt = lambda x: f'{x:.4f}'
    print('=== dev: tc-shift scan ===')
    print(scan[cols].to_string(index=False, float_format=fmt))

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
