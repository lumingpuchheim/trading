"""THE definitive per-bet statistics for v5r. One program, no editing.

Input: results/minervini_v5_moc_{dev,test}_trades.csv
Columns: ticker, entry_date, exit_date, entry_px, exit_px, days_held,
         ret_net, exit_reason

FACTS about how the simulator writes that file (minervini_backtest.py):
 - Every position is entered at 10% of equity. There is ONE entry rule
   and ONE exit rule set for all positions. No position is entered at
   half size, ever.
 - If a position's close reaches +20%, HALF the shares are sold there
   ('strength' row) and the remaining half exits later by the normal
   rules (its own row). Such a position therefore produces TWO rows,
   each representing ~5% of equity.
 - A position that never reaches +20% produces ONE row at ~10%.

Therefore:
 - counting ROWS overweights winners (each split winner counted twice,
   and 'ret_net' of its banked half is >= +20% by construction);
 - a POSITION's return = 0.5 x (half-1 return) + 0.5 x (half-2 return)
   when split, else its single row's return. (The two halves are equal
   up to one share; treated as exactly equal here.)

Under --v9 (spec section 13) a position can also be split by a
`climax_partial` row instead of a `strength` one. The arithmetic is
identical -- both sell half the shares -- so the same weighting holds.

Run: python minervini_stats.py            # v5_moc, as before
     python minervini_stats.py v9_moc     # any other run tag
"""

import sys

import numpy as np
import pandas as pd

TAG = sys.argv[1] if len(sys.argv) > 1 else 'v5_moc'

for period, years in (('dev', 12.0), ('test', 7.65)):
    t = pd.read_csv(f'results/minervini_{TAG}_{period}_trades.csv')
    t['pos_id'] = t['ticker'] + '|' + t['entry_date'].astype(str)
    t['is_split'] = t['pos_id'].duplicated(keep=False)
    t['weight'] = np.where(t['is_split'], 0.5, 1.0)

    rows = t['ret_net']
    pos = t.groupby('pos_id').apply(
        lambda d: float((d['ret_net'] * d['weight']).sum()),
        include_groups=False)

    print(f'============ {TAG}  {period}  ({years} years) ============')
    print(f'rows in file          : {len(t)}')
    print(f'  of which half-rows  : {int(t["is_split"].sum())} '
          f'(from {int(t["is_split"].sum() // 2)} split positions)')
    print(f'positions             : {len(pos)}')
    print()
    print('A) counting ROWS (wrong unit — winners double-counted):')
    print(f'   mean {rows.mean():+.4%}   median {rows.median():+.4%}   '
          f'P(win) {(rows > 0).mean():.1%}')
    print()
    print('B) counting POSITIONS (the honest unit, each one 10% of equity):')
    print(f'   mean            {pos.mean():+.4%}')
    print(f'   geometric mean  {np.expm1(np.mean(np.log1p(pos))):+.4%}'
          '   [ = (prod(1+x))^(1/n) - 1 ]')
    print(f'   median          {pos.median():+.4%}')
    print(f'   P(win)          {(pos > 0).mean():.1%}'
          f'   ({int((pos > 0).sum())} of {len(pos)})')
    print()
    win_rows = (rows > 0).mean()
    win_pos = (pos > 0).mean()
    print(f'why A says P(win)={win_rows:.0%} but B says {win_pos:.0%}: '
          f'every split position is a winner counted TWICE in A,')
    print('and its banked half (>= +20% by construction) also lifts the '
          'row mean.')
    print()
