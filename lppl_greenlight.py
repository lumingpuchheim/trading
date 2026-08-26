"""Green-light gate test: enter only when SPY > 200d SMA (trend) and SPY
20d vol <= trailing 756d 90th pct (calm). Decomposed against its halves.

Post-hoc caveat (declared before running): this gate was suggested by the
2026-08-26 descriptive market-state table computed on BOTH periods, so a
pass here still only registers it as a candidate for post-2026 judgement.

Run: python lppl_greenlight.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel, metrics, simulate


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    panel = load_panel(cfg)
    spy = panel['spy_close']

    trend = (spy > spy.rolling(200).mean()).to_numpy()
    v20 = spy.pct_change().rolling(20).std()
    calm = ~(v20 > v20.rolling(756).quantile(0.90)).to_numpy()
    runs = [('baseline', None), ('calm_only (volhalt_B)', calm),
            ('trend_only', trend), ('green (trend&calm)', trend & calm)]

    today = str(panel['calendar'][-1].date())
    fmt = lambda x: f'{x:.4f}'
    for pname, period in [('dev', (bt['start'], bt['dev_end'])),
                          ('test', (bt['test_start'], today))]:
        rows, curves = {}, {}
        for label, gate in runs:
            trades, equity, avg_inv = simulate(
                panel, cfg, 'lppl_dip2', period,
                entry_gate=gate if gate is not None else None)
            rows[label] = metrics(trades, equity, avg_inv)
            curves[label] = equity
        sm = pd.DataFrame(rows).T
        sm.to_csv(results / f'lppl_greenlight_{pname}.csv')
        print(f'=== {pname} ===')
        print(sm.to_string(float_format=fmt))

        s = spy[(spy.index >= period[0]) & (spy.index <= period[1])]
        plt.figure(figsize=(11, 6))
        plt.plot(s.index, s / s.iloc[0], label='SPY', color='gray',
                 linestyle='--')
        for label, eq in curves.items():
            plt.plot(eq.index, eq / eq.iloc[0], label=label)
        plt.yscale('log')
        plt.title(f'green-light entry gate vs baseline, {pname}')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(results / f'lppl_greenlight_{pname}.png', dpi=120)
        plt.close()
    print(f'charts -> {results}/lppl_greenlight_dev.png, _test.png')


if __name__ == '__main__':
    main()
