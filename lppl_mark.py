"""Mark LPPL bubble windows on any single symbol.

Usage:  python lppl_mark.py [SYMBOL]     (default GLD — the gold ETF)

Evaluates EVERY refit day (no pre-screen — one symbol is cheap, so no
flag gaps), using the frozen detector constants. Prints the persistent
2-of-5 stretches with median tc and writes the marked chart to
results/<symbol>_bubbles.png. Data comes from the main ohlcv cache when
the symbol is there; otherwise it is downloaded once (yfinance,
auto_adjust total-return prices) and cached in data/extra/.
"""

import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl import WindowGrid, evaluate_day
from lppl_backtest import ROOT, load_config


def load_series(symbol: str, cfg: dict) -> pd.Series:
    main = ROOT / cfg['data']['cache_dir'] / 'ohlcv' / f'{symbol}.parquet'
    if main.exists():
        return pd.read_parquet(main)['close'].dropna()
    extra = ROOT / cfg['data']['cache_dir'] / 'extra'
    extra.mkdir(exist_ok=True)
    cache = extra / f'{symbol}.parquet'
    if cache.exists():
        return pd.read_parquet(cache)['close'].dropna()
    import yfinance as yf
    df = yf.download(symbol, start=cfg['data']['start_date'],
                     auto_adjust=True, progress=False)
    if df is None or df.empty:
        sys.exit(f'no data for {symbol}')
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    out = df[['Close']].rename(columns={'Close': 'close'})
    out.index.name = 'date'
    out.to_parquet(cache)
    print(f'downloaded {symbol}: {len(out)} days '
          f'({out.index[0].date()} .. {out.index[-1].date()}) -> {cache}')
    return out['close'].dropna()


def flag_windows(f: pd.DataFrame, n: int, thr: int, cfg: dict) -> np.ndarray:
    g = cfg['lppl']
    mark = np.zeros(n, bool)
    prev = 0
    for r in f.itertuples():
        if r.votes >= thr:
            prev += 1
            if prev >= g['persistence']:
                mark[r.i:min(n, r.i + g['refit_every'])] = True
        else:
            prev = 0
    return mark


def main() -> None:
    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else 'GLD'
    cfg = load_config()
    g = cfg['lppl']
    px = load_series(symbol, cfg)
    log_c = np.log(px.to_numpy())
    grids = [WindowGrid(n, cfg) for n in g['windows']]

    rows = [{'i': i, 'date': px.index[i],
             **evaluate_day(log_c, i, grids, cfg)}
            for i in range(min(g['windows']), len(px), g['refit_every'])]
    f = pd.DataFrame(rows)
    n = len(px)
    b2 = flag_windows(f, n, g['min_votes_loose'], cfg)
    b3 = flag_windows(f, n, g['min_votes'], cfg)

    print(f'{symbol}: {len(f)} evaluations | votes>=1: {(f.votes >= 1).sum()}, '
          f'>=2: {(f.votes >= 2).sum()}, >=3: {(f.votes >= 3).sum()}')
    print('\npersistent 2-of-5 stretches:')
    d = pd.Series(b2, index=px.index).astype(int).diff().fillna(0)
    starts, ends = px.index[d == 1], px.index[d == -1]
    if b2[0]:
        starts = starts.insert(0, px.index[0])
    if b2[-1]:
        ends = ends.append(pd.DatetimeIndex([px.index[-1]]))
    for s, e in zip(starts, ends):
        seg = f[(f.date >= s) & (f.date <= e)]
        print(f'  {s.date()} .. {e.date()}  (max votes {seg.votes.max()}, '
              f'median tc {seg.tc_ahead.median():.0f} trading days ahead)')
    last = f.iloc[-1]
    print(f'\nlatest evaluation {last.date.date()}: {last.votes} votes '
          f'-> {"IN a flagged bubble now" if b2[-1] else "no active flag"}')

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(px.index, px, lw=0.9, color='black', label=symbol)
    ax.fill_between(px.index, px.min(), px.max(), where=b2, alpha=0.25,
                    color='orange', label='2-of-5 bubble (loose gate)')
    ax.fill_between(px.index, px.min(), px.max(), where=b3, alpha=0.45,
                    color='red', label='3-of-5 (full certification)')
    ax.set_yscale('log')
    ax.legend(loc='upper left')
    ax.grid(alpha=0.3)
    ax.set_title(f'{symbol} with LPPL bubble flags '
                 f'(every 5 trading days, no pre-screen)')
    fig.tight_layout()
    out = ROOT / cfg['backtest']['results_dir'] / f'{symbol.lower()}_bubbles.png'
    fig.savefig(out, dpi=120)
    print(f'chart -> {out}')


if __name__ == '__main__':
    main()
