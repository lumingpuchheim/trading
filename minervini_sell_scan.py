"""How much of a position should be realised when it reaches +20%?

Scans `strength_sell_frac` on the standing configuration v5r, changing
nothing else: same signals, same 10% slots, same exits, same market
light. 0.00 means never take a partial and let every position run to a
trend exit; 1.00 means sell the WHOLE position at +20%.

Reported per period: funded bets, the geometric mean euro returned per
euro committed (the honest per-bet unit -- see minervini_stats.py), the
total return and the max drawdown. No arithmetic means, no win rates.

POST-HOC, and the caveat is the same one the bet-size scan carries: both
periods have been seen many times, so a scan over them reads noise as
readily as signal. The point is the SHAPE of the curve and whether the
two periods agree, not the best cell in the table.

Note on 1.00: selling everything at +20% is a profit cap, and DECISIONS.md
records E1's rejection as permanent for exactly that reason -- the edge
lives in the +50-100% right tail. It is scanned anyway so the curve has
its endpoint, not because it is on the table.

Run: python minervini_sell_scan.py
"""

import copy

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config
from minervini_backtest import apply_v5, build_panel, pool_by_day, simulate

FRACTIONS = [0.00, 0.10, 0.20, 0.25, 0.33, 0.40, 0.50, 0.60, 0.67,
             0.75, 0.90, 1.00]


def euro_per_bet(trades: pd.DataFrame) -> float:
    """Geometric mean of the euro returned per euro committed, over
    positions. Uses the recorded per-row share weight, so it is exact for
    any split ratio rather than assuming halves."""
    if not len(trades):
        return np.nan
    t = trades.copy()
    t['pos_id'] = t['ticker'] + '|' + t['entry_date'].astype(str)
    mult = t.groupby('pos_id').apply(
        lambda d: float((d['weight'] * (1.0 + d['ret_net'])).sum()),
        include_groups=False)
    mult = mult[mult > 0]
    return float(np.exp(np.mean(np.log(mult))))


def main() -> None:
    base = apply_v5(load_config())
    base['minervini_trading']['reentry_fast'] = True          # v5r keeps E3
    panel = build_panel(base, v5=True)
    cal = panel['calendar']
    pool = pool_by_day(panel['watch'])
    periods = {}
    for name, a, b in [('dev', '2007-01-01', '2018-12-31'),
                       ('test', '2019-01-01', str(cal[-1].date()))]:
        j0 = int(cal.searchsorted(pd.Timestamp(a)))
        j1 = int(cal.searchsorted(pd.Timestamp(b), side='right')) - 1
        periods[name] = (j0, j1)

    rows = []
    for f in FRACTIONS:
        cfg = copy.deepcopy(base)
        cfg['minervini_trading']['strength_sell_frac'] = f
        row = {'sell_frac': f}
        for per, pr in periods.items():
            tr, eq, inv, _ = simulate(panel, cfg, pr, pool_days=pool, moc=True)
            pos = (tr['ticker'] + '|' + tr['entry_date'].astype(str)).nunique()
            row[f'{per}_bets'] = pos
            row[f'{per}_euro'] = euro_per_bet(tr)
            row[f'{per}_total'] = eq.iloc[-1] / eq.iloc[0] - 1
            row[f'{per}_maxdd'] = float((eq / eq.cummax() - 1).min())
        rows.append(row)
        print(f"sell {f:.0%} at +20%  |  dev euro/bet "
              f"{row['dev_euro']:.4f} total {row['dev_total']:+7.1%}  |  "
              f"test euro/bet {row['test_euro']:.4f} total "
              f"{row['test_total']:+7.1%}", flush=True)

    d = pd.DataFrame(rows)
    out = ROOT / load_config()['backtest']['results_dir']
    d.to_csv(out / 'minervini_sell_scan.csv', index=False)

    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    for per, col in (('dev', 'tab:blue'), ('test', 'tab:red')):
        ax[0].plot(d['sell_frac'] * 100, (d[f'{per}_euro'] - 1) * 100,
                   'o-', color=col, label=per)
        ax[1].plot(d['sell_frac'] * 100, d[f'{per}_total'] * 100,
                   'o-', color=col, label=per)
    ax[0].set_ylabel('geometric mean, % per bet')
    ax[1].set_ylabel('total return, %')
    for a in ax:
        a.set_xlabel('% of the position sold at +20%')
        a.axvline(50, color='k', ls=':', lw=1)
        a.grid(alpha=0.3)
        a.legend()
    ax[0].set_title('euro returned per euro committed')
    ax[1].set_title('portfolio total (10% slots, unchanged)')
    plt.tight_layout()
    plt.savefig(out / 'minervini_sell_scan.png', dpi=120)
    plt.close()
    print(f'\n-> {out}/minervini_sell_scan.csv and .png')


if __name__ == '__main__':
    main()
