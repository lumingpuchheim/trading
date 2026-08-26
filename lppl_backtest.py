"""Backtest of the LPPL bubble-dip family: long, short, loose-gate, and
curve-timed variants, plus the no-LPPL ablation.

Run after lppl_detect.py:  python lppl_backtest.py

Strategies (identical fills, costs, slots, cooldown, equal 10% sizing):
  1. lppl_dip     — 3-of-5 bubble gate AND >= 4% below the 20-day high close:
                    buy next open.
  2. lppl_short   — mirror of lppl_dip: the same entry days, but SELL SHORT.
                    Adverse stop at (2 - stop_loss) x entry; same tc exit.
  3. lppl_dip2    — lppl_dip with the looser 2-of-5 vote gate (earlier flag).
  4. lppl_bottom2 — 2-of-5 gate; once a dip has started, the entry is timed:
                    buy at the open of the day the fitted LPPL curve reaches
                    its next local minimum (the model's estimated dip bottom).
  5. dip_only     — pre-screen (accelerating run-up) + the same dip, no LPPL.

Exits: close <= 0.92 x entry (longs; mirrored for the short) -> next open;
or today past the position's median critical time tc. dip_only has no tc and
uses a 60-trading-day time cap instead. A fresh bubble evaluation rolls a
held position's tc forward; when the flag lapses the last tc stands.
Decisions use only evaluations dated on or before the decision day.
"""

from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from lppl import curve_value, next_curve_minimum, prescreen

ROOT = Path(__file__).parent
START_EQUITY = 100_000.0
# earlier variants kept as reference, currently disabled:
# 'lppl_dip' (3-of-5), 'lppl_dip1' (1-of-5), 'lppl_short' (mirror),
# 'lppl_bottom2' (curve-timed), 'dip_only' (no LPPL)
# exit variants _trail and _ma tested 2026-08-25 and removed: both lose to
# the tc clock in both periods (see FINDINGS.md) — the trailing stop is
# shaken out by the same oscillations the entry buys, the SMA cross churns
STRATEGIES = ['lppl_dip2', 'lppl_dip2_rs']
PARAM_COLS = ['p_n', 'p_tc', 'p_m', 'p_w', 'p_a', 'p_b', 'p_c1', 'p_c2', 'p_sigma']


def load_config() -> dict:
    with open(ROOT / 'config.yaml') as f:
        return yaml.safe_load(f)


def load_panel(cfg: dict) -> dict:
    """Per-ticker arrays on the SPY calendar, including bubble states at both
    vote thresholds and per-day pointers to the latest cached evaluation."""
    g = cfg['lppl']
    d = cfg['data']
    data_dir = ROOT / d['cache_dir']
    spy = pd.read_parquet(data_dir / 'ohlcv' / f"{d['benchmark']}.parquet")
    calendar = spy.index
    cal_pos = {dt: i for i, dt in enumerate(calendar)}

    flags = pd.read_parquet(data_dir / 'lppl_flags.parquet')
    flags_by_ticker = {t: gg.sort_values('date') for t, gg in flags.groupby('ticker')}

    # market-state gate: the SAME dip definition applied to SPY itself;
    # a stock's dip while the whole market is dipping is systemic, not
    # stock-specific seller exhaustion
    spy_hi20 = spy['close'].rolling(g['dip_high_window']).max()
    market_dip = (spy['close'] <= (1 - g['dip_from_high']) * spy_hi20) \
        .to_numpy(dtype=bool)

    arrays = {}
    for path in sorted((data_dir / 'ohlcv').glob('*.parquet')):
        t = path.stem
        if t == d['benchmark']:
            continue
        raw = pd.read_parquet(path)
        sma50 = raw['close'].rolling(cfg['lppl_trading']['sma_exit']).mean()
        df = raw.reindex(calendar)
        sma50 = sma50.reindex(calendar).ffill()
        close = df['close'].to_numpy()
        n = len(close)
        dollar_vol = (df['close'] * df['volume']).rolling(d['dollar_volume_window']).mean()
        liquid = ((df['close'] > d['min_price'])
                  & (dollar_vol > d['min_dollar_volume'])).to_numpy(dtype=bool)

        hi20 = df['close'].rolling(g['dip_high_window']).max().to_numpy()
        dip = np.isfinite(close) & np.isfinite(hi20) \
            & (close <= (1 - g['dip_from_high']) * hi20)
        # _band variants: dip must also be shallower than dip_max_from_high
        dip_band = dip & (close >= (1 - g['dip_max_from_high']) * hi20)

        pre = np.zeros(n, dtype=bool)
        finite = np.isfinite(close)
        cvals = df['close'].ffill().to_numpy()
        for i in range(g['prescreen_lookback'], n):
            if finite[i]:
                pre[i] = prescreen(cvals, i, cfg)

        gg = flags_by_ticker.get(t)
        evals: list[dict] = []
        votes_d = np.zeros(n, dtype=np.int8)
        r2_d = np.zeros(n)
        ev_ptr = np.full(n, -1, dtype=np.int64)

        def build_state(thr: int) -> tuple[np.ndarray, np.ndarray]:
            bubble = np.zeros(n, dtype=bool)
            tc_i = np.full(n, -1, dtype=np.int64)
            prev_ok = 0
            if gg is None:
                return bubble, tc_i
            for r in gg.itertuples():
                j = cal_pos.get(r.date)
                if j is None:
                    continue
                if r.votes >= thr:
                    prev_ok += 1
                    if prev_ok >= g['persistence']:
                        until = min(n, j + g['refit_every'])
                        bubble[j:until] = True
                        if np.isfinite(r.tc_ahead):
                            tc_i[j:until] = j + int(round(r.tc_ahead))
                else:
                    prev_ok = 0
            return bubble, tc_i

        b3, tc3 = build_state(g['min_votes'])
        b2, tc2 = build_state(g['min_votes_loose'])
        b1, tc1 = build_state(1)  # lppl_dip1: any single window qualifying

        if gg is not None:
            for r in gg.itertuples():
                j = cal_pos.get(r.date)
                if j is None:
                    continue
                until = min(n, j + g['refit_every'])
                votes_d[j:until] = r.votes
                r2_d[j:until] = r.mean_r2 if np.isfinite(r.mean_r2) else 0.0
                ev_ptr[j:until] = len(evals)
                evals.append({'j': j, **{c: getattr(r, c) for c in PARAM_COLS}})

        # trailing return for relative-strength ranking (_rs variant)
        lb = g['rs_lookback']
        rs = np.full(n, -np.inf)
        with np.errstate(invalid='ignore'):
            r = cvals[lb:] / cvals[:-lb] - 1
        rs[lb:] = np.where(np.isfinite(r), r, -np.inf)

        finite_idx = np.flatnonzero(finite)
        arrays[t] = {'open': df['open'].to_numpy(), 'close': close, 'rs': rs,
                     'close_f': cvals, 'liquid': liquid, 'dip': dip,
                     'dip_band': dip_band, 'pre': pre,
                     'b3': b3, 'tc3': tc3, 'b2': b2, 'tc2': tc2,
                     'b1': b1, 'tc1': tc1, 'sma50': sma50.to_numpy(),
                     'votes': votes_d, 'r2': r2_d, 'ev_ptr': ev_ptr,
                     'evals': evals,
                     'last_i': int(finite_idx[-1]) if len(finite_idx) else -1}
    return {'calendar': calendar, 'arrays': arrays, 'spy_close': spy['close'],
            'market_dip': market_dip}


def short_value(invest: float, p0: float, p: float, cost: float) -> float:
    """Current value of a short of notional `invest` opened at p0."""
    return invest * (1 + (p0 * (1 - cost) - p * (1 + cost)) / (p0 * (1 + cost)))


def curve_consistent(a: dict, i: int, cfg: dict) -> bool:
    """Guard 1: today's log-price must sit no further than `curve_band_sigma`
    fitted sigmas below the latest evaluation's own fitted curve. The model
    withdraws its bubble claim the day the price leaves the model."""
    ptr = a['ev_ptr'][i]
    if ptr < 0:
        return False
    p = a['evals'][ptr]
    k = i - p['j']
    if not p['p_n'] or not np.isfinite(p['p_sigma']) or k >= p['p_tc'] - 1:
        return False
    c = a['close'][i]
    if not (np.isfinite(c) and c > 0):
        return False
    band = cfg['lppl']['curve_band_sigma'] * p['p_sigma']
    return np.log(c) >= curve_value(p, k) - band


def candidates_today(arrays: dict, i: int, strategy: str, positions: dict,
                     cooldown: dict, pending: dict, cfg: dict,
                     market_dip: np.ndarray,
                     recent_tc: dict | None = None) -> list[dict]:
    if strategy.endswith('_guard') and market_dip[i]:
        return []  # guard 3: the market itself is dipping — systemic, stand aside
    out = []
    for t, a in arrays.items():
        if t in positions or t in pending or cooldown.get(t, -1) > i \
                or not a['liquid'][i]:
            continue
        cand = None
        if strategy in ('lppl_dip', 'lppl_short'):
            if a['b3'][i] and a['dip'][i] and a['tc3'][i] > i:
                cand = {'fill_i': i + 1, 'tc_i': int(a['tc3'][i])}
        elif strategy.startswith('lppl_dip2'):  # variants share the entry
            dip_col = 'dip_band' if strategy.endswith(('_band', '_bb')) else 'dip'
            if a['b2'][i] and a[dip_col][i] and a['tc2'][i] > i:
                need_curve = strategy.endswith(('_guard', '_g1'))
                if not need_curve or curve_consistent(a, i, cfg):
                    cand = {'fill_i': i + 1, 'tc_i': int(a['tc2'][i])}
            elif strategy.endswith('_leg2') and recent_tc is not None \
                    and i - recent_tc.get(t, -10**9) <= 130 \
                    and a['b1'][i] and a['dip'][i] and a['tc1'][i] > i:
                # second-leg re-entry: after a recent tc exit, the old bubble
                # pollutes the long windows, so 1-of-5 corroboration suffices
                cand = {'fill_i': i + 1, 'tc_i': int(a['tc1'][i])}
        elif strategy == 'lppl_dip1':
            if a['b1'][i] and a['dip'][i] and a['tc1'][i] > i:
                cand = {'fill_i': i + 1, 'tc_i': int(a['tc1'][i])}
        elif strategy == 'lppl_bottom2':
            if a['b2'][i] and a['dip'][i] and a['tc2'][i] > i \
                    and a['ev_ptr'][i] >= 0:
                p = a['evals'][a['ev_ptr'][i]]
                if p['p_n'] > 0:
                    k = next_curve_minimum(p, after=i - p['j'])
                    if k is not None:
                        cand = {'fill_i': p['j'] + k, 'tc_i': int(a['tc2'][i])}
        else:  # dip_only
            if a['pre'][i] and a['dip'][i]:
                cand = {'fill_i': i + 1, 'tc_i': -1}
        if cand is not None:
            out.append({'ticker': t, 'votes': int(a['votes'][i]),
                        'r2': float(a['r2'][i]), 'rs': float(a['rs'][i]),
                        **cand})
    if strategy.endswith(('_breadth', '_bb')) \
            and len(out) > cfg['lppl']['breadth_veto_max']:
        return []  # many candidates at once = systemic move, take none
    if strategy.endswith('_rs'):
        out.sort(key=lambda c: (-c['rs'], c['ticker']))
    else:
        out.sort(key=lambda c: (-c['votes'], -c['r2'], c['ticker']))
    return out


def simulate(panel: dict, cfg: dict, strategy: str, period: tuple[str, str],
             fraction: float | None = None, max_pos: int | None = None,
             tc_shift: int = 0, stop_loss: float | None = None,
             entry_gate: np.ndarray | None = None,
             size_mult: np.ndarray | None = None,
             tc_roll_key: str | None = None,
             earn_exit: tuple[dict, float] | None = None) -> tuple[pd.DataFrame, pd.Series, float]:
    tr = dict(cfg['lppl_trading'])
    if fraction is None:
        fraction = tr['equal_weight_fraction']
    if max_pos is None:
        max_pos = tr['max_positions']
    if stop_loss is not None:
        tr['stop_loss'] = stop_loss
    arrays = panel['arrays']
    cal = panel['calendar']
    day_pos = {d: i for i, d in enumerate(cal)}
    days = cal[(cal >= period[0]) & (cal <= period[1])]
    side = -1 if strategy == 'lppl_short' else 1
    flag_col = 'b3'
    if strategy == 'lppl_dip1':
        flag_col = 'b1'
    elif strategy.startswith('lppl_dip2') or strategy == 'lppl_bottom2':
        flag_col = 'b2'
    variant = 'trail' if strategy.endswith('_trail') \
        else 'ma' if strategy.endswith('_ma') \
        else 'fd' if strategy.endswith('_fd') \
        else 'greed' if strategy.endswith('_greed') else 'base'
    cost = tr['cost_per_side']
    short_stop = 2 - tr['stop_loss']  # e.g. 1.08: mirrored 8% adverse move

    cash, eq_prev = START_EQUITY, START_EQUITY
    positions: dict[str, dict] = {}
    cooldown: dict[str, int] = {}
    recent_tc: dict[str, int] = {}  # ticker -> day index of last tc exit
    pending: dict[str, dict] = {}
    trades: list[dict] = []
    equity = pd.Series(np.nan, index=days)
    invested: list[float] = []

    def pos_value(t: str, pos: dict, p: float) -> float:
        if pos['side'] == 1:
            return pos['shares'] * p
        return short_value(pos['invest'], pos['entry_px'], p, cost)

    def close_out(t, pos, px, i, reason, d):
        if pos['side'] == 1:
            proceeds = pos['shares'] * px * (1 - cost)
            ret = px * (1 - cost) / (pos['entry_px'] * (1 + cost)) - 1
        else:
            proceeds = short_value(pos['invest'], pos['entry_px'], px, cost)
            ret = proceeds / pos['invest'] - 1
        trades.append({
            'ticker': t, 'entry_date': pos['entry_date'], 'exit_date': d,
            'entry_px': pos['entry_px'], 'exit_px': px,
            'days_held': i - pos['entry_i'],
            'ret_net': ret, 'exit_reason': reason})
        cooldown[t] = i + tr['reentry_cooldown']
        if reason == 'tc':
            recent_tc[t] = i
        return proceeds

    for d in days:
        i = day_pos[d]

        # fills at the open: exits first, then entries due today
        for t in [t for t, p in positions.items() if p['exit_reason']]:
            pos = positions.pop(t)
            a = arrays[t]
            px = a['open'][i] if np.isfinite(a['open'][i]) else a['close_f'][i]
            cash += close_out(t, pos, px, i, pos['exit_reason'], d)

        for t in [t for t, e in pending.items() if e['fill_i'] <= i]:
            e = pending.pop(t)
            a = arrays[t]
            if t in positions or len(positions) >= tr['max_positions']:
                continue
            if strategy == 'lppl_bottom2' and not a[flag_col][i]:
                continue  # flag died before the timed entry day
            px = a['open'][i]
            if not np.isfinite(px):
                continue
            if side == 1:
                # whole shares only: round the target size down
                shares = np.floor(fraction * e.get('mult', 1.0) * eq_prev / px)
                outflow = shares * px * (1 + cost)
                if shares < 1 or outflow > cash:
                    continue  # cannot afford one share / would breach exposure
                positions[t] = {'side': 1, 'shares': shares, 'entry_px': px,
                                'entry_date': d, 'entry_i': i,
                                'exit_reason': None, 'tc_i': e['tc_i'],
                                'invest': outflow}
                cash -= outflow
            else:
                invest = fraction * eq_prev
                if invest > cash:
                    continue
                positions[t] = {'side': -1, 'shares': 0.0, 'entry_px': px,
                                'entry_date': d, 'entry_i': i,
                                'exit_reason': None, 'tc_i': e['tc_i'],
                                'invest': invest}
                cash -= invest

        # pre-earnings ejector: a report lands on the coming gap night and
        # the position is red beyond the threshold -> sell at THIS close
        # (the calendar is known in advance, so the fill is implementable)
        if earn_exit is not None:
            masks, thr = earn_exit
            for t in list(positions):
                pos = positions[t]
                m = masks.get(t)
                c = arrays[t]['close_f'][i]
                if m is not None and m[i] and pos['side'] == 1 \
                        and not pos['exit_reason'] \
                        and c <= thr * pos['entry_px']:
                    cash += close_out(t, positions.pop(t), c, i, 'earn', d)

        # decisions at the close
        for t, pos in positions.items():
            a = arrays[t]
            c = a['close_f'][i]
            # tc_roll_key: roll held positions' tc from an alternative stream
            # (e.g. refit-while-held evaluations) instead of the entry flags
            tc_col = tc_roll_key or ('tc' + flag_col[1])
            if strategy != 'dip_only' and a[tc_col][i] >= 0:
                pos['tc_i'] = int(a[tc_col][i])
            pos['peak'] = max(pos.get('peak', pos['entry_px']), c)
            if i >= a['last_i'] and a['last_i'] < len(cal) - 1:
                pos['exit_reason'] = 'delisted'
            elif variant == 'trail':
                # trailing 8% stop from the highest close since entry;
                # subsumes the fixed stop, replaces the tc clock
                if c <= tr['stop_loss'] * pos['peak']:
                    pos['exit_reason'] = 'trail_stop'
            elif variant == 'ma':
                # fixed stop + trend-death exit, no tc clock
                if c <= tr['stop_loss'] * pos['entry_px']:
                    pos['exit_reason'] = 'stop'
                elif np.isfinite(a['sma50'][i]) and c < a['sma50'][i]:
                    pos['exit_reason'] = 'sma'
            elif variant == 'fd':
                # fixed stop + flag-death: sell when the detector stops
                # affirming the bubble (votes below the loose gate), instead
                # of waiting for the stale tc date
                if c <= tr['stop_loss'] * pos['entry_px']:
                    pos['exit_reason'] = 'stop'
                elif a['votes'][i] < cfg['lppl']['min_votes_loose']:
                    pos['exit_reason'] = 'flag'
            elif pos['side'] == 1 and c <= tr['stop_loss'] * pos['entry_px']:
                pos['exit_reason'] = 'stop'
            elif pos['side'] == -1 and c >= short_stop * pos['entry_px']:
                pos['exit_reason'] = 'stop'
            elif strategy == 'dip_only':
                if i - pos['entry_i'] >= tr['dip_only_max_hold']:
                    pos['exit_reason'] = 'time'
            elif pos['tc_i'] >= 0 and i >= pos['tc_i'] + tc_shift:
                # _greed: postpone the tc exit while the stock is still in
                # its own top-decile 20-day acceleration (blow-off riding)
                if variant == 'greed' and a.get('accel') is not None \
                        and a['accel'][i]:
                    pass
                else:
                    pos['exit_reason'] = 'tc'

        exiting = sum(1 for p in positions.values() if p['exit_reason'])
        slots = tr['max_positions'] - (len(positions) - exiting) - len(pending)
        mult = 1.0 if size_mult is None else float(size_mult[i])
        if slots > 0 and mult > 0 and (entry_gate is None or entry_gate[i]):
            for c in candidates_today(arrays, i, strategy, positions, cooldown,
                                      pending, cfg, panel['market_dip'],
                                      recent_tc)[:slots]:
                c['mult'] = mult
                pending[c['ticker']] = c

        held = sum(pos_value(t, p, arrays[t]['close_f'][i])
                   for t, p in positions.items())
        eq_prev = cash + held
        equity[d] = eq_prev
        invested.append(abs(held) / eq_prev if eq_prev > 0 else 0.0)

    d, i = days[-1], day_pos[days[-1]]
    for t, pos in list(positions.items()):
        cash += close_out(t, pos, arrays[t]['close_f'][i], i, 'period_end', d)
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
            't_stat': r.mean() / (r.std() / np.sqrt(len(r)))
                      if len(r) > 2 and r.std() > 0 else np.nan,
            'avg_invested': avg_invested}


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    results.mkdir(exist_ok=True)
    fmt = lambda x: f'{x:.4f}'

    panel = load_panel(cfg)
    n3 = sum(int(a['b3'].sum()) for a in panel['arrays'].values())
    n2 = sum(int(a['b2'].sum()) for a in panel['arrays'].values())
    print(f'panel: {len(panel["arrays"])} tickers, '
          f'{n3} bubble days at 3-of-5, {n2} at 2-of-5')

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
            if len(trades):
                trades = trades.sort_values('entry_date')
            trades.to_csv(results / f'lppl_{pname}_trades_{strat}.csv', index=False)
            if len(trades):
                pd.concat([trades.nlargest(10, 'ret_net'),
                           trades.nsmallest(10, 'ret_net')]).to_csv(
                    results / f'lppl_{pname}_top_bottom_{strat}.csv', index=False)

        sm = pd.DataFrame(summary).T
        sm.to_csv(results / f'lppl_{pname}_summary.csv')
        print(sm.to_string(float_format=fmt))
        for strat in STRATEGIES:
            try:
                tdf = pd.read_csv(results / f'lppl_{pname}_trades_{strat}.csv')
            except pd.errors.EmptyDataError:
                continue
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
        plt.title(f'LPPL strategies, {pname} period ({period[0]} .. {period[1]})')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(results / f'lppl_equity_{pname}.png', dpi=120)
        plt.close()

    print(f'\nTables and charts written to {results}/')


if __name__ == '__main__':
    main()
