"""Do the three RS ranking keys predict what a euro becomes?

`simulate()` fills free slots by sorting each day's candidates on
(rsl_hi, weak, rs, ticker) -- spec 10.2, `rank_selection`. Those keys have
never been checked against outcomes. If they carry no information the
ranking is an elaborate coin flip; if they do, any filter is competing
with a selector that already works.

The keys are RELATIVE, so every measurement here is WITHIN A DAY. A raw
`rs` of +40% means one thing in 2009 and another in 2017; what the sorter
actually uses is the ordering among the candidates available that morning.
So candidates are grouped by day, ranked inside the day, and outcomes are
read off the rank -- never off the raw level.

The table that matters is the last one: geo y by the slot the sorter
would have handed the bet. If position 1 does not beat position 10, the
ranking is not selecting.

Usage
    python rs_keys_ev.py                # the whole record
"""

import sys

import numpy as np
import pandas as pd

from geostats import geo_mean_per_euro
from lppl_backtest import ROOT, load_config
from minervini_backtest import apply_v5, build_panel

LEDGER = ROOT / 'results' / 'minervini_bets_v5r.csv'


def by_bucket(df, col, y, nb=10, label=''):
    """Geometric mean y by within-day quantile of `col`, high bucket =
    best rank. Geometric because y is a multiple per bet, and this table
    is read against the portfolio's own per-bet figure: two averages of
    different kinds cannot be compared, which is exactly the mistake this
    file was used to make."""
    r = df.groupby('day')[col].rank(pct=True, ascending=False)
    b = np.clip((r * nb).astype(int), 0, nb - 1)
    out = df.assign(_b=b).groupby('_b')[y].agg(
        ['size', ('mean', geo_mean_per_euro)])
    print(f'\n  {label} (within-day rank, bucket 0 = strongest)')
    for i, row in out.iterrows():
        print(f'    bucket {i:2d}  n={int(row["size"]):6,d}  '
              f'geo y {row["mean"]:.4f}')
    lo, hi = out['mean'].iloc[0], out['mean'].iloc[-1]
    print(f'    strongest minus weakest: {lo - hi:+.4f}')


def main() -> None:
    av = sys.argv
    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    cfg = apply_v5(load_config())
    cfg['minervini_trading']['reentry_fast'] = True
    panel = build_panel(cfg, v5=True)
    cal = panel['calendar']
    rsl, wk, rsv = panel['rsl_hi'], panel['weak'], panel['rs']

    # the whole record, start to today: this file fits nothing, so there
    # is nothing a period split could hold out (EVALUATION_SPEC.md)
    led = pd.read_csv(LEDGER, parse_dates=['entry_date'])
    i = led['entry_i'].to_numpy(np.int64)
    j = led['ticker_j'].to_numpy(np.int64)
    d = pd.DataFrame({'day': i, 'y': led['y'].to_numpy(float),
                      'rsl': rsl[i, j].astype(float),
                      'weak': np.where(np.isfinite(wk[i, j]), wk[i, j], -np.inf),
                      'rs': np.where(np.isfinite(rsv[i, j]), rsv[i, j], -np.inf)})
    print(f'{len(d):,} candidates on {d["day"].nunique():,} days, '
          f'pool geo y {geo_mean_per_euro(d["y"]):.4f}, '
          f'median {d.groupby("day").size().median():.0f} candidates/day')

    g = d.groupby('rsl')['y'].agg(['size', ('mean', geo_mean_per_euro)])
    print('\n  rsl_hi (RS line at a 250-day high)')
    for v, row in g.iterrows():
        print(f'    {"True " if v else "False"}  n={int(row["size"]):6,d}  '
              f'geo y {row["mean"]:.4f}')
    if len(g) == 2:
        print(f'    True minus False: {g["mean"].iloc[1] - g["mean"].iloc[0]:+.4f}')

    by_bucket(d, 'weak', 'y', 10, 'weak (holds up on SPY down-days)')
    by_bucket(d, 'rs', 'y', 10, 'rs (126-day relative return)')

    # the real question: the sorter's own ordering
    d = d.sort_values(['day', 'rsl', 'weak', 'rs'],
                      ascending=[True, False, False, False])
    d['pos'] = d.groupby('day').cumcount() + 1
    def bucket(p):
        if p <= 10:
            return f'{p}'
        if p <= 15:
            return '11-15'
        return '16-25' if p <= 25 else '26+'

    order = [str(k) for k in range(1, 11)] + ['11-15', '16-25', '26+']
    d['slot'] = d['pos'].map(bucket)
    t = (d.groupby('slot', observed=True)['y']
         .agg(['size', ('mean', geo_mean_per_euro)])
         .reindex(order).dropna())
    print('\n  BY THE SLOT THE SORTER WOULD GIVE IT '
          '(1 = first choice of the day)')
    for s, row in t.iterrows():
        if s == 'x':
            continue
        print(f'    position {str(s):>6s}  n={int(row["size"]):6,d}  '
              f'geo y {row["mean"]:.4f}')
    top = d[d['pos'] <= 10]['y']
    rest = d[d['pos'] > 10]['y']
    gtop, grest = geo_mean_per_euro(top), geo_mean_per_euro(rest)
    print(f'\n    top 10 per day : n={len(top):6,d}  geo y {gtop:.4f}')
    print(f'    everyone else  : n={len(rest):6,d}  geo y {grest:.4f}')
    print(f'    difference     : {gtop - grest:+.4f}')


if __name__ == '__main__':
    main()
