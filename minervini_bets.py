"""Per-signal bet ledger: what one euro becomes on every v5r entry signal.

`minervini_backtest.py` answers a PORTFOLIO question -- 10 slots, a 20-day
re-entry cooldown, a position cap -- so v5's 66,099 entry signals become
~1,460 realised trades and the other ~64,600 are never priced at all. The
model cannot learn from trades that the slot queue happened to admit.

This script asks the single-bet question instead: put one euro on EVERY
signal, hold it under v5r's exit rules, record what the euro became.

Deliberately absent, per the user's scope decision (2026-08-28):
  - no slots, no cooldown, no position cap, no ranking
  - no fees, no tax, no whole-share rounding
  - bets overlap freely; a day with forty signals produces forty bets
The portfolio layer is a separate problem. This ledger is its INPUT.

Exits are v5r's own, read from config.yaml (never redefined here) and
evaluated in `simulate()`'s order: decided at a close, filled at the NEXT
open. Active under v5r (--v5 --e3 --moc):

    delisted    the ticker's history ends mid-period
    stop        close <= 0.92 x entry
    egg         on day 15 exactly, close < entry (tennis-ball window)
    breakeven   after 2R was touched, close back <= entry
    sma         decisive SMA50 break: >1% below, or below on volume
    strength    from day 15, close >= 1.2 x entry -> HALF out, rest runs

Not active under v5r and therefore not implemented: climax (e1), vol-weak
(e2), aging (e4), momentum-conditioned selling (--v9), pyramiding (v6/v11),
failed_breakout (a buy-stop artefact; MOC knows volume before it buys).

Usage
    python minervini_bets.py                      # ledger + summary
    python minervini_bets.py --windows 252        # + CNN input windows
    python minervini_bets.py --fix-egg            # see EGG NOTE below
    python minervini_bets.py --all-days           # ignore the market light

Requires data/minervini_panel_v5.npz. Build it first if absent:
    python minervini_backtest.py --v5 --e3 --moc

EGG NOTE. `simulate()` reads the tennis-ball peak as
`pos.get('peak2', c)` -- defaulting to TODAY's close -- and only assigns
`peak2` when `c > peak`. Since peak defaults to c on every visit, `c > peak`
is never true, `peak2` is never stored, and `dipped` / `recovered` stay
unset for the life of every position. The egg's recovery leg is therefore
dead code in the standing configuration: the egg fires on day 15 whenever
the close is under the entry, recovered or not. This file reproduces that
behaviour by default so its labels match the system that generated the
signals. `--fix-egg` runs the intended version (peak seeded at entry) so
the difference can be measured rather than argued about.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from geostats import geo_mean_per_euro
from lppl_backtest import ROOT, load_config
from minervini_backtest import apply_v5, build_panel, market_green

PANEL = ROOT / 'data' / 'minervini_panel_v5.npz'
OUT = ROOT / 'results' / 'minervini_bets_v5r.csv'
WIN_OUT = ROOT / 'results' / 'minervini_bets_v5r_windows.npz'

REP_NAMES = {0: 'pivot', 1: 'cheat', 2: 'pullback', 3: 'power'}


def load_panel(cfg: dict) -> dict:
    """The v5 panel, through the backtest's own builder so the cache, the
    calendar and the market light are the ones the strategy sees."""
    if not PANEL.exists():
        sys.exit(f'missing {PANEL.name}; build it with:\n'
                 f'    python minervini_backtest.py --v5 --e3 --moc')
    return build_panel(cfg, v5=True)


def price_bet(i0: int, j: int, arr: dict, p: dict) -> dict | None:
    """One euro on the signal at (day i0, ticker j). Returns the bet row,
    or None if the entry price is unusable.

    Mirrors `simulate()`'s close-then-next-open convention: a rule read at
    the close of day i is filled at the open of day i+1.
    """
    cl, op, sma50, volx, last_i = (arr['close'], arr['open'], arr['sma50'],
                                   arr['volx'], arr['last_i'])
    n = cl.shape[0]
    entry_px = cl[i0, j]                       # MOC fill == that day's close
    if not np.isfinite(entry_px) or entry_px <= 0:
        return None

    stop_px = p['stop_loss'] * entry_px
    be_px = p['be_level'] * entry_px
    sell_px = p['strength_sell_at'] * entry_px
    protect = p['protect_days']

    be = False
    dipped = recovered = False
    peak2 = entry_px if p['fix_egg'] else None   # see EGG NOTE in the header
    half_day = None                              # day the half-sale is DECIDED
    exit_day, reason = None, None

    for i in range(i0, n):
        c = cl[i, j]
        if not np.isfinite(c):
            exit_day, reason = i - 1, 'gap_in_history'
            break
        age = i - i0

        # --- the exit chain, in simulate()'s exact order -----------------
        if i >= last_i[j] and last_i[j] < n - 1:
            reason = 'delisted'
        elif c <= stop_px:
            reason = 'stop'
        elif p.get('max_hold') and age >= p['max_hold']:
            reason = 'max_hold'          # Amendment 6, simulate()'s order
        elif age < protect:
            pass                    # tennis-ball window: only the stop sells
        elif age == protect and c < entry_px and not recovered:
            reason = 'egg'
        elif be and c <= entry_px:
            reason = 'breakeven'
        elif (np.isfinite(sma50[i, j]) and c < sma50[i, j]
                and (c < (1.0 - p['decisive_break_frac']) * sma50[i, j]
                     or (p['decisive_volume'] and np.isfinite(volx[i, j])
                         and volx[i, j] > 1.0))):
            reason = 'sma'

        if not be and c >= be_px:
            be = True

        # --- tennis-ball bookkeeping (inert unless --fix-egg) ------------
        if peak2 is not None:
            if c > peak2:
                if dipped:
                    recovered = True
                peak2 = c
            elif c < peak2:
                dipped = True

        # --- the strength half-sale, after the exit chain has had its say
        if (half_day is None and age >= protect and c >= sell_px
                and reason is None):
            half_day = i

        if reason is not None:
            exit_day = i
            break
    else:
        exit_day, reason = n - 1, 'open_at_end'

    def fill_after(d: int) -> float:
        """Exits fill at the next open; fall back to the decision close."""
        if d + 1 < n and np.isfinite(op[d + 1, j]) and op[d + 1, j] > 0:
            return float(op[d + 1, j])
        return float(cl[d, j])

    px_exit = fill_after(exit_day)

    # --- dividends, as explicit cash ------------------------------------
    # Prices stopped being dividend adjusted on 2026-08-29, so a holder's
    # dividends have to be added here or they vanish. You collect an ex-date
    # if you held into it: entry fills at the CLOSE of i0, so an ex-date on
    # i0 is already missed; exits fill at the OPEN of exit_day+1, and an
    # ex-date that morning still pays you. Hence (i0, exit_day+1].
    # After the half-sale only the remaining fraction keeps collecting.
    dv = arr['div'][:, j] if arr.get('div') is not None else None

    def cash_between(a: int, b: int) -> float:
        if dv is None or b < a:
            return 0.0
        return float(np.nansum(dv[a:min(b, n - 1) + 1]))

    if half_day is None:
        frac, px_half = 0.0, np.nan
        cash = cash_between(i0 + 1, exit_day + 1)
        y = (px_exit + cash) / entry_px
        y_half, half_held = np.nan, 0
    else:
        # half banked into strength, the rest runs to its own exit. Written
        # as separate terms so a missing half price can never silently NaN
        # the whole bet.
        frac, px_half = p['strength_sell_frac'], fill_after(half_day)
        cash_pre = cash_between(i0 + 1, half_day + 1)
        cash_post = cash_between(half_day + 2, exit_day + 1)
        cash = cash_pre + (1.0 - frac) * cash_post
        y = (frac * px_half + (1.0 - frac) * px_exit + cash) / entry_px
        # THE BANKED LEG, as its own capital stream (RANKER_SPEC.md, "The
        # target"). Dividends collected before the sale were earned by the
        # WHOLE position, so both streams carry them at their capital
        # share; the identity that matters is
        #     frac*y_half + (1-frac)*y_rest == y
        # which is what lets the ranker recover y_rest from y and y_half
        # without a second price column.
        y_half = (px_half + cash_pre) / entry_px
        half_held = int(half_day - i0)

    return {'ticker': arr['tickers'][j], 'entry_i': i0, 'ticker_j': j,
            'entry_date': arr['calendar'][i0],
            'exit_date': arr['calendar'][min(exit_day + 1, n - 1)],
            'entry_px': float(entry_px), 'exit_px': px_exit,
            'half_px': px_half, 'half_frac': frac,
            'y_half': float(y_half), 'half_days_held': half_held,
            'days_held': int(exit_day - i0),
            'exit_reason': reason, 'y': float(y), 'r': float(np.log(y)),
            'div_cash': float(cash), 'div_pct': float(cash / entry_px),
            'rep': REP_NAMES.get(int(arr['rep_label'][i0, j]), '?'),
            'green': bool(arr['green'][i0]),
            'rs': float(arr['rs'][i0, j]) if np.isfinite(arr['rs'][i0, j])
            else np.nan}


def build_ledger(panel: dict, cfg: dict, fix_egg: bool,
                 all_days: bool) -> pd.DataFrame:
    tr = cfg['minervini_trading']
    p = {'stop_loss': tr['stop_loss'],
         'be_level': 1.0 + tr['breakeven_r'] * (1.0 - tr['stop_loss']),
         'strength_sell_at': tr['strength_sell_at'],
         'strength_sell_frac': tr['strength_sell_frac'],
         'protect_days': tr['protect_days'],
         'decisive_break_frac': tr['decisive_break_frac'],
         'decisive_volume': tr['decisive_volume'],
         'max_hold': int(tr.get('max_hold_days', 0) or 0),
         'fix_egg': fix_egg}
    print(f'exit params: stop {p["stop_loss"]:.2f}x  breakeven-arm '
          f'{p["be_level"]:.2f}x  egg day {p["protect_days"]}  '
          f'strength {p["strength_sell_at"]:.1f}x/'
          f'{p["strength_sell_frac"]:.0%}  decisive '
          f'{p["decisive_break_frac"]:.0%}+vol  fix_egg={fix_egg}')

    cal = panel['calendar']
    green = panel['green'] if 'green' in panel else market_green(
        panel['spy_close'])
    arr = {'close': panel['close'], 'open': panel['open'],
           'sma50': panel['sma50'], 'volx': panel['volx'],
           'last_i': panel['last_i'], 'tickers': panel['tickers'],
           'rep_label': panel['rep_label'], 'rs': panel['rs'],
           'div': panel.get('div'),
           'calendar': cal, 'green': green}

    trig = panel['trigger_moc'].copy()
    start = cal.searchsorted(pd.Timestamp(cfg['backtest']['start']))
    trig[:start] = False
    print(f'signals in panel: {int(panel["trigger_moc"].sum()):,}  '
          f'from {cfg["backtest"]["start"]}: {int(trig.sum()):,}')
    if not all_days:
        # The market light is read the day the ORDER is placed, not the day
        # the signal prints: simulate() places tomorrow's orders at tonight's
        # close. Keying this on green[i] instead of green[i-1] left 591
        # signals the book can buy and no filter can score, which every
        # filter arm then took unconditionally (EVALUATION_SPEC.md rule 3).
        prev = np.zeros_like(green)
        prev[1:] = green[:-1]
        trig &= prev[:, None]
        print(f'orderable (light green the day before): {int(trig.sum()):,}')

    rows = []
    days, names = np.nonzero(trig)
    for i0, j in zip(days.tolist(), names.tolist()):
        row = price_bet(i0, j, arr, p)
        if row is not None:
            rows.append(row)
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame) -> None:
    """Everything printed, nothing selected.

    GEOMETRIC, everywhere (arithmetic removed 2026-08-29). `y` is already
    one multiple per bet -- the half-sale and the final exit blended into
    one number, dividends inside it -- so the only thing left to get
    right is the average, and the average that answers 'what does one
    euro become' is the one that compounds. The arithmetic mean printed
    here read 1.0122 and was compared for weeks against a portfolio
    figure that was neither arithmetic nor per-bet. The trimmed column
    says whether a handful of rows is carrying the result.
    """
    print(f'\n{"":12s} {"n":>7s} {"geo y":>9s} {"median":>8s} {"win%":>7s} '
          f'{"p99 y":>8s} {"top5% share":>12s} {"geo ex-top1%":>14s}')
    for label, g in (('all bets', df),):
        y = g['y'].to_numpy()
        prof = y - 1.0
        gross = prof[prof > 0].sum()
        top5 = np.sort(prof)[-max(1, len(prof) // 20):].sum()
        keep = y <= np.quantile(y, 0.99)
        print(f'{label:12s} {len(y):7,d} {geo_mean_per_euro(y):9.4f} '
              f'{np.median(y):8.4f} {(y > 1).mean():6.1%} '
              f'{np.quantile(y, 0.99):8.3f} '
              f'{top5 / gross if gross > 0 else np.nan:11.1%} '
              f'{geo_mean_per_euro(y[keep]):14.4f}')
    dead = int((df['y'] <= 0).sum())
    if dead:
        # a total loss has no logarithm; say so rather than let the
        # geometric mean quietly drop the worst bets in the book
        print(f'  ({dead} bets with y <= 0 dropped by the geometric mean)')

    print('\nby entry type (geo y / n):')
    piv = df.groupby('rep')['y'].agg(
        ['size', ('geo', geo_mean_per_euro)])
    print(piv.to_string(float_format=lambda v: f'{v:.4f}'))

    print('\nby exit reason (geo y / n):')
    ex = df.groupby('exit_reason')['y'].agg(['size', ('geo', geo_mean_per_euro), 'median'])
    print(ex.sort_values('size', ascending=False).to_string(
        float_format=lambda v: f'{v:.4f}'))

    print(f'\nheld: median {df["days_held"].median():.0f}d  '
          f'mean {df["days_held"].mean():.0f}d  '
          f'max {df["days_held"].max():.0f}d')


def dump_windows(df: pd.DataFrame, panel: dict, width: int) -> None:
    """The CNN's input: `width` daily bars ending on the signal day.

    Scale-invariant by construction -- every channel is a ratio, so a 5 EUR
    stock and a 500 EUR stock present identically. Bets whose history is
    shorter than `width` are dropped and reported.
    """
    cl, sma50, volx = panel['close'], panel['sma50'], panel['volx']
    # `rs` in the panel is the raw trailing 126-day RETURN: unbounded,
    # heavy-tailed (max 871 = +87,000%, against a 4.09 sd) and not
    # comparable across eras. The strategy never reads its level -- only
    # whether it ranks in the day's top 30% -- so feed the model the same
    # thing: the cross-sectional percentile on each day, centred on zero.
    # Fed raw, a single 871 sets the channel's mean and sd and flattens
    # every real value to nothing.
    rs = pd.DataFrame(panel['rs']).rank(axis=1, pct=True).to_numpy() - 0.5
    spy = np.asarray(panel['spy_close'], dtype=float)
    spy_ma = pd.Series(spy).rolling(200).mean().to_numpy()

    ok = df['entry_i'].to_numpy() >= width
    if (~ok).any():
        print(f'\nwindows: dropping {int((~ok).sum()):,} bets with fewer '
              f'than {width} prior bars')
    d = df[ok]
    idx = d['entry_i'].to_numpy()
    jj = d['ticker_j'].to_numpy()

    rows = np.arange(-width + 1, 1)[None, :] + idx[:, None]   # (n, width)
    px = cl[rows, jj[:, None]]
    anchor = px[:, -1][:, None]
    with np.errstate(divide='ignore', invalid='ignore'):
        ch_px = np.log(px / anchor)
        ch_ma = np.log(px / sma50[rows, jj[:, None]])
        ch_vol = np.log(np.clip(volx[rows, jj[:, None]], 1e-3, None))
        ch_rs = rs[rows, jj[:, None]]
        ch_spy = np.log(spy[rows] / spy_ma[rows])
    x = np.stack([ch_px, ch_ma, ch_vol, ch_rs, ch_spy], axis=1)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    np.savez_compressed(
        WIN_OUT, x=x, y=d['y'].to_numpy(np.float32),
        r=d['r'].to_numpy(np.float32),
        entry_date=d['entry_date'].to_numpy().astype('datetime64[D]'),
        exit_date=d['exit_date'].to_numpy().astype('datetime64[D]'),
        rep=d['rep'].to_numpy().astype('U8'),
        ticker=d['ticker'].to_numpy().astype('U8'),
        channels=np.array(['logpx', 'log_px_over_sma50', 'log_volx',
                           'rs_pctile', 'log_spy_over_sma200']))
    print(f'windows: {x.shape} -> {WIN_OUT.name} '
          f'({WIN_OUT.stat().st_size / 1e6:.0f} MB)')


def main() -> None:
    fix_egg = '--fix-egg' in sys.argv
    all_days = '--all-days' in sys.argv
    width = 0
    if '--windows' in sys.argv:
        k = sys.argv.index('--windows')
        width = int(sys.argv[k + 1]) if k + 1 < len(sys.argv) else 252
    # RANKER_SPEC Amendment 6: the ledger is keyed on H because the CAP
    # changes outcomes -- y, y_half and days_held all move -- while the
    # WINDOWS do not, because a window is entry-day history and the cap
    # is an exit rule. Every H therefore shares one windows file and one
    # feature cache, and only labels and fits are re-made.
    hold = 0
    if '--max-hold' in sys.argv:
        hold = int(sys.argv[sys.argv.index('--max-hold') + 1])

    cfg = apply_v5(load_config())
    cfg['minervini_trading']['reentry_fast'] = True        # v5r keeps E3
    if hold:
        cfg['minervini_trading']['max_hold_days'] = hold
        print(f'max_hold: every position force-sold {hold} trading days '
              f'after entry')
    panel = load_panel(cfg)
    df = build_ledger(panel, cfg, fix_egg, all_days)
    summarise(df)

    out = (OUT if not hold
           else OUT.with_name(f'{OUT.stem}_H{hold}{OUT.suffix}'))
    out.parent.mkdir(exist_ok=True)
    df.to_csv(out, index=False)
    print()
    print(f'ledger: {len(df):,} bets -> {out.name}')
    if width:
        if hold:
            sys.exit('--windows with --max-hold would rewrite the shared '
                     'windows file for no reason: a window is entry-day '
                     'history and the cap is an exit rule. Build windows '
                     'once, uncapped.')
        dump_windows(df, panel, width)


if __name__ == '__main__':
    main()
