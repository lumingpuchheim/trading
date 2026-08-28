"""Industry group per ticker, from the Nasdaq screener.

-> data/industries.csv (ticker, sector, industry)

The input to MINERVINI_SPEC.md section 16. Section 10.2 listed
industry-group strength as "buildable, small data acquisition"; this is
the acquisition. 1,491 of the 1,496 S&P 1500 names are classified into
141 industry groups, which is the granularity closest to the ~197 groups
the source's school uses; the 13 sectors are too coarse to be a signal.

The classification is CURRENT and is applied to historical days. A
company that changed business is mislabelled in its past. See the
lookahead note in spec section 16 before trusting any result built on it.

Run: python fetch_industries.py               # the S&P 1500 universe
     python fetch_industries.py ohlcv_wide    # the wide universe
"""

import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).parent
SCREENER = ('https://api.nasdaq.com/api/screener/stocks'
            '?tableonly=true&limit=25&offset=0&download=true')


def main() -> None:
    src = ROOT / 'data' / (sys.argv[1] if len(sys.argv) > 1 else 'ohlcv')
    have = {p.stem for p in src.glob('*.parquet')} - {'SPY'}

    r = requests.get(SCREENER, timeout=60,
                     headers={'User-Agent': 'Mozilla/5.0',
                              'Accept': 'application/json'})
    r.raise_for_status()
    d = pd.DataFrame(r.json()['data']['rows'])
    d['ticker'] = (d['symbol'].str.strip().str.replace('.', '-', regex=False)
                   .str.replace('/', '-', regex=False))
    d = d[d['ticker'].isin(have)]
    d['industry'] = d['industry'].astype(str).str.strip()
    d = d[d['industry'] != ''][['ticker', 'sector', 'industry']]
    d = d.drop_duplicates('ticker').sort_values('ticker')

    out = ROOT / 'data' / ('industries.csv' if len(sys.argv) == 1
                           else 'industries_wide.csv')
    d.to_csv(out, index=False)
    n = d.groupby('industry').size()
    print(f'{len(d)} of {len(have)} tickers classified into '
          f'{d.industry.nunique()} groups ({int((n >= 5).sum())} with >= 5 '
          f'members, covering {int(n[n >= 5].sum())} tickers) -> {out.name}')


if __name__ == '__main__':
    main()
