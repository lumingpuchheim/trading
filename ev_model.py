"""Estimate what a euro becomes, from the MiniRocket score + the three RS keys.

Inputs
    rocket   MiniRocket score, walk-forward so every value is out-of-sample
    rsl_hi   RS line at a 250-day high (0/1)
    weak     avg return on SPY down-days inside the base -- NaN ~95% of rows
    rs       126-day relative return
    weak_na  explicit missing indicator for `weak`

Target and loss
    ln(y). Squared error on the log, so against a break-even bet predicting
    2x costs (ln 2)^2 = 0.4805 and predicting 1/2x costs (ln 0.5)^2 = 0.4805
    -- the same factor of error is the same punishment either way.

NaN handling, per the literature searched 2026-08-29:
  * `HistGradientBoostingRegressor` supports NaN NATIVELY -- it learns, per
    split, which side missing rows go to. sklearn's own docs note this
    removes the need for imputation, and the benchmark work (arxiv
    2202.10580) finds native support beats imputation at lower cost.
  * AND a missing indicator is added anyway, because `weak` is missing NOT
    at random: it is undefined when the candidate has no identifiable base,
    or no SPY down-day inside it. That absence is a fact about the setup,
    not a gap in the data, and the literature is explicit that indicators
    should accompany any missing-value strategy when missingness is
    informative.

Protocol: fit on dev, evaluate ONCE on test. No simulation -- this measures
prediction quality only.

Usage
    python ev_model.py
"""

import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import RidgeClassifierCV

from lppl_backtest import ROOT, load_config
from minervini_backtest import apply_v5, build_panel
from bets_common import (AUX_Q, MIN_TRAIN, label_from, load,
                         warmup_rows, year_blocks)
from minervini_rocket import ALPHAS, fit_biases, kernels, transform

LEDGER = ROOT / 'results' / 'minervini_bets_v5r.csv'
WINDOWS = ROOT / 'results' / 'minervini_bets_v5r_windows.npz'


def main() -> None:
    av = sys.argv
    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    cfg = apply_v5(load_config())
    cfg['minervini_trading']['reentry_fast'] = True
    panel = build_panel(cfg, v5=True)
    rsl, wk, rsv = panel['rsl_hi'], panel['weak'], panel['rs']

    d = load(str(WINDOWS))
    led = pd.read_csv(LEDGER, parse_dates=['entry_date'])
    w = pd.DataFrame({'ticker': [str(t) for t in d['ticker']],
                      'entry_date': pd.to_datetime(d['entry_date']),
                      'wrow': np.arange(len(d['y']))})
    m = (w.merge(led[['ticker', 'entry_date', 'entry_i', 'ticker_j', 'y']],
                 on=['ticker', 'entry_date'], how='inner')
         .drop_duplicates('wrow').reset_index(drop=True))
    x = d['x'][m['wrow'].to_numpy()]
    y = m['y'].to_numpy(float)
    ei = m['entry_i'].to_numpy(np.int64)
    tj = m['ticker_j'].to_numpy(np.int64)
    date = m['entry_date'].to_numpy().astype('datetime64[D]')
    yr = m['entry_date'].dt.year.to_numpy()

    # --- walk-forward MiniRocket score: never scores a year it saw --------
    W = kernels(); dil = [1, 2, 4, 8, 16]
    qs = np.linspace(0.0, 1.0, 4)[1:-1].astype(np.float32)
    rg = np.random.default_rng(0)
    seed = warmup_rows(date, 2000, rg)
    feats = transform(x, W, dil, fit_biases(x, W, dil, 2, seed, qs))
    rocket = np.full(len(y), np.nan)
    for Y, trm, ev in year_blocks(date, exits):
        a_tr = label_from(y, trm)[trm]
        if len(set(a_tr.tolist())) < 2:
            continue
        mu, sd = feats[trm].mean(0), feats[trm].std(0) + 1e-8
        clf = RidgeClassifierCV(alphas=ALPHAS, class_weight='balanced')
        clf.fit((feats[trm] - mu) / sd, a_tr)
        rocket[ev] = clf.decision_function((feats[ev] - mu) / sd)
        print(f'  rocket {Y}: fit {int(trm.sum()):,}', flush=True)

    wv = wk[ei, tj]
    X = pd.DataFrame({'rocket': rocket,
                      'rsl_hi': rsl[ei, tj].astype(float),
                      'weak': wv,                       # NaN kept as NaN
                      'weak_na': (~np.isfinite(wv)).astype(float),
                      'rs': np.where(np.isfinite(rsv[ei, tj]),
                                     rsv[ei, tj], np.nan)})
    X['weak'] = X['weak'].where(np.isfinite(X['weak']))
    r = np.log(np.clip(y, 1e-6, None))

    ok = np.isfinite(rocket)
    print(f'weak is NaN on {X["weak_na"].mean():.1%} of rows '
          f'-- kept as NaN and flagged, not imputed')

    # Walk-forward over the whole record: each block predicted by a model
    # fitted only on what came before it, then pooled. `base` carries that
    # fold's own training mean, which is the constant the R2 is against --
    # so the comparison is never made against a future average.
    pred = np.full(len(y), np.nan)
    base = np.full(len(y), np.nan)
    gb = None
    for Y, trm, ev in year_blocks(date, exits):
        m_, e_ = trm & ok, ev & ok
        if m_.sum() < MIN_TRAIN or not e_.any():
            continue
        gb = HistGradientBoostingRegressor(
            loss='squared_error',        # on ln(y): 2x and 1/2x cost the same
            max_iter=400, learning_rate=0.05, max_leaf_nodes=15,
            min_samples_leaf=200, l2_regularization=1.0,
            early_stopping=True, validation_fraction=0.15, random_state=0)
        gb.fit(X[m_], r[m_])
        pred[e_] = gb.predict(X[e_])
        base[e_] = float(r[m_].mean())
        print(f'  gb {Y}: fit {int(m_.sum()):,}, '
              f'predicted {int(e_.sum()):,}', flush=True)

    te = np.isfinite(pred)
    pr, rt = pred[te], r[te]
    print(f'\nout of fold: {int(te.sum()):,} bets')

    ss_res = float(((rt - pr) ** 2).sum())
    ss_tot = float(((rt - base[te]) ** 2).sum())
    print(f'\n--- OUT OF FOLD, whole record, prediction quality on ln(y) ---')
    print(f'  R2 vs the train-mean constant : {1 - ss_res / ss_tot:+.5f}')
    print(f'  RMSE (log)                    : {np.sqrt(ss_res / len(rt)):.5f}')
    print(f'  RMSE of the constant          : {np.sqrt(ss_tot / len(rt)):.5f}')
    print(f'  spearman(pred, actual y)      : {spearmanr(pr, y[te]).statistic:+.4f}')
    print(f'  predicted spread: min {np.exp(pr.min()):.4f}  '
          f'max {np.exp(pr.max()):.4f}  sd {np.exp(pr).std():.4f}')

    q = np.clip((pd.Series(pr).rank(pct=True) * 10).astype(int), 0, 9)
    print(f'\n  decile of prediction (0 = worst predicted)')
    print(f'    {"dec":>3s} {"n":>6s} {"predicted y":>12s} {"actual y":>10s}')
    for b in range(10):
        s = q.to_numpy() == b
        print(f'    {b:3d} {int(s.sum()):6,d} {np.exp(pr[s]).mean():12.4f} '
              f'{y[te][s].mean():10.4f}')
    top, bot = y[te][q.to_numpy() == 9], y[te][q.to_numpy() == 0]
    print(f'    top decile minus bottom: {top.mean() - bot.mean():+.4f}   '
          f'pool {y[te].mean():.4f}')

    pi = permutation_importance(gb, X[te], rt, n_repeats=10, random_state=0,
                                scoring='neg_mean_squared_error')
    print(f'\n  permutation importance of the LAST fold\'s model, scored on '
          f'every out-of-fold row (drop in log-MSE when shuffled)')
    for k, name in sorted(zip(pi.importances_mean, X.columns), reverse=True):
        print(f'    {name:10s} {k:+.6f}')


if __name__ == '__main__':
    main()
