"""Section 5: the learning step. Fitted on the development period ONLY.

Run after screener.py:  python learn.py
Builds the per-base trade table (5a, cached to data/trade_table.parquet),
fits the ridge expected-return model (5b), produces the fit-quality tables
and shrink function (5c), and the Kelly table (5d). Frozen artifacts go to
results/weights.json, results/shrink.json, results/kelly_table.json.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent

# the nine ridge inputs (section 5b): seven measurements + market + interaction
FEATURES = ['lambda_range', 'lambda_vol', 'p_today_range', 'p_today_vol',
            'r2_range', 'r2_vol', 'base_len', 'lambda_market', 'lambda_x_r2']


def load_config() -> dict:
    with open(ROOT / 'config.yaml') as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- trading rule

def simulate_trade(opens: np.ndarray, closes: np.ndarray, close_f: np.ndarray,
                   sma50: np.ndarray, entry_i: int, cfg: dict) -> dict | None:
    """Apply the fixed exit rules (section 6) to a single entry at
    opens[entry_i]. Uses only data from entry_i onward. Returns None if there
    is no usable entry print. `close_f` is the forward-filled close, used for
    fills when a day has no open print and for data-end exits."""
    tr = cfg['trading']
    n = len(closes)
    if entry_i >= n or not np.isfinite(opens[entry_i]):
        return None
    entry_px = opens[entry_i]

    trigger, reason = None, 'end'
    for j in range(entry_i, n):
        c = closes[j]
        if np.isfinite(c) and c <= tr['stop_loss'] * entry_px:
            trigger, reason = j, 'stop'
        elif np.isfinite(c) and c < sma50[j]:
            trigger, reason = j, 'sma'
        elif j - entry_i >= tr['max_holding_days']:
            trigger, reason = j, 'time'
        if trigger is not None:
            break

    if trigger is None or trigger + 1 >= n:
        exit_i, exit_px = n - 1, close_f[n - 1]  # data ends: last known close
        if trigger is None:
            reason = 'end'
    else:
        exit_i = trigger + 1
        exit_px = opens[exit_i] if np.isfinite(opens[exit_i]) else close_f[exit_i]

    cost = tr['cost_per_side']
    ret = exit_px * (1 - cost) / (entry_px * (1 + cost)) - 1
    return {'entry_i': entry_i, 'entry_px': float(entry_px), 'exit_i': int(exit_i),
            'exit_px': float(exit_px), 'exit_reason': reason, 'ret_net': float(ret)}


def kelly_fraction(p: float, w: float, l: float) -> float:
    """Full Kelly fraction for a bet that wins +w with probability p and loses
    -l otherwise: f* = p/l - (1-p)/w, clipped at 0. w and l are positive."""
    if w <= 0 or l <= 0:
        return 0.0
    return max(p / l - (1 - p) / w, 0.0)


# ---------------------------------------------------------------- trade table

def bases_for_ticker(df: pd.DataFrame, sig: pd.DataFrame, cfg: dict) -> list[dict]:
    """Run the arming/breakout state machine for one ticker with no position
    limits or sizing: every base that breaks out becomes one trade row."""
    floor = cfg['base']['min_below_high']
    max_age = cfg['base']['max_length']
    pos = {d: i for i, d in enumerate(df.index)}
    sig_by_i = {pos[r['date']]: r for r in sig.to_dict('records') if r['date'] in pos}
    if not sig_by_i:
        return []
    opens = df['open'].to_numpy()
    closes = df['close'].to_numpy()
    close_f = df['close'].ffill().to_numpy()
    sma50 = df['sma_fast'].to_numpy()

    out, armed = [], None
    for i in range(min(sig_by_i), len(df)):
        if armed is not None:
            c = closes[i]
            if i - armed['_i'] > max_age or (np.isfinite(c) and c < floor * armed['base_top']):
                armed = None
            elif np.isfinite(c) and i > armed['_i'] and c > armed['base_top']:
                trade = simulate_trade(opens, closes, close_f, sma50, i + 1, cfg)
                if trade is not None:
                    row = {k: v for k, v in armed.items() if k != '_i'}
                    row.update(trade,
                               entry_date=df.index[trade['entry_i']],
                               exit_date=df.index[trade['exit_i']],
                               days_held=trade['exit_i'] - trade['entry_i'])
                    out.append(row)
                armed = None
        if i in sig_by_i:
            armed = dict(sig_by_i[i])
            armed['_i'] = i
    return out


def build_trade_table(cfg: dict) -> pd.DataFrame:
    from screener import add_indicators  # deferred: keeps import cheap for tests

    cache = ROOT / cfg['data']['cache_dir'] / 'trade_table.parquet'
    if cache.exists():
        print(f'trade table cache found: {cache}')
        return pd.read_parquet(cache)

    signals = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'signals.parquet')
    signals = signals[signals['sanity_pass']]
    ohlcv_dir = ROOT / cfg['data']['cache_dir'] / 'ohlcv'

    rows = []
    tickers = sorted(signals['ticker'].unique())
    for n, t in enumerate(tickers, 1):
        df = add_indicators(pd.read_parquet(ohlcv_dir / f'{t}.parquet'), cfg)
        for row in bases_for_ticker(df, signals[signals['ticker'] == t], cfg):
            row['ticker'] = t
            rows.append(row)
        if n % 200 == 0:
            print(f'  {n}/{len(tickers)} tickers traded')

    table = pd.DataFrame(rows)
    table['ret_capped'] = table['ret_net'].clip(upper=cfg['learning']['return_cap'])
    table['r2'] = (table['r2_range'] + table['r2_vol']) / 2
    table['lambda_x_r2'] = table['lambda'] * table['r2']
    table.to_parquet(cache)
    print(f'{len(table)} base trades cached -> {cache}')
    return table


# ---------------------------------------------------------------- ridge (5b)

def fit_ridge(df: pd.DataFrame, features: list[str], alpha: float) -> dict:
    X = df[features].to_numpy(dtype=float)
    y = df['ret_capped'].to_numpy(dtype=float)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    Z = np.column_stack([np.ones(len(X)), (X - mu) / sd])
    penalty = alpha * np.eye(Z.shape[1])
    penalty[0, 0] = 0.0  # intercept unpenalised
    w = np.linalg.solve(Z.T @ Z + penalty, Z.T @ y)
    return {'features': features, 'mu': mu.tolist(), 'sd': sd.tolist(),
            'intercept': float(w[0]), 'weights': w[1:].tolist(), 'alpha': alpha}


def predict(model: dict, df: pd.DataFrame) -> np.ndarray:
    X = df[model['features']].to_numpy(dtype=float)
    Z = (X - np.array(model['mu'])) / np.array(model['sd'])
    return model['intercept'] + Z @ np.array(model['weights'])


def select_penalty(dev: pd.DataFrame, features: list[str], cfg: dict) -> tuple[float, dict]:
    """Walk-forward inside the development period only: score each penalty by
    the average realised (capped) return of the top `top_fraction` of bases
    ranked by prediction, averaged over the folds."""
    lc = cfg['learning']
    scores = {}
    for alpha in lc['ridge_penalties']:
        fold_scores = []
        for fold in lc['penalty_folds']:
            fit = dev[dev['entry_date'] <= fold['fit_end']]
            score = dev[(dev['entry_date'] >= fold['score_start'])
                        & (dev['entry_date'] <= fold['score_end'])]
            if len(fit) < 50 or len(score) < 20:
                continue
            pred = predict(fit_ridge(fit, features, alpha), score)
            k = max(1, int(len(score) * lc['top_fraction']))
            top = score['ret_capped'].to_numpy()[np.argsort(-pred)[:k]]
            fold_scores.append(float(top.mean()))
        scores[alpha] = float(np.mean(fold_scores)) if fold_scores else np.nan
    best = max(scores, key=lambda a: -np.inf if np.isnan(scores[a]) else scores[a])
    return best, scores


# ------------------------------------------------------- 5c tables and shrink

def bucket_of(values: np.ndarray, inner_edges: list[float]) -> np.ndarray:
    return np.searchsorted(np.asarray(inner_edges), values)


def fivec_tables(df: pd.DataFrame, r2_edges: list[float],
                 pred_edges: list[float]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """The three tables of section 5c for any set of trade rows that already
    has `predicted`. Bucket edges are passed in (frozen from development)."""
    d = df.copy()
    d['r2_bucket'] = bucket_of(d['r2'].to_numpy(), r2_edges)

    rows1, rows2, rows3 = [], [], []
    for b, g in d.groupby('r2_bucket'):
        r = g['ret_capped']
        wins, losses = r[r > 0], r[r <= 0]
        rows1.append({'r2_bucket': b, 'count': len(g), 'avg_return': r.mean(),
                      'win_rate': len(wins) / len(r),
                      'avg_winner': wins.mean() if len(wins) else np.nan,
                      'avg_loser': losses.mean() if len(losses) else np.nan})
        med = g['lambda'].median()
        hi, lo = g[g['lambda'] > med], g[g['lambda'] <= med]
        gap = (hi['ret_capped'].mean() if len(hi) else np.nan) \
            - (lo['ret_capped'].mean() if len(lo) else np.nan)
        rows2.append({'r2_bucket': b, 'count': len(g),
                      'high_lambda_avg': hi['ret_capped'].mean() if len(hi) else np.nan,
                      'low_lambda_avg': lo['ret_capped'].mean() if len(lo) else np.nan,
                      'lambda_gap': gap})
        g = g.copy()
        g['pred_bucket'] = bucket_of(g['predicted'].to_numpy(), pred_edges)
        for pb, gg in g.groupby('pred_bucket'):
            rows3.append({'r2_bucket': b, 'pred_quintile': pb, 'count': len(gg),
                          'predicted_avg': gg['predicted'].mean(),
                          'realised_avg': gg['ret_capped'].mean()})
    return pd.DataFrame(rows1), pd.DataFrame(rows2), pd.DataFrame(rows3)


def shrink_from_gaps(table2: pd.DataFrame, n_buckets: int) -> tuple[list[float], str]:
    gaps = [float(table2.set_index('r2_bucket')['lambda_gap'].get(b, np.nan))
            for b in range(n_buckets)]
    max_gap = np.nanmax(gaps)
    if not np.isfinite(max_gap) or max_gap <= 0:
        note = ('WARNING: no r2 bucket shows a positive high-vs-low lambda gap; '
                'shrink set flat at 1 (fit quality carries no signal here)')
        return [1.0] * n_buckets, note
    vals = [float(np.clip(g / max_gap, 0.0, 1.0)) if np.isfinite(g) else 0.0
            for g in gaps]
    return vals, ''


# --------------------------------------------------- frozen-artifact application

def apply_artifacts(df: pd.DataFrame, weights: dict, shrink: dict,
                    kelly_table: dict) -> pd.DataFrame:
    """Add predicted / shrink / edge / quarter_kelly columns to any frame that
    has the nine feature columns. Used on dev, test, and live signals alike —
    the artifacts are frozen, this only applies them."""
    d = df.copy()
    if 'r2' not in d:
        d['r2'] = (d['r2_range'] + d['r2_vol']) / 2
    if 'lambda_x_r2' not in d:
        d['lambda_x_r2'] = d['lambda'] * d['r2']
    d['predicted'] = predict(weights['nine_input'], d)
    shrink_vals = np.array(shrink['values'])
    d['shrink'] = shrink_vals[bucket_of(d['r2'].to_numpy(), shrink['r2_edges'])]
    d['edge'] = d['predicted'] * d['shrink']
    quarter = np.array([b['quarter_kelly'] for b in kelly_table['buckets']])
    d['quarter_kelly'] = quarter[bucket_of(d['edge'].to_numpy(), kelly_table['edge_edges'])]
    return d


# ------------------------------------------------------------------------ main

def main() -> None:
    cfg = load_config()
    lc = cfg['learning']
    results = ROOT / cfg['backtest']['results_dir']
    results.mkdir(exist_ok=True)

    table = build_trade_table(cfg)
    table['entry_date'] = pd.to_datetime(table['entry_date'])

    n_nan = int(table[FEATURES].isna().any(axis=1).sum())
    if n_nan:
        print(f'dropping {n_nan} trades with missing features (of {len(table)})')
    table = table.dropna(subset=FEATURES)

    dev = table[(table['entry_date'] >= cfg['backtest']['start'])
                & (table['entry_date'] <= cfg['backtest']['dev_end'])]
    print(f'{len(dev)} development-period base trades '
          f'({table["entry_date"].min().date()} first entry overall)')

    # 5b: nine-input ridge with walk-forward penalty, then the one-input version
    alpha, scores = select_penalty(dev, FEATURES, cfg)
    nine = fit_ridge(dev, FEATURES, alpha)
    alpha1, _ = select_penalty(dev, ['lambda'], cfg)
    one = fit_ridge(dev, ['lambda'], alpha1)

    print(f'\nridge penalty chosen: {alpha}  (fold scores: '
          + ', '.join(f'{a}: {s:.4f}' for a, s in scores.items()) + ')')
    print('nine-input weights (standardised inputs):')
    for f, w in zip(FEATURES, nine['weights']):
        print(f'  {f:15s} {w:+.5f}')
    print(f'  intercept       {nine["intercept"]:+.5f}')
    print(f'one-input (lambda) weight: {one["weights"][0]:+.5f}, '
          f'intercept {one["intercept"]:+.5f}, penalty {alpha1}')

    dev = dev.copy()
    dev['predicted'] = predict(nine, dev)

    # 5c: bucket edges frozen from development quantiles
    qs = np.linspace(0, 1, lc['n_r2_buckets'] + 1)[1:-1]
    r2_edges = np.quantile(dev['r2'], qs).tolist()
    pred_edges = np.quantile(dev['predicted'], qs).tolist()
    t1, t2, t3 = fivec_tables(dev, r2_edges, pred_edges)
    t1.to_csv(results / 'dev_5c_table1.csv', index=False)
    t2.to_csv(results / 'dev_5c_table2.csv', index=False)
    t3.to_csv(results / 'dev_5c_table3.csv', index=False)
    fmt = lambda x: f'{x:.4f}'
    print('\n5c table 1 — return by r2 bucket (dev):')
    print(t1.to_string(index=False, float_format=fmt))
    print('\n5c table 2 — lambda gap inside each r2 bucket (dev):')
    print(t2.to_string(index=False, float_format=fmt))

    shrink_vals, note = shrink_from_gaps(t2, lc['n_r2_buckets'])
    if note:
        print(f'\n{note}')
    shrink = {'r2_edges': r2_edges, 'values': shrink_vals, 'note': note}
    print(f'shrink(r2) over buckets: {[f"{v:.2f}" for v in shrink_vals]}')

    # 5d: Kelly table over edge buckets
    dev['shrink'] = np.array(shrink_vals)[bucket_of(dev['r2'].to_numpy(), r2_edges)]
    dev['edge'] = dev['predicted'] * dev['shrink']
    edge_edges = np.quantile(dev['edge'], qs).tolist()
    buckets = []
    for b in range(lc['n_edge_buckets']):
        g = dev[bucket_of(dev['edge'].to_numpy(), edge_edges) == b]
        r = g['ret_capped']
        wins, losses = r[r > 0], r[r <= 0]
        p = len(wins) / len(r) if len(r) else 0.0
        w = float(wins.mean()) if len(wins) else 0.0
        l = float(-losses.mean()) if len(losses) else 0.0
        k = kelly_fraction(p, w, l)
        buckets.append({'bucket': b, 'count': len(g), 'p': p, 'W': w, 'L': l,
                        'kelly': k, 'quarter_kelly': lc['kelly_multiplier'] * k})
    kelly_table = {'edge_edges': edge_edges, 'buckets': buckets}
    print('\nKelly table (dev edge buckets):')
    print(pd.DataFrame(buckets).to_string(index=False, float_format=fmt))

    weights = {
        'nine_input': nine, 'one_input': one, 'penalty_scores': scores,
        'pred_edges': pred_edges,
        'lambda_top_threshold': float(np.quantile(dev['lambda'],
                                                  1 - lc['lambda_top_fraction'])),
    }
    for name, obj in [('weights.json', weights), ('shrink.json', shrink),
                      ('kelly_table.json', kelly_table)]:
        with open(results / name, 'w') as f:
            json.dump(obj, f, indent=2)
    print(f'\nfrozen artifacts written to {results}/ '
          '(weights.json, shrink.json, kelly_table.json)')


if __name__ == '__main__':
    main()
