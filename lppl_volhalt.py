"""Rule B — the kept crash halt: no new entries while SPY's 20-day
volatility is above its trailing 756-day 90th percentile.

Compares SPY / baseline / vol-halt / softvote_c3 and writes the equity
charts.  Run: python lppl_volhalt.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config, load_panel, metrics, simulate
from lppl_c3_compare import sector_hostile
from lppl_softvote import build_votes


def vol_halt_gate(panel: dict) -> np.ndarray:
    spy = panel['spy_close']
    vol = spy.pct_change().rolling(20).std()
    return ~(vol > vol.rolling(756).quantile(0.90)).to_numpy()


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    panel = load_panel(cfg)
    cal = panel['calendar']

    gate_b = vol_halt_gate(panel)
    votes5 = build_votes(panel, cfg) + sector_hostile(panel, cfg).astype(int)
    mult5 = (5 - votes5) / 5.0

    today = str(cal[-1].date())
    fmt = lambda x: f'{x:.4f}'
    runs = [('baseline', {}), ('volhalt_B', {'entry_gate': gate_b}),
            ('softvote_c3', {'size_mult': mult5})]
    for pname, period in [('dev', (bt['start'], bt['dev_end'])),
                          ('test', (bt['test_start'], today))]:
        rows, curves = {}, {}
        for label, kw in runs:
            trades, equity, avg_inv = simulate(panel, cfg, 'lppl_dip2',
                                               period, **kw)
            rows[label] = metrics(trades, equity, avg_inv)
            curves[label] = equity
        sm = pd.DataFrame(rows).T
        sm.to_csv(results / f'lppl_volhalt_{pname}.csv')
        print(f'=== {pname} ===')
        print(sm.to_string(float_format=fmt))

        spy = panel['spy_close']
        spy = spy[(spy.index >= period[0]) & (spy.index <= period[1])]
        plt.figure(figsize=(11, 6))
        plt.plot(spy.index, spy / spy.iloc[0], label='SPY', color='gray',
                 linestyle='--')
        for label, eq in curves.items():
            plt.plot(eq.index, eq / eq.iloc[0], label=label)
        plt.yscale('log')
        plt.title(f'kept variants vs baseline vs SPY, {pname}')
        plt.legend()
        plt.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(results / f'lppl_volhalt_{pname}.png', dpi=120)
        plt.close()
    print(f'charts -> {results}/lppl_volhalt_dev.png, lppl_volhalt_test.png')


if __name__ == '__main__':
    main()
