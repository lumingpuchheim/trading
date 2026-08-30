"""Models indexed by when their training ENDED, so the embargo is free.

MODEL_STORE_SPEC.md has the reasoning. The short version: today's code
indexes a fit by the block it scores, so moving the embargo invalidates
every block at once and all eighteen refit. But a fitted model does not
know which block it will score -- it is determined by its training
window, its inputs and its learner. Index it that way and the embargo
becomes arithmetic done at scoring time:

    required_end = block_open - embargo
    model        = the stored model whose train_end is the latest grid
                   date AT OR BEFORE required_end        (never after)
    score        = that model applied to the block's rows

Sweeping ten embargo values then costs ten dot products and zero fits.

The grid is month ends, so the realised embargo is between what was asked
for and about 31 days more. That is reported per run, never hidden: the
2026-08-30 sweep moved a hundred points between 200 and 400 days, so a
fortnight of unreported slack is not acceptable.

A model is ~50 kB. A monthly grid over this record is ~210 of them, about
19 MB -- worth stating on a machine with 9.2 GB free.
"""

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeClassifier, RidgeClassifierCV

import fitcache

STORE = 'model'


def month_ends(dates: np.ndarray) -> np.ndarray:
    """The grid of candidate training cut-offs: the last calendar day of
    every month the record spans."""
    d = pd.to_datetime(np.asarray(dates).astype('datetime64[D]'))
    lo, hi = d.min(), d.max()
    return (pd.date_range(lo, hi, freq='ME')
            .to_numpy().astype('datetime64[D]'))


def snap(grid: np.ndarray, required_end) -> object:
    """The latest grid date AT OR BEFORE `required_end`, or None.

    Snapping down is a correctness rule, not a convenience: a model whose
    training ended later than required has seen data closer to the block
    than the caller asked for."""
    required_end = np.datetime64(required_end, 'D')
    ok = grid[grid <= required_end]
    return ok[-1] if len(ok) else None


def train_mask(dates: np.ndarray, train_end, lookback_years) -> np.ndarray:
    """Rows a model with this training window may see: everything up to
    and including `train_end`, and no older than `lookback_years`."""
    d = np.asarray(dates).astype('datetime64[D]')
    te = np.datetime64(train_end, 'D')
    m = d <= te
    if lookback_years:
        span = np.timedelta64(int(round(float(lookback_years) * 365)), 'D')
        m = m & (d > te - span)
    return m


def key(src, kind, train_end, lookback_years, alpha, aux_q, extra=()):
    """A model's identity. `src` carries the windows file, so rebuilding
    the ledger invalidates every entry without anyone remembering to."""
    return fitcache.key(STORE, src, kind, str(np.datetime64(train_end, 'D')),
                        lookback_years, alpha, aux_q, tuple(extra))


def fit_ridge(feats, y, mask, alpha, aux_q):
    """One model. The label threshold is the aux_q quantile of THIS
    window's outcomes and is stored with the model, so it can never be
    recomputed later from a different set of rows."""
    thr = float(np.quantile(y[mask], aux_q))
    a = (y[mask] >= thr).astype(np.int8)
    if len(set(a.tolist())) < 2:
        return None
    mu = feats[mask].mean(0).astype(np.float32)
    sd = (feats[mask].std(0) + 1e-8).astype(np.float32)
    xt = np.asarray(feats[mask], dtype=np.float32)
    xt -= mu
    xt /= sd
    clf = (RidgeClassifierCV(alphas=np.logspace(-3, 5, 17),
                             class_weight='balanced') if alpha == 'cv'
           else RidgeClassifier(alpha=float(alpha), class_weight='balanced'))
    clf.fit(xt, a)
    rec = {'coef': np.asarray(clf.coef_, np.float32).ravel(),
           'intercept': np.asarray(clf.intercept_, np.float32).ravel(),
           'mu': mu, 'sd': sd,
           'thr': np.float32(thr),
           'train_scores': clf.decision_function(xt).astype(np.float32),
           'n_train': np.int64(int(mask.sum())),
           'alpha': np.float32(getattr(clf, 'alpha_', 0.0)
                               if alpha == 'cv' else float(alpha))}
    del xt
    return rec


def apply(rec, feats, rows) -> np.ndarray:
    """Score rows with a stored model: standardise with ITS mu/sd, then a
    dot product. No fitting, which is the entire point."""
    xe = np.asarray(feats[rows], dtype=np.float32)
    xe -= rec['mu']
    xe /= rec['sd']
    out = xe @ rec['coef'] + rec['intercept'][0]
    del xe
    return out


def get_or_fit(src, kind, train_end, lookback_years, alpha, aux_q,
               feats, y, dates, min_train):
    """The stored model for this training window, fitting and storing it
    on first use. Returns None if the window has too little in it."""
    k = key(src, kind, train_end, lookback_years, alpha, aux_q)
    rec = fitcache.load(STORE, k)
    if rec is not None:
        return rec, True
    m = train_mask(dates, train_end, lookback_years)
    if m.sum() < min_train:
        return None, False
    rec = fit_ridge(feats, y, m, alpha, aux_q)
    if rec is None:
        return None, False
    fitcache.save(STORE, k, **rec)
    return rec, False
