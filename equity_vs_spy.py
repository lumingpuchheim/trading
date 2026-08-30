"""One continuous equity path 2009-2026, against SPY.

No dev/test split here -- a single simulation across the whole span, which
is what an account would actually have experienced. Start 2009 because the
filter work cannot score 2007-2008 (fewer than 2,000 training rows), so
those years are not comparable across arms.

No fees, no tax, per the user's scope. Positions are v5r's own: 10 slots,
10% each, market light, standard exits.

SPY is drawn twice and the difference matters now that prices are
unadjusted (2026-08-29):
  price only    -- the index level, no dividends
  total return  -- dividends reinvested, derived here from the `dividends`
                   column by the same routine Steady Giants uses
The strategy's own equity credits dividends as cash, so TOTAL RETURN is
the like-for-like reference. Price-only is shown to make the gap visible.

Usage
    python equity_vs_spy.py
    python equity_vs_spy.py --start 2009-01-01
"""

import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from filters import ShapeletFilter
from giants_features import total_return_prices
from geostats import geo_per_bet
from lppl_backtest import ROOT, load_config, metrics
from minervini_backtest import apply_v5, build_panel, pool_by_day, simulate
from bets_common import AUX_Q, LOOKBACK_YEARS, load, warmup_rows, year_blocks
from minervini_rocket import ALPHAS, fit_biases, kernels, transform
from sklearn.linear_model import RidgeClassifierCV

OUT = ROOT / 'results' / 'equity_vs_spy.png'


def main() -> None:
    av = sys.argv
    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    start = opt('--start', '2009-01-01')
    cfg = apply_v5(load_config())
    cfg['minervini_trading']['reentry_fast'] = True
    panel = build_panel(cfg, v5=True)
    cal = panel['calendar']
    pool = pool_by_day(panel['watch'] if 'watch' in panel else panel['setup'])

    j0 = int(cal.searchsorted(pd.Timestamp(start)))
    j1 = len(cal) - 1
    print(f'one continuous run: {cal[j0].date()} .. {cal[j1].date()}')

    # ---- optional filter arms, walk-forward -----------------------------
    # There is no "fit on dev, freeze for test" for a CONTINUOUS path: the
    # early years would be in-sample. A live account refits as data arrives,
    # so each year is scored by a model fitted on everything up to 400 days
    # before it -- and never on the year itself or anything after.
    gates = {}
    if '--filters' in av:
        led = pd.read_csv(ROOT / 'results' / 'minervini_bets_v5r.csv',
                          parse_dates=['entry_date'])
        d = load(str(ROOT / 'results' / 'minervini_bets_v5r_windows.npz'))
        w = pd.DataFrame({'ticker': [str(t) for t in d['ticker']],
                          'entry_date': pd.to_datetime(d['entry_date']),
                          'wrow': np.arange(len(d['y']))})
        mm = (w.merge(led[['ticker', 'entry_date', 'entry_i', 'ticker_j', 'y']],
                      on=['ticker', 'entry_date'], how='inner')
              .drop_duplicates('wrow').reset_index(drop=True))
        xw = d['x'][mm['wrow'].to_numpy()]
        yy = mm['y'].to_numpy(np.float64)
        ei = mm['entry_i'].to_numpy(np.int64)
        tj = mm['ticker_j'].to_numpy(np.int64)
        dt = mm['entry_date'].to_numpy().astype('datetime64[D]')
        yr = mm['entry_date'].dt.year.to_numpy()
        # the label is cut per fold, inside the loop below, from that
        # fold's own training rows (EVALUATION_SPEC.md rule 1)
        aux = np.zeros(len(yy), np.int8)
        years = sorted(set(yr[(ei >= j0) & (ei <= j1)]))
        keep = opt('--keep', 0.5, float)

        W = kernels(); dil = [1, 2, 4, 8, 16]
        qs = np.linspace(0.0, 1.0, 4)[1:-1].astype(np.float32)
        rg = np.random.default_rng(0)
        sd_rows = warmup_rows(dt, 2000, rg)
        feats = transform(xw, W, dil, fit_biases(xw, W, dil, 2, sd_rows, qs))

        only = opt('--only', '')
        kinds = [only] if only else ['rocket', 'shapelet']
        for kind in kinds:
            score = np.full(len(aux), np.nan)
            cut = np.full(len(aux), np.inf)
            for Y, trm, ev in year_blocks(
                    dt, exits, lookback_years=LOOKBACK_YEARS):
                thr = float(np.quantile(yy[trm], AUX_Q))
                a_tr = (yy[trm] >= thr).astype(np.int8)
                if len(set(a_tr.tolist())) < 2:
                    continue
                aux[ev] = (yy[ev] >= thr).astype(np.int8)
                if kind == 'rocket':
                    mu, sdv = feats[trm].mean(0), feats[trm].std(0) + 1e-8
                    clf = RidgeClassifierCV(alphas=ALPHAS,
                                            class_weight='balanced')
                    clf.fit((feats[trm] - mu) / sdv, a_tr)
                    score[ev] = clf.decision_function((feats[ev] - mu) / sdv)
                    s_tr = clf.decision_function((feats[trm] - mu) / sdv)
                else:
                    f = ShapeletFilter(gamma=0.0, seeds=3, epochs=40,
                                       loss='class')
                    f.fit(xw[trm], yy[trm], a_tr.astype(np.float32),
                          keep=keep)
                    score[ev] = f.score(xw[ev])
                    s_tr = f.score(xw[trm])
                cut[ev] = np.quantile(s_tr, keep)
                print(f'  {kind} {Y}: fit {int(trm.sum()):,}, '
                      f'scored {int(ev.sum()):,}', flush=True)
            g = np.ones(panel['close'].shape, bool)
            rej = np.isfinite(score) & (score < cut)
            g[ei[rej], tj[rej]] = False
            gates[kind] = g
            print(f'  {kind}: blocked {int(rej.sum()):,} signals', flush=True)

    tr_, eq, inv, _ = simulate(panel, cfg, (j0, j1), moc=True, pool_days=pool)
    t = pd.DataFrame(tr_)
    m = metrics(t, eq, inv)
    geo = geo_per_bet(t) - 1.0            # one vote per position, not per row
    print(f'v5r  total {m["total_return"]:+.1%}  ann {m["ann_return"]:+.2%}  '
          f'maxDD {m["max_drawdown"]:+.1%}  trades {m["n_trades"]}  '
          f'geo/bet {geo:+.2%}  invested {inv:.1%}')

    bench = cfg['data']['benchmark']
    spy = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'ohlcv'
                          / f'{bench}.parquet').reindex(cal)
    px = spy['close'].to_numpy()
    dd = spy['dividends'].fillna(0.0) if 'dividends' in spy.columns else None
    if dd is not None and (dd > 0).any():
        d2 = dd[dd > 0]
        divs = pd.DataFrame({'date': d2.index, 'amount': d2.to_numpy()})
        tot = total_return_prices(px, cal, divs)
    else:
        tot = px
    seg = slice(j0, j1 + 1)
    idx = cal[seg]
    curves = {'v5r (10 x 10%, no filter)': eq.reindex(idx).ffill()}
    for kind, g in gates.items():
        t2, e2, i2, _ = simulate(panel, cfg, (j0, j1), moc=True,
                                 pool_days=pool, gate=g)
        nm = ('MiniRocket k=0.50' if kind == 'rocket'
              else 'Shapelet g=0 k=0.50')
        curves[nm] = e2.reindex(idx).ffill()
        d2 = pd.DataFrame(t2)
        print(f'{nm:28s} trades {len(d2):5d}  invested {i2:.1%}  '
              f'geo/bet {geo_per_bet(d2) - 1:+.2%}')
    curves.update({
        f'{bench} total return': pd.Series(tot[seg] / tot[j0] * 100_000, idx),
        f'{bench} price only': pd.Series(px[seg] / px[j0] * 100_000, idx),
    })
    yrs = (len(idx)) / 252
    for name, c in curves.items():
        tot_r = c.iloc[-1] / c.iloc[0] - 1
        print(f'{name:28s} final {c.iloc[-1]:10,.0f}  total {tot_r:+8.1%}  '
              f'ann {(1 + tot_r) ** (1 / yrs) - 1:+6.2%}  '
              f'maxDD {(c / c.cummax() - 1).min():+7.1%}')

    print()
    print('=== return by calendar year ===')
    names = list(curves)
    print(f'{"year":6s}' + ''.join(f'{n[:15]:>17s}' for n in names))
    for Y in sorted(set(idx.year)):
        sel = idx[idx.year == Y]
        row = []
        for n in names:
            c = curves[n].loc[sel]
            row.append(f'{c.iloc[-1] / c.iloc[0] - 1:+16.1%} ')
        print(f'{Y:6d}' + ''.join(row))

    fig, ax = plt.subplots(figsize=(13.5, 6))
    styles = [('#1B3B6F', 1.9, '-'), ('#55A868', 1.7, '-'),
              ('#8172B2', 1.7, '-'), ('#C44E52', 1.5, '-'),
              ('#C44E52', 1.2, ':')]
    for (name, c), (col, lw, ls) in zip(curves.items(), styles):
        ax.plot(c.index, c.values, color=col, lw=lw, ls=ls, label=name)
    ax.set_yscale('log')
    ax.set_ylabel('equity (EUR, log scale, start 100,000)')
    ax.set_title(f'v5r vs {bench}, {cal[j0].date()} to {cal[j1].date()} '
                 f'— no fees, no tax')
    ax.legend(loc='upper left', fontsize=10)
    ax.grid(alpha=.15, which='both')
    fig.tight_layout()
    fig.savefig(OUT, dpi=140)
    print(f'-> {OUT}')


if __name__ == '__main__':
    main()
