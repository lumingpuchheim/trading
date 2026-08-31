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
    python filter_backtest.py                     # all four arms
    python filter_backtest.py --arms strength,keys   # a subset
    python filter_backtest.py --until 2014-12-31  # fail fast (see below)
    python filter_backtest.py --dump              # + each arm's trades
    python filter_backtest.py --slots 20          # 20 x 5%, same gross

The fail-fast window is 2014, not 2012: the alpha criterion purges 400
days either side of every held-out year inside the training window, and
a fold needs two years to survive that, so `--until 2012-12-31` can
contain ZERO fitted folds and prove nothing.
"""

import sys

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr
from sklearn.metrics import roc_auc_score

import fitcache
from bets_common import (AUX_Q, EMBARGO_DAYS, INNER_MIN, LOOKBACK_YEARS,
                         T_FLOOR, load, rate_target, rent_legs,
                         warmup_rows, year_blocks)
from geostats import bet_multiples, geo_mean_per_euro
from lppl_backtest import ROOT, load_config, metrics
from minervini_backtest import apply_v5, build_panel, pool_by_day, simulate
from minervini_rocket import fit_biases, kernels, transform
from rankers import (YCV_ALPHAS, RentRanker, derive_rent,
                     strength_matrix)

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


def keys_plus(keys: np.ndarray) -> np.ndarray:
    """The six key columns plus the two interactions of Amendment 2.

    The order the book uses is LEXICOGRAPHIC and a linear blend cannot
    be that in general -- but with a BINARY first key it can:
    `C*rsl_hi + rs` with C beyond rs's range reproduces the effective
    order exactly. These two columns let `rs` carry a different slope
    inside each `rsl_hi` tier, which is the realistic bent version of
    the same structure.

    What no linear form reaches is the MIDDLE priority of `weak`, which
    decided 8 of 55,737 signals. That residual is accepted, not
    modelled.
    """
    rsl = keys[:, 0:1]                       # rsl_hi, the binary tier
    return np.concatenate([keys, rsl * keys[:, 4:5], rsl * keys[:, 5:6]],
                          axis=1)


def blend_matrix(S, score, ei, tj, w):
    """Rank-average the two orderings that exist, per day (Amendment 3.1).

        p_strength = percentile rank of the strength ordering
        p_rocket   = percentile rank of the fitted scores
        score      = w * p_rocket + (1-w) * p_strength

    PER-DAY PERCENTILES, not raw values: one input is an integer
    encoding of a four-key sort and the other is a rate in ln per
    trading day. They have no common scale, and anything that added
    them directly would be reporting the arbitrary ratio of two units.
    Ranking inside the day removes both units and leaves the only thing
    the slot decision uses.

    The ranks are taken over the day's SCORED candidates -- the ledger
    rows entered that day, which are exactly the names that can fill.
    Pool names with no score keep their strength value, sort ahead of
    the blend band and cannot fill, exactly as they do in the fitted
    arms. Years the model never reached keep the control ordering whole.

    Both inputs are readable at the close that fills (the strength keys
    a day earlier), so the blend inherits the causality of its members
    and needs no new fit: the cached out-of-fold scores and the strength
    matrix are both already on disk.
    """
    A = S.copy()
    ok = np.isfinite(score)
    order = np.argsort(ei[ok], kind='stable')
    e, t = ei[ok][order], tj[ok][order]
    sc, st = score[ok][order], S[e, t]
    cuts = np.r_[0, np.flatnonzero(np.diff(e)) + 1, len(e)]
    for lo, hi in zip(cuts[:-1], cuts[1:]):
        n = hi - lo
        if n == 1:
            A[e[lo], t[lo]] = 0.5
            continue
        pr = (rankdata(sc[lo:hi]) - 1.0) / (n - 1.0)
        ps = (rankdata(st[lo:hi]) - 1.0) / (n - 1.0)
        A[e[lo:hi], t[lo:hi]] = w * pr + (1.0 - w) * ps
    return A


def arm_builder(arm: str, keys: np.ndarray, x, date, alpha_src):
    """(build, feature identity, feature count) for one fitted arm.

    `build(mask)` returns that arm's feature rows. The rocket arm
    concatenates fold by fold rather than once up front: its transform
    is a 0.94 GB memory map, and materialising it whole beside a
    standardised copy is the avoidable peak that killed this run twice
    on a 16 GB machine.

    THE IDENTITY GOES IN THE CACHE KEY, and it has to, because `src`
    alone no longer names the feature matrix: a keys-only arm and the
    rocket arm read the same windows file and would otherwise collide,
    serving one arm's scores to the other. `rocket` keeps the bare
    column count it has always hashed, so the folds measured on
    2026-08-31 still hit and the amendment costs no refit; the arms
    added since carry a name. Never give a new arm an integer identity.
    """
    if arm == 'keys':
        return (lambda m: keys[m]), 'keys6', keys.shape[1]
    if arm == 'keys+':
        kp = keys_plus(keys)
        return (lambda m: kp[m]), 'keys8', kp.shape[1]
    if arm == 'rocket':
        feats = rocket_features(x, date, alpha_src)
        return ((lambda m: np.concatenate(
                    [np.asarray(feats[m], np.float32), keys[m]], 1)),
                keys.shape[1],
                feats.shape[1] + keys.shape[1])
    sys.exit(f'unknown arm {arm!r}: strength, keys, keys+ and rocket are '
             f'built. RANKER_SPEC.md also lists multirocket and hydra; '
             f'their transforms are not in the tree.')


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

def fold_metrics(r_tr, p_tr, r_ev, p_ev, d_tr, d_ev, dp_ev) -> dict:
    """What a fold reports. `mse` is the loss itself, on the rent target;
    `spear` is the quantity the slot decision actually uses; `days R2` is
    the second head graded on its own -- whether the model can predict
    how long a bet will block the slot at all, which is the half of the
    target the ratio era never had."""
    null_d = float(np.mean((d_ev - d_tr.mean()) ** 2))
    with np.errstate(invalid='ignore'):
        return {'mse_tr': float(np.mean((p_tr - r_tr) ** 2)),
                'mse_ev': float(np.mean((p_ev - r_ev) ** 2)),
                'sp_tr': float(spearmanr(p_tr, r_tr).statistic),
                'sp_ev': float(spearmanr(p_ev, r_ev).statistic),
                'r2_days': float(1.0 - np.mean((dp_ev - d_ev) ** 2) / null_d)
                if null_d > 0 else float('nan')}


def fold_line(Y, n_train, m, alpha, cached, r2, years, c, rounds) -> str:
    # `r2` is the loss against the only honest null there is: predict
    # this fold's own TRAINING mean for every bet in the block. A raw mse
    # says nothing on its own. `c` is the slot rent this fold DERIVED,
    # never a knob, with the number of Dinkelbach rounds it took.
    return (f'  {Y}  train {n_train:>7,d}   '
            f'mse {m["mse_tr"]:.2e} / {m["mse_ev"]:.2e}  R2oof {r2:+6.2f}   '
            f'spear {m["sp_tr"]:+.2f} / {m["sp_ev"]:+.2f}   '
            f'daysR2 {m["r2_days"]:+.2f}   '
            f'c {c:.2e}/{rounds}r  alpha {alpha[0]:.3g}|{alpha[1]:.3g} '
            f'({years}y){"  (cached)" if cached else ""}')


RENT_MULTS = (0.5, 1.0, 2.0)


def score_walk_forward(build, feat_id, PD, taken, date, blocks, alpha, src,
                       embargo):
    """Fit each block on earlier rows only and score it out of fold.

    TWO HEADS PER FOLD -- log-profit and slot-days -- and the rent model
    for every `c` is their difference, so the sensitivity band costs
    nothing beyond three dot products (RANKER_SPEC Amendment 4). `c`
    itself is derived inside the fold from its own training window and
    the book's own selectivity; it is printed, never passed in.

    These fits are closed form -- there are no epochs, so there is no
    loss curve. The training record is one line per fold, printed as the
    fold completes, train and out-of-fold side by side.

    `build(mask)` hands over that arm's feature rows and `feat_id` names
    the matrix it came from; everything else is identical across arms,
    which is what lets the books be read side by side.

    Returns ({multiplier: scores}, fitted years, per-row `c`).
    """
    n = len(PD)
    scores = {mu: np.full(n, np.nan) for mu in RENT_MULTS}
    crow = np.full(n, np.nan)
    r2s, n_ev, fitted = [], [], []
    for Y, tr, ev in blocks:
        sel = float(taken[tr].mean())
        # a NEW cache name: two heads and a derived rent are a different
        # fit from the ratio era, whose `ridge-ycv` entries stay on disk
        # untouched as the record behind the DECISIONS rows
        ck = fitcache.key('ridge-rent', src, alpha, feat_id,
                          tuple(YCV_ALPHAS), embargo, INNER_MIN,
                          round(sel, 6), tuple(RENT_MULTS), tr, ev)
        hit = fitcache.load('block', ck)
        if hit is not None:
            h_ev, c, rounds = hit['heads'], float(hit['c']), int(hit['rounds'])
            m = {k: float(hit[k]) for k in ('mse_tr', 'mse_ev', 'sp_tr',
                                            'sp_ev', 'r2_days')}
            al = hit['alpha']
        else:
            xt = np.asarray(build(tr), np.float32)
            rk = RentRanker(alpha=alpha, embargo=embargo).fit(xt, PD[tr],
                                                              date[tr])
            if not rk.fitted_:
                # not enough purged years inside the training window to
                # choose alpha honestly. Fit nothing and leave the block
                # on the control ordering, as the earliest years are.
                del xt
                print(f'  {Y}  train {int(tr.sum()):>7,d}   fewer than two '
                      f'purged years in the window: no fit, block keeps '
                      f'the control ordering', flush=True)
                continue
            h_tr = rk.train_heads_
            del xt
            c, rounds = derive_rent(PD[tr, 0], PD[tr, 1], h_tr, sel)
            xe = np.asarray(build(ev), np.float32)
            h_ev = rk.heads(xe)
            del xe
            r_tr = PD[tr, 0] - c * PD[tr, 1]
            r_ev = PD[ev, 0] - c * PD[ev, 1]
            m = fold_metrics(r_tr, h_tr[:, 0] - c * h_tr[:, 1],
                             r_ev, h_ev[:, 0] - c * h_ev[:, 1],
                             PD[tr, 1], PD[ev, 1], h_ev[:, 1])
            al = np.asarray(rk.alpha_, np.float64)
            fitcache.save('block', ck, heads=h_ev.astype(np.float64),
                          c=np.float64(c), rounds=np.int64(rounds),
                          alpha=al, years=np.int64(len(rk.years_)),
                          **{k: np.float64(v) for k, v in m.items()})
        for mu in RENT_MULTS:
            scores[mu][ev] = h_ev[:, 0] - mu * c * h_ev[:, 1]
        crow[ev] = c
        r_ev = PD[ev, 0] - c * PD[ev, 1]
        r_tr = PD[tr, 0] - c * PD[tr, 1]
        null = float(np.mean((r_ev - r_tr.mean()) ** 2))
        r2 = 1.0 - m['mse_ev'] / null
        r2s.append(r2)
        n_ev.append(int(ev.sum()))
        fitted.append(Y)
        yrs = int(hit['years']) if hit is not None else len(rk.years_)
        print(fold_line(Y, int(tr.sum()), m, al, hit is not None, r2, yrs,
                        c, rounds), flush=True)
    if r2s:
        w = np.asarray(n_ev, float)
        print(f'  loss against the null (predict the training mean): '
              f'row-weighted R2 out of fold '
              f'{float(np.average(r2s, weights=w)):+.3f}, better than a '
              f'constant in {int(sum(v > 0 for v in r2s))} of {len(r2s)} '
              f'folds', flush=True)
    return scores, fitted, crow


# ----------------------------------------------------------------------
# the book
# ----------------------------------------------------------------------

def pool_rent(PD, crow) -> float:
    """`G_rent` for the whole candidate pool: the same shape as `G_day`,
    on the rent target. NaN where no fold ever priced the row."""
    if crow is None:
        return float('nan')
    v = PD[:, 0] - crow * PD[:, 1]
    v = v[np.isfinite(v)]
    return float(np.exp(v.mean())) - 1.0 if len(v) else float('nan')


def report_book(name, tdf, eq, inv, rate_of, rent_of, pool_r, pool_y,
                p_rent) -> None:
    """One arm's book: the portfolio, the per-bet multiple, and both
    targets -- the retired ratio (`G_day`, reported only) and the trained
    rent (`G_rent`) -- each beside the same number for the whole
    candidate pool, so a difference reads as selection and not as a
    level.

    `G_rent` uses ONE rent per row, the first fitted arm's, for every
    arm. The column then compares BOOKS rather than models; each arm's
    own derived `c` is on its fold lines.
    """
    mt = metrics(tdf, eq, inv)
    mult = bet_multiples(tdf)
    took = np.array([rate_of.get(k, np.nan) for k in mult.index], float)
    took = took[np.isfinite(took)]
    rent = np.array([rent_of.get(k, np.nan) for k in mult.index], float)
    rent = rent[np.isfinite(rent)]
    g_day = float(np.exp(np.mean(took))) if len(took) else float('nan')
    g_rent = float(np.exp(np.mean(rent))) if len(rent) else float('nan')
    print(f'{name:14s} {mt["total_return"]:+9.1%} {mt["ann_return"]:+7.1%} '
          f'{mt["max_drawdown"]:+8.1%} {len(tdf):7,d} {len(mult):6,d} '
          f'{geo_mean_per_euro(mult) - 1:+9.2%} {g_day - 1:+10.4%} '
          f'{g_rent - 1:+10.4%} {inv:8.1%}')
    print(f'{"":14s} {"":9s} {"":7s} {"":8s} {"":7s} '
          f'{len(pool_y):6,d} {geo_mean_per_euro(pool_y) - 1:+9.2%} '
          f'{float(np.exp(np.mean(pool_r))) - 1:+10.4%} '
          f'{p_rent:+10.4%}   <- the whole pool')


def main() -> None:
    av = sys.argv

    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    arms = [a for a in opt('--arms', 'strength,keys,keys+,rocket')
            .split(',') if a]
    alpha = opt('--alpha', 'cv', str)
    alpha = 'cv' if alpha == 'cv' else float(alpha)
    embargo = opt('--embargo', EMBARGO_DAYS, int)
    lookback = opt('--lookback', LOOKBACK_YEARS or 0, float) or None
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
    # --slots N: N positions at 1/N of equity, so GROSS EXPOSURE STAYS AT
    # 100%. The bet-size scan rejected changing gross (DECISIONS.md, OUT
    # table); this changes only how many draws the ranker gets from the
    # same money. `slot_sweep`'s 20 x 5% row -- same total, twice the
    # trades, maxDD -30.2% -> -21.7% -- was measured under the strength
    # ordering alone and has never been run with a fitted arm.
    slots = opt('--slots', 0, int)
    # --permute N: N books in which the fitted scores are shuffled
    # WITHIN each day, so the day's candidate set and the score
    # distribution are untouched and only the ranker's information
    # is destroyed. The spread of the resulting totals is what a
    # difference in total return has to clear to be readable at
    # all (Amendment 3.3).
    perm = opt('--permute', 0, int)
    if slots:
        cfg['minervini_trading']['max_positions'] = slots
        cfg['minervini_trading']['equal_weight_fraction'] = 1.0 / slots
    if '--no-fees' in av:
        cfg['minervini_trading']['cost_per_side'] = 0.0
        print('fees OFF: cost_per_side = 0')
    if '--capital-slots' in av:
        # capacity by capital instead of position count: a split position
        # occupies only the fraction it still holds, so two banked halves
        # free one whole slot. CHANGES EVERY BOOK including the control --
        # +291.5% is NOT the expected control total on these runs; the
        # in-process strength-vs-control IDENTICAL check still applies.
        cfg['minervini_trading']['capital_slots'] = True
        print('capital slots ON: a split position occupies its remaining '
              'fraction; +291.5% is not the expected control here')
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
    # THE TRAINED TARGET is the rent one (Amendment 4): two heads,
    # log-profit and slot-days, and `r = profit - c*days` with `c`
    # derived per fold. The ratio target survives only as the reported
    # `G_day` column, with its floor, and nothing trains on it.
    PD = np.stack(rent_legs(m['y'].to_numpy(np.float64),
                            m['days_held'].to_numpy(np.float64),
                            m['half_frac'].to_numpy(np.float64),
                            m['y_half'].to_numpy(np.float64),
                            m['half_days_held'].to_numpy(np.float64)), 1)
    r = rate_target(m['y'].to_numpy(np.float64),
                    m['days_held'].to_numpy(np.float64),
                    m['half_frac'].to_numpy(np.float64),
                    m['y_half'].to_numpy(np.float64),
                    m['half_days_held'].to_numpy(np.float64), T_FLOOR)
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
    n_rocket = 84 * len(DILATIONS) * N_BIAS * x.shape[1] + keys.shape[1]
    width = {'strength': 0, 'keys': keys.shape[1], 'keys+': keys.shape[1] + 2,
             'rocket': n_rocket}
    # blend<w> arms fit nothing: they rank-average the rocket scores with
    # the control ordering, per day (Amendment 3.1)
    blends = [a for a in arms if a.startswith('blend')]
    if blends and 'rocket' not in arms:
        sys.exit('a blend arm reads the rocket scores; add rocket to --arms')
    shown = ', '.join(a + ('' if a in ('strength',) or a in blends
                           else f'(features={width.get(a, 0):,})')
                      for a in arms)

    print(f'\nRANKER  embargo={embargo}d  window='
          f'{f"{lookback:g}y" if lookback else "expanding"}  '
          f'target=ln(y)-c*t  rent derived per fold')
    print(f'        estimator=ridge-{"ycv" if alpha == "cv" else alpha}  '
          f'blocks={len(blocks)} ({blocks[0][0]}-{blocks[-1][0]})  '
          f'arms: {shown}')
    tr_cfg = cfg['minervini_trading']
    print(f'        slots={tr_cfg["max_positions"]} x '
          f'{tr_cfg["equal_weight_fraction"]:.2%}  gross='
          f'{tr_cfg["max_positions"] * tr_cfg["equal_weight_fraction"]:.0%}'
          + (f'  min_score={min_score:g}' if min_score is not None else ''))
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

    # THE BOOK'S OWN SELECTIVITY, which the rent derivation needs: of
    # the orderable signals in a fold's training window, what fraction
    # did the book actually take? Read off the CONTROL's positions, so it
    # is a property of the simulator (ten slots, the cooldown, the market
    # light) and not of the arm being fitted.
    took_pos = set(bet_multiples(st).index)
    taken = np.array([f'{t}|{d}' in took_pos for t, d in
                      zip(m['ticker'], m['entry_date'].astype(str))])
    print(f'the control took {int(taken.sum()):,} of {len(taken):,} '
          f'orderable signals: selectivity {taken.mean():.2%}')

    hdr = (f'{"arm":14s} {"total":>9s} {"ann":>7s} {"maxDD":>8s} '
           f'{"rows":>7s} {"bets":>6s} {"geo/bet":>9s} {"G_day":>10s} '
           f'{"G_rent":>10s} {"invested":>8s}')

    # ---- the fitted arms --------------------------------------------
    src = fitcache.file_key(WINDOWS)
    books, years_of, sens, rent_c = {'strength': (S, st)}, {}, {}, None
    for arm in [a for a in arms if a != 'strength'
                and not a.startswith('blend')]:
        build, feat_id, n_f = arm_builder(arm, keys, x, date, src)
        print()
        print(f'walk-forward {arm} fits, features={n_f:,} '
              f'(train / out-of-fold, one line per fold):')
        sc, fitted, crow = score_walk_forward(build, feat_id, PD, taken,
                                              date, blocks, alpha, src,
                                              embargo)
        years_of[arm] = fitted
        if rent_c is None:
            rent_c = crow
        if arm == 'rocket':
            rocket_score = sc[1.0]
        ok = np.isfinite(sc[1.0])
        print(f'  {int(ok.sum()):,} of {len(PD):,} signals scored; the rest '
              f'sit in years with too little history to fit on and keep '
              f'the control ordering')
        # THE SENSITIVITY BAND, and it is free: the two heads give every
        # rent, so c/2 and 2c cost three dot products. Theory says the
        # ranking should move slowly with c; a book that swings across
        # this band is fragile and the run has to say so.
        row = {}
        for mu in RENT_MULTS:
            A = S.copy()
            A[ei[ok], tj[ok]] = sc[mu][ok]
            t_, eq_, inv_, _ = simulate(panel, cfg, (j0, j1), moc=True,
                                        pool_days=pool, scores=A,
                                        min_score=min_score)
            d_ = pd.DataFrame(t_)
            row[mu] = (A, d_, eq_, inv_)
        sens[arm] = row
        books[arm] = row[1.0][:2]

    # ---- the blend arms (Amendment 3.1) -----------------------------
    for arm in blends:
        w = float(arm[len('blend'):])
        A = blend_matrix(S, rocket_score, ei, tj, w)
        t_, eq_, inv_, _ = simulate(panel, cfg, (j0, j1), moc=True,
                                    pool_days=pool, scores=A,
                                    min_score=min_score)
        books[arm] = (A, pd.DataFrame(t_))
        sens[arm] = {1.0: (A, pd.DataFrame(t_), eq_, inv_)}

    # ---- the books --------------------------------------------------
    rent_of = ({f'{t}|{d}': v for t, d, v in
                zip(m['ticker'], m['entry_date'].astype(str),
                    PD[:, 0] - rent_c * PD[:, 1])}
               if rent_c is not None else {})
    print()
    print(hdr)
    report_book('strength', st, eq_s, inv_s, rate_of, rent_of, r, pool_y,
                pool_rent(PD, rent_c))
    for arm in [a for a in arms if a != 'strength']:
        A, d_, eq_, inv_ = sens[arm][1.0]
        report_book(arm, d_, eq_, inv_, rate_of, rent_of, r, pool_y,
                    pool_rent(PD, rent_c))
        if dump:
            d_.to_csv(ROOT / 'results' / f'ranker_trades_{arm}.csv',
                      index=False)
        if len(sens[arm]) > 1:
            band = '  '.join(
                f'{mu:g}c {metrics(sens[arm][mu][1], sens[arm][mu][2], sens[arm][mu][3])["total_return"]:+.1%}'
                for mu in RENT_MULTS)
            print(f'{"":14s} rent sensitivity: {band}')


    # The arms are comparable only if they were fitted on the same years.
    # The usability rule counts ROWS and DATES, never features, so this
    # holds by construction -- and a run where it stopped holding would
    # be four books of different things, so it is checked rather than
    # assumed (RANKER_SPEC Amendment 2, acceptance 3).
    if not years_of:
        return
    sets = {a: tuple(v) for a, v in years_of.items()}
    if len(set(sets.values())) > 1:
        for a, v in sets.items():
            print(f'  {a}: fitted {v}')
        sys.exit('the fitted arms do not share their fitted years; the '
                 'books differ by coverage as well as by features, and no '
                 'row from this run may be quoted.')
    fit_years = next(iter(sets.values()))
    print()
    print(f'all fitted arms fitted the same years: {fit_years[0]}-'
          f'{fit_years[-1]} ({len(fit_years)} of {len(blocks)} blocks)')

    # THE YEARS THE RANKER ACTUALLY HAS DATA FOR. The window is expanding,
    # so the early blocks train on a fraction of the record and the first
    # few cannot fit at all. Over the whole record every arm therefore
    # carries years in which it IS the control, and the books can only
    # differ after that -- which flatters or penalises nothing, but does
    # mix two regimes into one total. Same simulator, same scores, one
    # narrower window.
    k0 = int(cal.searchsorted(pd.Timestamp(f'{fit_years[0]}-01-01')))
    print()
    print(f'=== the same books over the fitted years only, '
          f'{cal[k0].date()} .. {cal[j1].date()} ===')
    print(hdr)
    for a in arms:
        t2, e2, i2, _ = simulate(panel, cfg, (k0, j1), moc=True,
                                 pool_days=pool, scores=books[a][0],
                                 min_score=None if a == 'strength'
                                 else min_score)
        report_book(a, pd.DataFrame(t2), e2, i2, rate_of, rent_of,
                    r, pool_y, pool_rent(PD, rent_c))

    if not perm or 'rocket' not in books:
        return
    # ---- the noise yardstick (Amendment 3.3) ------------------------
    # We watched a 40-point total-return difference flip sign with the
    # measurement path. Before any arm is judged on its book, find out
    # what a total return is worth: keep every day's candidate set and
    # every day's scores, permute WHICH name carries WHICH score, and
    # read the spread. A book inside that spread is not a result.
    rng = np.random.default_rng(0)
    fin = np.isfinite(rocket_score)
    o = np.argsort(ei[fin], kind='stable')
    e, t, sc = ei[fin][o], tj[fin][o], rocket_score[fin][o]
    cuts = np.r_[0, np.flatnonzero(np.diff(e)) + 1, len(e)]
    print()
    print(f'permutation yardstick: {perm} books, scores shuffled within '
          f'each day over {len(e):,} scored signals on '
          f'{len(cuts) - 1:,} days')
    tot, dd, geo = [], [], []
    for k in range(perm):
        sh = sc.copy()
        for lo, hi in zip(cuts[:-1], cuts[1:]):
            if hi - lo > 1:
                sh[lo:hi] = rng.permutation(sc[lo:hi])
        P = S.copy()
        P[e, t] = sh
        t2, e2, i2, _ = simulate(panel, cfg, (k0, j1), moc=True,
                                 pool_days=pool, scores=P)
        d2 = pd.DataFrame(t2)
        m2 = metrics(d2, e2, i2)
        tot.append(m2['total_return'])
        dd.append(m2['max_drawdown'])
        geo.append(geo_mean_per_euro(bet_multiples(d2)) - 1.0)
        if (k + 1) % 25 == 0:
            print(f'  {k + 1}/{perm} ...', flush=True)
    tot, dd, geo = np.array(tot), np.array(dd), np.array(geo)

    def band(name, v, fmt='+.1%'):
        q = np.percentile(v, [5, 25, 50, 75, 95])
        print(f'  {name:9s} min {v.min():{fmt}}  p5 {q[0]:{fmt}}  '
              f'p25 {q[1]:{fmt}}  median {q[2]:{fmt}}  p75 {q[3]:{fmt}}  '
              f'p95 {q[4]:{fmt}}  max {v.max():{fmt}}')
    print(f'  over {cal[k0].date()} .. {cal[j1].date()}, '
          f'{tr_cfg["max_positions"]} slots:')
    band('total', tot)
    band('maxDD', dd)
    band('geo/bet', geo, '+.2%')
    print('  where the real books sit in that distribution:')
    for a in arms:
        t2, e2, i2, _ = simulate(panel, cfg, (k0, j1), moc=True,
                                 pool_days=pool, scores=books[a][0],
                                 min_score=None if a == 'strength'
                                 else min_score)
        d2 = pd.DataFrame(t2)
        v = metrics(d2, e2, i2)['total_return']
        g = geo_mean_per_euro(bet_multiples(d2)) - 1.0
        print(f'    {a:11s} total {v:+8.1%} = pct '
              f'{float((tot < v).mean()):5.1%}   geo/bet {g:+.2%} = pct '
              f'{float((geo < g).mean()):5.1%}')


if __name__ == '__main__':
    main()
