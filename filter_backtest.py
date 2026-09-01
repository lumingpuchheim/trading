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
                         demean_by_day, load, rent_legs, value_target,
                         warmup_rows, year_blocks)
from geostats import bet_multiples, geo_mean_per_euro
from lppl_backtest import ROOT, load_config, metrics
from minervini import group_strength
from minervini_backtest import apply_v5, build_panel, pool_by_day, simulate
from minervini_rocket import fit_biases, kernels, transform
from rankers import (YCV_ALPHAS, MultiRidge, derive_rent,
                     purged_years, strength_matrix)

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


def group_pct_matrix(panel: dict, cfg: dict) -> np.ndarray:
    """Industry-group strength, (days x tickers), from the panel's own
    `rs` matrix and `industries.csv`.

    Built here rather than in `build_panel` because that would rebuild
    the 250 MB panel cache under a new name for a column this computes
    in seconds from a matrix already in memory. Same function, same
    config, so the column is the one the screener would have seen.

    THE §16 GATE'S REJECTION DOES NOT TRANSFER. That gate demanded the
    top 30% of groups and was rejected; the gates of Amendment 6 then
    found the column's sign is NEGATIVE in 12 of 15 years -- among
    candidates that already passed the strength screen, a stock from a
    hotter industry made a worse bet. The gate held a real signal by the
    wrong end. As a feature the model learns the sign per fold, and can
    walk it back if it fades.
    """
    tab = pd.read_csv(ROOT / 'data' / 'industries.csv')
    gmap = dict(zip(tab['ticker'], tab['industry']))
    gid = {g: i for i, g in enumerate(sorted(set(gmap.values())))}
    tickers = list(panel['tickers'])
    groups = np.array([gid.get(gmap.get(t, None), -1) for t in tickers])
    return group_strength(panel['rs'], groups, cfg)


def pair(mat, ei, tj) -> np.ndarray:
    """One panel column as the (value filled with 0, finite) pair the
    model sees, read on the day the ORDER is placed."""
    v = np.asarray(mat[np.maximum(ei - 1, 0), tj], dtype=np.float64)
    ok = np.isfinite(v)
    return np.stack([np.where(ok, v, 0.0), ok.astype(np.float64)],
                    1).astype(np.float32)


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


def cached_ratio_scores(src, date, exits, embargo, alpha='cv',
                        floor=3, feat_id=6):
    """The Amendment 1 RATIO-ERA out-of-fold scores, from cache only.

    Used by Amendment 9.2 as a CRASH-PROPENSITY RANKING, which is what it
    accidentally is: trained on `ln(y)/t`, where a three-day stop-out
    weighs -0.028/day against a best winner's +0.012/day, so most of its
    learnable variation was the left tail -- and the audit found all its
    discrimination there (coin-flip on winners, the only outside-noise
    drawdown in the register). Rank averaging needs order, not
    probabilities, so nothing is retrained and no P(crash) is computed.

    The key is the one that era wrote, reconstructed exactly: the ratio
    target's floor and the bare column count it hashed before arms had
    names. If it has drifted, every fold misses and the run says so
    rather than quietly scoring nothing.
    """
    blocks = year_blocks(date, exits, lookback_years=None,
                         embargo_days=embargo)
    score = np.full(len(date), np.nan)
    got = 0
    for Y, tr, ev in blocks:
        hit = fitcache.load('block', fitcache.key(
            'ridge-ycv', src, alpha, floor, feat_id, tuple(YCV_ALPHAS),
            embargo, INNER_MIN, tr, ev))
        if hit is None:
            if purged_years(date[tr], embargo) < 2:
                continue
            sys.exit(f'--compose: the {Y} fold of the ratio-era scores is '
                     f'not in the cache. Create it with:  python '
                     f'filter_backtest.py --target rent --arms rocket   '
                     f'(the Amendment 1 run), or drop --compose.')
        score[ev] = hit['score']
        got += 1
    print(f'  ratio-era crash ranking: {got} folds from cache, '
          f'{int(np.isfinite(score).sum()):,} signals ranked')
    return score


def blend_two(S, a, b, ei, tj, w):
    """Rank-average two cached rankings, per day (Amendment 9.2).

        score = w * pctile(a) + (1-w) * pctile(b)

    Per-day percentiles for the same reason as `blend_matrix`: the two
    scores are on incommensurable scales, and the slot decision only
    compares candidates that arrived together. A row missing either
    score keeps its strength value and cannot fill.
    """
    A = S.copy()
    ok = np.isfinite(a) & np.isfinite(b)
    order = np.argsort(ei[ok], kind='stable')
    e, t = ei[ok][order], tj[ok][order]
    av, bv = a[ok][order], b[ok][order]
    cuts = np.r_[0, np.flatnonzero(np.diff(e)) + 1, len(e)]
    for lo, hi in zip(cuts[:-1], cuts[1:]):
        n = hi - lo
        if n == 1:
            A[e[lo], t[lo]] = 0.5
            continue
        pa = (rankdata(av[lo:hi]) - 1.0) / (n - 1.0)
        pb = (rankdata(bv[lo:hi]) - 1.0) / (n - 1.0)
        A[e[lo:hi], t[lo:hi]] = w * pa + (1.0 - w) * pb
    return A


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


def arm_builder(arm: str, keys: np.ndarray, x, date, alpha_src, grp=None):
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
    if arm == 'keys+group':
        kg = np.concatenate([keys, grp], 1)
        return (lambda m: kg[m]), 'keys6+grp', kg.shape[1]
    if arm == 'rocket':
        feats = rocket_features(x, date, alpha_src)
        return ((lambda m: np.concatenate(
                    [np.asarray(feats[m], np.float32), keys[m]], 1)),
                keys.shape[1],
                feats.shape[1] + keys.shape[1])
    if arm == 'rocket+group':
        feats = rocket_features(x, date, alpha_src)
        kg = np.concatenate([keys, grp], 1)
        return ((lambda m: np.concatenate(
                    [np.asarray(feats[m], np.float32), kg[m]], 1)),
                'rocket+grp',
                feats.shape[1] + kg.shape[1])
    sys.exit(f'unknown arm {arm!r}: strength, keys, keys+, keys+group, '
             f'rocket and rocket+group are built. RANKER_SPEC.md also '
             f'lists multirocket and hydra; their transforms are not in '
             f'the tree.')


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


def fold_line(Y, n_train, m, alpha, cached, r2, years, c, rounds,
              head_alpha) -> str:
    # `r2` is the loss against the only honest null there is: predict
    # this fold's own TRAINING mean for every bet in the block. `c` is
    # the slot rent this fold DERIVED, never a knob, with the number of
    # Dinkelbach rounds it took. `heads` are the two diagnostic fits --
    # they are how the flat profit half was found -- and NOTHING in the
    # decision path is composed from them (Amendment 5).
    return (f'  {Y}  train {n_train:>7,d}   '
            f'mse {m["mse_tr"]:.2e} / {m["mse_ev"]:.2e}  R2oof {r2:+6.2f}   '
            f'spear {m["sp_tr"]:+.2f} / {m["sp_ev"]:+.2f}   '
            f'daysR2 {m["r2_days"]:+.2f}   '
            f'c {c:.2e}/{rounds}r  alpha {alpha:.3g}  '
            f'heads {head_alpha[0]:.3g}|{head_alpha[1]:.3g} '
            f'({years}y){"  (cached)" if cached else ""}')


def within_day_spearman(score, label, day, min_n=5) -> float:
    """Rank correlation of score against realised label INSIDE each day,
    averaged over the days that have at least `min_n` signals.

    This is the decision-relevant diagnostic and the pooled Spearman is
    not: the slot decision only ever compares candidates that arrived on
    the same day, so a model can rank the years correctly and the races
    wrongly, and pooled correlation would applaud it (Amendment 8).
    """
    d = np.asarray(day)
    order = np.argsort(d, kind='stable')
    ds, ss, ls = d[order], np.asarray(score)[order], np.asarray(label)[order]
    cuts = np.r_[0, np.flatnonzero(ds[1:] != ds[:-1]) + 1, len(ds)]
    out = []
    with np.errstate(invalid='ignore'):
        for lo, hi in zip(cuts[:-1], cuts[1:]):
            if hi - lo >= min_n:
                rho = spearmanr(ss[lo:hi], ls[lo:hi]).statistic
                if np.isfinite(rho):
                    out.append(float(rho))
    return float(np.mean(out)) if out else float('nan')


CRASH_CUT = 0.93          # a bet that loses 7%+ of the euro
BYPRODUCT_AUC = 0.653     # what the ratio era's accidental crash ranking
                          # already achieved, free. A dedicated model has
                          # to beat this or it has not earned existing.
SATURATE = 0.05           # clip saturation above which calibration
                          # switches to the training-window decile map


def expectation(probs, values, v_rest):
    """THE SCORE, and the only function in the decision path.

        score = sum_i p_i * value_i + (1 - sum_i p_i) * v_rest

    The law of total expectation over named outcomes, nothing else: no
    rank average (9.2 measured that harmful), no second-stage fit, no
    threshold. Two outcomes for Amendment 10 (crash, survive), three for
    Amendment 11 (crash, jackpot, the middle).

    The probabilities are clipped to a simplex -- each into [0, 1] and
    their sum to at most 1 -- because two independently calibrated
    models can claim more than all of the mass between them, and a
    negative weight on the middle would let a confident model score a
    candidate ABOVE its own best outcome. Scaling both down in
    proportion keeps their ratio, which is the only thing the ranking
    reads.
    """
    P = np.clip(np.stack([np.asarray(p, dtype=np.float64) for p in probs]),
                0.0, 1.0)
    tot = P.sum(0)
    over = tot > 1.0
    if over.any():
        P[:, over] /= tot[over]
        tot = np.minimum(tot, 1.0)
    out = (1.0 - tot) * np.asarray(v_rest, dtype=np.float64)
    for p, v in zip(P, values):
        out = out + p * float(v)
    return out


def total_expectation(p, L, v):
    """The two-outcome case, kept as the name Amendment 10 pinned.

        score = p*L + (1-p)*v

    The law of total expectation, nothing else: no rank average (9.2
    measured that harmful), no second-stage fit, no threshold. `p` is
    the crash model's calibrated probability, `L` the fold's own crash
    cost, `v` the survivor model's predicted gain GIVEN no crash.

    It is surgical by construction. At an ordinary `p` the score IS the
    survivor value, so the value model decides undiluted at the top of
    the ranking where all its skill lives; the crash opinion enters in
    proportion to its own confidence, so a false alarm costs millimetres
    of score rather than a whole rank vote. And it says itself in one
    sentence: expected value is the chance of a crash times what a crash
    costs, plus the chance of surviving times what survival pays.
    """
    return expectation([p], [L], v)


def calibrate(raw_tr, crash_tr, raw_ev):
    """Turn a ridge output into a probability, and say which way.

    A ridge on a 0/1 label is not a probability -- it runs past both
    ends. Clipping is the honest first choice and costs nothing while
    little is clipped. When a lot is, the clip is throwing away ordering
    at exactly the end that matters, so a monotone decile-to-frequency
    map built FROM THE TRAINING ROWS ONLY replaces it: each training
    decile contributes its own observed crash frequency, and an
    evaluation row takes the frequency of the decile it falls in.

    Returns (probabilities, mode).
    """
    sat = float(np.mean((raw_ev < 0.0) | (raw_ev > 1.0)))
    if sat <= SATURATE:
        return np.clip(raw_ev, 0.0, 1.0), f'clip({sat:.0%})'
    edges = np.quantile(raw_tr, np.linspace(0.0, 1.0, 11)[1:-1])
    freq = np.array([crash_tr[np.searchsorted(edges, raw_tr, 'right') == d]
                     .mean() if (np.searchsorted(edges, raw_tr, 'right')
                                 == d).any() else crash_tr.mean()
                     for d in range(10)])
    freq = np.maximum.accumulate(freq)            # monotone by construction
    return freq[np.searchsorted(edges, raw_ev, 'right')], f'decile({sat:.0%})'


def value_fold_line(Y, n_train, m, alpha, cached, r2, years, grp) -> str:
    # `grp` is the LEARNED, standardised weight on group_pct, printed so
    # the sign flip stays visible: the retired gate demanded the top 30%
    # of groups, the screen says the relationship runs the other way, and
    # nobody sets that by hand -- the fold's own window does, and can
    # walk it back if it fades (Amendment 7).
    return (f'  {Y}  train {n_train:>7,d}   '
            f'mse {m["mse_tr"]:.2e} / {m["mse_ev"]:.2e}  R2oof {r2:+6.2f}   '
            f'spear {m["sp_tr"]:+.2f} / {m["sp_ev"]:+.2f}   '
            f'wday {m["wd_tr"]:+.2f} / {m["wd_ev"]:+.2f}   '
            f'alpha {alpha:.3g}'
            + (f'  group {grp:+.2e}' if grp is not None else '')
            + f' ({years}y){"  (cached)" if cached else ""}')


def value_walk_forward(build, feat_id, rv, day, blocks, alpha, src,
                       embargo, hold, date, grp_at=None, name='ridge-value',
                       cached_only=False):
    """The capped-label walk-forward: ONE ridge per fold on `ln(y)`.

    No rent, no ratio, no floor and no second head. With the hold capped
    at `hold` trading days the trading rule has already made "profit per
    bet" and "profit per slot-time" the same ranking, so the target is
    just what a bet returned -- and there is nothing left to compose,
    which is the Amendment 5 rule holding by construction rather than by
    care.

    Labels are keyed on `hold` because the cap moves every outcome; the
    features are not, because a window is entry-day history. So this
    refits and the transform cache is untouched.

    Returns ({1.0: scores}, fitted years).
    """
    score = np.full(len(rv), np.nan)
    r2s, n_ev, fitted, gws, wds = [], [], [], [], []
    for Y, tr, ev in blocks:
        # a DIFFERENT cache name per target, never an extra key field:
        # the existing `ridge-value` entries stay loadable (CLAUDE.md,
        # never add a key field without checking what it costs)
        ck = fitcache.key(name, src, alpha, feat_id, hold,
                          tuple(YCV_ALPHAS), embargo, INNER_MIN, tr, ev)
        hit = fitcache.load('block', ck)
        if hit is not None:
            p_ev = hit['score']
            # entries written before Amendment 8 have no within-day
            # columns; they stay loadable and report the metric as
            # missing rather than being thrown away and refitted
            m = {k: (float(hit[k]) if k in hit else float('nan'))
                 for k in ('mse_tr', 'mse_ev', 'sp_tr', 'sp_ev',
                           'wd_tr', 'wd_ev')}
            al, yrs = float(hit['alpha']), int(hit['years'])
            gw = float(hit['group']) if 'group' in hit else None
        else:
            # the fittability test is dates only, so ask it BEFORE the
            # cache verdict: a fold that can never be fitted has no cache
            # entry by design, and --cached-only must not mistake that
            # for a missing run
            if purged_years(date[tr], embargo) < 2:
                print(f'  {Y}  train {int(tr.sum()):>7,d}   fewer than two '
                      f'purged years in the window: no fit, block keeps '
                      f'the control ordering', flush=True)
                continue
            if cached_only:
                sys.exit(f'--cached-only: the {Y} fold of {name} is not in '
                         f'the cache and this run may not fit it. Create it '
                         f'with the same command without --cached-only.')
            xt = np.asarray(build(tr), np.float32)
            rk = MultiRidge(alpha=alpha, embargo=embargo).fit(xt, rv[tr],
                                                              date[tr])
            if not rk.fitted_:
                del xt
                continue
            p_tr = rk.train_pred_[:, 0]
            del xt
            xe = np.asarray(build(ev), np.float32)
            p_ev = rk.score(xe)
            del xe
            with np.errstate(invalid='ignore'):
                m = {'mse_tr': float(np.mean((p_tr - rv[tr]) ** 2)),
                     'mse_ev': float(np.mean((p_ev - rv[ev]) ** 2)),
                     'sp_tr': float(spearmanr(p_tr, rv[tr]).statistic),
                     'sp_ev': float(spearmanr(p_ev, rv[ev]).statistic),
                     'wd_tr': within_day_spearman(p_tr, rv[tr], day[tr]),
                     'wd_ev': within_day_spearman(p_ev, rv[ev], day[ev])}
            al, yrs = float(np.asarray(rk.alpha_).ravel()[0]), len(rk.years_)
            gw = (float(rk.coef_[grp_at, 0]) if grp_at is not None
                  else None)
            extra = {} if gw is None else {'group': np.float64(gw)}
            fitcache.save('block', ck, score=p_ev.astype(np.float64),
                          alpha=np.float64(al), years=np.int64(yrs),
                          **extra,
                          **{k: np.float64(v) for k, v in m.items()})
        score[ev] = p_ev
        null = float(np.mean((rv[ev] - rv[tr].mean()) ** 2))
        r2 = 1.0 - m['mse_ev'] / null
        r2s.append(r2)
        n_ev.append(int(ev.sum()))
        fitted.append(Y)
        print(value_fold_line(Y, int(tr.sum()), m, al, hit is not None, r2,
                              yrs, gw), flush=True)
        if gw is not None:
            gws.append(gw)
        if np.isfinite(m['wd_ev']):
            wds.append(m['wd_ev'])
    if r2s:
        w = np.asarray(n_ev, float)
        print(f'  loss against the null (predict the training mean): '
              f'row-weighted R2 out of fold '
              f'{float(np.average(r2s, weights=w)):+.3f}, better than a '
              f'constant in {int(sum(v > 0 for v in r2s))} of {len(r2s)} '
              f'folds', flush=True)
    if wds:
        pos = int(sum(v > 0 for v in wds))
        print(f'  within-day Spearman out of fold: positive in {pos} of '
              f'{len(wds)} folds, mean {np.mean(wds):+.3f}', flush=True)
    if gws:
        neg = int(sum(v < 0 for v in gws))
        print(f'  learned group_pct weight: negative in {neg} of '
              f'{len(gws)} folds, median {np.median(gws):+.2e}', flush=True)
    return {1.0: score}, fitted


RENT_MULTS = (0.5, 1.0, 2.0)
AT_C = RENT_MULTS.index(1.0)


JACK_Q = 0.90             # the jackpot label: the training window's own
                          # top decile. A fixed multiple like 1.4 would
                          # privilege an era; a training quantile cannot.
JACK_BAR = 0.55           # the gate: out-of-fold AUC above this
JACK_DIAG = 0.49          # the ceiling of the diagnostic era -- every
                          # jackpot AUC ever seen here, on models trained
                          # for other things, sat at 0.43-0.49


def jackpot_cut(y_train) -> float:
    """The fold's own jackpot line: the top decile of ITS training
    window, like every other derived quantity in this repo."""
    return float(np.quantile(np.asarray(y_train, dtype=np.float64), JACK_Q))


def jackpot_walk_forward(build, feat_id, y, date, blocks, alpha, src,
                         embargo, cached_only=False):
    """The jackpot's fair shot -- a GATE, not a score (Amendment 11).

    The exact mirror of Amendment 10's crash model with one label
    flipped: binary `y >= the fold's own training top decile`, ridge on
    all training rows, same window, same grouped-CV alpha, same features,
    same calibration.

    Why it deserves the run at all: the four jackpot-hunting losses on
    record belong to the voided veto era, and every jackpot number since
    (out-of-fold AUC 0.43-0.49) was a DIAGNOSTIC of a model trained on
    something else. Crashes got a dedicated shot and measured their
    ceiling; jackpots never did. And unlike crash knowledge, jackpot
    knowledge would act at the TOP of the ranking -- the only place the
    book buys.

    Returns (aucs, fitted years). No score is composed here: the
    three-part expectation is built only if this gate clears.
    """
    aucs, fitted = [], []
    for Y, tr, ev in blocks:
        ck = fitcache.key('ridge-jackpot', src, alpha, feat_id,
                          tuple(YCV_ALPHAS), embargo, INNER_MIN, JACK_Q,
                          tr, ev)
        hit = fitcache.load('block', ck)
        if hit is not None:
            auc, cut = float(hit['auc']), float(hit['cut'])
            mode, yrs = str(hit['mode']), int(hit['years'])
            cached = True
        else:
            if purged_years(date[tr], embargo) < 2:
                print(f'  {Y}  train {int(tr.sum()):>7,d}   fewer than two '
                      f'purged years in the window: no fit', flush=True)
                continue
            if cached_only:
                sys.exit(f'--cached-only: the {Y} fold of ridge-jackpot is '
                         f'not cached and this run may not fit it.')
            cut = jackpot_cut(y[tr])
            lab_tr = (y[tr] >= cut).astype(np.float64)
            xt = np.asarray(build(tr), np.float32)
            jm = MultiRidge(alpha=alpha, embargo=embargo).fit(xt, lab_tr,
                                                              date[tr])
            del xt
            if not jm.fitted_:
                print(f'  {Y}  train {int(tr.sum()):>7,d}   no fit',
                      flush=True)
                continue
            xe = np.asarray(build(ev), np.float32)
            raw = jm.score(xe)
            del xe
            _, mode = calibrate(jm.train_pred_[:, 0], lab_tr, raw)
            # the block is graded against the TRAINING window's cut, never
            # its own -- the same rule as every label in this repo
            lab_ev = (y[ev] >= cut).astype(np.int8)
            auc = (float(roc_auc_score(lab_ev, raw))
                   if 0 < lab_ev.sum() < len(lab_ev) else float('nan'))
            yrs = len(jm.years_)
            fitcache.save('block', ck, auc=np.float64(auc),
                          cut=np.float64(cut), mode=np.array(mode),
                          years=np.int64(yrs))
            cached = False
        aucs.append(auc)
        fitted.append(Y)
        print(f'  {Y}  train {int(tr.sum()):>7,d}   '
              f'jackAUC {auc:.3f} vs {JACK_DIAG:.2f} diagnostic, bar '
              f'{JACK_BAR:.2f}   cut y>={cut:.4f}   {mode:12s} ({yrs}y)'
              f'{"  (cached)" if cached else ""}', flush=True)
    return aucs, fitted


CORNER_X = (10, 20, 30)   # the up-tail line: the top-X% of the fold's own
                          # training p_jack
CORNER_Y = (30, 50)       # the down-tail line: the bottom-Y% of its own
                          # training p_crash
CORNER_FOLDS = 10         # the money gate: this many folds of the fifteen
CORNER_BLOCKS = 15        # and it is fifteen the bar was registered
                          # against, so a narrower window decides nothing


def corner_cuts(pj_tr, pc_tr):
    """The six cells' two lines each, both from the fold's OWN training
    probabilities -- the same test-pinning rule every other derived
    quantity here obeys.

    Returns (jack cuts, crash cuts), in the order of `CORNER_X` and
    `CORNER_Y`.
    """
    j = np.array([np.quantile(np.asarray(pj_tr, np.float64), 1.0 - x / 100.0)
                  for x in CORNER_X])
    c = np.array([np.quantile(np.asarray(pc_tr, np.float64), y / 100.0)
                  for y in CORNER_Y])
    return j, c


def corner_members(pj, pc, jcut, ccut):
    """The corner: the up-tail elevated WITHOUT the down-tail.

    The three-part score is a SUM, and a sum permits substitution -- with
    `J` near +0.22 against `L` near -0.10 a large `p_jack` buys its way
    past a bad `p_crash`, so the composed top ADMITS names whose two
    tails are both elevated. A conjunction refuses them, which is a
    different claim: identifiable ASYMMETRY (Amendment 12), and the
    Minervini thesis itself -- tight base, limited downside, open upside.
    """
    return ((np.asarray(pj, np.float64) >= jcut)
            & (np.asarray(pc, np.float64) <= ccut))


def corner_first(score, member):
    """Corner members ahead of non-members, each group ordered by the
    composed score -- a lexicographic pair carried in one float, the
    StrengthScore trick.

    The preference is in the RANKING, not in the score path: nothing is
    dropped, no threshold is compared against a level, and the composed
    score still decides everything inside each group. The shift is one
    more than the finite scores' whole span, so the weakest member
    outranks the strongest non-member and no arithmetic accident can put
    them the other way round.
    """
    s = np.asarray(score, np.float64)
    fin = np.isfinite(s)
    span = float(s[fin].max() - s[fin].min()) if fin.any() else 0.0
    return s + np.asarray(member, bool).astype(np.float64) * (span + 1.0)


def corner_cell(per_fold, selectivity, min_folds=CORNER_FOLDS):
    """One cell's two gates, from its per-fold measurements.

    A row is `(n_corner, n_rows, n_days, geo_corner, geo_pool)`.

    GATE A, occupancy: the two probabilities correlate positively, so the
    corner is thin by construction, and a book that cannot fill its slots
    starves -- measured, and starvation loses to doing nothing. A cell
    offering less than the book's own selectivity is dead on arrival.

    GATE B, the money gate: the corner's realised per-bet geometric mean
    against the whole pool's, fold by fold, out of fold. Pre-registered
    at `min_folds` of the fifteen. Signal level, no simulation, no path
    noise -- exactly the question, are asymmetric-tail names better bets?
    """
    n_c = sum(r[0] for r in per_fold)
    n_r = sum(r[1] for r in per_fold)
    n_d = sum(r[2] for r in per_fold)
    ok = [r for r in per_fold if np.isfinite(r[3]) and np.isfinite(r[4])]
    wins = int(sum(r[3] > r[4] for r in ok))
    share = n_c / n_r if n_r else float('nan')
    # THE OPERATOR'S COLUMN (2026-09-01). The pre-registered gate counts
    # strict wins over the folds it can decide; the operator judges the
    # cell on how much it wins BY, and scores a fold it cannot decide as
    # half rather than dropping it. A fold is a SPLIT when the two geo
    # means are equal, or when the corner is empty there and the fold has
    # no opinion -- both are worth 0.5 of the fifteen.
    ties = int(sum(r[3] == r[4] for r in ok))
    empty = len(per_fold) - len(ok)
    score = wins + 0.5 * (ties + empty)   # `wins` is strict, ties are not in it
    # the average geometric win: the per-fold ratio of the corner's
    # per-euro growth to the pool's, averaged the way growth compounds
    lg = [np.log((1.0 + r[3]) / (1.0 + r[4])) for r in ok
          if 1.0 + r[3] > 0 and 1.0 + r[4] > 0]
    avg = float(np.exp(np.mean(lg)) - 1.0) if lg else float('nan')
    return {'n': int(n_c), 'share': share,
            'per_day': n_c / n_d if n_d else float('nan'),
            'wins': wins, 'folds': len(ok), 'splits': ties + empty,
            'score': score, 'of': len(per_fold), 'avg_win': avg,
            'alive': bool(np.isfinite(share) and share >= selectivity),
            'money': bool(wins >= min_folds)}


def corner_grid(comps, y, ei, selectivity):
    """The six cells, printed before any book exists (Amendment 12).

    Every number here is free arithmetic over components the fits already
    stored: no cell costs a fit, and the grid is read once.

    Returns `(cell, membership)` for the single best cell that clears
    BOTH gates -- most money-gate folds, ties to the fatter corner -- or
    None, which is the amendment's other outcome and a result.
    """
    y = np.asarray(y, np.float64)
    ev_all = np.concatenate([np.flatnonzero(c['ev']) for c in comps])
    pool_geo = geo_mean_per_euro(y[ev_all]) - 1.0
    print()
    print('the corner grid -- six cells, all from the stored p_crash and '
          'p_jack:')
    print(f'  gate A occupancy: at least the book\'s own '
          f'{selectivity:.2%} selectivity, or the book starves')
    print(f'  gate B money: the corner\'s per-bet geo mean over the '
          f'pool\'s in {CORNER_FOLDS} of {CORNER_BLOCKS} folds, out of '
          f'fold ({len(comps)} fitted here)')
    out = {}
    for xi, x in enumerate(CORNER_X):
        for yi, yy in enumerate(CORNER_Y):
            rows, mem_all = [], np.zeros(len(y), bool)
            for c in comps:
                mem = corner_members(c['pj'], c['pc'],
                                     float(c['jcuts'][xi]),
                                     float(c['ccuts'][yi]))
                mem_all[np.flatnonzero(c['ev'])[mem]] = True
                yv = y[c['ev']]
                rows.append((int(mem.sum()), int(mem.size),
                             len(np.unique(ei[c['ev']])),
                             geo_mean_per_euro(yv[mem]) - 1.0,
                             geo_mean_per_euro(yv) - 1.0))
            d = corner_cell(rows, selectivity)
            d['geo'] = geo_mean_per_euro(y[mem_all]) - 1.0
            d['pool'] = pool_geo
            out[(x, yy)] = (d, mem_all)
            print(f'  X{x:<3d}Y{yy:<3d} {d["n"]:>7,d} corner signals  '
                  f'{d["per_day"]:5.2f}/day  share {d["share"]:6.2%} '
                  f'{"LIVE" if d["alive"] else "DEAD"}   '
                  f'geo/bet {d["geo"]:+.2%} vs pool {d["pool"]:+.2%}   '
                  f'money {d["wins"]:>2d}/{d["folds"]} '
                  f'{"CLEARS" if d["money"] else "fails "}  '
                  f'|  avg geo win {d["avg_win"]:+.3%}  '
                  f'score {d["score"]:>4.1f}/{d["of"]} '
                  f'({d["splits"]} split)')
    if len(comps) < CORNER_BLOCKS:
        # the money gate is pre-registered in absolute folds, so a
        # narrowed window cannot pronounce on it either way
        print(f'  this window has {len(comps)} fitted folds, not '
              f'{CORNER_BLOCKS}: the money gate is not decided here and no '
              f'book runs. Run without --until.')
        return None
    live = [(k, v) for k, v in out.items() if v[0]['alive'] and v[0]['money']]
    if not live:
        print('  no cell clears both PRE-REGISTERED gates: at 10 of 15 '
              'strict money folds the two tails cannot be told apart in '
              'these features at any of the six operating points.')
        # THE OPERATOR'S OVERRIDE (2026-09-01). The registered verdict
        # above stands and is printed either way; but the question asked
        # of this run is whether the corner PERFORMS better, scored as
        # the average geometric win with a split worth half a fold. So a
        # book still runs -- for the best cell that can at least fill its
        # slots (gate A), because a starved book measures the starving,
        # not the corner.
        fill = [(k, v) for k, v in out.items() if v[0]['alive']]
        if not fill:
            print("  and no cell offers even the book's own "
                  "selectivity: "
                  'nothing to run a book on.')
            return None
        best = max(fill, key=lambda kv: (kv[1][0]['score'],
                                         kv[1][0]['avg_win']))
        d = best[1][0]
        print(f"  the operator's pick, gate B set aside: X{best[0][0]} "
              f'Y{best[0][1]} -- score {d["score"]:.1f}/{d["of"]}, avg '
              f'geo win {d["avg_win"]:+.3%}. One book, and only this one.')
        return best[0], best[1][1]
    best = max(live, key=lambda kv: (kv[1][0]['score'], kv[1][0]['avg_win']))
    d = best[1][0]
    print(f'  the single best cell: X{best[0][0]} Y{best[0][1]} '
          f'({d["wins"]}/{d["folds"]} money folds, score '
          f'{d["score"]:.1f}/{d["of"]}, avg geo win {d["avg_win"]:+.3%}, '
          f'geo/bet {d["geo"]:+.2%}). One book, and only this one.')
    return best[0], best[1][1]


def tail_auc_check(comps, blocks, src, alpha, feat_id, embargo):
    """Acceptance 2: the re-run is the SAME fits with the components now
    kept -- so every fold's crash AUC must be the one Amendment 10
    recorded, and every jackpot AUC the one Amendment 11 did.

    Both live in their own caches under their own keys, untouched by the
    change that made this run refit, so the check is a lookup rather than
    a memory.
    """
    at = {Y: (tr, ev) for Y, tr, ev in blocks}
    same = seen = 0
    print()
    print('the consistency check -- the same fits as Amendments 10 and 11:')
    for c in comps:
        tr, ev = at[c['Y']]
        pc = fitcache.load('block', fitcache.key(
            'ridge-crashvalue', src, alpha, feat_id, tuple(YCV_ALPHAS),
            embargo, INNER_MIN, CRASH_CUT, tr, ev))
        pj = fitcache.load('block', fitcache.key(
            'ridge-jackpot', src, alpha, feat_id, tuple(YCV_ALPHAS),
            embargo, INNER_MIN, JACK_Q, tr, ev))
        old_c = float(pc['auc']) if pc is not None else float('nan')
        old_j = float(pj['auc']) if pj is not None else float('nan')
        # a fold the earlier amendments never fitted is NOT a mismatch --
        # it is a fold with nothing to check against, and it says so
        bad = [np.isfinite(o) and not np.isclose(n, o, atol=1e-12, rtol=0.0)
               for n, o in ((c['auc_c'], old_c), (c['auc_j'], old_j))]
        seen += int(np.isfinite(old_c)) + int(np.isfinite(old_j))
        same += int(np.isfinite(old_c) and not bad[0])
        same += int(np.isfinite(old_j) and not bad[1])

        def against(v):
            return f'{v:.3f}' if np.isfinite(v) else 'not cached'

        print(f'  {c["Y"]}  crash {c["auc_c"]:.3f} vs A10 {against(old_c)}'
              f'   jack {c["auc_j"]:.3f} vs A11 {against(old_j)}'
              + ('   <- DIFFERS' if any(bad) else ''))
    print(f'  {same} of {seen} cached fold AUCs reproduce exactly'
          + ('' if same == seen else
             '   <- the components are NOT from the recorded fits'))


def threepart_walk_forward(build, feat_id, rv, y, date, blocks, alpha,
                           src, embargo, cached_only=False):
    """Crash, jackpot, and the middle -- one formula (Amendment 11).

        score = p_crash*L_crash + p_jack*J_hat
                + (1 - p_crash - p_jack) * v_mid

    The crash and jackpot heads are fitted TOGETHER, on the same training
    rows, so they share one eigendecomposition and the pair costs what a
    single head costs. `v_mid` cannot join them: it is fitted on the rows
    that are NEITHER crash nor jackpot, which is what stops either tail
    being counted twice -- the same discipline that put the survivor
    model on `y >= 0.93` rows in Amendment 10, applied at both ends.

    `L_crash` and `J_hat` are the fold's own training means over its
    crashes and its jackpots: constants per fold, never knobs.

    THE COMPONENTS ARE KEPT (Amendment 12). Amendment 11 cached the
    composed score alone, so the corner question -- up-tail elevated
    WITHOUT the down-tail -- had nothing to ask and would have paid for
    these fits a second time. A fold now stores `p_crash` and `p_jack`
    per scored signal, and the six corner cuts read off ITS OWN TRAINING
    probabilities. The fits are unchanged; an entry written before this
    change carries no components and is refitted once, which is the one
    training run Amendment 12 costs and the last time any question pays
    it.

    Returns ({1.0: scores}, fitted years, the per-fold components).
    """
    score = np.full(len(rv), np.nan)
    fitted, comps = [], []
    crash = y < CRASH_CUT
    for Y, tr, ev in blocks:
        ck = fitcache.key('ridge-threepart', src, alpha, feat_id,
                          tuple(YCV_ALPHAS), embargo, INNER_MIN,
                          CRASH_CUT, JACK_Q, tr, ev)
        hit = fitcache.load('block', ck)
        if hit is not None and 'pc' not in hit:
            hit = None            # written before the components were kept
        if hit is not None:
            score[ev] = hit['score']
            line = str(hit['line'])
            yrs = int(hit['years'])
            pc, pj = hit['pc'], hit['pj']
            jcuts, ccuts = hit['jcuts'], hit['ccuts']
            ac, aj = float(hit['auc_c']), float(hit['auc_j'])
            cached = True
        else:
            if purged_years(date[tr], embargo) < 2:
                print(f'  {Y}  train {int(tr.sum()):>7,d}   fewer than two '
                      f'purged years in the window: no fit', flush=True)
                continue
            if cached_only:
                sys.exit(f'--cached-only: the {Y} fold of ridge-threepart '
                         f'is not cached and this run may not fit it.')
            cut = jackpot_cut(y[tr])
            jack = y >= cut
            mid = tr & ~crash & ~jack
            if purged_years(date[mid], embargo) < 2:
                print(f'  {Y}  train {int(tr.sum()):>7,d}   the middle '
                      f'subset cannot supply two purged years: no fit',
                      flush=True)
                continue
            lab = np.stack([crash[tr].astype(np.float64),
                            jack[tr].astype(np.float64)], 1)
            xt = np.asarray(build(tr), np.float32)
            tails = MultiRidge(alpha=alpha, embargo=embargo).fit(xt, lab,
                                                                 date[tr])
            del xt
            xm = np.asarray(build(mid), np.float32)
            vm = MultiRidge(alpha=alpha, embargo=embargo).fit(xm, rv[mid],
                                                              date[mid])
            del xm
            if not (tails.fitted_ and vm.fitted_):
                print(f'  {Y}  train {int(tr.sum()):>7,d}   a head did not '
                      f'fit: no fit', flush=True)
                continue
            xe = np.asarray(build(ev), np.float32)
            raw = tails.predict(xe)
            v_mid = vm.score(xe)
            del xe
            pc, mc = calibrate(tails.train_pred_[:, 0], lab[:, 0], raw[:, 0])
            pj, mj = calibrate(tails.train_pred_[:, 1], lab[:, 1], raw[:, 1])
            # the corner's lines, from the TRAINING rows' own probabilities
            # -- the same calibration, applied to the rows it was built on
            pc_tr, _ = calibrate(tails.train_pred_[:, 0], lab[:, 0],
                                 tails.train_pred_[:, 0])
            pj_tr, _ = calibrate(tails.train_pred_[:, 1], lab[:, 1],
                                 tails.train_pred_[:, 1])
            jcuts, ccuts = corner_cuts(pj_tr, pc_tr)
            lhat = float(rv[tr & crash].mean())
            jhat = float(rv[tr & jack].mean())
            score[ev] = expectation([pc, pj], [lhat, jhat], v_mid)
            ac = (float(roc_auc_score(crash[ev].astype(np.int8), raw[:, 0]))
                  if 0 < crash[ev].sum() < int(ev.sum()) else float('nan'))
            aj = (float(roc_auc_score((y[ev] >= cut).astype(np.int8),
                                      raw[:, 1]))
                  if 0 < (y[ev] >= cut).sum() < int(ev.sum())
                  else float('nan'))
            yrs = len(vm.years_)
            line = (f'AUC crash {ac:.3f} jack {aj:.3f}   '
                    f'p {pc.mean():.3f}/{pj.mean():.3f}   '
                    f'L {lhat:+.4f} J {jhat:+.4f}   {mc}|{mj}')
            fitcache.save('block', ck, score=score[ev].astype(np.float64),
                          line=np.array(line), years=np.int64(yrs),
                          pc=np.asarray(pc, np.float64),
                          pj=np.asarray(pj, np.float64),
                          jcuts=np.asarray(jcuts, np.float64),
                          ccuts=np.asarray(ccuts, np.float64),
                          auc_c=np.float64(ac), auc_j=np.float64(aj))
            cached = False
        fitted.append(Y)
        comps.append({'Y': Y, 'ev': ev, 'pc': pc, 'pj': pj,
                      'jcuts': jcuts, 'ccuts': ccuts,
                      'auc_c': ac, 'auc_j': aj})
        print(f'  {Y}  train {int(tr.sum()):>7,d}   {line}   ({yrs}y)'
              f'{"  (cached)" if cached else ""}', flush=True)
    return {1.0: score}, fitted, comps


def crashvalue_walk_forward(build, feat_id, rv, y, date, blocks, alpha,
                            src, embargo, cached_only=False):
    """Two models per fold, one formula (RANKER_SPEC Amendment 10).

    CRASH: ridge on the binary `y < 0.93`, all training rows.
    SURVIVOR VALUE: ridge on `ln(y)`, training rows with `y >= 0.93`
    ONLY -- which is what makes the formula honest. The all-rows value
    model already carries crash mass inside it, so composing it with a
    crash probability would count the downside twice.
    `L_hat`: the mean realised value over the fold's TRAINING crashes, a
    constant per fold.

    They cannot share an eigendecomposition -- different row sets -- so
    this is two fits per fold, which is the training cost the amendment
    named.

    Returns ({1.0: composed scores}, fitted years).
    """
    score = np.full(len(rv), np.nan)
    fitted, aucs, sps = [], [], []
    crash = (y < CRASH_CUT)
    for Y, tr, ev in blocks:
        ck = fitcache.key('ridge-crashvalue', src, alpha, feat_id,
                          tuple(YCV_ALPHAS), embargo, INNER_MIN,
                          CRASH_CUT, tr, ev)
        hit = fitcache.load('block', ck)
        if hit is not None:
            score[ev] = hit['score']
            auc, mode = float(hit['auc']), str(hit['mode'])
            lhat, sp = float(hit['lhat']), float(hit['sp_ev'])
            yrs = int(hit['years'])
            cached = True
        else:
            if purged_years(date[tr], embargo) < 2:
                print(f'  {Y}  train {int(tr.sum()):>7,d}   fewer than two '
                      f'purged years in the window: no fit, block keeps '
                      f'the control ordering', flush=True)
                continue
            if cached_only:
                sys.exit(f'--cached-only: the {Y} fold of ridge-crashvalue '
                         f'is not cached and this run may not fit it.')
            surv = tr & ~crash
            if purged_years(date[surv], embargo) < 2:
                print(f'  {Y}  train {int(tr.sum()):>7,d}   the survivor '
                      f'subset cannot supply two purged years: no fit',
                      flush=True)
                continue
            xt = np.asarray(build(tr), np.float32)
            cm = MultiRidge(alpha=alpha, embargo=embargo).fit(
                xt, crash[tr].astype(np.float64), date[tr])
            del xt
            xs = np.asarray(build(surv), np.float32)
            vm = MultiRidge(alpha=alpha, embargo=embargo).fit(
                xs, rv[surv], date[surv])
            del xs
            if not (cm.fitted_ and vm.fitted_):
                print(f'  {Y}  train {int(tr.sum()):>7,d}   a head did not '
                      f'fit: no fit, block keeps the control ordering',
                      flush=True)
                continue
            xe = np.asarray(build(ev), np.float32)
            raw = cm.score(xe)
            v_hat = vm.score(xe)
            del xe
            p_hat, mode = calibrate(cm.train_pred_[:, 0],
                                    crash[tr].astype(np.float64), raw)
            lhat = float(rv[tr & crash].mean())
            score[ev] = total_expectation(p_hat, lhat, v_hat)
            lab = crash[ev].astype(np.int8)
            auc = (float(roc_auc_score(lab, raw))
                   if 0 < lab.sum() < len(lab) else float('nan'))
            with np.errstate(invalid='ignore'):
                sp = float(spearmanr(score[ev], rv[ev]).statistic)
            yrs = len(vm.years_)
            fitcache.save('block', ck, score=score[ev].astype(np.float64),
                          auc=np.float64(auc), mode=np.array(mode),
                          lhat=np.float64(lhat), sp_ev=np.float64(sp),
                          years=np.int64(yrs))
            cached = False
        # the value arm this has to beat, read from ITS cache
        prev = fitcache.load('block', fitcache.key(
            'ridge-value', src, alpha, feat_id, 0, tuple(YCV_ALPHAS),
            embargo, INNER_MIN, tr, ev))
        base = float(prev['sp_ev']) if prev is not None else float('nan')
        fitted.append(Y)
        aucs.append(auc)
        sps.append((sp, base))
        print(f'  {Y}  train {int(tr.sum()):>7,d}   '
              f'crashAUC {auc:.3f} vs {BYPRODUCT_AUC:.3f}   {mode:12s} '
              f'L {lhat:+.4f}   spear {sp:+.2f} vs value '
              f'{base:+.2f}   ({yrs}y)'
              f'{"  (cached)" if cached else ""}', flush=True)
    if aucs:
        win = int(sum(a > BYPRODUCT_AUC for a in aucs))
        print(f'  GATE 1 crash AUC over the {BYPRODUCT_AUC:.3f} byproduct: '
              f'{win} of {len(aucs)} folds, mean {np.nanmean(aucs):.3f}',
              flush=True)
        ok = [(a, b) for a, b in sps if np.isfinite(b)]
        keep = int(sum(a >= b for a, b in ok))
        print(f'  GATE 2 composed Spearman at least the value arm: '
              f'{keep} of {len(ok)} folds', flush=True)
    return {1.0: score}, fitted


def score_walk_forward(build, feat_id, PD, taken, date, blocks, alpha, src,
                       embargo):
    """Fit each block on earlier rows only and score it out of fold.

    TWO PASSES PER FOLD, and only the second one decides anything.

    Pass one fits the two heads -- log-profit and slot-days -- purely to
    derive the fold's rent `c` by the Dinkelbach iteration at the book's
    own selectivity, and to print them as diagnostics. Pass two fits ONE
    ridge directly on the rent number `r = profit - c*days`, with its
    own alpha chosen on r's own held-out error, and THAT is the score.

    Nothing subtracts two fits anywhere in the decision path. Amendment 4
    did, and the two individually-optimal halves composed into a score
    that ranked its own target negatively out of fold: the grouped CV
    judged profit alone (noise, shrunk to a constant) and days alone
    (signal, kept), so the difference charged duration its rent while
    ignoring duration's profit association. A decision comes from the
    single best estimate of the decision quantity, never from separately
    tuned estimates of its parts.

    The `c/2, c, 2c` band is three single fits sharing one set of
    eigendecompositions -- each with its own CV'd alpha, none of them
    composed.

    Returns ({multiplier: scores}, fitted years, per-row `c`).
    """
    n = len(PD)
    scores = {mu: np.full(n, np.nan) for mu in RENT_MULTS}
    crow = np.full(n, np.nan)
    r2s, n_ev, fitted = [], [], []
    for Y, tr, ev in blocks:
        sel = float(taken[tr].mean())
        # a NEW cache name: one fit on the rent number is a different
        # model from Amendment 4's difference of two, whose `ridge-rent`
        # entries stay on disk as the record behind that DECISIONS row
        ck = fitcache.key('ridge-rent1', src, alpha, feat_id,
                          tuple(YCV_ALPHAS), embargo, INNER_MIN,
                          round(sel, 6), tuple(RENT_MULTS), tr, ev)
        hit = fitcache.load('block', ck)
        if hit is not None:
            p_ev = hit['pred']
            c, rounds = float(hit['c']), int(hit['rounds'])
            m = {k: float(hit[k]) for k in ('mse_tr', 'mse_ev', 'sp_tr',
                                            'sp_ev', 'r2_days')}
            al, hal, yrs = hit['alpha'], hit['head_alpha'], int(hit['years'])
        else:
            # PASS 1 -- the two diagnostic heads, and the rent they
            # derive. Amendment 4's run computed exactly this, from the
            # same rows with the same criterion, and cached it; when that
            # entry is on disk the rent is READ rather than refitted. It
            # is the same number by construction, and it halves the run.
            prev = fitcache.load('block', fitcache.key(
                'ridge-rent', src, alpha, feat_id, tuple(YCV_ALPHAS),
                embargo, INNER_MIN, round(sel, 6), tuple(RENT_MULTS),
                tr, ev))
            xt = np.asarray(build(tr), np.float32)
            if prev is not None:
                c, rounds = float(prev['c']), int(prev['rounds'])
                hal, d_prev = prev['alpha'], prev['heads'][:, 1]
            else:
                hd = MultiRidge(alpha=alpha, embargo=embargo).fit(
                    xt, PD[tr], date[tr])
                if not hd.fitted_:
                    del xt
                    print(f'  {Y}  train {int(tr.sum()):>7,d}   fewer than '
                          f'two purged years in the window: no fit, block '
                          f'keeps the control ordering', flush=True)
                    continue
                c, rounds = derive_rent(PD[tr, 0], PD[tr, 1],
                                        hd.train_pred_, sel)
                hal, d_prev = np.asarray(hd.alpha_, np.float64), None
            # pass 2: the decision, one fit per rent on the rent itself
            R_tr = np.stack([PD[tr, 0] - mu * c * PD[tr, 1]
                             for mu in RENT_MULTS], 1)
            rk = MultiRidge(alpha=alpha, embargo=embargo).fit(xt, R_tr,
                                                              date[tr])
            if not rk.fitted_:
                del xt
                print(f'  {Y}  train {int(tr.sum()):>7,d}   fewer than two '
                      f'purged years in the window: no fit, block keeps '
                      f'the control ordering', flush=True)
                continue
            del xt
            xe = np.asarray(build(ev), np.float32)
            p_ev = rk.predict(xe)
            d_ev = d_prev if d_prev is not None else hd.predict(xe)[:, 1]
            del xe
            r_ev = PD[ev, 0] - c * PD[ev, 1]
            m = fold_metrics(R_tr[:, AT_C], rk.train_pred_[:, AT_C],
                             r_ev, p_ev[:, AT_C],
                             PD[tr, 1], PD[ev, 1], d_ev)
            al = np.asarray(rk.alpha_, np.float64)
            yrs = len(rk.years_)
            fitcache.save('block', ck, pred=p_ev.astype(np.float64),
                          c=np.float64(c), rounds=np.int64(rounds),
                          alpha=al, head_alpha=hal, years=np.int64(yrs),
                          **{k: np.float64(v) for k, v in m.items()})
        for k, mu in enumerate(RENT_MULTS):
            scores[mu][ev] = p_ev[:, k]
        crow[ev] = c
        r_ev = PD[ev, 0] - c * PD[ev, 1]
        r_tr = PD[tr, 0] - c * PD[tr, 1]
        null = float(np.mean((r_ev - r_tr.mean()) ** 2))
        r2 = 1.0 - m['mse_ev'] / null
        r2s.append(r2)
        n_ev.append(int(ev.sum()))
        fitted.append(Y)
        print(fold_line(Y, int(tr.sum()), m, float(al[AT_C]),
                        hit is not None, r2, yrs, c, rounds, hal),
              flush=True)
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
    """`G_rent` for the whole candidate pool. NaN where no fold ever
    priced the row."""
    if crow is None:
        return float('nan')
    v = PD[:, 0] - crow * PD[:, 1]
    v = v[np.isfinite(v)]
    return float(np.exp(v.mean())) - 1.0 if len(v) else float('nan')


def per_slot_day(profit, days) -> float:
    """Growth per slot-day, as a RATIO OF SUMS.

        exp( sum(ln y) / sum(t) )

    THE MEAN OF RATIOS THAT USED TO SIT HERE IS GONE. `G_day` averaged
    each bet's own `ln(y)/t`, one vote per bet, and that statistic's sign
    is an artefact: winners in this ledger are held 46.3 days and losers
    16.3, so dividing every bet's log by its own holding time hands each
    loser about 2.8x the weight of each winner. On the same 55,737 bets
    `geo/bet` reads +0.5161% and this ratio of sums +0.0177%, both
    positive, while `G_day` read -0.2380% -- and -0.36%/day compounded
    over 4,940 trading days would have wiped an account that in fact
    multiplied by 3.9. It could not be compounded or annualised, and it
    flattered whichever arm held its bets for less time, which is exactly
    what the rent-era arm had degenerated into.

    A ratio of sums has none of that: it is total log-profit over total
    slot-days, which is the quantity a slot's long-run growth actually
    is, and it is what Amendment 4 named when it retired the ratio
    target. Withdrawn on the operator's challenge, 2026-09-01;
    DECISIONS.md carries the arithmetic and the list of earlier readings
    it invalidates.
    """
    d = float(np.sum(days))
    return float(np.exp(np.sum(profit) / d)) - 1.0 if d > 0 else float('nan')


def zero_split(score, y, yr) -> None:
    """What the model's "don't buy" was worth, per year (Amendment 9.1).

    The natural zero declines a candidate whose PREDICTED value is
    negative. That does not need the model to rank well overall -- only
    its worst predictions need to be bad bets, which is the one skill
    this data has ever demonstrated. So the direct check is not the book
    at all: it is whether the signals it would have declined actually
    returned less than the ones it kept.

    Printed over the scored ledger rows, not the taken ones, because the
    slot constraint decides most of what is taken and would confound the
    comparison.
    """
    ok = np.isfinite(score)
    keep, drop = ok & (score >= 0), ok & (score < 0)
    print('  year   kept     geo/bet   declined   geo/bet    gap')
    for Y in sorted(set(yr[ok].tolist())):
        a, b = keep & (yr == Y), drop & (yr == Y)
        ga = geo_mean_per_euro(y[a]) - 1.0 if a.sum() else float('nan')
        gb = geo_mean_per_euro(y[b]) - 1.0 if b.sum() else float('nan')
        gap = ga - gb if np.isfinite(ga) and np.isfinite(gb) else float('nan')
        print(f'  {Y}  {int(a.sum()):5,d}  {ga:+9.2%}   {int(b.sum()):7,d}  '
              f'{gb:+9.2%}  ' + (f'{gap:+8.2%}' if np.isfinite(gap)
                                 else f'{"-":>8s}'))
    ga = geo_mean_per_euro(y[keep]) - 1.0
    gb = geo_mean_per_euro(y[drop]) - 1.0 if drop.sum() else float('nan')
    print(f'   all  {int(keep.sum()):5,d}  {ga:+9.2%}   '
          f'{int(drop.sum()):7,d}  ' + (f'{gb:+9.2%}' if np.isfinite(gb)
                                        else f'{"-":>9s}')
          + f'  {ga - gb:+8.2%}' if np.isfinite(gb) else '')


def report_book(name, tdf, eq, inv, pd_of, rent_of, pool_pd, pool_y,
                p_rent) -> None:
    """One arm's book: the portfolio, the per-bet multiple, growth per
    slot-day, and the trained rent -- each beside the same number for the
    whole candidate pool, so a difference reads as selection and not as a
    level.

    `G_rent` uses ONE rent per row, the first fitted arm's, for every
    arm, so the column compares BOOKS rather than models; each arm's own
    derived `c` is on its fold lines.
    """
    mt = metrics(tdf, eq, inv)
    mult = bet_multiples(tdf)
    legs = np.array([pd_of.get(k, (np.nan, np.nan)) for k in mult.index],
                    float)
    legs = legs[np.isfinite(legs).all(1)]
    rent = np.array([rent_of.get(k, np.nan) for k in mult.index], float)
    rent = rent[np.isfinite(rent)]
    day = (per_slot_day(legs[:, 0], legs[:, 1]) if len(legs)
           else float('nan'))
    g_rent = float(np.exp(np.mean(rent))) - 1.0 if len(rent) else float('nan')

    def cell(v):
        # a run with no fitted arm has no derived rent, so there is no
        # `G_rent` to print -- a dash, never a NaN dressed as a percentage
        return f'{v:+10.4%}' if np.isfinite(v) else f'{"-":>10s}'

    print(f'{name:14s} {mt["total_return"]:+9.1%} {mt["ann_return"]:+7.1%} '
          f'{mt["max_drawdown"]:+8.1%} {len(tdf):7,d} {len(mult):6,d} '
          f'{geo_mean_per_euro(mult) - 1:+9.2%} {cell(day)} '
          f'{cell(g_rent)} {inv:8.1%}')
    print(f'{"":14s} {"":9s} {"":7s} {"":8s} {"":7s} '
          f'{len(pool_y):6,d} {geo_mean_per_euro(pool_y) - 1:+9.2%} '
          f'{cell(per_slot_day(pool_pd[:, 0], pool_pd[:, 1]))} '
          f'{cell(p_rent)}   <- the whole pool')


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
    # --max-hold H: force-sell H trading days after entry (Amendment 6).
    # The ledger is keyed on H because the cap moves every outcome; the
    # windows and every feature cache are shared across all H.
    hold = opt('--max-hold', 0, int)
    # --cached-only: refuse to compute a single fit. Amendment 9 is
    # simulation only, and a run that silently refits would spend
    # hours the operator did not authorise.
    cached_only = '--cached-only' in av
    # --compose: rank-average the cached 5-year value scores with
    # the cached ratio-era ones, used as a crash-propensity
    # ranking (Amendment 9.2). No fit anywhere.
    compose = [float(v) for v in opt('--compose', '').split(',')
               if v]
    # THE TRAINED TARGET. `value` = ln(y), the standing one since
    # Amendment 6; `rent` = ln(y) - c*t, kept runnable so its recorded
    # negative can be reproduced, never as a default again.
    target = opt('--target', 'value')
    if target not in ('value', 'rent', 'daymean', 'crashvalue',
                      'jackpot', 'threepart', 'corner'):
        sys.exit(f'--target must be value, rent, daymean, '
                 f'crashvalue, jackpot, threepart or corner, '
                 f'not {target!r}')
    if min_score is not None and target == 'corner':
        sys.exit('--min-score is refused with target=corner: corner '
                 'members are lifted by a constant so they rank first, '
                 'so the number the threshold would read is no longer a '
                 'predicted rate (RANKER_SPEC Amendment 12).')
    if min_score is not None and target == 'daymean':
        sys.exit('--min-score is refused with target=daymean: the score '
                 'is relative to an unknown day level, so "predicted rate '
                 'below cash" is undefined. The natural zero belongs to '
                 'absolute targets only (RANKER_SPEC Amendment 8).')
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
    if hold:
        cfg['minervini_trading']['max_hold_days'] = hold
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
    ledger = (LEDGER if not hold
              else LEDGER.with_name(f'{LEDGER.stem}_H{hold}{LEDGER.suffix}'))
    if not ledger.exists():
        sys.exit(f'{ledger.name} is missing. Build it with:  '
                 f'python minervini_bets.py --max-hold {hold}')
    led = pd.read_csv(ledger, parse_dates=['entry_date'])
    need = ['y', 'days_held', 'half_frac', 'y_half', 'half_days_held']
    missing = [c for c in need if c not in led.columns]
    if missing:
        sys.exit(f'{ledger.name} is missing {missing}; the split legs are '
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
    # derived per fold -- or, since Amendment 6, the value one below.
    # WITH THE CAP ON, the target is the plain per-bet log multiple: the
    # trading rule has already collapsed "profit per bet" and "profit per
    # slot-time" into one ranking, so there is no rent to charge and
    # nothing to compose (Amendment 6).
    rv = value_target(m['y'].to_numpy(np.float64),
                      m['half_frac'].to_numpy(np.float64),
                      m['y_half'].to_numpy(np.float64))
    if target == 'daymean':
        # one line, and it is the whole amendment: the label becomes the
        # same shape as the decision. The day mean is taken over ALL
        # ledger signals entered that day, once, not per fold.
        rv = demean_by_day(rv, ei)
    PD = np.stack(rent_legs(m['y'].to_numpy(np.float64),
                            m['days_held'].to_numpy(np.float64),
                            m['half_frac'].to_numpy(np.float64),
                            m['y_half'].to_numpy(np.float64),
                            m['half_days_held'].to_numpy(np.float64)), 1)
    pool_y = m['y'].to_numpy(np.float64)
    # the book's positions, keyed the way `geostats.bet_multiples` keys
    # them, so a taken bet can be looked up by its ledger rate
    pd_of = {f'{t}|{s}': (a, b) for t, s, a, b in
             zip(m['ticker'], m['entry_date'].astype(str),
                 PD[:, 0], PD[:, 1])}

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
    grp = None
    if any(a.endswith('+group') for a in arms):
        grp = pair(group_pct_matrix(panel, cfg), ei, tj)
        cov = pd.Series(grp[:, 1], index=m['entry_date'].dt.year).groupby(
            level=0).mean()
        print('group_pct coverage by entry year (finite share of the '
              'ledger rows):')
        print('  ' + '  '.join(f'{int(Y)} {v:.0%}' for Y, v in cov.items()))
    width = {'strength': 0, 'keys': keys.shape[1], 'keys+': keys.shape[1] + 2,
             'keys+group': keys.shape[1] + 2, 'rocket': n_rocket,
             'rocket+group': n_rocket + 2}
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
          f'target={ {"value": "ln(y)", "rent": "ln(y)-c*t",
                       "daymean": "ln(y)-daymean",
                       "crashvalue": "p*L+(1-p)*v",
                       "jackpot": "1[y>=top decile]",
                       "threepart": "pc*L+pj*J+(1-pc-pj)*v",
                       "corner": "pc*L+pj*J+(1-pc-pj)*v, corner first"
                       }[target] }'
          + (f'  max_hold={hold}d' if hold else '')
          + ('  rent derived per fold' if target == 'rent' else ''))
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
           f'{"rows":>7s} {"bets":>6s} {"geo/bet":>9s} {"per_day":>10s} '
           f'{"G_rent":>10s} {"invested":>8s}')

    # ---- the fitted arms --------------------------------------------
    src = fitcache.file_key(WINDOWS)
    books, years_of, sens, rent_c = {'strength': (S, st)}, {}, {}, None
    corner_books = {}
    for arm in [a for a in arms if a != 'strength'
                and not a.startswith('blend')]:
        build, feat_id, n_f = arm_builder(arm, keys, x, date, src,
                                          grp)
        print()
        print(f'walk-forward {arm} fits, features={n_f:,} '
              f'(train / out-of-fold, one line per fold):')
        if target in ('threepart', 'corner'):
            sc, fitted, comps = threepart_walk_forward(
                build, feat_id, rv, m['y'].to_numpy(np.float64), date,
                blocks, alpha, src, embargo, cached_only)
            crow = None
            if target == 'corner':
                # THE GRID FIRST, AND A BOOK ONLY AFTER IT. Both gates are
                # signal level and read the stored components, so the six
                # cells cost arithmetic and the amendment's answer exists
                # before any simulation is run (Amendment 12).
                tail_auc_check(comps, blocks, src, alpha, feat_id, embargo)
                pick = corner_grid(comps, m['y'].to_numpy(np.float64), ei,
                                   float(taken.mean()))
                if pick is not None:
                    corner_books[arm] = (pick[0], pick[1], sc[1.0])
        elif target == 'jackpot':
            # A GATE, NOT A BOOK. Amendment 11 spends one training run on
            # the question and nothing else: a failed gate produces no
            # composition, no simulation and one register row.
            aucs, _ = jackpot_walk_forward(
                build, feat_id, m['y'].to_numpy(np.float64), date, blocks,
                alpha, src, embargo, cached_only)
            win = int(sum(a > JACK_BAR for a in aucs if np.isfinite(a)))
            n = int(sum(np.isfinite(a) for a in aucs))
            print()
            print(f'GATE  jackpot AUC above {JACK_BAR:.2f}: {win} of {n} '
                  f'folds (bar 8), mean {np.nanmean(aucs):.3f}, '
                  f'diagnostic-era ceiling {JACK_DIAG:.2f}')
            if n < 15:
                # the bar is 8 of 15 in absolute folds, so a narrowed
                # window cannot pronounce on it either way
                print(f'this window has {n} fitted folds, not 15: the gate '
                      f'is not decided here. Run without --until.')
                return
            if win >= 8:
                print('the gate CLEARS: the three-part expectation of '
                      'Amendment 11 is now authorised and is not built '
                      'yet. No book is simulated from this run.')
            else:
                print('the gate FAILS. Four purpose-built losses in the '
                      'voided era, the standing diagnostic, and a '
                      'dedicated fair shot under the honest machinery all '
                      'agree: jackpots are not predictable from these '
                      'windows. No book is simulated.')
            return
        elif target == 'crashvalue':
            sc, fitted = crashvalue_walk_forward(
                build, feat_id, rv, m['y'].to_numpy(np.float64), date,
                blocks, alpha, src, embargo, cached_only)
            crow = None
        elif target in ('value', 'daymean'):
            sc, fitted = value_walk_forward(
                build, feat_id, rv, ei, blocks, alpha, src, embargo,
                hold, date,
                grp_at=(n_f - 2 if arm.endswith('+group') else None),
                name=('ridge-daymean' if target == 'daymean'
                      else 'ridge-value'),
                cached_only=cached_only)
            crow = None
        else:
            sc, fitted, crow = score_walk_forward(build, feat_id, PD, taken,
                                                  date, blocks, alpha, src,
                                                  embargo)
        years_of[arm] = fitted
        if rent_c is None and crow is not None:
            rent_c = crow
        if arm == 'rocket':
            rocket_score = sc[1.0]
        ok = np.isfinite(sc[1.0])
        if min_score is not None:
            print(f'  the natural zero at {min_score:g}: what the model '
                  f'would decline, and what those bets actually returned')
            zero_split(sc[1.0], pool_y, m['entry_date'].dt.year.to_numpy())
        print(f'  {int(ok.sum()):,} of {len(PD):,} signals scored; the rest '
              f'sit in years with too little history to fit on and keep '
              f'the control ordering')
        # THE SENSITIVITY BAND, and it is free: the two heads give every
        # rent, so c/2 and 2c cost three dot products. Theory says the
        # ranking should move slowly with c; a book that swings across
        # this band is fragile and the run has to say so.
        row = {}
        for mu in (sc if target != 'rent' else RENT_MULTS):
            A = S.copy()
            A[ei[ok], tj[ok]] = sc[mu][ok]
            t_, eq_, inv_, _ = simulate(panel, cfg, (j0, j1), moc=True,
                                        pool_days=pool, scores=A,
                                        min_score=min_score)
            d_ = pd.DataFrame(t_)
            row[mu] = (A, d_, eq_, inv_)
        sens[arm] = row
        books[arm] = row[1.0][:2]

    # ---- the corner book (Amendment 12) -----------------------------
    # At most one book, for the single best cell, and the preference is
    # expressed as a re-ranking rather than as a filter: corner members
    # first among themselves, everyone else after, both groups ordered by
    # the composed three-part score. Nothing is dropped and no threshold
    # sits in the score path.
    for arm, (cell, member, cscore) in corner_books.items():
        nm = f'{arm}+corner'
        ok = np.isfinite(cscore)
        A = S.copy()
        A[ei[ok], tj[ok]] = corner_first(cscore, member)[ok]
        t_, eq_, inv_, _ = simulate(panel, cfg, (j0, j1), moc=True,
                                    pool_days=pool, scores=A)
        print()
        print(f'the corner book: cell X{cell[0]} Y{cell[1]}, '
              f'{int(member[ok].sum()):,} of {int(ok.sum()):,} scored '
              f'signals ranked ahead of the rest, judged on geo/bet '
              f'against the value-5y arm\'s +0.67%')
        books[nm] = (A, pd.DataFrame(t_))
        sens[nm] = {1.0: (A, pd.DataFrame(t_), eq_, inv_)}
        arms.append(nm)

    # ---- the crash-guard composition (Amendment 9.2) ----------------
    if compose:
        if 'rocket' not in arms:
            sys.exit('--compose reads the rocket arm cached scores; '
                     'add rocket to --arms')
        print()
        print(f'composition: w * pctile(value_{"5y" if lookback else "exp"})'
              f' + (1-w) * pctile(ratio-era), per day')
        guard = cached_ratio_scores(src, date, exits, embargo)
        for w in compose:
            A = blend_two(S, rocket_score, guard, ei, tj, w)
            t_, eq_, inv_, _ = simulate(panel, cfg, (j0, j1), moc=True,
                                        pool_days=pool, scores=A)
            nm = f'guard w={w:g}'
            books[nm] = (A, pd.DataFrame(t_))
            sens[nm] = {1.0: (A, pd.DataFrame(t_), eq_, inv_)}
            arms.append(nm)

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
    report_book('strength', st, eq_s, inv_s, pd_of, rent_of, PD, pool_y,
                pool_rent(PD, rent_c))
    for arm in [a for a in arms if a != 'strength']:
        A, d_, eq_, inv_ = sens[arm][1.0]
        report_book(arm, d_, eq_, inv_, pd_of, rent_of, PD, pool_y,
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
        report_book(a, pd.DataFrame(t2), e2, i2, pd_of, rent_of,
                    PD, pool_y, pool_rent(PD, rent_c))

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
