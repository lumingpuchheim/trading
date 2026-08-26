"""Weekly recommendations and bubble warnings (SIMULATOR_SPEC section 1).

Two sources, always labelled:
  LPPL_DIP2      — 2-of-5 bubble flag, persistent, 4% dip, tc still ahead
  STEADY_GIANTS  — low vol, straight 5y compounding, unbroken dividends

Every row is BUYABLE or BLOCKED with a reason. Warnings run the same
LPPL evaluation over held positions, gold and the S&P 500 index.
"""

import numpy as np
import pandas as pd

from lppl import WindowGrid, evaluate_day, prescreen
from lppl_backtest import ROOT
from sim.market import combined_close, universe

DATA = ROOT / 'data'

LPPL_DIP2, STEADY_GIANTS = 'LPPL_DIP2', 'STEADY_GIANTS'
_GRIDS: list[WindowGrid] = []


def grids(cfg: dict) -> list[WindowGrid]:
    global _GRIDS
    if not _GRIDS:
        _GRIDS = [WindowGrid(n, cfg) for n in cfg['lppl']['windows']]
    return _GRIDS


def flag_state(close: pd.Series, cfg: dict, n_evals: int = 3) -> dict:
    """Evaluate the last `n_evals` refit days; report the current flag.

    Persistent flag = the two most recent evaluations both qualify, the
    same rule the backtests used."""
    g = cfg['lppl']
    c = close.to_numpy()
    if len(c) < min(g['windows']) + g['refit_every'] * n_evals:
        return {'votes': 0, 'flagged2': False, 'flagged3': False,
                'tc_date': None, 'tc_ahead': np.nan, 'r2': np.nan}
    log_c = np.log(c)
    idx = [len(c) - 1 - g['refit_every'] * k for k in range(n_evals)][::-1]
    evs = [evaluate_day(log_c, i, grids(cfg), cfg) for i in idx]
    last = evs[-1]
    tc_date = None
    if np.isfinite(last['tc_ahead']):
        tc_date = (close.index[-1]
                   + pd.Timedelta(days=int(round(last['tc_ahead'] * 7 / 5))))
    return {
        'votes': int(last['votes']),
        'flagged2': all(e['votes'] >= g['min_votes_loose'] for e in evs[-2:]),
        'flagged3': all(e['votes'] >= g['min_votes'] for e in evs[-2:]),
        'tc_date': tc_date.date().isoformat() if tc_date is not None else None,
        'tc_ahead': float(last['tc_ahead']),
        'r2': float(last['mean_r2']),
    }


def market_light(cfg: dict) -> dict:
    """SPY > 200d SMA and 20d vol below its trailing 756d 90th pct."""
    spy = combined_close(cfg['data']['benchmark'])
    trend = bool(spy.iloc[-1] > spy.rolling(200).mean().iloc[-1])
    v = spy.pct_change().rolling(20).std()
    calm = bool(v.iloc[-1] <= v.rolling(756).quantile(0.90).iloc[-1])
    return {'green': trend and calm, 'trend': trend, 'calm': calm,
            'spy': float(spy.iloc[-1]),
            'sma200': float(spy.rolling(200).mean().iloc[-1])}


def scan_lppl(cfg: dict, light: dict, max_names: int = 25) -> list[dict]:
    """Universe scan for lppl_dip2 entry candidates."""
    g = cfg['lppl']
    out = []
    for t in universe():
        if t == cfg['data']['benchmark']:
            continue
        s = combined_close(t)
        if s is None or len(s) < min(g['windows']) + 20:
            continue
        c = s.to_numpy()
        if not prescreen(c, len(c) - 1, cfg):
            continue
        hi20 = float(np.max(c[-g['dip_high_window']:]))
        dip = c[-1] <= (1 - g['dip_from_high']) * hi20
        st = flag_state(s, cfg)
        if not (st['flagged2'] and st['tc_ahead'] > 0):
            continue
        blocked = []
        if not dip:
            blocked.append(f'no 4% dip (now {100 * (c[-1] / hi20 - 1):+.1f}% '
                           'from the 20d high)')
        if not light['green']:
            blocked.append('market light red')
        out.append({
            'symbol': t, 'source': LPPL_DIP2, 'price': float(c[-1]),
            'buyable': not blocked, 'reason': '; '.join(blocked),
            'detail': f"votes {st['votes']}/5, R2 {st['r2']:.3f}, "
                      f"tc ~{st['tc_date']}, "
                      f"{100 * (c[-1] / hi20 - 1):+.1f}% from 20d high",
            'votes': st['votes'], 'tc_date': st['tc_date']})
    out.sort(key=lambda r: (-r['votes'], r['symbol']))
    return out[:max_names]


def scan_giants(cfg: dict, light: dict, max_names: int = 25) -> list[dict]:
    """Universe scan for Steady-Giants qualifiers (STEADY_GIANTS_SPEC)."""
    from giants_features import div_record, slope_r2, ttm_eps
    d = cfg['data']
    divs = pd.read_parquet(DATA / 'dividends.parquet')
    eps = pd.read_parquet(DATA / 'earnings_eps.parquet') \
        .dropna(subset=['eps']).sort_values('date')
    div_by = {t: g for t, g in divs.groupby('ticker')}
    eps_by = {t: g for t, g in eps.groupby('ticker')}
    pe_hist = _pe_history()

    rows, vols = [], []
    for t in universe():
        if t == d['benchmark']:
            continue
        s = combined_close(t)
        if s is None or len(s) < 1260:
            continue
        c = s.to_numpy()
        if c[-1] <= d['min_price']:
            continue
        vol = float(np.std(pd.Series(c[-756:]).pct_change().dropna()))
        slope, r2 = slope_r2(np.log(c[-1260:]))
        dv = div_by.get(t)
        ysum = dv.groupby(dv['date'].dt.year)['amount'].sum() \
            if dv is not None and len(dv) else pd.Series(dtype=float)
        paid, cut = div_record(ysum, s.index[-1].year)
        eg = eps_by.get(t)
        ttm = ttm_eps(eg['date'].to_numpy(), eg['eps'].to_numpy(),
                      s.index[-1]) if eg is not None else np.nan
        vols.append(vol)
        if not (paid and not cut and slope > 0 and r2 >= 0.7
                and np.isfinite(ttm) and ttm > 0):
            continue
        rows.append({'symbol': t, 'vol': vol, 'r2': r2,
                     'price': float(c[-1]), 'pe': float(c[-1] / ttm)})

    if not rows:
        return []
    cut_vol = float(np.quantile(vols, 1 / 3)) if vols else np.inf
    out = []
    for r in rows:
        blocked = []
        if r['vol'] > cut_vol:
            blocked.append('not in the calmest third of the market')
        p90 = pe_hist.get(r['symbol'])
        if p90 is not None and r['pe'] > p90:
            blocked.append(f"P/E {r['pe']:.1f} above its own history "
                           f'p90 ({p90:.1f}) — too expensive now')
        if not light['green']:
            blocked.append('market light red')
        detail = (f"5y straightness R2 {r['r2']:.2f}, vol {100 * r['vol']:.2f}%/d, "
                  f"P/E {r['pe']:.1f}"
                  + (f" (own p90 {p90:.1f})" if p90 is not None else ''))
        out.append({'symbol': r['symbol'], 'source': STEADY_GIANTS,
                    'price': r['price'], 'buyable': not blocked,
                    'reason': '; '.join(blocked), 'detail': detail,
                    'votes': 0, 'tc_date': None, 'r2': r['r2']})
    out.sort(key=lambda x: (not x['buyable'], -x['r2']))
    return out[:max_names]


def warnings_for(symbols: list[str], cfg: dict) -> list[dict]:
    """Bubble warnings for holdings plus gold and the S&P 500 index."""
    out = []
    for s in symbols:
        series = combined_close(s)
        if series is None or not len(series):
            from sim.market import raw_frame
            df = raw_frame(s)
            series = df['close'] if df is not None else None
        if series is None or len(series) < min(cfg['lppl']['windows']) + 20:
            continue
        st = flag_state(series, cfg)
        level = 'CERTIFIED (3+ of 5)' if st['flagged3'] \
            else 'FLAGGED (2 of 5)' if st['flagged2'] else 'clear'
        out.append({'symbol': s, 'level': level, 'votes': st['votes'],
                    'tc_date': st['tc_date'], 'r2': st['r2'],
                    'price': float(series.iloc[-1]),
                    'warn': st['flagged2']})
    return out


def _pe_history() -> dict[str, float]:
    """Own-history P/E 90th percentile per ticker, from the giants
    monthly table; empty when that table has not been built yet."""
    path = DATA / 'giants_monthly.parquet'
    if not path.exists():
        return {}
    tab = pd.read_parquet(path)
    if 'pe' not in tab:
        return {}
    return (tab.dropna(subset=['pe']).groupby('ticker')['pe']
            .quantile(0.90).to_dict())
