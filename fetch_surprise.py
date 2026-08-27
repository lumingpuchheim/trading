"""Fetch quarterly EPS estimate / reported / surprise% for the universe.

-> data/earnings_surprise.parquet (ticker, date, eps_est, eps_rep,
   surprise_pct). Median ~96 quarters per ticker back to 1998, so it
   covers both backtest periods; this is the input to the SEPA catalyst
   leg (MINERVINI_SPEC.md 8c, `minervini.beat_gate`).

Takes ~45 min for the full 1,496-name universe. Reads an optional
data/_beat_tickers.csv to fetch a subset instead, writing wherever `out`
points; the two halves were fetched separately and are concatenated by
`minervini_backtest.build_panel`.

Run: python fetch_surprise.py
"""
import pathlib
import sys

import pandas as pd
import yfinance as yf

out = 'data/earnings_surprise.parquet'
subset = pathlib.Path('data/_beat_tickers.csv')
if subset.exists():
    tickers = pd.read_csv(subset)['ticker'].tolist()
else:
    tickers = sorted(p.stem for p in pathlib.Path('data/ohlcv').glob('*.parquet')
                     if p.stem != 'SPY')
rows, failed = [], []
for k, t in enumerate(tickers):
    try:
        ed = yf.Ticker(t).get_earnings_dates(limit=60)
        if ed is None or not len(ed):
            failed.append(t); continue
        d = ed.reset_index()
        d.columns = [str(c) for c in d.columns]
        date_col = d.columns[0]
        rows.append(pd.DataFrame({
            'ticker': t,
            'date': pd.to_datetime(d[date_col]).dt.tz_localize(None).dt.normalize(),
            'eps_est': d.get('EPS Estimate'),
            'eps_rep': d.get('Reported EPS'),
            'surprise_pct': d.get('Surprise(%)')}))
    except Exception:
        failed.append(t)
    if (k + 1) % 100 == 0:
        print(f'{k+1}/{len(tickers)}  ok={len(rows)} failed={len(failed)}', flush=True)
if rows:
    df = pd.concat(rows, ignore_index=True).dropna(subset=['eps_rep', 'eps_est'])
    df.to_parquet(out, index=False)
    print(f'saved {len(df)} rows, {df.ticker.nunique()} tickers, '
          f'{df.date.min().date()} .. {df.date.max().date()}, failed {len(failed)}')
else:
    print('nothing fetched'); sys.exit(1)
