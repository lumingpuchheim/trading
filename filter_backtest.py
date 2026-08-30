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
simulated year on data that had already closed out, and its buy
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
from sklearn.linear_model import RidgeClassifier, RidgeClassifierCV

import fitcache
import modelstore
from geostats import geo_per_bet
from lppl_backtest import ROOT, load_config, metrics
from minervini_backtest import (apply_v5, build_panel, pool_by_day,
                                simulate)
from bets_common import (AUX_Q, LEGACY_EMBARGO, LOOKBACK_YEARS,
                         MIN_TRAIN, load, warmup_rows, year_blocks)
from minervini_rocket import (ALPHAS, channel_subsets, fit_biases,
                              fit_biases_mv, kernels, transform,
                              transform_mv)
from filters import AllPass, ShapeletFilter

LEDGER = ROOT / 'results' / 'minervini_bets_v5r.csv'
LOSS, FB_THRESH, FB_BETA = 'class', 1.05, 1.0
WINDOWS = ROOT / 'results' / 'minervini_bets_v5r_windows.npz'


def score_shapelet(x, y, blocks, seeds, epochs, channels=(0,), src=''):
    global LOSS, FB_THRESH, FB_BETA
    """Shapelets refitted each block, gamma=0 -- PURE jackpot classification.

    gamma>0 weights each negative by how much money it lost, which turns
    the classifier into a partial value estimator. Zero removes that: the
    only question asked is "is this in the top 20%", and the only weight
    is class balance.

    The label is cut at AUX_Q of THIS fold's training rows, so a 2026
    block is never judged against a threshold made in 2018.
    """
    score = np.full(len(y), np.nan)
    aux = np.zeros(len(y), np.int8)
    cuts: dict = {}
    for Y, tr, ev in blocks:
        thr = float(np.quantile(y[tr], AUX_Q))
        a_tr = (y[tr] >= thr).astype(np.int8)
        if len(set(a_tr.tolist())) < 2:
            continue
        aux[ev] = (y[ev] >= thr).astype(np.int8)
        ck = fitcache.key('shapelet', src, seeds, epochs, tuple(channels),
                          LOSS, FB_THRESH, FB_BETA, AUX_Q, tr, ev)
        hit = fitcache.load('block', ck)
        if hit is not None:
            score[ev] = hit['score']
            cuts[Y] = hit['cut']
            print(f'  {Y}: cached ({int(tr.sum()):,} train rows)', flush=True)
            continue
        f = ShapeletFilter(gamma=0.0, seeds=seeds, epochs=epochs,
                           loss=LOSS, channels=channels,
                           fb_thresh=FB_THRESH, fb_beta=FB_BETA)
        f.fit(x[tr], y[tr], a_tr, keep=0.5)
        score[ev] = f.score(x[ev])
        cuts[Y] = f.score(x[tr])
        fitcache.save('block', ck, score=score[ev].astype(np.float32),
                      cut=np.asarray(cuts[Y], np.float32))
        print(f'  {Y}: shapelet on {int(tr.sum()):,} rows, scored '
              f'{int(ev.sum()):,}, label y>={thr:.4f}', flush=True)
    return score, cuts, aux


def jackpot_stats(score, aux, cuts, yr, sel):
    """Judge a jackpot picker on jackpots, not on lift."""
    if sel.sum() == 0:
        return 0.0, 0.0
    prec = float(aux[sel].mean())
    recall = float(aux[sel].sum() / max(1, aux.sum()))
    return prec, recall


def score_from_store(feats, y, date, blocks, embargo, lookback, alpha, src,
                     min_train=MIN_TRAIN):
    """Score every block from the model store (MODEL_STORE_SPEC.md).

    A model is identified by when its training ENDED, not by the block it
    scores, so changing the embargo changes only which stored model each
    block looks up. The first run over a grid pays for the fits; every
    embargo after that is a dot product.

    The realised embargo is returned and printed, because the month-end
    grid can only snap DOWN -- ask for 400 days and the model may have
    stopped 428 days out. Never reported as the requested value.
    """
    grid = modelstore.month_ends(date)
    score = np.full(len(y), np.nan)
    aux = np.zeros(len(y), np.int8)
    cuts, realised, hits = {}, [], 0
    for Y, _, ev in blocks:
        opens = np.datetime64(f'{Y}-01-01')
        need = opens - np.timedelta64(int(embargo), 'D')
        te = modelstore.snap(grid, need)
        if te is None:
            continue
        rec, hit = modelstore.get_or_fit(src, 'ridge', te, lookback, alpha,
                                         AUX_Q, feats, y, date, min_train)
        if rec is None:
            continue
        hits += hit
        # correctness rule 2: a model may never have trained into its block
        assert te < opens, f'{Y}: model trained to {te}, block opens {opens}'
        score[ev] = modelstore.apply(rec, feats, ev)
        cuts[Y] = rec['train_scores']
        aux[ev] = (y[ev] >= float(rec['thr'])).astype(np.int8)
        realised.append(int((opens - te).astype('timedelta64[D]').astype(int)))
        print(f'  {Y}: model trained to {te} '
              f'({int(rec["n_train"]):,} rows), '
              f'{"cached" if hit else "fitted"}', flush=True)
    if realised:
        print(f'  realised embargo: asked {embargo}d, got '
              f'{min(realised)}-{max(realised)}d '
              f'(median {int(np.median(realised))}d); '
              f'{hits}/{len(realised)} blocks came from the store')
    return score, cuts, aux, realised


def score_walk_forward(feats, y, blocks, alpha=100, src=''):
    """Score each block with a ridge fitted only on earlier data, and
    record that fit's own score quantiles so the decision cut can be
    frozen per block.

    Everything the fold uses comes from the fold's own training window:
    the label threshold, the standardisation, and the cut. No calendar
    constant appears anywhere (EVALUATION_SPEC.md)."""
    score = np.full(len(y), np.nan)
    aux = np.zeros(len(y), np.int8)
    cuts: dict = {}
    for Y, tr, ev in blocks:
        thr = float(np.quantile(y[tr], AUX_Q))
        a_tr = (y[tr] >= thr).astype(np.int8)
        if len(set(a_tr.tolist())) < 2:
            continue
        aux[ev] = (y[ev] >= thr).astype(np.int8)

        # A block's fit depends on its TRAINING ROWS, not on the flags that
        # produced them, so the key is the masks themselves: --until 2018
        # and the full run hash the same for 2009-2018 and share the entry.
        ck = fitcache.key('ridge', src, alpha, AUX_Q, tr, ev)
        hit = fitcache.load('block', ck)
        if hit is not None:
            score[ev] = hit['score']
            cuts[Y] = hit['cut']
            print(f'  {Y}: cached ({int(tr.sum()):,} train rows)', flush=True)
            continue

        # Standardise in place, in float32. `(feats[tr] - mu) / sd` makes
        # two more copies of a 49,334 x 4,200 array on the last blocks of
        # an expanding window, which is 1.7 GB of avoidable peak and is
        # what killed this run twice on a 16 GB machine.
        mu, sd = feats[tr].mean(0), feats[tr].std(0) + 1e-8
        xt = np.asarray(feats[tr], dtype=np.float32)
        xt -= mu.astype(np.float32)
        xt /= sd.astype(np.float32)
        if alpha == 'cv':
            clf = RidgeClassifierCV(alphas=ALPHAS, class_weight='balanced')
        else:
            clf = RidgeClassifier(alpha=float(alpha),
                                  class_weight='balanced')
        clf.fit(xt, a_tr)
        cuts[Y] = clf.decision_function(xt)
        del xt
        xe = np.asarray(feats[ev], dtype=np.float32)
        xe -= mu.astype(np.float32)
        xe /= sd.astype(np.float32)
        score[ev] = clf.decision_function(xe)
        del xe
        fitcache.save('block', ck, score=score[ev].astype(np.float32),
                      cut=np.asarray(cuts[Y], np.float32))
        used = getattr(clf, 'alpha_', alpha)
        print(f'  {Y}: fit on {int(tr.sum()):,} rows, scored '
              f'{int(ev.sum()):,}, label y>={thr:.4f}, '
              f'alpha {used}', flush=True)
    return score, cuts, aux


def main() -> None:
    av = sys.argv
    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    keeps = [float(k) for k in opt('--keeps', '0,0.5,0.8,0.9').split(',')]
    # THE SCHEDULE KNOBS (bets_common.year_blocks):
    #   --lookback 0       expanding, every year of history (the default)
    #                      3 = train on the last 3 years only
    #   --purge embargo    the default: keep a training bet if it was
    #                      ENTERED --embargo days before the block
    #          exact       keep it if it CLOSED before the block; needs no
    #                      constant, but is not the default by decision
    lookback = opt('--lookback', LOOKBACK_YEARS or 0, float) or None
    # 134 of 136 fits in the 2026-08-29/30 runs chose alpha=100 out of the
    # 17 RidgeClassifierCV searches, so the search is nearly all waste.
    # --alpha cv restores it; the two blocks that differed chose 316.
    alpha = opt('--alpha', 100, str)
    alpha = 'cv' if alpha == 'cv' else float(alpha)
    # --no-store refits every block instead of reusing the model store.
    # The two must agree; tests/test_modelstore.py pins that they do.
    no_store = '--no-store' in av
    purge = opt('--purge', 'embargo')
    # --embargo takes a LIST. The MiniRocket transform does not depend on
    # the embargo, so one process pays for it once and sweeps the values.
    # The FITS do depend on it -- the embargo decides which rows are in
    # each block's training set -- so those are redone for every value.
    embargos = ([int(v) for v in
                 str(opt('--embargo', LEGACY_EMBARGO, str)).split(',')]
                if purge == 'embargo' else [None])
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
    # ONE continuous window, start to today (EVALUATION_SPEC.md). The
    # holding out happens inside, per block, not by reserving a slice of
    # the calendar for the whole run. --from / --until narrow the SIMULATED
    # span so a historical measurement can be reproduced; they are not a
    # development / test split and nothing is held back by using them.
    j0 = int(cal.searchsorted(pd.Timestamp(opt('--from', bt['start']))))
    j1 = (int(cal.searchsorted(pd.Timestamp(opt('--until', '')), side='right')) - 1
          if '--until' in av else len(cal) - 1)
    print(f'window: {cal[j0].date()} .. {cal[j1].date()}')
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
    m = (w.merge(led[['ticker', 'entry_date', 'exit_date', 'entry_i',
                      'ticker_j', 'y']],
                 on=['ticker', 'entry_date'], how='inner')
         .drop_duplicates('wrow').reset_index(drop=True))
    print(f'{len(m):,} of {len(d["y"]):,} windowed signals matched to the ledger')

    x = d['x'][m['wrow'].to_numpy()]
    xw = x        # raw windows, for the shapelet filter
    y = m['y'].to_numpy(np.float64)
    ei = m['entry_i'].to_numpy(np.int64)
    tj = m['ticker_j'].to_numpy(np.int64)
    date = m['entry_date'].to_numpy().astype('datetime64[D]')
    exits = pd.to_datetime(m['exit_date']).to_numpy().astype('datetime64[D]')
    yr = m['entry_date'].dt.year.to_numpy()

    W = kernels()
    dil = [1, 2, 4, 8, 16]
    qs = np.linspace(0.0, 1.0, 4)[1:-1].astype(np.float32)
    rng = np.random.default_rng(0)
    seed = warmup_rows(date, 2000, rng)
    # The transform does not depend on the embargo, the lookback or the
    # simulated window -- only on the windows file and the kernel setup --
    # so it is built once and reused by every later run.
    src = fitcache.file_key(WINDOWS)
    tkey = fitcache.key('transform', src, tuple(dil), 2, mv, x.shape)
    if kind != 'rocket':
        feats = np.zeros((len(y), 1), np.float32)
    elif mv:
        subs = channel_subsets(x.shape[1], 5, 0)
        feats = fitcache.cached_big(
            'feats_mv', tkey,
            lambda: transform_mv(x, W, dil,
                                 fit_biases_mv(x, W, dil, 2, seed, qs, subs),
                                 subs))
    else:
        feats = fitcache.cached_big(
            'feats', tkey,
            lambda: transform(x, W, dil,
                              fit_biases(x, W, dil, 2, seed, qs)))

    label = (f'{kind}{"-MV" if mv and kind == "rocket" else ""}'
             f'{" ch=" + ",".join(map(str, chans)) if kind == "shapelet" else ""}')

    # A filter can only reject a signal it has a score for. If the ledger
    # ever stops covering what simulate() can order, the arms below stop
    # being the same experiment minus one thing -- so say so, loudly.
    # Does not depend on the schedule, so it is checked once.
    orderable = np.zeros(panel['close'].shape, bool)
    prev_green = np.zeros(len(cal), bool)
    prev_green[1:] = panel['green'][:-1]
    orderable[j0:j1 + 1] = (panel['trigger_moc'] & prev_green[:, None])[j0:j1 + 1]
    scored = np.zeros_like(orderable)
    scored[ei, tj] = True
    gap = int((orderable & ~scored).sum())
    print(f'orderable signals with no score: {gap:,}'
          + ('  <-- every arm takes these unconditionally' if gap else ''))

    curves, sweep, allpass = {}, [], None
    for embargo in embargos:
        blocks = year_blocks(date, exits, lookback_years=lookback,
                             embargo_days=embargo)
        # only score blocks the run actually simulates
        lo, hi = cal[j0].year, cal[j1].year
        blocks = [b for b in blocks if lo <= b[0] <= hi]
        years = [Y for Y, _, _ in blocks]
        rows = [int(t.sum()) for _, t, _ in blocks]
        print(f'\nschedule: purge='
              f'{f"blanket {embargo}d" if embargo else "on exit date"}, '
              f'lookback={f"{lookback:g}y" if lookback else "expanding"}, '
              f'{len(blocks)} blocks {years[0]}-{years[-1]}, '
              f'{min(rows):,}-{max(rows):,} training rows each, '
              f'{sum(rows):,} fitted on in total')
        print(f'walk-forward {label} fits:')
        if kind == 'shapelet':
            score, cuts, aux = score_shapelet(xw, y, blocks,
                                              opt('--seeds', 3, int),
                                              opt('--epochs', 40, int),
                                              chans, src=src)
        elif embargo is not None and not no_store:
            # the model store only applies when the training window is
            # defined by a cut-off date. Exact purging defines it per
            # block (exit < block open), so it cannot be shared.
            score, cuts, aux, _ = score_from_store(
                feats, y, date, blocks, embargo, lookback, alpha, src)
        else:
            score, cuts, aux = score_walk_forward(feats, y, blocks,
                                                  alpha=alpha, src=src)

        print(f'\n{"filter":16s} {"total":>9s} {"ann":>7s} {"maxDD":>8s} '
              f'{"trades":>7s} {"geo/bet":>10s} {"invested":>9s} '
              f'{"blocked":>8s}')
        for k in keeps:
            blocked, lift = 0, float('nan')
            # The baseline IS a filter -- AllPass, which approves everything
            # -- and it runs the same path as every other arm so the two
            # cannot drift into being different code (EVALUATION_SPEC rule 3).
            # It rejects nothing, so it is identical for every embargo and
            # is simulated once.
            if k == 0 and allpass is not None:
                print(allpass)
                continue
            gate = np.ones(panel['close'].shape, bool)
            gate[ei, tj] = AllPass().fit(xw, y, aux).decide(xw)
            if k > 0:
                ok = np.isfinite(score)
                cut = np.array([np.quantile(cuts[Y], k) if Y in cuts
                                else np.inf for Y in yr])
                reject = ok & (score < cut)
                gate[ei[reject], tj[reject]] = False
                blocked = int(reject.sum())
                sel = ok & ~reject
                prec, rec = jackpot_stats(score, aux, cuts, yr, sel)
                base = float(aux[ok].mean())
                lift = prec / max(base, 1e-9)
                print(f'    jackpot precision {prec:.1%} vs base rate '
                      f'{base:.1%} (lift x{lift:.2f}), recall {rec:.1%}')
            tr_, eq, inv, _ = simulate(panel, cfg, (j0, j1), moc=True,
                                       pool_days=pool, gate=gate)
            tdf = pd.DataFrame(tr_)
            mt = metrics(tdf, eq, inv)
            # THE per-bet number, and the only one: geostats.geo_per_bet.
            # This used to average ROWS, which counts a position that sold
            # half at +20% as two bets and a loser as one.
            geo = geo_per_bet(tdf) - 1.0
            name = ('AllPass (v5r)' if k == 0
                    else f'{kind.capitalize()} k={k:.2f}'
                         + (f' e{embargo}' if len(embargos) > 1 else ''))
            line_ = (f'{name:16s} {mt["total_return"]:+8.1%} '
                     f'{mt["ann_return"]:+6.1%} {mt["max_drawdown"]:+7.1%} '
                     f'{mt["n_trades"]:7d} {geo:+9.2%} '
                     f'{inv:8.1%} {blocked:8,d}')
            print(line_)
            curves[name] = eq
            # the arm's transactions, so no later question about this book
            # needs the whole run again
            tag_ = 'allpass' if k == 0 else f'{kind}_k{k:.2f}'
            if embargo is not None:
                tag_ += f'_e{embargo}'
            tdf.to_csv(ROOT / 'results' / f'filter_trades_{tag_}.csv',
                       index=False)
            if k == 0:
                allpass = line_
            else:
                sweep.append({'embargo': embargo, 'blocks': len(blocks),
                              'rows': sum(rows), 'keep': k,
                              'total': mt['total_return'],
                              'ann': mt['ann_return'],
                              'dd': mt['max_drawdown'], 'geo': geo,
                              'inv': inv, 'blocked': blocked, 'lift': lift})

    if len(embargos) > 1:
        print(f'\n=== embargo sweep, {label}, lookback '
              f'{f"{lookback:g}y" if lookback else "expanding"} '
              f'(AllPass rejects nothing and is the same in every row) ===')
        print(f'{"embargo":>8s} {"keep":>5s} {"blocks":>7s} {"train rows":>11s} '
              f'{"total":>9s} {"ann":>7s} {"maxDD":>8s} {"geo/bet":>9s} '
              f'{"invested":>9s} {"blocked":>8s} {"lift":>6s}')
        for r in sweep:
            print(f'{r["embargo"]:7d}d {r["keep"]:5.2f} {r["blocks"]:7d} '
                  f'{r["rows"]:11,d} {r["total"]:+8.1%} {r["ann"]:+6.1%} '
                  f'{r["dd"]:+7.1%} {r["geo"]:+8.2%} {r["inv"]:8.1%} '
                  f'{r["blocked"]:8,d} {r["lift"]:5.2f}x')

    # The daily curves, not just the year ends: intra-year drawdowns are
    # invisible at annual resolution, and the reported maxDD never appears
    # in a year-end table.
    cur = pd.DataFrame(curves).sort_index()
    cpath = ROOT / 'results' / f'filter_curves_{kind}.csv'
    cur.to_csv(cpath)
    print(f'daily equity curves -> {cpath.name} '
          f'({len(cur):,} days x {cur.shape[1]} arms)')

    print('=== equity at each year end ===')
    names = list(curves)
    print(f'{"year":6s}' + ''.join(f'{n[:14]:>16s}' for n in names))
    idx = curves[names[0]].index
    for Y in sorted(set(idx.year)):
        sel = idx[idx.year == Y]
        print(f'{Y:6d}' + ''.join(
            f'{curves[n].loc[sel].iloc[-1]:16,.0f}' for n in names))


if __name__ == '__main__':
    main()
