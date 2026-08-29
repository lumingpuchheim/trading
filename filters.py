"""Trade filters: a screener proposes candidates, a filter says buy or not.

    screener  ->  candidates (n, C, T)  ->  filter.decide(...)  ->  buy mask

The screener here is v5r itself (`minervini_bets.py` emits its signals with
a year of history each). A filter is anything that can look at those
windows and answer yes or no. `AllPass` is what the system does today --
take every signal -- so it is the baseline every other filter must beat,
not a placeholder.

DESIGN NOTES, because two of these are easy to get wrong.

1. The threshold is fixed at FIT time, from the training score
   distribution -- never from the scores of the block being judged. A
   filter that keeps "the best 10% of today's candidates" needs to see the
   whole future block before deciding, which no live agent can do. Here
   `fit` records the score at the `keep` quantile of the training rows and
   `decide` compares each candidate against that number alone. One
   candidate on one day gets the same answer whatever else arrives.

2. Standardisation belongs to the filter, fitted on its training rows and
   carried with it. Re-deriving it from the evaluation block leaks.

Nothing here re-implements a model: the shapelet and MiniRocket internals
are imported from the modules that already have them, so there is one
definition of each and the pipeline numbers match the research runs.

Adding a filter: subclass Filter, implement `fit` and `score`. Ensembles
(vote, rank-average, or a filter over filters) compose the same interface
-- see `Ensemble` for the simplest form.
"""

from __future__ import annotations

import numpy as np
import torch

from bets_common import DEV
from minervini_rocket import (ALPHAS, channel_subsets, fit_biases,
                              fit_biases_mv, kernels, transform,
                              transform_mv)
from minervini_shapelet import ShapeletNet, auto_gamma
from minervini_shapelet import fit as shapelet_fit
from sklearn.linear_model import RidgeClassifierCV


class Filter:
    """Decide whether a screened candidate is worth a euro.

    x is always (n_candidates, n_channels, n_days) -- the same windows the
    screener emitted, so a filter never re-reads prices itself.
    """

    name = 'base'

    def fit(self, x: np.ndarray, y: np.ndarray, aux: np.ndarray,
            keep: float = 0.90) -> 'Filter':
        """Learn from training candidates and record the decision threshold
        at the `keep` quantile of the training scores (keep=0.90 -> approve
        roughly the top tenth of what it will see)."""
        raise NotImplementedError

    def score(self, x: np.ndarray) -> np.ndarray:
        """Higher = more worth buying. Any monotone scale."""
        raise NotImplementedError

    def decide(self, x: np.ndarray) -> np.ndarray:
        return self.score(x) >= self.threshold

    # -- shared plumbing -------------------------------------------------
    def _set_scaler(self, x: np.ndarray) -> None:
        self.mu = x.mean(axis=(0, 2), keepdims=True)
        self.sd = x.std(axis=(0, 2), keepdims=True) + 1e-6

    def _z(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mu) / self.sd

    def _set_threshold(self, train_scores: np.ndarray, keep: float) -> None:
        self.threshold = float(np.quantile(train_scores, keep))
        self.keep = keep


class AllPass(Filter):
    """Take every candidate the screener proposes -- today's behaviour."""

    name = 'AllPass'

    def fit(self, x, y, aux, keep=0.90):
        self.threshold = -np.inf
        self.keep = 1.0
        return self

    def score(self, x):
        return np.zeros(len(x), dtype=np.float32)

    def decide(self, x):
        return np.ones(len(x), dtype=bool)


class ShapeletFilter(Filter):
    """K short curves; the feature is how closely the chart matches each
    one somewhere. 8 x 30 x 1 channel + 9 = 249 parameters."""

    name = 'Shapelet'

    def __init__(self, K=8, L=30, channels=(0,), temp=0.10, seeds=5,
                 epochs=60, patience=8, gamma='auto', lr=3e-3, wd=1e-3,
                 loss='class', thresh='quantile', breakeven=1.0,
                 fb_thresh=1.05, fb_beta=1.0):
        self.K, self.L, self.channels, self.temp = K, L, list(channels), temp
        self.seeds, self.epochs, self.patience = seeds, epochs, patience
        self.gamma, self.lr, self.wd = gamma, lr, wd
        # loss='value': the net's output IS ln(y_hat), so the decision can be
        # read off it directly -- buy when it predicts a gain. That is only
        # meaningful under the value loss; a BCE logit has no such zero.
        self.loss, self.thresh, self.breakeven = loss, thresh, breakeven
        # loss='f1': reward only a correctly predicted >fb_thresh winner
        self.fb_thresh, self.fb_beta = fb_thresh, fb_beta
        self.name = (f'Shapelet(K={K},L={L},{loss}'
                     + (f'>{fb_thresh:g}' if loss == 'f1' else '') + ')')

    def _sub(self, x):
        return x[:, self.channels, :]

    def fit(self, x, y, aux, keep=0.90):
        xs = self._sub(x)
        self._set_scaler(xs)
        n = len(xs)
        cut = int(n * 0.85)                       # tail of train = stopper
        xt = torch.from_numpy(self._z(xs)).to(DEV)
        at = torch.from_numpy(aux.astype(np.float32)).to(DEV)
        yt = torch.from_numpy(y.astype(np.float32)).to(DEV)
        rho = float((aux < 0.5).sum()) / max(1.0, float((aux >= 0.5).sum()))
        g = auto_gamma(y, rho) if self.gamma == 'auto' else float(self.gamma)
        self.gamma_used = g
        self.nets = [shapelet_fit(xt[:cut], at[:cut], yt[:cut],
                                  xt[cut:], at[cut:], s, self.K, self.L,
                                  self.temp, self.epochs, self.patience,
                                  g, self.lr, self.wd,
                                  loss=self.loss, yva=yt[cut:],
                                  fb_thresh=self.fb_thresh,
                                  fb_beta=self.fb_beta)
                     for s in range(self.seeds)]
        s_tr = self.score(x)
        if self.loss == 'value' and self.thresh == 'breakeven':
            # the output is ln(y_hat): buy whatever it expects to grow
            self.threshold = float(np.log(self.breakeven))
            self.keep = float((s_tr >= self.threshold).mean())
            print(f'    {self.name}: predicts ln y_hat in '
                  f'[{s_tr.min():+.4f}, {s_tr.max():+.4f}], median '
                  f'{np.median(s_tr):+.4f}; breakeven threshold approves '
                  f'{self.keep:.1%} of training candidates')
        else:
            self._set_threshold(s_tr, keep)
        return self

    def score(self, x):
        xt = torch.from_numpy(self._z(self._sub(x))).to(DEV)
        with torch.no_grad():
            return np.mean([torch.cat([n(c) for c in xt.split(4096)])
                            .cpu().numpy() for n in self.nets], axis=0)


class RocketFilter(Filter):
    """84 fixed kernels, PPV pooling, ridge. Nothing in the transform is
    learned and the ridge fit is closed form -- no optimiser, no seed."""

    name = 'MiniRocket'

    def __init__(self, dilations=(1, 2, 4, 8, 16), n_bias=2, seed=0,
                 multivariate=False, n_groups=5):
        self.dilations, self.n_bias, self.seed = list(dilations), n_bias, seed
        # multivariate=True sums a kernel's convolutions across a SUBSET of
        # channels before pooling, so one feature can require price and
        # volume to move together. Per-channel features cannot express that.
        self.multivariate, self.n_groups = multivariate, n_groups
        self.name = ('MiniRocketMV' if multivariate else 'MiniRocket') +             f'(d={len(self.dilations)},b={n_bias})'

    def _feats(self, x):
        if self.multivariate:
            return transform_mv(x, self.W, self.dilations, self.bias,
                                self.subs)
        return transform(x, self.W, self.dilations, self.bias)

    def fit(self, x, y, aux, keep=0.90):
        self.W = kernels()
        qs = np.linspace(0.0, 1.0, self.n_bias + 2)[1:-1].astype(np.float32)
        rng = np.random.default_rng(self.seed)
        rows = rng.choice(len(x), size=min(2000, len(x)), replace=False)
        if self.multivariate:
            self.subs = channel_subsets(x.shape[1], self.n_groups, self.seed)
            self.bias = fit_biases_mv(x, self.W, self.dilations, self.n_bias,
                                      rows, qs, self.subs)
        else:
            self.bias = fit_biases(x, self.W, self.dilations, self.n_bias,
                                   rows, qs)
        f = self._feats(x)
        self.fmu, self.fsd = f.mean(0), f.std(0) + 1e-8
        self.clf = RidgeClassifierCV(alphas=ALPHAS, class_weight='balanced')
        self.clf.fit((f - self.fmu) / self.fsd, aux.astype(np.int8))
        self.alpha_ = float(self.clf.alpha_)
        self._set_threshold(
            self.clf.decision_function((f - self.fmu) / self.fsd), keep)
        return self

    def score(self, x):
        f = self._feats(x)
        return self.clf.decision_function((f - self.fmu) / self.fsd)


class Ensemble(Filter):
    """Combine filters. mode='all' buys only what every member approves,
    'any' what at least one does, 'vote' what a majority does. Rank-average
    scoring is left for when a second scoring ensemble is actually wanted."""

    def __init__(self, members: list, mode: str = 'vote'):
        self.members, self.mode = members, mode
        self.name = f'Ensemble[{mode}]({"+".join(m.name for m in members)})'

    def fit(self, x, y, aux, keep=0.90):
        for m in self.members:
            m.fit(x, y, aux, keep)
        self.threshold = 0.0
        self.keep = keep
        return self

    def score(self, x):
        r = np.stack([np.argsort(np.argsort(m.score(x))) / max(1, len(x) - 1)
                      for m in self.members])
        return r.mean(0)

    def decide(self, x):
        votes = np.stack([m.decide(x) for m in self.members])
        if self.mode == 'all':
            return votes.all(0)
        if self.mode == 'any':
            return votes.any(0)
        return votes.sum(0) > len(self.members) / 2.0


REGISTRY = {'allpass': AllPass, 'shapelet': ShapeletFilter,
            'rocket': RocketFilter}
