"""Steady Giants phase 1: dividends, reported quarterly EPS, T-bill yield.

One-time fetches, cached as parquet (delete a file to re-fetch):
  data/dividends.parquet    ticker, date (ex-date), amount (split-adjusted)
  data/earnings_eps.parquet ticker, date, eps (reported, NaN for scheduled)
  data/dgs3mo.parquet       date, yield_pct (FRED DGS3MO, ^IRX fallback)

Run: python giants_data.py
"""

import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config

DIV = ROOT / 'data' / 'dividends.parquet'
EPS = ROOT / 'data' / 'earnings_eps.parquet'
TBILL = ROOT / 'data' / 'dgs3mo.parquet'


def universe() -> list[str]:
    cfg = load_config()
    d = ROOT / cfg['data']['cache_dir'] / 'ohlcv'
    return sorted(p.stem for p in d.glob('*.parquet')
                  if p.stem != cfg['data']['benchmark'])


def fetch_div_eps(tickers: list[str]) -> None:
    import yfinance as yf
    div_rows, eps_rows, failed = [], [], []
    for k, t in enumerate(tickers, 1):
        tk = yf.Ticker(t)
        try:
            dv = tk.dividends
            for ts, amt in dv.items():
                div_rows.append({'ticker': t,
                                 'date': pd.Timestamp(pd.Timestamp(ts).date()),
                                 'amount': float(amt)})
        except Exception:
            failed.append((t, 'div'))
        try:
            e = tk.get_earnings_dates(limit=100)
            if e is not None and len(e):
                for ts, row in e.iterrows():
                    rep = row.get('Reported EPS')
                    eps_rows.append({
                        'ticker': t,
                        'date': pd.Timestamp(pd.Timestamp(ts).date()),
                        'eps': float(rep) if pd.notna(rep) else np.nan})
        except Exception:
            failed.append((t, 'eps'))
        if k % 50 == 0:
            print(f'  {k}/{len(tickers)} tickers '
                  f'({len(div_rows)} dividends, {len(eps_rows)} eps rows)',
                  flush=True)
    pd.DataFrame(div_rows).drop_duplicates().to_parquet(DIV)
    pd.DataFrame(eps_rows).drop_duplicates(subset=['ticker', 'date']) \
        .to_parquet(EPS)
    print(f'dividends: {len(div_rows)} rows -> {DIV}')
    print(f'eps: {len(eps_rows)} rows -> {EPS}')
    if failed:
        print(f'failures ({len(failed)}): {failed[:20]}')


def fetch_tbill() -> None:
    try:
        tb = pd.read_csv('https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO')
        tb.columns = ['date', 'yield_pct']
        tb['date'] = pd.to_datetime(tb['date'])
        tb['yield_pct'] = pd.to_numeric(tb['yield_pct'], errors='coerce')
        tb = tb.dropna()
        src = 'FRED DGS3MO'
    except Exception:
        import yfinance as yf
        irx = yf.Ticker('^IRX').history(start='2004-01-01')['Close']
        tb = pd.DataFrame({'date': pd.to_datetime([pd.Timestamp(d).date() for d in irx.index]),
                           'yield_pct': irx.to_numpy()})
        src = '^IRX fallback'
    tb.to_parquet(TBILL)
    print(f'T-bill: {len(tb)} rows ({src}, '
          f'{tb["date"].min().date()} -> {tb["date"].max().date()}) -> {TBILL}')


def main() -> None:
    tks = universe()
    print(f'{len(tks)} tickers')
    if not TBILL.exists():
        fetch_tbill()
    if not (DIV.exists() and EPS.exists()):
        fetch_div_eps(tks)
    else:
        print('dividends/eps already cached')


if __name__ == '__main__':
    main()
