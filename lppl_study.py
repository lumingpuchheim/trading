"""Descriptive study: when is lppl_dip2 right and when is it wrong.
No strategy changes — diagnosis only. Run: python lppl_study.py

Sections: 1 money anatomy, 2 market-state table, 3 episode anatomy and
entry order, 4 tc calibration vs actual peaks, 5 flag-vs-control forward
returns (identification test), 6 speculative breadth vs yearly P&L.
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel

EP_GAP = 20        # trading-day gap in qualifying evals that ends an episode
PEAK_WIN = 120     # trading days after last flag eval searched for the peak
DD_WIN = 180       # trading days after the peak for the post-peak drawdown
CRASH = -0.30


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    panel = load_panel(cfg)
    cal = panel['calendar']
    cal_pos = {d: i for i, d in enumerate(cal)}
    n = len(cal)
    arrays = panel['arrays']
    flags = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'lppl_flags.parquet')
    trades = {}
    for p in ('dev', 'test'):
        t = pd.read_csv(results / f'lppl_{p}_trades_lppl_dip2.csv',
                        parse_dates=['entry_date', 'exit_date'])
        trades[p] = t

    print('=' * 70)
    print('SECTION 1 — money anatomy (real lppl_dip2 trades)')
    for p, t in trades.items():
        r = t['ret_net']
        wins = r[r > 0].sort_values(ascending=False)
        print(f'\n[{p}] n={len(t)} sum={r.sum():+.2f} mean={r.mean():+.4f} '
              f'median={r.median():+.4f} skew={r.skew():.2f}')
        print(f'  gross wins {wins.sum():+.2f}: top-5 trades carry '
              f'{wins.head(5).sum() / wins.sum():.1%}, top-10% of all trades '
              f'carry {wins.head(max(1, len(t) // 10)).sum() / wins.sum():.1%}')
        print(f'  trades > +50%: {(r > 0.5).sum()}, > +100%: {(r > 1.0).sum()}, '
              f'< -15% (gap-amplified stops): {(r < -0.15).sum()}')
        print(t.groupby('exit_reason')['ret_net']
              .agg(['count', 'mean', 'median']).round(4).to_string())
        print(f'  median hold: winners {t.loc[r > 0, "days_held"].median():.0f}d, '
              f'losers {t.loc[r <= 0, "days_held"].median():.0f}d')

    print('\n' + '=' * 70)
    print('SECTION 2 — performance by market state at entry (descriptive)')
    spy = panel['spy_close']
    above200 = (spy > spy.rolling(200).mean())
    v20 = spy.pct_change().rolling(20).std()
    volhi = (v20 > v20.rolling(756).quantile(0.90))
    for p, t in trades.items():
        st = pd.DataFrame({
            'ret': t['ret_net'].to_numpy(),
            'above200': above200.reindex(t['entry_date']).to_numpy(),
            'volhi': volhi.reindex(t['entry_date']).to_numpy()})
        g = st.groupby(['above200', 'volhi'])['ret'] \
            .agg(['count', 'mean', lambda x: (x > 0).mean()])
        g.columns = ['n', 'avg_ret', 'win_rate']
        print(f'\n[{p}]')
        print(g.round(4).to_string())

    print('\n' + '=' * 70)
    print('SECTION 3+4 — bubble episodes, entry order, tc calibration')
    eps = []
    for tick, gg in flags.groupby('ticker'):
        a = arrays.get(tick)
        if a is None:
            continue
        q = gg[gg['votes'] >= cfg['lppl']['min_votes_loose']].sort_values('date')
        if q.empty:
            continue
        close = a['close_f']
        pos = [(cal_pos[r.date], r.tc_ahead) for r in q.itertuples()
               if r.date in cal_pos]
        groups, cur = [], [pos[0]]
        for pr in pos[1:]:
            if pr[0] - cur[-1][0] > EP_GAP:
                groups.append(cur)
                cur = []
            cur.append(pr)
        groups.append(cur)
        for grp in groups:
            i0, i1 = grp[0][0], grp[-1][0]
            j_end = min(i1 + PEAK_WIN, n - 1)
            seg = close[i0:j_end + 1]
            if not np.all(np.isfinite(seg)) or len(seg) < 2:
                continue
            pk = i0 + int(np.argmax(seg))
            peak_px = close[pk]
            post = close[pk:min(pk + DD_WIN, n)]
            dd = float(post.min() / peak_px - 1)
            tc_ests = [g0 + tc for g0, tc in grp if np.isfinite(tc)]
            eps.append({
                'ticker': tick, 'start': cal[i0], 'year': cal[i0].year,
                'n_evals': len(grp), 'len_d': i1 - i0,
                'runup126': close[i0] / close[max(0, i0 - 126)] - 1,
                'peak_lag': pk - i0,
                'peak_after_flag_end': pk > i1,
                'tc_err': float(np.median(tc_ests)) - pk if tc_ests else np.nan,
                'post_peak_dd': dd, 'crash': dd <= CRASH})
    ep = pd.DataFrame(eps)
    ep.to_csv(results / 'lppl_study_episodes.csv', index=False)
    for p, lo, hi in [('dev', 2007, 2018), ('test', 2019, 2099)]:
        e = ep[(ep['year'] >= lo) & (ep['year'] <= hi)]
        print(f'\n[{p}] {len(e)} episodes | median length {e["len_d"].median():.0f}d, '
              f'evals {e["n_evals"].median():.0f}, run-up {e["runup126"].median():+.1%}')
        print(f'  peak timing: median {e["peak_lag"].median():.0f}d after episode '
              f'start; {e["peak_after_flag_end"].mean():.1%} of peaks come AFTER '
              f'the flag has already lapsed')
        print(f'  post-peak 180d drawdown: median {e["post_peak_dd"].median():+.1%}; '
              f'crash rate (<= -30%): {e["crash"].mean():.1%}')
        te = e['tc_err'].dropna()
        print(f'  tc error (median tc estimate - actual peak, trading days): '
              f'median {te.median():+.0f}, IQR [{te.quantile(.25):+.0f}, '
              f'{te.quantile(.75):+.0f}], '
              f'tc before peak: {(te < 0).mean():.1%}')

    # entry order inside an episode (real trades)
    print('\nentry order within episode (real trades):')
    for p, t in trades.items():
        t = t.copy()
        t['order'] = np.nan
        for tick, tt in t.groupby('ticker'):
            e_t = ep[ep['ticker'] == tick]
            for _, epi in e_t.iterrows():
                lo = epi['start']
                hi = lo + pd.Timedelta(days=int((epi['len_d'] + EP_GAP) * 1.6))
                m = (t['ticker'] == tick) & (t['entry_date'] >= lo) \
                    & (t['entry_date'] <= hi)
                if m.any():
                    t.loc[m, 'order'] = t.loc[m, 'entry_date'].rank()
        t['order_b'] = t['order'].map(
            lambda x: '1st' if x == 1 else '2nd' if x == 2 else
            '3rd+' if x >= 3 else 'unmatched')
        g = t.groupby('order_b')['ret_net'].agg(['count', 'mean', 'median'])
        print(f'[{p}]')
        print(g.round(4).to_string())

    print('\n' + '=' * 70)
    print('SECTION 5 — identification test: flagged vs matched control days')
    spy_np = spy.to_numpy()

    def fwd(arr, i, k):
        j = min(i + k, n - 1)
        return arr[j] / arr[i] - 1 if arr[i] > 0 else np.nan

    rows = []
    for tick, gg in flags.groupby('ticker'):
        a = arrays.get(tick)
        if a is None:
            continue
        close = a['close_f']
        for r in gg.itertuples():
            i = cal_pos.get(r.date)
            if i is None or not np.isfinite(close[i]) or i > n - 21:
                continue
            f60 = fwd(close, i, 60)
            f120 = fwd(close, i, 120)
            seg = close[i:min(i + 120, n)]
            dd120 = float(seg.min() / close[i] - 1)
            rows.append({'year': r.date.year, 'votes': r.votes,
                         'x60': f60 - fwd(spy_np, i, 60),
                         'x120': f120 - fwd(spy_np, i, 120),
                         'dd120': dd120})
    ev = pd.DataFrame(rows)
    ev['grp'] = np.select([ev['votes'] >= 3, ev['votes'] == 2, ev['votes'] == 1],
                          ['3-5 votes', '2 votes', '1 vote'], default='0 votes')
    for p, lo, hi in [('dev', 2007, 2018), ('test', 2019, 2099)]:
        e = ev[(ev['year'] >= lo) & (ev['year'] <= hi)]
        g = e.groupby('grp').agg(
            n=('x60', 'size'), x60_med=('x60', 'median'),
            x60_mean=('x60', 'mean'), x120_med=('x120', 'median'),
            x120_mean=('x120', 'mean'),
            crash120=('dd120', lambda x: (x <= CRASH).mean()))
        print(f'\n[{p}] forward EXCESS-vs-SPY returns from evaluation days '
              f'(controls = same accelerating run-up, 0 votes):')
        print(g.round(4).to_string())

    print('\n' + '=' * 70)
    print('SECTION 6 — speculative breadth (daily 2-of-5 flag count) vs yearly P&L')
    b2_count = np.zeros(n)
    for a in arrays.values():
        b2_count += a['b2']
    bc = pd.Series(b2_count, index=cal)
    tab = []
    allt = pd.concat([trades['dev'], trades['test']])
    for y in range(2007, cal[-1].year + 1):
        ty = allt[allt['entry_date'].dt.year == y]
        tab.append({'year': y, 'avg_flags': bc[bc.index.year == y].mean(),
                    'n_trades': len(ty), 'sum_ret': ty['ret_net'].sum(),
                    'win_rate': (ty['ret_net'] > 0).mean() if len(ty) else np.nan})
    tb = pd.DataFrame(tab)
    print(tb.round(3).to_string(index=False))
    tb.to_csv(results / 'lppl_study_breadth.csv', index=False)

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for p, lo, hi, c in [('dev', 2007, 2018, 'C0'), ('test', 2019, 2099, 'C1')]:
        te = ep[(ep['year'] >= lo) & (ep['year'] <= hi)]['tc_err'].dropna()
        axes[0].hist(te.clip(-150, 250), bins=40, alpha=0.5, label=p, color=c)
    axes[0].axvline(0, color='k', lw=1)
    axes[0].set_title('tc estimate − actual peak (trading days)')
    axes[0].legend()
    ax = axes[1]
    ax.bar(tb['year'], tb['sum_ret'], color=['C0' if y <= 2018 else 'C1'
                                             for y in tb['year']], alpha=0.7)
    ax2 = ax.twinx()
    ax2.plot(tb['year'], tb['avg_flags'], 'k.-', label='avg daily flags')
    ax.set_title('yearly summed trade return (bars) vs avg flag count (line)')
    for p, lo, hi, c in [('dev', 2007, 2018, 'C0'), ('test', 2019, 2099, 'C1')]:
        e = ev[(ev['year'] >= lo) & (ev['year'] <= hi)]
        g = e.groupby('grp')['x120'].median().reindex(
            ['0 votes', '1 vote', '2 votes', '3-5 votes'])
        axes[2].plot(g.index, g.values, 'o-', label=p, color=c)
    axes[2].axhline(0, color='gray', lw=0.5)
    axes[2].set_title('median 120d excess return by vote count')
    axes[2].legend()
    fig.tight_layout()
    fig.savefig(results / 'lppl_study.png', dpi=120)
    print(f'\nchart -> {results}/lppl_study.png')


if __name__ == '__main__':
    main()
