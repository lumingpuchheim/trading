"""THE per-bet statistics: how much is left of one euro, per position.

Input: results/minervini_{TAG}_trades.csv
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
               + dividends collected / what the position cost

with weight 0.5 for each leg of a split position and 1.0 for an unsplit
one. Prices stopped being dividend adjusted on 2026-08-29, so the second
term is the only place a holder's dividends survive. That is the number
this file reports, and it comes from geostats.bet_multiples -- the same
function every other per-bet figure in the repo goes through.

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

from geostats import bet_multiples, geo_mean_per_euro

TAG = sys.argv[1] if len(sys.argv) > 1 else 'v5_moc'

# One continuous record, start to today. The development / test split
# was removed 2026-08-29: nothing here is fitted, so it only ever cut one
# result into two halves that were then compared with each other.
for _ in (0,):
    t = pd.read_csv(f'results/minervini_{TAG}_trades.csv')
    span = pd.to_datetime(t['exit_date']).max() - pd.to_datetime(
        t['entry_date']).min()
    years = span.days / 365.25
    t['pos_id'] = t['ticker'] + '|' + t['entry_date'].astype(str)
    t['is_split'] = t['pos_id'].duplicated(keep=False)

    # one euro in, this many euros out, per position. The collapse lives
    # in geostats.bet_multiples so that this file, filter_backtest.py,
    # equity_vs_spy.py, slot_sweep.py and minervini_backtest.py cannot
    # drift into reporting different per-bet numbers again.
    mult = bet_multiples(t)
    split = t.groupby('pos_id')['is_split'].first().reindex(mult.index)

    geo = geo_mean_per_euro(mult)

    # Size-weighted geometric means (geostats.py). The unweighted one
    # gives a 5% pilot that never added the same vote as a completed 10%
    # ladder; these weight each bet by the money in it. Two weightings,
    # because they answer different questions: by EUROS lets late bets
    # dominate once equity has compounded, by FRACTION OF EQUITY does not.
    def wgeo(w: pd.Series) -> float:
        w = w.reindex(mult.index)
        return geo_mean_per_euro(mult.to_numpy(), w.to_numpy())

    eur = (t.groupby('pos_id')['bet_eur'].first()
           if 'bet_eur' in t.columns else pd.Series(dtype=float))
    frac = (t.groupby('pos_id')['bet_frac'].first()
            if 'bet_frac' in t.columns else pd.Series(dtype=float))
    print(f'================ {TAG}  ({years:.1f} years) ================')
    print(f'rows in file          : {len(t)}')
    print(f'positions (bets)      : {len(mult)}   '
          f'of which split in two: {int(split.sum())}')
    print()
    print('ONE EURO COMMITTED PER POSITION, EUROS RETURNED:')
    print(f'   GEOMETRIC mean  : {geo:.4f}   ({geo - 1:+.2%} per bet)'
          '   [one vote per bet]')
    if len(frac) and frac.notna().any():
        g = wgeo(frac)
        print(f'   weighted by % of equity staked : {g:.4f}   ({g - 1:+.2%})')
    if len(eur) and eur.notna().any():
        g = wgeo(eur)
        print(f'   weighted by euros staked       : {g:.4f}   ({g - 1:+.2%})')
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
