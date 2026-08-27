"""One calendar year of the Minervini system, up close.

Runs the market-on-close strategy over a single year (flat at the start,
liquidated at the end), charts the equity path against SPY with every
entry and exit marked, and prints the trades and the monthly opportunity
set (setup days and entries). Default year: 2021, the year Minervini won
the US Investing Championship at +334.8%.

Run: python minervini_year.py            # v3 rules, 2021
     python minervini_year.py 2023       # another year
     python minervini_year.py --v2       # the pre-fix rules
"""

import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config
from minervini_backtest import apply_v3, build_panel, pool_by_day, simulate


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    year = int(args[0]) if args else 2021
    use_v2 = '--v2' in sys.argv
    ver = 'v2' if use_v2 else 'v3'

    cfg = load_config()
    if not use_v2:
        cfg = apply_v3(cfg)
    panel = build_panel(cfg, v3=not use_v2)
    cal = panel['calendar']
    j0 = int(cal.searchsorted(pd.Timestamp(f'{year}-01-01')))
    j1 = int(cal.searchsorted(pd.Timestamp(f'{year}-12-31'), side='right')) - 1

    trades, equity, avg_inv, _ = simulate(
        panel, cfg, (j0, j1), pool_days=pool_by_day(panel['setup']), moc=True)

    days = cal[j0:j1 + 1]
    setups = pd.Series(panel['setup'][j0:j1 + 1].sum(axis=1), index=days)
    monthly = pd.DataFrame({
        'setup_days': setups.groupby(setups.index.month).sum().astype(int)})
    if len(trades):
        ent = pd.to_datetime(trades['entry_date']).dt.month.value_counts()
        monthly['entries'] = ent.reindex(monthly.index).fillna(0).astype(int)

    total = equity.iloc[-1] / equity.iloc[0] - 1
    spy = panel['spy_close'].iloc[j0:j1 + 1]
    spy_tot = spy.iloc[-1] / spy.iloc[0] - 1
    print(f'=== {ver.upper()} rules, {year} ===')
    print(f'strategy {total:+.1%} on {len(trades)} trades, '
          f'avg invested {avg_inv:.1%} | SPY {spy_tot:+.1%}')
    if len(trades):
        t = trades.sort_values('entry_date')
        print(t[['ticker', 'entry_date', 'exit_date', 'entry_px', 'exit_px',
                 'days_held', 'ret_net', 'exit_reason']]
              .to_string(index=False,
                         formatters={'ret_net': '{:+.1%}'.format,
                                     'entry_px': '{:.2f}'.format,
                                     'exit_px': '{:.2f}'.format}))
    print('\nopportunity set by month (universe-wide setup stock-days):')
    print(monthly.to_string())

    fig, (ax, axs) = plt.subplots(
        2, 1, figsize=(13, 8), sharex=True,
        gridspec_kw={'height_ratios': [3, 1], 'hspace': 0.08})
    ax.plot(days, equity / equity.iloc[0], color='crimson', lw=1.6,
            label=f'MINERVINI {ver} ({total:+.1%}, {len(trades)} trades, '
                  f'{avg_inv:.0%} avg invested)')
    ax.plot(spy.index, spy / spy.iloc[0], color='gray', ls='--',
            label=f'SPY ({spy_tot:+.1%})')
    for r in trades.itertuples():
        ax.axvline(pd.Timestamp(r.entry_date), color='green', alpha=0.25, lw=1)
        ax.axvline(pd.Timestamp(r.exit_date), color='red', alpha=0.18, lw=1)
    ax.set_title(f'{year}: the Minervini {ver} system vs the market '
                 f'(green lines = buys, red = sells)')
    ax.legend()
    ax.grid(alpha=0.3)
    axs.bar(setups.index, setups.values, color='steelblue', width=1.0)
    axs.set_ylabel('setups/day')
    axs.grid(alpha=0.3)
    fig.tight_layout()
    out = ROOT / cfg['backtest']['results_dir'] / f'minervini_{ver}_{year}.png'
    fig.savefig(out, dpi=120)
    plt.close(fig)
    print(f'\nchart -> {out}')


if __name__ == '__main__':
    main()
