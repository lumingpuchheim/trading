"""Download daily OHLCV for the universe and cache as parquet under data/.

Run once:  python download_data.py           # S&P 1500 -> data/ohlcv/
           python download_data.py --wide    # the rest of the US market
                                             #   -> data/ohlcv_wide/

The default universe is the current S&P 1500 (see below). `--wide` adds
every other US-listed common stock above $100M market cap and $5 a share
(Nasdaq screener API: NASDAQ + NYSE + AMEX), which is the Russell 2000 /
Nasdaq Composite / NYSE Composite ground the S&P 1500 leaves out. It is
written to a SEPARATE directory so the original universe -- and every
number already recorded against it -- stays exactly as it was; the
backtest opts in with `--wide`.

The $100M / $5 floor is a download-cost bound, not a filter that decides
anything: the pipeline's own liquidity gate ($5M average daily dollar
volume, config `data:`) is far stricter and is what actually binds.

Survivorship bias, and it is WORSE here than for the S&P 1500: this is a
snapshot of what is listed TODAY. Small caps that failed are absent, and
small caps fail more often than index members. See LIMITATIONS.md.
Prices are split/dividend adjusted (yfinance auto_adjust).
"""

import io
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import yaml
import yfinance as yf

# Wikipedia no longer lists Russell 1000 constituents, and the iShares IWB
# holdings CSV sits behind a bot wall. The S&P 1500 (500 + 400 + 600) is a
# comparable broad, liquid, current-constituent US universe.
WIKI_URLS = [
    'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
    'https://en.wikipedia.org/wiki/List_of_S%26P_400_companies',
    'https://en.wikipedia.org/wiki/List_of_S%26P_600_companies',
]
BATCH_SIZE = 100        # override with --batch=N on a retry pass
PAUSE = 1.0             # seconds between batches; --pause=N to be gentler
MIN_ROWS = 260  # need at least ~1 year of data to compute anything

SCREENER = ('https://api.nasdaq.com/api/screener/stocks'
            '?tableonly=true&limit=25&offset=0&download=true')
WIDE_MIN_MCAP = 100e6
WIDE_MIN_PRICE = 5.0
# rows the screener returns that are not common stock
NOT_COMMON = ('Warrant|Unit|Preferred|Depositary|Right|Notes|Debenture'
              '|%|Trust Units')


def load_config() -> dict:
    with open(Path(__file__).parent / 'config.yaml') as f:
        return yaml.safe_load(f)


def fetch_universe() -> list[str]:
    """Current S&P 1500 constituents from Wikipedia (three component lists)."""
    tickers: set[str] = set()
    for url in WIKI_URLS:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
        resp.raise_for_status()
        for table in pd.read_html(io.StringIO(resp.text)):
            cols = [str(c).lower() for c in table.columns]
            if 'symbol' in cols and len(table) > 300:
                col = table.columns[cols.index('symbol')]
                syms = table[col].astype(str).str.strip().str.replace('.', '-', regex=False)
                tickers.update(s for s in syms if s and s != 'nan')
                break
        else:
            raise RuntimeError(f'No constituents table found at {url}')
    return sorted(tickers)


def fetch_wide_universe(have: set[str]) -> list[str]:
    """Every US-listed common stock the S&P 1500 does not already cover,
    above the market-cap and price floor. Nasdaq's screener covers the
    NASDAQ, NYSE and AMEX listings, so this is the investable American
    market outside the index we started with."""
    resp = requests.get(SCREENER, timeout=60,
                        headers={'User-Agent': 'Mozilla/5.0',
                                 'Accept': 'application/json'})
    resp.raise_for_status()
    d = pd.DataFrame(resp.json()['data']['rows'])
    d = d[~d['name'].str.contains(NOT_COMMON, case=False, na=False)]
    d['mcap'] = pd.to_numeric(d['marketCap'], errors='coerce')
    d['px'] = pd.to_numeric(d['lastsale'].str.replace('$', '', regex=False),
                            errors='coerce')
    d['sym'] = (d['symbol'].str.strip().str.replace('.', '-', regex=False)
                .str.replace('/', '-', regex=False))     # BRK/B -> BRK-B
    d = d[(d['mcap'] >= WIDE_MIN_MCAP) & (d['px'] >= WIDE_MIN_PRICE)
          & ~d['sym'].isin(have)]
    return sorted(set(d['sym']) - {''})


def clean_ohlcv(raw: pd.DataFrame) -> pd.DataFrame | None:
    df = raw.rename(columns=str.lower)[['open', 'high', 'low', 'close', 'volume']]
    df = df.dropna(subset=['open', 'high', 'low', 'close'])
    df = df[df['close'] > 0]
    if len(df) < MIN_ROWS:
        return None
    df.index.name = 'date'
    return df


def main() -> None:
    global BATCH_SIZE, PAUSE
    for a in sys.argv[1:]:
        if a.startswith('--batch='):
            BATCH_SIZE = int(a.split('=')[1])
        elif a.startswith('--pause='):
            PAUSE = float(a.split('=')[1])
    wide = '--wide' in sys.argv
    cfg = load_config()
    data_dir = Path(__file__).parent / cfg['data']['cache_dir']
    ohlcv_dir = data_dir / ('ohlcv_wide' if wide else 'ohlcv')
    ohlcv_dir.mkdir(parents=True, exist_ok=True)
    start = cfg['data']['start_date']

    if wide:
        have = {p.stem for p in (data_dir / 'ohlcv').glob('*.parquet')}
        tickers = fetch_wide_universe(have)
        pd.Series(tickers, name='ticker').to_csv(
            data_dir / 'universe_wide.csv', index=False)
        print(f'Wide universe: {len(tickers)} tickers outside the S&P 1500 '
              f'({len(have)} already cached)')
    else:
        tickers = fetch_universe()
        pd.Series(tickers, name='ticker').to_csv(data_dir / 'universe.csv',
                                                 index=False)
        print(f'Universe: {len(tickers)} tickers')

    dropped: list[tuple[str, str]] = []
    saved = 0
    todo = tickers if wide else tickers + [cfg['data']['benchmark']]
    if wide:
        # a re-run fills the gaps a failed batch left, without re-fetching
        todo = [t for t in todo if not (ohlcv_dir / f'{t}.parquet').exists()]
        print(f'{len(todo)} still to fetch')
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        raw = yf.download(batch, start=start, auto_adjust=True, group_by='ticker',
                          progress=False, threads=True)
        for t in batch:
            try:
                sub = raw[t] if len(batch) > 1 else raw
            except KeyError:
                dropped.append((t, 'no data returned'))
                continue
            df = clean_ohlcv(sub)
            if df is None:
                dropped.append((t, f'fewer than {MIN_ROWS} usable rows'))
                continue
            df.to_parquet(ohlcv_dir / f'{t}.parquet')
            saved += 1
        print(f'  {min(i + BATCH_SIZE, len(todo))}/{len(todo)} done, {saved} saved, {len(dropped)} dropped')
        time.sleep(PAUSE)  # be polite to Yahoo

    with open(data_dir / f'download_log{"_wide" if wide else ""}.txt',
              'w') as f:
        for t, reason in dropped:
            f.write(f'{t}\t{reason}\n')
    print(f'Saved {saved} tickers, dropped {len(dropped)} (see data/download_log.txt)')


if __name__ == '__main__':
    sys.exit(main())
