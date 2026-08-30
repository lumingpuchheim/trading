"""MiniRocket + ridge on the v5r bet windows: the no-training baseline.

Nothing here is learned by gradient descent. The feature extractor is
FIXED -- 84 kernels of length 9 whose weights are all -1 or 2 (every way
of choosing 3 positions out of 9), applied at five dilations, pooled by
PPV (the fraction of the convolution that clears a bias). Only the ridge
classifier is fitted, and ridge has a closed-form solution.

That is the point. Every objection raised against the CNN -- can
backpropagation handle this many parameters, is the network too small to
optimise, how are the weights updated, which seed, when to stop -- does
not apply to a closed-form fit. There is no optimiser, no seed, no early
stopping and no local minimum. If this finds nothing, nothing was there
to find; the null stops being a statement about our training loop.

MiniRocket is also the reference baseline for time-series classification
at exactly this data shape (Dempster, Schmidt & Webb 2020,
arxiv.org/abs/2012.08791), and its authors' own guidance -- ridge is
preferred when features outnumber training examples and the dataset is
small -- describes this problem.

Label and protocol are the CNN's, so the numbers are comparable line for
line: a = 1[y >= 80th percentile of DEV], purged walk-forward with a
labels purged by exit date; --test pools every block out of fold.

Usage
    python minervini_rocket.py --data results/..._f16.npz
    python minervini_rocket.py --shuffle          # label-shuffle control
    python minervini_rocket.py --dilations 1,2,4,8,16 --biases 2
"""

import itertools
import sys

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from sklearn.linear_model import RidgeClassifierCV

from bets_common import (AUX_Q, LOOKBACK_YEARS, folds, label_from, line,
                         load, report, warmup_rows, year_blocks)

KERNEL_LEN = 9
N_POS = 3                     # weights: 3 twos and 6 minus-ones, so they sum to 0
ALPHAS = np.logspace(-3, 5, 17)


def kernels() -> torch.Tensor:
    """The 84 = C(9,3) MiniRocket kernels. Fixed, not random, not learned."""
    w = -np.ones((0, KERNEL_LEN), np.float32)
    rows = []
    for combo in itertools.combinations(range(KERNEL_LEN), N_POS):
        k = -np.ones(KERNEL_LEN, np.float32)
        k[list(combo)] = 2.0
        rows.append(k)
    w = np.stack(rows)
    assert w.shape[0] == 84 and abs(w.sum()) < 1e-6
    return torch.from_numpy(w).unsqueeze(1)          # (84, 1, 9)


def conv_channel(x1: torch.Tensor, W: torch.Tensor, d: int) -> torch.Tensor:
    """(N,1,T) -> (N,84,T) at dilation d, length preserved."""
    return F.conv1d(x1, W, dilation=d, padding=((KERNEL_LEN - 1) * d) // 2)


def fit_biases(x: np.ndarray, W, dilations, n_bias: int, rows: np.ndarray,
               qs: np.ndarray) -> dict:
    """Bias values per (channel, dilation, kernel), read off the quantiles of
    the convolution on a sample of TRAINING rows only."""
    out = {}
    xb = torch.from_numpy(x[rows])
    for c in range(x.shape[1]):
        for d in dilations:
            z = conv_channel(xb[:, c:c + 1, :], W, d)          # (n,84,T)
            z = z.permute(1, 0, 2).reshape(84, -1)
            out[(c, d)] = torch.quantile(z, torch.from_numpy(qs), dim=1).T
    return out                                                  # (84, n_bias)


def transform(x: np.ndarray, W, dilations, bias: dict,
              batch: int = 2048) -> np.ndarray:
    """PPV features. Convolutions are made and thrown away per batch: the
    full (N, 84, 252) intermediate would be ~5 GB."""
    n, C, _ = x.shape
    nb = bias[(0, dilations[0])].shape[1]
    F_ = C * len(dilations) * 84 * nb
    out = np.empty((n, F_), np.float32)
    print(f'transform: {n:,} rows -> {F_:,} features '
          f'({n * F_ * 4 / 1e9:.2f} GB)', flush=True)
    for s in range(0, n, batch):
        xb = torch.from_numpy(x[s:s + batch])
        cols = []
        for c in range(C):
            for d in dilations:
                z = conv_channel(xb[:, c:c + 1, :], W, d)       # (b,84,T)
                b = bias[(c, d)]                                # (84,nb)
                for j in range(nb):
                    cols.append((z > b[:, j][None, :, None]).float().mean(-1))
        out[s:s + batch] = torch.cat(cols, dim=1).numpy()
        if s == 0:
            print(f'  first batch ok', flush=True)
    return out


def ridge_scores(ftr, atr, fev):
    """Closed-form fit. No optimiser, no seed, no early stopping."""
    mu, sd = ftr.mean(0), ftr.std(0) + 1e-8
    clf = RidgeClassifierCV(alphas=ALPHAS, class_weight='balanced')
    clf.fit((ftr - mu) / sd, atr)
    return clf.decision_function((fev - mu) / sd), float(clf.alpha_)


def channel_subsets(C: int, n_groups: int, seed: int = 0) -> list:
    """Channel subsets for the MULTIVARIATE transform.

    The per-channel transform convolves one channel at a time and
    concatenates, so every feature describes a single channel and none can
    say "price contracted WHILE volume dried up" -- which is the VCP claim
    (LIMITATIONS.md, 2026-08-29). Here a kernel is applied to a SUBSET of
    channels and their convolutions are SUMMED BEFORE pooling, so one
    feature fires only when those channels move together.

    Published MiniRocket-Multivariate draws the subsets at random. The
    first four here are fixed instead -- price alone, volume alone, price
    AND volume, and price+sma+volume -- so the interaction this experiment
    exists to test is guaranteed representable rather than left to a draw.
    Remaining groups are random, as published.
    """
    fixed = [np.array([0]), np.array([2]), np.array([0, 2]),
             np.array([0, 1, 2])]
    subs = [f for f in fixed if f.max() < C][:n_groups]
    rng = np.random.default_rng(seed)
    while len(subs) < n_groups:
        k = int(rng.integers(1, C + 1))
        subs.append(np.sort(rng.choice(C, size=k, replace=False)))
    return subs


def _group_conv(xb, W, sub, d):
    z = conv_channel(xb[:, int(sub[0]):int(sub[0]) + 1, :], W, d)
    for c in sub[1:]:
        z = z + conv_channel(xb[:, int(c):int(c) + 1, :], W, d)
    return z


def fit_biases_mv(x, W, dilations, n_bias, rows, qs, subs) -> dict:
    out = {}
    xb = torch.from_numpy(x[rows])
    for g, sub in enumerate(subs):
        for d in dilations:
            z = _group_conv(xb, W, sub, d)
            z = z.permute(1, 0, 2).reshape(84, -1)
            out[(g, d)] = torch.quantile(z, torch.from_numpy(qs), dim=1).T
    return out


def transform_mv(x, W, dilations, bias, subs, batch: int = 2048):
    n = x.shape[0]
    nb = bias[(0, dilations[0])].shape[1]
    F_ = len(subs) * len(dilations) * 84 * nb
    out = np.empty((n, F_), np.float32)
    print(f'transform (multivariate): {n:,} rows -> {F_:,} features, '
          f'{len(subs)} channel groups {[list(map(int, s)) for s in subs]}',
          flush=True)
    for s0 in range(0, n, batch):
        xb = torch.from_numpy(x[s0:s0 + batch])
        cols = []
        for g, sub in enumerate(subs):
            for d in dilations:
                z = _group_conv(xb, W, sub, d)
                b = bias[(g, d)]
                for j in range(nb):
                    cols.append((z > b[:, j][None, :, None]).float().mean(-1))
        out[s0:s0 + batch] = torch.cat(cols, dim=1).numpy()
    return out


def shuffle_labels(y, date, mode: str, rng):
    """Build one draw from the null.

    mode='row'   -- reassign outcomes bet by bet. Destroys the clumping
                    that real outcomes have (overlapping holds, a shared
                    market factor), so the null comes out SMOOTHER than
                    reality and the p-value is flattered.
    mode='block' -- keep contiguous calendar quarters intact and shuffle
                    the quarters. A good quarter stays a good quarter and
                    merely lands elsewhere in history, so the null carries
                    the same serial correlation the real data does.
    """
    n = len(y)
    if mode == 'row':
        return y[rng.permutation(n)]
    order = np.argsort(date, kind='stable')
    q = pd.PeriodIndex(pd.to_datetime(date[order]), freq='Q')
    edges = np.flatnonzero(np.r_[True, q[1:] != q[:-1]])
    blocks = np.split(y[order], edges[1:])
    perm = rng.permutation(len(blocks))
    out = np.empty(n, dtype=y.dtype)
    out[order] = np.concatenate([blocks[k] for k in perm])
    return out


def evaluate(feats, y, date, exits, nfold, verbose=True):
    """Walk-forward validation over the whole record. The label is cut at
    AUX_Q of each fold's OWN training rows -- never once, from a fixed
    slice of history (EVALUATION_SPEC.md rule 1)."""
    rows = []
    for tr, va, v0, v1 in folds(date, nfold, exits):
        sc, alpha = ridge_scores(feats[tr], label_from(y, tr)[tr], feats[va])
        m = report(sc, y[va]); m['alpha'] = alpha
        rows.append(m)
        if verbose:
            print(line(f'  val {v0[:7]}..{v1[:7]}', m) + f'  alpha {alpha:.3g}',
                  flush=True)
    agg = {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}
    agg['n'] = int(np.sum([r['n'] for r in rows]))
    if verbose:
        print(line('  VAL MEAN', agg), flush=True)
    return agg, rows


def main() -> None:
    av = sys.argv
    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    d = load(opt('--data', None))
    x, y, date = d['x'], d['y'], d['date']
    if '--shuffle' in av:
        y = y[np.random.default_rng(0).permutation(len(y))]
        print('LABEL-SHUFFLE CONTROL: lift ~0, keep1% ~10%, auc ~0.50 expected')

    thr = float(np.quantile(y, AUX_Q))    # for the banner only
    dil = [int(v) for v in opt('--dilations', '1,2,4,8,16').split(',')]
    nb = opt('--biases', 2, int)
    qs = np.linspace(0.0, 1.0, nb + 2)[1:-1].astype(np.float32)

    W = kernels()
    print(f'MiniRocket: {W.shape[0]} fixed kernels x {len(dil)} dilations '
          f'{dil} x {nb} biases x {x.shape[1]} channels, '
          f'label ~y>={thr:.4f}, cut per fold on its own training rows')
    print('learned parameters in the transform: 0', flush=True)

    rng = np.random.default_rng(0)
    seed_rows = warmup_rows(date, 2000, rng)
    bias = fit_biases(x, W, dil, nb, seed_rows, qs)
    feats = transform(x, W, dil, bias)
    del x

    nfold = opt('--folds', 4, int)
    lookback = opt('--lookback', LOOKBACK_YEARS or 0, float) or None
    agg, rows = evaluate(feats, y, date, d['exit'], nfold)

    n_perm = opt('--permtest', 0, int)
    modes = opt('--nulls', 'row,block').split(',')
    if n_perm:
        # The real question fold 4 raises: how often does a null produce a
        # fold that good? One shuffle cannot say. Re-run the WHOLE procedure
        # -- relabel, refit, re-select alpha -- on n_perm permutations and
        # place the real numbers in the distribution that produces.
        print()
        print(f'permutation test: {n_perm} relabelings x {len(modes)} null '
              f'model(s) {modes}, full refit each', flush=True)
        obs = {'mean_lift': agg['lift'], 'max_lift': max(r['lift'] for r in rows),
               'mean_auc': agg['auc'], 'max_auc': max(r['auc'] for r in rows),
               'keep': agg['keep1%']}
        for mode in modes:
            null = {k: [] for k in obs}
            pr = np.random.default_rng(12345)
            print()
            print(f'--- null model: {mode} ---', flush=True)
            for i in range(n_perm):
                yp = shuffle_labels(y, date, mode, pr)
                a2, r2 = evaluate(feats, yp, date, d['exit'], nfold,
                                  verbose=False)
                null['mean_lift'].append(a2['lift'])
                null['max_lift'].append(max(r['lift'] for r in r2))
                null['mean_auc'].append(a2['auc'])
                null['max_auc'].append(max(r['auc'] for r in r2))
                null['keep'].append(a2['keep1%'])
                print(f'  {mode} perm {i+1:2d}/{n_perm}  '
                      f'mean_lift {a2["lift"]:+.4f}  '
                      f'max_lift {max(r["lift"] for r in r2):+.4f}  '
                      f'auc {a2["auc"]:.3f}', flush=True)

            print()
            print(f'NULL MODEL {mode.upper()}   (p = 1/{n_perm + 1} = '
                  f'{1 / (n_perm + 1):.3f} is the floor: no null run reached '
                  f'the observed value)')
            print(f'{"statistic":12s} {"observed":>10s} {"null mean":>10s} '
                  f'{"null sd":>9s} {"null p95":>10s} {"p-value":>9s}')
            for k in obs:
                v = np.array(null[k])
                p = float((v >= obs[k]).sum() + 1) / (n_perm + 1)
                print(f'{k:12s} {obs[k]:10.4f} {v.mean():10.4f} '
                      f'{v.std(ddof=1):9.4f} {np.quantile(v, 0.95):10.4f} '
                      f'{p:9.3f}')

    if '--test' in av:
        # Out-of-fold over the WHOLE record: every block scored by a fit
        # that ended `embargo` days before it, then pooled. There is no
        # reserved tail and no special year (EVALUATION_SPEC.md rule 1).
        score = np.full(len(y), np.nan)
        for Y, trm, ev in year_blocks(date, d['exit'],
                                      lookback_years=lookback):
            a_tr = label_from(y, trm)[trm]
            if len(set(a_tr.tolist())) < 2:
                continue
            score[ev], alpha = ridge_scores(feats[trm], a_tr, feats[ev])
            print(f'  {Y}: fit {int(trm.sum()):,}, scored {int(ev.sum()):,}, '
                  f'alpha {alpha:.3g}', flush=True)
        oof = np.isfinite(score)
        print(line(f'  OUT-OF-FOLD, all {int(oof.sum()):,}',
                   report(score[oof], y[oof])))


if __name__ == '__main__':
    main()
