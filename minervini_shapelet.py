"""Learned shapelets on the v5r bet windows: the "expert glances at the
chart" model, made literal.

A shapelet is a short curve. The only feature the model computes is, for
each shapelet, HOW CLOSELY THE CHART MATCHES IT SOMEWHERE -- the smallest
distance between the shapelet and any window of the series. A linear layer
turns those K distances into one score. That is the whole model.

    8 shapelets x 30 days x 1 channel + 9 = 249 parameters

against the CNN's 3,010. And unlike the CNN, the parameters ARE the
answer: `--plot` draws the learned shapelets, so you can put them beside a
chart and see what the model looks for, or see that it is nothing.

Distance is computed by convolution rather than by sliding a window:
    ||x_w - s||^2 = ||x_w||^2 - 2 <x_w, s> + ||s||^2
the first term a box filter over x^2, the second a conv1d against the
shapelets. The hard `min` over positions is replaced by a soft-min at
temperature T so it stays differentiable (Grabocka et al. 2014).

Shapelets are initialised FROM REAL TRAINING WINDOWS, not from noise: a
random curve is nowhere near any chart, so every distance saturates and
no gradient flows.

Label, folds, embargo and metrics are the CNN's, so the output lines are
comparable to minervini_rocket.py line for line.

Usage
    python minervini_shapelet.py --data results/..._f16.npz
    python minervini_shapelet.py --k 8 --len 30 --channels 0
    python minervini_shapelet.py --channels 0,2      # price and volume
    python minervini_shapelet.py --shuffle           # control
    python minervini_shapelet.py --plot results/shapelets.png
    python minervini_shapelet.py --save DIR
"""

import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.metrics import roc_auc_score

from bets_common import (AUX_Q, DEV, folds, jackpot_loss, line,
                           load, report)


def auto_gamma(y_tr: np.ndarray, rho: float, q: float = 0.05) -> float:
    """Choose the false-jackpot penalty from the data instead of by taste.

    The negative weight is 1 + gamma * max(0, 1-y): a bet that lost 15
    cents is punished by those 15 cents, one that merely underperformed by
    nothing. What gamma sets is the EXCHANGE RATE between the two mistakes
    the model can make -- calling a loser a jackpot, and missing a real
    one. Missing one already costs rho (the class-balance weight, ~4).

    So: fix gamma such that a BAD false positive -- the 5th percentile of
    losing bets, around y = 0.85 -- costs the same as a missed jackpot.

        1 + gamma * (1 - y_q) = rho     =>     gamma = (rho - 1) / (1 - y_q)

    With rho ~ 4 and y_q ~ 0.85 that lands near 20. Below it, mild misses
    stay nearly free, which is right: they did not lose money. Recomputed
    per fold from the training rows only, so it adapts if the mix moves
    and never reads the validation block.
    """
    lose = y_tr[y_tr < 1.0]
    if len(lose) < 100:
        return 10.0
    y_q = float(np.quantile(lose, q))
    return float(np.clip((rho - 1.0) / max(1.0 - y_q, 1e-3), 1.0, 100.0))


def soft_min_dist(x: torch.Tensor, S: torch.Tensor, T: float) -> torch.Tensor:
    """x (N,C,Ls), S (K,C,L) -> (N,K) soft-min squared distance per shapelet."""
    C, L = S.shape[1], S.shape[2]
    ones = torch.ones(1, C, L, device=x.device, dtype=x.dtype)
    xx = F.conv1d(x * x, ones)                       # (N,1,P)  ||x_w||^2
    xs = F.conv1d(x, S)                              # (N,K,P)  <x_w, s>
    ss = (S * S).sum(dim=(1, 2)).view(1, -1, 1)      # (1,K,1)  ||s||^2
    d = torch.clamp((xx - 2 * xs + ss) / (C * L), min=0.0)
    return (d * torch.softmax(-d / T, dim=-1)).sum(-1)


class ShapeletNet(nn.Module):
    def __init__(self, K: int, L: int, C: int, T: float = 0.10):
        super().__init__()
        self.S = nn.Parameter(torch.randn(K, C, L) * 0.1)
        self.lin = nn.Linear(K, 1)
        self.T = T

    def forward(self, x):
        return self.lin(soft_min_dist(x, self.S, self.T)).squeeze(-1)

    @torch.no_grad()
    def seed_from(self, x: torch.Tensor, rng: np.random.Generator):
        """Initialise each shapelet from a random window of a random series.
        Noise initialisation leaves every distance saturated and dead."""
        K, _, L = self.S.shape
        n, _, Ls = x.shape
        for k in range(K):
            i = int(rng.integers(n))
            t = int(rng.integers(Ls - L))
            self.S[k] = x[i, :, t:t + L]


def fbeta_loss(logit, y, thresh: float = 1.05, beta: float = 1.0,
               eps: float = 1e-8):
    """Reward ONLY a correctly predicted >5% winner (user's design,
    2026-08-29).

        a  = 1[y > thresh]            the thing worth predicting
        p  = sigmoid(logit)           how strongly the model claims it
        TP = sum p*a                  claimed it and was right   -> REWARD
        FP = sum p*(1-a)              claimed it and was wrong   -> cost
        FN = sum (1-p)*a              missed a real one          -> cost

        L = 1 - (1+b^2)*TP / ((1+b^2)*TP + b^2*FN + FP)

    True negatives appear NOWHERE. Correctly declining a bad trade earns
    the model nothing, which is the whole point: credit is only for
    calling a winner and being right.

    beta trades the two errors: beta<1 makes a false positive relatively
    worse (be sure before you claim), beta>1 makes a miss worse. beta=1
    weights them equally, which is what "both are bad" says.

    Unlike BCE this is a SET-level objective -- computed over the batch,
    not summed per example -- so nothing pushes probabilities toward
    calibration. It only cares which side of the batch each bet lands on,
    which is what a filter is judged on. With batch 512 and ~20% positives
    each batch carries ~100 positives, enough for a stable estimate.
    """
    a = (y > thresh).float()
    p = torch.sigmoid(logit)
    tp = (p * a).sum()
    fp = (p * (1.0 - a)).sum()
    fn = ((1.0 - p) * a).sum()
    b2 = beta * beta
    return 1.0 - (1.0 + b2) * tp / ((1.0 + b2) * tp + b2 * fn + fp + eps)


def log_value_loss(u, y):
    """Squared error between the predicted and realised LOG multiple
    (user's design, 2026-08-28).

    The network's output IS ln(y_hat), so y_hat = exp(u) and the loss is

        L = mean( (u - ln y)^2 )

    Symmetric in the multiplicative sense: against a break-even bet,
    predicting 2x costs (ln 2)^2 = 0.480 and predicting 1/2x costs
    (ln 0.5)^2 = 0.480. The same factor of error is the same punishment
    whichever side it falls on -- which is the property the cost-sensitive
    BCE did not have, and why that one taught the model to keep quiet
    rather than to choose.

    It also drops three arbitrary things: the top-20% cut, gamma, and rho.
    The model estimates what a euro becomes; the decision threshold is
    applied afterwards, separately, so estimating and deciding stop
    contaminating each other.
    """
    return F.mse_loss(u, torch.log(torch.clamp(y, min=1e-6)))


def fit(xtr, atr, ytr, xva, ava, seed, K, L, temp, epochs, patience,
        gamma, lr, wd, loss: str = 'class', yva=None,
        fb_thresh: float = 1.05, fb_beta: float = 1.0) -> ShapeletNet:
    """loss='class': cost-sensitive BCE, early stop on validation AUC.
       loss='value': symmetric log error, early stop on validation log MSE.
       loss='f1'   : soft F-beta on 'earns more than fb_thresh', early stop
                     on the same quantity measured on validation."""
    torch.manual_seed(seed)
    net = ShapeletNet(K, L, xtr.shape[1], temp).to(DEV)
    net.seed_from(xtr, np.random.default_rng(seed))
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=wd)
    rho = float((atr < 0.5).sum()) / max(1.0, float((atr >= 0.5).sum()))
    ava_np = ava.cpu().numpy()
    scorable = 0 < ava_np.sum() < len(ava_np)
    best, best_state, bad = -np.inf, None, 0
    n = len(xtr)
    for _ in range(epochs):
        net.train()
        for b in torch.randperm(n, device=DEV).split(512):
            opt.zero_grad()
            out = net(xtr[b])
            if loss == 'value':
                lo = log_value_loss(out, ytr[b])
            elif loss == 'f1':
                lo = fbeta_loss(out, ytr[b], fb_thresh, fb_beta)
            else:
                lo = jackpot_loss(out, atr[b], ytr[b], gamma, rho)
            lo.backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            sv = torch.cat([net(c) for c in xva.split(4096)]).cpu().numpy()
        if loss == 'value':
            # lower log-MSE is better, so negate to keep one comparison
            lv = np.log(np.clip(yva.cpu().numpy(), 1e-6, None))
            metric = -float(np.mean((sv - lv) ** 2))
        elif loss == 'f1':
            # early stop on the SAME quantity being trained, not on AUC
            with torch.no_grad():
                metric = -float(fbeta_loss(torch.from_numpy(sv).to(DEV),
                                           yva, fb_thresh, fb_beta))
        else:
            metric = roc_auc_score(ava_np, sv) if scorable else 0.5
        if metric > best + 1e-9:
            best, bad = metric, 0
            best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
    if best_state is not None:
        net.load_state_dict(best_state)
    net.eval()
    return net


def plot(S: np.ndarray, chans: list, names: list, path: str) -> None:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    K = S.shape[0]
    fig, axes = plt.subplots(1, K, figsize=(2.0 * K, 2.4), sharey=True)
    for k, ax in enumerate(np.atleast_1d(axes)):
        for j, c in enumerate(chans):
            ax.plot(S[k, j], lw=1.6, label=names[c] if k == 0 else None)
        ax.set_title(f'#{k}', fontsize=9)
        ax.tick_params(labelsize=7)
    if len(chans) > 1:
        fig.legend(loc='lower center', ncol=len(chans), fontsize=7)
    fig.suptitle('learned shapelets (standardised units, x = days)',
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    print(f'  shapelets drawn -> {path}')


def main() -> None:
    av = sys.argv
    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    d = load(opt('--data', None))
    y, date = d['y'], d['date']
    names = [str(c) for c in d['channels']]
    chans = [int(c) for c in opt('--channels', '0').split(',')]
    x = d['x'][:, chans, :]
    if '--shuffle' in av:
        y = y[np.random.default_rng(0).permutation(len(y))]
        print('LABEL-SHUFFLE CONTROL: lift ~0, keep1% ~10%, auc ~0.50 expected')

    K = opt('--k', 8, int)
    L = opt('--len', 30, int)
    temp = opt('--temp', 0.10, float)
    gamma_arg = opt('--gamma', 'auto')     # 'auto' derives it per fold
    lossmode = opt('--loss', 'class')      # 'value' = symmetric log error
    seeds = list(range(opt('--seeds', 3, int)))
    # no embargo constant: a training bet is kept if it CLOSED before
    # the validation block opened (bets_common._purge)
    thr = float(np.quantile(y, AUX_Q))    # for the banner only; the label
    aux = np.zeros(len(y), np.float32)    # is cut per fold, in the loop
    npar = K * len(chans) * L + K + 1
    print(f'shapelets: {K} x {L} days x {len(chans)} channel(s) '
          f'{[names[c] for c in chans]} = {npar:,} parameters, '
          f'temp {temp}, loss {lossmode}, gamma {gamma_arg}, '
          f'label ~y>={thr:.4f}, cut per fold', flush=True)

    rows, last = [], None
    for tr, va, v0, v1 in folds(date, opt('--folds', 4, int), d['exit']):
        # the label, cut at AUX_Q of THIS fold's training rows
        aux = (y >= float(np.quantile(y[tr], AUX_Q))).astype(np.float32)
        mu = x[tr].mean(axis=(0, 2), keepdims=True)
        sd = x[tr].std(axis=(0, 2), keepdims=True) + 1e-6
        t = lambda m: torch.from_numpy((x[m] - mu) / sd).to(DEV)
        xtr, xva = t(tr), t(va)
        atr = torch.from_numpy(aux[tr]).to(DEV)
        ava = torch.from_numpy(aux[va]).to(DEV)
        ytr = torch.from_numpy(y[tr].astype(np.float32)).to(DEV)
        rho_tr = float((aux[tr] < 0.5).sum()) / max(1.0, float((aux[tr] >= 0.5).sum()))
        gamma = (auto_gamma(y[tr], rho_tr) if gamma_arg == 'auto'
                 else float(gamma_arg))
        if gamma_arg == 'auto' and lossmode != 'value':
            lose = y[tr][y[tr] < 1.0]
            print(f'  gamma {gamma:.1f}  (rho {rho_tr:.2f}, 5th pct of '
                  f'losers y={np.quantile(lose, 0.05):.3f}, so the worst '
                  f'false jackpots cost about as much as a missed one)',
                  flush=True)
        yva_t = torch.from_numpy(y[va].astype(np.float32)).to(DEV)
        nets = [fit(xtr, atr, ytr, xva, ava, s, K, L, temp,
                    opt('--epochs', 60, int), opt('--patience', 8, int),
                    gamma, opt('--lr', 3e-3, float), opt('--wd', 1e-3, float),
                    loss=lossmode, yva=yva_t)
                for s in seeds]
        with torch.no_grad():
            sc = np.mean([torch.cat([n(c) for c in xva.split(4096)]).cpu().numpy()
                          for n in nets], axis=0)
        m = report(sc, y[va])
        rows.append(m)
        last = nets[0]
        print(line(f'  val {v0[:7]}..{v1[:7]}', m), flush=True)
        del xtr, xva

    agg = {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}
    agg['n'] = int(np.sum([r['n'] for r in rows]))
    print(line('  VAL MEAN', agg), flush=True)

    S = last.S.detach().cpu().numpy()
    if '--plot' in av:
        plot(S, list(range(len(chans))), [names[c] for c in chans],
             opt('--plot', 'shapelets.png'))
    if '--save' in av:
        out = Path(opt('--save', '.')); out.mkdir(parents=True, exist_ok=True)
        f = out / f'shapelet_k{K}_l{L}.pt'
        torch.save({'shapelets': S, 'channels': [names[c] for c in chans],
                    'K': K, 'L': L, 'temp': temp, 'gamma': gamma, 'gamma_mode': gamma_arg,
                    'label_threshold': float(thr), 'val_mean': agg,
                    'lin': last.lin.state_dict()}, f)
        print(f'  saved -> {f}')


if __name__ == '__main__':
    main()
