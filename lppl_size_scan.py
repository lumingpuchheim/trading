"""Bet-size scan for lppl_dip2: run the identical strategy at a range of
position fractions (whole shares only, $100k start) and report how return,
drawdown, and worst-trade portfolio impact scale. Also reports the win rate
per calendar year next to SPY's return, to show how (un)stable the win rate
is across good and bad years.

Run after lppl_detect.py:  python lppl_size_scan.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel, metrics, simulate

FRACTIONS = [0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.125, 0.15, 0.20]


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    results.mkdir(exist_ok=True)
    panel = load_panel(cfg)
    today = str(panel['calendar'][-1].date())
    periods = {'dev': (bt['start'], bt['dev_end']),
               'test': (bt['test_start'], today)}

    rows = []
    trades10 = {}
    for pname, period in periods.items():
        for f in FRACTIONS:
            trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2', period,
                                               fraction=f)
            m = metrics(trades, equity, avg_inv)
            worst_hit = (trades['ret_net'].min() * f if len(trades) else np.nan)
            rows.append({'period': pname, 'fraction': f, **m,
                         'worst_trade_portfolio_hit': worst_hit})
            if f == 0.10:
                trades10[pname] = trades
        print(f'{pname} done')

    scan = pd.DataFrame(rows)
    scan.to_csv(results / 'lppl_size_scan.csv', index=False)
    cols = ['fraction', 'total_return', 'ann_return', 'max_drawdown',
            'n_trades', 'win_rate', 'avg_invested', 'worst_trade_portfolio_hit']
    fmt = lambda x: f'{x:.4f}'
    for pname in periods:
        print(f'\n=== {pname}: bet-size scan (whole shares, $100k start) ===')
        print(scan[scan['period'] == pname][cols].to_string(index=False,
                                                            float_format=fmt))

    # win rate by calendar year (at the 10% size) next to SPY's year return
    spy = panel['spy_close']
    spy_year = spy.resample('YE').last().pct_change()
    spy_year.index = spy_year.index.year
    print('\n=== lppl_dip2 win rate by entry year (10% size) ===')
    yr_rows = []
    for pname, trades in trades10.items():
        trades = trades.copy()
        trades['year'] = pd.to_datetime(trades['entry_date']).dt.year
        for y, g in trades.groupby('year'):
            yr_rows.append({'period': pname, 'year': y, 'n': len(g),
                            'win_rate': (g['ret_net'] > 0).mean(),
                            'avg_trade': g['ret_net'].mean(),
                            'spy_year': spy_year.get(y, np.nan)})
    yr = pd.DataFrame(yr_rows).sort_values('year')
    yr.to_csv(results / 'lppl_winrate_by_year.csv', index=False)
    print(yr.to_string(index=False, float_format=fmt))

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for pname, marker in [('dev', 'o'), ('test', 's')]:
        s = scan[scan['period'] == pname]
        axes[0].plot(s['fraction'] * 100, s['ann_return'] * 100,
                     marker=marker, label=pname)
        axes[1].plot(s['fraction'] * 100, s['max_drawdown'] * 100,
                     marker=marker, label=pname)
    axes[0].set_xlabel('position size (% of portfolio)')
    axes[0].set_ylabel('annualised return (%)')
    axes[0].set_title('return vs bet size')
    axes[1].set_xlabel('position size (% of portfolio)')
    axes[1].set_ylabel('max drawdown (%)')
    axes[1].set_title('drawdown vs bet size')
    for ax in axes:
        ax.grid(alpha=0.3)
        ax.legend()
    fig.suptitle('lppl_dip2 bet-size scan (10 slots, whole shares, $100k)')
    fig.tight_layout()
    fig.savefig(results / 'lppl_size_scan.png', dpi=120)
    print(f'\nwritten: {results}/lppl_size_scan.csv, lppl_winrate_by_year.csv, '
          'lppl_size_scan.png')


if __name__ == '__main__':
    main()
