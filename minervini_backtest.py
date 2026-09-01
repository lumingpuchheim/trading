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

from geostats import geo_per_bet
from lppl_backtest import ROOT, load_config, metrics
from minervini import (beat_gate, code33_legs, eps_gate, group_strength,
                       repertoire, report_within, rs_line_at_high,
                       rs_ok_matrix, rs_return, signals, weak_day_score)
from vcp_marco import marco_flags

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


def apply_v11(cfg: dict) -> dict:
    """v11 = the standing config v5r + section 17's 5/3/2 pyramid: a
    half-size pilot, then two shrinking adds, each requiring a fresh
    trigger, open profit, a non-extended price, and enough open profit to
    pay for the new shares' risk."""
    cfg = apply_v5(cfg)
    cfg['minervini_trading']['reentry_fast'] = True      # v5r keeps E3
    for key, val in cfg['minervini_v11'].items():
        cfg['minervini_trading'][key] = val
    return cfg


def apply_v10(cfg: dict) -> dict:
    """v10 = v5r + section 14's four pullback qualifiers (dry-up, depth
    cap, hold-and-bounce, no gapped high).

    REVERTED 2026-08-28: worse in both periods (+148/+147 -> +71/+33) and
    the 23rd control percentile in test. Kept runnable only to reproduce
    that recorded result, exactly as --v6 is. Not a standing config."""
    cfg = apply_v5(cfg)
    cfg['minervini_trading']['reentry_fast'] = True      # v5r keeps E3
    cfg['minervini_trading']['strict_pullback'] = True
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


def load_surprise(data_dir) -> pd.DataFrame:
    """Every cached earnings-surprise table, concatenated. The universe was
    fetched in pieces (`fetch_surprise.py`), and the wide universe adds
    its own file; all of them are named earnings_surprise*.parquet."""
    return pd.concat([pd.read_parquet(q) for q in
                      sorted(data_dir.glob('earnings_surprise*.parquet'))]
                     ).sort_values('date')


def build_panel(cfg: dict, rebuild: bool = False, fund: bool = False,
                beat: bool = False, v3: bool = False,
                v4: bool = False, v5: bool = False,
                wide: bool = False, code33: str = '',
                group: str = '', marco: bool = False) -> dict:
    """Per-day signal matrices (days x tickers) on the SPY calendar.

    fund=True additionally requires the SEPA pillar-2 EPS gate (spec
    section 8) on every setup day, and narrows the control pool the same
    way, so the comparison isolates the fundamentals filter alone.

    wide=True adds data/ohlcv_wide (every other US-listed common stock
    over $100M, from `download_data.py --wide`) to the S&P 1500, and
    caches it separately so the narrow-universe results are untouched."""
    d = cfg['data']
    data_dir = ROOT / d['cache_dir']
    cache = data_dir / ('minervini_panel_v5.npz' if v5 else
                        PANEL_CACHE_V4 if v4 else PANEL_CACHE_V3 if v3 else
                        ((PANEL_CACHE_BOTH if fund else PANEL_CACHE_BEAT) if beat
                         else (PANEL_CACHE_FUND if fund else PANEL_CACHE)))
    if wide:
        cache = cache.with_name(cache.stem + '_wide.npz')
    if cfg.get('minervini_trading', {}).get('strict_pullback'):
        cache = cache.with_name(cache.stem + '_v10.npz')
    if code33:
        cache = cache.with_name(cache.stem + f'_c33{code33}.npz')
    if group:
        cache = cache.with_name(cache.stem + f'_grp{group}.npz')
    if marco:
        cache = cache.with_name(cache.stem + '_marco.npz')
    spy = pd.read_parquet(data_dir / 'ohlcv' / f"{d['benchmark']}.parquet")
    cal = spy.index

    if cache.exists() and not rebuild:
        z = np.load(cache, allow_pickle=False)
        panel = {k: z[k] for k in z.files}
        panel['tickers'] = [str(t) for t in panel['tickers']]
    else:
        paths = [p for p in sorted((data_dir / 'ohlcv').glob('*.parquet'))
                 if p.stem != d['benchmark']]
        if wide:
            paths += sorted((data_dir / 'ohlcv_wide').glob('*.parquet'))
            paths.sort(key=lambda p: p.stem)
        tickers = [p.stem for p in paths]
        n, k = len(cal), len(tickers)
        op = np.full((n, k), np.nan)
        hi = np.full((n, k), np.nan)
        lo = np.full((n, k), np.nan)
        cl = np.full((n, k), np.nan)
        vol = np.full((n, k), np.nan)
        # Cash paid per share on each ex-date. Since 2026-08-29 the stored
        # prices are NOT dividend adjusted, so a holder's dividends have to
        # be added explicitly wherever profit is computed -- they are no
        # longer smuggled in by the price series. Zero, not NaN: "no
        # dividend that day" is a fact, not a missing value.
        div = np.zeros((n, k))
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
            if 'dividends' in raw.columns:
                div[:, j] = raw['dividends'].fillna(0.0).to_numpy()

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
            sp = load_surprise(data_dir)
            by_beat = {t: g for t, g in sp.groupby('ticker')}
            for j, t in enumerate(tickers):
                g = by_beat.get(t)
                if g is None:
                    liquid[:, j] = False
                    continue
                liquid[:, j] &= beat_gate(g['date'].to_numpy(),
                                          g['surprise_pct'].to_numpy(), cal, cfg)
            print(f'beat gate: {int(liquid.sum())} liquid+qualifying stock-days')

        if group:
            # §16: industry-group strength. Computed from the same rs
            # matrix condition 9 uses, so the group reading and the stock
            # reading are the same measure at two scales.
            tab = pd.read_csv(data_dir / 'industries.csv')
            gmap = dict(zip(tab['ticker'], tab['industry']))
            names = sorted(set(gmap.values()))
            gid = {g: i for i, g in enumerate(names)}
            groups = np.array([gid.get(gmap.get(t, None), -1) for t in tickers])
            group_pct = group_strength(rs, groups, cfg)
            lead = group_pct >= 1.0 - cfg['minervini']['rs_top_fraction']
            print(f'industry groups: {int((groups >= 0).sum())} tickers '
                  f'classified, {int(np.isfinite(group_pct).sum()):,} ranked '
                  f'stock-days, {int(lead.sum()):,} in a leading group')
            if group == 'gate':
                liquid &= lead

        if code33:
            # §15: the sales and margin legs, from EDGAR XBRL filings.
            # `filed` is the causal date, not the period end.
            fq = pd.read_parquet(data_dir / 'fundamentals_quarterly.parquet')
            fq = fq.sort_values(['ticker', 'filed'])
            by_f = {t: g for t, g in fq.groupby('ticker')}
            legs = np.zeros((n, k), dtype=np.int8)
            for j, t in enumerate(tickers):
                g = by_f.get(t)
                if g is None:
                    continue
                legs[:, j] = code33_legs(g['filed'].to_numpy(),
                                         g['revenue'].to_numpy(),
                                         g['net_income'].to_numpy(), cal, cfg)
            eps_ok = np.zeros((n, k), dtype=bool)
            eps_tab = (pd.read_parquet(data_dir / 'earnings_eps.parquet')
                       .dropna(subset=['eps']).sort_values('date'))
            by_e = {t: g for t, g in eps_tab.groupby('ticker')}
            for j, t in enumerate(tickers):
                g = by_e.get(t)
                if g is not None:
                    eps_ok[:, j] = eps_gate(g['date'].to_numpy(),
                                            g['eps'].to_numpy(), cal, cfg)
            code33_score = legs + eps_ok.astype(np.int8)      # 0..3 legs
            print(f'Code 33: {int((code33_score == 3).sum())} stock-days pass '
                  f'all three legs, {int((code33_score >= 1).sum())} pass one')
            if code33 == 'gate':
                liquid &= code33_score == 3

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
        marco_ok = np.zeros((n, k), bool)
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

            if marco:
                marco_ok[:, j] = marco_flags({'high': hi[:, j], 'low': lo[:, j],
                                              'volume': vol[:, j]})

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

        if group == 'gate':
            # same leak as the code33 gate below: the repertoire reads the
            # raw template, so gating `liquid` alone would not reach it
            setup &= lead
            watch &= lead
            trigger[1:] &= lead[:-1]
            trigger_moc[1:] &= lead[:-1]
            print(f'group gate: {int(setup.sum())} setup days, '
                  f'{int(trigger_moc.sum())} MOC triggers remain')

        if code33 == 'gate':
            # The repertoire (§11) reads the RAW trend template, not the
            # `liquid`-gated one, so narrowing `liquid` reaches `setup`
            # and leaves every pullback/cheat/power-play trigger standing.
            # The gate has to be applied to those outputs too -- the same
            # shape as the earnings blackout immediately below. Without
            # this the strategy trades an ungated repertoire against
            # controls drawn from a gated pool, which is not a comparison.
            ok33 = code33_score == 3
            setup &= ok33
            watch &= ok33
            trigger[1:] &= ok33[:-1]
            trigger_moc[1:] &= ok33[:-1]
            print(f'Code 33 gate: {int(setup.sum())} setup days, '
                  f'{int(trigger_moc.sum())} MOC triggers remain')

        if marco:
            # Their VCP is a SETUP state ("a base, not broken out yet"),
            # the same kind of state as ours, so it gates the watchlist
            # and every entry that answers to the previous day's list --
            # the same shape as the code33 and group gates above. The
            # control pool (`template`) is deliberately NOT gated, so
            # this run's controls are the same ones the ungated run
            # faced and the two are directly comparable.
            print(f'marco VCP: {int(marco_ok.sum()):,} stock-days show their '
                  f'pattern; ours and theirs agree on '
                  f'{int((setup & marco_ok).sum()):,} of '
                  f'{int(setup.sum()):,} setup days')
            setup &= marco_ok
            watch &= marco_ok
            trigger[1:] &= marco_ok[:-1]
            trigger_moc[1:] &= marco_ok[:-1]
            print(f'marco gate: {int(setup.sum()):,} setup days, '
                  f'{int(trigger.sum()):,} buy-stop fills, '
                  f'{int(trigger_moc.sum()):,} MOC triggers remain')

        blackout_days = cfg['minervini'].get('earnings_blackout_days', 0)
        if blackout_days:
            sp = load_surprise(data_dir)
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
                 'div': div,
                 'sma50': sma50, 'template': template, 'setup': setup,
                 'trigger': trigger, 'vol_ok': vol_ok, 'fill_px': fill_px,
                 'trigger_moc': trigger_moc, 'fill_moc': fill_moc,
                 'volx': volx, 'pivot': pivot, 'last_i': last_i,
                 'rs': rs, 'rsl_hi': rsl_hi, 'weak': weak,
                 'rep_label': rep_label, 'watch': watch,
                 'gc': gc, 'udv': udv, 'marco': marco_ok,
                 'code33': (code33_score if code33
                            else np.zeros((n, k), dtype=np.int8)),
                 'group_pct': (group_pct if group
                               else np.full((n, k), np.nan))}
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
             pool_days: list | None = None, moc: bool = False,
             record: dict | None = None,
             gate: np.ndarray | None = None,
             scores: np.ndarray | None = None,
             watch_cap: int = 100,
             min_score: float | None = None):
    """One portfolio path. rng=None runs the strategy; with an rng the run
    is a control (random names, next-open fills).

    moc=False: the spec's resting buy stop, filled intraday, with the
    volume verdict at the close and a failed-breakout eject.
    moc=True:  the third fill convention -- judge price and volume
    together at the close and buy market-on-close. Same base, same
    template, same exits, same everything else; no eject is needed
    because the volume is known before the trade is taken.

    Pass `record` (a dict) to get the day-by-day detail back: it is
    filled with 'invested', the fraction of equity held in stock on each
    day of the period -- 1 minus the cash fraction.

    `scores` is the ranker's (days x tickers) predicted growth rate: with
    it, the day's fillable candidates are offered the free slots in score
    order (ticker as the only tie) instead of in the order the strength
    keys put them in. `min_score` leaves a slot empty rather than fill it
    below that rate; None (the default) never declines a slot.

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
    # RANKER_SPEC Amendment 6: force-sell H trading days after entry.
    # 0 or absent = off, and with it off every number in this repo
    # reproduces bit for bit -- the branch below cannot be reached.
    max_hold = int(tr.get('max_hold_days', 0) or 0)
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
    ladder = tr.get('pyramid_ladder')             # §17 pyramid 5/3/2
    ext_max = tr.get('pyramid_max_extended', 0.0)
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
    # LEGACY. A trade filter (filters.py) plugs in here and nowhere else:
    # a (days x tickers) boolean that suppresses triggers it rejects. The
    # veto architecture it belongs to was retired on 2026-08-31
    # (DECISIONS.md, "The filter architecture is wrong"); the argument
    # survives for `equity_vs_spy.py` and the other scripts that still
    # reproduce the retired chain. Nothing new should use it.
    if gate is not None:
        trigger = trigger & gate
    # THE RANKER (rankers.py) plugs in here instead: a (days x tickers)
    # float, one predicted growth rate per orderable signal, read at the
    # day the order FILLS. It changes exactly one thing -- the order in
    # which the day's fillable candidates are offered the free slots --
    # and nothing else about the portfolio. -inf means "no score"; such a
    # name is offered last and, in practice, cannot fill anyway (it never
    # reached the ledger, so it never triggers).
    if scores is not None and gate is not None:
        raise ValueError('scores and gate are two architectures; pass one')
    # Slot capacity: by POSITION COUNT (today's book), or by CAPITAL when
    # `capital_slots` is set. Under capital slots a position that banked
    # its +20% half occupies only the fraction it still holds (~0.5), so
    # two split positions free one whole slot between them and the
    # freed capital can buy an 11th name. Off by default: every recorded
    # book, the +291.5% control included, was measured by position count.
    cap_slots = bool(tr.get('capital_slots'))

    def pos_weight(p) -> float:
        return min(1.0, p['shares'] / p['shares0']) if cap_slots else 1.0
    vol_ok = panel['vol_ok']
    # Prices are NOT dividend adjusted since 2026-08-29, so a holder's
    # dividends are cash that has to be credited here or it disappears
    # from the book. `div` is zeros where nothing was paid, never NaN.
    div = panel.get('div')
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
            # share of the ORIGINAL position this row disposes of, so the
            # euro-per-bet statistics need no assumption about halves
            'weight': pos['shares'] / pos.get('shares0', pos['shares']),
            # what the WHOLE position cost, so bets can be averaged by
            # the money in them rather than one-vote-each
            'bet_eur': pos.get('bet_eur', np.nan),
            'bet_frac': pos.get('bet_frac', np.nan),
            # dividend cash collected by the shares THIS row disposes of;
            # summed over a position's rows it is the whole bet's dividends
            'div_eur': pos.get('div_ps', 0.0) * pos['shares'],
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

        # 0. dividends, before anything sells. You collect an ex-date if
        #    you held INTO it: an entry fills at this day's close so it
        #    misses today, an exit fills at this day's open and still
        #    gets paid, and a half-sale that fills this morning collects
        #    on the whole position one last time. Same window as
        #    minervini_bets.py -- the ledger and the book must price the
        #    same bet identically or their averages cannot be compared.
        if div is not None and positions:
            for j, pos in positions.items():
                d = div[i, j]
                if d:
                    cash += pos['shares'] * d
                    pos['div_ps'] = pos.get('div_ps', 0.0) + d

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
                # the added shares never collected the dividends already
                # banked, so the per-share figure dilutes; the euros it
                # stands for do not change
                pos['div_ps'] = pos.get('div_ps', 0.0) * pos['shares'] / tot
                pos['shares'] = tot
                pos['bet_eur'] = pos.get('bet_eur', 0.0) + outflow
                cash -= outflow
                pos['added'] = True

        # 1b. v4 strength sales: half out at the open, rest keeps running
        for j in [j for j, p in positions.items() if p.get('sell_half')]:
            pos = positions[j]
            pos['sell_half'] = False
            # `strength_sell_frac` was read and never used before
            # 2026-08-28: the sale was hardcoded to half regardless.
            part = np.floor(pos['shares'] * sell_frac)
            px = op[i, j] if np.isfinite(op[i, j]) else cl[i, j]
            if part >= 1:
                cash += part * px * (1 - cost)
                trades.append({
                    'ticker': tickers[j], 'entry_date': pos['entry_date'],
                    'exit_date': cal[i], 'entry_px': pos['entry_px'],
                    'exit_px': px, 'days_held': i - pos['entry_i'],
                    'ret_net': px * (1 - cost)
                               / (pos['entry_px'] * (1 + cost)) - 1,
                    'weight': part / pos['shares0'],
                    'bet_eur': pos.get('bet_eur', np.nan),
                    'bet_frac': pos.get('bet_frac', np.nan),
                    'div_eur': pos.get('div_ps', 0.0) * part,
                    'exit_reason': 'strength'})
                pos['shares'] -= part
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
            if j in positions or not np.isfinite(px) \
                    or sum(pos_weight(p) for p in positions.values()) \
                    > tr['max_positions'] - 1 + 1e-9:
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
                            'shares0': shares, 'bet_eur': outflow,
                            'bet_frac': frac}
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
            elif max_hold and i - pos['entry_i'] >= max_hold:
                # the time cap, ABOVE every discretionary exit and below
                # the stop: a position that has blocked the slot for H
                # days goes, whatever the tennis window, the velocity
                # hold or the trend say. Fills at the next open like the
                # other slow exits.
                pos['exit_reason'] = 'max_hold'
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
                            'weight': part / pos['shares0'],
                            'bet_eur': pos.get('bet_eur', np.nan),
                            'bet_frac': pos.get('bet_frac', np.nan),
                            'div_eur': pos.get('div_ps', 0.0) * part,
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
            if scores is not None:
                # The slot decision, and the only one: highest predicted
                # rate first, ticker as the sole determinism tie. No veto,
                # no threshold, no strength keys -- selectivity is slot
                # capacity and nothing else (RANKER_SPEC.md).
                due.sort(key=lambda j: (-scores[i, j], tickers[j]))
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
                        or sum(pos_weight(p) for p in positions.values())
                        > tr['max_positions'] - 1 + 1e-9):
                    continue
                if min_score is not None and scores is not None                         and not scores[i, j] >= min_score:
                    # the natural zero: cash grows at 0.0/day, so a slot
                    # may stay empty rather than buy a negative predicted
                    # rate. Off unless min_score is passed.
                    continue
                if tr.get('adaptive'):
                    if adaptive_frac is None:
                        continue          # cap reached: skip, never liquidate
                    frac = adaptive_frac
                elif ladder:
                    frac = ladder[0]      # §17: the pilot, not a full bet
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
                                'shares0': shares, 'leg': 1,
                                'bet_eur': outflow, 'bet_frac': frac}
                cash -= outflow

        # 3c. §17 pyramid: a name we already hold fires a fresh trigger.
        #     Every condition is knowable at this close, so the add fills
        #     here like any other market-on-close buy.
        if ladder and moc and not is_control:
            for j, pos in positions.items():
                leg = pos.get('leg', 1)
                if pos['exit_reason'] or leg >= len(ladder):
                    continue
                # A5: the ladder BUILDS the position and the +20% rule
                # HARVESTS it; a position that has already sold part of
                # itself is never added to. Without this the cost basis
                # and the share total are rewritten after a sale has been
                # booked against the old ones, and the position's rows
                # stop summing to one whole bet.
                if pos.get('half_sold') or pos.get('sell_half'):
                    continue
                if not trigger[i, j]:         # under moc this IS trigger_moc
                    continue                      # A1: no fresh buy point
                c = cl[i, j]
                if not (np.isfinite(c) and c > pos['entry_px']):
                    continue                      # A2: never add to a loser
                s50 = sma50[i, j]
                if not (np.isfinite(s50) and s50 < c <= ext_max * s50):
                    continue                      # A3: above the line, not extended
                add = np.floor(ladder[leg] * eq_prev / c)
                outflow = add * c * (1 + cost)
                if add < 1 or outflow > cash:
                    continue
                tot = pos['shares'] + add
                blended = (pos['entry_px'] * pos['shares'] + c * add) / tot
                # A4: the new shares' risk to the stop that will apply
                # after the add, paid for out of the profit already there
                if add * (c - tr['stop_loss'] * blended) \
                        > pos['shares'] * (c - pos['entry_px']):
                    continue
                pos['entry_px'] = blended     # stop, 2R and +20% all move up
                pos['div_ps'] = pos.get('div_ps', 0.0) * pos['shares'] / tot
                pos['shares'] = tot
                pos['shares0'] = tot
                pos['leg'] = leg + 1
                pos['bet_eur'] = pos.get('bet_eur', 0.0) + outflow
                pos['bet_frac'] = pos.get('bet_frac', 0.0) + ladder[leg]
                cash -= outflow

        orders = {j: day for j, day in orders.items() if day > i}

        # 4. place tomorrow's orders
        exiting = sum(pos_weight(p) for p in positions.values()
                      if p['exit_reason'])
        open_w = sum(pos_weight(p) for p in positions.values())
        # a new entry is a whole 10% bet, so only WHOLE free slots arm
        # orders: floor. With capital_slots off every weight is 1.0 and
        # this floor of an integer-valued float is the old integer.
        slots = int(np.floor(tr['max_positions'] - (open_w - exiting)
                             - len(orders) + 1e-9))
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
                    c33 = panel.get('code33') if tr.get('code33_rank') else None
                    gpc = panel.get('group_pct') if tr.get('group_rank') else None

                    def key(j):
                        # §15/§16 run 2: conviction first -- Code 33 legs or
                        # the industry-group percentile -- then v4 strength
                        base = [-int(c33[i, j])] if c33 is not None else []
                        if gpc is not None:
                            base += [-(gpc[i, j] if np.isfinite(gpc[i, j])
                                       else -np.inf)]
                        base += [-int(rsl[i, j]),
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
                    # `watch_cap` is a WATCHLIST size, not a decision: an
                    # order costs nothing until it fills, so v5 arms every
                    # name it can and lets max_positions bind at entry. It
                    # is ordered by the strength keys because on 1,993 of
                    # 4,944 days the pool is larger than the cap and
                    # something has to choose -- and at this point in the
                    # day the ranker's score, which belongs to tomorrow's
                    # close, does not exist yet. Under a scored run the
                    # cap is the ONLY place those keys are still read, and
                    # the slot decision itself (step 3b) never sees them.
                    take = [j for j in sorted(day_pool, key=key)
                            if usable(j)][:watch_cap]
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
    if record is not None:
        record['invested'] = pd.Series(invested, index=days)
    return pd.DataFrame(trades), equity, float(np.mean(invested)), slot_days


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    results.mkdir(exist_ok=True)
    fund = '--fund' in sys.argv
    beat = '--beat' in sys.argv
    wide = '--wide' in sys.argv      # S&P 1500 + the rest of the US market
    code33 = ('gate' if '--code33' in sys.argv else
              'rank' if '--code33rank' in sys.argv else '')   # §15
    group = ('gate' if '--group' in sys.argv else
             'rank' if '--grouprank' in sys.argv else '')     # §16
    marco = '--marco' in sys.argv   # gate on the marco-hui-95 VCP too
    v7 = '--v7' in sys.argv
    v6 = '--v6' in sys.argv
    v9 = '--v9' in sys.argv          # §13 momentum-conditioned selling
    v10 = '--v10' in sys.argv        # §14 pullback qualifiers
    v11 = '--v11' in sys.argv        # §17 pyramid 5/3/2
    v5 = '--v5' in sys.argv or v6 or v7 or v9 or v10 or v11
    v4 = '--v4' in sys.argv or v5
    v3 = '--v3' in sys.argv or v4
    if v11:
        cfg = apply_v11(cfg)
    elif v10:
        cfg = apply_v10(cfg)
    elif v9:
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
    if code33 == 'rank':
        cfg['minervini_trading']['code33_rank'] = True
    if group == 'rank':
        cfg['minervini_trading']['group_rank'] = True
    if cfg['minervini_trading'].get('adaptive'):
        for k, v in cfg['minervini_v8'].items():
            cfg['minervini_trading'][k] = v
    panel = build_panel(cfg, rebuild='--rebuild' in sys.argv, fund=fund,
                        beat=beat, v3=v3 and not v4, v4=v4 and not v5, v5=v5,
                        wide=wide, code33=code33, group=group, marco=marco)
    cal = panel['calendar']

    print(f'panel: {len(panel["tickers"])} tickers, '
          f'{int(panel["template"].sum())} template stock-days, '
          f'{int(panel["setup"].sum())} setup days, '
          f'{int(panel["trigger"].sum())} buy-stop fills '
          f'({int((panel["trigger"] & panel["vol_ok"]).sum())} volume-confirmed)')

    today = str(cal[-1].date())
    # ONE continuous run, start to today. The development / test split at
    # 2019 was removed 2026-08-29: nothing in the screener is fitted, so
    # it split a result in half rather than an experiment, and the word
    # collided with the rolling fit boundary the filters use.
    periods = {'full': (
        int(cal.searchsorted(pd.Timestamp(bt['start']))),
        int(cal.searchsorted(pd.Timestamp(today), side='right')) - 1)}

    moc = '--moc' in sys.argv
    ab = ''.join(f[2:] for f in ('--e1','--e2','--e3','--e4','--park','--craft') if f in sys.argv)
    for a in sys.argv:
        if a.startswith('--size='):
            ab += 's' + a[7:].replace('0.','')
    tag = (('v11' if v11 else 'v10' if v10 else 'v9' if v9 else 'v7' if v7 else ('v5_' + ab) if ab else 'v6' if v6 else 'v5' if v5 else 'v4' if v4 else 'v3' if v3 else 'v2') + ('_moc' if moc else '')
           + ('_fund' if fund else '') + ('_beat' if beat else '')
           + ('_wide' if wide else '') + (f'_c33{code33}' if code33 else '')
           + (f'_grp{group}' if group else '') + ('_marco' if marco else ''))
    if moc:
        print('ENTRY: market-on-close (third fill convention) — '
              f'{int(panel["trigger_moc"].sum())} entries available')
    n_ctl = cfg['minervini_trading']['n_controls']
    strat_pool = panel['watch'] if v5 and 'watch' in panel else panel['setup']
    setup_days = pool_by_day(strat_pool)
    tmpl_days = pool_by_day(panel['template'])
    summary, curves, cash = {}, {}, {}
    for pname, period in periods.items():
        rec = {}
        trades, equity, avg_inv, slot_days = simulate(
            panel, cfg, period, pool_days=setup_days, moc=moc, record=rec)
        cash[pname] = 1.0 - rec['invested']
        m = metrics(trades, equity, avg_inv)
        # win rate removed 2026-08-28: being right 90% of the time at
        # break-even while the rest takes real money still loses, so it
        # cannot say whether a system works. The per-bet euro multiple is
        # the honest unit -- geostats.geo_per_bet.
        m.pop('win_rate', None)
        # `avg_trade` removed 2026-08-29 for two reasons at once: it is an
        # ARITHMETIC mean, and it averages ROWS, so a position that sold
        # half at +20% votes twice while a loser votes once. It read
        # 1.0406 next to a 1.0302 computed from the same trades in
        # filter_backtest.py, and neither was the per-bet number.
        m.pop('avg_trade', None)
        m['geo_bet'] = geo_per_bet(trades)
        rate = len(trades) / slot_days if slot_days else 0.0
        trades.to_csv(results / f'minervini_{tag}_trades.csv', index=False)
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
            results / f'minervini_{tag}_controls.csv', index=False)

        print(f'\n=== {cal[period[0]].date()} .. '
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
        plt.title('Minervini v2 vs random-template controls')
        plt.tight_layout()
        plt.savefig(results / f'minervini_{tag}_controls.png', dpi=120)
        plt.close()

        spy = panel['spy_close'].iloc[period[0]:period[1] + 1]
        plt.figure(figsize=(11, 6))
        plt.plot(equity.index, equity / equity.iloc[0], label='MINERVINI v2')
        plt.plot(spy.index, spy / spy.iloc[0], '--', color='gray',
                 label='SPY (context)')
        plt.yscale('log')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.title('Minervini v2 Stage-2 breakouts')
        plt.tight_layout()
        plt.savefig(results / f'minervini_{tag}_equity.png', dpi=120)
        plt.close()

    pd.DataFrame(summary).T.to_csv(results / f'minervini_{tag}_summary.csv')

    # --- how often does it actually bet? The cash side of the book, daily.
    cash_s = cash['full'].sort_index()
    cash_s.name = 'cash_fraction'
    cash_s.to_csv(results / f'minervini_{tag}_cash.csv')
    yr = cash_s.groupby(cash_s.index.year)
    tbl = pd.DataFrame({'mean_cash': yr.mean(),
                        'days_flat': yr.apply(lambda x: (x > 0.99).mean()),
                        'days_fully_in': yr.apply(lambda x: (x < 0.05).mean())})
    tbl.to_csv(results / f'minervini_{tag}_cash_by_year.csv')
    print('\ncash by year, % of equity / % of days '
          '(mean cash, days flat, days >95% invested):')
    print((tbl * 100).round(1).to_string())
    print(f'\nwhole span: mean cash {cash_s.mean():.1%}, '
          f'flat on {(cash_s > 0.99).mean():.1%} of days, '
          f'>95% invested on {(cash_s < 0.05).mean():.1%} of days')

    fig, ax = plt.subplots(2, 1, figsize=(13, 8), sharex=True,
                           gridspec_kw={'height_ratios': [2, 1]})
    ax[0].fill_between(cash_s.index, cash_s * 100, color='steelblue',
                       alpha=0.35, label='cash')
    ax[0].plot(cash_s.index, cash_s.rolling(63).mean() * 100, color='crimson',
               lw=1.2, label='cash, 63-day mean')
    ax[0].set_ylabel('cash, % of equity')
    ax[0].set_ylim(0, 100)
    ax[0].legend(loc='upper right')
    ax[0].grid(alpha=0.3)
    ax[0].set_title(f'{tag}: cash held day by day, '
                    f'mean {cash_s.mean():.0%}')
    ax[1].plot(cash_s.index, (cash_s < 0.05).rolling(252).mean() * 100,
               color='darkgreen', lw=1.2)
    ax[1].set_ylabel('% of last 252 days\nfully invested')
    ax[1].set_ylim(0, 100)
    ax[1].grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(results / f'minervini_{tag}_cash.png', dpi=120)
    plt.close()

    print(f'\ntables and charts -> {results}/minervini_{tag}_*')


if __name__ == '__main__':
    main()
