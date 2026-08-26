"""Stage 1 for the two remaining regime monitors — no backtest.

M1 internal — universal signal hit-rate: every lppl_dip2 entry signal
(candidate condition, ignoring slots/cooldown) is paper-scored: success =
the stock did NOT close <= 0.92 x entry within 20 trading days. The
monitor at day d is the success rate of signals RESOLVED in the trailing
63 days (a signal entered at e resolves at e+22; only resolved signals
count — no lookahead).

M2 external — style ratio: IWO (Russell 2000 Growth) / SPY, hostile when
the ratio is below its own value 126 trading days ago.

Output: per-year values, regime windows, and the trade-level audit of the
baseline's actual trades conditional on each monitor.
Run: python lppl_monitors.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel

HORIZON = 20      # days a signal must survive
RESOLVE = 22      # entry next open + 20 closes -> known
WINDOW = 63       # trailing window of resolved signals


def main() -> None:
    cfg = load_config()
    results = ROOT / cfg['backtest']['results_dir']
    panel = load_panel(cfg)
    cal = panel['calendar']
    n = len(cal)
    cal_pos = {d: i for i, d in enumerate(cal)}
    stop = cfg['lppl_trading']['stop_loss']

    # ---- M1: paper-score every entry signal
    resolved_day, success = [], []
    for t, a in panel['arrays'].items():
        cand = a['b2'] & a['dip'] & a['liquid'] & (a['tc2'] > np.arange(n))
        for e in np.flatnonzero(cand):
            if e + RESOLVE >= n:
                continue
            px = a['open'][e + 1]
            if not np.isfinite(px):
                continue
            win = a['close_f'][e + 2:e + 2 + HORIZON]
            ok = not np.any(win <= stop * px)
            resolved_day.append(e + RESOLVE)
            success.append(ok)
    sig = pd.DataFrame({'r': resolved_day, 'ok': success})
    daily_ok = sig.groupby('r')['ok'].agg(['sum', 'count'])
    ok_s = pd.Series(0.0, index=range(n)); ok_s[daily_ok.index] = daily_ok['sum']
    n_s = pd.Series(0.0, index=range(n)); n_s[daily_ok.index] = daily_ok['count']
    roll_ok = ok_s.rolling(WINDOW).sum()
    roll_n = n_s.rolling(WINDOW).sum()
    m1 = pd.Series(np.where(roll_n >= 10, roll_ok / roll_n, np.nan), index=cal)
    print(f'M1: {len(sig)} signals scored, overall success {sig.ok.mean():.0%}')

    # ---- M2: IWO/SPY style ratio
    iwo = pd.read_parquet(ROOT / 'data' / 'IWO.parquet')['close'].reindex(cal).ffill()
    ratio = (iwo / panel['spy_close']).to_numpy()
    lb = cfg['lppl']['rs_lookback']
    m2_hostile = np.zeros(n, dtype=bool)
    m2_hostile[lb:] = ratio[lb:] < ratio[:-lb]
    m2 = pd.Series(m2_hostile, index=cal)

    print('\nper-year: M1 median success rate | M2 fraction hostile')
    for y in range(2007, 2027):
        yy = str(y)
        print(f'  {y}: M1 {m1[yy].median():5.0%}   M2 {m2[yy].mean():4.0%}')

    print('\nM2 regime windows (fraction hostile):')
    for label, a, b in [('2008', '2008-01-01', '2008-12-31'),
                        ('2020 crash', '2020-02-20', '2020-04-15'),
                        ('2021', '2021-01-01', '2021-12-31'),
                        ('2021 H2', '2021-07-01', '2021-12-31'),
                        ('2022', '2022-01-01', '2022-12-31'),
                        ('2009-2013 GOOD', '2009-01-01', '2013-12-31'),
                        ('2016 GOOD', '2016-01-01', '2016-12-31'),
                        ('2023-2025 GOOD', '2023-01-01', '2025-12-31')]:
        print(f'  {label:16s} {m2[a:b].mean():4.0%}')

    # ---- trade-level audits on the baseline's actual trades
    m1_arr = m1.to_numpy()
    for p in ['dev', 'test']:
        t = pd.read_csv(f'results/lppl_{p}_trades_lppl_dip2.csv',
                        parse_dates=['entry_date'])
        dec = np.array([cal_pos[d] - 1 for d in t.entry_date])
        t['m1'] = m1_arr[dec]
        t['m2_hostile'] = m2_hostile[dec]
        print(f'\n--- {p}: baseline trades conditional on the monitors ---')
        valid = t.dropna(subset=['m1'])
        terc = valid['m1'].quantile([1 / 3, 2 / 3]).to_numpy()
        b = np.digitize(valid['m1'], terc)
        g1 = valid.groupby(b)['ret_net'].agg(['count', 'mean'])
        g1.index = ['M1 low', 'M1 mid', 'M1 high']
        print(g1.to_string(float_format=lambda x: f'{x:.4f}'))
        g2 = t.groupby('m2_hostile')['ret_net'].agg(['count', 'mean'])
        g2.index = ['M2 friendly', 'M2 hostile'][:len(g2)]
        print(g2.to_string(float_format=lambda x: f'{x:.4f}'))

    fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    axes[0].plot(cal, m1, lw=0.9, color='tab:blue')
    axes[0].axhline(0.5, color='gray', ls=':')
    axes[0].set_title('M1: rolling success rate of ALL entry signals (survive 20d without -8%)')
    axes[0].grid(alpha=0.3)
    axes[1].plot(cal, ratio / ratio[200], lw=0.9, color='black')
    axes[1].fill_between(cal, 0, 1, where=m2_hostile,
                         transform=axes[1].get_xaxis_transform(),
                         color='red', alpha=0.15)
    axes[1].set_title('M2: IWO/SPY style ratio (red = hostile)')
    axes[1].grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(results / 'lppl_monitors.png', dpi=120)
    print(f'\nchart -> {results / "lppl_monitors.png"}')


if __name__ == '__main__':
    main()
