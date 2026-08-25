"""Download daily OHLCV for the universe and cache as parquet under data/.

Run once:  python download_data.py
Universe: current Russell 1000 constituents (Wikipedia). Survivorship bias —
see LIMITATIONS.md. Prices are split/dividend adjusted (yfinance auto_adjust).
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
BATCH_SIZE = 100
MIN_ROWS = 260  # need at least ~1 year of data to compute anything


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


def clean_ohlcv(raw: pd.DataFrame) -> pd.DataFrame | None:
    df = raw.rename(columns=str.lower)[['open', 'high', 'low', 'close', 'volume']]
    df = df.dropna(subset=['open', 'high', 'low', 'close'])
    df = df[df['close'] > 0]
    if len(df) < MIN_ROWS:
        return None
    df.index.name = 'date'
    return df


def main() -> None:
    cfg = load_config()
    data_dir = Path(__file__).parent / cfg['data']['cache_dir']
    ohlcv_dir = data_dir / 'ohlcv'
    ohlcv_dir.mkdir(parents=True, exist_ok=True)
    start = cfg['data']['start_date']

    tickers = fetch_universe()
    pd.Series(tickers, name='ticker').to_csv(data_dir / 'universe.csv', index=False)
    print(f'Universe: {len(tickers)} tickers')

    dropped: list[tuple[str, str]] = []
    saved = 0
    todo = tickers + [cfg['data']['benchmark']]
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
        time.sleep(1)  # be polite to Yahoo

    with open(data_dir / 'download_log.txt', 'w') as f:
        for t, reason in dropped:
            f.write(f'{t}\t{reason}\n')
    print(f'Saved {saved} tickers, dropped {len(dropped)} (see data/download_log.txt)')


if __name__ == '__main__':
    sys.exit(main())
