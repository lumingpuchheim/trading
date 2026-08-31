"""The ranker walk-forward: four keys or four thousand, one slot decision.

    features -> ranker.score() -> (days x tickers) rates -> simulate()

One trained model produces one number per orderable signal -- the
predicted growth rate of a euro spent on it, in ln per trading day -- and
`simulate()` fills each day's free slots from the top of those numbers.
There is no veto, no threshold, no `--keeps` and no strength key in the
slot decision. Selectivity is slot capacity and nothing else.

This file replaces the veto driver of the same name, retired on
2026-08-31 (DECISIONS.md, "The filter architecture is wrong";
RANKER_SPEC.md is the contract). The old chain binarised the outcome
into a top-quantile label, collapsed the trained score into a boolean at
a frozen quantile, and then let a hard-coded sort the loss had never
seen make the pick the money rode on. Three lossy conversions between
the goal and the decision; none of them is left here.

THE CONTROL RUNS FIRST, ALWAYS. `strength` fits nothing and encodes the
ordering the book uses today, so it must reproduce today's AllPass book
row for row and +291.5% over 2007-01-03 .. 2026-08-27. The run computes
that book itself, in the same process, and compares row for row before
it prints a single fitted number. A mismatch stops the run.

WALK-FORWARD BY YEAR (`bets_common.year_blocks`): expanding window, 400
day embargo, everything a fold uses -- the standardisation, the alpha,
the AUC label -- cut from that fold's own training rows. No file names a
year. Years with too little history behind them to fit on keep today's
ordering in every arm, so the arms differ only where the model exists.

Usage
    python filter_backtest.py                     # both arms, full record
    python filter_backtest.py --arms strength     # the control alone
    python filter_backtest.py --until 2014-12-31  # fail fast (see below)
    python filter_backtest.py --alpha 100         # skip the alpha search
    python filter_backtest.py --dump              # + each arm's trades

The fail-fast window is 2014, not 2012: the alpha criterion purges 400
days either side of every held-out year inside the training window, and
a fold needs two years to survive that, so `--until 2012-12-31` can
contain ZERO fitted folds and prove nothing.
"""

import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

import fitcache
from bets_common import (AUX_Q, EMBARGO_DAYS, INNER_MIN, LOOKBACK_YEARS,
                         T_FLOOR, load, rate_target, warmup_rows,
                         year_blocks)
from geostats import bet_multiples, geo_mean_per_euro
from lppl_backtest import ROOT, load_config, metrics
from minervini_backtest import apply_v5, build_panel, pool_by_day, simulate
from minervini_rocket import fit_biases, kernels, transform
from rankers import YCV_ALPHAS, RidgeRanker, strength_matrix

LEDGER = ROOT / 'results' / 'minervini_bets_v5r.csv'
WINDOWS = ROOT / 'results' / 'minervini_bets_v5r_windows.npz'
KEY_COLS = ('rsl_hi', 'weak', 'rs')
DILATIONS = (1, 2, 4, 8, 16)
N_BIAS = 2


# ----------------------------------------------------------------------
# features
# ----------------------------------------------------------------------

def key_columns(panel: dict, ei: np.ndarray, tj: np.ndarray) -> np.ndarray:
    """The three keys the old picker spent, as model features.

    This is what makes the architecture safe: a fit that puts weight on
    these and nothing else IS today's ordering, so the baseline sits
    inside the hypothesis space and persistent underperformance of it
    stops being an available failure mode of the design.

    Read on the day the ORDER is placed -- `entry_i - 1` -- because that
    is the instant `simulate()` reads them, so this column and the
    control arm see the same number. Each is carried as a (value filled
    with 0, finite-indicator) pair: `weak` is finite in 8 of 55,737 rows,
    and a column that is 99.99% NaN must not be allowed to look like a
    column that is 99.99% zero.
    """
    i = np.maximum(ei - 1, 0)
    out = []
    for nm in KEY_COLS:
        v = np.asarray(panel[nm][i, tj], dtype=np.float64)
        ok = np.isfinite(v)
        out += [np.where(ok, v, 0.0), ok.astype(np.float64)]
    return np.stack(out, axis=1).astype(np.float32)


def rocket_features(x: np.ndarray, date: np.ndarray, src: str):
    """MiniRocket, exactly today's: 84 fixed kernels, five dilations, two
    biases, PPV. Nothing here is learned, the transform key is unchanged,
    and the cache built before the architecture changed is still valid."""
    W = kernels()
    qs = np.linspace(0.0, 1.0, N_BIAS + 2)[1:-1].astype(np.float32)
    seed = warmup_rows(date, 2000, np.random.default_rng(0))
    tkey = fitcache.key('transform', src, tuple(DILATIONS), N_BIAS, False,
                        x.shape)
    return fitcache.cached_big(
        'feats', tkey,
        lambda: transform(x, W, DILATIONS,
                          fit_biases(x, W, DILATIONS, N_BIAS, seed, qs)))


# ----------------------------------------------------------------------
# the walk-forward
# ----------------------------------------------------------------------

def fold_metrics(r_tr, p_tr, r_ev, p_ev) -> dict:
    """The six numbers a fold reports. Only `mse` is the loss; `spear` is
    the quantity the slot decision actually uses, and `auc` is graded on
    the TRAINING window's own top-20% cut -- diagnostic, nothing trains
    on it."""
    thr = float(np.quantile(r_tr, AUX_Q))

    def auc(r, p):
        lab = (r >= thr).astype(np.int8)
        return (roc_auc_score(lab, p) if 0 < lab.sum() < len(lab)
                else float('nan'))

    with np.errstate(invalid='ignore'):
        return {'mse_tr': float(np.mean((p_tr - r_tr) ** 2)),
                'mse_ev': float(np.mean((p_ev - r_ev) ** 2)),
                'sp_tr': float(spearmanr(p_tr, r_tr).statistic),
                'sp_ev': float(spearmanr(p_ev, r_ev).statistic),
                'auc_tr': float(auc(r_tr, p_tr)),
                'auc_ev': float(auc(r_ev, p_ev))}


def fold_line(Y, n_train, m, alpha, cached, r2, years) -> str:
    # `r2` is the loss against the only honest null there is: predict
    # this fold's own TRAINING mean for every bet in the block. A raw mse
    # on a quantity with sd 7.4e-03 says nothing on its own -- the first
    # full run read 1.45e-04 as "small" when it is eight times worse than
    # a constant. Negative means the fit's LEVEL is wrong, which the slot
    # decision survives only because it reads nothing but rank.
    return (f'  {Y}  train {n_train:>7,d}   '
            f'mse {m["mse_tr"]:.2e} / {m["mse_ev"]:.2e}  R2oof {r2:+6.2f}   '
            f'spear {m["sp_tr"]:+.2f} / {m["sp_ev"]:+.2f}   '
            f'auc {m["auc_tr"]:.2f} / {m["auc_ev"]:.2f}   '
            f'alpha {alpha:g} ({years}y)'
            f'{"  (cached)" if cached else ""}')


def score_walk_forward(feats, keys, r, date, blocks, alpha, src, floor,
                       embargo):
    """Fit each block on earlier rows only and score it out of fold.

    These fits are closed form -- there are no epochs, so there is no
    loss curve. The training record is one line per fold, printed as the
    fold completes, train and out-of-fold side by side.

    A fold's rows are concatenated onto the key columns fold by fold
    rather than once up front: `feats` is a 0.94 GB memory map, and
    materialising it whole beside a standardised copy is the avoidable
    peak that killed this run twice on a 16 GB machine.
    """
    score = np.full(len(r), np.nan)
    r2s, n_ev = [], []
    for Y, tr, ev in blocks:
        # the null the loss has to beat, computed here rather than stored:
        # it needs no fit, so a cached fold reports it too
        null = float(np.mean((r[ev] - r[tr].mean()) ** 2))
        # a NEW cache name, with the grid and the criterion's own two
        # constants in the key: the measured `ridge-loo` entries stay on
        # disk untouched as the record behind the DECISIONS row
        ck = fitcache.key('ridge-ycv', src, alpha, floor, keys.shape[1],
                          tuple(YCV_ALPHAS), embargo, INNER_MIN, tr, ev)
        hit = fitcache.load('block', ck)
        if hit is not None:
            score[ev] = hit['score']
            m = {k: float(hit[k]) for k in ('mse_tr', 'mse_ev', 'sp_tr',
                                            'sp_ev', 'auc_tr', 'auc_ev')}
            r2 = 1.0 - m['mse_ev'] / null
            r2s.append(r2)
            n_ev.append(int(ev.sum()))
            print(fold_line(Y, int(tr.sum()), m, float(hit['alpha']), True,
                            r2, int(hit['years'])), flush=True)
            continue
        xt = np.concatenate([np.asarray(feats[tr], np.float32), keys[tr]], 1)
        rk = RidgeRanker(alpha=alpha, embargo=embargo).fit(xt, r[tr],
                                                           date[tr])
        if not rk.fitted_:
            # not enough purged years inside the training window to
            # choose alpha honestly. Fit nothing and leave the block on
            # the control ordering, exactly as the pre-2009 years are.
            del xt
            print(f'  {Y}  train {int(tr.sum()):>7,d}   fewer than two '
                  f'purged years in the window: no fit, block keeps the '
                  f'control ordering', flush=True)
            continue
        p_tr = rk.train_pred_
        del xt
        xe = np.concatenate([np.asarray(feats[ev], np.float32), keys[ev]], 1)
        p_ev = rk.score(xe)
        del xe
        score[ev] = p_ev
        m = fold_metrics(r[tr], p_tr, r[ev], p_ev)
        fitcache.save('block', ck, score=p_ev.astype(np.float64),
                      alpha=np.float64(rk.alpha_),
                      years=np.int64(len(rk.years_)),
                      **{k: np.float64(v) for k, v in m.items()})
        r2 = 1.0 - m['mse_ev'] / null
        r2s.append(r2)
        n_ev.append(int(ev.sum()))
        print(fold_line(Y, int(tr.sum()), m, rk.alpha_, False, r2,
                        len(rk.years_)), flush=True)
    if r2s:
        w = np.asarray(n_ev, float)
        print(f'  loss against the null (predict the training mean): '
              f'row-weighted R2 out of fold '
              f'{float(np.average(r2s, weights=w)):+.3f}, better than a '
              f'constant in {int(sum(v > 0 for v in r2s))} of {len(r2s)} '
              f'folds', flush=True)
    return score


# ----------------------------------------------------------------------
# the book
# ----------------------------------------------------------------------

def report_book(name, tdf, eq, inv, rate_of, pool_r, pool_y) -> None:
    """One arm's book: the portfolio, the per-bet multiple, and the rate
    the target is written in -- each printed beside the same number for
    the whole candidate pool, so a difference reads as selection rather
    than as a level."""
    mt = metrics(tdf, eq, inv)
    mult = bet_multiples(tdf)
    took = np.array([rate_of.get(k, np.nan) for k in mult.index], float)
    took = took[np.isfinite(took)]
    g_day = float(np.exp(np.mean(took))) if len(took) else float('nan')
    print(f'{name:14s} {mt["total_return"]:+9.1%} {mt["ann_return"]:+7.1%} '
          f'{mt["max_drawdown"]:+8.1%} {len(tdf):7,d} {len(mult):6,d} '
          f'{geo_mean_per_euro(mult) - 1:+9.2%} {g_day - 1:+10.4%} '
          f'{inv:8.1%}')
    print(f'{"":14s} {"":9s} {"":7s} {"":8s} {"":7s} '
          f'{len(pool_y):6,d} {geo_mean_per_euro(pool_y) - 1:+9.2%} '
          f'{float(np.exp(np.mean(pool_r))) - 1:+10.4%}   <- the whole pool')


def main() -> None:
    av = sys.argv

    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    arms = [a for a in opt('--arms', 'strength,rocket').split(',') if a]
    alpha = opt('--alpha', 'cv', str)
    alpha = 'cv' if alpha == 'cv' else float(alpha)
    embargo = opt('--embargo', EMBARGO_DAYS, int)
    lookback = opt('--lookback', LOOKBACK_YEARS or 0, float) or None
    floor = opt('--floor', T_FLOOR, int)
    # the natural zero: cash grows at 0.0/day, so a slot MAY stay empty
    # when the best candidate's predicted rate is negative. Read off the
    # predicted quantity itself, never a tuned threshold. Off by default
    # -- the market light already does the regime version of this.
    min_score = opt('--min-score', None, float)
    # everything goes to the console (RANKER_SPEC.md). --dump writes each
    # arm's transactions as well, for when a later question about one
    # book should not cost the whole run again.
    dump = '--dump' in av

    cfg = apply_v5(load_config())
    cfg['minervini_trading']['reentry_fast'] = True          # v5r
    if '--no-fees' in av:
        cfg['minervini_trading']['cost_per_side'] = 0.0
        print('fees OFF: cost_per_side = 0')
    panel = build_panel(cfg, v5=True)
    cal, bt = panel['calendar'], cfg['backtest']
    j0 = int(cal.searchsorted(pd.Timestamp(opt('--from', bt['start']))))
    j1 = (int(cal.searchsorted(pd.Timestamp(opt('--until', '')),
                               side='right')) - 1
          if '--until' in av else len(cal) - 1)
    # v5 orders come from the WATCH list, not the narrow VCP setup list.
    # simulate() falls back to panel['setup'] when pool_days is None,
    # which silently reduces v5r to the pivot-only system.
    pool = pool_by_day(panel['watch'] if 'watch' in panel else panel['setup'])

    d = load(str(WINDOWS))
    led = pd.read_csv(LEDGER, parse_dates=['entry_date'])
    need = ['y', 'days_held', 'half_frac', 'y_half', 'half_days_held']
    missing = [c for c in need if c not in led.columns]
    if missing:
        sys.exit(f'{LEDGER.name} is missing {missing}; the split legs are '
                 f'part of the target now. Rebuild it with:\n'
                 f'    python minervini_bets.py --windows 252')
    w = pd.DataFrame({'ticker': [str(t) for t in d['ticker']],
                      'entry_date': pd.to_datetime(d['entry_date']),
                      'wrow': np.arange(len(d['y']))})
    m = (w.merge(led[['ticker', 'entry_date', 'exit_date', 'entry_i',
                      'ticker_j'] + need],
                 on=['ticker', 'entry_date'], how='inner')
         .drop_duplicates('wrow').reset_index(drop=True))
    x = d['x'][m['wrow'].to_numpy()]
    ei = m['entry_i'].to_numpy(np.int64)
    tj = m['ticker_j'].to_numpy(np.int64)
    date = m['entry_date'].to_numpy().astype('datetime64[D]')
    exits = pd.to_datetime(m['exit_date']).to_numpy().astype('datetime64[D]')
    r = rate_target(m['y'].to_numpy(np.float64),
                    m['days_held'].to_numpy(np.float64),
                    m['half_frac'].to_numpy(np.float64),
                    m['y_half'].to_numpy(np.float64),
                    m['half_days_held'].to_numpy(np.float64), floor)
    pool_y = m['y'].to_numpy(np.float64)
    # the book's positions, keyed the way `geostats.bet_multiples` keys
    # them, so a taken bet can be looked up by its ledger rate
    rate_of = {f'{t}|{s}': v for t, s, v in
               zip(m['ticker'], m['entry_date'].astype(str), r)}

    # a ranker can only rank a signal it has a score for. If the ledger
    # ever stops covering what simulate() can order, the arms stop being
    # the same experiment minus one thing -- so say so, loudly.
    prev_green = np.zeros(len(cal), bool)
    prev_green[1:] = panel['green'][:-1]
    orderable = np.zeros(panel['close'].shape, bool)
    orderable[j0:j1 + 1] = (panel['trigger_moc']
                            & prev_green[:, None])[j0:j1 + 1]
    scored = np.zeros_like(orderable)
    scored[ei, tj] = True
    gap = int((orderable & ~scored).sum())

    # ONE SCHEDULE, EVERY ARM. Built once here and handed to each arm in
    # turn, so no arm can pick its own: 400-day embargo, expanding
    # window, both from `bets_common`. `--embargo` and `--lookback` move
    # the whole run or nothing. The control fits nothing and is pinned
    # instead by having to reproduce today's book row for row, which is
    # the stronger constraint of the two.
    blocks = year_blocks(date, exits, lookback_years=lookback,
                         embargo_days=embargo)
    lo, hi = cal[j0].year, cal[j1].year
    blocks = [b for b in blocks if lo <= b[0] <= hi]
    keys = key_columns(panel, ei, tj)
    n_feat = 84 * len(DILATIONS) * N_BIAS * x.shape[1] + keys.shape[1]

    print(f'\nRANKER  embargo={embargo}d  window='
          f'{f"{lookback:g}y" if lookback else "expanding"}  '
          f'target=ln(y)/t  floor={floor}d')
    print(f'        estimator=ridge-{"ycv" if alpha == "cv" else alpha}  '
          f'arms={"+".join(arms)}  features={n_feat:,}  '
          f'blocks={len(blocks)} ({blocks[0][0]}-{blocks[-1][0]})')
    print(f'        window {cal[j0].date()} .. {cal[j1].date()}  '
          f'{len(m):,} orderable signals, {gap:,} of them unscoreable')
    if gap:
        print('        ^ every arm takes those unconditionally')

    # ---- the control, first and always ------------------------------
    ctrl, eq_c, inv_c, _ = simulate(panel, cfg, (j0, j1), moc=True,
                                    pool_days=pool)
    ctrl = pd.DataFrame(ctrl)
    S, B = strength_matrix(panel, pool, j0 - 1, j1)
    # the score decides a FILL; the keys it encodes are read the day the
    # order is placed, which is the day before
    S[1:] = S[:-1]
    S[0] = -np.inf
    st, eq_s, inv_s, _ = simulate(panel, cfg, (j0, j1), moc=True,
                                  pool_days=pool, scores=S)
    st = pd.DataFrame(st)
    same = (len(st) == len(ctrl)
            and st.reset_index(drop=True).equals(ctrl.reset_index(drop=True)))
    tot = metrics(ctrl, eq_c, inv_c)['total_return']
    print(f"\ncontrol: StrengthScore (B={B}) against today's book -- "
          f'{len(ctrl):,} rows, {"IDENTICAL" if same else "DIFFERENT"}, '
          f'{tot:+.1%}')
    if not same:
        sys.exit('the do-nothing arm did not reproduce the book. The '
                 'encoding has no freedom, so this is a bug in the score '
                 'path -- no fitted row may be read from this run.')

    print(f'\n{"arm":14s} {"total":>9s} {"ann":>7s} {"maxDD":>8s} '
          f'{"rows":>7s} {"bets":>6s} {"geo/bet":>9s} {"G_day":>10s} '
          f'{"invested":>8s}')
    if 'strength' in arms:
        report_book('strength', st, eq_s, inv_s, rate_of, r, pool_y)
        if dump:
            st.to_csv(ROOT / 'results' / 'ranker_trades_strength.csv',
                      index=False)

    # ---- the fitted arms --------------------------------------------
    for arm in [a for a in arms if a != 'strength']:
        if arm != 'rocket':
            sys.exit(f'unknown arm {arm!r}: only strength and rocket are '
                     f'built. RANKER_SPEC.md lists multirocket and hydra '
                     f'as arms; their transforms are not in the tree.')
        src = fitcache.file_key(WINDOWS)
        feats = rocket_features(x, date, src)
        print(f'\nwalk-forward {arm} fits '
              f'(train / out-of-fold, one line per fold):')
        score = score_walk_forward(feats, keys, r, date, blocks, alpha,
                                   src, floor, embargo)
        ok = np.isfinite(score)
        print(f'  {int(ok.sum()):,} of {len(r):,} signals scored; the rest '
              f'sit in years with too little history to fit on and keep '
              f'the control ordering')
        # years the model does not reach keep today's ordering, in every
        # arm, so the arms differ exactly where the model exists
        A = S.copy()
        A[ei[ok], tj[ok]] = score[ok]
        tr_, eq, inv, _ = simulate(panel, cfg, (j0, j1), moc=True,
                                   pool_days=pool, scores=A,
                                   min_score=min_score)
        tdf = pd.DataFrame(tr_)
        report_book(arm, tdf, eq, inv, rate_of, r, pool_y)
        if dump:
            tdf.to_csv(ROOT / 'results' / f'ranker_trades_{arm}.csv',
                       index=False)


if __name__ == '__main__':
    main()
