"""THE per-bet statistics: how much is left of one euro, per position.

Input: results/minervini_{TAG}_{dev,test}_trades.csv
Columns: ticker, entry_date, exit_date, entry_px, exit_px, days_held,
         ret_net, exit_reason

THE QUESTION THIS ANSWERS (user's framing, 2026-08-28): commit one euro
to a position at entry; when everything about that position has closed,
how much is left? Nothing else -- not win rate, not row averages.

WHY THE OBVIOUS CALCULATION IS WRONG. The simulator writes one ROW per
sale, and a position can sell in two pieces:

 - every position is entered at one slot (10% of equity);
 - if it later reaches +20% the strength rule sells HALF, so that
   position emits TWO rows -- the banked half and the rider's eventual
   exit (v9 adds a `climax_partial` that splits the same way);
 - if it never reaches +20% it exits whole as ONE row.

So averaging `ret_net` over rows counts a +150% rider as a full bet when
only half the money was riding, and double-counts every winner because
losers never split. The euro left per euro committed is

    multiple = SUM over legs of  weight_leg x (1 + ret_net_leg)

with weight 0.5 for each leg of a split position and 1.0 for an unsplit
one. That is the number this file reports.

(Approximation, stated: the simulator sells floor(shares/2), so an odd
share count leaves the rider marginally more than half. The error is
under 1% of one position's weight and is not corrected here.)

ONE AVERAGE, THE GEOMETRIC ONE. It is what a euro becomes per bet when
the same euro is cycled through the bets in sequence, and it is what
decides whether a sequence of bets compounds up or grinds down.

The arithmetic mean is deliberately absent (removed 2026-08-28). It
answers a question nobody is asking: it is the average of outcomes that
were never averaged, because money is not spread across bets in parallel
-- it passes through them. A single +900% bet lifts the arithmetic mean
of a losing system above 1.0 while the euro that actually travelled the
sequence is gone.

Win rate is absent for the same reason (removed 2026-08-28): being right
90% of the time at break-even while the other 10% takes real money still
loses, so the statistic cannot say whether a system works.

Run: python minervini_stats.py                 # v5_moc
     python minervini_stats.py v5_e3_moc       # any other run tag
"""

import sys

import numpy as np
import pandas as pd

TAG = sys.argv[1] if len(sys.argv) > 1 else 'v5_moc'

for period, years in (('dev', 12.0), ('test', 7.65)):
    t = pd.read_csv(f'results/minervini_{TAG}_{period}_trades.csv')
    t['pos_id'] = t['ticker'] + '|' + t['entry_date'].astype(str)
    t['is_split'] = t['pos_id'].duplicated(keep=False)
    if 'weight' not in t.columns:      # runs written before 2026-08-28
        t['weight'] = np.where(t['is_split'], 0.5, 1.0)

    # one euro in, this many euros out, per position
    mult = t.groupby('pos_id').apply(
        lambda d: float((d['weight'] * (1.0 + d['ret_net'])).sum()),
        include_groups=False)
    split = t.groupby('pos_id')['is_split'].first()

    geo = float(np.exp(np.mean(np.log(mult))))
    print(f'================ {TAG}  {period}  ({years} years) ================')
    print(f'rows in file          : {len(t)}')
    print(f'positions (bets)      : {len(mult)}   '
          f'of which split in two: {int(split.sum())}')
    print()
    print('ONE EURO COMMITTED PER POSITION, EUROS RETURNED:')
    print(f'   GEOMETRIC mean  : {geo:.4f}   ({geo - 1:+.2%} per bet)')
    print(f'   median          : {mult.median():.4f}   '
          f'({mult.median() - 1:+.2%})')
    print(f'   worst / best    : {mult.min():.4f} / {mult.max():.4f}')
    print()
    # NOT the portfolio's return: the book runs 10 slots at 10%, so a euro
    # of capital rides a tenth of each bet, not all of one after another.
    # This is the geometric mean's own meaning, stated in euros.
    print('   one euro through every bet END TO END (not the portfolio):')
    print(f'      x{geo ** len(mult):,.4g} over {len(mult)} bets '
          f'({(geo ** (len(mult) / years) - 1):+.1%} a year at this bet rate)')
    print()
    print('   split vs unsplit (the split ones are winners by construction '
          '-- reaching +20% is what splits them):')
    for label, sel in (('split  ', split.to_numpy()),
                       ('unsplit', ~split.to_numpy())):
        m = mult[sel]
        if len(m):
            print(f'      {label}: n={len(m):4d}  '
                  f'geo {np.exp(np.mean(np.log(m))):.4f}')
    print()
    q = mult.quantile([0.05, 0.25, 0.5, 0.75, 0.95])
    print('   euro returned by percentile: '
          + '  '.join(f'p{int(p * 100)} {v:.3f}' for p, v in q.items()))
    print()
