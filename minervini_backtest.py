"""Minervini Stage-2 breakout — portfolio audit (MINERVINI_SPEC.md).

Zero tunables: every constant was frozen in the spec and lives in the
`minervini:` / `minervini_trading:` blocks of config.yaml. Nothing here
selects anything, so both periods are reported and the bar is "positive
and non-collapsed in BOTH".

Entries: trend template (9 conditions, RS ranked against the liquid
universe that day) + mechanical VCP + close above the pivot on >= 1.5x
the 50-day mean volume, market light green. Fill at the next open.
Exits: close <= 0.92 x entry, or close < SMA50 (trend death) -> next open.
Mechanics copied from lppl_dip2: 10 slots, 10% equal weight, whole shares,
0.2% per side, 20-day re-entry cooldown.

Controls (the actual science): 200 random portfolios that buy random
template-passing stocks on random days under the same slots, cooldown,
market light and exits. Their entry rate is matched to the strategy's own
(one draw per free slot per green day, probability = the strategy's
realised entries per free-slot-green-day), so the only difference is
WHICH stock on WHICH day — i.e. the VCP/pivot timing.

More candidates than free slots are allocated alphabetically: the spec
rejects RS as a slot-priority rule (the `_rs` experiment failed OOS), and
no other ranking is pre-registered.

Run: python minervini_backtest.py            # audit + controls
     python minervini_backtest.py --cases    # SPHR / SMCI trigger history
     python minervini_backtest.py --rebuild  # ignore the panel cache
"""

import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, metrics
from minervini import rs_ok_matrix, rs_return, signals

START_EQUITY = 100_000.0
PANEL_CACHE = 'minervini_panel.npz'


def market_green(spy_close: pd.Series) -> np.ndarray:
    """The gate we already trust: SPY above its 200d SMA (trend) and 20d
    realised vol at or below its trailing 756d 90th percentile (calm)."""
    trend = spy_close > spy_close.rolling(200).mean()
    v20 = spy_close.pct_change().rolling(20).std()
    calm = ~(v20 > v20.rolling(756).quantile(0.90))
    return (trend & calm).to_numpy()


def build_panel(cfg: dict, rebuild: bool = False) -> dict:
    """Per-day signal matrices (days x tickers) on the SPY calendar."""
    d = cfg['data']
    data_dir = ROOT / d['cache_dir']
    cache = data_dir / PANEL_CACHE
    spy = pd.read_parquet(data_dir / 'ohlcv' / f"{d['benchmark']}.parquet")
    cal = spy.index

    if cache.exists() and not rebuild:
        z = np.load(cache, allow_pickle=False)
        panel = {k: z[k] for k in z.files}
        panel['tickers'] = [str(t) for t in panel['tickers']]
        panel['calendar'] = cal
        panel['spy_close'] = spy['close']
        panel['green'] = market_green(spy['close'])
        return panel

    paths = [p for p in sorted((data_dir / 'ohlcv').glob('*.parquet'))
             if p.stem != d['benchmark']]
    tickers = [p.stem for p in paths]
    n, k = len(cal), len(tickers)
    op = np.full((n, k), np.nan)
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
        dollar_vol = (c * raw['volume']).rolling(d['dollar_volume_window']).mean()
        liquid[:, j] = ((c > d['min_price'])
                        & (dollar_vol > d['min_dollar_volume'])).to_numpy()
        op[:, j] = raw['open'].to_numpy()
        cl[:, j] = c.ffill().to_numpy()
        vol[:, j] = raw['volume'].ffill().to_numpy()

    rs = np.column_stack([rs_return(cl[:, j], cfg) for j in range(k)])
    rs_ok = rs_ok_matrix(rs, liquid, cfg)

    template = np.zeros((n, k), bool)
    setup = np.zeros((n, k), bool)
    trigger = np.zeros((n, k), bool)
    pivot = np.full((n, k), np.nan)
    sma50 = np.full((n, k), np.nan)
    for j in range(k):
        s = signals(cl[:, j], vol[:, j], cfg, rs_ok=rs_ok[:, j],
                    liquid=liquid[:, j])
        template[:, j] = s['template'] & liquid[:, j]
        setup[:, j] = s['setup']
        trigger[:, j] = s['trigger']
        pivot[:, j] = s['pivot']
        sma50[:, j] = pd.Series(cl[:, j]).rolling(
            cfg['minervini_trading']['sma_exit']).mean().to_numpy()

    panel = {'tickers': np.array(tickers), 'open': op, 'close': cl,
             'sma50': sma50, 'template': template, 'setup': setup,
             'trigger': trigger, 'pivot': pivot, 'last_i': last_i}
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
             pool_days: list | None = None) -> tuple[pd.DataFrame, pd.Series, float, int]:
    """One portfolio path. rng=None runs the strategy; with an rng the run
    is a control: entries are drawn at random from that day's
    template-passing names, one draw per free slot at probability
    `entry_rate`. Returns (trades, equity, avg invested, free-slot-green-day
    count)."""
    tr = cfg['minervini_trading']
    cost = tr['cost_per_side']
    j0, j1 = period
    cal = panel['calendar']
    tickers = panel['tickers']
    op, cl, sma50 = panel['open'], panel['close'], panel['sma50']
    last_i, green = panel['last_i'], panel['green']
    if pool_days is None:
        pool_days = pool_by_day(
            panel['template'] if rng is not None else panel['trigger'])

    cash, eq_prev = START_EQUITY, START_EQUITY
    positions: dict[int, dict] = {}
    pending: dict[int, int] = {}      # ticker index -> fill day
    cooldown: dict[int, int] = {}
    trades: list[dict] = []
    days = cal[j0:j1 + 1]
    equity = pd.Series(np.nan, index=days)
    invested: list[float] = []
    slot_days = 0

    def close_out(j: int, i: int, pos: dict, px: float, reason: str) -> float:
        proceeds = pos['shares'] * px * (1 - cost)
        trades.append({
            'ticker': tickers[j], 'entry_date': pos['entry_date'],
            'exit_date': cal[i], 'entry_px': pos['entry_px'], 'exit_px': px,
            'days_held': i - pos['entry_i'],
            'ret_net': px * (1 - cost) / (pos['entry_px'] * (1 + cost)) - 1,
            'exit_reason': reason})
        cooldown[j] = i + tr['reentry_cooldown']
        return proceeds

    for i in range(j0, j1 + 1):
        # fills at the open: exits first, then the entries scheduled yesterday
        for j in [j for j, p in positions.items() if p['exit_reason']]:
            pos = positions.pop(j)
            px = op[i, j] if np.isfinite(op[i, j]) else cl[i, j]
            cash += close_out(j, i, pos, px, pos['exit_reason'])

        for j in [j for j, fill in pending.items() if fill <= i]:
            pending.pop(j)
            px = op[i, j]
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

        # decisions at the close
        for j, pos in positions.items():
            c = cl[i, j]
            if i >= last_i[j] and last_i[j] < len(cal) - 1:
                pos['exit_reason'] = 'delisted'
            elif c <= tr['stop_loss'] * pos['entry_px']:
                pos['exit_reason'] = 'stop'
            elif np.isfinite(sma50[i, j]) and c < sma50[i, j]:
                pos['exit_reason'] = 'sma'

        exiting = sum(1 for p in positions.values() if p['exit_reason'])
        slots = tr['max_positions'] - (len(positions) - exiting) - len(pending)
        if slots > 0 and green[i] and i + 1 < len(cal):
            slot_days += slots
            day_pool = pool_days[i]

            def usable(j: int) -> bool:
                return (j not in positions and j not in pending
                        and cooldown.get(j, -1) <= i and last_i[j] > i
                        and np.isfinite(op[i + 1, j]))

            if rng is None:
                # more triggers than free slots: alphabetical, the only
                # tie-break the spec leaves open (RS is a membership filter,
                # never a slot priority)
                take = [j for j in sorted(day_pool, key=lambda j: tickers[j])
                        if usable(j)][:slots]
            else:
                # one draw per free slot at the strategy's own realised rate,
                # then a random name from that day's template-passing pool
                # (sampled with replacement and de-duplicated by `usable`;
                # the pool is ~2 orders of magnitude larger than the draws)
                draws = int((rng.random(slots) < entry_rate).sum())
                take = []
                if draws and len(day_pool):
                    for j in day_pool[rng.integers(0, len(day_pool), 4 * draws)]:
                        if usable(j) and j not in take:
                            take.append(j)
                            if len(take) == draws:
                                break
            for j in take:
                pending[int(j)] = i + 1

        held = sum(p['shares'] * cl[i, j] for j, p in positions.items())
        eq_prev = cash + held
        equity.iloc[i - j0] = eq_prev
        invested.append(held / eq_prev if eq_prev > 0 else 0.0)

    for j, pos in list(positions.items()):
        positions.pop(j)
        cash += close_out(j, j1, pos, cl[j1, j], 'period_end')
    equity.iloc[-1] = cash
    return (pd.DataFrame(trades), equity, float(np.mean(invested)), slot_days)


def case_studies(panel: dict, cfg: dict) -> None:
    cal = panel['calendar']
    for t in ('SPHR', 'SMCI'):
        if t not in panel['tickers']:
            print(f'{t}: not in the universe')
            continue
        j = panel['tickers'].index(t)
        trig = np.flatnonzero(panel['trigger'][:, j])
        setups = int(panel['setup'][:, j].sum())
        print(f'\n{t}: {len(trig)} triggers, {setups} setup days, '
              f'{int(panel["template"][:, j].sum())} template days')
        for i in trig:
            print(f'  {cal[i].date()}  close {panel["close"][i, j]:8.2f}  '
                  f'pivot {panel["pivot"][i, j]:8.2f}  '
                  f'green {bool(panel["green"][i])}')


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    results.mkdir(exist_ok=True)
    panel = build_panel(cfg, rebuild='--rebuild' in sys.argv)
    cal = panel['calendar']

    if '--cases' in sys.argv:
        case_studies(panel, cfg)
        return

    print(f'panel: {len(panel["tickers"])} tickers, '
          f'{int(panel["template"].sum())} template stock-days, '
          f'{int(panel["setup"].sum())} setup days, '
          f'{int(panel["trigger"].sum())} breakout triggers')

    today = str(cal[-1].date())
    periods = {}
    for name, a, b in [('dev', bt['start'], bt['dev_end']),
                       ('test', bt['test_start'], today)]:
        j0 = int(cal.searchsorted(pd.Timestamp(a)))
        j1 = int(cal.searchsorted(pd.Timestamp(b), side='right')) - 1
        periods[name] = (j0, j1)

    n_ctl = cfg['minervini_trading']['n_controls']
    trig_days = pool_by_day(panel['trigger'])
    tmpl_days = pool_by_day(panel['template'])
    summary, curves = {}, {}
    for pname, period in periods.items():
        trades, equity, avg_inv, slot_days = simulate(panel, cfg, period,
                                                      pool_days=trig_days)
        m = metrics(trades, equity, avg_inv)
        rate = len(trades) / slot_days if slot_days else 0.0
        trades.to_csv(results / f'minervini_{pname}_trades.csv', index=False)
        curves[pname] = equity

        ctl_tot, ctl_n = [], []
        for s in range(n_ctl):
            ct, ce, _, _ = simulate(panel, cfg, period,
                                    rng=np.random.default_rng(s),
                                    entry_rate=rate, pool_days=tmpl_days)
            ctl_tot.append(ce.iloc[-1] / ce.iloc[0] - 1)
            ctl_n.append(len(ct))
        ctl_tot = np.array(ctl_tot)
        m['entry_rate'] = rate
        m['ctl_n_trades_median'] = float(np.median(ctl_n))
        m['ctl_median_total'] = float(np.median(ctl_tot))
        m['ctl_p25_total'] = float(np.quantile(ctl_tot, 0.25))
        m['ctl_p75_total'] = float(np.quantile(ctl_tot, 0.75))
        m['pct_vs_controls'] = float((m['total_return'] > ctl_tot).mean())
        summary[pname] = m
        pd.DataFrame({'seed': np.arange(n_ctl), 'total_return': ctl_tot,
                      'n_trades': ctl_n}).to_csv(
            results / f'minervini_controls_{pname}.csv', index=False)

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
                    label=f'MINERVINI ({m["total_return"]:+.0%}, beats '
                          f'{m["pct_vs_controls"]:.0%})')
        plt.xlabel('total return, %')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.title(f'Minervini vs random-template controls, {pname}')
        plt.tight_layout()
        plt.savefig(results / f'minervini_controls_{pname}.png', dpi=120)
        plt.close()

        spy = panel['spy_close'].iloc[period[0]:period[1] + 1]
        plt.figure(figsize=(11, 6))
        plt.plot(equity.index, equity / equity.iloc[0], label='MINERVINI')
        plt.plot(spy.index, spy / spy.iloc[0], '--', color='gray',
                 label='SPY (context)')
        plt.yscale('log')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.title(f'Minervini Stage-2 breakouts, {pname}')
        plt.tight_layout()
        plt.savefig(results / f'minervini_equity_{pname}.png', dpi=120)
        plt.close()

    pd.DataFrame(summary).T.to_csv(results / 'minervini_summary.csv')
    print(f'\ntables and charts -> {results}/minervini_*')
    case_studies(panel, cfg)


if __name__ == '__main__':
    main()
