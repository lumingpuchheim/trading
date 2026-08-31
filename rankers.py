"""Rankers: candidate in, one number out -- and nothing else.

    ranker.score(features)  ->  predicted growth rate of a euro spent on
                                this candidate, in ln per trading day

That is the whole interface. A ranker never trades, never vetoes and
never sees a calendar: `filter_backtest.py` fits it fold by fold and
hands `simulate()` a (days x tickers) matrix of its scores, and
`simulate()` fills each day's free slots from the top of that matrix.
Slot capacity is the only selectivity there is.

This replaces `filters.py`, whose veto-plus-strength-sort construction
was audited and retired on 2026-08-31 (DECISIONS.md, "The filter
architecture is wrong"; RANKER_SPEC.md is the contract implemented
here). `filters.py` survives only because `equity_vs_spy.py`,
`filter_agreement.py` and `rocket_ev.py` still reproduce numbers from
that retired chain; nothing new may use it.

Two arms live here.

`StrengthScore` fits nothing and encodes the ordering the book uses
today, so "run the new architecture and change nothing" reproduces
today's AllPass book exactly. It is the control, and no fitted row may
be read from a run in which it has not reproduced.

`RidgeRanker` is the fitted arm: least squares on the rate itself,
closed form, alpha chosen on the fold's own training rows. The transform
in front of it (MiniRocket today) is assembled by the driver, so the
same estimator serves every future transform without a line changing
here.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import rankdata

from bets_common import EMBARGO_DAYS, INNER_MIN

# The grid the measured `ridge-loo` run used. Kept exactly as it was so
# that run's cache entries stay loadable as the record behind the
# DECISIONS row (RANKER_SPEC Amendment 1, acceptance 5).
ALPHAS = np.logspace(-3, 5, 17)
# The grid the grouped criterion searches. It GROWS UPWARD: the old top
# of 1e5 presumed an answer, and the criterion has to be able to say
# "shrink almost everything" if that is the truth.
YCV_ALPHAS = np.logspace(-3, 8, 23)


class Ranker:
    """Candidate in, one predicted rate out."""

    name = 'base'

    def fit(self, F: np.ndarray, r: np.ndarray,
            when: np.ndarray | None = None) -> 'Ranker':
        """`when` is the entry date of each row. The alpha criterion
        groups by it (RANKER_SPEC Amendment 1); a ranker that does not
        fit ignores it."""
        raise NotImplementedError

    def score(self, F: np.ndarray) -> np.ndarray:
        raise NotImplementedError


# ----------------------------------------------------------------------
# the do-nothing arm
# ----------------------------------------------------------------------

def strength_matrix(panel: dict, pool_days: list, i0: int, i1: int):
    """Today's slot ordering, encoded as one float per (day, ticker).

    `simulate()` picks by `(-rsl_hi, -weak, -rs, ticker)` -- four keys
    read lexicographically, with NaN sorting last. One float has to carry
    all of that, or the new architecture cannot reproduce the old book.

    Per day, rank `weak` and `rs` over that day's pool. Ranks are DENSE,
    so equal values share one and a tie still falls through to the next
    key, exactly as a tuple comparison does. Then compose

        score = (rsl_hi * B + rank_weak) * B + rank_rs

    with B one more than the largest pool, so no level can reach into the
    one above it. Every term is a small integer and float64 is exact far
    beyond the B**2 + 2B this reaches (B = 319 -> 203,841), so the
    encoding has no freedom: sorting by this score descending, ticker
    ascending, is the same permutation as the tuple sort, and any
    difference in the book is a bug in the score path rather than a
    rounding accident.

    Returns (matrix, B). Names outside a day's pool score -inf.
    """
    rsl, wk, rsv = panel['rsl_hi'], panel['weak'], panel['rs']
    n_days, n_tick = panel['close'].shape
    lo, hi = max(0, i0), min(n_days - 1, i1)
    B = max((len(pool_days[i]) for i in range(lo, hi + 1)), default=0) + 1
    E = np.full((n_days, n_tick), -np.inf)
    for i in range(lo, hi + 1):
        P = pool_days[i]
        if not len(P):
            continue
        w = np.where(np.isfinite(wk[i, P]), wk[i, P], -np.inf)
        s = np.where(np.isfinite(rsv[i, P]), rsv[i, P], -np.inf)
        E[i, P] = ((rsl[i, P].astype(np.float64) * B
                    + rankdata(w, method='dense')) * B
                   + rankdata(s, method='dense'))
    return E, B


class StrengthScore(Ranker):
    """The control arm. Fits nothing; reproduces today's book or fails.

    Its score is not a function of a bet's window at all -- it is the
    panel's own strength keys, read on the day the ORDER is placed, which
    is where `simulate()` reads them today. That is one day before the
    fill the score decides, so it is causal by a day's margin, and it is
    the only way the encoding can be the same permutation the incumbent
    sort produces.
    """

    name = 'strength'

    def fit(self, F, r, when=None):
        return self

    def score(self, F):
        raise NotImplementedError(
            'StrengthScore is a panel quantity, not a per-window one: '
            'build its matrix with rankers.strength_matrix(panel, ...)')


# ----------------------------------------------------------------------
# the fitted arm
# ----------------------------------------------------------------------

def loo_ridge(X: np.ndarray, y: np.ndarray, alphas=ALPHAS, chunk=2048):
    """Ridge regression with alpha chosen by exact leave-one-out.

    Closed form twice over: the fit itself is closed form, and so is the
    choice of alpha. One eigendecomposition of the Gram matrix serves the
    whole grid, because a ridge fit's leave-one-out residual is available
    without ever leaving one out --

        LOO_i(a) = (y_i - yhat_i(a)) / (1 - h_ii(a))

    -- so seventeen alphas cost one decomposition, not seventeen fits and
    certainly not seventeen times n of them.

    THE CHEAP APPROXIMATION DOES NOT SURVIVE HERE, which is why h_ii is
    computed rather than averaged. Generalised cross-validation replaces
    h_ii by its mean, df/n, and that is fine while df stays far below n.
    Three of the first four folds have FEWER rows than the 4,206 features
    -- 2,174 in 2009 -- so the fit can interpolate, df approaches n, and
    the GCV ratio becomes 0/0: it picked alpha=0.001 for 2012 and left an
    out-of-fold mse of 0.78 against a training mse of 1.9e-05. The exact
    denominator does not degenerate that way; it goes to zero for the row
    that is being interpolated, which is the whole point.

    Two routes to the same numbers, chosen by shape. n < p decomposes the
    n x n Gram in sample space; n >= p decomposes the p x p one and takes
    one extra pass over the rows in chunks to accumulate the hat diagonal
    and the fitted values for every alpha at once. `sklearn.RidgeCV` does
    this from an SVD of X, which at 49,334 x 4,206 needs the better part
    of four gigabytes; nothing here holds more than the p x p Gram
    (142 MB) and a chunk, and this repo has been killed twice by peak
    memory (HANDOVER trap: one avoidable 1.7 GB copy).

    X must already be standardised (mean 0) -- that is where the caller
    puts the fold's own mu and sd. `y` is centred here and its mean comes
    back as the intercept; the intercept's own degree of freedom enters
    the hat diagonal as the 1/n term.

    The Gram is accumulated in float32: one sgemm rather than a float64
    one at eight times the cost. Over 49k standardised rows that is a
    relative error of ~1e-5 on entries of order n, which no alpha in this
    grid can feel.

    Returns (coef, intercept, alpha, train predictions).
    """
    X = np.asarray(X, dtype=np.float32)
    n, p = X.shape
    A = np.asarray(alphas, dtype=np.float64).ravel()
    ym = float(np.mean(y))
    yc = np.asarray(y, dtype=np.float64) - ym
    dual = n < p
    M = np.asarray((X @ X.T) if dual else (X.T @ X), dtype=np.float64)
    L, V = np.linalg.eigh(M)
    del M
    L = np.clip(L, 0.0, None)
    c = (V.T @ yc if dual else
         V.T @ np.asarray(X.T @ yc.astype(np.float32), dtype=np.float64))

    if len(A) == 1:
        alpha = float(A[0])
    else:
        H = np.empty((n, len(A)))          # hat diagonal, per alpha
        F = np.empty((n, len(A)))          # fitted values, per alpha
        D = 1.0 / (L[:, None] + A[None, :])
        if dual:
            # h_i = sum_k U_ik^2 L_k/(L_k+a),  yhat_i = sum_k U_ik w_k c_k
            Wt = L[:, None] * D
            H[:] = (V ** 2) @ Wt
            F[:] = V @ (Wt * c[:, None])
        else:
            # Z = X V:  h_i = sum_k Z_ik^2/(L_k+a),  yhat = Z (c/(L+a))
            CD = c[:, None] * D
            Vf = V.astype(np.float32)
            for s in range(0, n, chunk):
                Z = np.asarray(X[s:s + chunk] @ Vf, dtype=np.float64)
                H[s:s + chunk] = (Z ** 2) @ D
                F[s:s + chunk] = Z @ CD
                del Z
            del Vf, CD
        # + 1/n: the intercept costs a degree of freedom too
        den = np.clip(1.0 - H - 1.0 / n, 1e-9, None)
        loo = np.mean(((yc[:, None] - F) / den) ** 2, axis=0)
        alpha = float(A[int(np.argmin(loo))])
        del H, F, den

    d = 1.0 / (L + alpha)
    coef = (np.asarray(X.T @ (V @ (c * d)).astype(np.float32), dtype=np.float64)
            if dual else V @ (c * d))
    pred = np.asarray(X @ coef.astype(np.float32), dtype=np.float64) + ym
    return coef, ym, alpha, pred


def inner_split(when: np.ndarray, Y: int, embargo: int = EMBARGO_DAYS):
    """Which rows a held-out YEAR removes, and which it scores.

    The purge is symmetric around the year, by `embargo` CALENDAR days
    from each of its two boundaries: the left side keeps Y's outcomes out
    of what the inner fit trains on (the embargo exceeds the longest hold
    in the ledger), and the right side keeps out the bets whose feature
    windows look back into Y and whose holding periods overlap its
    unresolved tails. One constant, already defined, both directions.

    Returns (removed, held): `removed` is everything the inner fit may
    not see -- Y plus both margins -- and `held` is Y itself, the rows
    the alpha is graded on.
    """
    d = np.asarray(when).astype('datetime64[D]')
    y0 = np.datetime64(f'{int(Y)}-01-01', 'D')
    y1 = np.datetime64(f'{int(Y)}-12-31', 'D')
    gap = np.timedelta64(int(embargo), 'D')
    return ((d >= y0 - gap) & (d <= y1 + gap), (d >= y0) & (d <= y1))


def ycv_ridge(X: np.ndarray, y: np.ndarray, when: np.ndarray,
              alphas=YCV_ALPHAS, embargo: int = EMBARGO_DAYS,
              inner_min: int = INNER_MIN):
    """Ridge whose alpha is chosen by GROUPED, PURGED cross-validation.

    RANKER_SPEC.md Amendment 1, and the reason for it: leave-one-bet-out
    picked alpha=100 while a constant beat the fit out of fold in all 18
    folds. The bets are not independent -- about twelve share each
    trading day's market move and their 252-day windows overlap almost
    completely -- so hiding ONE bet leaves its twins in the training set.
    The criterion then recognises the held-out bet instead of predicting
    it, and under-regularises with full confidence.

    So hide a bet together with its twins. Group the rows by the calendar
    year of entry; for each held-out year, purge symmetrically around it
    (`inner_split`), fit on what is left, and score that year. Pool the
    held-out bets across years -- one vote per bet, the convention
    everywhere else -- and take the alpha with the smallest pooled mse.
    It is deliberately NOT the outer block that judges alpha: that block
    would then leak into model selection and the walk-forward would stop
    being one.

    GRAM MATRICES ADD OVER ROWS, which is the whole trick. One pass
    builds `G = X'X`, `b = X'y` and the column sums over every training
    row; each held-out year then costs one pass over the rows it removes
    and a subtraction:

        G_inner = G - G_out,   b_inner = b - b_out

    One eigendecomposition of `G_inner` serves the entire alpha grid for
    that year. Peak memory stays at a few p x p float64 matrices (142 MB
    at p = 4,206), never an n x p copy.

    X must already be standardised on the OUTER fold's training rows --
    not re-centred per inner fit, because that is exactly what would
    break the subtraction. The intercept is the inner fit's own mean of
    y, which the column sums correct for.

    Returns (coef, intercept, alpha, train predictions, usable years), or
    None if fewer than two years survive the purge with `inner_min` rows.
    A caller that gets None must fit nothing for that fold.
    """
    X = np.asarray(X, dtype=np.float32)
    n, p = X.shape
    y = np.asarray(y, dtype=np.float64)
    A = np.asarray(alphas, dtype=np.float64).ravel()
    when = np.asarray(when).astype('datetime64[D]')

    G = np.asarray(X.T @ X, dtype=np.float64)
    b = np.asarray(X.T @ y.astype(np.float32), dtype=np.float64)
    s = np.asarray(X.sum(0), dtype=np.float64)
    ysum = float(y.sum())

    pooled = np.zeros(len(A))
    graded, used = 0, []
    for Y in sorted({int(v) for v in when.astype('datetime64[Y]').astype(int)
                     + 1970}):
        out, held = inner_split(when, Y, embargo)
        n_held = int(held.sum())
        n_in = n - int(out.sum())
        if n_held == 0 or n_in < inner_min:
            continue
        Xo = X[out]
        Gi = G - np.asarray(Xo.T @ Xo, dtype=np.float64)
        bi = b - np.asarray(Xo.T @ y[out].astype(np.float32), dtype=np.float64)
        si = s - np.asarray(Xo.sum(0), dtype=np.float64)
        ybar = (ysum - float(y[out].sum())) / n_in
        del Xo
        L, V = np.linalg.eigh(Gi)
        del Gi
        L = np.clip(L, 0.0, None)
        c = V.T @ (bi - ybar * si)
        Z = np.asarray(X[held] @ V.astype(np.float32), dtype=np.float64)
        del V
        yh = y[held]
        for k, a in enumerate(A):
            pooled[k] += float(np.sum((Z @ (c / (L + a)) + ybar - yh) ** 2))
        del Z
        graded += n_held
        used.append(Y)
    if len(used) < 2:
        return None
    alpha = float(A[int(np.argmin(pooled / max(graded, 1)))])

    L, V = np.linalg.eigh(G)
    L = np.clip(L, 0.0, None)
    ym = ysum / n
    coef = V @ ((V.T @ (b - ym * s)) / (L + alpha))
    pred = np.asarray(X @ coef.astype(np.float32), dtype=np.float64) + ym
    return coef, ym, alpha, pred, used


class RidgeRanker(Ranker):
    """Least squares on the rate itself. No label, no threshold, no veto.

    The loss IS the goal: squared error estimates the expected rate, and
    ranking by expected rate is the greedy-optimal slot assignment when
    every slot-day not spent on A is available to B.

    Standardisation comes from the fold's own training rows and is
    carried with the model, so nothing about the block being scored ever
    reaches the fit. Alpha comes from `ycv_ridge` -- grouped and purged,
    never leave-one-out, for the reason in that docstring. A fold whose
    training window cannot supply two purged years fits nothing:
    `fitted_` is False and the caller keeps the control ordering, exactly
    as the years before the first fittable block do.
    """

    def __init__(self, name: str = 'rocket', alpha='cv', alphas=YCV_ALPHAS,
                 embargo: int = EMBARGO_DAYS, inner_min: int = INNER_MIN):
        self.name = name
        self.alpha, self.alphas = alpha, alphas
        self.embargo, self.inner_min = embargo, inner_min

    def fit(self, F: np.ndarray, r: np.ndarray,
            when: np.ndarray | None = None) -> 'RidgeRanker':
        X = np.asarray(F, dtype=np.float32)
        self.mu = X.mean(0)
        self.sd = X.std(0) + np.float32(1e-8)
        X = X - self.mu
        X /= self.sd
        self.fitted_, self.years_ = True, []
        if self.alpha != 'cv':
            (self.coef_, self.intercept_, self.alpha_,
             self.train_pred_) = loo_ridge(X, r, [float(self.alpha)])
            return self
        if when is None:
            raise ValueError('the alpha criterion groups by entry year, so '
                             'fit() needs the entry dates (RANKER_SPEC '
                             'Amendment 1)')
        got = ycv_ridge(X, r, when, self.alphas, self.embargo,
                        self.inner_min)
        if got is None:
            self.fitted_ = False
            return self
        (self.coef_, self.intercept_, self.alpha_,
         self.train_pred_, self.years_) = got
        return self

    def score(self, F: np.ndarray) -> np.ndarray:
        X = np.asarray(F, dtype=np.float32)
        X = X - self.mu
        X /= self.sd
        return (np.asarray(X @ self.coef_.astype(np.float32),
                           dtype=np.float64) + self.intercept_)


REGISTRY = {'strength': StrengthScore, 'rocket': RidgeRanker}
