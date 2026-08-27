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

Run: python minervini_backtest.py            # audit + controls
     python minervini_backtest.py --rebuild  # ignore the panel cache
"""

import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, metrics
from minervini import beat_gate, eps_gate, rs_ok_matrix, rs_return, signals

START_EQUITY = 100_000.0
PANEL_CACHE = 'minervini_panel_v2.npz'
PANEL_CACHE_FUND = 'minervini_panel_v2_fund.npz'
PANEL_CACHE_BEAT = 'minervini_panel_v2_beat.npz'
PANEL_CACHE_BOTH = 'minervini_panel_v2_both.npz'


def market_green(spy_close: pd.Series) -> np.ndarray:
    """The gate we already trust: SPY above its 200d SMA (trend) and 20d
    realised vol at or below its trailing 756d 90th percentile (calm)."""
    trend = spy_close > spy_close.rolling(200).mean()
    v20 = spy_close.pct_change().rolling(20).std()
    calm = ~(v20 > v20.rolling(756).quantile(0.90))
    return (trend & calm).to_numpy()


def build_panel(cfg: dict, rebuild: bool = False, fund: bool = False,
                beat: bool = False) -> dict:
    """Per-day signal matrices (days x tickers) on the SPY calendar.

    fund=True additionally requires the SEPA pillar-2 EPS gate (spec
    section 8) on every setup day, and narrows the control pool the same
    way, so the comparison isolates the fundamentals filter alone."""
    d = cfg['data']
    data_dir = ROOT / d['cache_dir']
    cache = data_dir / ((PANEL_CACHE_BOTH if fund else PANEL_CACHE_BEAT) if beat
                        else (PANEL_CACHE_FUND if fund else PANEL_CACHE))
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
        for j in range(k):
            bars = {'open': op[:, j], 'high': hi[:, j], 'close': cl[:, j],
                    'volume': vol[:, j]}
            s = signals(bars, cfg, rs_ok=rs_ok[:, j], liquid=liquid[:, j])
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

        panel = {'tickers': np.array(tickers), 'open': op, 'close': cl,
                 'sma50': sma50, 'template': template, 'setup': setup,
                 'trigger': trigger, 'vol_ok': vol_ok, 'fill_px': fill_px,
                 'trigger_moc': trigger_moc, 'fill_moc': fill_moc,
                 'pivot': pivot, 'last_i': last_i}
        np.savez_compressed(cache, **panel)
        panel['tickers'] = tickers

    panel['calendar'] = cal
    panel['spy_close'] = spy['close']
    panel['green'] = market_green(spy['close'])
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
        cooldown[j] = i + tr['reentry_cooldown']
        return pos['shares'] * px * (1 - cost)

    for i in range(j0, j1 + 1):
        # 1. exits fill at the open, freeing capital before any entry
        for j in [j for j, p in positions.items() if p['exit_reason']]:
            pos = positions.pop(j)
            px = op[i, j] if np.isfinite(op[i, j]) else cl[i, j]
            cash += close_out(j, i, pos, px, pos['exit_reason'])

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
            shares = np.floor(tr['equal_weight_fraction'] * eq_prev / px)
            outflow = shares * px * (1 + cost)
            if shares < 1 or outflow > cash:
                continue
            positions[j] = {'shares': shares, 'entry_px': px, 'entry_i': i,
                            'entry_date': cal[i], 'exit_reason': None}
            cash -= outflow

        # 3. decisions at the close
        for j, pos in positions.items():
            c = cl[i, j]
            if i >= last_i[j] and last_i[j] < len(cal) - 1:
                pos['exit_reason'] = 'delisted'
            elif (pos['entry_i'] == i and not is_control and not moc
                    and not vol_ok[i, j]):
                pos['exit_reason'] = 'failed_breakout'
            elif c <= tr['stop_loss'] * pos['entry_px']:
                pos['exit_reason'] = 'stop'
            elif np.isfinite(sma50[i, j]) and c < sma50[i, j]:
                pos['exit_reason'] = 'sma'

        # 3b. market-on-close entries: price above the pivot AND volume
        #     confirmed, both read at this close, bought at this close
        if moc and not is_control:
            for j in [j for j, day in orders.items() if day == i]:
                orders.pop(j)
                px = fill_px[i, j]
                if (not trigger[i, j] or j in positions
                        or not np.isfinite(px)
                        or len(positions) >= tr['max_positions']):
                    continue
                shares = np.floor(tr['equal_weight_fraction'] * eq_prev / px)
                outflow = shares * px * (1 + cost)
                if shares < 1 or outflow > cash:
                    continue
                positions[j] = {'shares': shares, 'entry_px': px, 'entry_i': i,
                                'entry_date': cal[i], 'exit_reason': None}
                cash -= outflow

        orders = {j: day for j, day in orders.items() if day > i}

        # 4. place tomorrow's orders
        exiting = sum(1 for p in positions.values() if p['exit_reason'])
        slots = tr['max_positions'] - (len(positions) - exiting) - len(orders)
        if slots > 0 and green[i] and i + 1 < len(cal):
            slot_days += slots
            day_pool = pool_days[i]

            def usable(j: int) -> bool:
                return (j not in positions and j not in orders
                        and cooldown.get(j, -1) <= i and last_i[j] > i)

            if not is_control:
                # more setups than free slots: alphabetical, the only
                # tie-break the spec leaves open (RS is a membership
                # filter, never a slot priority)
                take = [j for j in sorted(day_pool, key=lambda j: tickers[j])
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
    panel = build_panel(cfg, rebuild='--rebuild' in sys.argv, fund=fund,
                        beat=beat)
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
    tag = (('v2_moc' if moc else 'v2') + ('_fund' if fund else '')
           + ('_beat' if beat else ''))
    if moc:
        print('ENTRY: market-on-close (third fill convention) — '
              f'{int(panel["trigger_moc"].sum())} entries available')
    n_ctl = cfg['minervini_trading']['n_controls']
    setup_days = pool_by_day(panel['setup'])
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
