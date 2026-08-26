"""Steady Giants phase 3: portfolio backtest per STEADY_GIANTS_SPEC.

Monthly decisions at the first trading day's close (fills at that close,
0.2%/side, fractional shares). Cash accrues the T-bill yield daily.
Sells: own-history P/E ceiling, LPPL certification event (any 2-of-5
persistent flag day since the last decision), dividend cut, delisting.
Buys: green light + qualification + P/E not above own p90; rank by
straightest compounder (R2 desc, vol asc). 8 slots, 12.5% of equity at
entry, winners never trimmed.

Tunables (dev only): r2 threshold {0.6,0.7,0.8} x sell ceiling
{pe_p90, pe_p95, pe_max}; dev selection by MAR (CAGR/|maxDD|), declared
before running. Controls: 200 random-qualifier portfolios (same rules,
random picks); criterion: beat >=75% of controls on total return in
BOTH periods. Run: python giants_backtest.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config

START = 100_000.0
SLOTS = 8
COST = 0.002
N_CONTROLS = 200
R2_GRID = [0.6, 0.7, 0.8]
SELL_GRID = ['pe_p90', 'pe_p95', 'pe_max']


def build_market(cfg):
    d = cfg['data']
    data_dir = ROOT / d['cache_dir']
    spy = pd.read_parquet(data_dir / 'ohlcv' / f"{d['benchmark']}.parquet")
    cal = spy.index
    s = spy['close']
    green = ((s > s.rolling(200).mean())
             & ~(s.pct_change().rolling(20).std()
                 > s.pct_change().rolling(20).std().rolling(756).quantile(0.90))
             ).to_numpy()
    tb = pd.read_parquet(data_dir / 'dgs3mo.parquet').set_index('date')['yield_pct']
    tb = tb.reindex(cal).ffill().fillna(0.0).to_numpy()
    tb_f = (1.0 + tb / 100.0) ** (1.0 / 252.0)
    spy_f = s.pct_change().fillna(0.0).to_numpy() + 1.0
    return data_dir, cal, s, green, tb_f, spy_f


def load_prices(data_dir, cal, benchmark):
    closes, last_i = {}, {}
    for path in sorted((data_dir / 'ohlcv').glob('*.parquet')):
        t = path.stem
        if t == benchmark:
            continue
        c = pd.read_parquet(path)['close'].reindex(cal)
        fin = np.flatnonzero(np.isfinite(c.to_numpy()))
        if not len(fin):
            continue
        closes[t] = c.ffill().to_numpy()
        last_i[t] = int(fin[-1])
    return closes, last_i


def build_b2(data_dir, cal, cfg):
    g = cfg['lppl']
    cal_pos = {d: i for i, d in enumerate(cal)}
    n = len(cal)
    flags = pd.read_parquet(data_dir / 'lppl_flags.parquet')
    out = {}
    for t, gg in flags.groupby('ticker'):
        arr = np.zeros(n, bool)
        prev = 0
        for r in gg.sort_values('date').itertuples():
            j = cal_pos.get(r.date)
            if j is None:
                continue
            if r.votes >= g['min_votes_loose']:
                prev += 1
                if prev >= g['persistence']:
                    arr[j:min(n, j + g['refit_every'])] = True
            else:
                prev = 0
        if arr.any():
            out[t] = arr
    return out


def run(period_i, dec_list, by_i, closes, last_i, b2, green, tb_f,
        r2_th, sell_col, rng=None, parking='tbill', spy_f=None):
    """One portfolio path. dec_list: decision day indices inside period.
    by_i: {day index -> month table}. Returns (daily equity, trades).
    parking: idle cash sits in 'tbill' (spec), 'spy_always' (SPY even on
    red light) or 'spy_green' (SPY on green, T-bills on red). Moving the
    whole idle balance in/out of SPY pays COST per side; the smaller
    flows at stock buys/sells are modeled costless."""
    j0, j1 = period_i
    cash, positions, trades = START, {}, []
    equity = np.empty(j1 - j0 + 1)
    dec_set = set(dec_list)
    prev_dec = {d: p for d, p in zip(dec_list, [j0 - 1] + dec_list[:-1])}
    in_spy = False

    for j in range(j0, j1 + 1):
        want_spy = (parking == 'spy_always'
                    or (parking == 'spy_green' and green[j]))
        if want_spy != in_spy:
            cash *= 1.0 - COST          # move the idle balance in or out
            in_spy = want_spy
        cash *= spy_f[j] if in_spy else tb_f[j]
        if j in dec_set:
            tab = by_i.get(j)
            rows = {r.ticker: r for r in tab.itertuples()} if tab is not None else {}
            for t in list(positions):
                pos = positions[t]
                r = rows.get(t)
                px, reason = closes[t][j], None
                if last_i[t] <= j:
                    px, reason = closes[t][last_i[t]], 'delisted'
                elif t in b2 and b2[t][prev_dec[j] + 1:j + 1].any():
                    reason = 'lppl_tc'
                elif r is not None and r.div_cut:
                    reason = 'div_cut'
                elif r is not None and np.isfinite(r.pe) \
                        and np.isfinite(getattr(r, sell_col)) \
                        and r.pe > getattr(r, sell_col):
                    reason = 'pe_ceiling'
                if reason:
                    cash += pos['shares'] * px * (1 - COST)
                    trades.append({'ticker': t, 'action': 'sell', 'i': j,
                                   'px': px, 'reason': reason,
                                   'entry_i': pos['entry_i'],
                                   'entry_px': pos['entry_px'],
                                   'ret': px * (1 - COST)
                                          / (pos['entry_px'] * (1 + COST)) - 1})
                    del positions[t]
            if green[j] and len(positions) < SLOTS and tab is not None:
                elig = [r for r in tab.itertuples()
                        if r.ticker not in positions
                        and r.vol_terc == 0 and r.slope > 0
                        and r.r2 >= r2_th and r.div_paid and not r.div_cut
                        and r.has_eps and np.isfinite(r.pe)
                        and not (np.isfinite(r.pe_p90) and r.pe > r.pe_p90)
                        and last_i[r.ticker] > j
                        and not any(tr['ticker'] == r.ticker and tr['i'] == j
                                    for tr in trades)]
                if rng is None:
                    elig.sort(key=lambda r: (-r.r2, r.vol))
                else:
                    rng.shuffle(elig)
                eq_now = cash + sum(p['shares'] * closes[t_][j]
                                    for t_, p in positions.items())
                for r in elig[:SLOTS - len(positions)]:
                    outlay = min(eq_now / SLOTS, cash)
                    if outlay < eq_now / SLOTS * 0.5:
                        continue
                    px = closes[r.ticker][j]
                    positions[r.ticker] = {
                        'shares': outlay / (px * (1 + COST)),
                        'entry_px': px, 'entry_i': j}
                    cash -= outlay
                    trades.append({'ticker': r.ticker, 'action': 'buy',
                                   'i': j, 'px': px, 'reason': 'buy',
                                   'entry_i': j, 'entry_px': px, 'ret': np.nan})
        equity[j - j0] = cash + sum(p['shares'] * closes[t][j]
                                    for t, p in positions.items())
    return equity, trades


def metrics(eq: np.ndarray, days) -> dict:
    yrs = len(eq) / 252
    tot = eq[-1] / eq[0] - 1
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    cagr = (1 + tot) ** (1 / yrs) - 1
    return {'total': tot, 'cagr': cagr, 'maxdd': dd,
            'mar': cagr / abs(dd) if dd < 0 else np.nan}


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    data_dir, cal, spy, green, tb_f, spy_f = build_market(cfg)
    closes, last_i = load_prices(data_dir, cal, cfg['data']['benchmark'])
    b2 = build_b2(data_dir, cal, cfg)
    tab = pd.read_parquet(data_dir / 'giants_monthly.parquet')
    by_i = {int(i): g for i, g in tab.groupby('i')}
    all_dec = sorted(by_i)

    today = str(cal[-1].date())
    periods = {}
    for name, a, b in [('dev', bt['start'], bt['dev_end']),
                       ('test', bt['test_start'], today)]:
        j0 = int(cal.searchsorted(pd.Timestamp(a)))
        j1 = int(cal.searchsorted(pd.Timestamp(b), side='right')) - 1
        periods[name] = ((j0, j1), [i for i in all_dec if j0 <= i <= j1])

    import sys
    if '--parking' in sys.argv:
        # user experiment: park idle cash in SPY instead of T-bills,
        # frozen winning config, no grid, no controls
        r2_th, sell_col = 0.7, 'pe_p90'
        rows = []
        for pname, (pi, dl) in periods.items():
            for mode in ('tbill', 'spy_always', 'spy_green'):
                eq, _ = run(pi, dl, by_i, closes, last_i, b2, green, tb_f,
                            r2_th, sell_col, parking=mode, spy_f=spy_f)
                m = metrics(eq, None)
                rows.append({'period': pname, 'parking': mode, **m})
                print(f'{pname:5s} {mode:11s}: total {m["total"]:+.1%} '
                      f'cagr {m["cagr"]:+.2%} maxDD {m["maxdd"]:+.1%} '
                      f'MAR {m["mar"]:.2f}')
        pd.DataFrame(rows).to_csv(results / 'giants_parking.csv',
                                  index=False)
        print(f'-> {results / "giants_parking.csv"}')
        return

    print('=== dev grid (select by MAR, declared in spec/docstring) ===')
    grid = {}
    (pi, dl) = periods['dev']
    for r2_th in R2_GRID:
        for sc in SELL_GRID:
            eq, tr = run(pi, dl, by_i, closes, last_i, b2, green, tb_f,
                         r2_th, sc, spy_f=spy_f)
            m = metrics(eq, None)
            m['n_buys'] = sum(1 for x in tr if x['action'] == 'buy')
            grid[(r2_th, sc)] = m
            print(f'  r2>={r2_th} sell={sc}: total {m["total"]:+.1%} '
                  f'cagr {m["cagr"]:+.2%} maxDD {m["maxdd"]:+.1%} '
                  f'MAR {m["mar"]:.2f} buys {m["n_buys"]}')
    r2_th, sell_col = max(grid, key=lambda k: grid[k]['mar'])
    print(f'selected: r2>={r2_th}, sell={sell_col}')

    fmt = lambda x: f'{x:.4f}'
    summary = {}
    for pname, (pi, dl) in periods.items():
        eq, tr = run(pi, dl, by_i, closes, last_i, b2, green, tb_f,
                     r2_th, sell_col, spy_f=spy_f)
        m = metrics(eq, None)
        trd = pd.DataFrame(tr)
        trd['date'] = [cal[i] for i in trd['i']]
        trd.to_csv(results / f'giants_{pname}_trades.csv', index=False)
        sells = trd[trd['action'] == 'sell']
        m['n_buys'] = int((trd['action'] == 'buy').sum())
        m['avg_hold_y'] = float((sells['i'] - sells['entry_i']).mean() / 252) \
            if len(sells) else np.nan
        # controls
        ctl = []
        for s in range(N_CONTROLS):
            ceq, _ = run(pi, dl, by_i, closes, last_i, b2, green, tb_f,
                         r2_th, sell_col, rng=np.random.default_rng(s),
                         spy_f=spy_f)
            ctl.append(ceq[-1] / ceq[0] - 1)
        ctl = np.array(ctl)
        m['pct_vs_controls'] = float((m['total'] > ctl).mean())
        m['ctl_median'] = float(np.median(ctl))
        summary[pname] = m
        print(f'\n=== {pname} ===')
        print({k: round(v, 4) if isinstance(v, float) else v
               for k, v in m.items()})
        if len(sells):
            print('sell reasons:', sells['reason'].value_counts().to_dict())

        spy_p = spy.iloc[pi[0]:pi[1] + 1]
        tb_eq = np.cumprod(tb_f[pi[0]:pi[1] + 1])
        plt.figure(figsize=(11, 6))
        plt.plot(cal[pi[0]:pi[1] + 1], eq / eq[0], label='giants')
        plt.plot(spy_p.index, spy_p / spy_p.iloc[0], '--', color='gray',
                 label='SPY (context)')
        plt.plot(cal[pi[0]:pi[1] + 1], tb_eq / tb_eq[0], ':', color='green',
                 label='T-bills')
        plt.yscale('log')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.title(f'Steady Giants, {pname} (r2>={r2_th}, sell={sell_col})')
        plt.tight_layout()
        plt.savefig(results / f'giants_{pname}.png', dpi=120)
        plt.close()

    pd.DataFrame(summary).T.to_csv(results / 'giants_summary.csv')

    print('\n=== case studies ===')
    for pname in ('dev', 'test'):
        trd = pd.read_csv(results / f'giants_{pname}_trades.csv')
        cs = trd[trd['ticker'].isin(['KO', 'PG', 'JNJ', 'COST'])]
        if len(cs):
            print(f'[{pname}]')
            print(cs[['ticker', 'action', 'date', 'px', 'reason', 'ret']]
                  .to_string(index=False))
        else:
            print(f'[{pname}] no KO/PG/JNJ/COST trades')
    print(f'\ncharts -> {results}/giants_dev.png, giants_test.png')


if __name__ == '__main__':
    main()
