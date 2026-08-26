"""Live market data for the simulator (SIMULATOR_SPEC section 8).

The frozen research cache (data/) is READ-ONLY here. New rows land in
data_live/ and are concatenated on read:

  data_live/updates/<ticker>.parquet   adjusted closes after the cache's
                                       last date — feeds the detectors
                                       (same convention as the backtests)
  data_live/raw/<symbol>.parquet       RAW open/close — feeds fills, so
                                       every executed price is auditable
                                       against a public quote
  data_live/divs/<symbol>.parquet      dividends per share, as announced
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
RESEARCH = ROOT / 'data' / 'ohlcv'
LIVE = ROOT / 'data_live'
UPDATES, RAW, DIVS = LIVE / 'updates', LIVE / 'raw', LIVE / 'divs'
FX_SYMBOL = 'EURUSD=X'


def _safe(symbol: str) -> str:
    return symbol.replace('=', '_').replace('^', '_')


def _ensure_dirs() -> None:
    for d in (UPDATES, RAW, DIVS):
        d.mkdir(parents=True, exist_ok=True)


def universe() -> list[str]:
    return sorted(p.stem for p in RESEARCH.glob('*.parquet'))


def combined_close(ticker: str) -> pd.Series | None:
    """Adjusted close: frozen research history + live updates."""
    parts = []
    base = RESEARCH / f'{ticker}.parquet'
    if base.exists():
        parts.append(pd.read_parquet(base)['close'])
    upd = UPDATES / f'{_safe(ticker)}.parquet'
    if upd.exists():
        parts.append(pd.read_parquet(upd)['close'])
    if not parts:
        return None
    s = pd.concat(parts)
    s = s[~s.index.duplicated(keep='last')].sort_index()
    return s.dropna()


def update_adjusted(tickers: list[str], chunk: int = 120,
                    verbose: bool = True) -> int:
    """Append adjusted closes newer than what we already hold."""
    import yfinance as yf
    _ensure_dirs()
    written = 0
    for k in range(0, len(tickers), chunk):
        batch = tickers[k:k + chunk]
        starts = {}
        for t in batch:
            s = combined_close(t)
            starts[t] = (s.index[-1] + pd.Timedelta(days=1)) if s is not None \
                and len(s) else pd.Timestamp('2005-01-01')
        start = min(starts.values())
        if start > pd.Timestamp.today().normalize():
            continue
        df = yf.download(batch, start=start.date().isoformat(),
                         auto_adjust=True, progress=False, group_by='ticker',
                         threads=True)
        if df is None or df.empty:
            continue
        for t in batch:
            try:
                sub = df[t]['Close'] if isinstance(df.columns, pd.MultiIndex) \
                    else df['Close']
            except KeyError:
                continue
            sub = sub.dropna()
            sub = sub[sub.index >= starts[t]]
            if not len(sub):
                continue
            path = UPDATES / f'{_safe(t)}.parquet'
            out = pd.DataFrame({'close': sub})
            if path.exists():
                out = pd.concat([pd.read_parquet(path), out])
                out = out[~out.index.duplicated(keep='last')].sort_index()
            out.index.name = 'date'
            out.to_parquet(path)
            written += 1
        if verbose:
            print(f'  adjusted {min(k + chunk, len(tickers))}/{len(tickers)}',
                  flush=True)
    return written


def update_raw(symbols: list[str], lookback_days: int = 30,
               history_start: str = '2005-01-01') -> None:
    """Refresh RAW open/close for the symbols we actually trade.

    A symbol we have never seen is fetched in full (the bubble warnings
    need years of history, not a month); known symbols only top up."""
    import yfinance as yf
    _ensure_dirs()
    recent = (pd.Timestamp.today() - pd.Timedelta(days=lookback_days)).date()
    for s in symbols:
        known = (RAW / f'{_safe(s)}.parquet').exists()
        start = recent.isoformat() if known else history_start
        df = yf.download(s, start=start, auto_adjust=False, progress=False)
        if df is None or df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        out = df[['Open', 'Close']].rename(columns={'Open': 'open',
                                                    'Close': 'close'})
        out.index.name = 'date'
        path = RAW / f'{_safe(s)}.parquet'
        if known and path.exists():
            out = pd.concat([pd.read_parquet(path), out])
            out = out[~out.index.duplicated(keep='last')].sort_index()
        out.to_parquet(path)


def raw_frame(symbol: str) -> pd.DataFrame | None:
    path = RAW / f'{_safe(symbol)}.parquet'
    return pd.read_parquet(path) if path.exists() else None


def opens_on(symbols: list[str], date: str) -> dict[str, float]:
    """Raw opening prices on `date` (missing symbols simply absent)."""
    out = {}
    for s in symbols:
        df = raw_frame(s)
        if df is None:
            continue
        hit = df[df.index.normalize() == pd.Timestamp(date)]
        if len(hit) and np.isfinite(hit['open'].iloc[0]):
            out[s] = float(hit['open'].iloc[0])
    return out


def last_close(symbol: str) -> float | None:
    df = raw_frame(symbol)
    if df is None or not len(df):
        return None
    return float(df['close'].iloc[-1])


def fx_eurusd(date: str | None = None) -> float:
    """USD per EUR on `date` (or the latest available)."""
    df = raw_frame(FX_SYMBOL)
    if df is None or not len(df):
        raise RuntimeError('no EURUSD data — run update_raw first')
    if date:
        hit = df[df.index.normalize() <= pd.Timestamp(date)]
        if len(hit):
            return float(hit['close'].iloc[-1])
    return float(df['close'].iloc[-1])


def update_dividends(symbols: list[str]) -> None:
    import yfinance as yf
    _ensure_dirs()
    for s in symbols:
        try:
            dv = yf.Ticker(s).dividends
        except Exception:
            continue
        if dv is None or not len(dv):
            continue
        out = pd.DataFrame({'amount': dv.to_numpy()},
                           index=pd.DatetimeIndex(
                               [pd.Timestamp(d).tz_localize(None).normalize()
                                for d in dv.index], name='date'))
        out.to_parquet(DIVS / f'{_safe(s)}.parquet')


def dividends_on(symbol: str, date: str) -> float:
    """Dividend per share with ex-date == `date`, else 0."""
    path = DIVS / f'{_safe(symbol)}.parquet'
    if not path.exists():
        return 0.0
    df = pd.read_parquet(path)
    hit = df[df.index.normalize() == pd.Timestamp(date)]
    return float(hit['amount'].iloc[0]) if len(hit) else 0.0
