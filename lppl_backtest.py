"""Backtest of the LPPL bubble-dip strategy plus its two ablations.

Run after lppl_detect.py:  python lppl_backtest.py

Strategies (identical fills, costs, slots, cooldown, equal 10% sizing):
  1. lppl_dip     — in a persistent bubble AND >= 4% below the 20-day high
                    close: buy next open. THE strategy.
  2. bubble_nodip — buy as soon as the bubble flag is tradeable, no dip wait.
                    Shows what waiting for the dip adds.
  3. dip_only     — pre-screen passes (accelerating run-up) AND the same dip,
                    but no LPPL fit required. Shows what the expensive fit
                    adds beyond cheap momentum.

Exits: close <= 0.92 x entry (the 8% rule) -> next open; or today past the
bubble's median critical time tc -> next open. dip_only has no tc, so it
uses a 60-trading-day time cap instead (stated in the summary).

The bubble flag: an evaluation every `refit_every` days; a day is tradeable
when the last `persistence` consecutive evaluations (staleness < refit gap)
said bubble. tc is carried from the latest evaluation while the flag lasts.
Decisions use only evaluations dated on or before the decision day.
"""

from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from lppl import prescreen

ROOT = Path(__file__).parent
START_EQUITY = 100_000.0
STRATEGIES = ['lppl_dip', 'lppl_dip_once', 'bubble_nodip', 'dip_only']


def load_config() -> dict:
    with open(ROOT / 'config.yaml') as f:
        return yaml.safe_load(f)


def load_panel(cfg: dict) -> dict:
    """Per-ticker arrays on the SPY calendar: prices, liquidity, dip flag,
    pre-screen flag, and the bubble state expanded from cached evaluations."""
    g = cfg['lppl']
    d = cfg['data']
    data_dir = ROOT / d['cache_dir']
    spy = pd.read_parquet(data_dir / 'ohlcv' / f"{d['benchmark']}.parquet")
    calendar = spy.index
    cal_pos = {dt: i for i, dt in enumerate(calendar)}

    flags = pd.read_parquet(data_dir / 'lppl_flags.parquet')
    flags_by_ticker = {t: gg for t, gg in flags.groupby('ticker')}

    arrays = {}
    for path in sorted((data_dir / 'ohlcv').glob('*.parquet')):
        t = path.stem
        if t == d['benchmark']:
            continue
        df = pd.read_parquet(path).reindex(calendar)
        close = df['close'].to_numpy()
        n = len(close)
        dollar_vol = (df['close'] * df['volume']).rolling(d['dollar_volume_window']).mean()
        liquid = ((df['close'] > d['min_price'])
                  & (dollar_vol > d['min_dollar_volume'])).to_numpy(dtype=bool)

        hi20 = df['close'].rolling(g['dip_high_window']).max().to_numpy()
        dip = np.isfinite(close) & np.isfinite(hi20) \
            & (close <= (1 - g['dip_from_high']) * hi20)

        pre = np.zeros(n, dtype=bool)
        finite = np.isfinite(close)
        cvals = df['close'].ffill().to_numpy()
        for i in range(g['prescreen_lookback'], n):
            if finite[i]:
                pre[i] = prescreen(cvals, i, cfg)

        # expand cached evaluations into daily bubble state
        bubble = np.zeros(n, dtype=bool)       # tradeable (persistent) flag
        tc_i = np.full(n, -1, dtype=np.int64)  # calendar index of current tc
        gg = flags_by_ticker.get(t)
        if gg is not None:
            gg = gg.sort_values('date')
            prev_ok = 0
            for r in gg.itertuples():
                j = cal_pos.get(r.date)
                if j is None:
                    continue
                if r.bubble:
                    prev_ok += 1
                    if prev_ok >= g['persistence']:
                        until = min(n, j + g['refit_every'])
                        bubble[j:until] = True
                        tc_day = j + int(round(r.tc_ahead))
                        tc_i[j:until] = tc_day
                else:
                    prev_ok = 0
        # bubble episodes: flag runs separated by < episode_gap off-days are
        # one episode; a longer quiet stretch means the bubble ended
        episode = np.full(n, -1, dtype=np.int64)
        eid, off_run = -1, 10 ** 9
        for j in range(n):
            if bubble[j]:
                if off_run >= cfg['lppl_trading']['episode_gap']:
                    eid += 1
                off_run = 0
                episode[j] = eid
            else:
                off_run += 1

        finite_idx = np.flatnonzero(finite)
        last_i = int(finite_idx[-1]) if len(finite_idx) else -1
        arrays[t] = {'open': df['open'].to_numpy(), 'close': close,
                     'close_f': df['close'].ffill().to_numpy(),
                     'liquid': liquid, 'dip': dip, 'pre': pre,
                     'bubble': bubble, 'tc_i': tc_i, 'episode': episode,
                     'last_i': last_i, 'votes': None}
        if gg is not None:
            v = np.zeros(n, dtype=np.int8)
            r2 = np.zeros(n)
            for r in gg.itertuples():
                j = cal_pos.get(r.date)
                if j is not None:
                    until = min(n, j + g['refit_every'])
                    v[j:until] = r.votes
                    r2[j:until] = r.mean_r2 if np.isfinite(r.mean_r2) else 0.0
            arrays[t]['votes'] = v
            arrays[t]['r2'] = r2
    return {'calendar': calendar, 'arrays': arrays, 'spy_close': spy['close']}


def candidates_today(arrays: dict, i: int, strategy: str, positions: dict,
                     cooldown: dict, profited: dict) -> list[dict]:
    out = []
    for t, a in arrays.items():
        if t in positions or cooldown.get(t, -1) > i or not a['liquid'][i]:
            continue
        if strategy in ('lppl_dip', 'lppl_dip_once'):
            ok = a['bubble'][i] and a['dip'][i] and a['tc_i'][i] > i
            # once-rule: already took a profit in this same bubble episode
            if ok and strategy == 'lppl_dip_once' \
                    and profited.get(t, -2) == a['episode'][i]:
                ok = False
        elif strategy == 'bubble_nodip':
            ok = a['bubble'][i] and a['tc_i'][i] > i
        else:  # dip_only
            ok = a['pre'][i] and a['dip'][i]
        if ok:
            votes = int(a['votes'][i]) if a['votes'] is not None else 0
            r2 = float(a['r2'][i]) if a['votes'] is not None else 0.0
            out.append({'ticker': t, 'votes': votes, 'r2': r2,
                        'tc_i': int(a['tc_i'][i]),
                        'episode': int(a['episode'][i])})
    out.sort(key=lambda c: (-c['votes'], -c['r2'], c['ticker']))
    return out


def simulate(panel: dict, cfg: dict, strategy: str,
             period: tuple[str, str]) -> tuple[pd.DataFrame, pd.Series, float]:
    tr = cfg['lppl_trading']
    arrays = panel['arrays']
    cal = panel['calendar']
    day_pos = {d: i for i, d in enumerate(cal)}
    days = cal[(cal >= period[0]) & (cal <= period[1])]

    cash, eq_prev = START_EQUITY, START_EQUITY
    positions: dict[str, dict] = {}
    cooldown: dict[str, int] = {}
    profited: dict[str, int] = {}  # ticker -> episode id of a profitable exit
    pending: list[dict] = []
    trades: list[dict] = []
    equity = pd.Series(np.nan, index=days)
    invested: list[float] = []
    cost = tr['cost_per_side']

    def close_out(t, pos, px, i, reason, d):
        ret = px * (1 - cost) / (pos['entry_px'] * (1 + cost)) - 1
        trades.append({
            'ticker': t, 'entry_date': pos['entry_date'], 'exit_date': d,
            'entry_px': pos['entry_px'], 'exit_px': px,
            'days_held': i - pos['entry_i'],
            'ret_net': ret, 'exit_reason': reason})
        cooldown[t] = i + tr['reentry_cooldown']
        if ret > 0:
            profited[t] = pos.get('episode', -2)

    for d in days:
        i = day_pos[d]

        # fills at the open: exits scheduled yesterday, then entries
        for t, reason in [(t, p['exit_reason']) for t, p in positions.items()
                          if p['exit_reason']]:
            pos = positions.pop(t)
            a = arrays[t]
            px = a['open'][i] if np.isfinite(a['open'][i]) else a['close_f'][i]
            cash += pos['shares'] * px * (1 - cost)
            close_out(t, pos, px, i, reason, d)

        for e in pending:
            t = e['ticker']
            if t in positions or len(positions) >= tr['max_positions']:
                continue
            px = arrays[t]['open'][i]
            if not np.isfinite(px):
                continue
            invest = tr['equal_weight_fraction'] * eq_prev
            if invest > cash:
                continue  # would breach full investment: skip
            positions[t] = {'shares': invest / (px * (1 + cost)), 'entry_px': px,
                            'entry_date': d, 'entry_i': i, 'exit_reason': None,
                            'tc_i': e.get('tc_i', -1),
                            'episode': e.get('episode', -2)}
            cash -= invest
        pending = []

        # decisions at the close
        for t, pos in positions.items():
            a = arrays[t]
            c = a['close_f'][i]
            # a fresh bubble evaluation rolls the position's critical time
            # forward; when the flag lapses, the last known tc stands
            if strategy != 'dip_only' and a['tc_i'][i] >= 0:
                pos['tc_i'] = int(a['tc_i'][i])
            if i >= a['last_i'] and a['last_i'] < len(cal) - 1:
                pos['exit_reason'] = 'delisted'
            elif c <= tr['stop_loss'] * pos['entry_px']:
                pos['exit_reason'] = 'stop'
            elif strategy == 'dip_only':
                if i - pos['entry_i'] >= tr['dip_only_max_hold']:
                    pos['exit_reason'] = 'time'
            elif pos['tc_i'] >= 0 and i >= pos['tc_i']:
                pos['exit_reason'] = 'tc'

        exiting = sum(1 for p in positions.values() if p['exit_reason'])
        slots = tr['max_positions'] - (len(positions) - exiting)
        if slots > 0:
            pending = candidates_today(arrays, i, strategy, positions,
                                       cooldown, profited)[:slots]

        held = sum(p['shares'] * arrays[t]['close_f'][i] for t, p in positions.items())
        eq_prev = cash + held
        equity[d] = eq_prev
        invested.append(held / eq_prev if eq_prev > 0 else 0.0)

    d, i = days[-1], day_pos[days[-1]]
    for t, pos in list(positions.items()):
        px = arrays[t]['close_f'][i]
        cash += pos['shares'] * px * (1 - cost)
        close_out(t, pos, px, i, 'period_end', d)
    equity[days[-1]] = cash
    return pd.DataFrame(trades), equity, float(np.mean(invested))


def metrics(trades: pd.DataFrame, equity: pd.Series, avg_invested: float) -> dict:
    total = equity.iloc[-1] / equity.iloc[0] - 1
    years = len(equity) / 252
    r = trades['ret_net'] if len(trades) else pd.Series(dtype=float)
    winners, losers = r[r > 0], r[r <= 0]
    return {'total_return': total,
            'ann_return': (1 + total) ** (1 / years) - 1 if years > 0 else np.nan,
            'max_drawdown': (equity / equity.cummax() - 1).min(),
            'n_trades': len(trades),
            'win_rate': len(winners) / len(r) if len(r) else np.nan,
            'avg_winner': winners.mean() if len(winners) else np.nan,
            'avg_loser': losers.mean() if len(losers) else np.nan,
            'avg_trade': r.mean() if len(r) else np.nan,
            'avg_invested': avg_invested}


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    results.mkdir(exist_ok=True)
    fmt = lambda x: f'{x:.4f}'

    panel = load_panel(cfg)
    n_bubble_days = sum(int(a['bubble'].sum()) for a in panel['arrays'].values())
    print(f'panel: {len(panel["arrays"])} tickers, '
          f'{n_bubble_days} tradeable bubble stock-days')

    today = str(panel['calendar'][-1].date())
    periods = {'dev': (bt['start'], bt['dev_end']),
               'test': (bt['test_start'], today)}

    for pname, period in periods.items():
        print(f'\n=== {pname} period {period[0]} .. {period[1]} ===')
        summary, curves = {}, {}
        for strat in STRATEGIES:
            trades, equity, avg_inv = simulate(panel, cfg, strat, period)
            summary[strat] = metrics(trades, equity, avg_inv)
            curves[strat] = equity
            trades.sort_values('entry_date').to_csv(
                results / f'lppl_{pname}_trades_{strat}.csv', index=False)
            if len(trades):
                pd.concat([trades.nlargest(10, 'ret_net'),
                           trades.nsmallest(10, 'ret_net')]).to_csv(
                    results / f'lppl_{pname}_top_bottom_{strat}.csv', index=False)

        sm = pd.DataFrame(summary).T
        sm.to_csv(results / f'lppl_{pname}_summary.csv')
        print(sm.to_string(float_format=fmt))
        for strat in STRATEGIES:
            tdf = pd.read_csv(results / f'lppl_{pname}_trades_{strat}.csv')
            if len(tdf):
                print(f'  {strat} exits: '
                      f'{tdf["exit_reason"].value_counts().to_dict()}')

        spy = panel['spy_close']
        spy = spy[(spy.index >= period[0]) & (spy.index <= period[1])]
        plt.figure(figsize=(11, 6))
        for strat, eq in curves.items():
            plt.plot(eq.index, eq / eq.iloc[0], label=strat)
        plt.plot(spy.index, spy / spy.iloc[0], label='SPY', color='gray',
                 linestyle='--')
        plt.yscale('log')
        plt.title(f'LPPL bubble-dip strategies, {pname} period '
                  f'({period[0]} .. {period[1]})')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(results / f'lppl_equity_{pname}.png', dpi=120)
        plt.close()

    print(f'\nTables and charts written to {results}/')


if __name__ == '__main__':
    main()
