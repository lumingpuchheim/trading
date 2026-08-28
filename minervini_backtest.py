"""Minervini Stage-2 breakout — portfolio audit (MINERVINI_SPEC.md v2).

Zero tunables: every constant was frozen in the spec and lives in the
`minervini:` / `minervini_trading:` blocks of config.yaml. Nothing here
selects anything, so both periods are reported and the bar is "positive
and non-collapsed in BOTH".

Entries: a name on yesterday's setup list gets a resting buy stop at
pivot x 1.001. It fills intraday at max(open, stop); a fill more than
5% over the pivot is refused rather than chased. Market light green.
Exits: close <= 0.92 x entry, close < SMA50, or a breakout that closed
without 1.5x volume (`failed_breakout`, sold at the next open).
Mechanics copied from lppl_dip2: 10 slots, 10% equal weight, whole
shares, 0.2% per side, 20-day re-entry cooldown.

Controls: 200 random portfolios buying random template-passing stocks on
random days under the same slots, cooldown, market light and exits, at
the strategy's own realised entry rate. They fill at the next open --
a random name has no pivot to rest an order on -- so the strategy's
intraday buy-stop fill is the one mechanical difference between them.

The v2 acceptance gate FAILS (see minervini_gate.py and FINDINGS). This
audit was run anyway, at the user's explicit instruction, in preference
to hand-amending the rules. Read every number below through that.

Run: python minervini_backtest.py             # audit + controls
     python minervini_backtest.py --rebuild   # ignore the panel cache
     python minervini_backtest.py --v5 --e3 --moc   # standing config v5r
     python minervini_backtest.py --v9 --moc        # v5r + section 13

--v9 is section 13's momentum-conditioned selling on top of v5r: the
+20% half-sale fires only for SLOW winners, a stock that ran +20% inside
15 trading days is held whole for 40 days (stop, breakeven and the
climax partial stay on), and a still-whole position more than 30% up
sells half into the largest up-day of its run.
"""

import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, metrics
from minervini import (beat_gate, eps_gate, repertoire, report_within,
                       rs_line_at_high, rs_ok_matrix, rs_return, signals,
                       weak_day_score)

START_EQUITY = 100_000.0
PANEL_CACHE = 'minervini_panel_v2.npz'
PANEL_CACHE_FUND = 'minervini_panel_v2_fund.npz'
PANEL_CACHE_BEAT = 'minervini_panel_v2_beat.npz'
PANEL_CACHE_BOTH = 'minervini_panel_v2_both.npz'
PANEL_CACHE_V3 = 'minervini_panel_v3.npz'
PANEL_CACHE_V4 = 'minervini_panel_v4.npz'


def market_dimmer(spy_close: pd.Series) -> np.ndarray:
    """Spec 12.2: a four-point market score instead of a binary light."""
    s = spy_close
    v20 = s.pct_change().rolling(20).std()
    pts = ((s > s.rolling(200).mean()).astype(int)
           + (s > s.rolling(50).mean()).astype(int)
           + (~(v20 > v20.rolling(756).quantile(0.90))).astype(int)
           + (s.pct_change(20) > 0).astype(int))
    return pts.to_numpy()


def market_green(spy_close: pd.Series) -> np.ndarray:
    """The gate we already trust: SPY above its 200d SMA (trend) and 20d
    realised vol at or below its trailing 756d 90th percentile (calm)."""
    trend = spy_close > spy_close.rolling(200).mean()
    v20 = spy_close.pct_change().rolling(20).std()
    calm = ~(v20 > v20.rolling(756).quantile(0.90))
    return (trend & calm).to_numpy()


def apply_v3(cfg: dict) -> dict:
    """Overlay the frozen section-9 constants (MINERVINI_SPEC.md):
    higher lows, earnings blackout, decisive trend exit, breakeven."""
    import copy
    cfg = copy.deepcopy(cfg)
    v3 = cfg['minervini_v3']
    cfg['minervini']['require_higher_lows'] = v3['require_higher_lows']
    cfg['minervini']['earnings_blackout_days'] = v3['earnings_blackout_days']
    for key in ('decisive_break_frac', 'decisive_volume', 'breakeven_r'):
        cfg['minervini_trading'][key] = v3[key]
    return cfg


def apply_v9(cfg: dict) -> dict:
    """v9 = the standing config v5r (--v5 --e3) + section 13's
    momentum-conditioned selling: the +20% partial becomes conditional on
    HOW the stock got there, and a climax partial is added."""
    cfg = apply_v5(cfg)
    cfg['minervini_trading']['reentry_fast'] = True      # v5r keeps E3
    cfg['minervini_trading']['momentum_sell'] = True
    return cfg


def apply_v6(cfg: dict) -> dict:
    """v6 = v5 + the money and market engines (spec 12.1-12.2)."""
    cfg = apply_v5(cfg)
    for key, val in cfg['minervini_v6'].items():
        cfg['minervini_trading'][key] = val
    return cfg


def apply_v5(cfg: dict) -> dict:
    """v5 = v4 context + the section-11 entry repertoire."""
    cfg = apply_v4(cfg)
    cfg['minervini_trading']['repertoire'] = True
    return cfg


def apply_v4(cfg: dict) -> dict:
    """Overlay the frozen section-10 constants on top of v3."""
    cfg = apply_v3(cfg)
    v4 = cfg['minervini_v4']
    for key in ('protect_days', 'strength_sell_at', 'strength_sell_frac',
                'rank_selection'):
        cfg['minervini_trading'][key] = v4[key]
    return cfg


def build_panel(cfg: dict, rebuild: bool = False, fund: bool = False,
                beat: bool = False, v3: bool = False,
                v4: bool = False, v5: bool = False) -> dict:
    """Per-day signal matrices (days x tickers) on the SPY calendar.

    fund=True additionally requires the SEPA pillar-2 EPS gate (spec
    section 8) on every setup day, and narrows the control pool the same
    way, so the comparison isolates the fundamentals filter alone."""
    d = cfg['data']
    data_dir = ROOT / d['cache_dir']
    cache = data_dir / ('minervini_panel_v5.npz' if v5 else
                        PANEL_CACHE_V4 if v4 else PANEL_CACHE_V3 if v3 else
                        ((PANEL_CACHE_BOTH if fund else PANEL_CACHE_BEAT) if beat
                         else (PANEL_CACHE_FUND if fund else PANEL_CACHE)))
    spy = pd.read_parquet(data_dir / 'ohlcv' / f"{d['benchmark']}.parquet")
    cal = spy.index

    if cache.exists() and not rebuild:
        z = np.load(cache, allow_pickle=False)
        panel = {k: z[k] for k in z.files}
        panel['tickers'] = [str(t) for t in panel['tickers']]
    else:
        paths = [p for p in sorted((data_dir / 'ohlcv').glob('*.parquet'))
                 if p.stem != d['benchmark']]
        tickers = [p.stem for p in paths]
        n, k = len(cal), len(tickers)
        op = np.full((n, k), np.nan)
        hi = np.full((n, k), np.nan)
        lo = np.full((n, k), np.nan)
        cl = np.full((n, k), np.nan)
        vol = np.full((n, k), np.nan)
        liquid = np.zeros((n, k), bool)
        last_i = np.full(k, -1, dtype=np.int64)

        for j, path in enumerate(paths):
            raw = pd.read_parquet(path).reindex(cal)
            c = raw['close']
            fin = np.flatnonzero(np.isfinite(c.to_numpy()))
            if not len(fin):
                continue
            last_i[j] = int(fin[-1])
            dvol = (c * raw['volume']).rolling(d['dollar_volume_window']).mean()
            liquid[:, j] = ((c > d['min_price'])
                            & (dvol > d['min_dollar_volume'])).to_numpy()
            op[:, j] = raw['open'].to_numpy()
            hi[:, j] = raw['high'].to_numpy()
            lo[:, j] = raw['low'].to_numpy()
            cl[:, j] = c.ffill().to_numpy()
            vol[:, j] = raw['volume'].ffill().to_numpy()

        rs = np.column_stack([rs_return(cl[:, j], cfg) for j in range(k)])
        rs_ok = rs_ok_matrix(rs, liquid, cfg)

        if fund:
            eps_tab = (pd.read_parquet(data_dir / 'earnings_eps.parquet')
                       .dropna(subset=['eps']).sort_values('date'))
            by_ticker = {t: g for t, g in eps_tab.groupby('ticker')}
            for j, t in enumerate(tickers):
                g = by_ticker.get(t)
                if g is None:
                    liquid[:, j] = False
                    continue
                liquid[:, j] &= eps_gate(g['date'].to_numpy(),
                                         g['eps'].to_numpy(), cal, cfg)
            print(f'fundamentals gate: {int(liquid.sum())} liquid+qualifying '
                  f'stock-days')

        if beat:
            sp = pd.concat([pd.read_parquet(q) for q in
                            (data_dir / 'earnings_surprise.parquet',
                             data_dir / 'earnings_surprise_rest.parquet')
                            if q.exists()]).sort_values('date')
            by_beat = {t: g for t, g in sp.groupby('ticker')}
            for j, t in enumerate(tickers):
                g = by_beat.get(t)
                if g is None:
                    liquid[:, j] = False
                    continue
                liquid[:, j] &= beat_gate(g['date'].to_numpy(),
                                          g['surprise_pct'].to_numpy(), cal, cfg)
            print(f'beat gate: {int(liquid.sum())} liquid+qualifying stock-days')

        template = np.zeros((n, k), bool)
        setup = np.zeros((n, k), bool)
        trigger = np.zeros((n, k), bool)
        vol_ok = np.zeros((n, k), bool)
        trigger_moc = np.zeros((n, k), bool)
        fill_px = np.full((n, k), np.nan)
        fill_moc = np.full((n, k), np.nan)
        pivot = np.full((n, k), np.nan)
        sma50 = np.full((n, k), np.nan)
        volx = np.full((n, k), np.nan)
        rsl_hi = np.zeros((n, k), bool)
        weak = np.full((n, k), np.nan)
        rep_label = np.zeros((n, k), dtype=np.int8)
        watch = np.zeros((n, k), bool)
        gc = np.full((n, k), np.nan)
        udv = np.full((n, k), np.nan)
        spy_np = spy['close'].to_numpy()
        for j in range(k):
            bars = {'open': op[:, j], 'high': hi[:, j], 'close': cl[:, j],
                    'volume': vol[:, j]}
            s = signals(bars, cfg, rs_ok=rs_ok[:, j], liquid=liquid[:, j])
            if v4 or v5:
                rsl_hi[:, j] = rs_line_at_high(cl[:, j], spy_np)
                weak[:, j] = weak_day_score(cl[:, j], spy_np, s['base_age'])

            template[:, j] = s['template'] & liquid[:, j]
            trigger_moc[:, j] = s['trigger_moc']
            fill_moc[:, j] = s['fill_moc']
            setup[:, j] = s['setup']
            trigger[:, j] = s['trigger']
            vol_ok[:, j] = s['vol_ok']
            fill_px[:, j] = s['fill_px']
            pivot[:, j] = s['pivot']
            sma50[:, j] = pd.Series(cl[:, j]).rolling(
                cfg['minervini_trading']['sma_exit']).mean().to_numpy()
            volx[:, j] = vol[:, j] / pd.Series(vol[:, j]).rolling(
                cfg['minervini']['dryup_long']).mean().to_numpy()
            if v5:
                hl = hi[:, j] - lo[:, j]
                with np.errstate(invalid='ignore', divide='ignore'):
                    gcd = (cl[:, j] - lo[:, j]) / np.where(hl > 0, hl, np.nan)
                gc[:, j] = pd.Series((gcd > 0.5).astype(float)).rolling(20).mean().to_numpy()
                up = np.concatenate(([False], cl[1:, j] > cl[:-1, j]))
                uv = pd.Series(np.where(up, vol[:, j], 0.0)).rolling(20).sum()
                dv = pd.Series(np.where(~up, vol[:, j], 0.0)).rolling(20).sum()
                udv[:, j] = (uv / dv.replace(0, np.nan)).to_numpy()
                rep = repertoire({'close': cl[:, j], 'low': lo[:, j],
                                  'open': op[:, j], 'volume': vol[:, j]},
                                 cfg, s['setup'], s['pivot'], s['template'])
                extra = rep['trigger'] & ~trigger_moc[:, j]
                trigger_moc[:, j] |= extra
                fill_moc[extra, j] = cl[extra, j]
                rep_label[:, j] = rep['label']
                watch[:, j] = rep['armed'] | s['setup']

        blackout_days = cfg['minervini'].get('earnings_blackout_days', 0)
        if blackout_days:
            sp = pd.concat([pd.read_parquet(q) for q in
                            (data_dir / 'earnings_surprise.parquet',
                             data_dir / 'earnings_surprise_rest.parquet')
                            if q.exists()]).sort_values('date')
            by_rep = {t: g['date'].to_numpy() for t, g in sp.groupby('ticker')}
            for j, t in enumerate(tickers):
                rd = by_rep.get(t)
                if rd is None:
                    continue
                clear = ~report_within(rd, cal, blackout_days)
                setup[:, j] &= clear
                watch[:, j] &= clear
                # entry days answer to the PREVIOUS day's setup verdict
                trigger[1:, j] &= clear[:-1]
                trigger_moc[1:, j] &= clear[:-1]
            print(f'earnings blackout ({blackout_days}cd): '
                  f'{int(setup.sum())} setup days remain')

        panel = {'tickers': np.array(tickers), 'open': op, 'close': cl,
                 'sma50': sma50, 'template': template, 'setup': setup,
                 'trigger': trigger, 'vol_ok': vol_ok, 'fill_px': fill_px,
                 'trigger_moc': trigger_moc, 'fill_moc': fill_moc,
                 'volx': volx, 'pivot': pivot, 'last_i': last_i,
                 'rs': rs, 'rsl_hi': rsl_hi, 'weak': weak,
                 'rep_label': rep_label, 'watch': watch,
                 'gc': gc, 'udv': udv}
        np.savez_compressed(cache, **panel)
        panel['tickers'] = tickers

    panel['calendar'] = cal
    panel['spy_close'] = spy['close']
    panel['green'] = market_green(spy['close'])
    panel['dimmer'] = market_dimmer(spy['close'])
    return panel


def pool_by_day(pool: np.ndarray) -> list:
    """Ticker indices eligible on each day, precomputed once so the 200
    control paths do not rebuild them."""
    return [np.flatnonzero(row) for row in pool]


def simulate(panel: dict, cfg: dict, period: tuple[int, int],
             rng: np.random.Generator | None = None,
             entry_rate: float = 0.0,
             pool_days: list | None = None, moc: bool = False):
    """One portfolio path. rng=None runs the strategy; with an rng the run
    is a control (random names, next-open fills).

    moc=False: the spec's resting buy stop, filled intraday, with the
    volume verdict at the close and a failed-breakout eject.
    moc=True:  the third fill convention -- judge price and volume
    together at the close and buy market-on-close. Same base, same
    template, same exits, same everything else; no eject is needed
    because the volume is known before the trade is taken.

    Returns (trades, equity, avg invested, slot-days)."""
    tr = cfg['minervini_trading']
    cost = tr['cost_per_side']
    j0, j1 = period
    cal = panel['calendar']
    tickers = panel['tickers']
    op, cl, sma50 = panel['open'], panel['close'], panel['sma50']
    volx = panel.get('volx')
    dec_frac = tr.get('decisive_break_frac', 0.0)
    dec_vol = tr.get('decisive_volume', False)
    be_r = tr.get('breakeven_r', 0)
    be_level = 1.0 + be_r * (1.0 - tr['stop_loss'])
    protect = tr.get('protect_days', 0)           # v4 tennis-ball window
    risk_frac = tr.get('risk_per_trade', 0.0)     # v6 money engine
    pos_cap = tr.get('position_cap', 0.0)
    pyr_frac = tr.get('pyramid_frac', 0.0)
    streak_n = tr.get('streak_window', 0)
    streak_mult = tr.get('streak_mult', 1.0)
    dim_min = tr.get('dimmer_min_score', 0)
    dimmer = panel.get('dimmer')
    recent_rets: list[float] = []
    e1 = tr.get('exit_climax', False)
    e2 = tr.get('exit_vol_weak', False)
    e3 = tr.get('reentry_fast', False)
    e4 = tr.get('aging_stop', False)
    v7c = cfg.get('minervini_v7', {})
    sell_at = tr.get('strength_sell_at', 0.0)
    sell_frac = tr.get('strength_sell_frac', 0.0)
    mom = tr.get('momentum_sell', False)          # §13 momentum-conditioned
    v9c = cfg.get('minervini_v9', {})
    vel_gain = v9c.get('velocity_gain', 0.0)
    vel_days = v9c.get('velocity_days', 0)
    vel_hold = v9c.get('velocity_hold_days', 0)
    cx_gain = v9c.get('climax_min_gain', 0.0)
    cx_day = v9c.get('climax_day_ret', 0.0)
    cx_frac = v9c.get('climax_sell_frac', 0.5)
    rank_sel = tr.get('rank_selection', False)
    if moc:
        fill_px, trigger = panel['fill_moc'], panel['trigger_moc']
    else:
        fill_px, trigger = panel['fill_px'], panel['trigger']
    vol_ok = panel['vol_ok']
    last_i, green = panel['last_i'], panel['green']
    is_control = rng is not None
    if pool_days is None:
        pool_days = pool_by_day(panel['template'] if is_control
                                else panel['setup'])

    park = tr.get('park_spy', False)
    spy_f = panel['spy_close'].pct_change().fillna(0.0).to_numpy() + 1.0
    cash, eq_prev = START_EQUITY, START_EQUITY
    positions: dict[int, dict] = {}
    orders: dict[int, int] = {}          # ticker -> the one day it is live
    cooldown: dict[int, int] = {}
    trades: list[dict] = []
    days = cal[j0:j1 + 1]
    equity = pd.Series(np.nan, index=days)
    invested: list[float] = []
    slot_days = 0

    def close_out(j: int, i: int, pos: dict, px: float, reason: str) -> float:
        trades.append({
            'ticker': tickers[j], 'entry_date': pos['entry_date'],
            'exit_date': cal[i], 'entry_px': pos['entry_px'], 'exit_px': px,
            'days_held': i - pos['entry_i'],
            'ret_net': px * (1 - cost) / (pos['entry_px'] * (1 + cost)) - 1,
            'exit_reason': reason})
        cd = tr['reentry_cooldown']
        if e3 and reason != 'stop':
            cd = v7c['reentry_fast_days']
        cooldown[j] = i + cd
        recent_rets.append(px * (1 - cost) / (pos['entry_px'] * (1 + cost)) - 1)
        return pos['shares'] * px * (1 - cost)

    for i in range(j0, j1 + 1):
        if park:
            cash *= spy_f[i]     # idle balance rides SPY (flow costs unmodelled)
        # 1. exits fill at the open, freeing capital before any entry
        for j in [j for j, p in positions.items() if p['exit_reason']]:
            pos = positions.pop(j)
            px = op[i, j] if np.isfinite(op[i, j]) else cl[i, j]
            cash += close_out(j, i, pos, px, pos['exit_reason'])

        # 1a2. v6 pyramiding: the scheduled add fills at the open
        for j in [j for j, p in positions.items() if p.get('add_due')]:
            pos = positions[j]
            pos['add_due'] = False
            px = op[i, j] if np.isfinite(op[i, j]) else cl[i, j]
            add = np.floor(pos.get('shares0', pos['shares']) * pyr_frac)
            outflow = add * px * (1 + cost)
            if add >= 1 and outflow <= cash:
                # blended entry price keeps every exit rule consistent
                tot = pos['shares'] + add
                pos['entry_px'] = (pos['entry_px'] * pos['shares']
                                   + px * add) / tot
                pos['shares'] = tot
                cash -= outflow
                pos['added'] = True

        # 1b. v4 strength sales: half out at the open, rest keeps running
        for j in [j for j, p in positions.items() if p.get('sell_half')]:
            pos = positions[j]
            pos['sell_half'] = False
            half = np.floor(pos['shares'] / 2)
            px = op[i, j] if np.isfinite(op[i, j]) else cl[i, j]
            if half >= 1:
                cash += half * px * (1 - cost)
                trades.append({
                    'ticker': tickers[j], 'entry_date': pos['entry_date'],
                    'exit_date': cal[i], 'entry_px': pos['entry_px'],
                    'exit_px': px, 'days_held': i - pos['entry_i'],
                    'ret_net': px * (1 - cost)
                               / (pos['entry_px'] * (1 + cost)) - 1,
                    'exit_reason': 'strength'})
                pos['shares'] -= half
                pos['half_sold'] = True

        # 2. yesterday's resting orders: the strategy's fill happens
        #    intraday at the buy stop, the control's at the open. Under
        #    moc the fill waits for step 3b, at the close.
        for j in ([] if (moc and not is_control)
                  else [j for j, day in orders.items() if day == i]):
            orders.pop(j)
            px = fill_px[i, j] if not is_control else op[i, j]
            if not is_control and not trigger[i, j]:
                continue                      # never touched, or too extended
            if j in positions or len(positions) >= tr['max_positions'] \
                    or not np.isfinite(px):
                continue
            frac = tr['equal_weight_fraction']
            if risk_frac:
                frac = min(risk_frac / (1.0 - tr['stop_loss']), pos_cap)
                if streak_n and len(recent_rets) >= streak_n                         and sum(recent_rets[-streak_n:]) < 0:
                    frac *= streak_mult
                if dimmer is not None:
                    frac *= dimmer[i] / 4.0
            shares = np.floor(frac * eq_prev / px)
            outflow = shares * px * (1 + cost)
            if shares < 1 or outflow > cash:
                continue
            positions[j] = {'shares': shares, 'entry_px': px, 'entry_i': i,
                            'entry_date': cal[i], 'exit_reason': None,
                            'shares0': shares}
            cash -= outflow

        # 3. decisions at the close
        for j, pos in positions.items():
            c = cl[i, j]
            # §13: a position that ran +20% inside 15 days is held WHOLE
            # for 40 days -- stop, breakeven and the climax partial only
            vel = bool(mom and pos.get('velocity')
                       and i - pos['entry_i'] < vel_hold)
            if i >= last_i[j] and last_i[j] < len(cal) - 1:
                pos['exit_reason'] = 'delisted'
            elif (pos['entry_i'] == i and not is_control and not moc
                    and not vol_ok[i, j]):
                pos['exit_reason'] = 'failed_breakout'
            elif c <= tr['stop_loss'] * pos['entry_px']:
                pos['exit_reason'] = 'stop'
            elif e4 and i - pos['entry_i'] >= v7c['aging_stop_day']                     and c <= v7c['aging_stop_level'] * pos['entry_px']:
                pos['exit_reason'] = 'aged'
            elif e1 and c >= v7c['climax_min_gain'] * pos['entry_px']                     and i > pos['entry_i']                     and c / cl[i - 1, j] - 1 >= v7c['climax_day_ret']:
                pos['exit_reason'] = 'climax'
            elif protect and i - pos['entry_i'] < protect:
                pass          # v4 tennis-ball window: only the stop may sell
            elif protect and i - pos['entry_i'] == protect and not vel \
                    and c < pos['entry_px'] and not pos.get('recovered'):
                # never bounced back over its post-entry high: an egg
                pos['exit_reason'] = 'egg'
            elif pos.get('be') and c <= pos['entry_px']:
                # v3: a position that reached 2R may not become a loss
                pos['exit_reason'] = 'breakeven'
            elif vel:
                pass          # §13: the 40-day hold suspends the trend exit
            elif np.isfinite(sma50[i, j]) and c < sma50[i, j] and (
                    (volx is not None and np.isfinite(volx[i, j])
                     and volx[i, j] > 1.0) if e2 else (
                    c < (1.0 - dec_frac) * sma50[i, j]
                    or (dec_vol and volx is not None
                        and np.isfinite(volx[i, j]) and volx[i, j] > 1.0))):
                # v2: any close below the SMA50 (dec_frac 0, dec_vol off)
                # v3: only a DECISIVE break — >1% below, or on volume
                pos['exit_reason'] = 'sma'
            if be_r and not pos.get('be') and c >= be_level * pos['entry_px']:
                pos['be'] = True
                if pyr_frac and not pos.get('added') and not pos['exit_reason']:
                    pos['add_due'] = True

            # §13 momentum-conditioned selling. Two rules, both read at
            # this close and both causal there: the velocity flag needs
            # only today's close, the climax partial needs today's gain
            # and the gains already seen, so it can sell AT the close.
            if mom and i > pos['entry_i']:
                if not pos.get('velocity') \
                        and i - pos['entry_i'] <= vel_days \
                        and c >= vel_gain * pos['entry_px']:
                    pos['velocity'] = True        # a jackpot-cohort runner
                prev = cl[i - 1, j]
                day_ret = (c / prev - 1) if (np.isfinite(prev) and prev > 0) \
                    else -np.inf
                largest = day_ret > pos.get('max_day', -np.inf)
                if largest and np.isfinite(day_ret):
                    pos['max_day'] = day_ret
                if (largest and day_ret >= cx_day
                        and c >= cx_gain * pos['entry_px']
                        and not pos.get('half_sold')
                        and not pos.get('sell_half')
                        and not pos['exit_reason']):
                    # sell into the climax: the run's largest up-day while
                    # well extended. Only positions still held whole.
                    part = np.floor(pos['shares'] * cx_frac)
                    if part >= 1:
                        cash += part * c * (1 - cost)
                        trades.append({
                            'ticker': tickers[j],
                            'entry_date': pos['entry_date'],
                            'exit_date': cal[i], 'entry_px': pos['entry_px'],
                            'exit_px': c, 'days_held': i - pos['entry_i'],
                            'ret_net': c * (1 - cost)
                                       / (pos['entry_px'] * (1 + cost)) - 1,
                            'exit_reason': 'climax_partial'})
                        pos['shares'] -= part
                        pos['half_sold'] = True
            # today's flag counts for today's partial: a name that first
            # reaches +20% ON day 15 is fast, not a slow winner
            vel = bool(mom and pos.get('velocity')
                       and i - pos['entry_i'] < vel_hold)
            # v4 bookkeeping: tennis-ball recovery + the strength sale.
            # A pullback = any close under the running post-entry peak;
            # recovery = a later close above that peak.
            if protect:
                peak = pos.get('peak2', c)
                if c > peak:
                    if pos.get('dipped'):
                        pos['recovered'] = True
                    pos['peak2'] = c
                elif c < peak:
                    pos['dipped'] = True
                if sell_at and not pos.get('half_sold') \
                        and not pos.get('sell_half') \
                        and i - pos['entry_i'] >= protect \
                        and c >= sell_at * pos['entry_px'] \
                        and not pos['exit_reason'] and not vel:
                    # §13: `not vel` -- a stock that got here FAST keeps
                    # the whole position; only slow winners sell half
                    pos['sell_half'] = True

        # 3b. market-on-close entries: price above the pivot AND volume
        #     confirmed, both read at this close, bought at this close
        if moc and not is_control:
            due = [j for j, day in orders.items() if day == i]
            adaptive_frac = None
            if tr.get('adaptive'):
                # v8 (user method): split the free budget under the 80%
                # exposure cap across TODAY'S fillable signals — few
                # signals bet big (cap 20% each), many signals shrink.
                # Existing positions are never touched to fund new ones.
                fillable = [j for j in due
                            if trigger[i, j] and j not in positions
                            and np.isfinite(fill_px[i, j])]
                k_eff = min(len(fillable),
                            max(0, tr['max_positions'] - len(positions)))
                held_now = sum(p['shares'] * cl[i, jj]
                               for jj, p in positions.items())
                budget = tr['adaptive_cap'] * eq_prev - held_now
                if k_eff > 0 and budget > 0:
                    adaptive_frac = min(tr['adaptive_max_single'],
                                        budget / k_eff / eq_prev)
            for j in due:
                orders.pop(j)
                px = fill_px[i, j]
                if (not trigger[i, j] or j in positions
                        or not np.isfinite(px)
                        or len(positions) >= tr['max_positions']):
                    continue
                if tr.get('adaptive'):
                    if adaptive_frac is None:
                        continue          # cap reached: skip, never liquidate
                    frac = adaptive_frac
                else:
                    frac = tr['equal_weight_fraction']
                if risk_frac:
                    frac = min(risk_frac / (1.0 - tr['stop_loss']), pos_cap)
                    if streak_n and len(recent_rets) >= streak_n                             and sum(recent_rets[-streak_n:]) < 0:
                        frac *= streak_mult
                    if dimmer is not None:
                        frac *= dimmer[i] / 4.0
                shares = np.floor(frac * eq_prev / px)
                outflow = shares * px * (1 + cost)
                if shares < 1 or outflow > cash:
                    continue
                positions[j] = {'shares': shares, 'entry_px': px, 'entry_i': i,
                                'entry_date': cal[i], 'exit_reason': None,
                                'shares0': shares}
                cash -= outflow

        orders = {j: day for j, day in orders.items() if day > i}

        # 4. place tomorrow's orders
        exiting = sum(1 for p in positions.values() if p['exit_reason'])
        slots = tr['max_positions'] - (len(positions) - exiting) - len(orders)
        market_ok = (dimmer[i] >= dim_min) if (dimmer is not None and dim_min)             else green[i]
        if slots > 0 and market_ok and i + 1 < len(cal):
            slot_days += slots
            day_pool = pool_days[i]

            def usable(j: int) -> bool:
                return (j not in positions and j not in orders
                        and cooldown.get(j, -1) <= i and last_i[j] > i)

            if not is_control:
                if tr.get('repertoire'):
                    craft = tr.get('craft_rank', False)
                    # v5: an order costs nothing until it fills; watch every
                    # armed name, ranked, and let max_positions bind at entry
                    rsl, wk, rsv = panel['rsl_hi'], panel['weak'], panel['rs']
                    gc_, udv_ = panel.get('gc'), panel.get('udv')

                    def key(j):
                        base = [-int(rsl[i, j]),
                                -(wk[i, j] if np.isfinite(wk[i, j])
                                  else -np.inf)]
                        if craft and gc_ is not None:
                            base += [-(udv_[i, j] if np.isfinite(udv_[i, j])
                                       else -np.inf),
                                     -(gc_[i, j] if np.isfinite(gc_[i, j])
                                       else -np.inf)]
                        base += [-(rsv[i, j] if np.isfinite(rsv[i, j])
                                   else -np.inf), tickers[j]]
                        return tuple(base)
                    take = [j for j in sorted(day_pool, key=key)
                            if usable(j)][:100]
                elif rank_sel:
                    # v4 (spec 10.2): fill slots by strength, not alphabet —
                    # RS-line at a high first, then holds-up-when-weak,
                    # then raw RS; ticker only as the final determinism tie
                    rsl, wk, rsv = panel['rsl_hi'], panel['weak'], panel['rs']
                    take = [j for j in sorted(
                        day_pool,
                        key=lambda j: (-int(rsl[i, j]),
                                       -(wk[i, j] if np.isfinite(wk[i, j])
                                         else -np.inf),
                                       -(rsv[i, j] if np.isfinite(rsv[i, j])
                                         else -np.inf),
                                       tickers[j]))
                        if usable(j)][:slots]
                else:
                    # v2/v3: alphabetical, the only tie-break those specs
                    # leave open (RS is a membership filter there)
                    take = [j for j in sorted(day_pool,
                                              key=lambda j: tickers[j])
                            if usable(j)][:slots]
            else:
                draws = int((rng.random(slots) < entry_rate).sum())
                take = []
                if draws and len(day_pool):
                    for j in day_pool[rng.integers(0, len(day_pool), 4 * draws)]:
                        if usable(j) and j not in take:
                            take.append(j)
                            if len(take) == draws:
                                break
            for j in take:
                orders[int(j)] = i + 1

        held = sum(p['shares'] * cl[i, j] for j, p in positions.items())
        eq_prev = cash + held
        equity.iloc[i - j0] = eq_prev
        invested.append(held / eq_prev if eq_prev > 0 else 0.0)

    for j, pos in list(positions.items()):
        positions.pop(j)
        cash += close_out(j, j1, pos, cl[j1, j], 'period_end')
    equity.iloc[-1] = cash
    return pd.DataFrame(trades), equity, float(np.mean(invested)), slot_days


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    results.mkdir(exist_ok=True)
    fund = '--fund' in sys.argv
    beat = '--beat' in sys.argv
    v7 = '--v7' in sys.argv
    v6 = '--v6' in sys.argv
    v9 = '--v9' in sys.argv          # §13 momentum-conditioned selling
    v5 = '--v5' in sys.argv or v6 or v7 or v9
    v4 = '--v4' in sys.argv or v5
    v3 = '--v3' in sys.argv or v4
    if v9:
        cfg = apply_v9(cfg)
    elif v6:
        cfg = apply_v6(cfg)
    elif v5:
        cfg = apply_v5(cfg)
    elif v4:
        cfg = apply_v4(cfg)
    elif v3:
        cfg = apply_v3(cfg)
    for flag, key in (('--e1', 'exit_climax'), ('--e2', 'exit_vol_weak'),
                      ('--e3', 'reentry_fast'), ('--e4', 'aging_stop'),
                      ('--park', 'park_spy'), ('--craft', 'craft_rank'),
                      ('--adaptive', 'adaptive')):
        if flag in sys.argv or (v7 and flag.startswith('--e')):
            cfg['minervini_trading'][key] = True
    for a in sys.argv:
        if a.startswith('--size='):
            cfg['minervini_trading']['equal_weight_fraction'] = float(a[7:])
    if cfg['minervini_trading'].get('adaptive'):
        for k, v in cfg['minervini_v8'].items():
            cfg['minervini_trading'][k] = v
    panel = build_panel(cfg, rebuild='--rebuild' in sys.argv, fund=fund,
                        beat=beat, v3=v3 and not v4, v4=v4 and not v5, v5=v5)
    cal = panel['calendar']

    print(f'panel: {len(panel["tickers"])} tickers, '
          f'{int(panel["template"].sum())} template stock-days, '
          f'{int(panel["setup"].sum())} setup days, '
          f'{int(panel["trigger"].sum())} buy-stop fills '
          f'({int((panel["trigger"] & panel["vol_ok"]).sum())} volume-confirmed)')

    today = str(cal[-1].date())
    periods = {}
    for name, a, b in [('dev', bt['start'], bt['dev_end']),
                       ('test', bt['test_start'], today)]:
        j0 = int(cal.searchsorted(pd.Timestamp(a)))
        j1 = int(cal.searchsorted(pd.Timestamp(b), side='right')) - 1
        periods[name] = (j0, j1)

    moc = '--moc' in sys.argv
    ab = ''.join(f[2:] for f in ('--e1','--e2','--e3','--e4','--park','--craft') if f in sys.argv)
    for a in sys.argv:
        if a.startswith('--size='):
            ab += 's' + a[7:].replace('0.','')
    tag = (('v9' if v9 else 'v7' if v7 else ('v5_' + ab) if ab else 'v6' if v6 else 'v5' if v5 else 'v4' if v4 else 'v3' if v3 else 'v2') + ('_moc' if moc else '')
           + ('_fund' if fund else '') + ('_beat' if beat else ''))
    if moc:
        print('ENTRY: market-on-close (third fill convention) — '
              f'{int(panel["trigger_moc"].sum())} entries available')
    n_ctl = cfg['minervini_trading']['n_controls']
    strat_pool = panel['watch'] if v5 and 'watch' in panel else panel['setup']
    setup_days = pool_by_day(strat_pool)
    tmpl_days = pool_by_day(panel['template'])
    summary, curves = {}, {}
    for pname, period in periods.items():
        trades, equity, avg_inv, slot_days = simulate(
            panel, cfg, period, pool_days=setup_days, moc=moc)
        m = metrics(trades, equity, avg_inv)
        rate = len(trades) / slot_days if slot_days else 0.0
        trades.to_csv(results / f'minervini_{tag}_{pname}_trades.csv', index=False)
        curves[pname] = equity

        ctl_tot, ctl_n = [], []
        for s in range(n_ctl):
            ct, ce, _, _ = simulate(panel, cfg, period,
                                    rng=np.random.default_rng(s),
                                    entry_rate=rate, pool_days=tmpl_days,
                                    moc=moc)
            ctl_tot.append(ce.iloc[-1] / ce.iloc[0] - 1)
            ctl_n.append(len(ct))
        ctl_tot = np.array(ctl_tot)
        m['entry_rate'] = rate
        m['ctl_n_trades_median'] = float(np.median(ctl_n))
        m['ctl_median_total'] = float(np.median(ctl_tot))
        m['pct_vs_controls'] = float((m['total_return'] > ctl_tot).mean())
        summary[pname] = m
        pd.DataFrame({'seed': np.arange(n_ctl), 'total_return': ctl_tot,
                      'n_trades': ctl_n}).to_csv(
            results / f'minervini_{tag}_controls_{pname}.csv', index=False)

        print(f'\n=== {pname} {cal[period[0]].date()} .. '
              f'{cal[period[1]].date()} ===')
        print({k: (round(v, 4) if isinstance(v, float) else v)
               for k, v in m.items()})
        if len(trades):
            print('exits:', trades['exit_reason'].value_counts().to_dict())

        plt.figure(figsize=(11, 6))
        plt.hist(ctl_tot * 100, bins=30, color='lightsteelblue',
                 label=f'{n_ctl} random template-passing controls')
        plt.axvline(m['total_return'] * 100, color='crimson',
                    label=f'MINERVINI v2 ({m["total_return"]:+.0%}, beats '
                          f'{m["pct_vs_controls"]:.0%})')
        plt.xlabel('total return, %')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.title(f'Minervini v2 vs random-template controls, {pname}')
        plt.tight_layout()
        plt.savefig(results / f'minervini_{tag}_controls_{pname}.png', dpi=120)
        plt.close()

        spy = panel['spy_close'].iloc[period[0]:period[1] + 1]
        plt.figure(figsize=(11, 6))
        plt.plot(equity.index, equity / equity.iloc[0], label='MINERVINI v2')
        plt.plot(spy.index, spy / spy.iloc[0], '--', color='gray',
                 label='SPY (context)')
        plt.yscale('log')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.title(f'Minervini v2 Stage-2 breakouts, {pname}')
        plt.tight_layout()
        plt.savefig(results / f'minervini_{tag}_equity_{pname}.png', dpi=120)
        plt.close()

    pd.DataFrame(summary).T.to_csv(results / f'minervini_{tag}_summary.csv')
    print(f'\ntables and charts -> {results}/minervini_{tag}_*')


if __name__ == '__main__':
    main()
