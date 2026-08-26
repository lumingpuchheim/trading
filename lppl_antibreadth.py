"""ANTIBUBBLE_SPEC V1: anti-bubble breadth as the missing 2021 gauge.

Gauge: daily fraction of active tickers holding a certified anti-bubble
flag (2-of-5 votes, persistence 2, standard refit window), hostile when
the fraction exceeds its trailing 756d 80th percentile.

Pre-registered claims (PASS/FAIL printed against each):
  MUST mark hostile:  2021-H2 >= 50% of days, 2022 >= 50%,
                      2020-03..2020-05 hostile
  MUST stay quiet:    2009..2013 <= 20% of days, 2023..2025 <= 25%
Then the trade-level kill test: avg return of baseline lppl_dip2 trades
entered on hostile vs quiet days, BOTH periods. If blocked >= allowed,
V1 dies regardless of the regime table (the flagged-cohort lesson).

Run after lppl_anti_detect.py:  python lppl_antibreadth.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config

CLAIMS_HOSTILE = [('2021-H2', '2021-07-01', '2021-12-31', 0.50),
                  ('2022', '2022-01-03', '2022-12-30', 0.50),
                  ('2020-crash', '2020-03-01', '2020-05-29', 0.50)]
CLAIMS_QUIET = [('2009-2013', '2009-01-02', '2013-12-31', 0.20),
                ('2023-2025', '2023-01-03', '2025-12-31', 0.25)]


def main() -> None:
    cfg = load_config()
    g = cfg['lppl']
    data_dir = ROOT / cfg['data']['cache_dir']
    results = ROOT / cfg['backtest']['results_dir']
    spy = pd.read_parquet(data_dir / 'ohlcv' / f"{cfg['data']['benchmark']}.parquet")
    cal = spy.index
    cal_pos = {d: i for i, d in enumerate(cal)}
    n = len(cal)

    flags = pd.read_parquet(data_dir / 'lppl_anti_flags.parquet')
    certified = np.zeros(n)
    for t, gg in flags.groupby('ticker'):
        prev = 0
        for r in gg.sort_values('date').itertuples():
            j = cal_pos.get(r.date)
            if j is None:
                continue
            if r.votes >= g['min_votes_loose']:
                prev += 1
                if prev >= g['persistence']:
                    certified[j:min(n, j + g['refit_every'])] += 1
            else:
                prev = 0

    active = np.zeros(n)
    for path in sorted((data_dir / 'ohlcv').glob('*.parquet')):
        if path.stem == cfg['data']['benchmark']:
            continue
        active += np.isfinite(
            pd.read_parquet(path, columns=['close'])['close']
            .reindex(cal).to_numpy())
    frac = pd.Series(np.where(active > 0, certified / np.maximum(active, 1), 0.0),
                     index=cal)
    thresh = frac.rolling(756).quantile(0.80)
    hostile = (frac > thresh) & thresh.notna()
    pd.DataFrame({'frac': frac, 'thresh': thresh, 'hostile': hostile}) \
        .to_parquet(data_dir / 'anti_breadth.parquet')

    print('=== stage 1: pre-registered regime claims ===')
    ok_all = True
    for name, a, b, need in CLAIMS_HOSTILE:
        h = hostile[a:b].mean()
        ok = h >= need
        ok_all &= ok
        print(f'  {name}: hostile {h:.0%} (need >= {need:.0%}) '
              f'{"PASS" if ok else "FAIL"}')
    for name, a, b, cap in CLAIMS_QUIET:
        h = hostile[a:b].mean()
        ok = h <= cap
        ok_all &= ok
        print(f'  {name}: hostile {h:.0%} (need <= {cap:.0%}) '
              f'{"PASS" if ok else "FAIL"}')
    print(f'regime table: {"ALL CLAIMS PASS" if ok_all else "CLAIMS FAILED"}')

    print('\n=== stage 2: trade-level kill test (baseline lppl_dip2) ===')
    hostile_np = hostile.to_numpy()
    for p in ('dev', 'test'):
        t = pd.read_csv(results / f'lppl_{p}_trades_lppl_dip2.csv',
                        parse_dates=['entry_date'])
        dec = [cal_pos.get(d) for d in t['entry_date']]
        t['blocked'] = [bool(hostile_np[max(0, j - 1)]) if j is not None
                        else False for j in dec]
        bl, al = t[t['blocked']], t[~t['blocked']]
        print(f'[{p}] blocked n={len(bl)} avg {bl["ret_net"].mean():+.4f} | '
              f'allowed n={len(al)} avg {al["ret_net"].mean():+.4f} | '
              f'kill test {"PASS (blocked worse)" if len(bl) and bl["ret_net"].mean() < al["ret_net"].mean() else "FAIL"}')

    fig, ax = plt.subplots(figsize=(13, 5))
    ax.plot(frac.index, frac, lw=0.7, label='anti-bubble breadth')
    ax.plot(thresh.index, thresh, lw=0.7, ls='--', label='756d 80th pct')
    ax.fill_between(frac.index, 0, frac.max(), where=hostile, alpha=0.15,
                    color='red', label='hostile')
    s = spy['close']
    ax2 = ax.twinx()
    ax2.plot(s.index, s, color='gray', lw=0.6, alpha=0.6)
    ax2.set_yscale('log')
    ax.legend(loc='upper left')
    ax.set_title('anti-bubble breadth gauge (gray: SPY, log)')
    fig.tight_layout()
    fig.savefig(results / 'lppl_antibreadth.png', dpi=120)
    print(f'\nchart -> {results}/lppl_antibreadth.png')


if __name__ == '__main__':
    main()
