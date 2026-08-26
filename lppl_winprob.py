"""Win-probability model (user request): train P(trade wins) on the
payoff pseudo-trade table's decision-time features.

Differs from the expected-win audit: the 0/1 label has bounded noise, so
win-rate gradients are better powered than return gradients — and P(win)
here is mechanically P(no -8% stop before the tc clock), which
volatility-like features could plausibly predict. Pre-registered trap:
E[ret|features] is already known to be flat OOS, so if P(win) IS
predictable, high-p trades must win SMALLER (the stop fixes the loss
side); the decile RETURN row decides usability, not AUC.

Protocol: ridge-logistic, 15 features standardized on dev, lambda by
mean walk-forward fold AUC (learning.penalty_folds), fit on full dev,
ONE test evaluation: AUC, win rate and mean return by predicted-p decile
(dev edges), calibration. Run: python lppl_winprob.py (needs
data/payoff_trades.parquet from lppl_payoff.py).
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from lppl_backtest import ROOT, load_config

FEATURES = ['votes', 'mean_r2', 'tc_runway', 'p_m', 'p_w', 'p_n', 'p_sigma',
            'osc_amp', 'damping', 'flag_age', 'persist_depth', 'dip_depth',
            'runup126', 'vol20', 'rel_dip']


def fit_logistic(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    s = 2.0 * y - 1.0

    def nll(b):
        z = np.clip(X @ b, -30, 30)
        return float(np.mean(np.log1p(np.exp(-s * z)))) + lam * float(b[1:] @ b[1:])

    return minimize(nll, np.zeros(X.shape[1]), method='L-BFGS-B').x


def auc(y: np.ndarray, score: np.ndarray) -> float:
    r = pd.Series(score).rank().to_numpy()
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def main() -> None:
    cfg = load_config()
    L = cfg['learning']
    bt = cfg['backtest']
    df = pd.read_parquet(ROOT / 'data' / 'payoff_trades.parquet') \
        .dropna(subset=FEATURES)
    dev = df[df['entry_date'] <= bt['dev_end']].reset_index(drop=True)
    test = df[df['entry_date'] >= bt['test_start']].reset_index(drop=True)
    print(f'dev {len(dev)} trades (win rate {(dev.ret_net > 0).mean():.1%}), '
          f'test {len(test)} (win rate {(test.ret_net > 0).mean():.1%})')

    mu, sd = dev[FEATURES].mean(), dev[FEATURES].std().replace(0, 1)

    def design(frame):
        Z = ((frame[FEATURES] - mu) / sd).to_numpy()
        return np.column_stack([np.ones(len(Z)), Z])

    Xd, yd = design(dev), (dev['ret_net'] > 0).to_numpy().astype(float)

    scores = {}
    for lam in L['ridge_penalties']:
        aa = []
        for fold in L['penalty_folds']:
            trn = (dev['entry_date'] <= fold['fit_end']).to_numpy()
            sco = ((dev['entry_date'] >= fold['score_start'])
                   & (dev['entry_date'] <= fold['score_end'])).to_numpy()
            if trn.sum() < 50 or sco.sum() < 50:
                continue
            b = fit_logistic(Xd[trn], yd[trn], lam)
            aa.append(auc(yd[sco], Xd[sco] @ b))
        scores[lam] = float(np.mean(aa)) if aa else np.nan
    lam = max(scores, key=lambda k: -np.inf if np.isnan(scores[k]) else scores[k])
    print(f'fold AUCs by lambda: { {k: round(v, 4) for k, v in scores.items()} } '
          f'-> chosen {lam}')

    b = fit_logistic(Xd, yd, lam)
    print('weights (per 1 sd):',
          {f: round(float(w), 3) for f, w in zip(FEATURES, b[1:])})

    pd_dev = 1 / (1 + np.exp(-Xd @ b))
    edges = np.unique(np.quantile(pd_dev, np.linspace(0, 1, 6)))
    edges[0], edges[-1] = -np.inf, np.inf
    for name, frame in [('dev (in-sample)', dev), ('test (single audit)', test)]:
        X = design(frame)
        p = 1 / (1 + np.exp(-X @ b))
        y = (frame['ret_net'] > 0).to_numpy().astype(float)
        q = pd.cut(p, edges, labels=False, include_lowest=True)
        g = frame.assign(p=p, q=q, win=y).groupby('q').agg(
            n=('win', 'size'), pred_p=('p', 'mean'), win_rate=('win', 'mean'),
            mean_ret=('ret_net', 'mean'), med_ret=('ret_net', 'median'),
            mean_win_size=('ret_net', lambda x: x[x > 0].mean()))
        print(f'\n[{name}] AUC {auc(y, p):.4f}')
        print(g.round(4).to_string())


if __name__ == '__main__':
    main()
