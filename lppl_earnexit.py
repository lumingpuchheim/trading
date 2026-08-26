"""Pre-earnings ejector seat: if an earnings report lands on the coming
gap night and the position is down more than X% from entry, sell at the
pre-report close instead of risking the gap (stops do not hold across
gaps: earnings-night stops filled at -12.6% vs -9.6% in dev).

Protocol (declared before running): X scanned on DEV ONLY over
{0%, 2%, 4%, 6%} below entry; dev-best X (by t-stat) gets ONE test run.
'always' (sell before every report regardless of P&L) is a reference row
excluded from selection — predicted tail-amputating, since winners sit
through 1.5-1.9 reports. AMC reports (hour >= 15) eject at that day's
close, BMO at the prior day's close. Coverage: earnings dates exist only
for the 429 historically-traded tickers; the rule cannot fire elsewhere.
Expectation: the addressable pool is small (13%/6% of stops are
earnings-adjacent); a null result is likely and welcome.

Run: python lppl_earnexit.py
"""

import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel, metrics, simulate


def build_masks(cal) -> dict[str, np.ndarray]:
    earn = pd.read_parquet(ROOT / 'data' / 'earnings_dates.parquet')
    n = len(cal)
    masks = {}
    for t, g in earn.groupby('ticker'):
        m = np.zeros(n, bool)
        for r in g.itertuples():
            side = 'right' if r.hour >= 15 else 'left'
            j = cal.searchsorted(r.date, side=side) - 1
            if 0 <= j < n:
                m[j] = True
        masks[t] = m
    return masks


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    panel = load_panel(cfg)
    masks = build_masks(panel['calendar'])
    today = str(panel['calendar'][-1].date())
    fmt = lambda x: f'{x:.4f}'

    xs = [0.00, 0.02, 0.04, 0.06]
    dev_rows = {}
    period = (bt['start'], bt['dev_end'])
    for label, kw in [('baseline', {})] + \
            [(f'eject_x{int(x * 100)}', {'earn_exit': (masks, 1 - x)})
             for x in xs] + \
            [('always (ref)', {'earn_exit': (masks, 1e9)})]:
        trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2', period, **kw)
        m = metrics(trades, equity, avg_inv)
        m['earn_exits'] = int((trades['exit_reason'] == 'earn').sum()) \
            if len(trades) else 0
        dev_rows[label] = m
    sm = pd.DataFrame(dev_rows).T
    sm.to_csv(results / 'lppl_earnexit_dev.csv')
    print('=== dev (selection) ===')
    print(sm.to_string(float_format=fmt))

    cand = {k: v for k, v in dev_rows.items()
            if k.startswith('eject_x')}
    best = max(cand, key=lambda k: cand[k]['t_stat'])
    x_best = int(best.split('x')[1]) / 100
    print(f'\ndev-best (by t-stat, always excluded): {best}')

    period = (bt['test_start'], today)
    test_rows = {}
    for label, kw in [('baseline', {}),
                      (best, {'earn_exit': (masks, 1 - x_best)})]:
        trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2', period, **kw)
        m = metrics(trades, equity, avg_inv)
        m['earn_exits'] = int((trades['exit_reason'] == 'earn').sum()) \
            if len(trades) else 0
        test_rows[label] = m
        if label == best and m['earn_exits']:
            e = trades[trades['exit_reason'] == 'earn']
            print(f'\n[test] {best} earn exits: n={len(e)}, '
                  f'avg ret {e["ret_net"].mean():+.4f}')
    sm = pd.DataFrame(test_rows).T
    sm.to_csv(results / 'lppl_earnexit_test.csv')
    print('\n=== test (single audit run) ===')
    print(sm.to_string(float_format=fmt))


if __name__ == '__main__':
    main()
