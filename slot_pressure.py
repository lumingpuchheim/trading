"""How hard does the slot limit actually bind? Signals vs free slots, daily.

The filter argument rests on an assumption nobody has measured: that v5r
sees far more signals than it can hold, so the slot queue is already
throwing most of them away by arrival order, and choosing better is free.

That is only true on days when slots are FULL and signals are arriving.
On a day with three signals and six free slots the filter has nothing to
decide. This draws both series so the assumption can be checked rather
than asserted.

Signals come from the panel's MOC triggers on green-light days -- the
entries v5r could actually have taken. Occupancy is reconstructed from
the simulator's own trade log (entry_date .. exit_date), so the slot
count is the one the reported equity curve actually experienced, not a
re-simulation that might drift from it.

Usage
    python slot_pressure.py
    python slot_pressure.py --slots 10 --out results/slot_pressure.png
"""

import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config
from minervini_backtest import apply_v5, build_panel

TRADES = [ROOT / 'results' / 'minervini_v5_e3_moc_trades.csv']


def main() -> None:
    av = sys.argv
    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    n_slots = opt('--slots', 10, int)
    out = opt('--out', str(ROOT / 'results' / 'slot_pressure.png'))

    cfg = apply_v5(load_config())
    cfg['minervini_trading']['reentry_fast'] = True
    panel = build_panel(cfg, v5=True)
    cal = panel['calendar']
    green = panel['green']

    # signals a green day actually offered
    sig = (panel['trigger_moc'] & green[:, None]).sum(axis=1).astype(float)
    sig = pd.Series(sig, index=cal)

    # occupancy from the simulator's own trade log
    tr = pd.concat([pd.read_csv(p, parse_dates=['entry_date', 'exit_date'])
                    for p in TRADES if p.exists()])
    pos = pd.Series(0, index=cal, dtype=int)
    d = {dt: i for i, dt in enumerate(cal)}
    delta = np.zeros(len(cal) + 1, dtype=int)
    for a, b in zip(tr['entry_date'], tr['exit_date']):
        i, j = d.get(a), d.get(b)
        if i is None:
            continue
        j = len(cal) - 1 if j is None else j
        delta[i] += 1
        delta[j + 1] -= 1
    pos[:] = np.cumsum(delta)[:len(cal)]
    free = (n_slots - pos).clip(lower=0)

    span = (cal >= pd.Timestamp('2007-01-01'))
    sig, free, pos = sig[span], free[span], pos[span]
    mo = lambda s: s.resample('ME').mean()

    full = float((pos >= n_slots).mean())
    contested = float(((pos >= n_slots) & (sig > 0)).mean())
    idle_with_sig = float(((pos < n_slots) & (sig > 0)).mean())
    print(f'{len(sig):,} trading days 2007-2026, {n_slots} slots')
    print(f'  slots FULL                        {full:6.1%} of days')
    print(f'  full AND signals arriving         {contested:6.1%}  <- the filter can only matter here')
    print(f'  a free slot AND signals arriving  {idle_with_sig:6.1%}  <- nothing to choose, take them all')
    print(f'  median signals on a green day     {sig[sig > 0].median():.0f}')
    print(f'  median free slots                 {free.median():.0f}')

    fig, ax = plt.subplots(2, 1, figsize=(13, 6.5), sharex=True,
                           gridspec_kw={'height_ratios': [2, 1]})
    ax[0].fill_between(sig.index, sig.values, color='#4C78A8', alpha=.25, lw=0)
    ax[0].plot(mo(sig).index, mo(sig).values, color='#1B3B6F', lw=1.4,
               label='signals offered per day (monthly mean)')
    ax[0].axhline(n_slots, color='#C44E52', ls='--', lw=1.2,
                  label=f'{n_slots} slots — the whole book')
    ax[0].set_yscale('symlog', linthresh=10)
    ax[0].set_ylabel('signals per day')
    ax[0].legend(loc='upper left', fontsize=9)
    ax[0].set_title('v5r, no filter: signals offered vs slots available')

    ax[1].fill_between(free.index, free.values, color='#55A868', alpha=.3, lw=0)
    ax[1].plot(mo(free).index, mo(free).values, color='#1E5B32', lw=1.4)
    ax[1].axhline(0, color='#C44E52', ls='--', lw=1.0)
    ax[1].set_ylabel('free slots')
    ax[1].set_ylim(-0.4, n_slots + 0.4)
    ax[1].set_xlabel('')
    fig.text(0.01, 0.01, f'slots full on {full:.0%} of days; full with signals '
             f'arriving on {contested:.0%} — the filter can only act there',
             fontsize=9, color='#444')
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    print(f'-> {out}')


if __name__ == '__main__':
    main()
