"""Earnings adjacency of lppl_dip2 stop exits (descriptive, no changes).

Question from the anatomy study: stops average -10/-11% against an 8%
nominal stop, with 7-9 fills per period below -15% — are those gap nights
earnings reports?

Design: fetch historical earnings dates (yfinance, cached to
data/earnings_dates.parquet) for all traded tickers. A stop exit is
"earnings-adjacent" if a report falls within [trigger day - 1, fill day],
where trigger = the close that broke the stop (one trading day before the
fill). Control/base rate: tc exits are scheduled by the clock, not by
price shocks, so their adjacency rate estimates how often a random
~3-trading-day window contains a report (~5-6% expected).

Run: python lppl_earnings.py   (delete the parquet to re-fetch)
"""

import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config

CACHE = ROOT / 'data' / 'earnings_dates.parquet'
BIG_LOSS = -0.15


def load_trades(cfg: dict) -> dict[str, pd.DataFrame]:
    results = ROOT / cfg['backtest']['results_dir']
    return {p: pd.read_csv(results / f'lppl_{p}_trades_lppl_dip2.csv',
                           parse_dates=['entry_date', 'exit_date'])
            for p in ('dev', 'test')}


def fetch_earnings(tickers: list[str]) -> pd.DataFrame:
    import yfinance as yf
    rows, failed = [], []
    for k, t in enumerate(sorted(tickers), 1):
        try:
            d = yf.Ticker(t).get_earnings_dates(limit=100)
            if d is None or d.empty:
                failed.append(t)
                continue
            for ts in d.index.tz_convert('America/New_York'):
                rows.append({'ticker': t, 'date': pd.Timestamp(ts.date()),
                             'hour': ts.hour})
        except Exception:
            failed.append(t)
        if k % 50 == 0:
            print(f'  {k}/{len(tickers)} tickers fetched', flush=True)
    print(f'fetched {len(tickers) - len(failed)}/{len(tickers)} tickers; '
          f'no data: {failed}')
    return pd.DataFrame(rows).drop_duplicates()


def main() -> None:
    cfg = load_config()
    trades = load_trades(cfg)
    tickers = sorted(set(pd.concat(trades.values())['ticker']))
    print(f'{len(tickers)} unique traded tickers')

    if CACHE.exists():
        earn = pd.read_parquet(CACHE)
        print(f'cached earnings dates: {len(earn)} rows, '
              f'{earn["ticker"].nunique()} tickers')
    else:
        earn = fetch_earnings(tickers)
        earn.to_parquet(CACHE)
        print(f'{len(earn)} earnings dates -> {CACHE}')

    spy = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'ohlcv' / 'SPY.parquet')
    cal = spy.index
    cal_pos = {d: i for i, d in enumerate(cal)}
    edates = {t: set(g['date']) for t, g in earn.groupby('ticker')}

    def adjacent(ticker: str, exit_date: pd.Timestamp, back: int) -> bool | None:
        """Report within [exit - back trading days, exit]? None = no data."""
        ed = edates.get(ticker)
        if not ed:
            return None
        j = cal_pos.get(exit_date)
        if j is None:
            return None
        window = set(cal[max(0, j - back): j + 1])
        return bool(window & ed)

    for p, t in trades.items():
        t = t.copy()
        # coverage: ticker must have at least one report before the exit,
        # otherwise its history simply doesn't reach back that far
        first_rep = earn.groupby('ticker')['date'].min()
        t['covered'] = t.apply(
            lambda r: r['ticker'] in first_rep.index
            and first_rep[r['ticker']] <= r['exit_date'], axis=1)
        t['adj'] = t.apply(
            lambda r: adjacent(r['ticker'], r['exit_date'], back=2)
            if r['covered'] else None, axis=1)
        t['adj5'] = t.apply(
            lambda r: adjacent(r['ticker'], r['exit_date'], back=5)
            if r['covered'] else None, axis=1)
        c = t[t['covered']]
        print(f'\n=== [{p}] coverage {t["covered"].mean():.0%} '
              f'({len(c)}/{len(t)} trades) ===')
        for reason in ('stop', 'tc'):
            s = c[c['exit_reason'] == reason]
            if not len(s):
                continue
            a = s['adj'].astype(bool)
            print(f'{reason}: n={len(s)}, earnings within trigger window '
                  f'(3 trading days): {a.mean():.1%}  '
                  f'| within 6 days: {s["adj5"].astype(bool).mean():.1%}')
            print(f'   avg ret adjacent {s.loc[a, "ret_net"].mean():+.4f} '
                  f'(n={a.sum()}) vs not {s.loc[~a, "ret_net"].mean():+.4f} '
                  f'(n={(~a).sum()})')
        big = c[(c['exit_reason'] == 'stop') & (c['ret_net'] <= BIG_LOSS)]
        if len(big):
            print(f'catastrophic stops (<= {BIG_LOSS:.0%}): n={len(big)}, '
                  f'earnings-adjacent {big["adj"].astype(bool).mean():.1%} '
                  f'| within 6 days {big["adj5"].astype(bool).mean():.1%}')
            print(big[['ticker', 'exit_date', 'ret_net', 'adj', 'adj5']]
                  .sort_values('ret_net').to_string(index=False))

        # exposure context: how many reports does a typical hold sit through?
        def n_reports_held(r):
            ed = edates.get(r['ticker'], set())
            return sum(1 for d in ed if r['entry_date'] <= d <= r['exit_date'])
        c = c.assign(n_rep=c.apply(n_reports_held, axis=1))
        print('reports sat through per trade (winners vs losers): '
              f'{c.loc[c["ret_net"] > 0, "n_rep"].mean():.2f} vs '
              f'{c.loc[c["ret_net"] <= 0, "n_rep"].mean():.2f}')


if __name__ == '__main__':
    main()
