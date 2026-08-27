"""Minervini v5r through the SIMULATOR's real cost and tax engine.

One continuous run 2007 -> today, no dev/test split: nothing was fitted
on the development period, so a single compounding path is the honest
picture. (Caveat kept in view: rules from v3 onward were CHOSEN after
both periods were seen, so the whole span is in-sample for them. Removing
the split changes the presentation, not that fact.)

What this adds over `minervini_backtest.py`:

  - **Comdirect order fees** (`sim/costs.py`): 4.90 EUR + 0.25% of
    volume, clamped to [9.90, 59.90], plus a 2.50 venue fee — an
    ABSOLUTE charge per order, so it bites small books hardest, unlike
    the flat 0.2% the research backtests assume.
  - **German investment tax** (`sim/tax.py`): 26.375% withheld on every
    realised gain, the Aktien-Topf loss pot carrying forward, and the
    1,000 EUR yearly Sparer-Pauschbetrag.

Assumption declared: prices are treated as EUR-equivalent (no FX leg).
Currency conversion would add a spread on every order and is not
modelled, so these numbers remain a best case.

Run: python minervini_live_sim.py                # 20k and 100k books
     python minervini_live_sim.py --park         # idle cash rides SPY
"""

import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config
from minervini_backtest import apply_v5, build_panel, pool_by_day
from sim.costs import load_sim_config, order_fee
from sim.tax import STOCK, TaxState, tax_on_sale

BOOKS = (20_000.0, 100_000.0)


def run(panel: dict, cfg: dict, scfg: dict, start_equity: float,
        park: bool = False) -> tuple[pd.Series, pd.DataFrame, dict]:
    """One continuous path with real fees and withheld tax."""
    tr = cfg['minervini_trading']
    cal = panel['calendar']
    tickers = panel['tickers']
    op, cl, sma50 = panel['open'], panel['close'], panel['sma50']
    volx, last_i = panel['volx'], panel['last_i']
    green, dimmer = panel['green'], panel.get('dimmer')
    trig, fill_px = panel['trigger_moc'], panel['fill_moc']
    rsl, wk, rsv = panel['rsl_hi'], panel['weak'], panel['rs']
    pool_days = pool_by_day(panel['watch'])
    spy_f = panel['spy_close'].pct_change().fillna(0.0).to_numpy() + 1.0

    dec_frac = tr.get('decisive_break_frac', 0.0)
    dec_vol = tr.get('decisive_volume', False)
    be_level = 1.0 + tr.get('breakeven_r', 0) * (1.0 - tr['stop_loss'])
    protect = tr.get('protect_days', 0)
    frac = tr['equal_weight_fraction']

    cash = eq_prev = start_equity
    positions: dict[int, dict] = {}
    orders: dict[int, int] = {}
    cooldown: dict[int, int] = {}
    trades: list[dict] = []
    tax = TaxState()
    fees_paid = taxes_paid = 0.0
    equity = pd.Series(np.nan, index=cal)

    def sell(j: int, i: int, pos: dict, px: float, shares: float,
             reason: str) -> float:
        nonlocal fees_paid, taxes_paid
        gross = shares * px
        fee = order_fee(gross, scfg)
        basis = shares * pos['entry_px'] + pos['fee_per_share'] * shares
        gain = gross - fee - basis
        t = tax_on_sale(tax, STOCK, gain, i - pos['entry_i'],
                        cal[i].year, scfg)
        fees_paid += fee
        taxes_paid += t
        trades.append({'ticker': tickers[j], 'entry_date': pos['entry_date'],
                       'exit_date': cal[i], 'shares': shares,
                       'entry_px': pos['entry_px'], 'exit_px': px,
                       'gain_eur': gain, 'fee_eur': fee, 'tax_eur': t,
                       'days_held': i - pos['entry_i'], 'exit_reason': reason,
                       'ret_net': gain / basis if basis > 0 else 0.0})
        cd = tr['reentry_cooldown'] if reason == 'stop' \
            else cfg['minervini_v7']['reentry_fast_days']
        cooldown[j] = i + cd
        return gross - fee - t

    for i in range(len(cal)):
        if park:
            cash *= spy_f[i]

        for j in [j for j, p in positions.items() if p['exit_reason']]:
            pos = positions.pop(j)
            px = op[i, j] if np.isfinite(op[i, j]) else cl[i, j]
            cash += sell(j, i, pos, px, pos['shares'], pos['exit_reason'])

        for j in [j for j, p in positions.items() if p.get('sell_half')]:
            pos = positions[j]
            pos['sell_half'] = False
            half = np.floor(pos['shares'] / 2)
            if half >= 1:
                px = op[i, j] if np.isfinite(op[i, j]) else cl[i, j]
                cash += sell(j, i, pos, px, half, 'strength')
                pos['shares'] -= half
                pos['half_sold'] = True

        for j, pos in positions.items():
            c = cl[i, j]
            if i >= last_i[j] and last_i[j] < len(cal) - 1:
                pos['exit_reason'] = 'delisted'
            elif c <= tr['stop_loss'] * pos['entry_px']:
                pos['exit_reason'] = 'stop'
            elif protect and i - pos['entry_i'] < protect:
                pass
            elif protect and i - pos['entry_i'] == protect \
                    and c < pos['entry_px'] and not pos.get('recovered'):
                pos['exit_reason'] = 'egg'
            elif pos.get('be') and c <= pos['entry_px']:
                pos['exit_reason'] = 'breakeven'
            elif np.isfinite(sma50[i, j]) and c < sma50[i, j] and (
                    c < (1.0 - dec_frac) * sma50[i, j]
                    or (dec_vol and np.isfinite(volx[i, j])
                        and volx[i, j] > 1.0)):
                pos['exit_reason'] = 'sma'
            if not pos.get('be') and c >= be_level * pos['entry_px']:
                pos['be'] = True
            peak = pos.get('peak2', c)
            if c > peak:
                if pos.get('dipped'):
                    pos['recovered'] = True
                pos['peak2'] = c
            elif c < peak:
                pos['dipped'] = True
            if not pos.get('half_sold') and not pos.get('sell_half') \
                    and i - pos['entry_i'] >= protect \
                    and c >= tr['strength_sell_at'] * pos['entry_px'] \
                    and not pos['exit_reason']:
                pos['sell_half'] = True

        for j in [j for j, day in orders.items() if day == i]:
            orders.pop(j)
            px = fill_px[i, j]
            if not trig[i, j] or j in positions or not np.isfinite(px) \
                    or len(positions) >= tr['max_positions']:
                continue
            target = frac * eq_prev
            shares = np.floor(min(target, cash) / px)
            if shares < 1:
                continue
            gross = shares * px
            fee = order_fee(gross, scfg)
            if gross + fee > cash:
                shares -= 1
                if shares < 1:
                    continue
                gross = shares * px
                fee = order_fee(gross, scfg)
                if gross + fee > cash:
                    continue
            fees_paid += fee
            positions[j] = {'shares': shares, 'entry_px': px, 'entry_i': i,
                            'entry_date': cal[i], 'exit_reason': None,
                            'fee_per_share': fee / shares}
            cash -= gross + fee

        exiting = sum(1 for p in positions.values() if p['exit_reason'])
        slots = tr['max_positions'] - (len(positions) - exiting) - len(orders)
        if slots > 0 and green[i] and i + 1 < len(cal):
            def key(j):
                return (-int(rsl[i, j]),
                        -(wk[i, j] if np.isfinite(wk[i, j]) else -np.inf),
                        -(rsv[i, j] if np.isfinite(rsv[i, j]) else -np.inf),
                        tickers[j])
            for j in sorted(pool_days[i], key=key):
                if j in positions or j in orders or cooldown.get(j, -1) > i \
                        or last_i[j] <= i:
                    continue
                orders[int(j)] = i + 1
                if len(orders) >= 100:
                    break

        held = sum(p['shares'] * cl[i, j] for j, p in positions.items())
        eq_prev = cash + held
        equity.iloc[i] = eq_prev

    return equity, pd.DataFrame(trades), {'fees': fees_paid, 'taxes': taxes_paid}


def main() -> None:
    cfg = apply_v5(load_config())
    scfg = load_sim_config()
    park = '--park' in sys.argv
    panel = build_panel(cfg, v5=True)
    cal = panel['calendar']
    spy = panel['spy_close']
    results = ROOT / cfg['backtest']['results_dir']

    curves, rows = {}, []
    for cap in BOOKS:
        eq, trades, meta = run(panel, cfg, scfg, cap, park=park)
        curves[cap] = eq
        yrs = len(eq) / 252
        tot = eq.iloc[-1] / eq.iloc[0] - 1
        gross = trades['gain_eur'].sum() + meta['fees']
        rows.append({
            'book_eur': cap, 'total': tot,
            'cagr': (1 + tot) ** (1 / yrs) - 1,
            'maxdd': float((eq / eq.cummax() - 1).min()),
            'trades': len(trades),
            'fees_eur': meta['fees'], 'taxes_eur': meta['taxes'],
            'fee_pct_of_gross': meta['fees'] / gross if gross else np.nan,
            'final_eur': eq.iloc[-1]})
        trades.to_csv(results / f'minervini_livesim_{int(cap)}_trades.csv',
                      index=False)

    summary = pd.DataFrame(rows)
    print(summary.to_string(index=False, float_format=lambda v: f'{v:,.2f}'))
    spy_tot = spy.iloc[-1] / spy.iloc[0] - 1
    print(f'\nSPY same span {cal[0].date()} .. {cal[-1].date()}: {spy_tot:+.1%} '
          f'({(1 + spy_tot) ** (252 / len(cal)) - 1:+.2%}/yr)')

    ann = pd.DataFrame({f'{int(c)} EUR book': e.groupby(e.index.year).last()
                        .pct_change().fillna(e.iloc[0] and
                                             e.groupby(e.index.year).last()
                                             .iloc[0] / e.iloc[0] - 1)
                        for c, e in curves.items()})
    ann['SPY'] = spy.groupby(spy.index.year).last().pct_change()
    print('\nannual returns:')
    print((ann * 100).round(1).to_string())
    ann.to_csv(results / 'minervini_livesim_annual.csv')

    plt.figure(figsize=(13, 7))
    for cap, e in curves.items():
        plt.plot(e.index, e / e.iloc[0],
                 label=f'v5r, {int(cap):,} EUR book, real fees+tax '
                       f'({e.iloc[-1] / e.iloc[0] - 1:+.0%})')
    plt.plot(spy.index, spy / spy.iloc[0], '--', color='gray',
             label=f'SPY ({spy_tot:+.0%})')
    plt.yscale('log')
    plt.title('Minervini v5r 2007-2026, one continuous run, '
              'Comdirect fees + German tax'
              + (' + SPY parking' if park else ''))
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    out = results / f'minervini_livesim{"_park" if park else ""}.png'
    plt.savefig(out, dpi=120)
    plt.close()
    print(f'\nchart -> {out}')


if __name__ == '__main__':
    main()
