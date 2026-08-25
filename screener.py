"""Sections 2-4: trend filter, base definition, seller-decay model.

Run after download_data.py:  python screener.py
Writes data/signals.parquet — one row per (ticker, day) with a valid base,
including the decay-fit statistics and a `model_pass` flag.
Everything is computed from data up to and including the decision day only.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from curve_fit import fit_decay

ROOT = Path(__file__).parent


def load_config() -> dict:
    with open(ROOT / 'config.yaml') as f:
        return yaml.safe_load(f)


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Add SMAs, 52-week levels, liquidity, normalised range/volume, and the
    per-day trend-filter verdict. All rolling windows end at the current row,
    so every column at row i uses only data up to and including day i."""
    tf = cfg['trend_filter']
    d = cfg['data']
    df = df.copy()
    c = df['close']

    df['sma_fast'] = c.rolling(tf['sma_fast']).mean()
    df['sma_mid'] = c.rolling(tf['sma_mid']).mean()
    df['sma_slow'] = c.rolling(tf['sma_slow']).mean()
    sma_slow_ago = df['sma_slow'].shift(tf['sma_slow_rising_lookback'])
    hi52 = c.rolling(tf['week52_window']).max()
    lo52 = c.rolling(tf['week52_window']).min()

    dollar_vol = (c * df['volume']).rolling(d['dollar_volume_window']).mean()
    df['liquid'] = (c > d['min_price']) & (dollar_vol > d['min_dollar_volume'])

    df['trend_ok'] = (
        (c > df['sma_mid'])
        & (c > df['sma_slow'])
        & (df['sma_mid'] > df['sma_slow'])
        & (df['sma_slow'] > sma_slow_ago)
        & (df['sma_fast'] > df['sma_mid'])
        & (c > df['sma_fast'])
        & (c >= tf['min_above_52w_low'] * lo52)
        & (c >= tf['min_of_52w_high'] * hi52)
    )
    df['qualify'] = df['trend_ok'] & df['liquid']

    # normalisation baselines: mean over the 120 days *prior* to each day
    # (shift(1) excludes the day itself)
    bw = cfg['model']['baseline_window']
    raw_range = (df['high'] - df['low']) / c
    df['norm_range'] = raw_range / raw_range.shift(1).rolling(bw).mean()
    df['norm_vol'] = df['volume'] / df['volume'].shift(1).rolling(bw).mean()
    return df


def find_base(closes: np.ndarray, cfg: dict) -> tuple[float, int] | None:
    """Section 3 on a window of the last `high_window` closes ending at the
    decision day. Returns (base_top, base_length_in_days) or None."""
    b = cfg['base']
    if len(closes) < b['high_window']:
        return None
    h = closes.max()
    start = int(np.argmax(closes >= b['start_threshold'] * h))
    seg = closes[start:]
    length = len(seg)  # base start through today, inclusive
    if not (b['min_length'] <= length <= b['max_length']):
        return None
    if seg.max() > b['max_above_high'] * h or seg.min() < b['min_below_high'] * h:
        return None
    return float(h), length


def compute_signals(df: pd.DataFrame, cfg: dict, spy_norm: pd.DataFrame) -> pd.DataFrame:
    """Sections 3-4 for one ticker. `df` must already have indicators.
    `spy_norm` holds SPY's norm_range/norm_vol on the market calendar, used to
    fit `lambda_market` over the same window as the stock's own fit.
    Returns one row per day with a qualifying trend AND a valid base."""
    m = cfg['model']
    hw = cfg['base']['high_window']
    closes = df['close'].to_numpy()
    nrange = df['norm_range'].to_numpy()
    nvol = df['norm_vol'].to_numpy()
    spy_nr = spy_norm['norm_range'].to_numpy()
    spy_nv = spy_norm['norm_vol'].to_numpy()
    spy_pos = {d: j for j, d in enumerate(spy_norm.index)}

    rows = []
    for i in np.flatnonzero(df['qualify'].to_numpy()):
        if i + 1 < hw:
            continue
        base = find_base(closes[i + 1 - hw:i + 1], cfg)
        if base is None:
            continue
        base_top, base_len = base

        # last N days of the base (the whole base if it is shorter than N)
        w = min(base_len, m['fit_window'])
        fit_r = fit_decay(nrange[i + 1 - w:i + 1], m['min_valid_points'])
        fit_v = fit_decay(nvol[i + 1 - w:i + 1], m['min_valid_points'])
        if fit_r is None or fit_v is None:
            continue

        lambda_market = np.nan
        j = spy_pos.get(df.index[i])
        if j is not None and j + 1 >= w:
            mkt_r = fit_decay(spy_nr[j + 1 - w:j + 1], m['min_valid_points'])
            mkt_v = fit_decay(spy_nv[j + 1 - w:j + 1], m['min_valid_points'])
            if mkt_r is not None and mkt_v is not None:
                lambda_market = (mkt_r.lam + mkt_v.lam) / 2

        rows.append({
            'date': df.index[i],
            'base_top': base_top,
            'base_len': base_len,
            'lambda_range': fit_r.lam,
            'lambda_vol': fit_v.lam,
            'lambda': (fit_r.lam + fit_v.lam) / 2,
            'p_today_range': fit_r.p_today,
            'p_today_vol': fit_v.p_today,
            'r2_range': fit_r.r2,
            'r2_vol': fit_v.r2,
            'lambda_market': lambda_market,
            # FIXED sanity filter, not tuned: drops fits with no relationship
            'sanity_pass': fit_r.r2 > m['sanity_r2_min'] and fit_v.r2 > m['sanity_r2_min'],
        })
    return pd.DataFrame(rows)


def main() -> None:
    cfg = load_config()
    ohlcv_dir = ROOT / cfg['data']['cache_dir'] / 'ohlcv'
    benchmark = cfg['data']['benchmark']
    files = sorted(p for p in ohlcv_dir.glob('*.parquet') if p.stem != benchmark)
    if not files:
        sys.exit('No data found — run download_data.py first')

    spy = add_indicators(pd.read_parquet(ohlcv_dir / f'{benchmark}.parquet'), cfg)
    spy_norm = spy[['norm_range', 'norm_vol']]

    all_signals = []
    dropped = []
    for n, path in enumerate(files, 1):
        ticker = path.stem
        df = pd.read_parquet(path)
        if len(df) < cfg['base']['high_window']:
            dropped.append((ticker, f'only {len(df)} rows'))
            continue
        sig = compute_signals(add_indicators(df, cfg), cfg, spy_norm)
        if not sig.empty:
            sig['ticker'] = ticker
            all_signals.append(sig)
        if n % 100 == 0:
            print(f'  {n}/{len(files)} tickers screened')

    signals = pd.concat(all_signals, ignore_index=True)
    out = ROOT / cfg['data']['cache_dir'] / 'signals.parquet'
    signals.to_parquet(out)
    for t, reason in dropped:
        print(f'dropped {t}: {reason}')
    print(f'{len(signals)} base-valid rows ({int(signals["sanity_pass"].sum())} sanity passes) '
          f'across {signals["ticker"].nunique()} tickers -> {out}')
    print(f'dropped {len(dropped)} tickers with insufficient history')


if __name__ == '__main__':
    main()
