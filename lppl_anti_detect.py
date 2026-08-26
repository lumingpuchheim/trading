"""Run the ANTI-bubble detector over the cached universe (ANTIBUBBLE_SPEC).

Mirror of lppl_detect.py: the pre-screen selects deep established
declines (>= 25% below the 252d high, below the 200d SMA), the fit uses
the mirrored grid (tc BEFORE the window: log-periodic decay since a past
peak). All fit constants inherited frozen from the bubble side. Caches
one row per evaluated day to data/lppl_anti_flags.parquet; the tc_ahead
column holds tc_BEHIND (days between the estimated past peak and the
window start). Run: python lppl_anti_detect.py  (~20-50 min on 7 cores)
"""

import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
WORKERS = max(1, (os.cpu_count() or 2) - 1)


def load_config() -> dict:
    import yaml
    with open(ROOT / 'config.yaml') as f:
        return yaml.safe_load(f)


def detect_ticker(path_str: str) -> tuple[list[dict], list[dict], int, int]:
    from lppl import WindowGrid, evaluate_day, prescreen_anti

    cfg = load_config()
    g = cfg['lppl']
    global _AGRIDS
    try:
        grids = _AGRIDS
    except NameError:
        grids = _AGRIDS = [WindowGrid(n, cfg, mirror=True)
                           for n in g['windows']]

    path = Path(path_str)
    df = pd.read_parquet(path)
    closes = df['close'].to_numpy()
    log_close = np.log(closes)
    dates = df.index

    rows, probes = [], []
    n_pass = n_reject = 0
    rng = np.random.default_rng(abs(hash(path.stem)) % (2 ** 32))
    start = min(g['windows'])
    for i in range(start, len(df), g['refit_every']):
        if prescreen_anti(closes, i, cfg):
            n_pass += 1
            ev = evaluate_day(log_close, i, grids, cfg)
            rows.append({'ticker': path.stem, 'date': dates[i],
                         'i_local': i, **ev})
        else:
            n_reject += 1
            if rng.random() < 0.002:
                ev = evaluate_day(log_close, i, grids, cfg)
                probes.append({'ticker': path.stem, 'date': dates[i], **ev})
    return rows, probes, n_pass, n_reject


def main() -> None:
    cfg = load_config()
    data_dir = ROOT / cfg['data']['cache_dir']
    files = sorted(str(p) for p in (data_dir / 'ohlcv').glob('*.parquet')
                   if Path(p).stem != cfg['data']['benchmark'])
    if not files:
        sys.exit('No data found — run download_data.py first')
    print(f'{len(files)} tickers, {WORKERS} workers (anti-bubble)', flush=True)

    t0 = time.time()
    all_rows, all_probes = [], []
    n_pass = n_reject = 0
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        for k, (rows, probes, np_, nr_) in enumerate(
                pool.map(detect_ticker, files, chunksize=8), 1):
            all_rows.extend(rows)
            all_probes.extend(probes)
            n_pass += np_
            n_reject += nr_
            if k % 100 == 0:
                el = time.time() - t0
                print(f'  {k}/{len(files)} tickers, {len(all_rows)} evaluations, '
                      f'{el / 60:.0f} min elapsed, '
                      f'eta {el / k * (len(files) - k) / 60:.0f} min', flush=True)

    flags = pd.DataFrame(all_rows)
    flags.to_parquet(data_dir / 'lppl_anti_flags.parquet')
    total = n_pass + n_reject
    print(f'\nanti pre-screen: {n_pass}/{total} refit days passed '
          f'({n_pass / total:.1%})')
    print(f'{len(flags)} evaluations, {int(flags["bubble"].sum())} anti verdicts '
          f'({flags["bubble"].mean():.1%}) -> {data_dir / "lppl_anti_flags.parquet"}')
    probes = pd.DataFrame(all_probes)
    if len(probes):
        rate = probes['bubble'].mean()
        probes.to_parquet(data_dir / 'lppl_anti_prescreen_probe.parquet')
        print(f'pre-screen validation: {len(probes)} rejected days probed, '
              f'{rate:.2%} would have qualified '
              f'({"OK" if rate < 0.01 else "WARNING: pre-screen too tight"})')
    print(f'total {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
