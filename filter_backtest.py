"""v5r with a trade filter in front of it, through the SAME simulator.

The filter question only has an answer when capital is scarce. v5r sees
~26,000 signals in seven years and holds ten positions, so it already
declines almost all of them -- by arrival order. A filter does not throw
signals away; it decides which one a freed slot is spent on, instead of
letting whoever signalled first take it.

Nothing here re-implements a portfolio. `minervini_backtest.simulate()`
keeps the slots, the cooldown, the market light, the exits and the equity
curve, and takes one new argument: a (days x tickers) boolean `gate`. A
filtered run and an unfiltered one therefore differ by exactly one thing.

WALK-FORWARD BY YEAR. The filter is refitted at the start of every
simulated year on data ending `embargo` days earlier, and its buy
threshold is frozen from that training window's own scores. No year is
judged by a model that saw it, and no threshold depends on the scores of
the block it is applied to.

Signals older than the 252-day window (which the filter cannot see) are
ALLOWED, so the filter never blocks what it had no chance to read.

Usage
    python filter_backtest.py                       # dev, 0/.5/.8/.9 keeps
    python filter_backtest.py --period test
    python filter_backtest.py --keeps 0,0.5,0.8,0.9,0.95
"""

import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeClassifierCV

from lppl_backtest import ROOT, load_config, metrics
from minervini_backtest import (apply_v5, build_panel, pool_by_day,
                                simulate)
from bets_common import AUX_Q, DEV_END, load
from minervini_rocket import (ALPHAS, channel_subsets, fit_biases,
                              fit_biases_mv, kernels, transform,
                              transform_mv)
from filters import ShapeletFilter

LEDGER = ROOT / 'results' / 'minervini_bets_v5r.csv'
LOSS, FB_THRESH, FB_BETA = 'class', 1.05, 1.0
WINDOWS = ROOT / 'results' / 'minervini_bets_v5r_windows.npz'


def score_shapelet(x, y, aux, date, yr, years, embargo, seeds, epochs,
                   channels=(0,)):
    global LOSS, FB_THRESH, FB_BETA
    """Shapelets refitted each year, gamma=0 -- PURE jackpot classification.

    gamma>0 weights each negative by how much money it lost, which turns
    the classifier into a partial value estimator. Zero removes that: the
    only question asked is "is this in the top 20%", and the only weight
    is class balance.
    """
    score = np.full(len(aux), np.nan)
    cuts: dict = {}
    for Y in years:
        tr = date < np.datetime64(f'{Y}-01-01') - np.timedelta64(embargo, 'D')
        ev = yr == Y
        if tr.sum() < 2000 or not ev.any() or len(set(aux[tr])) < 2:
            continue
        f = ShapeletFilter(gamma=0.0, seeds=seeds, epochs=epochs,
                           loss=LOSS, channels=channels,
                           fb_thresh=FB_THRESH, fb_beta=FB_BETA)
        f.fit(x[tr], y[tr], aux[tr], keep=0.5)
        score[ev] = f.score(x[ev])
        cuts[Y] = f.score(x[tr])
        print(f'  {Y}: shapelet on {int(tr.sum()):,} rows, scored '
              f'{int(ev.sum()):,}', flush=True)
    return score, cuts


def jackpot_stats(score, aux, cuts, yr, sel):
    """Judge a jackpot picker on jackpots, not on lift."""
    if sel.sum() == 0:
        return 0.0, 0.0
    prec = float(aux[sel].mean())
    recall = float(aux[sel].sum() / max(1, aux.sum()))
    return prec, recall


def score_walk_forward(feats, aux, date, yr, years, embargo):
    """Score each year with a ridge fitted only on earlier data, and record
    that fit's own score quantiles so a threshold can be frozen per year."""
    score = np.full(len(aux), np.nan)
    cuts: dict = {}
    for Y in years:
        tr = date < np.datetime64(f'{Y}-01-01') - np.timedelta64(embargo, 'D')
        ev = yr == Y
        if tr.sum() < 2000 or not ev.any() or len(set(aux[tr])) < 2:
            continue
        mu, sd = feats[tr].mean(0), feats[tr].std(0) + 1e-8
        clf = RidgeClassifierCV(alphas=ALPHAS, class_weight='balanced')
        clf.fit((feats[tr] - mu) / sd, aux[tr])
        score[ev] = clf.decision_function((feats[ev] - mu) / sd)
        s_tr = clf.decision_function((feats[tr] - mu) / sd)
        cuts[Y] = s_tr
        print(f'  {Y}: fit on {int(tr.sum()):,} rows, scored '
              f'{int(ev.sum()):,}, alpha {clf.alpha_:.3g}', flush=True)
    return score, cuts


def main() -> None:
    av = sys.argv
    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    keeps = [float(k) for k in opt('--keeps', '0,0.5,0.8,0.9').split(',')]
    embargo = opt('--embargo', 400, int)
    which = opt('--period', 'dev')
    kind = opt('--filter', 'rocket')
    mv = '--mv' in av                       # price x volume interaction
    global LOSS, FB_THRESH, FB_BETA
    LOSS = opt('--loss', 'class')
    FB_THRESH = opt('--fb-thresh', 1.05, float)
    FB_BETA = opt('--fb-beta', 1.0, float)
    chans = [int(c) for c in opt('--channels', '0').split(',')]

    cfg = apply_v5(load_config())
    cfg['minervini_trading']['reentry_fast'] = True          # v5r
    panel = build_panel(cfg, v5=True)
    cal = panel['calendar']
    bt = cfg['backtest']
    if which == 'dev':
        a, b = bt['start'], bt['dev_end']
    else:
        a, b = bt['test_start'], str(cal[-1].date())
    j0 = int(cal.searchsorted(pd.Timestamp(a)))
    j1 = int(cal.searchsorted(pd.Timestamp(b), side='right')) - 1
    print(f'period {which}: {cal[j0].date()} .. {cal[j1].date()}')
    # v5 orders come from the WATCH list, not the narrow VCP setup list.
    # simulate() falls back to panel['setup'] when pool_days is None, which
    # silently reduces v5r to the pivot-only system -- 65 trades instead of
    # 832. main() passes this explicitly; so must we.
    pool = pool_by_day(panel['watch'] if 'watch' in panel else panel['setup'])
    print(f'order pool: {"watch" if "watch" in panel else "setup"}, '
          f'{int(panel["trigger_moc"].sum()):,} MOC entries available')

    d = load(str(WINDOWS))
    led = pd.read_csv(LEDGER, parse_dates=['entry_date'])
    w = pd.DataFrame({'ticker': [str(t) for t in d['ticker']],
                      'entry_date': pd.to_datetime(d['entry_date']),
                      'wrow': np.arange(len(d['y']))})
    m = (w.merge(led[['ticker', 'entry_date', 'entry_i', 'ticker_j', 'y']],
                 on=['ticker', 'entry_date'], how='inner')
         .drop_duplicates('wrow').reset_index(drop=True))
    print(f'{len(m):,} of {len(d["y"]):,} windowed signals matched to the ledger')

    x = d['x'][m['wrow'].to_numpy()]
    xw = x        # raw windows, for the shapelet filter
    y = m['y'].to_numpy(np.float64)
    ei = m['entry_i'].to_numpy(np.int64)
    tj = m['ticker_j'].to_numpy(np.int64)
    date = m['entry_date'].to_numpy().astype('datetime64[D]')
    yr = m['entry_date'].dt.year.to_numpy()
    thr = float(np.quantile(y[date <= DEV_END], AUX_Q))
    aux = (y >= thr).astype(np.int8)

    W = kernels()
    dil = [1, 2, 4, 8, 16]
    qs = np.linspace(0.0, 1.0, 4)[1:-1].astype(np.float32)
    rng = np.random.default_rng(0)
    seed = rng.choice(np.flatnonzero(date <= DEV_END),
                      size=min(2000, int((date <= DEV_END).sum())),
                      replace=False)
    if kind != 'rocket':
        feats = np.zeros((len(y), 1), np.float32)
    elif mv:
        subs = channel_subsets(x.shape[1], 5, 0)
        feats = transform_mv(x, W, dil,
                             fit_biases_mv(x, W, dil, 2, seed, qs, subs), subs)
    else:
        feats = transform(x, W, dil, fit_biases(x, W, dil, 2, seed, qs))

    years = sorted(set(yr[(ei >= j0) & (ei <= j1)]))
    label = (f'{kind}{"-MV" if mv and kind == "rocket" else ""}'
             f'{" ch=" + ",".join(map(str, chans)) if kind == "shapelet" else ""}')
    print(f'walk-forward {label} fits for {years[0]}-{years[-1]}:')
    if kind == 'shapelet':
        score, cuts = score_shapelet(xw, y, aux, date, yr, years, embargo,
                                     opt('--seeds', 3, int),
                                     opt('--epochs', 40, int), chans)
    else:
        score, cuts = score_walk_forward(feats, aux, date, yr, years, embargo)

    print(f'\n{"filter":16s} {"total":>9s} {"ann":>7s} {"maxDD":>8s} '
          f'{"trades":>7s} {"geo/trade":>10s} {"invested":>9s} {"blocked":>8s}')
    curves = {}
    for k in keeps:
        gate = None
        blocked = 0
        if k > 0:
            gate = np.ones(panel['close'].shape, bool)
            ok = np.isfinite(score)
            cut = np.array([np.quantile(cuts[Y], k) if Y in cuts else np.inf
                            for Y in yr])
            reject = ok & (score < cut)
            gate[ei[reject], tj[reject]] = False
            blocked = int(reject.sum())
            sel = ok & ~reject
            prec, rec = jackpot_stats(score, aux, cuts, yr, sel)
            base = float(aux[ok].mean())
            print(f'    jackpot precision {prec:.1%} vs base rate {base:.1%} '
                  f'(lift x{prec / max(base, 1e-9):.2f}), recall {rec:.1%}')
        tr_, eq, inv, _ = simulate(panel, cfg, (j0, j1), moc=True,
                                   pool_days=pool, gate=gate)
        tdf = pd.DataFrame(tr_)
        mt = metrics(tdf, eq, inv)
        # GEOMETRIC per trade. The arithmetic mean is not what compounds and
        # is not comparable to anything else reported here.
        rr = tdf['ret_net'].to_numpy() if len(tdf) else np.array([0.0])
        geo = float(np.exp(np.log1p(rr).mean()) - 1)
        name = ('AllPass (v5r)' if k == 0
                else f'{kind.capitalize()} k={k:.2f}')
        print(f'{name:16s} {mt["total_return"]:+8.1%} '
              f'{mt["ann_return"]:+6.1%} {mt["max_drawdown"]:+7.1%} '
              f'{mt["n_trades"]:7d} {geo:+9.2%} '
              f'{inv:8.1%} {blocked:8,d}')
        curves[name] = eq

    print(f'\n=== equity at each year end, {which} ===')
    names = list(curves)
    print(f'{"year":6s}' + ''.join(f'{n[:14]:>16s}' for n in names))
    idx = curves[names[0]].index
    for Y in sorted(set(idx.year)):
        sel = idx[idx.year == Y]
        print(f'{Y:6d}' + ''.join(
            f'{curves[n].loc[sel].iloc[-1]:16,.0f}' for n in names))


if __name__ == '__main__':
    main()
