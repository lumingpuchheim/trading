"""Sections 6-7: fixed trading rule, five-strategy walk-forward backtest.

Run after learn.py:  python backtest.py
Applies the frozen artifacts (weights, shrink, Kelly table) to all signals,
simulates the five comparison strategies over the development and test
periods, and writes tables to results/ plus one equity chart per period.

Mechanics (all decisions on close, all fills at the next day's open):
- a signal on day d arms the ticker with that day's base_top; the first later
  day whose close > base_top triggers a buy at the next open. An armed signal
  is dropped if close falls below 0.70 x base_top or after 90 trading days
  (the maximum base length) without a refresh.
- exits follow learn.simulate_trade — the same code that built the learning
  targets — so backtested trades and the trade table can never disagree.
- position size is a fraction of total portfolio value at the previous close
  (the value known when the entry was scheduled); an entry whose cost exceeds
  available cash would breach the 100% exposure cap and is skipped.
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from learn import FEATURES, apply_artifacts, fivec_tables, simulate_trade

ROOT = Path(__file__).parent
START_EQUITY = 100_000.0

STRATEGIES = ['model_kelly', 'model_ew', 'lambda_ew', 'template', 'random']


def load_config() -> dict:
    with open(ROOT / 'config.yaml') as f:
        return yaml.safe_load(f)


def load_panel(cfg: dict) -> dict:
    """Per-ticker numpy arrays aligned to the SPY calendar."""
    from screener import add_indicators

    data_dir = ROOT / cfg['data']['cache_dir']
    spy = pd.read_parquet(data_dir / 'ohlcv' / f"{cfg['data']['benchmark']}.parquet")
    calendar = spy.index

    arrays = {}
    for path in sorted((data_dir / 'ohlcv').glob('*.parquet')):
        t = path.stem
        if t == cfg['data']['benchmark']:
            continue
        df = add_indicators(pd.read_parquet(path), cfg).reindex(calendar)
        arrays[t] = {
            'open': df['open'].to_numpy(),
            'close': df['close'].to_numpy(),
            'close_f': df['close'].ffill().to_numpy(),
            'sma50': df['sma_fast'].to_numpy(),
            'qualify': df['qualify'].fillna(False).to_numpy(dtype=bool),
        }
    return {'calendar': calendar, 'arrays': arrays, 'spy_close': spy['close']}


def strategy_signals(signals: pd.DataFrame, strategy: str, weights: dict) -> pd.DataFrame:
    if strategy in ('model_kelly', 'model_ew'):
        return signals[signals['sanity_pass']]
    if strategy == 'lambda_ew':
        return signals[signals['sanity_pass']
                       & (signals['lambda'] >= weights['lambda_top_threshold'])]
    if strategy == 'template':
        return signals
    return signals.iloc[0:0]  # random: no base signals at all


def simulate(panel: dict, signals: pd.DataFrame, cfg: dict, strategy: str,
             period: tuple[str, str], seed: int) -> tuple[pd.DataFrame, pd.Series, float]:
    """Walk one period day by day. Returns (trades, equity, avg_invested)."""
    tr = cfg['trading']
    max_age = cfg['base']['max_length']
    floor = cfg['base']['min_below_high']
    rng = np.random.default_rng(seed)
    arrays = panel['arrays']

    cal = panel['calendar']
    day_pos = {d: i for i, d in enumerate(cal)}
    days = cal[(cal >= period[0]) & (cal <= period[1])]

    sig_by_day: dict = {}
    for d, g in signals.groupby('date'):
        sig_by_day[d] = list(zip(g['ticker'], g['base_top'], g['lambda'],
                                 g['edge'], g['quarter_kelly']))

    cash = START_EQUITY
    eq_prev = START_EQUITY
    positions: dict[str, dict] = {}
    armed: dict[str, dict] = {}
    cooldown: dict[str, int] = {}
    pending: list[dict] = []
    trades: list[dict] = []
    equity = pd.Series(np.nan, index=days)
    invested_frac: list[float] = []

    def record(t, pos, exit_px, exit_i, reason, d_exit):
        cost = tr['cost_per_side']
        trades.append({
            'ticker': t, 'entry_date': pos['entry_date'], 'exit_date': d_exit,
            'entry_px': pos['entry_px'], 'exit_px': exit_px,
            'days_held': exit_i - pos['entry_i'],
            'ret_net': exit_px * (1 - cost) / (pos['entry_px'] * (1 + cost)) - 1,
            'exit_reason': reason, 'lambda': pos['lam'], 'edge': pos['edge'],
        })

    for d in days:
        i = day_pos[d]

        # 1. fills at today's open: scheduled exits first, then entries
        for t in [t for t, p in positions.items() if p['exit_i'] <= i]:
            pos = positions.pop(t)
            cash += pos['shares'] * pos['exit_px'] * (1 - tr['cost_per_side'])
            record(t, pos, pos['exit_px'], i, pos['exit_reason'], d)
            cooldown[t] = i + tr['reentry_cooldown']

        for e in pending:
            t = e['ticker']
            if t in positions or len(positions) >= tr['max_positions']:
                continue
            plan = simulate_trade(arrays[t]['open'], arrays[t]['close'],
                                  arrays[t]['close_f'], arrays[t]['sma50'], i, cfg)
            if plan is None:
                continue  # no open print today: entry lapses
            invest = e['fraction'] * eq_prev
            exposure = (eq_prev - cash + invest) / eq_prev
            if invest <= 0 or exposure > tr['max_total_exposure'] + 1e-9:
                continue  # would breach the total exposure cap: skip
            shares = invest / (plan['entry_px'] * (1 + tr['cost_per_side']))
            cash -= invest
            positions[t] = {'shares': shares, 'entry_px': plan['entry_px'],
                            'entry_date': d, 'entry_i': i, 'lam': e['lam'],
                            'edge': e['edge'], 'exit_i': plan['exit_i'],
                            'exit_px': plan['exit_px'],
                            'exit_reason': plan['exit_reason']}
        pending = []

        # 2. decisions at today's close: breakout candidates for tomorrow
        exiting = sum(1 for p in positions.values() if p['exit_i'] <= i + 1)
        slots = tr['max_positions'] - (len(positions) - exiting)

        if strategy == 'random':
            cands = [t for t, a in arrays.items()
                     if a['qualify'][i] and t not in positions
                     and cooldown.get(t, -1) <= i]
            picked = list(rng.choice(cands, size=min(max(slots, 0), len(cands)),
                                     replace=False)) if slots > 0 and cands else []
            ranked = [{'ticker': t, 'lam': np.nan, 'edge': np.nan,
                       'fraction': tr['equal_weight_fraction']} for t in picked]
        else:
            breakouts = []
            for t, a in list(armed.items()):
                c = arrays[t]['close'][i]
                if i - a['armed_i'] > max_age or (np.isfinite(c) and c < floor * a['base_top']):
                    del armed[t]
                    continue
                if np.isfinite(c) and a['armed_i'] < i and c > a['base_top'] \
                        and t not in positions and cooldown.get(t, -1) <= i:
                    breakouts.append({'ticker': t, 'lam': a['lam'], 'edge': a['edge'],
                                      'quarter_kelly': a['quarter_kelly']})
            if strategy == 'model_kelly':
                # a base whose Kelly fraction is zero is not bought
                breakouts = [b for b in breakouts if b['quarter_kelly'] > 0]
                breakouts.sort(key=lambda b: -b['edge'])
            elif strategy == 'model_ew':
                breakouts.sort(key=lambda b: -b['edge'])
            elif strategy == 'lambda_ew':
                breakouts.sort(key=lambda b: -b['lam'])
            else:
                rng.shuffle(breakouts)
            ranked = []
            for b in breakouts[:max(slots, 0)]:
                if strategy == 'model_kelly':
                    b['fraction'] = min(b['quarter_kelly'], tr['max_position_fraction'])
                else:
                    b['fraction'] = tr['equal_weight_fraction']
                ranked.append(b)
                del armed[b['ticker']]

            for t, base_top, lam, edge, qk in sig_by_day.get(d, []):
                armed[t] = {'base_top': base_top, 'lam': lam, 'edge': edge,
                            'quarter_kelly': qk, 'armed_i': i}

        # 3. mark equity at the close
        held = sum(p['shares'] * arrays[t]['close_f'][i] for t, p in positions.items())
        eq_prev = cash + held
        equity[d] = eq_prev
        invested_frac.append(held / eq_prev if eq_prev > 0 else 0.0)
        pending = ranked

    # liquidate at the last close of the period for clean comparison
    d, i = days[-1], day_pos[days[-1]]
    for t, pos in list(positions.items()):
        px = arrays[t]['close_f'][i]
        cash += pos['shares'] * px * (1 - tr['cost_per_side'])
        record(t, pos, px, i, 'period_end', d)
    equity[d] = cash

    return pd.DataFrame(trades), equity, float(np.mean(invested_frac))


def metrics(trades: pd.DataFrame, equity: pd.Series, avg_invested: float) -> dict:
    total = equity.iloc[-1] / equity.iloc[0] - 1
    years = len(equity) / 252
    ann = (1 + total) ** (1 / years) - 1 if years > 0 else np.nan
    dd = (equity / equity.cummax() - 1).min()
    r = trades['ret_net'] if len(trades) else pd.Series(dtype=float)
    winners, losers = r[r > 0], r[r <= 0]
    return {
        'total_return': total, 'ann_return': ann, 'max_drawdown': dd,
        'n_trades': len(trades),
        'win_rate': len(winners) / len(r) if len(r) else np.nan,
        'avg_winner': winners.mean() if len(winners) else np.nan,
        'avg_loser': losers.mean() if len(losers) else np.nan,
        'avg_trade': r.mean() if len(r) else np.nan,
        'avg_invested': avg_invested,
    }


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    results.mkdir(exist_ok=True)
    fmt = lambda x: f'{x:.4f}'

    with open(results / 'weights.json') as f:
        weights = json.load(f)
    with open(results / 'shrink.json') as f:
        shrink = json.load(f)
    with open(results / 'kelly_table.json') as f:
        kelly_table = json.load(f)

    signals = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'signals.parquet')
    signals['date'] = pd.to_datetime(signals['date'])
    ok = signals['sanity_pass'] & signals[FEATURES[:-1]].notna().all(axis=1)
    scored = apply_artifacts(signals[ok], weights, shrink, kelly_table)
    signals = signals.merge(
        scored[['ticker', 'date', 'predicted', 'edge', 'quarter_kelly']],
        on=['ticker', 'date'], how='left')
    # template rows without a scored model get neutral values; they are ranked
    # randomly and sized equal-weight, so these are never used for decisions
    signals[['edge', 'quarter_kelly']] = signals[['edge', 'quarter_kelly']].fillna(0.0)
    print(f'{len(signals)} signal rows, {int(ok.sum())} scored')

    panel = load_panel(cfg)
    print(f'panel: {len(panel["arrays"])} tickers x {len(panel["calendar"])} days')

    today = str(panel['calendar'][-1].date())
    periods = {'dev': (bt['start'], bt['dev_end']),
               'test': (bt['test_start'], today)}

    for pname, period in periods.items():
        print(f'\n=== {pname} period {period[0]} .. {period[1]} ===')
        summary, curves = {}, {}
        for k, strat in enumerate(STRATEGIES):
            sig = strategy_signals(signals, strat, weights)
            trades, equity, avg_inv = simulate(panel, sig, cfg, strat, period,
                                               seed=bt['random_seed'] + k)
            summary[strat] = metrics(trades, equity, avg_inv)
            curves[strat] = equity
            trades.sort_values('entry_date').to_csv(
                results / f'{pname}_trades_{strat}.csv', index=False)
            pd.concat([trades.nlargest(10, 'ret_net'),
                       trades.nsmallest(10, 'ret_net')]).to_csv(
                results / f'{pname}_top_bottom_{strat}.csv', index=False)

            if strat == 'model_kelly' and len(trades) >= 5:
                q = pd.qcut(trades['edge'], 5, labels=False, duplicates='drop')
                quint = trades.groupby(q)['ret_net'].agg(['mean', 'count'])
                quint.index.name = 'edge_quintile (0=lowest)'
                quint.to_csv(results / f'{pname}_edge_quintiles.csv')
                print(f'\n[{pname}] model_kelly: avg trade return by edge quintile')
                print(quint.to_string(float_format=fmt))

        sm = pd.DataFrame(summary).T
        sm.to_csv(results / f'{pname}_summary.csv')
        print(f'\n[{pname}] summary')
        print(sm.to_string(float_format=fmt))

        spy = panel['spy_close']
        spy = spy[(spy.index >= period[0]) & (spy.index <= period[1])]
        plt.figure(figsize=(11, 6))
        for strat, eq in curves.items():
            plt.plot(eq.index, eq / eq.iloc[0], label=strat)
        plt.plot(spy.index, spy / spy.iloc[0], label='SPY', color='gray', linestyle='--')
        plt.yscale('log')
        plt.title(f'Equity curves, {pname} period ({period[0]} .. {period[1]})')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(results / f'equity_{pname}.png', dpi=120)
        plt.close()

    # 5c tables recomputed on the test period with dev-frozen bucket edges
    table = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'trade_table.parquet')
    table['entry_date'] = pd.to_datetime(table['entry_date'])
    test = table[(table['entry_date'] >= bt['test_start'])
                 & (table['entry_date'] <= today)].dropna(subset=FEATURES)
    test = apply_artifacts(test, weights, shrink, kelly_table)
    t1, t2, t3 = fivec_tables(test, shrink['r2_edges'], weights['pred_edges'])
    for n, t in [(1, t1), (2, t2), (3, t3)]:
        t.to_csv(results / f'test_5c_table{n}.csv', index=False)
    print('\n5c table 1 recomputed on test period:')
    print(t1.to_string(index=False, float_format=fmt))
    print('\n5c table 2 (lambda gap per r2 bucket) recomputed on test period:')
    print(t2.to_string(index=False, float_format=fmt))
    print(f'\nTables and charts written to {results}/')


if __name__ == '__main__':
    main()
