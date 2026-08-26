"""Can hostile-time winners be told from hostile-time losers ex ante?

For every baseline trade entered while >= 1 regime voter was hostile,
build the ex-ante feature vector (voters, hostility age, market state,
stock state) plus the SIMULTANEOUS-SPY comparison:

  rel_dip  = stock's 20d drawdown-from-high  minus  SPY's, same day
             (how much of the dip is the stock's own vs the market's)
  rel_ret5 = stock 5-day return minus SPY 5-day return

Prints winners-vs-losers feature means and age/rel_dip tercile returns,
dev and test separately. A feature only counts if its sign agrees across
both periods.  Run: python lppl_hostile_audit.py
"""

import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel
from lppl_softvote import build_votes


def main() -> None:
    cfg = load_config()
    panel = load_panel(cfg)
    cal = panel['calendar']
    n = len(cal)
    cal_pos = {d: i for i, d in enumerate(cal)}
    spy = panel['spy_close']

    votes = build_votes(panel, cfg)
    sma = spy.rolling(200).mean()
    spy_dist = (spy / sma - 1).to_numpy()
    vol_ratio = (spy.pct_change().rolling(20).std()
                 / spy.pct_change().rolling(20).std().rolling(756).median()).to_numpy()
    spy_np = spy.to_numpy()
    spy_hi20 = spy.rolling(20).max().to_numpy()
    spy_dip = 1 - spy_np / spy_hi20
    spy_r5 = spy.pct_change(5).to_numpy()

    age = np.zeros(n)
    for i in range(1, n):
        age[i] = age[i - 1] + 1 if votes[i] >= 1 else 0

    rows = []
    for p in ['dev', 'test']:
        t = pd.read_csv(ROOT / 'results' / f'lppl_{p}_trades_lppl_dip2.csv',
                        parse_dates=['entry_date'])
        for r in t.itertuples():
            i = cal_pos[r.entry_date] - 1
            a = panel['arrays'][r.ticker]
            c = a['close_f']
            hi20 = pd.Series(c[max(0, i - 19):i + 1]).max()
            dip = 1 - c[i] / hi20
            r5 = c[i] / c[i - 5] - 1 if i >= 5 and c[i - 5] > 0 else np.nan
            rows.append(dict(
                period=p, ret=r.ret_net, win=r.ret_net > 0, hostile=votes[i] >= 1,
                h=votes[i], age=age[i], spy_dist=spy_dist[i],
                vol_ratio=vol_ratio[i], dip=dip,
                rel_dip=dip - spy_dip[i], rel_ret5=r5 - spy_r5[i]))
    df = pd.DataFrame(rows)

    for p in ['dev', 'test']:
        for scope, d in [('hostile-time', df[(df.period == p) & df.hostile]),
                         ('ALL', df[df.period == p])]:
            print(f'=== {p} / {scope}: {len(d)} trades, {d.win.mean():.0%} winners ===')
            cols = ['h', 'age', 'spy_dist', 'vol_ratio', 'dip', 'rel_dip', 'rel_ret5']
            cmp = d.groupby('win')[cols].mean().T
            cmp.columns = ['losers', 'winners'][:len(cmp.columns)]
            print(cmp.to_string(float_format=lambda x: f'{x:.4f}'))
            for feat in ['age', 'rel_dip']:
                q = d[feat].quantile([1 / 3, 2 / 3]).to_numpy()
                b = np.digitize(d[feat], q)
                g = d.groupby(b)['ret'].agg(['count', 'mean'])
                g.index = [f'{feat} low', f'{feat} mid', f'{feat} high'][:len(g)]
                print(g.to_string(float_format=lambda x: f'{x:.4f}'))
            print()


if __name__ == '__main__':
    main()
