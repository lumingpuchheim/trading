"""Democratic regime gate: four previously-failed indicators vote; entries
are blocked when >= 2 of 4 call the day hostile. Configuration declared
before running, no scanning:

  V1 market:  SPY close <= 0.96 x its 20-day high close
  V2 dead:    63-day rolling pre-screen count < trailing 756-day 10th pctile
  V3 mania:   63-day rolling pre-screen count > trailing 756-day 90th pctile
  V4 cohort:  cohort/SPY ratio below its value 126 days ago

Stage 1 verifies the votes against the known regimes BEFORE any backtest.
Stage 2 backtests lppl_dip2 with the ensemble gate, dev and test.

Run:  python lppl_ensemble_gate.py
"""

import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel, metrics, simulate
from lppl_cohort_gate import build_gate


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    panel = load_panel(cfg)
    cal = panel['calendar']
    n = len(cal)

    # V1: market dip (already built in the panel)
    v1 = panel['market_dip']

    # V2/V3: habitat census extremes, self-normalised by trailing history
    flags = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'lppl_flags.parquet')
    daily = flags.groupby('date').size().reindex(cal).fillna(0.0)
    census = daily.rolling(63).sum()
    lo = census.rolling(756).quantile(0.10)
    hi = census.rolling(756).quantile(0.90)
    v2 = (census < lo).to_numpy()   # habitat dead
    v3 = (census > hi).to_numpy()   # mania top

    # V4: cohort-ratio slope negative
    gate_cohort, _ = build_gate(panel, cfg)
    v4 = ~gate_cohort

    hostile = v1.astype(int) + v2.astype(int) + v3.astype(int) + v4.astype(int)
    gate = hostile < 2  # entries allowed while fewer than 2 voters object

    g = pd.Series(gate, index=cal)
    print('per-year fraction of days entries are ALLOWED:')
    for y, v in g.groupby(g.index.year).mean().items():
        if y >= 2007:
            print(f'  {y}: {v:.0%}')
    print('\nvote check in the known regimes (fraction of days blocked):')
    for label, a, b in [('2008', '2008-01-01', '2008-12-31'),
                        ('2009-2012 (good!)', '2009-01-01', '2012-12-31'),
                        ('2020 crash', '2020-02-20', '2020-04-15'),
                        ('2021', '2021-01-01', '2021-12-31'),
                        ('2022', '2022-01-01', '2022-12-31'),
                        ('2023-2025 (good!)', '2023-01-01', '2025-12-31')]:
        w = ~g[a:b]
        print(f'  {label}: blocked {w.mean():.0%}')

    today = str(cal[-1].date())
    fmt = lambda x: f'{x:.4f}'
    for pname, period in [('dev', (bt['start'], bt['dev_end'])),
                          ('test', (bt['test_start'], today))]:
        rows = {}
        for label, eg in [('baseline', None), ('ensemble_gate', gate)]:
            trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2', period,
                                               entry_gate=eg)
            rows[label] = metrics(trades, equity, avg_inv)
        sm = pd.DataFrame(rows).T
        sm.to_csv(results / f'lppl_ensemble_gate_{pname}.csv')
        print(f'\n=== {pname} ===')
        print(sm.to_string(float_format=fmt))


if __name__ == '__main__':
    main()
