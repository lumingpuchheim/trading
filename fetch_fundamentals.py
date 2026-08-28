"""Quarterly sales, gross profit and net income from SEC EDGAR XBRL.

-> data/fundamentals_quarterly.parquet
   (ticker, period_end, filed, revenue, gross_profit, net_income)

WHY THIS EXISTS. MINERVINI_SPEC.md sections 8 and 8b record that sales
and margins are "not obtainable — the provider returns 5 quarters for
AAPL and 6 for POWL". That is true of yfinance and false of the data:
EDGAR's XBRL company facts carry 50-60 quarters per filer back to
2009-2010, which is enough for Code 33's three-quarter acceleration test
in both legs the EPS gate was missing.

Two properties this source has that the cached EPS table does not:

  - **Point in time.** Every fact carries `filed`, the date the number
    became public. A gate that only reads facts with filed <= the
    decision day cannot use a number nobody had yet, and restatements
    appear as later facts rather than silently overwriting the original.
  - **Margins.** GrossProfit and NetIncomeLoss are reported alongside
    revenue, so gross and net margin are computable per quarter.

Limits, stated plainly:

  - **XBRL was phased in 2009-2011** (large filers first). History before
    ~2010 is thin and before 2009 mostly absent, so the dev period
    (2007-2018) is only partly covered while the test period is complete.
  - **Revenue changed concept names** at the ASC 606 transition. Several
    tags are tried in order; a company using an unusual tag yields no
    revenue and is simply absent rather than guessed at.
  - **10-K rows are annual, not Q4.** Only facts whose start/end span
    80-100 days are kept, so Q4 is absent unless the filer reports it as
    a quarter. This undercounts quarters rather than inventing them.

Run: python fetch_fundamentals.py               # the S&P 1500 universe
     python fetch_fundamentals.py ohlcv_wide    # the wide universe
"""

import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).parent
UA = {'User-Agent': 'ElterngeldAdvisor trading research luming.sjtu@gmail.com'}
TICKER_MAP = 'https://www.sec.gov/files/company_tickers.json'
FACTS = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'
PAUSE = 0.15                      # SEC asks for <= 10 requests a second
MIN_DAYS, MAX_DAYS = 80, 100      # a fact spanning one quarter

REVENUE = ('RevenueFromContractWithCustomerExcludingAssessedTax',
           'Revenues', 'SalesRevenueNet',
           'RevenueFromContractWithCustomerIncludingAssessedTax',
           'SalesRevenueGoodsNet')
GROSS = ('GrossProfit',)
NET = ('NetIncomeLoss',)


def quarterly(facts: dict, concepts: tuple) -> pd.DataFrame:
    """Every quarter-length USD fact under any of `concepts`, newest
    filing wins for a given period end."""
    rows = []
    for c in concepts:
        for x in facts.get(c, {}).get('units', {}).get('USD', []):
            if not x.get('start') or x.get('form') not in ('10-Q', '10-K'):
                continue
            span = (pd.Timestamp(x['end']) - pd.Timestamp(x['start'])).days
            if MIN_DAYS <= span <= MAX_DAYS:
                rows.append((x['end'], x['filed'], float(x['val'])))
    if not rows:
        return pd.DataFrame(columns=['period_end', 'filed', 'val'])
    d = pd.DataFrame(rows, columns=['period_end', 'filed', 'val'])
    d = d.sort_values('filed').drop_duplicates('period_end', keep='first')
    return d.sort_values('period_end')


def main() -> None:
    src = ROOT / 'data' / (sys.argv[1] if len(sys.argv) > 1 else 'ohlcv')
    tickers = sorted(p.stem for p in src.glob('*.parquet') if p.stem != 'SPY')
    out = ROOT / 'data' / ('fundamentals_quarterly.parquet'
                           if len(sys.argv) == 1 else
                           'fundamentals_quarterly_wide.parquet')

    m = requests.get(TICKER_MAP, headers=UA, timeout=60).json()
    cik = {v['ticker'].replace('.', '-'): int(v['cik_str']) for v in m.values()}
    todo = [(t, cik[t]) for t in tickers if t in cik]
    print(f'{len(todo)} of {len(tickers)} tickers mapped to a CIK', flush=True)

    frames, missing = [], []
    for k, (t, c) in enumerate(todo):
        try:
            r = requests.get(FACTS.format(cik=c), headers=UA, timeout=60)
            if r.status_code != 200:
                missing.append(t)
            else:
                f = r.json().get('facts', {}).get('us-gaap', {})
                rev, gp, ni = (quarterly(f, REVENUE), quarterly(f, GROSS),
                               quarterly(f, NET))
                if len(rev):
                    d = rev.rename(columns={'val': 'revenue'})
                    for other, name in ((gp, 'gross_profit'), (ni, 'net_income')):
                        d = d.merge(other[['period_end', 'val']].rename(
                            columns={'val': name}), on='period_end', how='left')
                    d.insert(0, 'ticker', t)
                    frames.append(d)
                else:
                    missing.append(t)
        except Exception:
            missing.append(t)
        time.sleep(PAUSE)
        if (k + 1) % 100 == 0:
            print(f'{k + 1}/{len(todo)} ok={len(frames)} missing={len(missing)}',
                  flush=True)

    df = pd.concat(frames, ignore_index=True)
    df['period_end'] = pd.to_datetime(df['period_end'])
    df['filed'] = pd.to_datetime(df['filed'])
    df.to_parquet(out, index=False)
    q = df.groupby('ticker').size()
    print(f'saved {len(df)} rows, {df.ticker.nunique()} tickers, median '
          f'{q.median():.0f} quarters, {df.period_end.min().date()} .. '
          f'{df.period_end.max().date()}; {len(missing)} without usable revenue')


if __name__ == '__main__':
    main()
